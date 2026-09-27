r"""
backend/apps/laytime/engine/interruption_evaluator.py
============================================================
Cycle 1.2.4 — Working Day & Interruption Rules Evaluator

Applies contractual charter party terms (SHINC, SHEX, SHEX_EIU, FHINC,
FHEX, WWD, WWDSHEX) and operational interruption rules to discretized
time slices, computing for each slice:
  - Counting fraction: $f \in [0.0, 1.0]$
  - Counted duration: $\Delta T_{counted} = \Delta T \times f$
  - Excepted duration: $\Delta T_{excepted} = \Delta T \times (1 - f)$
  - Count status: FULLY_COUNTED, PARTIALLY_COUNTED, or EXCLUDED
  - Deduction reason (audit ledger annotation)

Contractual Laytime Rules Implemented
-------------------------------------
1. Pre-commencement & Post-completion:
   - Slices before laytime_commences_at or after discharge_completed_at
     are 100% EXCLUDED ($f = 0.0$).

2. Rest Days & Weekends (SHEX, WWDSHEX, FHEX):
   - SHEX / WWDSHEX: Saturday 17:00 → Monday 08:00 (Baltic cutoff)
     or Sunday 00:00 → 24:00 (pure Sunday).
   - FHEX: Friday 00:00 → 24:00.
   - Public holidays: 00:00 → 24:00.
   - Under SHEX_EIU (Even If Used): Always EXCLUDED ($f = 0.0$) even if
     vessel works during excepted hours.
   - Under standard SHEX (non-EIU): Excepted period is EXCLUDED ($f = 0.0$)
     unless cargo operations are actually active (time used counts).

3. Weather Interruptions (WWD, WWDSHEX):
   - Under Weather Working Days: Rain, heavy swell, or excessive wind
     halting discharge are 100% EXCLUDED ($f = 0.0$).
   - Under non-WWD terms (e.g., pure SHINC): Weather is charterer's risk
     and continues to count ($f = 1.0$), unless custom exception applies.

4. Mechanical Interruptions (Crane Downtime / Power Failure):
   - Full Breakdown (cranes_working == 0): 100% EXCLUDED ($f = 0.0$).
   - Partial Breakdown (cranes_working < cranes_total):
     If crane_pro_rata is enabled:
       $f = \frac{\text{cranes\_working}}{\text{cranes\_total}}$
     Status: PARTIALLY_COUNTED.

5. Sub-Slice Calendar Boundary Refinement:
   - If an operational slice crosses a calendar boundary (e.g., Saturday
     17:00, Sunday midnight, Monday 08:00, or a public holiday midnight),
     it is cleanly partitioned at that boundary so each sub-slice has
     uniform contractual counting rules.

6. Mathematical Accounting Invariant:
   $\Delta T_{counted} + \Delta T_{excepted} \equiv \Delta T_{total}$
   verified to within $\pm 0.000010$ hours across the entire voyage ledger.
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Sequence

from .constants import (
    LaytimeTerms,
    SUNDAY_HOLIDAY_EXCLUDING_TERMS,
    FRIDAY_EXCLUDING_TERMS,
    WEATHER_WORKING_TERMS,
    EIU_TERMS,
)
from .time_slice import (
    TimeSlice,
    OperationalState,
    IntervalCountStatus,
)

# ---------------------------------------------------------------------------
# Decimal precision context
# ---------------------------------------------------------------------------
_ENGINE_CONTEXT = decimal.Context(
    prec=28,
    rounding=ROUND_HALF_UP,
    traps=[decimal.DivisionByZero, decimal.InvalidOperation, decimal.Overflow],
)
_6DP = Decimal("0.000001")
_SECONDS_PER_HOUR = Decimal("3600")


# ---------------------------------------------------------------------------
# Evaluated Slice Domain Models
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EvaluatedSlice:
    """An individual discrete time slice with its laytime counting valuation."""
    slice_index: int
    start_time: datetime
    end_time: datetime
    duration_seconds: int
    duration_hours: Decimal
    count_fraction: Decimal
    counted_hours: Decimal
    excepted_hours: Decimal
    count_status: IntervalCountStatus
    deduction_reason: Optional[str]
    operational_state: OperationalState
    is_demurrage: bool = False

    def __post_init__(self) -> None:
        if self.start_time >= self.end_time:
            raise ValueError(f"Invalid evaluated slice interval: {self.start_time} >= {self.end_time}")

    def __str__(self) -> str:
        reason = f" ({self.deduction_reason})" if self.deduction_reason else ""
        return (
            f"EvalSlice #{self.slice_index} "
            f"[{self.start_time.strftime('%Y-%m-%d %H:%M')} -> {self.end_time.strftime('%H:%M')}] "
            f"Dur={self.duration_hours}h, Frac={self.count_fraction} -> "
            f"Counted={self.counted_hours}h, Excepted={self.excepted_hours}h "
            f"[{self.count_status.value}]{reason}"
        )


@dataclass(frozen=True)
class InterruptionEvaluationResult:
    """Immutable aggregate result of the laytime interruption evaluation."""
    slices: tuple[EvaluatedSlice, ...]
    total_elapsed_hours: Decimal
    total_counted_hours: Decimal
    total_excepted_hours: Decimal
    accounting_invariant_holds: bool
    laytime_terms: LaytimeTerms
    notes: tuple[str, ...]


# ---------------------------------------------------------------------------
# Main Evaluator
# ---------------------------------------------------------------------------
class InterruptionRulesEvaluator:
    """
    Evaluates working day and operational interruption rules for TimeSlices.
    """

    def __init__(
        self,
        laytime_terms: LaytimeTerms = LaytimeTerms.WWDSHEX,
        saturday_cutoff: bool = True,
        public_holidays: Optional[frozenset[date]] = None,
        crane_pro_rata: bool = True,
        strike_clause_half_rate: bool = False,
    ) -> None:
        self._terms = LaytimeTerms(laytime_terms)
        self._sat_cut = saturday_cutoff
        self._holidays = public_holidays or frozenset()
        self._crane_pro_rata = crane_pro_rata
        self._strike_half = strike_clause_half_rate

        self._excludes_sundays_holidays = self._terms in SUNDAY_HOLIDAY_EXCLUDING_TERMS
        self._excludes_fridays = self._terms in FRIDAY_EXCLUDING_TERMS
        self._is_wwd = self._terms in WEATHER_WORKING_TERMS
        self._is_eiu = self._terms in EIU_TERMS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def evaluate(self, slices: Sequence[TimeSlice]) -> InterruptionEvaluationResult:
        """
        Evaluate each input TimeSlice, sub-partitioning at calendar boundaries
        if needed, and compute final counted and excepted hours.
        """
        notes: list[str] = []
        refined_slices: list[EvaluatedSlice] = []
        global_idx = 0

        for raw_slice in slices:
            sub_intervals = self._split_slice_at_calendar_boundaries(raw_slice.start_time, raw_slice.end_time)

            for sub_start, sub_end in sub_intervals:
                sub_sec = int((sub_end - sub_start).total_seconds())
                with decimal.localcontext(_ENGINE_CONTEXT):
                    sub_hours = (Decimal(str(sub_sec)) / _SECONDS_PER_HOUR).quantize(_6DP, rounding=ROUND_HALF_UP)

                # Determine counting fraction and status
                frac, status, reason = self._evaluate_interval(
                    start=sub_start,
                    end=sub_end,
                    state=raw_slice.state,
                    is_pre=raw_slice.is_pre_commencement,
                    is_post=raw_slice.is_post_completion,
                )

                with decimal.localcontext(_ENGINE_CONTEXT):
                    counted = (sub_hours * frac).quantize(_6DP, rounding=ROUND_HALF_UP)
                    excepted = (sub_hours * (Decimal("1") - frac)).quantize(_6DP, rounding=ROUND_HALF_UP)

                evaluated = EvaluatedSlice(
                    slice_index=global_idx,
                    start_time=sub_start,
                    end_time=sub_end,
                    duration_seconds=sub_sec,
                    duration_hours=sub_hours,
                    count_fraction=frac,
                    counted_hours=counted,
                    excepted_hours=excepted,
                    count_status=status,
                    deduction_reason=reason,
                    operational_state=raw_slice.state,
                )
                refined_slices.append(evaluated)
                global_idx += 1

        # Totals accounting
        total_elapsed = sum((s.duration_hours for s in refined_slices), Decimal("0.000000"))
        total_counted = sum((s.counted_hours for s in refined_slices), Decimal("0.000000"))
        total_excepted = sum((s.excepted_hours for s in refined_slices), Decimal("0.000000"))

        with decimal.localcontext(_ENGINE_CONTEXT):
            diff = abs((total_counted + total_excepted) - total_elapsed)
            invariant_holds = diff <= Decimal("0.000010")

        notes.append(
            f"Evaluated {len(refined_slices)} slice(s) under terms {self._terms.value}: "
            f"Total={total_elapsed}h, Counted={total_counted}h, Excepted={total_excepted}h "
            f"(accounting diff={diff})."
        )

        return InterruptionEvaluationResult(
            slices=tuple(refined_slices),
            total_elapsed_hours=total_elapsed,
            total_counted_hours=total_counted,
            total_excepted_hours=total_excepted,
            accounting_invariant_holds=invariant_holds,
            laytime_terms=self._terms,
            notes=tuple(notes),
        )

    # ------------------------------------------------------------------
    # Interval Evaluation Logic
    # ------------------------------------------------------------------
    def _evaluate_interval(
        self,
        start: datetime,
        end: datetime,
        state: OperationalState,
        is_pre: bool,
        is_post: bool,
    ) -> tuple[Decimal, IntervalCountStatus, Optional[str]]:
        """
        Evaluate a single uniform interval and return (count_fraction, status, reason).
        """
        # 1. Pre-commencement & Post-completion
        if is_pre:
            return Decimal("0.000000"), IntervalCountStatus.EXCLUDED, "Pre-commencement Turn-Time Buffer"
        if is_post:
            return Decimal("0.000000"), IntervalCountStatus.EXCLUDED, "Post-Discharge Operations"

        # 2. Rest Days / Weekends / Holidays (SHEX / WWDSHEX / FHEX)
        is_rest, rest_reason = self._is_rest_period(start, end)
        if is_rest:
            if self._is_eiu:
                # Even If Used: never counts
                return Decimal("0.000000"), IntervalCountStatus.EXCLUDED, f"{rest_reason} (EIU)"
            else:
                # Standard SHEX: if used, counts!
                if state.is_discharging:
                    # Discharging active during weekend: time used counts
                    # Check if crane breakdown during work
                    frac, status, mech_reason = self._evaluate_crane_breakdown(state)
                    reason = f"{rest_reason} (Worked)"
                    if mech_reason:
                        reason += f" - {mech_reason}"
                    return frac, status, reason
                else:
                    return Decimal("0.000000"), IntervalCountStatus.EXCLUDED, rest_reason

        # 3. Weather Interruptions (WWD / WWDSHEX)
        if self._is_wwd and state.has_weather_interruption:
            reasons = []
            if state.is_rain:
                reasons.append("Rain / Precipitation Halt")
            if state.is_wind_excessive:
                reasons.append("Excessive Wind Force Halt")
            return Decimal("0.000000"), IntervalCountStatus.EXCLUDED, f"WWD Exception: {', '.join(reasons)}"

        # 4. Strike / Labour Disputes
        if state.is_labour_dispute:
            if self._strike_half:
                # Half-rate strike clause
                return Decimal("0.500000"), IntervalCountStatus.PARTIALLY_COUNTED, "Strike Clause (Half Rate)"
            else:
                return Decimal("0.000000"), IntervalCountStatus.EXCLUDED, "Labour Dispute / Strike Exception"

        # 5. Mechanical / Crane Breakdown
        frac, status, mech_reason = self._evaluate_crane_breakdown(state)
        if mech_reason:
            return frac, status, mech_reason

        # 6. Port Power Failure
        if state.is_power_failure:
            return Decimal("0.000000"), IntervalCountStatus.EXCLUDED, "Port Power Failure Interruption"

        # 7. Normal Operation
        return Decimal("1.000000"), IntervalCountStatus.FULLY_COUNTED, None

    def _evaluate_crane_breakdown(
        self,
        state: OperationalState,
    ) -> tuple[Decimal, IntervalCountStatus, Optional[str]]:
        """Evaluate crane operational fraction."""
        if not state.is_crane_breakdown:
            return Decimal("1.000000"), IntervalCountStatus.FULLY_COUNTED, None

        cw = state.cranes_working
        ct = state.cranes_total

        # Total breakdown
        if cw is not None and cw == 0:
            return Decimal("0.000000"), IntervalCountStatus.EXCLUDED, "Crane Breakdown (All Cranes Idle)"

        # Partial breakdown with pro-rata
        if self._crane_pro_rata and cw is not None and ct is not None and ct > 0:
            if cw < ct:
                with decimal.localcontext(_ENGINE_CONTEXT):
                    frac = (Decimal(str(cw)) / Decimal(str(ct))).quantize(_6DP, rounding=ROUND_HALF_UP)
                return frac, IntervalCountStatus.PARTIALLY_COUNTED, f"Partial Crane Breakdown ({cw}/{ct} Working)"

        # If breakdown flagged but no crane counts given, treat as full breakdown
        if cw is None or cw == 0:
            return Decimal("0.000000"), IntervalCountStatus.EXCLUDED, "Crane Breakdown Interruption"

        return Decimal("1.000000"), IntervalCountStatus.FULLY_COUNTED, None

    # ------------------------------------------------------------------
    # Rest Period Detection
    # ------------------------------------------------------------------
    def _is_rest_period(self, start: datetime, end: datetime) -> tuple[bool, Optional[str]]:
        """
        Check if the interval falls within a contractual rest day or public holiday.
        Evaluates at interval midpoint to be robust for intervals within a period.
        """
        mid = start + (end - start) / 2
        d = mid.date()
        wd = mid.weekday()

        # Public Holiday
        if (self._excludes_sundays_holidays or self._excludes_fridays) and d in self._holidays:
            return True, f"Public Holiday ({d})"

        # SHEX / WWDSHEX
        if self._excludes_sundays_holidays:
            if self._sat_cut:
                # Saturday 17:00 -> Monday 08:00
                if wd == 5 and mid.hour >= 17:
                    return True, "Saturday Cut-off (SHEX)"
                if wd == 6:
                    return True, "Sunday (SHEX)"
                if wd == 0 and mid.hour < 8:
                    return True, "Monday Pre-08:00 (SHEX)"
            else:
                # Pure Sunday
                if wd == 6:
                    return True, "Sunday (SHEX)"

        # FHEX
        if self._excludes_fridays:
            if wd == 4:
                return True, "Friday (FHEX)"

        return False, None

    # ------------------------------------------------------------------
    # Calendar Boundary Splitting
    # ------------------------------------------------------------------
    def _split_slice_at_calendar_boundaries(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        """
        Split [start, end) at any calendar or contractual boundaries:
        - Saturday 17:00
        - Sunday 00:00
        - Monday 00:00
        - Monday 08:00
        - Friday 00:00 & 24:00 (for FHEX)
        - Public holiday 00:00 & 24:00
        """
        cut_points = {start, end}

        # Check each day in the span
        curr_day = start.date()
        end_day = end.date()

        while curr_day <= end_day + timedelta(days=1):
            day_midnight = datetime(curr_day.year, curr_day.month, curr_day.day, 0, 0, tzinfo=timezone.utc)

            # Midnights
            if start < day_midnight < end:
                cut_points.add(day_midnight)

            # SHEX Cut-offs
            if self._excludes_sundays_holidays and self._sat_cut:
                # Saturday 17:00
                if curr_day.weekday() == 5:
                    sat_17 = day_midnight.replace(hour=17)
                    if start < sat_17 < end:
                        cut_points.add(sat_17)
                # Monday 08:00
                if curr_day.weekday() == 0:
                    mon_08 = day_midnight.replace(hour=8)
                    if start < mon_08 < end:
                        cut_points.add(mon_08)

            curr_day += timedelta(days=1)

        sorted_cuts = sorted(cut_points)
        return [(sorted_cuts[i], sorted_cuts[i + 1]) for i in range(len(sorted_cuts) - 1)]
