"""
backend/apps/voyages/sof_normalizer.py
============================================================
Cycle 2.2.2 — SOF Timeline Event Normalization & Ordering

Deterministic, pure-Python normalizer for inbound Statement of Facts (SOF)
event batches.  No mocking.  No random state.

Responsibilities
----------------
1. UTC normalization  — reject naive timestamps; convert timezone-aware to UTC.
2. Chronological sort — stable sort by (event_timestamp, sequence_number).
3. Duplicate detection — same voyage + event_type + event_timestamp is idempotent
   and rejected with a descriptive error list (not silently dropped).
4. Retrograde ordering guard — within a batch, no event may precede a prior
   event whose timestamp is already committed to the database.
5. Interruption pair validation — for every COMMENCE event (RAIN_COMMENCED,
   CRANE_BREAKDOWN_COMMENCED, …) in the inbound batch + existing DB state,
   ensure the matching END event is present and does NOT precede the START.
   Unclosed interruptions that extend beyond the last event are allowed
   (the port operation may still be ongoing).
6. Phase precedence invariant — arrival-phase events (VESSEL_ARRIVED_*,
   NOR_*) must precede discharge-phase events (DISCHARGE_*) and departure-
   phase events (LINES_RELEASED, VESSEL_DEPARTED, …).

All errors are collected and returned as a list of ``SOFNormalizationError``
namedtuples so callers can surface ALL problems in a single API response.

This module is stateless and may be called from tests without a database.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from django.utils import timezone

from .sof_enums import (
    INTERRUPTION_END_EVENTS,
    INTERRUPTION_START_EVENTS,
    SOFEventType,
)

# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SOFNormalizationError:
    """
    Immutable value-object describing a single normalization violation.

    Attributes
    ----------
    code        : Short machine-readable error code (e.g. ``RETROGRADE_TIMESTAMP``).
    detail      : Human-readable explanation.
    event_index : 0-based index of the offending item in the *original* input
                  batch (``None`` when the violation concerns the batch globally).
    event_type  : The event_type string of the offending event, or ``None``.
    """
    code: str
    detail: str
    event_index: Optional[int] = None
    event_type: Optional[str] = None


@dataclass
class NormalizedSOFItem:
    """
    Mutable representation of one SOF event after normalization.
    Carries all original fields plus the resolved UTC timestamp.
    """
    event_type: str
    event_timestamp: datetime.datetime          # always UTC-aware after normalization
    sequence_number: Optional[int]
    event_description: str
    recorded_by_agent: str
    crane_number: Optional[int]
    hatch_number: Optional[int]
    quantity_discharged_at_event: Optional[object]   # Decimal or None
    interruption_cause: str
    weather_condition: str
    is_amendment: bool
    amended_sof_id: Optional[object]
    source_document_id: Optional[object]
    # Back-reference to original input index (before sorting)
    _original_index: int = field(default=0, compare=False, repr=False)


@dataclass(frozen=True)
class NormalizationResult:
    """
    Return value of ``normalize_sof_batch``.

    Attributes
    ----------
    events : Chronologically-sorted, UTC-normalized event list.
             Empty when ``is_valid`` is ``False``.
    errors : List of all collected ``SOFNormalizationError`` items.
    is_valid : ``True`` only when ``errors`` is empty.
    """
    events: List[NormalizedSOFItem]
    errors: List[SOFNormalizationError]

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


# ---------------------------------------------------------------------------
# Interruption pair mapping: start event → expected end event
# ---------------------------------------------------------------------------
_INTERRUPTION_PAIR: Dict[str, str] = {
    SOFEventType.RAIN_COMMENCED.value:              SOFEventType.RAIN_CEASED.value,
    SOFEventType.WIND_FORCE_EXCEEDED_LIMIT.value:   SOFEventType.WIND_FORCE_WITHIN_LIMIT.value,
    SOFEventType.CRANE_BREAKDOWN_COMMENCED.value:   SOFEventType.CRANE_BREAKDOWN_RESUMED.value,
    SOFEventType.POWER_FAILURE_COMMENCED.value:     SOFEventType.POWER_FAILURE_RESUMED.value,
    SOFEventType.LABOUR_DISPUTE_COMMENCED.value:    SOFEventType.LABOUR_DISPUTE_ENDED.value,
    SOFEventType.BERTH_CONGESTION_DELAY_START.value: SOFEventType.BERTH_CONGESTION_DELAY_END.value,
    SOFEventType.DISCHARGE_SUSPENDED.value:         SOFEventType.DISCHARGE_RESUMED.value,
}

# Phase ordering: events in later phases must have timestamps >= events in earlier phases
_PHASE_ORDER: Dict[str, int] = {
    # Phase 0: Arrival
    SOFEventType.VESSEL_ARRIVED_PILOT_STATION.value: 0,
    SOFEventType.VESSEL_ARRIVED_ANCHORAGE.value:      0,
    SOFEventType.NOR_TENDERED.value:                  0,
    SOFEventType.NOR_ACCEPTED.value:                  0,
    SOFEventType.NOR_REJECTED.value:                  0,
    SOFEventType.VESSEL_SHIFTED_ANCHORAGE.value:      0,
    # Phase 1: Berthing
    SOFEventType.PILOT_BOARDED.value:                 1,
    SOFEventType.VESSEL_COMMENCED_BERTHING.value:     1,
    SOFEventType.FIRST_LINE_ASHORE.value:             1,
    SOFEventType.ALL_FAST.value:                      1,
    SOFEventType.GANGWAY_DOWN.value:                  1,
    # Phase 2: Pre-discharge
    SOFEventType.CUSTOMS_BOARDING.value:              2,
    SOFEventType.CUSTOMS_CLEARED.value:               2,
    SOFEventType.DRAFT_SURVEY_COMMENCED.value:        2,
    SOFEventType.DRAFT_SURVEY_COMPLETED.value:        2,
    SOFEventType.HATCHES_OPENED.value:                2,
    # Phase 3: Discharge operations (interruptions may span phases 2-3)
    SOFEventType.DISCHARGE_COMMENCED.value:           3,
    SOFEventType.DISCHARGE_SUSPENDED.value:           3,
    SOFEventType.DISCHARGE_RESUMED.value:             3,
    SOFEventType.DISCHARGE_COMPLETED.value:           3,
    # Phase 4: Interruptions (time-classified, no strict phase bound)
    SOFEventType.RAIN_COMMENCED.value:                -1,   # -1 = phase-agnostic
    SOFEventType.RAIN_CEASED.value:                   -1,
    SOFEventType.WIND_FORCE_EXCEEDED_LIMIT.value:     -1,
    SOFEventType.WIND_FORCE_WITHIN_LIMIT.value:       -1,
    SOFEventType.CRANE_BREAKDOWN_COMMENCED.value:     -1,
    SOFEventType.CRANE_BREAKDOWN_RESUMED.value:       -1,
    SOFEventType.POWER_FAILURE_COMMENCED.value:       -1,
    SOFEventType.POWER_FAILURE_RESUMED.value:         -1,
    SOFEventType.LABOUR_DISPUTE_COMMENCED.value:      -1,
    SOFEventType.LABOUR_DISPUTE_ENDED.value:          -1,
    SOFEventType.BERTH_CONGESTION_DELAY_START.value:  -1,
    SOFEventType.BERTH_CONGESTION_DELAY_END.value:    -1,
    # Phase 5: Departure
    SOFEventType.HATCHES_CLOSED.value:                5,
    SOFEventType.DRAFT_SURVEY_FINAL.value:            5,
    SOFEventType.LINES_RELEASED.value:                5,
    SOFEventType.VESSEL_UNMOORED.value:               5,
    SOFEventType.PILOT_DISEMBARKED.value:             5,
    SOFEventType.VESSEL_DEPARTED.value:               5,
}


# ---------------------------------------------------------------------------
# Public normalizer entry point
# ---------------------------------------------------------------------------

def normalize_sof_batch(
    raw_items: Sequence[dict],
    *,
    existing_timestamps: Optional[Sequence[datetime.datetime]] = None,
    existing_interruption_state: Optional[Dict[str, Optional[datetime.datetime]]] = None,
) -> NormalizationResult:
    """
    Validate and normalize a batch of inbound SOF event dicts.

    Parameters
    ----------
    raw_items :
        List of validated dicts from ``SOFIngestionItemSerializer``.
        Each dict must contain at least ``event_type`` and ``event_timestamp``.
    existing_timestamps :
        UTC-aware timestamps of SOF events already persisted for this voyage
        (used for retrograde detection).  Pass ``[]`` or ``None`` for new voyages.
    existing_interruption_state :
        Mapping of ``{start_event_type: last_open_start_timestamp_or_None}``
        representing open (unpaired) interruption intervals already in the DB.
        Pass ``{}`` or ``None`` if no open intervals exist.

    Returns
    -------
    NormalizationResult
        All errors are collected — callers should check ``result.is_valid``
        before using ``result.events``.
    """
    existing_timestamps = list(existing_timestamps or [])
    existing_interruption_state = dict(existing_interruption_state or {})

    errors: List[SOFNormalizationError] = []
    items: List[NormalizedSOFItem] = []

    # ------------------------------------------------------------------
    # Pass 1: Per-item validation (UTC normalization, known event types)
    # ------------------------------------------------------------------
    for idx, raw in enumerate(raw_items):
        event_type = raw.get("event_type", "")
        raw_ts = raw.get("event_timestamp")

        # 1a. Timestamp presence
        if raw_ts is None:
            errors.append(SOFNormalizationError(
                code="MISSING_TIMESTAMP",
                detail="event_timestamp is required.",
                event_index=idx,
                event_type=event_type,
            ))
            continue

        # 1b. UTC normalization
        ts = _to_utc(raw_ts)
        if ts is None:
            errors.append(SOFNormalizationError(
                code="NAIVE_TIMESTAMP",
                detail=(
                    f"event_timestamp '{raw_ts}' is naive (no timezone offset). "
                    "All SOF timestamps must carry an explicit UTC offset."
                ),
                event_index=idx,
                event_type=event_type,
            ))
            continue

        # 1c. Timestamp sanity: not in the future beyond 5 minutes
        now_utc = timezone.now()
        if ts > now_utc + datetime.timedelta(minutes=5):
            errors.append(SOFNormalizationError(
                code="FUTURE_TIMESTAMP",
                detail=(
                    f"event_timestamp '{ts.isoformat()}' is in the future. "
                    "SOF events must record historical port occurrences."
                ),
                event_index=idx,
                event_type=event_type,
            ))
            # Continue — collect all errors

        items.append(NormalizedSOFItem(
            event_type=event_type,
            event_timestamp=ts,
            sequence_number=raw.get("sequence_number"),
            event_description=raw.get("event_description", ""),
            recorded_by_agent=raw.get("recorded_by_agent", ""),
            crane_number=raw.get("crane_number"),
            hatch_number=raw.get("hatch_number"),
            quantity_discharged_at_event=raw.get("quantity_discharged_at_event"),
            interruption_cause=raw.get("interruption_cause", ""),
            weather_condition=raw.get("weather_condition", ""),
            is_amendment=raw.get("is_amendment", False),
            amended_sof_id=raw.get("amended_sof_id"),
            source_document_id=raw.get("source_document_id"),
            _original_index=idx,
        ))

    # Abort early if timestamp errors prevent further analysis
    if len(items) != len(raw_items):
        return NormalizationResult(events=[], errors=errors)

    # ------------------------------------------------------------------
    # Pass 2: Stable chronological sort
    #   Primary key:   event_timestamp (ascending)
    #   Secondary key: sequence_number (ascending; None sorts last)
    # ------------------------------------------------------------------
    items.sort(key=lambda e: (
        e.event_timestamp,
        e.sequence_number if e.sequence_number is not None else 999_999,
    ))

    # ------------------------------------------------------------------
    # Pass 3: Retrograde guard against already-committed DB timestamps
    # ------------------------------------------------------------------
    if existing_timestamps:
        latest_committed = max(existing_timestamps)
        first_batch_ts = items[0].event_timestamp if items else None
        if first_batch_ts is not None and first_batch_ts < latest_committed:
            errors.append(SOFNormalizationError(
                code="RETROGRADE_TIMESTAMP",
                detail=(
                    f"The earliest inbound event timestamp '{first_batch_ts.isoformat()}' "
                    f"precedes the latest committed SOF timestamp "
                    f"'{latest_committed.isoformat()}'. "
                    "Retrograde ingestion violates chronological monotonicity. "
                    "Use is_amendment=True to correct historical records."
                ),
                event_index=items[0]._original_index if items else None,
                event_type=items[0].event_type if items else None,
            ))

    # ------------------------------------------------------------------
    # Pass 4: Within-batch retrograde check (batch must be monotonic)
    # ------------------------------------------------------------------
    for i in range(1, len(items)):
        prev, curr = items[i - 1], items[i]
        if curr.event_timestamp < prev.event_timestamp:
            errors.append(SOFNormalizationError(
                code="BATCH_RETROGRADE",
                detail=(
                    f"Event #{curr._original_index} ({curr.event_type}) at "
                    f"'{curr.event_timestamp.isoformat()}' precedes event "
                    f"#{prev._original_index} ({prev.event_type}) at "
                    f"'{prev.event_timestamp.isoformat()}' after sorting. "
                    "This should never occur — report a normalizer bug."
                ),
                event_index=curr._original_index,
                event_type=curr.event_type,
            ))

    # ------------------------------------------------------------------
    # Pass 5: Duplicate detection within the batch
    #   (same event_type + same event_timestamp = duplicate)
    # ------------------------------------------------------------------
    seen: Dict[Tuple[str, datetime.datetime], int] = {}
    for item in items:
        key = (item.event_type, item.event_timestamp)
        if key in seen:
            errors.append(SOFNormalizationError(
                code="DUPLICATE_EVENT",
                detail=(
                    f"Event '{item.event_type}' at '{item.event_timestamp.isoformat()}' "
                    f"is a duplicate of item at original index {seen[key]}."
                ),
                event_index=item._original_index,
                event_type=item.event_type,
            ))
        else:
            seen[key] = item._original_index

    # ------------------------------------------------------------------
    # Pass 6: Interruption pair validation
    #   Rules:
    #     a) If a START appears without a matching END in the combined
    #        (existing + batch) timeline, flag as unclosed.
    #     b) If an END appears without any prior (committed or in-batch)
    #        START, flag as orphaned.
    #     c) START timestamp must strictly precede END timestamp.
    #
    #   We only surface errors if an END is missing for a START that
    #   was ALREADY committed (before this batch).  New opens within
    #   the batch are allowed — they can be closed later.
    # ------------------------------------------------------------------
    intr_errors = _validate_interruption_pairs(
        items=items,
        existing_interruption_state=existing_interruption_state,
    )
    errors.extend(intr_errors)

    # ------------------------------------------------------------------
    # Pass 7: Phase precedence invariant
    #   Arrival-phase events must not appear AFTER departure-phase events.
    #   We compare the EARLIEST departure-phase event against any later
    #   arrival-phase events.
    # ------------------------------------------------------------------
    phase_errors = _validate_phase_precedence(items)
    errors.extend(phase_errors)

    if errors:
        return NormalizationResult(events=[], errors=errors)

    return NormalizationResult(events=items, errors=[])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_utc(ts_value: object) -> Optional[datetime.datetime]:
    """
    Convert ``ts_value`` to a UTC-aware datetime.

    Accepts:
      - ``datetime.datetime`` with tzinfo (converts to UTC)
      - ``str`` ISO 8601 with 'Z' or offset (parsed then converted)

    Returns ``None`` if the value is naive (no tzinfo).
    """
    if isinstance(ts_value, datetime.datetime):
        if ts_value.tzinfo is None:
            return None
        return ts_value.astimezone(datetime.timezone.utc)

    if isinstance(ts_value, str):
        # Django REST Framework has already parsed this into a datetime
        # by the time we receive validated_data.  Handle raw strings as
        # a fallback for direct unit-test usage.
        try:
            # Python 3.11+: datetime.fromisoformat handles 'Z'
            dt = datetime.datetime.fromisoformat(ts_value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return None
            return dt.astimezone(datetime.timezone.utc)
        except ValueError:
            return None

    return None


def _validate_interruption_pairs(
    items: List[NormalizedSOFItem],
    existing_interruption_state: Dict[str, Optional[datetime.datetime]],
) -> List[SOFNormalizationError]:
    """
    Validate START ↔ END pairing for interruption events.

    The algorithm:
    - Initialise ``open_starts`` from ``existing_interruption_state``
      (mapping start_event_type → timestamp of open interval, or None if closed).
    - Walk through sorted ``items``:
        * START event: if one is already open for same type → flag UNCLOSED_INTERRUPTION
          then replace (allow re-opening after close).
        * END event: look up its matching START.
          - If no open start → ORPHANED_INTERRUPTION_END.
          - If end_ts < open_start_ts → INTERRUPTION_INVERTED.
          - Otherwise → close the interval.
    - After all items: any open start that was already in ``existing_interruption_state``
      (i.e. was open BEFORE this batch) and has no close in the batch → UNCLOSED_INTERRUPTION.
    """
    errors: List[SOFNormalizationError] = []

    # Mapping: start_event_type → open start timestamp (None = closed)
    open_starts: Dict[str, Optional[datetime.datetime]] = dict(existing_interruption_state)

    # Mark which keys were pre-existing (open before this batch)
    pre_existing_open: set = {
        k for k, v in existing_interruption_state.items() if v is not None
    }

    # Build reverse mapping: end_event_type → start_event_type
    end_to_start = {v: k for k, v in _INTERRUPTION_PAIR.items()}

    for item in items:
        ev = item.event_type

        if ev in _INTERRUPTION_PAIR:
            # This is a START event
            if open_starts.get(ev) is not None:
                errors.append(SOFNormalizationError(
                    code="UNCLOSED_INTERRUPTION",
                    detail=(
                        f"Event '{ev}' at '{item.event_timestamp.isoformat()}' "
                        f"opens a new interruption interval, but a prior '{ev}' "
                        f"interval opened at '{open_starts[ev].isoformat()}' "
                        "has not yet been closed with "
                        f"'{_INTERRUPTION_PAIR[ev]}'."
                    ),
                    event_index=item._original_index,
                    event_type=ev,
                ))
            open_starts[ev] = item.event_timestamp

        elif ev in end_to_start:
            # This is an END event
            start_ev = end_to_start[ev]
            open_ts = open_starts.get(start_ev)

            if open_ts is None:
                errors.append(SOFNormalizationError(
                    code="ORPHANED_INTERRUPTION_END",
                    detail=(
                        f"Event '{ev}' at '{item.event_timestamp.isoformat()}' "
                        f"closes interruption type '{start_ev}', "
                        "but no corresponding open interval was found."
                    ),
                    event_index=item._original_index,
                    event_type=ev,
                ))
            elif item.event_timestamp < open_ts:
                errors.append(SOFNormalizationError(
                    code="INTERRUPTION_INVERTED",
                    detail=(
                        f"End event '{ev}' at '{item.event_timestamp.isoformat()}' "
                        f"precedes its start event '{start_ev}' "
                        f"at '{open_ts.isoformat()}'. "
                        "End timestamp must be >= start timestamp."
                    ),
                    event_index=item._original_index,
                    event_type=ev,
                ))
            else:
                # Successfully closed — clear the open interval
                open_starts[start_ev] = None
                pre_existing_open.discard(start_ev)

    # After processing all items, flag pre-existing unclosed intervals
    # that this batch failed to close (only errors if the interval was
    # already open BEFORE the batch; new opens are allowed to remain open).
    for start_ev in pre_existing_open:
        if open_starts.get(start_ev) is not None:
            errors.append(SOFNormalizationError(
                code="UNCLOSED_PRE_EXISTING_INTERRUPTION",
                detail=(
                    f"Interruption type '{start_ev}' opened at "
                    f"'{existing_interruption_state[start_ev].isoformat()}' "
                    "was not closed by any event in this batch. "
                    f"Expected '{_INTERRUPTION_PAIR[start_ev]}' to close it."
                ),
                event_index=None,
                event_type=start_ev,
            ))

    return errors


def _validate_phase_precedence(
    items: List[NormalizedSOFItem],
) -> List[SOFNormalizationError]:
    """
    Enforce coarse phase ordering:
      Phase 0 (arrival/NOR) events must not appear after Phase 5 (departure) events.
      Phase 3 (discharge) events must not appear after Phase 5 (departure) events.

    Phase-agnostic events (interruptions, -1) are exempt.
    """
    errors: List[SOFNormalizationError] = []

    # Find the earliest Phase-5 (departure) timestamp
    earliest_departure_ts: Optional[datetime.datetime] = None
    for item in items:
        ph = _PHASE_ORDER.get(item.event_type, -1)
        if ph == 5:
            if earliest_departure_ts is None or item.event_timestamp < earliest_departure_ts:
                earliest_departure_ts = item.event_timestamp

    if earliest_departure_ts is None:
        return errors

    # Any arrival (phase 0) or discharge (phase 3) event AFTER earliest departure = error
    for item in items:
        ph = _PHASE_ORDER.get(item.event_type, -1)
        if ph in (0, 3) and item.event_timestamp > earliest_departure_ts:
            errors.append(SOFNormalizationError(
                code="PHASE_PRECEDENCE_VIOLATION",
                detail=(
                    f"Event '{item.event_type}' (phase {ph}) at "
                    f"'{item.event_timestamp.isoformat()}' occurs AFTER "
                    f"the earliest departure-phase event at "
                    f"'{earliest_departure_ts.isoformat()}'. "
                    "Arrival and discharge events must precede departure events."
                ),
                event_index=item._original_index,
                event_type=item.event_type,
            ))

    return errors


# ---------------------------------------------------------------------------
# Utility: Build existing interruption state from a queryset/list of SOF records
# ---------------------------------------------------------------------------

def build_existing_interruption_state(
    sof_events: Sequence,
) -> Dict[str, Optional[datetime.datetime]]:
    """
    Compute the open-interval state from an ordered sequence of persisted
    ``StatementOfFacts`` records (sorted by event_timestamp ascending).

    Returns mapping:
        { start_event_type: timestamp_if_open_else_None }

    Only includes interruption start types that have appeared at least once.
    """
    end_to_start = {v: k for k, v in _INTERRUPTION_PAIR.items()}
    state: Dict[str, Optional[datetime.datetime]] = {}

    for sof in sof_events:
        ev = sof.event_type
        if ev in _INTERRUPTION_PAIR:
            state[ev] = sof.event_timestamp
        elif ev in end_to_start:
            start_ev = end_to_start[ev]
            state[start_ev] = None

    return state
