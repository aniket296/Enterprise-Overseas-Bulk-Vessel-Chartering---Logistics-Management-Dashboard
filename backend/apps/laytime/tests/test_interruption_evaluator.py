"""
backend/apps/laytime/tests/test_interruption_evaluator.py
============================================================
Cycle 1.2.4 — Unit Tests: InterruptionRulesEvaluator

Test categories:
  A. SHINC terms (working days, weekends, and weather behavior)
  B. WWD terms (weather working day halts under rain and excessive wind)
  C. SHEX terms (Saturday 17:00 cutoff, Sunday exclusion, EIU vs non-EIU)
  D. Calendar boundary automatic partitioning
  E. Mechanical & crane pro-rata evaluation (partial vs full breakdown)
  F. FHEX terms (Friday exclusion)
  G. Public holidays under SHEX vs SHINC
  H. Pre-commencement and post-completion exclusions
  I. Strike clause (standard exclusion vs half-rate)
  J. Mathematical accounting invariant (counted + excepted == total)
"""

from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
import pytest

from apps.laytime.engine.interruption_evaluator import (
    InterruptionRulesEvaluator,
    EvaluatedSlice,
    InterruptionEvaluationResult,
)
from apps.laytime.engine.time_slice import (
    TimeSlice,
    OperationalState,
    IntervalCountStatus,
)
from apps.laytime.engine.constants import LaytimeTerms


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def make_slice(
    start: datetime,
    end: datetime,
    state: OperationalState = OperationalState(),
    is_pre: bool = False,
    is_post: bool = False,
) -> TimeSlice:
    sec = int((end - start).total_seconds())
    hours = (Decimal(str(sec)) / Decimal("3600")).quantize(Decimal("0.000001"))
    return TimeSlice(
        index=0,
        start_time=start,
        end_time=end,
        duration_seconds=sec,
        duration_hours=hours,
        state=state,
        events_at_start=(),
        is_pre_commencement=is_pre,
        is_post_completion=is_post,
    )


# ===========================================================================
# A. SHINC Terms
# ===========================================================================
class TestSHINCTerms:

    def test_shinc_weekday_counts_fully(self):
        """SHINC normal weekday discharge counts 100%."""
        s = make_slice(utc(2025, 6, 4, 10, 0), utc(2025, 6, 4, 16, 0), OperationalState(is_discharging=True))
        ev = InterruptionRulesEvaluator(LaytimeTerms.SHINC)
        res = ev.evaluate([s])

        assert len(res.slices) == 1
        es = res.slices[0]
        assert es.count_fraction == Decimal("1.000000")
        assert es.count_status == IntervalCountStatus.FULLY_COUNTED
        assert es.counted_hours == Decimal("6.000000")
        assert es.excepted_hours == Decimal("0.000000")

    def test_shinc_sunday_counts_fully(self):
        """SHINC Sunday counts 100% even if resting."""
        s = make_slice(utc(2025, 6, 8, 10, 0), utc(2025, 6, 8, 16, 0), OperationalState(is_discharging=False))
        ev = InterruptionRulesEvaluator(LaytimeTerms.SHINC)
        res = ev.evaluate([s])

        assert len(res.slices) == 1
        assert res.slices[0].count_fraction == Decimal("1.000000")
        assert res.slices[0].count_status == IntervalCountStatus.FULLY_COUNTED

    def test_shinc_rain_halt_still_counts(self):
        """Under pure SHINC (not WWD), weather is charterer's risk and counts."""
        s = make_slice(utc(2025, 6, 4, 10, 0), utc(2025, 6, 4, 14, 0), OperationalState(is_rain=True))
        ev = InterruptionRulesEvaluator(LaytimeTerms.SHINC)
        res = ev.evaluate([s])

        assert res.slices[0].count_fraction == Decimal("1.000000")
        assert res.slices[0].count_status == IntervalCountStatus.FULLY_COUNTED


# ===========================================================================
# B. WWD Terms (Weather Working Days)
# ===========================================================================
class TestWWDTerms:

    def test_wwd_clear_weather_counts(self):
        s = make_slice(utc(2025, 6, 4, 8, 0), utc(2025, 6, 4, 12, 0), OperationalState(is_discharging=True))
        ev = InterruptionRulesEvaluator(LaytimeTerms.WWD)
        res = ev.evaluate([s])

        assert res.slices[0].count_fraction == Decimal("1.000000")
        assert res.slices[0].count_status == IntervalCountStatus.FULLY_COUNTED

    def test_wwd_rain_halt_is_excluded(self):
        """Under WWD, rain halt is 100% excluded."""
        s = make_slice(utc(2025, 6, 4, 12, 0), utc(2025, 6, 4, 16, 0), OperationalState(is_rain=True))
        ev = InterruptionRulesEvaluator(LaytimeTerms.WWD)
        res = ev.evaluate([s])

        assert res.slices[0].count_fraction == Decimal("0.000000")
        assert res.slices[0].count_status == IntervalCountStatus.EXCLUDED
        assert res.slices[0].counted_hours == Decimal("0.000000")
        assert res.slices[0].excepted_hours == Decimal("4.000000")
        assert "Rain" in res.slices[0].deduction_reason

    def test_wwd_wind_excess_is_excluded(self):
        s = make_slice(utc(2025, 6, 4, 14, 0), utc(2025, 6, 4, 16, 0), OperationalState(is_wind_excessive=True))
        ev = InterruptionRulesEvaluator(LaytimeTerms.WWD)
        res = ev.evaluate([s])

        assert res.slices[0].count_fraction == Decimal("0.000000")
        assert res.slices[0].count_status == IntervalCountStatus.EXCLUDED
        assert "Wind" in res.slices[0].deduction_reason


# ===========================================================================
# C. SHEX Terms & EIU vs Non-EIU
# ===========================================================================
class TestSHEXTerms:

    def test_shex_sunday_idle_is_excluded(self):
        """Sunday idle under SHEX is excluded."""
        s = make_slice(utc(2025, 6, 8, 10, 0), utc(2025, 6, 8, 16, 0), OperationalState(is_discharging=False))
        ev = InterruptionRulesEvaluator(LaytimeTerms.SHEX)
        res = ev.evaluate([s])

        assert res.slices[0].count_fraction == Decimal("0.000000")
        assert res.slices[0].count_status == IntervalCountStatus.EXCLUDED

    def test_shex_sunday_worked_counts(self):
        """Under standard SHEX (non-EIU), time used on Sunday counts."""
        s = make_slice(utc(2025, 6, 8, 10, 0), utc(2025, 6, 8, 16, 0), OperationalState(is_discharging=True))
        ev = InterruptionRulesEvaluator(LaytimeTerms.SHEX)
        res = ev.evaluate([s])

        assert res.slices[0].count_fraction == Decimal("1.000000")
        assert res.slices[0].count_status == IntervalCountStatus.FULLY_COUNTED
        assert "Worked" in res.slices[0].deduction_reason

    def test_shex_eiu_sunday_worked_still_excluded(self):
        """Under SHEX_EIU, Sunday is excluded Even If Used."""
        s = make_slice(utc(2025, 6, 8, 10, 0), utc(2025, 6, 8, 16, 0), OperationalState(is_discharging=True))
        ev = InterruptionRulesEvaluator(LaytimeTerms.SHEX_EIU)
        res = ev.evaluate([s])

        assert res.slices[0].count_fraction == Decimal("0.000000")
        assert res.slices[0].count_status == IntervalCountStatus.EXCLUDED
        assert "EIU" in res.slices[0].deduction_reason


# ===========================================================================
# D. Calendar Boundary Automatic Partitioning
# ===========================================================================
class TestCalendarBoundaryPartitioning:

    def test_slice_crossing_saturday_cutoff_is_split(self):
        """
        A single slice from Saturday 15:00 to 19:00 spans the 17:00 cutoff.
        Must be partitioned into:
          [15:00 -> 17:00): Saturday working hours (counted)
          [17:00 -> 19:00): Saturday cutoff rest hours (excluded)
        """
        s = make_slice(utc(2025, 6, 7, 15, 0), utc(2025, 6, 7, 19, 0), OperationalState(is_discharging=False))
        ev = InterruptionRulesEvaluator(LaytimeTerms.SHEX, saturday_cutoff=True)
        res = ev.evaluate([s])

        assert len(res.slices) == 2

        # Sub-slice 0: [15:00 -> 17:00)
        assert res.slices[0].start_time == utc(2025, 6, 7, 15, 0)
        assert res.slices[0].end_time == utc(2025, 6, 7, 17, 0)
        assert res.slices[0].duration_hours == Decimal("2.000000")
        assert res.slices[0].count_fraction == Decimal("1.000000")
        assert res.slices[0].count_status == IntervalCountStatus.FULLY_COUNTED

        # Sub-slice 1: [17:00 -> 19:00)
        assert res.slices[1].start_time == utc(2025, 6, 7, 17, 0)
        assert res.slices[1].end_time == utc(2025, 6, 7, 19, 0)
        assert res.slices[1].duration_hours == Decimal("2.000000")
        assert res.slices[1].count_fraction == Decimal("0.000000")
        assert res.slices[1].count_status == IntervalCountStatus.EXCLUDED

    def test_slice_crossing_monday_resume_is_split(self):
        """
        A slice from Monday 06:00 to 10:00 spans the 08:00 resume point.
        Must be partitioned into:
          [06:00 -> 08:00): Pre-08:00 SHEX cutoff (excluded)
          [08:00 -> 10:00): Normal working hours (counted)
        """
        s = make_slice(utc(2025, 6, 9, 6, 0), utc(2025, 6, 9, 10, 0), OperationalState(is_discharging=False))
        ev = InterruptionRulesEvaluator(LaytimeTerms.SHEX, saturday_cutoff=True)
        res = ev.evaluate([s])

        assert len(res.slices) == 2
        assert res.slices[0].count_fraction == Decimal("0.000000")
        assert res.slices[0].duration_hours == Decimal("2.000000")
        assert res.slices[1].count_fraction == Decimal("1.000000")
        assert res.slices[1].duration_hours == Decimal("2.000000")


# ===========================================================================
# E. Mechanical & Crane Pro-Rata Evaluation
# ===========================================================================
class TestCraneProRata:

    def test_full_crane_breakdown_excluded(self):
        """0 working cranes out of 4 = 100% excluded."""
        state = OperationalState(is_crane_breakdown=True, cranes_working=0, cranes_total=4)
        s = make_slice(utc(2025, 6, 4, 10, 0), utc(2025, 6, 4, 14, 0), state)
        ev = InterruptionRulesEvaluator(LaytimeTerms.SHINC, crane_pro_rata=True)
        res = ev.evaluate([s])

        assert res.slices[0].count_fraction == Decimal("0.000000")
        assert res.slices[0].count_status == IntervalCountStatus.EXCLUDED
        assert res.slices[0].counted_hours == Decimal("0.000000")
        assert res.slices[0].excepted_hours == Decimal("4.000000")

    def test_half_cranes_working_half_fraction(self):
        """2 working cranes out of 4 = 50% fraction."""
        state = OperationalState(is_crane_breakdown=True, cranes_working=2, cranes_total=4)
        s = make_slice(utc(2025, 6, 4, 10, 0), utc(2025, 6, 4, 14, 0), state)
        ev = InterruptionRulesEvaluator(LaytimeTerms.SHINC, crane_pro_rata=True)
        res = ev.evaluate([s])

        assert res.slices[0].count_fraction == Decimal("0.500000")
        assert res.slices[0].count_status == IntervalCountStatus.PARTIALLY_COUNTED
        assert res.slices[0].counted_hours == Decimal("2.000000")
        assert res.slices[0].excepted_hours == Decimal("2.000000")
        assert "2/4" in res.slices[0].deduction_reason

    def test_three_quarters_cranes_working(self):
        """3 working cranes out of 4 = 75% fraction."""
        state = OperationalState(is_crane_breakdown=True, cranes_working=3, cranes_total=4)
        s = make_slice(utc(2025, 6, 4, 10, 0), utc(2025, 6, 4, 14, 0), state)
        ev = InterruptionRulesEvaluator(LaytimeTerms.SHINC, crane_pro_rata=True)
        res = ev.evaluate([s])

        assert res.slices[0].count_fraction == Decimal("0.750000")
        assert res.slices[0].count_status == IntervalCountStatus.PARTIALLY_COUNTED
        assert res.slices[0].counted_hours == Decimal("3.000000")
        assert res.slices[0].excepted_hours == Decimal("1.000000")

    def test_one_third_cranes_working_decimal_precision(self):
        """1 working crane out of 3 = 0.333333 fraction (precision to 6dp)."""
        state = OperationalState(is_crane_breakdown=True, cranes_working=1, cranes_total=3)
        s = make_slice(utc(2025, 6, 4, 10, 0), utc(2025, 6, 4, 13, 0), state)
        ev = InterruptionRulesEvaluator(LaytimeTerms.SHINC, crane_pro_rata=True)
        res = ev.evaluate([s])

        assert res.slices[0].count_fraction == Decimal("0.333333")
        assert res.slices[0].count_status == IntervalCountStatus.PARTIALLY_COUNTED


# ===========================================================================
# F. FHEX Terms (Friday Exclusion)
# ===========================================================================
class TestFHEXTerms:

    def test_fhex_thursday_counts(self):
        s = make_slice(utc(2025, 6, 5, 10, 0), utc(2025, 6, 5, 16, 0), OperationalState(is_discharging=True))
        ev = InterruptionRulesEvaluator(LaytimeTerms.FHEX)
        res = ev.evaluate([s])

        assert res.slices[0].count_fraction == Decimal("1.000000")
        assert res.slices[0].count_status == IntervalCountStatus.FULLY_COUNTED

    def test_fhex_friday_idle_excluded(self):
        s = make_slice(utc(2025, 6, 6, 10, 0), utc(2025, 6, 6, 16, 0), OperationalState(is_discharging=False))
        ev = InterruptionRulesEvaluator(LaytimeTerms.FHEX)
        res = ev.evaluate([s])

        assert res.slices[0].count_fraction == Decimal("0.000000")
        assert res.slices[0].count_status == IntervalCountStatus.EXCLUDED
        assert "Friday" in res.slices[0].deduction_reason


# ===========================================================================
# G. Pre-commencement & Post-completion
# ===========================================================================
class TestPrePostExclusions:

    def test_pre_commencement_is_always_excluded(self):
        s = make_slice(utc(2025, 6, 4, 8, 0), utc(2025, 6, 4, 12, 0), OperationalState(is_discharging=True), is_pre=True)
        ev = InterruptionRulesEvaluator(LaytimeTerms.SHINC)
        res = ev.evaluate([s])

        assert res.slices[0].count_fraction == Decimal("0.000000")
        assert res.slices[0].count_status == IntervalCountStatus.EXCLUDED
        assert "Pre-commencement" in res.slices[0].deduction_reason

    def test_post_completion_is_always_excluded(self):
        s = make_slice(utc(2025, 6, 4, 18, 0), utc(2025, 6, 4, 20, 0), OperationalState(is_discharging=False), is_post=True)
        ev = InterruptionRulesEvaluator(LaytimeTerms.SHINC)
        res = ev.evaluate([s])

        assert res.slices[0].count_fraction == Decimal("0.000000")
        assert res.slices[0].count_status == IntervalCountStatus.EXCLUDED
        assert "Post-Discharge" in res.slices[0].deduction_reason


# ===========================================================================
# H. Strike / Labour Disputes
# ===========================================================================
class TestLabourDispute:

    def test_standard_strike_excluded(self):
        state = OperationalState(is_labour_dispute=True)
        s = make_slice(utc(2025, 6, 4, 10, 0), utc(2025, 6, 4, 14, 0), state)
        ev = InterruptionRulesEvaluator(LaytimeTerms.SHINC, strike_clause_half_rate=False)
        res = ev.evaluate([s])

        assert res.slices[0].count_fraction == Decimal("0.000000")
        assert res.slices[0].count_status == IntervalCountStatus.EXCLUDED

    def test_half_rate_strike_counts_at_half(self):
        state = OperationalState(is_labour_dispute=True)
        s = make_slice(utc(2025, 6, 4, 10, 0), utc(2025, 6, 4, 14, 0), state)
        ev = InterruptionRulesEvaluator(LaytimeTerms.SHINC, strike_clause_half_rate=True)
        res = ev.evaluate([s])

        assert res.slices[0].count_fraction == Decimal("0.500000")
        assert res.slices[0].count_status == IntervalCountStatus.PARTIALLY_COUNTED
        assert res.slices[0].counted_hours == Decimal("2.000000")
        assert res.slices[0].excepted_hours == Decimal("2.000000")


# ===========================================================================
# I. Mathematical Accounting Invariant
# ===========================================================================
class TestAccountingInvariant:

    @pytest.mark.parametrize("terms", [
        LaytimeTerms.SHINC,
        LaytimeTerms.SHEX,
        LaytimeTerms.SHEX_EIU,
        LaytimeTerms.WWD,
        LaytimeTerms.WWDSHEX,
        LaytimeTerms.FHEX,
    ])
    def test_counted_plus_excepted_equals_total_elapsed(self, terms):
        """
        Verify across a 4-day multi-state voyage that:
        total_counted_hours + total_excepted_hours == total_elapsed_hours
        """
        slices = [
            make_slice(utc(2025, 6, 6, 8, 0), utc(2025, 6, 6, 14, 0), OperationalState(is_discharging=True), is_pre=True),
            make_slice(utc(2025, 6, 6, 14, 0), utc(2025, 6, 7, 12, 0), OperationalState(is_discharging=True)),
            make_slice(utc(2025, 6, 7, 12, 0), utc(2025, 6, 7, 18, 0), OperationalState(is_rain=True, is_discharging=False)),
            make_slice(utc(2025, 6, 7, 18, 0), utc(2025, 6, 8, 12, 0), OperationalState(is_crane_breakdown=True, cranes_working=2, cranes_total=4)),
            make_slice(utc(2025, 6, 8, 12, 0), utc(2025, 6, 9, 12, 0), OperationalState(is_discharging=True)),
            make_slice(utc(2025, 6, 9, 12, 0), utc(2025, 6, 9, 16, 0), OperationalState(is_discharging=False), is_post=True),
        ]
        ev = InterruptionRulesEvaluator(terms)
        res = ev.evaluate(slices)

        assert res.accounting_invariant_holds is True
        assert res.total_counted_hours + res.total_excepted_hours == res.total_elapsed_hours
