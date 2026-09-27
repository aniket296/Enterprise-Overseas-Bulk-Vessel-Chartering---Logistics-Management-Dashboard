"""
backend/apps/laytime/tests/test_laycan_validator.py
============================================================
Cycle 1.2.1 — Unit Tests: LaycanValidator & NOR Window Evaluator

Test categories:
  A. NOR within laycan (standard path)
  B. NOR before laycan (held NOR)
  C. NOR after laycan (late vessel)
  D. NOR not tendered
  E. Office-hours clause behaviour (WIPON, WIBON)
  F. Weekend NOR detection
  G. Turn-time computation accuracy
  H. Input validation errors
  I. Utility properties (laycan duration, midpoint)
"""

import pytest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from apps.laytime.engine.laycan_validator import (
    LaycanValidator,
    NORStatus,
    OfficeHoursClause,
    NORValidationResult,
)

# Convenience UTC factory
def utc(year, month, day, hour=0, minute=0, second=0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


# ===========================================================================
# A. NOR within laycan
# ===========================================================================
class TestNORWithinLaycan:

    def test_nor_on_laycan_start(self):
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 1),
            laycan_end      = date(2025, 6, 10),
            turn_time_hours = Decimal("6"),
            nor_tendered_at = utc(2025, 6, 1, 10, 0),
        )
        result = v.validate()
        assert result.nor_status == NORStatus.WITHIN_LAYCAN
        assert result.effective_nor_at == utc(2025, 6, 1, 10, 0)
        assert result.days_early is None
        assert result.days_late is None

    def test_nor_on_laycan_end(self):
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 1),
            laycan_end      = date(2025, 6, 10),
            nor_tendered_at = utc(2025, 6, 10, 18, 30),
        )
        result = v.validate()
        assert result.nor_status == NORStatus.WITHIN_LAYCAN

    def test_nor_mid_laycan(self):
        v = LaycanValidator(
            laycan_start    = date(2025, 7, 1),
            laycan_end      = date(2025, 7, 15),
            nor_tendered_at = utc(2025, 7, 8, 9, 0),
        )
        result = v.validate()
        assert result.nor_status == NORStatus.WITHIN_LAYCAN
        assert result.effective_nor_at == utc(2025, 7, 8, 9, 0)

    def test_laytime_commences_after_turn_time(self):
        """laytime_commences_at = effective_nor + 6 hours."""
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 1),
            laycan_end      = date(2025, 6, 10),
            turn_time_hours = Decimal("6"),
            nor_tendered_at = utc(2025, 6, 5, 8, 0),
        )
        result = v.validate()
        assert result.laytime_commences_at == utc(2025, 6, 5, 14, 0)

    def test_fractional_turn_time(self):
        """Turn-time of 6.5 hours = 6h 30m."""
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 1),
            laycan_end      = date(2025, 6, 10),
            turn_time_hours = Decimal("6.5"),
            nor_tendered_at = utc(2025, 6, 5, 8, 0),
        )
        result = v.validate()
        assert result.laytime_commences_at == utc(2025, 6, 5, 14, 30)

    def test_within_laycan_no_notes_about_late(self):
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 1),
            laycan_end      = date(2025, 6, 10),
            nor_tendered_at = utc(2025, 6, 5, 10, 0),
        )
        result = v.validate()
        assert not any("LATE" in n for n in result.validation_notes)
        assert not any("BEFORE" in n for n in result.validation_notes)


# ===========================================================================
# B. NOR before laycan (held NOR)
# ===========================================================================
class TestNORBeforeLaycan:

    def test_nor_one_day_before_laycan(self):
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 10),
            laycan_end      = date(2025, 6, 20),
            turn_time_hours = Decimal("6"),
            nor_tendered_at = utc(2025, 6, 9, 14, 0),  # 1 day early
        )
        result = v.validate()
        assert result.nor_status == NORStatus.BEFORE_LAYCAN
        assert result.days_early == 1
        # Effective NOR = midnight UTC on laycan_start
        assert result.effective_nor_at == utc(2025, 6, 10, 0, 0)

    def test_nor_held_laytime_commences_from_laycan_start(self):
        """Laytime commences = laycan_start midnight + turn_time_hours."""
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 10),
            laycan_end      = date(2025, 6, 20),
            turn_time_hours = Decimal("6"),
            nor_tendered_at = utc(2025, 6, 5, 8, 0),  # 5 days early
        )
        result = v.validate()
        assert result.effective_nor_at == utc(2025, 6, 10, 0, 0)
        assert result.laytime_commences_at == utc(2025, 6, 10, 6, 0)

    def test_nor_five_days_before(self):
        v = LaycanValidator(
            laycan_start    = date(2025, 8, 15),
            laycan_end      = date(2025, 8, 25),
            nor_tendered_at = utc(2025, 8, 10, 6, 0),
        )
        result = v.validate()
        assert result.days_early == 5

    def test_early_nor_note_contains_held_language(self):
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 10),
            laycan_end      = date(2025, 6, 20),
            nor_tendered_at = utc(2025, 6, 8, 14, 0),
        )
        result = v.validate()
        assert any("held" in n.lower() for n in result.validation_notes)


# ===========================================================================
# C. NOR after laycan (late vessel)
# ===========================================================================
class TestNORAfterLaycan:

    def test_nor_one_day_late(self):
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 1),
            laycan_end      = date(2025, 6, 10),
            nor_tendered_at = utc(2025, 6, 11, 9, 0),
        )
        result = v.validate()
        assert result.nor_status == NORStatus.AFTER_LAYCAN
        assert result.days_late == 1
        assert result.days_early is None

    def test_late_effective_nor_is_actual_nor(self):
        """For late NOR, effective NOR = actual tender (not adjusted)."""
        nor_time = utc(2025, 6, 15, 10, 0)
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 1),
            laycan_end      = date(2025, 6, 10),
            nor_tendered_at = nor_time,
        )
        result = v.validate()
        assert result.effective_nor_at == nor_time

    def test_late_nor_note_contains_cancellation_warning(self):
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 1),
            laycan_end      = date(2025, 6, 10),
            nor_tendered_at = utc(2025, 6, 12, 9, 0),
        )
        result = v.validate()
        assert any("cancellation" in n.lower() for n in result.validation_notes)


# ===========================================================================
# D. NOR not tendered
# ===========================================================================
class TestNORNotTendered:

    def test_none_nor_returns_not_yet_tendered(self):
        v = LaycanValidator(
            laycan_start = date(2025, 6, 1),
            laycan_end   = date(2025, 6, 10),
        )
        result = v.validate()
        assert result.nor_status == NORStatus.NOT_YET_TENDERED
        assert result.effective_nor_at is None
        assert result.laytime_commences_at is None
        assert result.allowed_hours_min is None if hasattr(result, "allowed_hours_min") else True

    def test_none_nor_all_timestamps_null(self):
        v = LaycanValidator(
            laycan_start = date(2025, 6, 1),
            laycan_end   = date(2025, 6, 10),
        )
        result = v.validate()
        assert result.nor_tendered_at is None
        assert result.is_within_office_hours is None
        assert result.is_weekend_nor is None


# ===========================================================================
# E. Office-hours clause behaviour
# ===========================================================================
class TestOfficeHoursClauses:

    def test_wipon_laytime_commences_immediately(self):
        """WIPON: laytime commences at effective_nor, turn-time waived."""
        v = LaycanValidator(
            laycan_start         = date(2025, 6, 1),
            laycan_end           = date(2025, 6, 10),
            turn_time_hours      = Decimal("6"),
            office_hours_clause  = OfficeHoursClause.WIPON,
            nor_tendered_at      = utc(2025, 6, 5, 9, 0),
        )
        result = v.validate()
        assert result.laytime_commences_at == result.effective_nor_at

    def test_wipon_note_mentions_waived(self):
        v = LaycanValidator(
            laycan_start         = date(2025, 6, 1),
            laycan_end           = date(2025, 6, 10),
            office_hours_clause  = OfficeHoursClause.WIPON,
            nor_tendered_at      = utc(2025, 6, 5, 9, 0),
        )
        result = v.validate()
        assert any("waived" in n.lower() for n in result.validation_notes)

    def test_wibon_turn_time_still_applies(self):
        """WIBON does not waive turn-time; it just confirms NOR valid at anchor."""
        v = LaycanValidator(
            laycan_start         = date(2025, 6, 1),
            laycan_end           = date(2025, 6, 10),
            turn_time_hours      = Decimal("6"),
            office_hours_clause  = OfficeHoursClause.WIBON,
            nor_tendered_at      = utc(2025, 6, 5, 8, 0),
        )
        result = v.validate()
        assert result.laytime_commences_at == utc(2025, 6, 5, 14, 0)

    def test_out_of_office_hours_flagged(self):
        """NOR at 03:00 UTC → outside office hours → note added."""
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 1),
            laycan_end      = date(2025, 6, 10),
            nor_tendered_at = utc(2025, 6, 5, 3, 0),
        )
        result = v.validate()
        assert result.is_within_office_hours is False
        assert any("outside office hours" in n for n in result.validation_notes)

    def test_in_office_hours_no_flag(self):
        """NOR at 10:00 → within office hours."""
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 1),
            laycan_end      = date(2025, 6, 10),
            nor_tendered_at = utc(2025, 6, 5, 10, 0),
        )
        result = v.validate()
        assert result.is_within_office_hours is True


# ===========================================================================
# F. Weekend NOR detection
# ===========================================================================
class TestWeekendNOR:

    def test_saturday_nor_flagged(self):
        # 2025-06-07 is a Saturday
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 1),
            laycan_end      = date(2025, 6, 15),
            nor_tendered_at = utc(2025, 6, 7, 10, 0),
        )
        result = v.validate()
        assert result.is_weekend_nor is True
        assert any("weekend" in n.lower() for n in result.validation_notes)

    def test_sunday_nor_flagged(self):
        # 2025-06-08 is a Sunday
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 1),
            laycan_end      = date(2025, 6, 15),
            nor_tendered_at = utc(2025, 6, 8, 14, 0),
        )
        result = v.validate()
        assert result.is_weekend_nor is True

    def test_weekday_nor_not_weekend(self):
        # 2025-06-05 is a Thursday
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 1),
            laycan_end      = date(2025, 6, 15),
            nor_tendered_at = utc(2025, 6, 5, 10, 0),
        )
        result = v.validate()
        assert result.is_weekend_nor is False


# ===========================================================================
# G. Turn-time computation accuracy
# ===========================================================================
class TestTurnTimeAccuracy:

    @pytest.mark.parametrize("turn_hours, expected_delta_hours", [
        ("6",    6),
        ("0",    0),
        ("12",   12),
        ("6.5",  6.5),
        ("1.25", 1.25),
    ])
    def test_turn_time_values(self, turn_hours, expected_delta_hours):
        base_time = utc(2025, 6, 5, 8, 0)
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 1),
            laycan_end      = date(2025, 6, 15),
            turn_time_hours = Decimal(turn_hours),
            nor_tendered_at = base_time,
        )
        result = v.validate()
        expected = base_time + timedelta(hours=expected_delta_hours)
        assert result.laytime_commences_at == expected

    def test_zero_turn_time_commences_at_nor(self):
        nor_time = utc(2025, 6, 5, 14, 30)
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 1),
            laycan_end      = date(2025, 6, 15),
            turn_time_hours = Decimal("0"),
            nor_tendered_at = nor_time,
        )
        result = v.validate()
        assert result.laytime_commences_at == nor_time


# ===========================================================================
# H. Input validation errors
# ===========================================================================
class TestLaycanValidatorInputs:

    def test_laycan_start_after_end_raises(self):
        with pytest.raises(ValueError, match="laycan_start"):
            LaycanValidator(
                laycan_start = date(2025, 6, 20),
                laycan_end   = date(2025, 6, 10),
            )

    def test_naive_datetime_raises(self):
        """Timezone-naive datetime must be rejected."""
        with pytest.raises(ValueError, match="timezone-aware"):
            LaycanValidator(
                laycan_start    = date(2025, 6, 1),
                laycan_end      = date(2025, 6, 10),
                nor_tendered_at = datetime(2025, 6, 5, 10, 0),  # naive
            )

    def test_negative_turn_time_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            LaycanValidator(
                laycan_start    = date(2025, 6, 1),
                laycan_end      = date(2025, 6, 10),
                turn_time_hours = Decimal("-1"),
            )

    def test_result_is_frozen(self):
        v = LaycanValidator(
            laycan_start    = date(2025, 6, 1),
            laycan_end      = date(2025, 6, 10),
            nor_tendered_at = utc(2025, 6, 5, 9, 0),
        )
        result = v.validate()
        with pytest.raises((AttributeError, TypeError)):
            result.nor_status = NORStatus.AFTER_LAYCAN  # type: ignore


# ===========================================================================
# I. Utility properties
# ===========================================================================
class TestUtilityProperties:

    def test_laycan_duration_single_day(self):
        v = LaycanValidator(date(2025, 6, 5), date(2025, 6, 5))
        assert v.laycan_duration_days == 1

    def test_laycan_duration_ten_days(self):
        v = LaycanValidator(date(2025, 6, 1), date(2025, 6, 10))
        assert v.laycan_duration_days == 10

    def test_laycan_midpoint_even_range(self):
        v = LaycanValidator(date(2025, 6, 1), date(2025, 6, 11))
        assert v.laycan_midpoint == date(2025, 6, 6)

    def test_laycan_midpoint_odd_range(self):
        v = LaycanValidator(date(2025, 6, 1), date(2025, 6, 15))
        assert v.laycan_midpoint == date(2025, 6, 8)
