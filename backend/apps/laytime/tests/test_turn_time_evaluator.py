"""
backend/apps/laytime/tests/test_turn_time_evaluator.py
============================================================
Cycle 1.2.2 — Unit Tests: TurnTimeEvaluator

Test categories:
  A. SHINC — continuous turn-time (no suspensions)
  B. SHEX  — Saturday 17:00–Monday 08:00 suspension (standard Baltic clause)
  C. SHEX  — pure Sunday exclusion (saturday_cutoff=False)
  D. WWDSHEX — identical suspension logic to SHEX for turn-time
  E. FHEX  — Friday exclusion (Gulf ports)
  F. WIPON — turn-time waived entirely
  G. Zero turn-time — commences at NOR
  H. Public holidays
  I. Compound: SHEX + holiday overlap
  J. Accounting invariant: counted + suspended == wall_clock_elapsed
  K. Input validation errors
  L. TurnTimeSuspension dataclass
"""

import pytest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from apps.laytime.engine.turn_time_evaluator import (
    TurnTimeEvaluator,
    TurnTimeResult,
    TurnTimeSuspension,
)
from apps.laytime.engine.constants import LaytimeTerms
from apps.laytime.engine.laycan_validator import OfficeHoursClause


# ── Helpers ────────────────────────────────────────────────────────────────
def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)

def make_eval(
    nor:      datetime,
    hours:    str = "6",
    terms:    LaytimeTerms = LaytimeTerms.SHINC,
    clause:   OfficeHoursClause = OfficeHoursClause.NONE,
    holidays: frozenset[date] = frozenset(),
    sat_cut:  bool = True,
) -> TurnTimeEvaluator:
    return TurnTimeEvaluator(
        effective_nor_at    = nor,
        turn_time_hours     = Decimal(hours),
        laytime_terms       = terms,
        office_hours_clause = clause,
        public_holidays     = holidays,
        saturday_cutoff     = sat_cut,
    )


# ===========================================================================
# A. SHINC — continuous turn-time
# ===========================================================================
class TestSHINCTurnTime:

    def test_6h_weekday_midday(self):
        """SHINC 6h from Thursday 09:00 → Thursday 15:00."""
        nor = utc(2025, 6, 5, 9, 0)   # Thursday
        r = make_eval(nor, "6", LaytimeTerms.SHINC).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 5, 15, 0)
        assert r.suspended_hours == Decimal("0.000000")
        assert r.suspensions == ()
        assert r.wipon_applied is False

    def test_6h_spans_midnight(self):
        """SHINC 6h from Thursday 22:00 → Friday 04:00 (no suspension)."""
        nor = utc(2025, 6, 5, 22, 0)
        r = make_eval(nor, "6", LaytimeTerms.SHINC).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 6, 4, 0)
        assert r.suspended_hours == Decimal("0.000000")

    def test_6h_over_saturday_sunday_shinc(self):
        """
        SHINC: turn-time runs through Saturday and Sunday without suspension.
        NOR Friday 20:00 + 6h = Saturday 02:00.
        """
        nor = utc(2025, 6, 6, 20, 0)   # Friday 20:00
        r = make_eval(nor, "6", LaytimeTerms.SHINC).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 7, 2, 0)  # Saturday 02:00
        assert r.suspended_hours == Decimal("0.000000")

    def test_12h_turn_time(self):
        """SHINC 12h from Wednesday 08:00 → Wednesday 20:00."""
        nor = utc(2025, 6, 4, 8, 0)
        r = make_eval(nor, "12", LaytimeTerms.SHINC).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 4, 20, 0)
        assert r.turn_time_counted_hours == Decimal("12.000000")

    def test_wall_clock_equals_counted_shinc(self):
        """SHINC: wall_clock == turn_time_counted (no suspension)."""
        nor = utc(2025, 6, 4, 10, 0)
        r = make_eval(nor, "6", LaytimeTerms.SHINC).evaluate()
        assert r.wall_clock_elapsed_hours == r.turn_time_counted_hours
        assert r.wall_clock_elapsed_hours == Decimal("6.000000")

    def test_fractional_turn_time_shinc(self):
        """SHINC 6.5h from 08:00 → 14:30."""
        nor = utc(2025, 6, 4, 8, 0)
        r = make_eval(nor, "6.5", LaytimeTerms.SHINC).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 4, 14, 30)


# ===========================================================================
# B. SHEX — Saturday 17:00–Monday 08:00 suspension
# ===========================================================================
class TestSHEXSaturdayCutoff:

    def test_nor_saturday_crosses_sat_cutoff(self):
        """
        SHEX, NOR Saturday 14:00, 6h turn-time.
        Counts 3h to Saturday 17:00, suspends, resumes Monday 08:00.
        Remaining 3h: Monday 08:00 → Monday 11:00.
        """
        # 2025-06-07 is Saturday
        nor = utc(2025, 6, 7, 14, 0)
        r = make_eval(nor, "6", LaytimeTerms.SHEX, sat_cut=True).evaluate()
        # Commences Monday 11:00
        assert r.laytime_commences_at == utc(2025, 6, 9, 11, 0)
        assert len(r.suspensions) == 1
        # Suspension: Sat 17:00 → Mon 08:00 = 39 hours
        assert r.suspensions[0].suspended_from == utc(2025, 6, 7, 17, 0)
        assert r.suspensions[0].resumed_at     == utc(2025, 6, 9, 8, 0)
        assert r.suspensions[0].duration_hours  == Decimal("39.000000")

    def test_nor_friday_after_cutoff_waits_to_monday(self):
        """
        SHEX, NOR Friday 18:00 (after 17:00 cutoff).
        Turn-time immediately in exclusion zone (Saturday cut-off starts Friday at 17:00? 
        No — Saturday cut-off starts Saturday 17:00. Friday 18:00 is still counting.)
        NOR Friday 18:00 + 6h = Saturday 00:00... but Sat 00:00–17:00 still counts.
        Friday 18:00 + 6h = Saturday 00:00 → all within counting window.
        """
        nor = utc(2025, 6, 6, 18, 0)  # Friday 18:00
        r = make_eval(nor, "6", LaytimeTerms.SHEX, sat_cut=True).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 7, 0, 0)  # Saturday 00:00
        assert r.suspended_hours == Decimal("0.000000")

    def test_nor_saturday_before_cutoff_runs_to_cutoff_then_suspends(self):
        """
        SHEX, NOR Saturday 15:00, 6h turn-time.
        Counts 2h to Saturday 17:00, suspends Sat 17:00–Mon 08:00 (39h).
        Remaining 4h: Monday 08:00 → Monday 12:00.
        """
        # 2025-06-07 is Saturday
        nor = utc(2025, 6, 7, 15, 0)
        r = make_eval(nor, "6", LaytimeTerms.SHEX, sat_cut=True).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 9, 12, 0)
        assert r.suspended_hours == Decimal("39.000000")

    def test_nor_saturday_after_cutoff_immediately_suspended(self):
        """
        SHEX, NOR Saturday 18:00 (already past 17:00 → in exclusion zone).
        Immediately suspended until Monday 08:00.
        All 6h run Monday 08:00 → Monday 14:00.
        """
        nor = utc(2025, 6, 7, 18, 0)  # Saturday 18:00
        r = make_eval(nor, "6", LaytimeTerms.SHEX, sat_cut=True).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 9, 14, 0)
        assert len(r.suspensions) >= 1

    def test_nor_sunday_all_suspended(self):
        """
        SHEX, NOR Sunday 10:00, 6h turn-time.
        Entire Sunday excluded; resumes Monday 08:00.
        All 6h Monday 08:00 → Monday 14:00.
        """
        nor = utc(2025, 6, 8, 10, 0)  # Sunday
        r = make_eval(nor, "6", LaytimeTerms.SHEX, sat_cut=True).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 9, 14, 0)

    def test_nor_monday_no_suspension_if_past_0800(self):
        """
        SHEX, NOR Monday 09:00 (past 08:00 resume) — no suspension.
        6h → Monday 15:00.
        """
        nor = utc(2025, 6, 9, 9, 0)  # Monday
        r = make_eval(nor, "6", LaytimeTerms.SHEX, sat_cut=True).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 9, 15, 0)
        assert r.suspended_hours == Decimal("0.000000")

    def test_nor_monday_before_0800_suspended_first(self):
        """
        SHEX, NOR Monday 06:00 (before 08:00 resume from weekend).
        Suspended until Monday 08:00 (2h), then 6h → Monday 14:00.
        """
        nor = utc(2025, 6, 9, 6, 0)  # Monday 06:00
        r = make_eval(nor, "6", LaytimeTerms.SHEX, sat_cut=True).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 9, 14, 0)
        assert r.suspended_hours == Decimal("2.000000")


# ===========================================================================
# C. SHEX — pure Sunday exclusion (saturday_cutoff=False)
# ===========================================================================
class TestSHEXPureSundayExclusion:

    def test_nor_saturday_noon_no_suspension(self):
        """Pure Sunday SHEX: Saturday counts fully. NOR Sat 12:00 + 6h = Sat 18:00."""
        nor = utc(2025, 6, 7, 12, 0)
        r = make_eval(nor, "6", LaytimeTerms.SHEX, sat_cut=False).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 7, 18, 0)
        assert r.suspended_hours == Decimal("0.000000")

    def test_nor_saturday_evening_crosses_sunday(self):
        """
        Pure Sunday SHEX, NOR Sat 20:00, 6h.
        Counts 4h to Sun 00:00, suspends Sun 00:00–Mon 00:00 (24h).
        Remaining 2h Mon 00:00 → Mon 02:00.
        """
        nor = utc(2025, 6, 7, 20, 0)  # Saturday 20:00
        r = make_eval(nor, "6", LaytimeTerms.SHEX, sat_cut=False).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 9, 2, 0)
        assert r.suspended_hours == Decimal("24.000000")

    def test_nor_sunday_entire_day_excluded(self):
        """Pure Sunday SHEX, NOR Sun 08:00 → suspended all day → Mon 00:00, then +6h."""
        nor = utc(2025, 6, 8, 8, 0)
        r = make_eval(nor, "6", LaytimeTerms.SHEX, sat_cut=False).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 9, 6, 0)


# ===========================================================================
# D. WWDSHEX — same suspension logic as SHEX for turn-time
# ===========================================================================
class TestWWDSHEXTurnTime:

    def test_wwdshex_behaves_same_as_shex_for_turntime(self):
        """WWDSHEX and SHEX produce identical turn-time windows."""
        nor = utc(2025, 6, 6, 14, 0)  # Friday
        r_shex    = make_eval(nor, "6", LaytimeTerms.SHEX,    sat_cut=True).evaluate()
        r_wwdshex = make_eval(nor, "6", LaytimeTerms.WWDSHEX, sat_cut=True).evaluate()
        assert r_shex.laytime_commences_at == r_wwdshex.laytime_commences_at
        assert r_shex.suspended_hours      == r_wwdshex.suspended_hours


# ===========================================================================
# E. FHEX — Friday exclusion (Gulf/Middle East ports)
# ===========================================================================
class TestFHEXTurnTime:

    def test_nor_thursday_crosses_friday(self):
        """
        FHEX, NOR Thursday 20:00, 6h.
        Counts 4h to Friday 00:00, suspends 24h, resumes Saturday 00:00.
        Remaining 2h Sat 00:00 → Sat 02:00.
        """
        # 2025-06-05 = Thursday
        nor = utc(2025, 6, 5, 20, 0)
        r = make_eval(nor, "6", LaytimeTerms.FHEX, sat_cut=False).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 7, 2, 0)
        assert r.suspended_hours == Decimal("24.000000")

    def test_nor_friday_entirely_excluded(self):
        """FHEX, NOR Friday 10:00, 6h → suspended to Saturday 00:00, then +6h."""
        nor = utc(2025, 6, 6, 10, 0)  # Friday
        r = make_eval(nor, "6", LaytimeTerms.FHEX, sat_cut=False).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 7, 6, 0)

    def test_nor_wednesday_no_suspension(self):
        """FHEX, NOR Wednesday 10:00, 6h — no Friday in window → no suspension."""
        nor = utc(2025, 6, 4, 10, 0)  # Wednesday
        r = make_eval(nor, "6", LaytimeTerms.FHEX, sat_cut=False).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 4, 16, 0)
        assert r.suspended_hours == Decimal("0.000000")


# ===========================================================================
# F. WIPON — turn-time waived
# ===========================================================================
class TestWIPON:

    def test_wipon_commences_at_nor(self):
        nor = utc(2025, 6, 5, 9, 0)
        r = make_eval(nor, "6", LaytimeTerms.SHINC, clause=OfficeHoursClause.WIPON).evaluate()
        assert r.laytime_commences_at == nor
        assert r.wipon_applied is True
        assert r.suspended_hours == Decimal("0.000000")
        assert r.wall_clock_elapsed_hours == Decimal("0.000000")

    def test_wipon_on_sunday_still_commences_immediately(self):
        nor = utc(2025, 6, 8, 10, 0)  # Sunday
        r = make_eval(nor, "6", LaytimeTerms.SHEX, clause=OfficeHoursClause.WIPON).evaluate()
        assert r.laytime_commences_at == nor
        assert r.wipon_applied is True

    def test_wipon_large_turn_time_still_waived(self):
        """Even 24h turn-time is waived under WIPON."""
        nor = utc(2025, 6, 5, 9, 0)
        r = make_eval(nor, "24", LaytimeTerms.SHINC, clause=OfficeHoursClause.WIPON).evaluate()
        assert r.laytime_commences_at == nor


# ===========================================================================
# G. Zero turn-time
# ===========================================================================
class TestZeroTurnTime:

    def test_zero_commences_at_nor(self):
        nor = utc(2025, 6, 5, 14, 0)
        r = make_eval(nor, "0", LaytimeTerms.SHEX).evaluate()
        assert r.laytime_commences_at == nor
        assert r.turn_time_counted_hours == Decimal("0.000000")
        assert r.wall_clock_elapsed_hours == Decimal("0.000000")

    def test_zero_no_suspensions(self):
        nor = utc(2025, 6, 8, 10, 0)  # Sunday under SHEX
        r = make_eval(nor, "0", LaytimeTerms.SHEX).evaluate()
        assert r.suspensions == ()


# ===========================================================================
# H. Public holidays
# ===========================================================================
class TestPublicHolidays:

    def test_holiday_during_turn_time_shinc(self):
        """
        Under SHINC, holidays do NOT suspend turn-time.
        (Terms include SHINC which is not in SUNDAY_HOLIDAY_EXCLUDING_TERMS.)
        Wait — for SHINC, we don't exclude Sundays, but what about holidays?
        Per maritime law, for SHINC holidays are included.
        """
        holiday = date(2025, 6, 5)
        nor     = utc(2025, 6, 5, 9, 0)
        r = make_eval(nor, "6", LaytimeTerms.SHINC, holidays=frozenset({holiday})).evaluate()
        # SHINC: no holiday exclusion
        assert r.laytime_commences_at == utc(2025, 6, 5, 15, 0)
        assert r.suspended_hours == Decimal("0.000000")

    def test_holiday_during_turn_time_shex(self):
        """
        Under SHEX, a public holiday suspends the turn-time for its full 24h.
        NOR Wednesday 10:00, holiday on Thursday, 6h turn-time.
        Counts 14h to Thursday 00:00, suspends 24h (holiday), resumes Friday 00:00.
        Remaining: 6-14h < 0? No — 6h < 14h available before midnight Wed.
        Let's use 20h turn-time instead.
        NOR Wed 10:00, 20h:
          Counts 14h to Thu 00:00 (holiday), suspend 24h.
          Remaining 6h: Fri 00:00 → Fri 06:00.
        """
        holiday = date(2025, 6, 5)    # Thursday
        nor     = utc(2025, 6, 4, 10, 0)  # Wednesday 10:00
        r = make_eval(nor, "20", LaytimeTerms.SHEX,
                      holidays=frozenset({holiday}), sat_cut=False).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 6, 6, 0)
        assert r.suspended_hours == Decimal("24.000000")

    def test_multiple_holidays(self):
        """Two consecutive holidays under SHEX suspend 48h."""
        holidays = frozenset({date(2025, 6, 4), date(2025, 6, 5)})
        nor      = utc(2025, 6, 3, 20, 0)  # Tuesday 20:00
        # Counts 4h to Wed 00:00 (holiday), suspends Wed 00:00–Thu 00:00 (24h)
        # Thu 00:00 also holiday, suspends Thu 00:00–Fri 00:00 (24h)
        # Remaining: 20-4=16h, Fri 00:00 → Fri 16:00
        r = make_eval(nor, "20", LaytimeTerms.SHEX,
                      holidays=holidays, sat_cut=False).evaluate()
        assert r.laytime_commences_at == utc(2025, 6, 6, 16, 0)
        assert r.suspended_hours == Decimal("48.000000")


# ===========================================================================
# I. Compound: SHEX + holiday overlap
# ===========================================================================
class TestCompoundExclusions:

    def test_holiday_on_sunday_no_double_count(self):
        """
        Holiday falling on Sunday (already in SHEX cut-off window)
        should not extend the suspension beyond Monday 08:00.
        """
        # Sunday 2025-06-08 declared holiday
        holiday = date(2025, 6, 8)
        nor     = utc(2025, 6, 7, 16, 0)  # Saturday 16:00 (before 17:00 cut)
        r = make_eval(nor, "6", LaytimeTerms.SHEX,
                      holidays=frozenset({holiday}), sat_cut=True).evaluate()
        # Saturday 16:00: approaching Sat cut-off.
        # Counts 1h to 17:00 (cut-off), then suspended until Mon 08:00.
        # Mon 08:00 + 5h = Mon 13:00.
        assert r.laytime_commences_at == utc(2025, 6, 9, 13, 0)


# ===========================================================================
# J. Accounting invariant
# ===========================================================================
class TestAccountingInvariant:

    @pytest.mark.parametrize("nor_str,hours,terms,sat_cut", [
        ("2025-06-05T09:00:00",  "6",  "SHINC",   True),
        ("2025-06-06T14:00:00",  "6",  "SHEX",    True),   # Friday
        ("2025-06-07T15:00:00",  "6",  "SHEX",    True),   # Saturday
        ("2025-06-08T10:00:00",  "12", "SHEX",    True),   # Sunday
        ("2025-06-06T20:00:00",  "6",  "SHEX",    False),  # Saturday pure
        ("2025-06-05T20:00:00",  "6",  "FHEX",    False),  # Thursday
        ("2025-06-06T10:00:00",  "6",  "FHEX",    False),  # Friday
        ("2025-06-09T06:00:00",  "6",  "SHEX",    True),   # Monday before 08:00
        ("2025-06-04T10:00:00",  "6",  "WWDSHEX", True),
    ])
    def test_accounting_invariant(self, nor_str, hours, terms, sat_cut):
        """
        counted + suspended == wall_clock_elapsed (within 0.001h).
        """
        nor = datetime.fromisoformat(nor_str).replace(tzinfo=timezone.utc)
        ev  = TurnTimeEvaluator(
            effective_nor_at = nor,
            turn_time_hours  = Decimal(hours),
            laytime_terms    = LaytimeTerms(terms),
            saturday_cutoff  = sat_cut,
        )
        result  = ev.evaluate()
        ok, msg = ev.verify_accounting_invariant(result)
        assert ok, f"Accounting invariant failed: {msg}"


# ===========================================================================
# K. Input validation
# ===========================================================================
class TestInputValidation:

    def test_naive_datetime_raises(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            TurnTimeEvaluator(
                effective_nor_at = datetime(2025, 6, 5, 9, 0),  # naive
                turn_time_hours  = Decimal("6"),
            )

    def test_negative_turn_time_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            TurnTimeEvaluator(
                effective_nor_at = utc(2025, 6, 5, 9, 0),
                turn_time_hours  = Decimal("-1"),
            )

    def test_invalid_laytime_terms_raises(self):
        with pytest.raises(ValueError):
            TurnTimeEvaluator(
                effective_nor_at = utc(2025, 6, 5, 9, 0),
                turn_time_hours  = Decimal("6"),
                laytime_terms    = "BOGUS_TERM",  # type: ignore
            )

    def test_result_is_frozen(self):
        nor = utc(2025, 6, 5, 9, 0)
        r   = make_eval(nor, "6", LaytimeTerms.SHINC).evaluate()
        with pytest.raises((AttributeError, TypeError)):
            r.laytime_commences_at = utc(2025, 6, 5, 20, 0)  # type: ignore


# ===========================================================================
# L. TurnTimeSuspension dataclass
# ===========================================================================
class TestTurnTimeSuspension:

    def test_duration_hours_computed(self):
        s = TurnTimeSuspension(
            suspended_from = utc(2025, 6, 7, 17, 0),
            resumed_at     = utc(2025, 6, 9, 8, 0),
            reason         = "Saturday cut-off",
        )
        assert s.duration_hours == Decimal("39.000000")

    def test_str_representation(self):
        s = TurnTimeSuspension(
            suspended_from = utc(2025, 6, 7, 17, 0),
            resumed_at     = utc(2025, 6, 9, 8, 0),
            reason         = "test reason",
        )
        assert "39.000000" in str(s)
        assert "test reason" in str(s)

    def test_suspension_is_frozen(self):
        s = TurnTimeSuspension(
            suspended_from = utc(2025, 6, 7, 17, 0),
            resumed_at     = utc(2025, 6, 9, 8, 0),
            reason         = "test",
        )
        with pytest.raises((AttributeError, TypeError)):
            s.reason = "mutate"  # type: ignore
