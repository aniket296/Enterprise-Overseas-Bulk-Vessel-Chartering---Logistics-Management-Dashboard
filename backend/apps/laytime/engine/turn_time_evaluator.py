"""
backend/apps/laytime/engine/turn_time_evaluator.py
============================================================
Cycle 1.2.2 — NOR Turn-Time Window Evaluator

Computes the exact laytime commencement timestamp by advancing a
turn-time clock of ``turn_time_hours`` forward from the effective
NOR, correctly suspending the clock over excluded periods.

Contractual Rules Implemented
------------------------------
SHINC / WWD / FHINC
    Turn-time runs continuously (wall-clock). No suspensions.

SHEX / SHEX_EIU / WWDSHEX / FHEX
    Turn-time clock SUSPENDS during excluded periods:
      - Every Sunday (weekday 6), full 24-h period 00:00–24:00
      - Public/gazetted holidays supplied by the caller
      - (FHEX): Fridays instead of Sundays

WIPON clause
    Turn-time is WAIVED entirely: laytime commences at effective NOR.

WIBON clause
    Turn-time runs from NOR tender even at anchorage; no suspension
    adjustment beyond standard term rules.

Saturday cut-off (SHEX, Baltic Exchange standard)
    For SHEX contracts, the common port clause is:
    "time does not count from 1700 Saturday to 0800 Monday".
    This is modelled as:
      - Saturday 17:00 → Monday 08:00 is a non-counting window
    This replaces the simpler "Sunday-only" exclusion and is the
    default when ``saturday_cutoff`` is True (default).

Algorithm (Forward-Simulation)
-------------------------------
Starting from ``effective_nor_at``, advance a virtual clock:
  1. If the current moment falls inside a non-counting window:
       Jump ahead to the window's end (no turn-time consumed).
  2. Otherwise, calculate the turning-time available until the
       next non-counting boundary.
  3. Consume min(remaining_turn_time, available) hours.
  4. Repeat until remaining turn-time == 0.

The endpoint is ``laytime_commences_at``.

All datetimes are UTC timezone-aware.
All arithmetic uses Python ``decimal.Decimal``.
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from .constants import (
    LaytimeTerms,
    SUNDAY_HOLIDAY_EXCLUDING_TERMS,
    FRIDAY_EXCLUDING_TERMS,
    HOURS_PER_DAY,
)
from .laycan_validator import OfficeHoursClause

# ---------------------------------------------------------------------------
# Decimal context (shared with allowed_laytime, same precision)
# ---------------------------------------------------------------------------
_ENGINE_CONTEXT = decimal.Context(
    prec=28,
    rounding=ROUND_HALF_UP,
    traps=[decimal.DivisionByZero, decimal.InvalidOperation, decimal.Overflow],
)
_6DP = Decimal("0.000001")
_SECONDS_PER_HOUR = Decimal("3600")


def _D(v) -> Decimal:
    return Decimal(str(v)) if not isinstance(v, Decimal) else v


def _hours_to_td(hours: Decimal) -> timedelta:
    """Convert Decimal hours to timedelta (microsecond precision)."""
    total_us = int((hours * _SECONDS_PER_HOUR * Decimal("1000000")).to_integral_value(ROUND_HALF_UP))
    return timedelta(microseconds=total_us)


def _td_to_hours(td: timedelta) -> Decimal:
    """Convert timedelta to Decimal hours (6dp)."""
    with decimal.localcontext(_ENGINE_CONTEXT):
        hours = Decimal(str(td.total_seconds())) / _SECONDS_PER_HOUR
        return hours.quantize(_6DP, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Non-counting window helpers
# ---------------------------------------------------------------------------

class _NoncountingWindows:
    """
    Determines non-counting periods for a given set of laytime terms.

    Under SHEX / WWDSHEX (with Saturday cut-off enabled):
      Non-counting: Saturday 17:00 – Monday 08:00 (UTC)

    Under SHEX / WWDSHEX (saturday_cutoff disabled, pure Sunday):
      Non-counting: Sunday 00:00 – Monday 00:00 (UTC)

    Under FHEX:
      Non-counting: Friday 00:00 – Saturday 00:00 (UTC)

    Public holidays: full 24-h non-counting period starting at 00:00.
    """

    def __init__(
        self,
        terms:           LaytimeTerms,
        public_holidays: frozenset[date],
        saturday_cutoff: bool = True,
    ) -> None:
        self._terms    = terms
        self._holidays = public_holidays
        self._sat_cut  = saturday_cutoff

        # Determine which weekdays are excluded rest days
        if terms in SUNDAY_HOLIDAY_EXCLUDING_TERMS:
            self._rest_weekday: Optional[int] = 6   # Sunday
            self._use_sat_cut  = saturday_cutoff
            self._excludes_holidays = True
        elif terms in FRIDAY_EXCLUDING_TERMS:
            self._rest_weekday = 4  # Friday
            self._use_sat_cut  = False
            self._excludes_holidays = True
        else:
            self._rest_weekday = None
            self._use_sat_cut  = False
            self._excludes_holidays = False

    def is_excluded(self, dt: datetime) -> bool:
        """
        Return True if ``dt`` falls within a non-counting window.
        Requires UTC timezone-aware dt.
        """
        if not self._excludes_holidays and self._rest_weekday is None:
            return False

        if self._rest_weekday is not None:
            wd = dt.weekday()

            if self._use_sat_cut:
                # Non-counting: Saturday 17:00 → Monday 08:00
                if wd == 5 and dt.hour >= 17:       # Saturday ≥ 17:00
                    return True
                if wd == 6:                          # Sunday (all day)
                    return True
                if wd == 0 and dt.hour < 8:         # Monday < 08:00
                    return True
            else:
                # Pure rest-day exclusion (all day)
                if wd == self._rest_weekday:
                    return True

        return self._is_holiday(dt)

    def _is_holiday(self, dt: datetime) -> bool:
        if not self._excludes_holidays:
            return False
        return dt.date() in self._holidays

    def next_counting_start(self, dt: datetime) -> datetime:
        """
        Given that ``dt`` is inside a non-counting window,
        return the earliest datetime that is not excluded.
        Steps forward by 1-minute increments (max 4320 steps for a
        long weekend — fast enough for CI, replaced by direct
        calculation below for production paths).
        """
        # Direct calculation is faster and avoids step loops:
        wd = dt.weekday()

        if self._use_sat_cut:
            # Inside Saturday 17:00+ or all Sunday or Monday before 08:00
            if wd == 5:   # Saturday ≥ 17:00
                days_to_monday = 2
                return dt.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=days_to_monday)
            elif wd == 6:  # Sunday
                return dt.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
            elif wd == 0 and dt.hour < 8:  # Monday before 08:00
                return dt.replace(hour=8, minute=0, second=0, microsecond=0)
            # Must be a holiday
            return self._next_non_holiday_midnight(dt)

        elif self._rest_weekday is not None:
            if wd == self._rest_weekday:
                return (dt.replace(hour=0, minute=0, second=0, microsecond=0)
                        + timedelta(days=1))
            return self._next_non_holiday_midnight(dt)

        return self._next_non_holiday_midnight(dt)

    def _next_non_holiday_midnight(self, dt: datetime) -> datetime:
        """Advance to midnight of the next non-holiday day."""
        candidate = dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        while candidate.date() in self._holidays:
            candidate += timedelta(days=1)
        return candidate

    def next_excluded_start(self, dt: datetime) -> Optional[datetime]:
        """
        Given that ``dt`` is in a counting window, return the next
        datetime at which a non-counting window begins, searching up
        to 8 calendar days ahead.  Returns None if no exclusion applies
        (SHINC / pure counting terms).
        """
        has_holiday_exclusion = self._excludes_holidays and bool(self._holidays)
        if self._rest_weekday is None and not has_holiday_exclusion:
            return None

        candidate = dt + timedelta(minutes=1)
        max_search = dt + timedelta(days=8)

        while candidate <= max_search:
            if self.is_excluded(candidate):
                # Step back to find the exact start second
                # The excluded window starts at a clean boundary;
                # back-calculate:
                if self._use_sat_cut and candidate.weekday() == 5 and candidate.hour >= 17:
                    exc_start = candidate.replace(hour=17, minute=0, second=0, microsecond=0)
                    if exc_start > dt:
                        return exc_start
                elif self._use_sat_cut and candidate.weekday() == 6:
                    exc_start = candidate.replace(hour=0, minute=0, second=0, microsecond=0)
                    # Saturday cut-off started at 17:00 yesterday
                    exc_start = (candidate - timedelta(days=1)).replace(
                        hour=17, minute=0, second=0, microsecond=0
                    )
                    if exc_start <= dt:
                        exc_start = candidate.replace(hour=0, minute=0, second=0, microsecond=0)
                    if exc_start > dt:
                        return exc_start
                elif self._use_sat_cut and candidate.weekday() == 0 and candidate.hour < 8:
                    # Already in Monday pre-08:00 — started Sunday 00:00
                    exc_start = (candidate - timedelta(days=1)).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )
                    if exc_start <= dt:
                        # We are already past the start; the NEXT exclusion is
                        # next Saturday 17:00
                        candidate = candidate.replace(hour=8, minute=0, second=0, microsecond=0)
                        continue
                    return exc_start
                elif candidate.date() in self._holidays:
                    exc_start = candidate.replace(hour=0, minute=0, second=0, microsecond=0)
                    if exc_start > dt:
                        return exc_start
                else:
                    # Pure rest-day (FHEX / pure SHEX)
                    exc_start = candidate.replace(hour=0, minute=0, second=0, microsecond=0)
                    if exc_start > dt:
                        return exc_start
                    # Already past: next rest day
                    candidate = candidate.replace(
                        hour=0, minute=0, second=0, microsecond=0
                    ) + timedelta(days=1)
                    continue

            candidate += timedelta(hours=1)

        return None   # No exclusion window found within search range


# ---------------------------------------------------------------------------
# Suspension record (for audit trail)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TurnTimeSuspension:
    """Represents one period during which the turn-time clock was suspended."""
    suspended_from: datetime
    resumed_at:     datetime
    reason:         str

    @property
    def duration_hours(self) -> Decimal:
        return _td_to_hours(self.resumed_at - self.suspended_from)

    def __str__(self) -> str:
        return (
            f"Suspended [{self.suspended_from.isoformat()} → "
            f"{self.resumed_at.isoformat()}] "
            f"({self.duration_hours} h): {self.reason}"
        )


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TurnTimeResult:
    """
    Immutable result of the turn-time window evaluation.

    ``laytime_commences_at`` is the authoritative timestamp from which
    the Cycles 1.2.3–1.2.5 interval engines begin their counting.
    """
    # --- Inputs echoed ---
    effective_nor_at:       datetime
    turn_time_hours:        Decimal
    laytime_terms:          LaytimeTerms
    office_hours_clause:    OfficeHoursClause
    public_holidays:        frozenset[date]
    saturday_cutoff:        bool

    # --- Core result ---
    laytime_commences_at:   datetime
    """The exact datetime from which laytime counting begins."""

    # --- Accounting ---
    wall_clock_elapsed_hours: Decimal
    """Total wall-clock time from effective_nor to commencement."""

    turn_time_counted_hours:  Decimal
    """Hours that counted toward the turn-time (= turn_time_hours for fixed contracts)."""

    suspended_hours:          Decimal
    """Total hours the turn-time clock was suspended (SHEX/holiday exclusions)."""

    suspensions:              tuple[TurnTimeSuspension, ...]
    """Ordered list of suspension windows with reasons."""

    # --- Flags ---
    wipon_applied:            bool
    """True if WIPON clause waived the turn-time entirely."""

    # --- Audit ---
    notes:                    tuple[str, ...]

    def __str__(self) -> str:
        return (
            f"TurnTimeResult[{self.laytime_terms.value}]: "
            f"NOR={self.effective_nor_at.isoformat()}, "
            f"commences={self.laytime_commences_at.isoformat()}, "
            f"suspended={self.suspended_hours}h"
        )


# ---------------------------------------------------------------------------
# Main evaluator
# ---------------------------------------------------------------------------
class TurnTimeEvaluator:
    """
    Evaluates the turn-time window from effective NOR to laytime commencement.

    Parameters
    ----------
    effective_nor_at : datetime
        The effective NOR timestamp (UTC, timezone-aware).
        This is the output of ``LaycanValidator.validate().effective_nor_at``.
    turn_time_hours : Decimal | str
        Contractual turn-time buffer in hours (e.g., ``Decimal("6")``).
    laytime_terms : LaytimeTerms
        Governs which periods the turn-time clock is suspended over.
    office_hours_clause : OfficeHoursClause
        WIPON waives turn-time; others are informational.
    public_holidays : frozenset[date] | None
        Port-specific gazetted holidays. Empty frozenset if none.
    saturday_cutoff : bool
        If True (default), SHEX exclusion window is Sat 17:00–Mon 08:00.
        If False, only Sundays (and holidays) are excluded (simpler clause).
    """

    def __init__(
        self,
        effective_nor_at:    datetime,
        turn_time_hours:     Decimal | str,
        laytime_terms:       LaytimeTerms = LaytimeTerms.SHINC,
        office_hours_clause: OfficeHoursClause = OfficeHoursClause.NONE,
        public_holidays:     Optional[frozenset[date]] = None,
        saturday_cutoff:     bool = True,
    ) -> None:
        if effective_nor_at.tzinfo is None:
            raise ValueError("effective_nor_at must be UTC timezone-aware.")

        self._nor         = effective_nor_at
        self._tt_hours    = _D(turn_time_hours)
        self._terms       = LaytimeTerms(laytime_terms)
        self._clause      = OfficeHoursClause(office_hours_clause)
        self._holidays    = public_holidays or frozenset()
        self._sat_cut     = saturday_cutoff
        self._windows     = _NoncountingWindows(self._terms, self._holidays, saturday_cutoff)

        if self._tt_hours < Decimal("0"):
            raise ValueError(f"turn_time_hours must be non-negative; got {self._tt_hours}")

    def evaluate(self) -> TurnTimeResult:
        """
        Run the forward-simulation to compute laytime commencement.

        Returns
        -------
        TurnTimeResult
            Fully auditable result with laytime commencement timestamp,
            suspension breakdown, and all flag fields.
        """
        notes: list[str] = []
        suspensions: list[TurnTimeSuspension] = []

        # ── WIPON: waive turn-time entirely ──────────────────────────────
        if self._clause == OfficeHoursClause.WIPON:
            notes.append("WIPON clause: turn-time waived; laytime commences at effective NOR.")
            return TurnTimeResult(
                effective_nor_at          = self._nor,
                turn_time_hours           = self._tt_hours,
                laytime_terms             = self._terms,
                office_hours_clause       = self._clause,
                public_holidays           = frozenset(self._holidays),
                saturday_cutoff           = self._sat_cut,
                laytime_commences_at      = self._nor,
                wall_clock_elapsed_hours  = Decimal("0.000000"),
                turn_time_counted_hours   = Decimal("0.000000"),
                suspended_hours           = Decimal("0.000000"),
                suspensions               = (),
                wipon_applied             = True,
                notes                     = tuple(notes),
            )

        # ── Zero turn-time ───────────────────────────────────────────────
        if self._tt_hours == Decimal("0"):
            notes.append("Turn-time is zero; laytime commences at effective NOR.")
            return TurnTimeResult(
                effective_nor_at          = self._nor,
                turn_time_hours           = self._tt_hours,
                laytime_terms             = self._terms,
                office_hours_clause       = self._clause,
                public_holidays           = frozenset(self._holidays),
                saturday_cutoff           = self._sat_cut,
                laytime_commences_at      = self._nor,
                wall_clock_elapsed_hours  = Decimal("0.000000"),
                turn_time_counted_hours   = Decimal("0.000000"),
                suspended_hours           = Decimal("0.000000"),
                suspensions               = (),
                wipon_applied             = False,
                notes                     = tuple(notes),
            )

        # ── Forward simulation ───────────────────────────────────────────
        current_dt       = self._nor
        remaining_hours  = self._tt_hours   # hours still to consume
        total_suspended  = Decimal("0")

        MAX_ITERATIONS = 500   # safety limit (~500 non-counting windows)
        iteration      = 0

        while remaining_hours > Decimal("0"):
            iteration += 1
            if iteration > MAX_ITERATIONS:
                raise RuntimeError(
                    f"TurnTimeEvaluator exceeded {MAX_ITERATIONS} iterations. "
                    f"Possible infinite loop — check public_holidays or term config."
                )

            # ── Is current_dt inside a non-counting window? ──────────────
            if self._windows.is_excluded(current_dt):
                resume_at = self._windows.next_counting_start(current_dt)
                suspension = TurnTimeSuspension(
                    suspended_from = current_dt,
                    resumed_at     = resume_at,
                    reason         = self._exclusion_reason(current_dt),
                )
                suspensions.append(suspension)
                total_suspended += suspension.duration_hours
                current_dt = resume_at
                notes.append(
                    f"Turn-time suspended: {suspension.suspended_from.isoformat()} → "
                    f"{resume_at.isoformat()} ({suspension.duration_hours} h) "
                    f"— {suspension.reason}"
                )
                continue

            # ── Find next non-counting boundary ─────────────────────────
            next_excl = self._windows.next_excluded_start(current_dt)

            if next_excl is None:
                # No more exclusions — consume all remaining turn-time
                advance = _hours_to_td(remaining_hours)
                current_dt      += advance
                remaining_hours  = Decimal("0")
            else:
                # Time available until next exclusion
                available_td    = next_excl - current_dt
                available_hours = _td_to_hours(available_td)

                if available_hours >= remaining_hours:
                    # All remaining turn-time fits before next exclusion
                    advance = _hours_to_td(remaining_hours)
                    current_dt      += advance
                    remaining_hours  = Decimal("0")
                else:
                    # Consume up to the exclusion boundary
                    current_dt      = next_excl
                    remaining_hours -= available_hours

        laytime_commences_at   = current_dt
        wall_clock_elapsed     = _td_to_hours(laytime_commences_at - self._nor)
        turn_time_counted      = self._tt_hours  # By construction: consumed exactly this much

        if suspensions:
            notes.append(
                f"Total turn-time suspended: {total_suspended.quantize(_6DP)} h "
                f"across {len(suspensions)} window(s)."
            )

        notes.append(
            f"Laytime commences: {laytime_commences_at.isoformat()} "
            f"(wall-clock elapsed: {wall_clock_elapsed.quantize(_6DP)} h, "
            f"turn-time counted: {turn_time_counted} h, "
            f"suspended: {total_suspended.quantize(_6DP)} h)"
        )

        return TurnTimeResult(
            effective_nor_at          = self._nor,
            turn_time_hours           = self._tt_hours,
            laytime_terms             = self._terms,
            office_hours_clause       = self._clause,
            public_holidays           = frozenset(self._holidays),
            saturday_cutoff           = self._sat_cut,
            laytime_commences_at      = laytime_commences_at,
            wall_clock_elapsed_hours  = wall_clock_elapsed.quantize(_6DP),
            turn_time_counted_hours   = turn_time_counted.quantize(_6DP),
            suspended_hours           = total_suspended.quantize(_6DP),
            suspensions               = tuple(suspensions),
            wipon_applied             = False,
            notes                     = tuple(notes),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _exclusion_reason(self, dt: datetime) -> str:
        if dt.date() in self._holidays:
            return f"Public holiday ({dt.date()})"
        wd  = dt.weekday()
        names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
        if self._sat_cut and wd == 5 and dt.hour >= 17:
            return "Saturday cut-off (17:00 Sat → 08:00 Mon, SHEX)"
        if self._sat_cut and wd == 6:
            return "Sunday exclusion (SHEX)"
        if self._sat_cut and wd == 0 and dt.hour < 8:
            return "Saturday cut-off — Monday pre-08:00 (SHEX)"
        if wd == 4 and self._terms in FRIDAY_EXCLUDING_TERMS:
            return "Friday exclusion (FHEX)"
        return f"{names[wd]} exclusion ({self._terms.value})"

    # ------------------------------------------------------------------
    # Accounting invariant (for external verification)
    # ------------------------------------------------------------------
    def verify_accounting_invariant(self, result: TurnTimeResult) -> tuple[bool, str]:
        """
        Verify: turn_time_counted + suspended = wall_clock_elapsed (± 0.001 h).
        Returns (is_consistent, description).
        """
        computed = result.turn_time_counted_hours + result.suspended_hours
        diff = abs(computed - result.wall_clock_elapsed_hours)
        ok   = diff <= Decimal("0.001")
        return (ok, f"counted({result.turn_time_counted_hours}) + "
                    f"suspended({result.suspended_hours}) "
                    f"= {computed} vs wall({result.wall_clock_elapsed_hours}), "
                    f"diff={diff}")
