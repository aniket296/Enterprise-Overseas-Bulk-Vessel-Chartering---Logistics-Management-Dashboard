"""
backend/apps/voyages/laytime_balance.py
============================================================
Cycle 2.2.4 — Live Laytime Balance Calculator

A pure read-only, on-demand orchestrator that runs the complete 4-stage
laytime calculation pipeline for a voyage and returns a fully auditable
``LaytimeBalanceSnapshot`` — no DB writes.

Pipeline
--------
Stage 1  AllowedLaytimeEngine      — compute contractual allowed hours
Stage 2  TimeSliceDiscretizationEngine — segment SOF timeline into slices
Stage 3  InterruptionRulesEvaluator — evaluate counting fraction per slice
Stage 4  LaytimeSettlementEngine   — cumulative consumption + outcome + USD

Output (LaytimeBalanceSnapshot)
--------------------------------
- outcome            : PENDING / IN_PROGRESS / DEMURRAGE / DESPATCH / EVEN_KEEL
- allowed_hours      : Contractual allowed laytime (Decimal, 6dp)
- counted_hours      : Actual laytime consumed so far (Decimal, 6dp)
- excepted_hours     : Hours excluded from counting (Decimal, 6dp)
- balance_hours      : counted_hours - allowed_hours (positive = over, negative = under)
- demurrage_hours    : Hours on demurrage (0 if still in laytime)
- despatch_hours     : Hours saved (0 if over-time or even keel)
- gross_demurrage_usd: Estimated demurrage amount (Decimal, 2dp)
- gross_despatch_usd : Estimated despatch amount (Decimal, 2dp)
- net_settlement_usd : Net financial position (+ = owner earns, - = owner pays)
- demurrage_commenced_at : UTC timestamp when demurrage started (or None)
- is_final           : True if voyage is fully discharged (discharge_completed_at set)
- audit_formula      : Human-readable financial formula string
- slice_count        : Number of evaluated time slices
- as_of_timestamp    : UTC timestamp this calculation was computed

Error Handling
--------------
Returns a ``LaytimeBalanceError`` when prerequisite milestones are missing
(e.g. no SOF events, no laytime start timestamp, no discharge rate on contract).
The API surfaces this as HTTP 409 CONFLICT with an ``error`` field.

Air-Gap Compliance
------------------
No external calls, no caching, no Celery — pure in-process deterministic
Decimal arithmetic. Every call re-runs from DB records.
"""

from __future__ import annotations

import decimal
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from apps.laytime.engine.allowed_laytime import AllowedLaytimeEngine
from apps.laytime.engine.constants import LaytimeTerms, INDEFINITE_LAYTIME_TERMS
from apps.laytime.engine.interruption_evaluator import InterruptionRulesEvaluator
from apps.laytime.engine.settlement_engine import LaytimeSettlementEngine, LaytimeOutcome
from apps.laytime.engine.time_slice import (
    SOFEvent as EngineSOFEvent,
    SOFEventType as EngineSOFEventType,
    TimeSliceDiscretizationEngine,
)

logger = logging.getLogger(__name__)

_6DP = Decimal("0.000001")
_2DP = Decimal("0.01")
_ENGINE_CONTEXT = decimal.Context(
    prec=28,
    rounding=ROUND_HALF_UP,
    traps=[decimal.DivisionByZero, decimal.InvalidOperation, decimal.Overflow],
)


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------
class LaytimeBalanceError(Exception):
    """
    Raised when the balance cannot be computed because prerequisite data
    is missing or the voyage is not yet in a computable state.

    Attributes
    ----------
    code   : machine-readable error code (e.g. ``"NO_SOF_EVENTS"``)
    detail : human-readable explanation
    """
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Result value-objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LaytimeBalanceSnapshot:
    """
    Immutable point-in-time laytime balance for a voyage.

    All monetary values are in USD.
    All duration values are in fractional hours (Decimal, 6 decimal places).
    """
    voyage_id: str
    voyage_reference: str
    outcome: str                        # LaytimeOutcome.value
    is_final: bool                      # discharge_completed_at is set
    is_indefinite_laytime: bool         # CQD / AS_FAST_AS_CAN — no fixed allowance

    # Hours
    allowed_hours: Optional[Decimal]    # None for CQD / AS_FAST_AS_CAN
    counted_hours: Decimal
    excepted_hours: Decimal
    balance_hours: Decimal              # counted - allowed (positive = over-time)
    demurrage_hours: Decimal
    despatch_hours: Decimal

    # Financial
    gross_demurrage_usd: Decimal
    gross_despatch_usd: Decimal
    net_settlement_usd: Decimal         # positive: charterer owes shipowner; negative: owner owes charterer

    # Timestamps
    demurrage_commenced_at: Optional[datetime]
    laytime_commenced_at: Optional[datetime]
    discharge_completed_at: Optional[datetime]
    as_of_timestamp: datetime

    # Contract inputs (echo for audit)
    laytime_terms: str
    demurrage_rate_usd_per_day: Decimal
    despatch_rate_usd_per_day: Decimal

    # Audit
    audit_formula: str
    allowed_laytime_formula: str
    slice_count: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_laytime_balance(
    voyage,
    *,
    public_holidays: Optional[frozenset] = None,
    saturday_cutoff: bool = True,
    crane_pro_rata: bool = True,
) -> LaytimeBalanceSnapshot:
    """
    Run the complete laytime calculation pipeline for ``voyage`` and return
    an immutable ``LaytimeBalanceSnapshot``.

    Parameters
    ----------
    voyage :
        A ``VesselVoyage`` ORM instance with ``contract`` and ``sof_events``
        pre-fetched (or queryable via reverse FK).
    public_holidays :
        Optional ``frozenset[datetime.date]`` of public holiday dates at the
        discharge port.
    saturday_cutoff :
        Use Baltic Exchange Saturday 17:00 cutoff for SHEX/WWDSHEX (default True).
    crane_pro_rata :
        Use pro-rata fraction for partial crane breakdowns (default True).

    Returns
    -------
    LaytimeBalanceSnapshot

    Raises
    ------
    LaytimeBalanceError
        When prerequisite data is missing.
    """
    public_holidays = public_holidays or frozenset()
    contract = voyage.contract
    as_of = datetime.now(tz=timezone.utc)

    # ──────────────────────────────────────────────────────────────────────
    # Stage 0: Prerequisite checks
    # ──────────────────────────────────────────────────────────────────────
    sof_qs = (
        voyage.sof_events
        .all()
        .order_by("sequence_number", "event_timestamp")
    )
    sof_records = list(sof_qs)

    if not sof_records:
        raise LaytimeBalanceError(
            code="NO_SOF_EVENTS",
            detail=f"Voyage {voyage.voyage_reference} has no Statement of Facts events. "
                   f"Ingest at least one SOF event before computing laytime balance.",
        )

    # Determine laytime commencement timestamp
    laytime_commences_at = _resolve_laytime_commencement(voyage)
    if laytime_commences_at is None:
        raise LaytimeBalanceError(
            code="NO_LAYTIME_COMMENCEMENT",
            detail="Laytime has not yet commenced. Either NOR has not been tendered "
                   "or the discharge has not started. Ingest the relevant SOF milestone.",
        )

    discharge_completed_at = voyage.discharge_completed_at
    is_final = discharge_completed_at is not None

    # ──────────────────────────────────────────────────────────────────────
    # Stage 1: Allowed Laytime
    # ──────────────────────────────────────────────────────────────────────
    try:
        laytime_terms = LaytimeTerms(contract.laytime_terms)
    except ValueError:
        laytime_terms = LaytimeTerms.SHINC
        logger.warning("Unknown laytime terms '%s' — defaulting SHINC.", contract.laytime_terms)

    is_indefinite = laytime_terms in INDEFINITE_LAYTIME_TERMS

    allowed_engine = AllowedLaytimeEngine(
        cargo_mt_nominated=contract.nominated_quantity_mt,
        discharge_rate_mt_day=contract.discharge_rate_mt_per_day,
        laytime_terms=laytime_terms,
        override_allowed_hours=(
            contract.allowed_laytime_hours
            if contract.allowed_laytime_hours and contract.allowed_laytime_hours > Decimal("0")
            else None
        ),
    )
    allowed_result = allowed_engine.compute()
    allowed_hours = allowed_result.allowed_hours_nominated   # None for CQD / AS_FAST_AS_CAN
    allowed_formula = allowed_result.formula_string

    if is_indefinite:
        # CQD / AS_FAST_AS_CAN: return a minimal snapshot — no financial computation possible
        return LaytimeBalanceSnapshot(
            voyage_id=str(voyage.voyage_id),
            voyage_reference=voyage.voyage_reference,
            outcome=LaytimeOutcome.PENDING.value,
            is_final=is_final,
            is_indefinite_laytime=True,
            allowed_hours=None,
            counted_hours=Decimal("0.000000"),
            excepted_hours=Decimal("0.000000"),
            balance_hours=Decimal("0.000000"),
            demurrage_hours=Decimal("0.000000"),
            despatch_hours=Decimal("0.000000"),
            gross_demurrage_usd=Decimal("0.00"),
            gross_despatch_usd=Decimal("0.00"),
            net_settlement_usd=Decimal("0.00"),
            demurrage_commenced_at=None,
            laytime_commenced_at=laytime_commences_at,
            discharge_completed_at=discharge_completed_at,
            as_of_timestamp=as_of,
            laytime_terms=laytime_terms.value,
            demurrage_rate_usd_per_day=Decimal(str(contract.demurrage_rate_usd_per_day)),
            despatch_rate_usd_per_day=Decimal(str(contract.despatch_rate_usd_per_day)),
            audit_formula="CQD/AS_FAST_AS_CAN: no fixed financial settlement computable.",
            allowed_laytime_formula=allowed_formula,
            slice_count=0,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Stage 2: Discretize SOF timeline
    # ──────────────────────────────────────────────────────────────────────
    engine_events = _build_engine_events(sof_records)

    if len(engine_events) < 2:
        raise LaytimeBalanceError(
            code="INSUFFICIENT_SOF_EVENTS",
            detail="At least two distinct SOF event timestamps are required to form time slices. "
                   "Ingest additional SOF events.",
        )

    try:
        discretizer = TimeSliceDiscretizationEngine(
            events=engine_events,
            laytime_commences_at=laytime_commences_at,
            discharge_completed_at=discharge_completed_at,
        )
        discretization = discretizer.discretize()
    except ValueError as exc:
        raise LaytimeBalanceError(
            code="DISCRETIZATION_ERROR",
            detail=f"Timeline discretization failed: {exc}",
        ) from exc

    # ──────────────────────────────────────────────────────────────────────
    # Stage 3: Evaluate interruption rules
    # ──────────────────────────────────────────────────────────────────────
    evaluator = InterruptionRulesEvaluator(
        laytime_terms=laytime_terms,
        saturday_cutoff=saturday_cutoff,
        public_holidays=public_holidays,
        crane_pro_rata=crane_pro_rata,
    )
    evaluation = evaluator.evaluate(list(discretization.slices))

    # ──────────────────────────────────────────────────────────────────────
    # Stage 4: Settlement
    # ──────────────────────────────────────────────────────────────────────
    dem_rate = Decimal(str(contract.demurrage_rate_usd_per_day))
    des_rate = Decimal(str(contract.despatch_rate_usd_per_day))

    settlement_engine = LaytimeSettlementEngine(
        allowed_laytime_hours=allowed_hours,
        demurrage_rate_usd_per_day=dem_rate,
        despatch_rate_usd_per_day=des_rate,
        once_on_demurrage_always_on=True,   # Baltic Exchange standard
    )
    settlement = settlement_engine.settle(list(evaluation.slices))

    # ──────────────────────────────────────────────────────────────────────
    # Build snapshot
    # ──────────────────────────────────────────────────────────────────────
    # Outcome: IN_PROGRESS if voyage not yet complete and no demurrage yet
    if not is_final and settlement.outcome == LaytimeOutcome.DESPATCH:
        outcome_str = LaytimeOutcome.IN_PROGRESS.value
    else:
        outcome_str = settlement.outcome.value

    with decimal.localcontext(_ENGINE_CONTEXT):
        # balance_hours = total time consumed (laytime + demurrage) - allowed
        # The settlement engine caps total_laytime_counted_hours at allowed_hours;
        # any excess time is in total_demurrage_hours.
        # Positive balance = over-time (demurrage); negative = time saved (despatch).
        balance_hours = (
            settlement.total_laytime_counted_hours
            + settlement.total_demurrage_hours
            - allowed_hours
        ).quantize(_6DP, rounding=ROUND_HALF_UP)

    return LaytimeBalanceSnapshot(
        voyage_id=str(voyage.voyage_id),
        voyage_reference=voyage.voyage_reference,
        outcome=outcome_str,
        is_final=is_final,
        is_indefinite_laytime=False,
        allowed_hours=allowed_hours,
        counted_hours=settlement.total_laytime_counted_hours,
        excepted_hours=settlement.total_excepted_hours,
        balance_hours=balance_hours,
        demurrage_hours=settlement.total_demurrage_hours,
        despatch_hours=settlement.total_despatch_hours,
        gross_demurrage_usd=settlement.gross_demurrage_usd,
        gross_despatch_usd=settlement.gross_despatch_usd,
        net_settlement_usd=settlement.net_settlement_usd,
        demurrage_commenced_at=settlement.demurrage_commenced_at,
        laytime_commenced_at=laytime_commences_at,
        discharge_completed_at=discharge_completed_at,
        as_of_timestamp=as_of,
        laytime_terms=laytime_terms.value,
        demurrage_rate_usd_per_day=dem_rate,
        despatch_rate_usd_per_day=des_rate,
        audit_formula=settlement.audit_formula,
        allowed_laytime_formula=allowed_formula,
        slice_count=len(evaluation.slices),
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _resolve_laytime_commencement(voyage) -> Optional[datetime]:
    """
    Resolve the laytime commencement timestamp using the following priority:
      1. voyage.discharge_commenced_at  (discharge actually started)
      2. voyage.nor_accepted_at + turn_time — not available without TurnTimeEvaluator;
         use nor_tendered_at as conservative fallback for partial voyages
      3. voyage.nor_tendered_at

    Returns None if no NOR has been tendered yet.
    """
    if voyage.discharge_commenced_at:
        return voyage.discharge_commenced_at
    if voyage.nor_accepted_at:
        return voyage.nor_accepted_at
    if voyage.nor_tendered_at:
        return voyage.nor_tendered_at
    return None


def _build_engine_events(sof_records) -> list:
    """
    Translate StatementOfFacts DB records into EngineSOFEvent objects.
    Records with unrecognised event_type are silently skipped.
    """
    known_types = {e.value for e in EngineSOFEventType}
    result = []

    for rec in sof_records:
        if rec.event_type not in known_types:
            continue
        ts = rec.event_timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        try:
            result.append(EngineSOFEvent(
                timestamp=ts,
                event_type=EngineSOFEventType(rec.event_type),
                sequence_number=rec.sequence_number,
                description=rec.event_description or "",
                cranes_working=rec.crane_number,
                cranes_total=None,
                sof_id=str(rec.sof_id),
            ))
        except Exception as exc:
            logger.debug("Skipping SOF record %s: %s", rec.sof_id, exc)

    return result
