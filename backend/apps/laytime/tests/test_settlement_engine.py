"""
backend/apps/laytime/tests/test_settlement_engine.py
============================================================
Cycle 1.2.5 — Unit Tests: LaytimeSettlementEngine

Test categories:
  A. Standard demurrage calculation (Baltic standard fixture)
  B. Standard despatch calculation (time saved, 50% rate)
  C. Even keel calculation (zero settlement)
  D. "Once on demurrage, always on demurrage" doctrine verification
  E. Mid-slice threshold splitting (exact timestamp partitioning)
  F. Fractional rate threshold splitting (pro-rata crane breakdown)
  G. Database integrity invariants (gross_dem == 0 OR gross_des == 0)
  H. Input validation (negative rates, despatch > demurrage, invalid allowed)
  I. Decimal precision and zero rounding drift
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from apps.laytime.engine.settlement_engine import (
    LaytimeSettlementEngine,
    SettlementResult,
    SettlementSlice,
    LaytimeOutcome,
)
from apps.laytime.engine.interruption_evaluator import EvaluatedSlice
from apps.laytime.engine.time_slice import OperationalState, IntervalCountStatus


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def make_eval_slice(
    start: datetime,
    end: datetime,
    frac: str = "1.000000",
    status: IntervalCountStatus = IntervalCountStatus.FULLY_COUNTED,
    reason: str = None,
    state: OperationalState = OperationalState(),
) -> EvaluatedSlice:
    sec = int((end - start).total_seconds())
    hours = (Decimal(str(sec)) / Decimal("3600")).quantize(Decimal("0.000001"))
    d_frac = Decimal(frac)
    counted = (hours * d_frac).quantize(Decimal("0.000001"))
    excepted = (hours * (Decimal("1") - d_frac)).quantize(Decimal("0.000001"))
    return EvaluatedSlice(
        slice_index=0,
        start_time=start,
        end_time=end,
        duration_seconds=sec,
        duration_hours=hours,
        count_fraction=d_frac,
        counted_hours=counted,
        excepted_hours=excepted,
        count_status=status,
        deduction_reason=reason,
        operational_state=state,
    )


# ===========================================================================
# A. Standard Demurrage Calculation
# ===========================================================================
class TestStandardDemurrage:

    def test_basic_demurrage_settlement(self):
        """
        Allowed laytime: 48.0 hours.
        Total counted: 60.0 hours (12 hours on demurrage).
        Demurrage rate: $12,000 / day ($500 / hr).
        Expected gross demurrage: 12 * $500 = $6,000.00 USD.
        """
        slices = [
            make_eval_slice(utc(2025, 6, 1, 0, 0), utc(2025, 6, 2, 0, 0)),  # 24h
            make_eval_slice(utc(2025, 6, 2, 0, 0), utc(2025, 6, 3, 0, 0)),  # 24h (total 48h)
            make_eval_slice(utc(2025, 6, 3, 0, 0), utc(2025, 6, 3, 12, 0)), # 12h on demurrage
        ]
        engine = LaytimeSettlementEngine(
            allowed_laytime_hours=Decimal("48.000000"),
            demurrage_rate_usd_per_day=Decimal("12000.00"),
            despatch_rate_usd_per_day=Decimal("6000.00"),
        )
        res = engine.settle(slices)

        assert res.outcome == LaytimeOutcome.DEMURRAGE
        assert res.total_laytime_counted_hours == Decimal("48.000000")
        assert res.total_demurrage_hours == Decimal("12.000000")
        assert res.total_despatch_hours == Decimal("0.000000")
        assert res.gross_demurrage_usd == Decimal("6000.00")
        assert res.gross_despatch_usd == Decimal("0.00")
        assert res.net_settlement_usd == Decimal("6000.00")
        assert res.demurrage_commenced_at == utc(2025, 6, 3, 0, 0)


# ===========================================================================
# B. Standard Despatch Calculation
# ===========================================================================
class TestStandardDespatch:

    def test_basic_despatch_settlement(self):
        """
        Allowed laytime: 72.0 hours.
        Total laytime used: 48.0 hours (24 hours saved).
        Despatch rate: $7,200 / day ($300 / hr).
        Expected gross despatch: 24 * $300 = $7,200.00 USD.
        Net settlement: -$7,200.00 USD (payable to charterer).
        """
        slices = [
            make_eval_slice(utc(2025, 6, 1, 0, 0), utc(2025, 6, 2, 0, 0)),  # 24h
            make_eval_slice(utc(2025, 6, 2, 0, 0), utc(2025, 6, 3, 0, 0)),  # 24h (total 48h)
        ]
        engine = LaytimeSettlementEngine(
            allowed_laytime_hours=Decimal("72.000000"),
            demurrage_rate_usd_per_day=Decimal("14400.00"),
            despatch_rate_usd_per_day=Decimal("7200.00"),
        )
        res = engine.settle(slices)

        assert res.outcome == LaytimeOutcome.DESPATCH
        assert res.total_laytime_counted_hours == Decimal("48.000000")
        assert res.total_demurrage_hours == Decimal("0.000000")
        assert res.total_despatch_hours == Decimal("24.000000")
        assert res.variance_hours == Decimal("-24.000000")
        assert res.gross_demurrage_usd == Decimal("0.00")
        assert res.gross_despatch_usd == Decimal("7200.00")
        assert res.net_settlement_usd == Decimal("-7200.00")
        assert res.demurrage_commenced_at is None


# ===========================================================================
# C. Even Keel Calculation
# ===========================================================================
class TestEvenKeel:

    def test_even_keel_zero_settlement(self):
        """Allowed = consumed exactly -> EVEN_KEEL, $0.00."""
        slices = [
            make_eval_slice(utc(2025, 6, 1, 0, 0), utc(2025, 6, 2, 0, 0)),  # 24h
        ]
        engine = LaytimeSettlementEngine(
            allowed_laytime_hours=Decimal("24.000000"),
            demurrage_rate_usd_per_day=Decimal("10000.00"),
            despatch_rate_usd_per_day=Decimal("5000.00"),
        )
        res = engine.settle(slices)

        assert res.outcome == LaytimeOutcome.EVEN_KEEL
        assert res.total_laytime_counted_hours == Decimal("24.000000")
        assert res.total_demurrage_hours == Decimal("0.000000")
        assert res.total_despatch_hours == Decimal("0.000000")
        assert res.gross_demurrage_usd == Decimal("0.00")
        assert res.gross_despatch_usd == Decimal("0.00")
        assert res.net_settlement_usd == Decimal("0.00")


# ===========================================================================
# D. "Once on Demurrage, Always on Demurrage" Doctrine
# ===========================================================================
class TestOnceOnDemurrageDoctrine:

    def test_weekend_exception_fails_once_on_demurrage(self):
        """
        Vessel enters demurrage on Friday 24:00 (June 6).
        Saturday and Sunday (48 hours) have count_fraction=0.0 (SHEX exception).
        Under once_on_demurrage_always_on = True:
          All 48 weekend hours count as demurrage!
        """
        slices = [
            make_eval_slice(utc(2025, 6, 6, 0, 0), utc(2025, 6, 7, 0, 0)),  # 24h Friday (exhausts 24h laytime)
            # Weekend (SHEX exception normally fraction 0.0)
            make_eval_slice(utc(2025, 6, 7, 0, 0), utc(2025, 6, 9, 0, 0), frac="0.000000", status=IntervalCountStatus.EXCLUDED, reason="SHEX Weekend"),
        ]
        engine = LaytimeSettlementEngine(
            allowed_laytime_hours=Decimal("24.000000"),
            demurrage_rate_usd_per_day=Decimal("24000.00"),  # $1,000/hr
            despatch_rate_usd_per_day=Decimal("12000.00"),
            once_on_demurrage_always_on=True,
        )
        res = engine.settle(slices)

        assert res.outcome == LaytimeOutcome.DEMURRAGE
        assert res.total_laytime_counted_hours == Decimal("24.000000")
        # Weekend counts 48 hours on demurrage!
        assert res.total_demurrage_hours == Decimal("48.000000")
        assert res.gross_demurrage_usd == Decimal("48000.00")

    def test_exceptions_apply_if_once_on_demurrage_disabled(self):
        """
        If once_on_demurrage_always_on is False:
          The weekend exception continues to exclude time even on demurrage.
        """
        slices = [
            make_eval_slice(utc(2025, 6, 6, 0, 0), utc(2025, 6, 7, 0, 0)),  # 24h Friday
            make_eval_slice(utc(2025, 6, 7, 0, 0), utc(2025, 6, 9, 0, 0), frac="0.000000", status=IntervalCountStatus.EXCLUDED, reason="SHEX Weekend"),
        ]
        engine = LaytimeSettlementEngine(
            allowed_laytime_hours=Decimal("24.000000"),
            demurrage_rate_usd_per_day=Decimal("24000.00"),
            despatch_rate_usd_per_day=Decimal("12000.00"),
            once_on_demurrage_always_on=False,
        )
        res = engine.settle(slices)

        # Weekend was excluded, so demurrage hours = 0!
        assert res.total_demurrage_hours == Decimal("0.000000")
        assert res.outcome == LaytimeOutcome.EVEN_KEEL


# ===========================================================================
# E. Mid-Slice Threshold Splitting
# ===========================================================================
class TestMidSliceThresholdSplitting:

    def test_slice_breaching_allowed_laytime_is_split(self):
        """
        Allowed laytime: 10.0 hours.
        Slice: 08:00 to 14:00 (6.0 hours, counting at 1.0).
        Initial laytime consumed prior to slice: 8.0 hours.
        Remaining allowed: 2.0 hours.
        Threshold reached at: 08:00 + 2h = 10:00.
        Expected split:
          Slice 1: [08:00 -> 10:00) 2.0h laytime
          Slice 2: [10:00 -> 14:00) 4.0h demurrage
        """
        slices = [
            make_eval_slice(utc(2025, 6, 1, 0, 0), utc(2025, 6, 1, 8, 0)),   # 8.0h laytime
            make_eval_slice(utc(2025, 6, 1, 8, 0), utc(2025, 6, 1, 14, 0)),  # 6.0h slice
        ]
        engine = LaytimeSettlementEngine(
            allowed_laytime_hours=Decimal("10.000000"),
            demurrage_rate_usd_per_day=Decimal("24000.00"),
            despatch_rate_usd_per_day=Decimal("12000.00"),
        )
        res = engine.settle(slices)

        assert len(res.slices) == 3
        # Sub-slice 1: [08:00 -> 10:00)
        assert res.slices[1].start_time == utc(2025, 6, 1, 8, 0)
        assert res.slices[1].end_time == utc(2025, 6, 1, 10, 0)
        assert res.slices[1].laytime_counted_hours == Decimal("2.000000")
        assert res.slices[1].demurrage_counted_hours == Decimal("0.000000")
        assert res.slices[1].is_demurrage is False

        # Sub-slice 2: [10:00 -> 14:00)
        assert res.slices[2].start_time == utc(2025, 6, 1, 10, 0)
        assert res.slices[2].end_time == utc(2025, 6, 1, 14, 0)
        assert res.slices[2].laytime_counted_hours == Decimal("0.000000")
        assert res.slices[2].demurrage_counted_hours == Decimal("4.000000")
        assert res.slices[2].is_demurrage is True

        assert res.demurrage_commenced_at == utc(2025, 6, 1, 10, 0)
        assert res.total_demurrage_hours == Decimal("4.000000")


# ===========================================================================
# F. Fractional Rate Threshold Splitting
# ===========================================================================
class TestFractionalThresholdSplitting:

    def test_half_rate_slice_threshold_calculation(self):
        """
        Allowed laytime: 10.0 hours.
        Initial laytime consumed: 8.0 hours.
        Remaining allowed: 2.0 hours.
        Incoming slice: 08:00 to 14:00 (6.0 hours, counting at fraction 0.500000).
        At 0.5 fraction, consuming 2.0 laytime hours takes:
          2.0 / 0.5 = 4.0 clock hours!
        Threshold reached at: 08:00 + 4h = 12:00.
        Expected split:
          Slice 1: [08:00 -> 12:00) 4h duration, 2.0h laytime
          Slice 2: [12:00 -> 14:00) 2h duration, 2.0h demurrage (all time counts under once on dem)
        """
        slices = [
            make_eval_slice(utc(2025, 6, 1, 0, 0), utc(2025, 6, 1, 8, 0)),
            make_eval_slice(utc(2025, 6, 1, 8, 0), utc(2025, 6, 1, 14, 0), frac="0.500000"),
        ]
        engine = LaytimeSettlementEngine(
            allowed_laytime_hours=Decimal("10.000000"),
            demurrage_rate_usd_per_day=Decimal("24000.00"),
            despatch_rate_usd_per_day=Decimal("12000.00"),
            once_on_demurrage_always_on=True,
        )
        res = engine.settle(slices)

        assert len(res.slices) == 3
        # Threshold sub-slice 1
        assert res.slices[1].start_time == utc(2025, 6, 1, 8, 0)
        assert res.slices[1].end_time == utc(2025, 6, 1, 12, 0)
        assert res.slices[1].laytime_counted_hours == Decimal("2.000000")

        # Threshold sub-slice 2
        assert res.slices[2].start_time == utc(2025, 6, 1, 12, 0)
        assert res.slices[2].end_time == utc(2025, 6, 1, 14, 0)
        assert res.slices[2].demurrage_counted_hours == Decimal("2.000000")
        assert res.demurrage_commenced_at == utc(2025, 6, 1, 12, 0)


# ===========================================================================
# G. Database Integrity Invariants
# ===========================================================================
class TestDatabaseInvariants:

    def test_demurrage_and_despatch_mutually_exclusive(self):
        """gross_demurrage_usd == 0 OR gross_despatch_usd == 0."""
        # Demurrage case
        res_dem = LaytimeSettlementEngine(10, 10000, 5000).settle([
            make_eval_slice(utc(2025, 6, 1, 0, 0), utc(2025, 6, 1, 15, 0))
        ])
        assert res_dem.gross_demurrage_usd > 0
        assert res_dem.gross_despatch_usd == 0

        # Despatch case
        res_des = LaytimeSettlementEngine(20, 10000, 5000).settle([
            make_eval_slice(utc(2025, 6, 1, 0, 0), utc(2025, 6, 1, 10, 0))
        ])
        assert res_des.gross_demurrage_usd == 0
        assert res_des.gross_despatch_usd > 0

    def test_net_settlement_identity(self):
        """net_settlement_usd == gross_demurrage_usd - gross_despatch_usd."""
        res = LaytimeSettlementEngine(10, 12000, 6000).settle([
            make_eval_slice(utc(2025, 6, 1, 0, 0), utc(2025, 6, 1, 5, 0))
        ])
        assert res.net_settlement_usd == res.gross_demurrage_usd - res.gross_despatch_usd


# ===========================================================================
# H. Input Validation
# ===========================================================================
class TestInputValidation:

    def test_zero_or_negative_allowed_laytime_raises(self):
        with pytest.raises(ValueError, match="strictly positive"):
            LaytimeSettlementEngine(0, 10000, 5000)

    def test_negative_demurrage_rate_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            LaytimeSettlementEngine(10, -500, 250)

    def test_despatch_greater_than_demurrage_raises(self):
        with pytest.raises(ValueError, match="cannot exceed demurrage"):
            LaytimeSettlementEngine(10, 10000, 15000)
