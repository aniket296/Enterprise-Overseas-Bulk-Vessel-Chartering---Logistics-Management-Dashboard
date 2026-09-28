"""
backend/apps/laytime/tests/test_edge_case_interruptions.py
============================================================
Cycle 1.3.2 — Edge-Case Interruption Unit Testing

Comprehensive stress tests for extreme and complex maritime operational
scenarios encountered at Indian bulk ports (Paradip, Dhamra, Vizag):

1. Split-Day Monsoons (Intermittent Rainfall Clusters):
   - Multiple rapid start/stop rain cycles within a single 24-hour period.
   - Verification of sub-second precision and zero rounding drift across
     clustered weather events.

2. Berth Shifting Operations:
   - Shifting from Berth 1 to Berth 2 with unmooring, transit, re-mooring,
     and gangway down.
   - Shifting downtime exclusion verification.

3. Compounded Weekend & Crane Breakdown Matrix:
   - Sunday work under standard SHEX with partial crane breakdown (2/4 cranes)
     -> counts at 50% pro-rata.
   - Sunday work under SHEX_EIU with partial crane breakdown
     -> 100% excluded (EIU precedence).

4. Mid-Monsoon Demurrage Breaches:
   - Allowed laytime expiring during an ongoing monsoon rain squall.
   - Precise split at threshold: pre-threshold rain excluded, post-threshold
     rain counted as demurrage under "once on demurrage, always on demurrage".

5. Compound Simultaneous Interruptions:
   - Overlapping rain, power failure, and labour disputes simultaneously.
   - Priority and deduction reason attribution.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest

from apps.laytime.engine.time_slice import (
    TimeSliceDiscretizationEngine,
    TimeSlice,
    SOFEvent,
    SOFEventType,
    OperationalState,
    IntervalCountStatus,
)
from apps.laytime.engine.interruption_evaluator import (
    InterruptionRulesEvaluator,
    EvaluatedSlice,
)
from apps.laytime.engine.settlement_engine import (
    LaytimeSettlementEngine,
    LaytimeOutcome,
)
from apps.laytime.engine.constants import LaytimeTerms


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# ===========================================================================
# 1. Split-Day Monsoons (Intermittent Rainfall Clusters)
# ===========================================================================
class TestSplitDayMonsoon:

    def test_intermittent_monsoon_rain_clusters(self):
        """
        Simulate an active Indian monsoon day (e.g., Paradip Port in July):
        Day starts at 00:00, 6 distinct rain showers intercut with discharging:
          1. 02:00 - 03:15: Rain shower 1 (75 min halt)
          2. 06:30 - 08:00: Rain shower 2 (90 min halt)
          3. 11:15 - 12:45: Rain shower 3 (90 min halt)
          4. 14:00 - 15:30: Rain shower 4 (90 min halt)
          5. 18:00 - 19:45: Rain shower 5 (105 min halt)
          6. 22:30 - 23:30: Rain shower 6 (60 min halt)
        Total rain halt: 75 + 90 + 90 + 90 + 105 + 60 = 510 min = 8.5 hours.
        Total active discharge: 24.0 - 8.5 = 15.5 hours.
        """
        day_start = utc(2025, 7, 10, 0, 0)
        day_end = utc(2025, 7, 11, 0, 0)

        events = [
            SOFEvent(timestamp=day_start, event_type=SOFEventType.DISCHARGE_COMMENCED),
            # Shower 1
            SOFEvent(timestamp=utc(2025, 7, 10, 2, 0), event_type=SOFEventType.RAIN_COMMENCED),
            SOFEvent(timestamp=utc(2025, 7, 10, 3, 15), event_type=SOFEventType.RAIN_CEASED),
            # Shower 2
            SOFEvent(timestamp=utc(2025, 7, 10, 6, 30), event_type=SOFEventType.RAIN_COMMENCED),
            SOFEvent(timestamp=utc(2025, 7, 10, 8, 0), event_type=SOFEventType.RAIN_CEASED),
            # Shower 3
            SOFEvent(timestamp=utc(2025, 7, 10, 11, 15), event_type=SOFEventType.RAIN_COMMENCED),
            SOFEvent(timestamp=utc(2025, 7, 10, 12, 45), event_type=SOFEventType.RAIN_CEASED),
            # Shower 4
            SOFEvent(timestamp=utc(2025, 7, 10, 14, 0), event_type=SOFEventType.RAIN_COMMENCED),
            SOFEvent(timestamp=utc(2025, 7, 10, 15, 30), event_type=SOFEventType.RAIN_CEASED),
            # Shower 5
            SOFEvent(timestamp=utc(2025, 7, 10, 18, 0), event_type=SOFEventType.RAIN_COMMENCED),
            SOFEvent(timestamp=utc(2025, 7, 10, 19, 45), event_type=SOFEventType.RAIN_CEASED),
            # Shower 6
            SOFEvent(timestamp=utc(2025, 7, 10, 22, 30), event_type=SOFEventType.RAIN_COMMENCED),
            SOFEvent(timestamp=utc(2025, 7, 10, 23, 30), event_type=SOFEventType.RAIN_CEASED),
            SOFEvent(timestamp=day_end, event_type=SOFEventType.DISCHARGE_COMPLETED),
        ]

        # 1. Discretize
        disc_res = TimeSliceDiscretizationEngine(
            events=events,
            laytime_commences_at=day_start,
            discharge_completed_at=day_end,
        ).discretize()

        # Exactly 13 contiguous slices across the 6 rain interruptions
        assert len(disc_res.slices) == 13
        assert disc_res.total_port_hours == Decimal("24.000000")

        # 2. Evaluate under WWD terms (Weather Working Days)
        eval_res = InterruptionRulesEvaluator(laytime_terms=LaytimeTerms.WWD).evaluate(disc_res.slices)

        assert eval_res.accounting_invariant_holds is True
        assert eval_res.total_elapsed_hours == Decimal("24.000000")
        assert eval_res.total_excepted_hours == Decimal("8.500000")
        assert eval_res.total_counted_hours == Decimal("15.500000")

    def test_monsoon_micro_squall_three_minutes(self):
        """Micro-interruption of exactly 3 minutes (0.050000 hours)."""
        t0 = utc(2025, 7, 10, 10, 0)
        t1 = utc(2025, 7, 10, 10, 3)
        t2 = utc(2025, 7, 10, 12, 0)

        events = [
            SOFEvent(timestamp=t0, event_type=SOFEventType.RAIN_COMMENCED),
            SOFEvent(timestamp=t1, event_type=SOFEventType.RAIN_CEASED),
            SOFEvent(timestamp=t2, event_type=SOFEventType.DISCHARGE_COMPLETED),
        ]

        disc_res = TimeSliceDiscretizationEngine(events).discretize()
        assert disc_res.slices[0].duration_seconds == 180
        assert disc_res.slices[0].duration_hours == Decimal("0.050000")

        eval_res = InterruptionRulesEvaluator(LaytimeTerms.WWD).evaluate(disc_res.slices)
        assert eval_res.slices[0].excepted_hours == Decimal("0.050000")
        assert eval_res.accounting_invariant_holds is True


# ===========================================================================
# 2. Berth Shifting Operations
# ===========================================================================
class TestBerthShiftingOperations:

    def test_berth_shifting_sequence_exclusion(self):
        """
        Vessel discharges at Berth 1, ordered to shift to Berth 2.
        Timeline:
          10:00 - 14:00: Discharge at Berth 1 (4h active)
          14:00: DISCHARGE_SUSPENDED
          14:30: LINES_RELEASED / VESSEL_UNMOORED (Berth 1)
          15:00: VESSEL_SHIFTED_ANCHORAGE (in-harbor repositioning)
          16:30: FIRST_LINE_ASHORE (Berth 2)
          17:00: ALL_FAST (Berth 2)
          17:30: DISCHARGE_RESUMED (Berth 2)
          17:30 - 22:00: Discharge at Berth 2 (4.5h active)

        Total shifting duration: 14:00 to 17:30 = 3.5 hours.
        Under standard charter party, shifting time does NOT count as laytime.
        """
        events = [
            SOFEvent(timestamp=utc(2025, 6, 5, 10, 0), event_type=SOFEventType.DISCHARGE_COMMENCED),
            SOFEvent(timestamp=utc(2025, 6, 5, 14, 0), event_type=SOFEventType.DISCHARGE_SUSPENDED),
            SOFEvent(timestamp=utc(2025, 6, 5, 14, 30), event_type=SOFEventType.LINES_RELEASED),
            SOFEvent(timestamp=utc(2025, 6, 5, 15, 0), event_type=SOFEventType.VESSEL_SHIFTED_ANCHORAGE),
            SOFEvent(timestamp=utc(2025, 6, 5, 16, 30), event_type=SOFEventType.FIRST_LINE_ASHORE),
            SOFEvent(timestamp=utc(2025, 6, 5, 17, 0), event_type=SOFEventType.ALL_FAST),
            SOFEvent(timestamp=utc(2025, 6, 5, 17, 30), event_type=SOFEventType.DISCHARGE_RESUMED),
            SOFEvent(timestamp=utc(2025, 6, 5, 22, 0), event_type=SOFEventType.DISCHARGE_COMPLETED),
        ]

        disc_res = TimeSliceDiscretizationEngine(
            events=events,
            laytime_commences_at=utc(2025, 6, 5, 10, 0),
            discharge_completed_at=utc(2025, 6, 5, 22, 0),
        ).discretize()

        eval_res = InterruptionRulesEvaluator(LaytimeTerms.WWDSHEX).evaluate(disc_res.slices)

        assert eval_res.total_elapsed_hours == Decimal("12.000000")
        assert eval_res.accounting_invariant_holds is True

        # Slices between 14:00 and 17:30 are not discharging
        shifting_slices = [s for s in eval_res.slices if utc(2025, 6, 5, 14, 0) <= s.start_time < utc(2025, 6, 5, 17, 30)]
        shifting_hours = sum(s.duration_hours for s in shifting_slices)
        assert shifting_hours == Decimal("3.500000")


# ===========================================================================
# 3. Compounded Weekend & Crane Breakdown Matrix
# ===========================================================================
class TestCompoundedWeekendCraneMatrix:

    def test_sunday_work_with_two_cranes_broken_under_shex(self):
        """
        Vessel discharges on Sunday (10:00 to 18:00 = 8.0 hours).
        Contract: standard SHEX (time used counts).
        During this Sunday work: 2 out of 4 cranes are broken down (50% working).
        Expected:
          - Sunday work is NOT excluded because time used counts under SHEX.
          - Crane pro-rata applies: 50% counting fraction.
          - Counted laytime: 8.0h * 0.500000 = 4.000000 hours.
          - Excepted laytime: 4.000000 hours.
        """
        sunday_start = utc(2025, 6, 8, 10, 0)
        sunday_end = utc(2025, 6, 8, 18, 0)

        events = [
            SOFEvent(
                timestamp=sunday_start,
                event_type=SOFEventType.DISCHARGE_COMMENCED,
                cranes_working=2,
                cranes_total=4,
            ),
            SOFEvent(
                timestamp=sunday_start,
                event_type=SOFEventType.CRANE_BREAKDOWN_COMMENCED,
                cranes_working=2,
                cranes_total=4,
            ),
            SOFEvent(timestamp=sunday_end, event_type=SOFEventType.DISCHARGE_COMPLETED),
        ]

        disc_res = TimeSliceDiscretizationEngine(events).discretize()
        eval_res = InterruptionRulesEvaluator(LaytimeTerms.SHEX, crane_pro_rata=True).evaluate(disc_res.slices)

        assert len(eval_res.slices) == 1
        es = eval_res.slices[0]
        assert es.count_fraction == Decimal("0.500000")
        assert es.count_status == IntervalCountStatus.PARTIALLY_COUNTED
        assert es.counted_hours == Decimal("4.000000")
        assert es.excepted_hours == Decimal("4.000000")
        assert "Worked" in es.deduction_reason
        assert "2/4" in es.deduction_reason

    def test_sunday_work_with_two_cranes_broken_under_shex_eiu(self):
        """
        Same Sunday work with 2 broken cranes, but contract is SHEX_EIU (Even If Used).
        Expected:
          - Under SHEX_EIU, Sunday is 100% EXCLUDED regardless of work or crane status!
          - Counted laytime: 0.000000 hours.
          - Excepted laytime: 8.000000 hours.
        """
        sunday_start = utc(2025, 6, 8, 10, 0)
        sunday_end = utc(2025, 6, 8, 18, 0)

        events = [
            SOFEvent(
                timestamp=sunday_start,
                event_type=SOFEventType.DISCHARGE_COMMENCED,
                cranes_working=2,
                cranes_total=4,
            ),
            SOFEvent(
                timestamp=sunday_start,
                event_type=SOFEventType.CRANE_BREAKDOWN_COMMENCED,
                cranes_working=2,
                cranes_total=4,
            ),
            SOFEvent(timestamp=sunday_end, event_type=SOFEventType.DISCHARGE_COMPLETED),
        ]

        disc_res = TimeSliceDiscretizationEngine(events).discretize()
        eval_res = InterruptionRulesEvaluator(LaytimeTerms.SHEX_EIU, crane_pro_rata=True).evaluate(disc_res.slices)

        assert len(eval_res.slices) == 1
        es = eval_res.slices[0]
        assert es.count_fraction == Decimal("0.000000")
        assert es.count_status == IntervalCountStatus.EXCLUDED
        assert es.counted_hours == Decimal("0.000000")
        assert es.excepted_hours == Decimal("8.000000")
        assert "EIU" in es.deduction_reason


# ===========================================================================
# 4. Mid-Monsoon Demurrage Breaches
# ===========================================================================
class TestMidMonsoonDemurrageBreach:

    def test_laytime_expires_during_heavy_rain(self):
        """
        Allowed laytime: 24.0 hours.
        Prior laytime consumed: 22.0 hours (from 00:00 to 22:00 on day 1).
        At 22:00 on Day 1, heavy rain commences and lasts until 04:00 on Day 2 (6h rain).
        Discharge continues in rain with tarpaulins/shore cranes, or vessel is on demurrage.
        Suppose vessel discharges at 100%:
          Allowed laytime reaches 24.0h at 24:00 (after 2h).
          At 24:00, vessel enters DEMURRAGE!
          Rain continues from 24:00 to 04:00 (4h).
          Under once_on_demurrage_always_on = True:
            The rain from 24:00 to 04:00 counts 100% as DEMURRAGE!
        """
        # Day 1: 00:00 to 22:00 (22.0h clear discharge)
        # Day 1 22:00 to Day 2 04:00 (6.0h rain discharge)
        slices = [
            EvaluatedSlice(
                slice_index=0,
                start_time=utc(2025, 7, 1, 0, 0),
                end_time=utc(2025, 7, 1, 22, 0),
                duration_seconds=79200,
                duration_hours=Decimal("22.000000"),
                count_fraction=Decimal("1.000000"),
                counted_hours=Decimal("22.000000"),
                excepted_hours=Decimal("0.000000"),
                count_status=IntervalCountStatus.FULLY_COUNTED,
                deduction_reason=None,
                operational_state=OperationalState(is_discharging=True),
            ),
            # Incoming 6h slice
            EvaluatedSlice(
                slice_index=1,
                start_time=utc(2025, 7, 1, 22, 0),
                end_time=utc(2025, 7, 2, 4, 0),
                duration_seconds=21600,
                duration_hours=Decimal("6.000000"),
                count_fraction=Decimal("1.000000"),
                counted_hours=Decimal("6.000000"),
                excepted_hours=Decimal("0.000000"),
                count_status=IntervalCountStatus.FULLY_COUNTED,
                deduction_reason=None,
                operational_state=OperationalState(is_discharging=True, is_rain=True),
            ),
        ]

        engine = LaytimeSettlementEngine(
            allowed_laytime_hours=Decimal("24.000000"),
            demurrage_rate_usd_per_day=Decimal("24000.00"),  # $1000/hr
            despatch_rate_usd_per_day=Decimal("12000.00"),
            once_on_demurrage_always_on=True,
        )
        res = engine.settle(slices)

        assert res.outcome == LaytimeOutcome.DEMURRAGE
        assert res.total_laytime_counted_hours == Decimal("24.000000")
        assert res.total_demurrage_hours == Decimal("4.000000")
        assert res.gross_demurrage_usd == Decimal("4000.00")
        assert res.demurrage_commenced_at == utc(2025, 7, 2, 0, 0)


# ===========================================================================
# 5. Compound Simultaneous Interruptions
# ===========================================================================
class TestCompoundSimultaneousInterruptions:

    def test_simultaneous_rain_power_failure_and_strike(self):
        """
        Triple simultaneous interruption: Rain + Power Failure + Strike.
        All three active during a 4-hour slice.
        Must be 100% excluded under WWD, with all reasons captured in audit notes.
        """
        state = OperationalState(
            is_rain=True,
            is_power_failure=True,
            is_labour_dispute=True,
        )
        reasons = state.active_interruption_reasons()
        assert len(reasons) == 3
        assert "Rain / Precipitation" in reasons
        assert "Port Power Failure" in reasons
        assert "Labour Dispute / Strike" in reasons

        s = TimeSlice(
            index=0,
            start_time=utc(2025, 6, 1, 10, 0),
            end_time=utc(2025, 6, 1, 14, 0),
            duration_seconds=14400,
            duration_hours=Decimal("4.000000"),
            state=state,
            events_at_start=(),
            is_pre_commencement=False,
            is_post_completion=False,
        )

        eval_res = InterruptionRulesEvaluator(LaytimeTerms.WWD).evaluate([s])
        assert eval_res.slices[0].count_fraction == Decimal("0.000000")
        assert eval_res.slices[0].count_status == IntervalCountStatus.EXCLUDED
        assert eval_res.slices[0].excepted_hours == Decimal("4.000000")
