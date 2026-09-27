"""
backend/apps/laytime/tests/test_time_slice.py
============================================================
Cycle 1.2.3 — Unit Tests: TimeSliceDiscretizationEngine

Test categories:
  A. Basic contiguity and mathematical invariants (no gaps, no overlaps)
  B. Clean milestone partitioning (laytime_commences_at & discharge_completed_at)
  C. Weather & breakdown operational state tracking
  D. Concurrent overlapping interruptions (rain + crane breakdown matrix)
  E. Coincident events (multiple events at identical timestamp)
  F. Pre-commencement and post-completion segregation
  G. Input validation & edge cases (naive tz, inverted timestamps, <2 cuts)
  H. Immutability checks (frozen dataclasses)
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from apps.laytime.engine.time_slice import (
    TimeSliceDiscretizationEngine,
    SOFEvent,
    SOFEventType,
    OperationalState,
    TimeSlice,
    DiscretizationResult,
    IntervalCountStatus,
)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# ===========================================================================
# A. Basic Contiguity and Mathematical Invariants
# ===========================================================================
class TestBasicContiguity:

    def test_contiguous_slices_no_gaps(self):
        """Sequential events produce strictly contiguous slices."""
        events = [
            SOFEvent(timestamp=utc(2025, 6, 1, 8, 0), event_type=SOFEventType.NOR_TENDERED),
            SOFEvent(timestamp=utc(2025, 6, 1, 14, 0), event_type=SOFEventType.ALL_FAST),
            SOFEvent(timestamp=utc(2025, 6, 1, 16, 0), event_type=SOFEventType.DISCHARGE_COMMENCED),
            SOFEvent(timestamp=utc(2025, 6, 2, 20, 0), event_type=SOFEventType.DISCHARGE_COMPLETED),
        ]
        engine = TimeSliceDiscretizationEngine(events)
        res = engine.discretize()

        assert len(res.slices) == 3

        # Contiguity check
        for i in range(len(res.slices) - 1):
            assert res.slices[i].end_time == res.slices[i + 1].start_time

        # Bound checks
        assert res.slices[0].start_time == utc(2025, 6, 1, 8, 0)
        assert res.slices[-1].end_time == utc(2025, 6, 2, 20, 0)

        # Mathematical duration conservation
        expected_total_seconds = int((utc(2025, 6, 2, 20, 0) - utc(2025, 6, 1, 8, 0)).total_seconds())
        assert res.total_port_seconds == expected_total_seconds
        assert sum(s.duration_seconds for s in res.slices) == expected_total_seconds

    def test_duration_hours_precision(self):
        """Hours must equal seconds / 3600 to 6 decimal places."""
        events = [
            SOFEvent(timestamp=utc(2025, 6, 1, 10, 0), event_type=SOFEventType.DISCHARGE_COMMENCED),
            SOFEvent(timestamp=utc(2025, 6, 1, 11, 30), event_type=SOFEventType.DISCHARGE_COMPLETED),
        ]
        res = TimeSliceDiscretizationEngine(events).discretize()
        assert len(res.slices) == 1
        s = res.slices[0]
        assert s.duration_seconds == 5400
        assert s.duration_hours == Decimal("1.500000")


# ===========================================================================
# B. Milestone Partitioning
# ===========================================================================
class TestMilestonePartitioning:

    def test_laytime_commences_at_creates_clean_boundary(self):
        """
        An injected laytime_commences_at creates a slice boundary even
        if no raw SOF event occurred at that exact minute.
        """
        events = [
            SOFEvent(timestamp=utc(2025, 6, 1, 6, 0), event_type=SOFEventType.NOR_TENDERED),
            SOFEvent(timestamp=utc(2025, 6, 1, 18, 0), event_type=SOFEventType.ALL_FAST),
        ]
        commences = utc(2025, 6, 1, 12, 0)  # 6 hours after NOR
        res = TimeSliceDiscretizationEngine(
            events=events,
            laytime_commences_at=commences,
        ).discretize()

        assert len(res.slices) == 2
        # Slice 0: [06:00 -> 12:00) Pre-commencement
        assert res.slices[0].start_time == utc(2025, 6, 1, 6, 0)
        assert res.slices[0].end_time == utc(2025, 6, 1, 12, 0)
        assert res.slices[0].is_pre_commencement is True
        assert res.slices[0].is_active_laytime_window is False

        # Slice 1: [12:00 -> 18:00) Active laytime
        assert res.slices[1].start_time == utc(2025, 6, 1, 12, 0)
        assert res.slices[1].end_time == utc(2025, 6, 1, 18, 0)
        assert res.slices[1].is_pre_commencement is False
        assert res.slices[1].is_active_laytime_window is True

    def test_discharge_completed_at_creates_clean_post_boundary(self):
        """
        Discharge completion partitions active laytime from post-completion
        unmooring/departure operations.
        """
        events = [
            SOFEvent(timestamp=utc(2025, 6, 1, 12, 0), event_type=SOFEventType.DISCHARGE_COMMENCED),
            SOFEvent(timestamp=utc(2025, 6, 1, 18, 0), event_type=SOFEventType.DISCHARGE_COMPLETED),
            SOFEvent(timestamp=utc(2025, 6, 1, 20, 0), event_type=SOFEventType.VESSEL_DEPARTED),
        ]
        res = TimeSliceDiscretizationEngine(
            events=events,
            laytime_commences_at=utc(2025, 6, 1, 12, 0),
            discharge_completed_at=utc(2025, 6, 1, 18, 0),
        ).discretize()

        assert len(res.slices) == 2
        # Slice 0: Active laytime
        assert res.slices[0].is_active_laytime_window is True
        assert res.slices[0].is_post_completion is False

        # Slice 1: Post-completion
        assert res.slices[1].is_active_laytime_window is False
        assert res.slices[1].is_post_completion is True


# ===========================================================================
# C. Operational State Tracking
# ===========================================================================
class TestOperationalStateTracking:

    def test_rain_event_state_toggle(self):
        """RAIN_COMMENCED activates is_rain, RAIN_CEASED deactivates it."""
        events = [
            SOFEvent(timestamp=utc(2025, 6, 1, 10, 0), event_type=SOFEventType.DISCHARGE_COMMENCED),
            SOFEvent(timestamp=utc(2025, 6, 1, 12, 0), event_type=SOFEventType.RAIN_COMMENCED),
            SOFEvent(timestamp=utc(2025, 6, 1, 14, 0), event_type=SOFEventType.RAIN_CEASED),
            SOFEvent(timestamp=utc(2025, 6, 1, 16, 0), event_type=SOFEventType.DISCHARGE_COMPLETED),
        ]
        res = TimeSliceDiscretizationEngine(events).discretize()
        assert len(res.slices) == 3

        # Slice 0 [10:00 -> 12:00): Discharging, Clear
        assert res.slices[0].state.is_discharging is True
        assert res.slices[0].state.is_rain is False
        assert res.slices[0].state.has_weather_interruption is False

        # Slice 1 [12:00 -> 14:00): Rain active
        assert res.slices[1].state.is_discharging is True
        assert res.slices[1].state.is_rain is True
        assert res.slices[1].state.has_weather_interruption is True
        assert "Rain / Precipitation" in res.slices[1].state.active_interruption_reasons()

        # Slice 2 [14:00 -> 16:00): Rain ceased
        assert res.slices[2].state.is_rain is False
        assert res.slices[2].state.has_weather_interruption is False

    def test_crane_breakdown_toggle(self):
        """CRANE_BREAKDOWN_COMMENCED -> CRANE_BREAKDOWN_RESUMED."""
        events = [
            SOFEvent(timestamp=utc(2025, 6, 1, 10, 0), event_type=SOFEventType.DISCHARGE_COMMENCED, cranes_working=4, cranes_total=4),
            SOFEvent(timestamp=utc(2025, 6, 1, 11, 0), event_type=SOFEventType.CRANE_BREAKDOWN_COMMENCED, cranes_working=2, cranes_total=4),
            SOFEvent(timestamp=utc(2025, 6, 1, 13, 0), event_type=SOFEventType.CRANE_BREAKDOWN_RESUMED, cranes_working=4, cranes_total=4),
            SOFEvent(timestamp=utc(2025, 6, 1, 15, 0), event_type=SOFEventType.DISCHARGE_COMPLETED),
        ]
        res = TimeSliceDiscretizationEngine(events).discretize()
        assert len(res.slices) == 3

        # Breakdown slice [11:00 -> 13:00)
        assert res.slices[1].state.is_crane_breakdown is True
        assert res.slices[1].state.cranes_working == 2
        assert res.slices[1].state.cranes_total == 4
        assert res.slices[1].state.has_mechanical_interruption is True

        # Resumed slice [13:00 -> 15:00)
        assert res.slices[2].state.is_crane_breakdown is False
        assert res.slices[2].state.cranes_working == 4


# ===========================================================================
# D. Concurrent Overlapping Interruptions Matrix
# ===========================================================================
class TestConcurrentInterruptions:

    def test_overlapping_rain_and_crane_breakdown(self):
        """
        Timeline:
          10:00 Discharge commenced
          11:00 Rain commenced
          12:00 Crane breakdown commenced (rain still active)
          13:00 Rain ceased (crane still broken)
          14:00 Crane resumed
          15:00 Discharge completed

        Expect 5 uniform discrete slices:
          1. [10, 11): Normal discharge
          2. [11, 12): Rain only
          3. [12, 13): Rain AND Crane breakdown
          4. [13, 14): Crane breakdown only
          5. [14, 15): Normal discharge
        """
        events = [
            SOFEvent(timestamp=utc(2025, 6, 1, 10, 0), event_type=SOFEventType.DISCHARGE_COMMENCED),
            SOFEvent(timestamp=utc(2025, 6, 1, 11, 0), event_type=SOFEventType.RAIN_COMMENCED),
            SOFEvent(timestamp=utc(2025, 6, 1, 12, 0), event_type=SOFEventType.CRANE_BREAKDOWN_COMMENCED),
            SOFEvent(timestamp=utc(2025, 6, 1, 13, 0), event_type=SOFEventType.RAIN_CEASED),
            SOFEvent(timestamp=utc(2025, 6, 1, 14, 0), event_type=SOFEventType.CRANE_BREAKDOWN_RESUMED),
            SOFEvent(timestamp=utc(2025, 6, 1, 15, 0), event_type=SOFEventType.DISCHARGE_COMPLETED),
        ]
        res = TimeSliceDiscretizationEngine(events).discretize()
        assert len(res.slices) == 5

        # Slice 1: Rain only
        assert res.slices[1].state.is_rain is True
        assert res.slices[1].state.is_crane_breakdown is False

        # Slice 2: Both
        assert res.slices[2].state.is_rain is True
        assert res.slices[2].state.is_crane_breakdown is True
        reasons = res.slices[2].state.active_interruption_reasons()
        assert "Rain / Precipitation" in reasons
        assert "Crane Breakdown" in reasons

        # Slice 3: Crane only
        assert res.slices[3].state.is_rain is False
        assert res.slices[3].state.is_crane_breakdown is True

        # Slice 4: Clear
        assert res.slices[4].state.is_rain is False
        assert res.slices[4].state.is_crane_breakdown is False


# ===========================================================================
# E. Coincident Events (Same Timestamp)
# ===========================================================================
class TestCoincidentEvents:

    def test_multiple_events_at_same_timestamp_no_zero_slice(self):
        """
        Multiple milestones occurring at the identical minute (e.g. ALL_FAST
        and CUSTOMS_BOARDING) must NOT produce a 0-second slice.
        """
        t_same = utc(2025, 6, 1, 14, 0)
        events = [
            SOFEvent(timestamp=utc(2025, 6, 1, 10, 0), event_type=SOFEventType.NOR_TENDERED),
            SOFEvent(timestamp=t_same, event_type=SOFEventType.ALL_FAST),
            SOFEvent(timestamp=t_same, event_type=SOFEventType.CUSTOMS_BOARDING),
            SOFEvent(timestamp=utc(2025, 6, 1, 16, 0), event_type=SOFEventType.CUSTOMS_CLEARED),
        ]
        res = TimeSliceDiscretizationEngine(events).discretize()
        # Exactly 2 slices: [10:00 -> 14:00) and [14:00 -> 16:00)
        assert len(res.slices) == 2
        assert res.slices[0].duration_seconds > 0
        assert res.slices[1].duration_seconds > 0

        # The slice starting at 14:00 has both events associated
        assert len(res.slices[1].events_at_start) == 2
        assert res.slices[1].state.is_berthed is True


# ===========================================================================
# F. Accounting Sum Invariant
# ===========================================================================
class TestAccountingSumInvariant:

    def test_sum_of_partitions_equals_total(self):
        """pre + active + post seconds == total port seconds."""
        events = [
            SOFEvent(timestamp=utc(2025, 6, 1, 6, 0), event_type=SOFEventType.NOR_TENDERED),
            SOFEvent(timestamp=utc(2025, 6, 1, 8, 0), event_type=SOFEventType.ALL_FAST),
            SOFEvent(timestamp=utc(2025, 6, 1, 16, 0), event_type=SOFEventType.DISCHARGE_COMMENCED),
            SOFEvent(timestamp=utc(2025, 6, 2, 10, 0), event_type=SOFEventType.DISCHARGE_COMPLETED),
            SOFEvent(timestamp=utc(2025, 6, 2, 14, 0), event_type=SOFEventType.VESSEL_DEPARTED),
        ]
        commences = utc(2025, 6, 1, 12, 0)
        completed = utc(2025, 6, 2, 10, 0)

        res = TimeSliceDiscretizationEngine(
            events=events,
            laytime_commences_at=commences,
            discharge_completed_at=completed,
        ).discretize()

        assert res.pre_commencement_seconds + res.active_laytime_seconds + res.post_completion_seconds == res.total_port_seconds
        assert res.pre_commencement_hours + res.active_laytime_hours + res.post_completion_hours == res.total_port_hours


# ===========================================================================
# G. Input Validation & Edge Cases
# ===========================================================================
class TestInputValidation:

    def test_naive_datetime_raises_value_error(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            SOFEvent(timestamp=datetime(2025, 6, 1, 10, 0), event_type=SOFEventType.NOR_TENDERED)

    def test_inverted_milestones_raises(self):
        events = [
            SOFEvent(timestamp=utc(2025, 6, 1, 10, 0), event_type=SOFEventType.NOR_TENDERED),
            SOFEvent(timestamp=utc(2025, 6, 2, 10, 0), event_type=SOFEventType.DISCHARGE_COMPLETED),
        ]
        with pytest.raises(ValueError, match="cannot be after"):
            TimeSliceDiscretizationEngine(
                events=events,
                laytime_commences_at=utc(2025, 6, 2, 12, 0),
                discharge_completed_at=utc(2025, 6, 1, 12, 0),
            )

    def test_single_event_insufficient_for_slices(self):
        events = [
            SOFEvent(timestamp=utc(2025, 6, 1, 10, 0), event_type=SOFEventType.NOR_TENDERED),
        ]
        with pytest.raises(ValueError, match="At least two distinct"):
            TimeSliceDiscretizationEngine(events).discretize()


# ===========================================================================
# H. Immutability
# ===========================================================================
class TestImmutability:

    def test_time_slice_is_frozen(self):
        events = [
            SOFEvent(timestamp=utc(2025, 6, 1, 10, 0), event_type=SOFEventType.NOR_TENDERED),
            SOFEvent(timestamp=utc(2025, 6, 1, 12, 0), event_type=SOFEventType.DISCHARGE_COMPLETED),
        ]
        res = TimeSliceDiscretizationEngine(events).discretize()
        s = res.slices[0]
        with pytest.raises((AttributeError, TypeError)):
            s.duration_seconds = 999  # type: ignore
