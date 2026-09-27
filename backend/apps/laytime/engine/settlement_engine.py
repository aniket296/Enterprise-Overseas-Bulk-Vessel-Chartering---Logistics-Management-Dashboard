r"""
backend/apps/laytime/engine/settlement_engine.py
============================================================
Cycle 1.2.5 — Demurrage State Transitions & Despatch Settlement Engine

Implements the definitive cumulative laytime consumption engine and
the core maritime legal principle:
    "Once on demurrage, always on demurrage" (Semper in mora)

Maritime Legal & Financial Principles
-------------------------------------
1. Laytime vs. Demurrage State Transition:
   - While cumulative laytime < allowed_laytime_hours, contractual exceptions
     (SHEX weekends, WWD weather halts, crane breakdowns) suspend laytime.
   - At the exact timestamp T_demurrage when cumulative laytime equals
     allowed_laytime_hours, the vessel enters DEMURRAGE.
   - If `once_on_demurrage_always_on` is True (Baltic Exchange standard):
     From T_demurrage onward, all elapsed wall-clock time counts as
     demurrage, regardless of bad weather, weekends, or holidays.
     Exceptions no longer apply unless expressly drafted to apply while
     on demurrage.

2. Clean Threshold Splitting:
   - If a time slice crosses the boundary where allowed laytime expires,
     it is mathematically split into:
       Slice A: [start, T_demurrage) — final laytime consumption
       Slice B: [T_demurrage, end)   — initial demurrage consumption

3. Despatch Settlement ("All Time Saved" vs "Working Time Saved"):
   - If laytime consumed < allowed_laytime_hours at discharge completion:
       time_saved = allowed_laytime_hours - laytime_consumed
       outcome = DESPATCH
       gross_despatch_usd = time_saved * (despatch_rate_usd_per_day / 24)
       gross_demurrage_usd = 0.00
       net_settlement_usd = -gross_despatch_usd (owner owes charterer)

4. Demurrage Settlement:
   - If laytime consumed >= allowed_laytime_hours:
       outcome = DEMURRAGE
       gross_demurrage_usd = demurrage_hours * (demurrage_rate_usd_per_day / 24)
       gross_despatch_usd = 0.00
       net_settlement_usd = gross_demurrage_usd (charterer owes owner)

5. Strict Database Integrity Invariant:
   (gross_demurrage_usd == 0 OR gross_despatch_usd == 0)
   net_settlement_usd == gross_demurrage_usd - gross_despatch_usd
   variance_hours == total_counted_hours - allowed_laytime_hours
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional, Sequence

from .constants import HOURS_PER_DAY
from .time_slice import OperationalState
from .interruption_evaluator import EvaluatedSlice

# ---------------------------------------------------------------------------
# Decimal precision context
# ---------------------------------------------------------------------------
_ENGINE_CONTEXT = decimal.Context(
    prec=28,
    rounding=ROUND_HALF_UP,
    traps=[decimal.DivisionByZero, decimal.InvalidOperation, decimal.Overflow],
)
_6DP = Decimal("0.000001")
_2DP = Decimal("0.01")
_SECONDS_PER_HOUR = Decimal("3600")


# ---------------------------------------------------------------------------
# Laytime Outcome Enum mirroring V001 DB schema
# ---------------------------------------------------------------------------
class LaytimeOutcome(str, Enum):
    PENDING     = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    DESPATCH    = "DESPATCH"
    DEMURRAGE   = "DEMURRAGE"
    EVEN_KEEL   = "EVEN_KEEL"
    DISPUTED    = "DISPUTED"
    SETTLED     = "SETTLED"


# ---------------------------------------------------------------------------
# Settlement Domain Models
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SettlementSlice:
    """Individual slice annotated with cumulative laytime and demurrage accounting."""
    slice_index: int
    start_time: datetime
    end_time: datetime
    duration_hours: Decimal
    laytime_counted_hours: Decimal
    demurrage_counted_hours: Decimal
    excepted_hours: Decimal
    cumulative_laytime_hours: Decimal
    cumulative_demurrage_hours: Decimal
    is_demurrage: bool
    deduction_reason: Optional[str]
    operational_state: OperationalState

    def __post_init__(self) -> None:
        if self.start_time >= self.end_time:
            raise ValueError(f"Invalid settlement slice: {self.start_time} >= {self.end_time}")

    def __str__(self) -> str:
        state = "DEMURRAGE" if self.is_demurrage else "LAYTIME"
        return (
            f"SetSlice #{self.slice_index} [{state}] "
            f"{self.start_time.strftime('%Y-%m-%d %H:%M')} -> {self.end_time.strftime('%H:%M')} "
            f"Dur={self.duration_hours}h | Lay={self.laytime_counted_hours}h (cum={self.cumulative_laytime_hours}h), "
            f"Dem={self.demurrage_counted_hours}h (cum={self.cumulative_demurrage_hours}h)"
        )


@dataclass(frozen=True)
class SettlementResult:
    """Immutable final settlement result for the voyage laytime calculation."""
    outcome: LaytimeOutcome
    allowed_laytime_hours: Decimal
    total_port_hours: Decimal
    total_laytime_counted_hours: Decimal
    total_demurrage_hours: Decimal
    total_despatch_hours: Decimal
    total_excepted_hours: Decimal
    variance_hours: Decimal
    demurrage_rate_usd_per_day: Decimal
    despatch_rate_usd_per_day: Decimal
    gross_demurrage_usd: Decimal
    gross_despatch_usd: Decimal
    net_settlement_usd: Decimal
    demurrage_commenced_at: Optional[datetime]
    slices: tuple[SettlementSlice, ...]
    once_on_demurrage_always_on: bool
    audit_formula: str

    def __str__(self) -> str:
        if self.outcome == LaytimeOutcome.DEMURRAGE:
            fin = f"Demurrage: {self.total_demurrage_hours}h @ ${self.demurrage_rate_usd_per_day}/day = ${self.gross_demurrage_usd} USD"
        elif self.outcome == LaytimeOutcome.DESPATCH:
            fin = f"Despatch: {self.total_despatch_hours}h @ ${self.despatch_rate_usd_per_day}/day = ${self.gross_despatch_usd} USD"
        else:
            fin = "Even Keel: Laytime exactly balanced ($0.00 USD)"

        return (
            f"SettlementResult[{self.outcome.value}]: "
            f"Allowed={self.allowed_laytime_hours}h, Used={self.total_laytime_counted_hours}h, "
            f"Var={self.variance_hours}h | {fin}"
        )


# ---------------------------------------------------------------------------
# Settlement Engine
# ---------------------------------------------------------------------------
class LaytimeSettlementEngine:
    """
    Computes cumulative laytime consumption, state transitions to demurrage,
    and final monetary settlement in USD.
    """

    def __init__(
        self,
        allowed_laytime_hours: Decimal | str | float,
        demurrage_rate_usd_per_day: Decimal | str | float,
        despatch_rate_usd_per_day: Decimal | str | float,
        once_on_demurrage_always_on: bool = True,
    ) -> None:
        with decimal.localcontext(_ENGINE_CONTEXT):
            self._allowed_hours = Decimal(str(allowed_laytime_hours)).quantize(_6DP, rounding=ROUND_HALF_UP)
            self._demurrage_rate = Decimal(str(demurrage_rate_usd_per_day)).quantize(_2DP, rounding=ROUND_HALF_UP)
            self._despatch_rate = Decimal(str(despatch_rate_usd_per_day)).quantize(_2DP, rounding=ROUND_HALF_UP)

        self._once_on_demurrage = once_on_demurrage_always_on

        if self._allowed_hours <= Decimal("0"):
            raise ValueError(f"allowed_laytime_hours must be strictly positive; got {self._allowed_hours}")
        if self._demurrage_rate < Decimal("0"):
            raise ValueError(f"demurrage_rate_usd_per_day must be non-negative; got {self._demurrage_rate}")
        if self._despatch_rate < Decimal("0"):
            raise ValueError(f"despatch_rate_usd_per_day must be non-negative; got {self._despatch_rate}")
        if self._despatch_rate > self._demurrage_rate:
            raise ValueError(
                f"despatch_rate ({self._despatch_rate}) cannot exceed demurrage_rate ({self._demurrage_rate})."
            )

    def settle(self, evaluated_slices: Sequence[EvaluatedSlice]) -> SettlementResult:
        """
        Process evaluated time slices chronologically, managing laytime consumption
        and demurrage state transitions.
        """
        slices: list[SettlementSlice] = []
        global_idx = 0

        cum_laytime = Decimal("0.000000")
        cum_demurrage = Decimal("0.000000")
        is_on_demurrage = False
        demurrage_commenced_at: Optional[datetime] = None

        for es in evaluated_slices:
            if is_on_demurrage:
                # ── ALREADY ON DEMURRAGE ──────────────────────────────────
                if self._once_on_demurrage:
                    # Once on demurrage, always on demurrage: all clock hours count
                    dem_hours = es.duration_hours
                    excepted = Decimal("0.000000")
                    reason = "On Demurrage (All Time Counts)"
                else:
                    # Exceptions still apply
                    dem_hours = es.counted_hours
                    excepted = es.excepted_hours
                    reason = es.deduction_reason

                cum_demurrage = (cum_demurrage + dem_hours).quantize(_6DP, rounding=ROUND_HALF_UP)

                slices.append(
                    SettlementSlice(
                        slice_index=global_idx,
                        start_time=es.start_time,
                        end_time=es.end_time,
                        duration_hours=es.duration_hours,
                        laytime_counted_hours=Decimal("0.000000"),
                        demurrage_counted_hours=dem_hours,
                        excepted_hours=excepted,
                        cumulative_laytime_hours=cum_laytime,
                        cumulative_demurrage_hours=cum_demurrage,
                        is_demurrage=True,
                        deduction_reason=reason,
                        operational_state=es.operational_state,
                    )
                )
                global_idx += 1

            else:
                # ── IN LAYTIME PERIOD ─────────────────────────────────────
                pot_laytime = es.counted_hours
                rem_allowed = self._allowed_hours - cum_laytime

                if pot_laytime < rem_allowed:
                    # Entire slice consumed without breaching allowed laytime
                    cum_laytime = (cum_laytime + pot_laytime).quantize(_6DP, rounding=ROUND_HALF_UP)

                    slices.append(
                        SettlementSlice(
                            slice_index=global_idx,
                            start_time=es.start_time,
                            end_time=es.end_time,
                            duration_hours=es.duration_hours,
                            laytime_counted_hours=pot_laytime,
                            demurrage_counted_hours=Decimal("0.000000"),
                            excepted_hours=es.excepted_hours,
                            cumulative_laytime_hours=cum_laytime,
                            cumulative_demurrage_hours=cum_demurrage,
                            is_demurrage=False,
                            deduction_reason=es.deduction_reason,
                            operational_state=es.operational_state,
                        )
                    )
                    global_idx += 1

                elif pot_laytime == rem_allowed:
                    # Exactly reaches demurrage threshold at end of slice
                    cum_laytime = self._allowed_hours
                    is_on_demurrage = True
                    demurrage_commenced_at = es.end_time

                    slices.append(
                        SettlementSlice(
                            slice_index=global_idx,
                            start_time=es.start_time,
                            end_time=es.end_time,
                            duration_hours=es.duration_hours,
                            laytime_counted_hours=pot_laytime,
                            demurrage_counted_hours=Decimal("0.000000"),
                            excepted_hours=es.excepted_hours,
                            cumulative_laytime_hours=cum_laytime,
                            cumulative_demurrage_hours=cum_demurrage,
                            is_demurrage=False,
                            deduction_reason=es.deduction_reason,
                            operational_state=es.operational_state,
                        )
                    )
                    global_idx += 1

                else:
                    # ── TRANSITION: Slice breaches allowed laytime! ─────────
                    # Split slice at the exact moment of laytime exhaustion
                    # Fraction of time in this slice before threshold:
                    # rem_allowed = time_to_thresh_hours * es.count_fraction
                    if es.count_fraction > Decimal("0"):
                        time_to_thresh_hours = (rem_allowed / es.count_fraction).quantize(_6DP, rounding=ROUND_HALF_UP)
                    else:
                        time_to_thresh_hours = rem_allowed

                    thresh_sec = int((time_to_thresh_hours * _SECONDS_PER_HOUR).to_integral_value(ROUND_HALF_UP))
                    thresh_dt = es.start_time + timedelta(seconds=thresh_sec)

                    # Clamp to ensure strict bounds
                    if thresh_dt <= es.start_time:
                        thresh_dt = es.start_time + timedelta(seconds=1)
                    elif thresh_dt >= es.end_time:
                        thresh_dt = es.end_time - timedelta(seconds=1)

                    # Sub-slice 1: Final laytime consumption
                    dur_lay_sec = int((thresh_dt - es.start_time).total_seconds())
                    with decimal.localcontext(_ENGINE_CONTEXT):
                        dur_lay_hours = (Decimal(str(dur_lay_sec)) / _SECONDS_PER_HOUR).quantize(_6DP, rounding=ROUND_HALF_UP)
                        counted_lay = rem_allowed
                        excepted_lay = (dur_lay_hours - counted_lay).quantize(_6DP, rounding=ROUND_HALF_UP)

                    cum_laytime = self._allowed_hours
                    is_on_demurrage = True
                    demurrage_commenced_at = thresh_dt

                    slices.append(
                        SettlementSlice(
                            slice_index=global_idx,
                            start_time=es.start_time,
                            end_time=thresh_dt,
                            duration_hours=dur_lay_hours,
                            laytime_counted_hours=counted_lay,
                            demurrage_counted_hours=Decimal("0.000000"),
                            excepted_hours=max(excepted_lay, Decimal("0.000000")),
                            cumulative_laytime_hours=cum_laytime,
                            cumulative_demurrage_hours=cum_demurrage,
                            is_demurrage=False,
                            deduction_reason="Allowed Laytime Expired (Final Laytime)",
                            operational_state=es.operational_state,
                        )
                    )
                    global_idx += 1

                    # Sub-slice 2: Initial demurrage consumption
                    dur_dem_sec = int((es.end_time - thresh_dt).total_seconds())
                    with decimal.localcontext(_ENGINE_CONTEXT):
                        dur_dem_hours = (Decimal(str(dur_dem_sec)) / _SECONDS_PER_HOUR).quantize(_6DP, rounding=ROUND_HALF_UP)
                        if self._once_on_demurrage:
                            dem_hours = dur_dem_hours
                            excepted_dem = Decimal("0.000000")
                            reason = "Demurrage Commenced (Once on Demurrage, Always on Demurrage)"
                        else:
                            dem_hours = (dur_dem_hours * es.count_fraction).quantize(_6DP, rounding=ROUND_HALF_UP)
                            excepted_dem = (dur_dem_hours * (Decimal("1") - es.count_fraction)).quantize(_6DP, rounding=ROUND_HALF_UP)
                            reason = "Demurrage Commenced"

                    cum_demurrage = (cum_demurrage + dem_hours).quantize(_6DP, rounding=ROUND_HALF_UP)

                    slices.append(
                        SettlementSlice(
                            slice_index=global_idx,
                            start_time=thresh_dt,
                            end_time=es.end_time,
                            duration_hours=dur_dem_hours,
                            laytime_counted_hours=Decimal("0.000000"),
                            demurrage_counted_hours=dem_hours,
                            excepted_hours=excepted_dem,
                            cumulative_laytime_hours=cum_laytime,
                            cumulative_demurrage_hours=cum_demurrage,
                            is_demurrage=True,
                            deduction_reason=reason,
                            operational_state=es.operational_state,
                        )
                    )
                    global_idx += 1

        # ── FINANCIAL COMPUTATION & OUTCOME ─────────────────────────────────
        total_port_hours = sum((s.duration_hours for s in slices), Decimal("0.000000"))
        total_laytime_hours = cum_laytime
        total_demurrage_hours = cum_demurrage
        total_excepted_hours = sum((s.excepted_hours for s in slices), Decimal("0.000000"))

        hourly_dem_rate = self._demurrage_rate / HOURS_PER_DAY
        hourly_des_rate = self._despatch_rate / HOURS_PER_DAY

        if total_demurrage_hours > Decimal("0.000000"):
            outcome = LaytimeOutcome.DEMURRAGE
            variance_hours = total_demurrage_hours
            total_despatch_hours = Decimal("0.000000")

            with decimal.localcontext(_ENGINE_CONTEXT):
                gross_dem = (total_demurrage_hours * hourly_dem_rate).quantize(_2DP, rounding=ROUND_HALF_UP)
                gross_des = Decimal("0.00")
                net_settlement = gross_dem

            formula = (
                f"DEMURRAGE: {total_demurrage_hours} hours on demurrage × "
                f"(${self._demurrage_rate}/day ÷ 24 = ${hourly_dem_rate.quantize(Decimal('0.0001'))}/h) "
                f"= ${gross_dem} USD"
            )

        elif total_laytime_hours < self._allowed_hours:
            outcome = LaytimeOutcome.DESPATCH
            time_saved = self._allowed_hours - total_laytime_hours
            variance_hours = -time_saved
            total_despatch_hours = time_saved

            with decimal.localcontext(_ENGINE_CONTEXT):
                gross_dem = Decimal("0.00")
                gross_des = (time_saved * hourly_des_rate).quantize(_2DP, rounding=ROUND_HALF_UP)
                net_settlement = -gross_des  # Owner owes charterer

            formula = (
                f"DESPATCH: {time_saved} hours saved × "
                f"(${self._despatch_rate}/day ÷ 24 = ${hourly_des_rate.quantize(Decimal('0.0001'))}/h) "
                f"= ${gross_des} USD"
            )

        else:
            outcome = LaytimeOutcome.EVEN_KEEL
            variance_hours = Decimal("0.000000")
            total_despatch_hours = Decimal("0.000000")
            gross_dem = Decimal("0.00")
            gross_des = Decimal("0.00")
            net_settlement = Decimal("0.00")
            formula = "EVEN KEEL: Laytime consumed exactly equals allowed laytime ($0.00 USD)"

        return SettlementResult(
            outcome=outcome,
            allowed_laytime_hours=self._allowed_hours,
            total_port_hours=total_port_hours,
            total_laytime_counted_hours=total_laytime_hours,
            total_demurrage_hours=total_demurrage_hours,
            total_despatch_hours=total_despatch_hours,
            total_excepted_hours=total_excepted_hours,
            variance_hours=variance_hours,
            demurrage_rate_usd_per_day=self._demurrage_rate,
            despatch_rate_usd_per_day=self._despatch_rate,
            gross_demurrage_usd=gross_dem,
            gross_despatch_usd=gross_des,
            net_settlement_usd=net_settlement,
            demurrage_commenced_at=demurrage_commenced_at,
            slices=tuple(slices),
            once_on_demurrage_always_on=self._once_on_demurrage,
            audit_formula=formula,
        )
