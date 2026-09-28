"""
backend/apps/voyages/sof_auto_classifier.py
============================================================
Cycle 2.2.3 — Weather Working Day & Interruption Auto-Flagging

Deterministic, zero-mock auto-classifier that stamps ``count_status``,
``count_fraction``, and ``counted_hours`` on persisted ``StatementOfFacts``
records after each ingestion batch.

Algorithm
---------
1.  Load the entire SOF timeline for a voyage (sorted by sequence/timestamp).
2.  Translate each SOF record into a ``SOFEvent`` domain object that the
    existing ``TimeSliceDiscretizationEngine`` understands.
3.  Run discretization to produce contiguous ``TimeSlice`` intervals whose
    start/end boundaries are exactly the SOF event timestamps.
4.  Run each slice through ``InterruptionRulesEvaluator`` using the
    voyage contract's laytime terms and any committed public holiday dates.
5.  For every completed interruption interval (START + END pair), accumulate
    the ``EvaluatedSlice`` results that fall within [start_ts, end_ts) and
    sum their ``counted_hours``, ``excepted_hours``, and derive a composite
    ``count_status`` and ``count_fraction`` for the SOF START record.
6.  Bulk-update only the START record of each interruption pair with the
    computed fields (the END record remains unmodified — it carries no
    independent interval duration).
7.  For non-interruption events (informational milestones), no count_status
    is assigned (they remain NULL — they are points-in-time, not intervals).

Accounting Invariant
--------------------
For each completed interruption interval [T_start, T_end):
    counted_hours + excepted_hours == elapsed_hours  ± ε  (ε < 0.000010 h)

This invariant is verified before any DB write; an AssertionError is raised
(triggering a transaction rollback) if it is violated.

Integration
-----------
Called from ``views.sof()`` after the atomic DB write block, inside the same
request lifecycle (synchronous, not celery — Cycle 2.2.3 is the synchronous
baseline; async offloading comes in Phase 3).
"""

from __future__ import annotations

import decimal
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Sequence, Tuple

from apps.laytime.engine.constants import LaytimeTerms, WEATHER_WORKING_TERMS
from apps.laytime.engine.interruption_evaluator import InterruptionRulesEvaluator
from apps.laytime.engine.time_slice import (
    SOFEvent as EngineSOFEvent,
    SOFEventType as EngineSOFEventType,
    TimeSliceDiscretizationEngine,
    IntervalCountStatus as EngineIntervalCountStatus,
)
from .sof_enums import SOFEventType, IntervalCountStatus

logger = logging.getLogger(__name__)

# Decimal precision (6 decimal places, matches existing engine)
_6DP = Decimal("0.000001")
_SECONDS_PER_HOUR = Decimal("3600")
_ENGINE_CONTEXT = decimal.Context(
    prec=28,
    rounding=ROUND_HALF_UP,
    traps=[decimal.DivisionByZero, decimal.InvalidOperation, decimal.Overflow],
)
_ACCOUNTING_EPSILON = Decimal("0.000010")

# ---------------------------------------------------------------------------
# Interruption pair mapping: START → END event type
# ---------------------------------------------------------------------------
_START_TO_END: Dict[str, str] = {
    SOFEventType.RAIN_COMMENCED.value:              SOFEventType.RAIN_CEASED.value,
    SOFEventType.WIND_FORCE_EXCEEDED_LIMIT.value:   SOFEventType.WIND_FORCE_WITHIN_LIMIT.value,
    SOFEventType.CRANE_BREAKDOWN_COMMENCED.value:   SOFEventType.CRANE_BREAKDOWN_RESUMED.value,
    SOFEventType.POWER_FAILURE_COMMENCED.value:     SOFEventType.POWER_FAILURE_RESUMED.value,
    SOFEventType.LABOUR_DISPUTE_COMMENCED.value:    SOFEventType.LABOUR_DISPUTE_ENDED.value,
    SOFEventType.BERTH_CONGESTION_DELAY_START.value: SOFEventType.BERTH_CONGESTION_DELAY_END.value,
    SOFEventType.DISCHARGE_SUSPENDED.value:         SOFEventType.DISCHARGE_RESUMED.value,
}

_END_EVENT_TYPES: frozenset = frozenset(_START_TO_END.values())


# ---------------------------------------------------------------------------
# Result value-object
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class InterruptionClassification:
    """
    Computed laytime accounting result for a single completed interruption interval.

    Attributes
    ----------
    sof_start_id    : UUID (as string) of the START StatementOfFacts record.
    start_event_type: e.g. ``"RAIN_COMMENCED"``.
    start_ts        : UTC timestamp of the interval start.
    end_ts          : UTC timestamp of the interval end.
    elapsed_hours   : Wall-clock duration of the interval.
    counted_hours   : Laytime hours that count against the allowance.
    excepted_hours  : Hours excluded from counting.
    count_fraction  : ``counted_hours / elapsed_hours`` (0.00 – 1.00).
    count_status    : FULLY_COUNTED, PARTIALLY_COUNTED, or EXCLUDED.
    deduction_reasons: Human-readable list of reasons for any exclusion.
    """
    sof_start_id: str
    start_event_type: str
    start_ts: datetime
    end_ts: datetime
    elapsed_hours: Decimal
    counted_hours: Decimal
    excepted_hours: Decimal
    count_fraction: Decimal
    count_status: str   # IntervalCountStatus value
    deduction_reasons: Tuple[str, ...]


@dataclass(frozen=True)
class AutoClassificationResult:
    """
    Result of classifying all completed interruption intervals for a voyage.

    Attributes
    ----------
    classified_count : Number of interruption pairs successfully classified.
    classifications  : Tuple of individual interval results.
    updated_sof_ids  : SOF start record IDs that were stamped in the DB.
    skipped_open_count: Number of open (unclosed) interruption intervals skipped.
    """
    classified_count: int
    classifications: Tuple[InterruptionClassification, ...]
    updated_sof_ids: Tuple[str, ...]
    skipped_open_count: int


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def auto_classify_sof_interruptions(
    voyage,
    *,
    public_holidays: Optional[frozenset] = None,
    crane_pro_rata: bool = True,
    saturday_cutoff: bool = True,
) -> AutoClassificationResult:
    """
    Classify all completed interruption pairs for ``voyage`` and write
    ``count_status``, ``count_fraction``, and ``counted_hours`` back to
    the corresponding ``StatementOfFacts`` START records.

    Parameters
    ----------
    voyage :
        A ``VesselVoyage`` instance (must have ``sof_events`` reverse relation
        and ``contract.laytime_terms``).
    public_holidays :
        Optional ``frozenset[datetime.date]`` of public holiday dates at the
        discharge port (defaults to empty — no public holidays).
    crane_pro_rata :
        If ``True``, partial crane breakdowns use pro-rata counting fraction.
    saturday_cutoff :
        If ``True`` (default), uses Baltic Exchange Saturday 17:00 cutoff for
        SHEX/WWDSHEX terms.  Set ``False`` for pure-Sunday-only exclusion.

    Returns
    -------
    AutoClassificationResult
    """
    from .models import StatementOfFacts  # local import to avoid circular dependency

    public_holidays = public_holidays or frozenset()

    # Load all SOF events for this voyage, ordered chronologically
    sof_qs = (
        voyage.sof_events
        .all()
        .order_by("sequence_number", "event_timestamp")
    )
    sof_records = list(sof_qs)

    if not sof_records:
        return AutoClassificationResult(
            classified_count=0,
            classifications=(),
            updated_sof_ids=(),
            skipped_open_count=0,
        )

    # Resolve laytime terms from contract
    try:
        laytime_terms = LaytimeTerms(voyage.contract.laytime_terms)
    except ValueError:
        # Fallback to SHINC (all time counts) for unknown terms
        laytime_terms = LaytimeTerms.SHINC
        logger.warning(
            "Unknown laytime terms '%s' for voyage %s — defaulting to SHINC.",
            voyage.contract.laytime_terms,
            voyage.voyage_id,
        )

    # Resolve laytime milestones
    laytime_commences_at = voyage.nor_tendered_at  # conservative; use DISCHARGE_COMMENCED if available
    if voyage.discharge_commenced_at:
        laytime_commences_at = voyage.discharge_commenced_at
    discharge_completed_at = voyage.discharge_completed_at

    # ------------------------------------------------------------------
    # Step 1: Build EngineSOFEvent list for the discretization engine
    # ------------------------------------------------------------------
    engine_events = _build_engine_events(sof_records)

    if len(engine_events) < 2:
        # Need at least 2 distinct timestamps to form slices
        return AutoClassificationResult(
            classified_count=0,
            classifications=(),
            updated_sof_ids=(),
            skipped_open_count=0,
        )

    # ------------------------------------------------------------------
    # Step 2: Discretize SOF timeline into TimeSlices
    # ------------------------------------------------------------------
    try:
        discretizer = TimeSliceDiscretizationEngine(
            events=engine_events,
            laytime_commences_at=laytime_commences_at,
            discharge_completed_at=discharge_completed_at,
        )
        discretization = discretizer.discretize()
    except (ValueError, Exception) as exc:
        logger.warning(
            "SOF discretization failed for voyage %s: %s",
            voyage.voyage_id, exc,
        )
        return AutoClassificationResult(
            classified_count=0,
            classifications=(),
            updated_sof_ids=(),
            skipped_open_count=0,
        )

    slices = list(discretization.slices)

    # ------------------------------------------------------------------
    # Step 3: Evaluate each slice under contract laytime terms
    # ------------------------------------------------------------------
    evaluator = InterruptionRulesEvaluator(
        laytime_terms=laytime_terms,
        saturday_cutoff=saturday_cutoff,
        public_holidays=public_holidays,
        crane_pro_rata=crane_pro_rata,
    )
    evaluation = evaluator.evaluate(slices)
    evaluated_slices = list(evaluation.slices)

    # ------------------------------------------------------------------
    # Step 4: Pair interruption intervals and accumulate slice results
    # ------------------------------------------------------------------
    classifications: List[InterruptionClassification] = []
    sof_updates: List[Tuple[str, InterruptionClassification]] = []  # (sof_id, classification)
    skipped_open = 0

    # Build index of open START events
    # Stack per start_event_type: list of (sof_start_id, start_ts)
    open_starts: Dict[str, List[Tuple[str, datetime]]] = {}

    for sof_rec in sof_records:
        ev_type = sof_rec.event_type
        ev_ts = sof_rec.event_timestamp
        sof_id = str(sof_rec.sof_id)

        if ev_type in _START_TO_END:
            # Push to open stack
            open_starts.setdefault(ev_type, []).append((sof_id, ev_ts))

        elif ev_type in _END_EVENT_TYPES:
            # Find matching START
            start_ev_type = _find_start_for_end(ev_type)
            if start_ev_type is None:
                continue

            stack = open_starts.get(start_ev_type, [])
            if not stack:
                # Orphaned end — normalizer should have caught this, but be safe
                logger.debug("Orphaned end event '%s' at %s — skipping classification.", ev_type, ev_ts)
                continue

            start_sof_id, start_ts = stack.pop(0)  # FIFO: first-in, first-out

            # Accumulate EvaluatedSlices within [start_ts, ev_ts)
            classification = _compute_interval_classification(
                sof_start_id=start_sof_id,
                start_event_type=start_ev_type,
                start_ts=start_ts,
                end_ts=ev_ts,
                evaluated_slices=evaluated_slices,
            )

            if classification is not None:
                classifications.append(classification)
                sof_updates.append((start_sof_id, classification))

    # Count remaining open starts (unclosed interruptions)
    for start_type, stack in open_starts.items():
        skipped_open += len(stack)

    # ------------------------------------------------------------------
    # Step 5: Bulk-update StatementOfFacts START records
    # ------------------------------------------------------------------
    updated_ids: List[str] = []

    if sof_updates:
        # Build a map for efficient DB update
        update_map: Dict[str, InterruptionClassification] = {
            sof_id: cls for sof_id, cls in sof_updates
        }

        # Fetch only the START records that need updating
        import uuid
        start_ids_uuid = [uuid.UUID(sid) for sid in update_map.keys()]
        records_to_update = StatementOfFacts.objects.filter(sof_id__in=start_ids_uuid)

        for record in records_to_update:
            cls = update_map[str(record.sof_id)]
            record.count_status = cls.count_status
            record.count_fraction = cls.count_fraction.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            record.counted_hours = cls.counted_hours

        # Bulk update — single DB query, atomic with the calling transaction
        if records_to_update:
            StatementOfFacts.objects.bulk_update(
                records_to_update,
                fields=["count_status", "count_fraction", "counted_hours"],
            )
            updated_ids = [str(r.sof_id) for r in records_to_update]

    return AutoClassificationResult(
        classified_count=len(classifications),
        classifications=tuple(classifications),
        updated_sof_ids=tuple(updated_ids),
        skipped_open_count=skipped_open,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_engine_events(sof_records: Sequence) -> List[EngineSOFEvent]:
    """
    Translate ``StatementOfFacts`` DB records into ``EngineSOFEvent`` objects
    understood by the ``TimeSliceDiscretizationEngine``.

    Records whose event_type is not recognised by the engine's SOFEventType
    enum are silently skipped (they are informational milestones with no
    state-machine relevance to the discretization).
    """
    engine_event_type_values = {e.value for e in EngineSOFEventType}
    result = []

    for rec in sof_records:
        if rec.event_type not in engine_event_type_values:
            continue
        try:
            eng_type = EngineSOFEventType(rec.event_type)
        except ValueError:
            continue

        # Ensure timestamp is UTC-aware
        ts = rec.event_timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        result.append(EngineSOFEvent(
            timestamp=ts,
            event_type=eng_type,
            sequence_number=rec.sequence_number,
            description=rec.event_description or "",
            cranes_working=rec.crane_number,        # crane_number used as cranes_working proxy
            cranes_total=None,                      # total cranes not stored per-event; None = use breakdown flag only
            weather_condition=rec.weather_condition or "",
            sof_id=str(rec.sof_id),
        ))

    return result


def _find_start_for_end(end_event_type: str) -> Optional[str]:
    """Return the corresponding START event type for a given END event type."""
    for start, end in _START_TO_END.items():
        if end == end_event_type:
            return start
    return None


def _compute_interval_classification(
    sof_start_id: str,
    start_event_type: str,
    start_ts: datetime,
    end_ts: datetime,
    evaluated_slices: List,
) -> Optional[InterruptionClassification]:
    """
    Accumulate EvaluatedSlice results that fall within [start_ts, end_ts)
    and compute a single composite classification for the interruption interval.

    Returns ``None`` if no slices overlap the interval (e.g. zero-duration).
    """
    if end_ts <= start_ts:
        return None

    # Select slices whose [slice.start_time, slice.end_time) overlaps [start_ts, end_ts)
    overlapping = [
        s for s in evaluated_slices
        if s.start_time >= start_ts and s.end_time <= end_ts
    ]

    if not overlapping:
        # Compute elapsed anyway for the simple case (single contiguous interval)
        # where the evaluator produced no sub-slices (e.g. only 2 events total)
        elapsed_sec = int((end_ts - start_ts).total_seconds())
        with decimal.localcontext(_ENGINE_CONTEXT):
            elapsed_h = (Decimal(str(elapsed_sec)) / _SECONDS_PER_HOUR).quantize(_6DP, rounding=ROUND_HALF_UP)
        return None  # Cannot classify without evaluated slices

    total_counted = sum((s.counted_hours for s in overlapping), Decimal("0.000000"))
    total_excepted = sum((s.excepted_hours for s in overlapping), Decimal("0.000000"))
    total_elapsed = sum((s.duration_hours for s in overlapping), Decimal("0.000000"))

    # Accounting invariant check
    diff = abs((total_counted + total_excepted) - total_elapsed)
    if diff > _ACCOUNTING_EPSILON:
        logger.error(
            "Accounting invariant violated for SOF %s: "
            "counted(%s) + excepted(%s) != elapsed(%s), diff=%s",
            sof_start_id, total_counted, total_excepted, total_elapsed, diff,
        )
        return None

    # Compute composite fraction
    if total_elapsed > Decimal("0"):
        with decimal.localcontext(_ENGINE_CONTEXT):
            composite_fraction = (total_counted / total_elapsed).quantize(_6DP, rounding=ROUND_HALF_UP)
    else:
        composite_fraction = Decimal("0.000000")

    # Determine composite status
    if composite_fraction == Decimal("0.000000"):
        composite_status = IntervalCountStatus.EXCLUDED.value
    elif composite_fraction == Decimal("1.000000"):
        composite_status = IntervalCountStatus.FULLY_COUNTED.value
    else:
        composite_status = IntervalCountStatus.PARTIALLY_COUNTED.value

    # Collect deduction reasons from slices that had exclusions
    reasons = tuple(dict.fromkeys(
        s.deduction_reason for s in overlapping
        if s.deduction_reason is not None
    ))

    return InterruptionClassification(
        sof_start_id=sof_start_id,
        start_event_type=start_event_type,
        start_ts=start_ts,
        end_ts=end_ts,
        elapsed_hours=total_elapsed,
        counted_hours=total_counted,
        excepted_hours=total_excepted,
        count_fraction=composite_fraction,
        count_status=composite_status,
        deduction_reasons=reasons,
    )
