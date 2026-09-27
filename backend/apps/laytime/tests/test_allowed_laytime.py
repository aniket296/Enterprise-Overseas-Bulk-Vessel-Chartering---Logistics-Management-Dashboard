"""
backend/apps/laytime/tests/test_allowed_laytime.py
============================================================
Cycle 1.2.1 — Unit Tests: AllowedLaytimeEngine

Test categories:
  A. Standard formula correctness (known Baltic Exchange scenarios)
  B. All LaytimeTerms produce valid results (or correct indefinite flag)
  C. Tolerance band (min / max cargo scenarios)
  D. Decimal precision invariance (no rounding drift across iterations)
  E. Override allowed hours + verification method
  F. Input validation (LaytimeInputError on bad inputs)
  G. from_contract_dict constructor
  H. Edge cases (minimum cargo, round numbers, large Capesize cargoes)
  I. Arithmetic invariants (result consistency checks)
"""

import decimal
from decimal import Decimal

import pytest

from apps.laytime.engine.allowed_laytime import (
    AllowedLaytimeEngine,
    AllowedLaytimeResult,
    LaytimeInputError,
)
from apps.laytime.engine.constants import (
    LaytimeTerms,
    INDEFINITE_LAYTIME_TERMS,
    HOURS_PER_DAY,
)


# ===========================================================================
# Helpers
# ===========================================================================
def make_engine(
    cargo:     str | Decimal = "75000",
    rate:      str | Decimal = "8000",
    tol:       str | Decimal = "5",
    terms:     LaytimeTerms  = LaytimeTerms.SHINC,
    override:  str | None    = None,
) -> AllowedLaytimeEngine:
    return AllowedLaytimeEngine(
        cargo_mt_nominated    = Decimal(cargo),
        discharge_rate_mt_day = Decimal(rate),
        tolerance_pct         = Decimal(tol),
        laytime_terms         = terms,
        override_allowed_hours= Decimal(override) if override else None,
    )


# ===========================================================================
# A. Standard formula correctness
# ===========================================================================
class TestStandardFormula:

    def test_shinc_standard_capesize(self):
        """
        Capesize voyage: 75,000 MT coking coal at 8,000 MT/day SHINC.
        Expected: 75000/8000 × 24 = 9.375 × 24 = 225.000000 hours
        """
        result = make_engine("75000", "8000", "0", LaytimeTerms.SHINC).compute()
        assert result.allowed_hours_nominated == Decimal("225.000000")
        assert result.days_nominated == Decimal("9.375000")

    def test_wwdshex_panamax(self):
        """
        Panamax: 65,000 MT iron ore at 12,000 MT/WWD WWDSHEX.
        Expected: 65000/12000 × 24 = 5.41666... × 24 = 130.000000 hours
        """
        result = make_engine("65000", "12000", "0", LaytimeTerms.WWDSHEX).compute()
        assert result.allowed_hours_nominated == Decimal("130.000000")

    def test_shex_supramax(self):
        """
        Supramax: 55,000 MT thermal coal at 7,500 MT/day SHEX.
        Expected: 55000/7500 × 24 = 7.33333... × 24 = 176.000000 hours
        """
        result = make_engine("55000", "7500", "0", LaytimeTerms.SHEX).compute()
        # 55000/7500 = 7.333... days; × 24 = 176.000000
        expected = (Decimal("55000") / Decimal("7500") * Decimal("24")).quantize(
            Decimal("0.000001"), rounding=decimal.ROUND_HALF_UP
        )
        assert result.allowed_hours_nominated == expected

    def test_wwd_round_days(self):
        """
        Exact division: 48,000 MT at 8,000 MT/day = exactly 6 days = 144 hours.
        """
        result = make_engine("48000", "8000", "0", LaytimeTerms.WWD).compute()
        assert result.allowed_hours_nominated == Decimal("144.000000")
        assert result.days_nominated == Decimal("6.000000")

    def test_fhinc_gulf_cargo(self):
        """
        FHINC (Friday included): 30,000 MT manganese at 5,000 MT/day.
        Expected: 30000/5000 × 24 = 144.000000 hours.
        """
        result = make_engine("30000", "5000", "0", LaytimeTerms.FHINC).compute()
        assert result.allowed_hours_nominated == Decimal("144.000000")

    def test_shex_eiu_same_formula_as_shex(self):
        """
        SHEX_EIU uses the same allowance formula as SHEX; terms only affect counting.
        """
        r_shex   = make_engine("60000", "9000", "0", LaytimeTerms.SHEX).compute()
        r_shexeiu = make_engine("60000", "9000", "0", LaytimeTerms.SHEX_EIU).compute()
        assert r_shex.allowed_hours_nominated == r_shexeiu.allowed_hours_nominated

    def test_formula_string_contains_inputs(self):
        """Formula string must echo cargo, rate, and result hours."""
        result = make_engine("75000", "8000", "0", LaytimeTerms.SHINC).compute()
        assert "75000" in result.formula_string
        assert "8000"  in result.formula_string
        assert "225.000000" in result.formula_string

    def test_large_vloc_cargo(self):
        """
        VLOC: 180,000 MT iron ore at 45,000 MT/day.
        Expected: 180000/45000 × 24 = 4 × 24 = 96.000000 hours.
        """
        result = make_engine("180000", "45000", "0", LaytimeTerms.SHINC).compute()
        assert result.allowed_hours_nominated == Decimal("96.000000")
        assert result.days_nominated == Decimal("4.000000")


# ===========================================================================
# B. All LaytimeTerms — fixed vs indefinite
# ===========================================================================
class TestAllLaytimeTerms:

    @pytest.mark.parametrize("terms", [
        LaytimeTerms.SHINC,
        LaytimeTerms.SHEX,
        LaytimeTerms.SHEX_EIU,
        LaytimeTerms.FHINC,
        LaytimeTerms.FHEX,
        LaytimeTerms.WWD,
        LaytimeTerms.WWDSHEX,
    ])
    def test_fixed_terms_produce_hours(self, terms):
        result = make_engine("50000", "10000", "0", terms).compute()
        assert result.is_fixed_laytime is True
        assert result.allowed_hours_nominated is not None
        assert result.allowed_hours_nominated == Decimal("120.000000")

    @pytest.mark.parametrize("terms", [
        LaytimeTerms.CQD,
        LaytimeTerms.AS_FAST_AS_CAN,
    ])
    def test_indefinite_terms_return_none(self, terms):
        result = make_engine("50000", "10000", "0", terms).compute()
        assert result.is_fixed_laytime is False
        assert result.allowed_hours_nominated is None
        assert result.allowed_hours_min is None
        assert result.allowed_hours_max is None
        assert result.days_nominated is None
        assert "No fixed allowance" in result.formula_string

    def test_indefinite_terms_in_constants(self):
        assert LaytimeTerms.CQD in INDEFINITE_LAYTIME_TERMS
        assert LaytimeTerms.AS_FAST_AS_CAN in INDEFINITE_LAYTIME_TERMS


# ===========================================================================
# C. Tolerance band scenarios
# ===========================================================================
class TestToleranceBand:

    def test_five_pct_tolerance(self):
        """
        75,000 MT ±5% at 8,000 MT/day SHINC.
        Min qty = 75000 × 0.95 = 71250 MT → 71250/8000 × 24 = 213.750000 h
        Max qty = 75000 × 1.05 = 78750 MT → 78750/8000 × 24 = 236.250000 h
        Nominated = 225.000000 h
        """
        result = make_engine("75000", "8000", "5", LaytimeTerms.SHINC).compute()
        assert result.cargo_mt_min == Decimal("71250.000000")
        assert result.cargo_mt_max == Decimal("78750.000000")
        assert result.allowed_hours_min == Decimal("213.750000")
        assert result.allowed_hours_max == Decimal("236.250000")
        assert result.allowed_hours_nominated == Decimal("225.000000")

    def test_ten_pct_tolerance(self):
        """60,000 MT ±10%."""
        result = make_engine("60000", "6000", "10", LaytimeTerms.WWD).compute()
        assert result.cargo_mt_min == Decimal("54000.000000")
        assert result.cargo_mt_max == Decimal("66000.000000")
        # 54000/6000 × 24 = 216.000000; 66000/6000 × 24 = 264.000000
        assert result.allowed_hours_min == Decimal("216.000000")
        assert result.allowed_hours_max == Decimal("264.000000")

    def test_zero_tolerance(self):
        """Zero tolerance: min == nominated == max."""
        result = make_engine("75000", "8000", "0", LaytimeTerms.SHINC).compute()
        assert result.cargo_mt_min == result.cargo_mt_nominated
        assert result.cargo_mt_max == result.cargo_mt_nominated
        assert result.allowed_hours_min == result.allowed_hours_nominated
        assert result.allowed_hours_max == result.allowed_hours_nominated

    def test_tolerance_monotonicity(self):
        """hours_min <= hours_nominated <= hours_max always."""
        result = make_engine("75000", "8000", "7.5", LaytimeTerms.SHINC).compute()
        assert result.allowed_hours_min <= result.allowed_hours_nominated
        assert result.allowed_hours_nominated <= result.allowed_hours_max

    def test_tolerance_symmetry(self):
        """hours_nominated - hours_min == hours_max - hours_nominated."""
        result = make_engine("75000", "8000", "5", LaytimeTerms.SHINC).compute()
        delta_low  = result.allowed_hours_nominated - result.allowed_hours_min
        delta_high = result.allowed_hours_max - result.allowed_hours_nominated
        assert delta_low == delta_high


# ===========================================================================
# D. Decimal precision invariance
# ===========================================================================
class TestDecimalPrecision:

    def test_no_float_drift_repeated_computation(self):
        """
        Running the same computation 1000 times must produce bit-for-bit
        identical Decimal results — no accumulated rounding drift.
        """
        engine = make_engine("75432.567", "8123.456", "5", LaytimeTerms.SHINC)
        results = [engine.compute().allowed_hours_nominated for _ in range(1000)]
        assert len(set(results)) == 1, "Repeated computation produced different results!"

    def test_associativity_invariant(self):
        """
        (a/b × c) must equal ((a × c) / b) within output precision for
        the allowed laytime formula — tests Decimal associativity.
        """
        cargo = Decimal("75000")
        rate  = Decimal("8000")
        # Formula A: (cargo / rate) × 24
        a = (cargo / rate * Decimal("24")).quantize(Decimal("0.000001"), rounding=decimal.ROUND_HALF_UP)
        # Formula B: (cargo × 24) / rate
        b = (cargo * Decimal("24") / rate).quantize(Decimal("0.000001"), rounding=decimal.ROUND_HALF_UP)
        result = make_engine("75000", "8000", "0", LaytimeTerms.SHINC).compute()
        # Both should agree with engine output to 6 decimal places
        assert abs(result.allowed_hours_nominated - a) <= Decimal("0.000001")
        assert abs(result.allowed_hours_nominated - b) <= Decimal("0.000001")

    def test_output_always_6_decimal_places(self):
        """All Decimal outputs must be quantized to exactly 6 decimal places."""
        result = make_engine("73456.789", "7654.321", "3.75", LaytimeTerms.WWDSHEX).compute()
        for attr_name in (
            "allowed_hours_nominated", "allowed_hours_min",
            "allowed_hours_max", "days_nominated",
            "cargo_mt_min", "cargo_mt_max",
        ):
            val = getattr(result, attr_name)
            assert val is not None
            sign, digits, exponent = val.as_tuple()
            assert exponent == -6, (
                f"{attr_name} = {val} has exponent {exponent}, expected -6"
            )

    def test_precision_context_is_28_digits(self):
        result = make_engine("75000", "8000", "0", LaytimeTerms.SHINC).compute()
        assert result.precision_context_prec == 28

    def test_days_hours_relationship(self):
        """days_nominated must equal allowed_hours_nominated / 24 (to 6dp)."""
        result = make_engine("73456.789", "7654.321", "0", LaytimeTerms.SHINC).compute()
        expected_days = (result.allowed_hours_nominated / Decimal("24")).quantize(
            Decimal("0.000001"), rounding=decimal.ROUND_HALF_UP
        )
        assert result.days_nominated == expected_days


# ===========================================================================
# E. Override allowed hours + verification
# ===========================================================================
class TestOverrideAndVerification:

    def test_override_replaces_formula(self):
        """When override is given, allowed_hours_nominated uses override value."""
        result = make_engine("75000", "8000", "0", LaytimeTerms.SHINC, override="200").compute()
        assert result.allowed_hours_nominated == Decimal("200.000000")

    def test_verify_consistent(self):
        """Formula-derived = stated → is_consistent=True, deviation=0."""
        engine = make_engine("75000", "8000", "0", LaytimeTerms.SHINC)
        is_ok, deviation = engine.verify_formula_against_contract("225")
        assert is_ok is True
        assert deviation == Decimal("0.000000")

    def test_verify_small_rounding_within_epsilon(self):
        """Deviation of 0.0005 hours (< 0.001 threshold) is consistent."""
        engine = make_engine("75000", "8000", "0", LaytimeTerms.SHINC)
        is_ok, deviation = engine.verify_formula_against_contract("225.0005")
        assert is_ok is True

    def test_verify_inconsistent_detects_error(self):
        """A 1-hour deviation must be flagged as inconsistent."""
        engine = make_engine("75000", "8000", "0", LaytimeTerms.SHINC)
        # Contract states 224 hours but formula gives 225
        is_ok, deviation = engine.verify_formula_against_contract("224")
        assert is_ok is False
        assert deviation == Decimal("1.000000")

    def test_verify_cqd_always_consistent(self):
        """CQD has no formula to check — always returns consistent."""
        engine = make_engine("75000", "8000", "0", LaytimeTerms.CQD)
        is_ok, deviation = engine.verify_formula_against_contract("999")
        assert is_ok is True
        assert deviation == Decimal("0")


# ===========================================================================
# F. Input validation
# ===========================================================================
class TestInputValidation:

    def test_zero_cargo_raises(self):
        with pytest.raises(LaytimeInputError, match="cargo_mt_nominated"):
            make_engine("0", "8000")

    def test_negative_cargo_raises(self):
        with pytest.raises((LaytimeInputError, Exception)):
            make_engine("-1000", "8000")

    def test_zero_rate_raises(self):
        with pytest.raises(LaytimeInputError, match="discharge_rate_mt_day"):
            make_engine("75000", "0")

    def test_negative_rate_raises(self):
        with pytest.raises(LaytimeInputError, match="discharge_rate_mt_day"):
            make_engine("75000", "-500")

    def test_tolerance_too_high_raises(self):
        with pytest.raises(LaytimeInputError, match="tolerance_pct"):
            make_engine("75000", "8000", "16")

    def test_negative_tolerance_raises(self):
        with pytest.raises(LaytimeInputError, match="tolerance_pct"):
            make_engine("75000", "8000", "-1")

    def test_excessive_laytime_raises(self):
        """Very low discharge rate producing >365 days laytime must raise."""
        with pytest.raises(LaytimeInputError, match="safety ceiling"):
            # 75000 MT at 1 MT/day = 75000 days >> 8760 hours ceiling
            make_engine("75000", "1", "0").compute()

    def test_zero_override_raises(self):
        with pytest.raises(LaytimeInputError, match="override_allowed_hours"):
            make_engine("75000", "8000", override="0")

    def test_excessive_rate_raises(self):
        with pytest.raises(LaytimeInputError, match="maximum"):
            make_engine("75000", "999999", "0")

    def test_invalid_terms_raises(self):
        with pytest.raises(ValueError):
            AllowedLaytimeEngine(
                cargo_mt_nominated=Decimal("75000"),
                discharge_rate_mt_day=Decimal("8000"),
                laytime_terms="INVALID_TERM",  # type: ignore
            )


# ===========================================================================
# G. from_contract_dict constructor
# ===========================================================================
class TestFromContractDict:

    def test_basic_dict(self):
        contract = {
            "nominated_quantity_mt":   "75000",
            "discharge_rate_mt_per_day": "8000",
            "tolerance_pct":           "5",
            "laytime_terms":           "SHINC",
        }
        engine = AllowedLaytimeEngine.from_contract_dict(contract)
        result = engine.compute()
        assert result.allowed_hours_nominated == Decimal("225.000000")

    def test_dict_with_override(self):
        contract = {
            "nominated_quantity_mt":   "75000",
            "discharge_rate_mt_per_day": "8000",
            "tolerance_pct":           "0",
            "laytime_terms":           "WWD",
            "allowed_laytime_hours":   "210",
        }
        engine = AllowedLaytimeEngine.from_contract_dict(contract)
        result = engine.compute()
        assert result.allowed_hours_nominated == Decimal("210.000000")

    def test_dict_cqd_terms(self):
        contract = {
            "nominated_quantity_mt":   "50000",
            "discharge_rate_mt_per_day": "10000",
            "tolerance_pct":           "0",
            "laytime_terms":           "CQD",
        }
        engine = AllowedLaytimeEngine.from_contract_dict(contract)
        result = engine.compute()
        assert result.is_fixed_laytime is False


# ===========================================================================
# H. Edge cases
# ===========================================================================
class TestEdgeCases:

    def test_minimum_cargo_quantity(self):
        """0.001 MT (minimum allowed) should compute without error."""
        result = make_engine("0.001", "8000", "0", LaytimeTerms.SHINC).compute()
        assert result.allowed_hours_nominated is not None
        assert result.allowed_hours_nominated > Decimal("0")

    def test_exactly_one_day(self):
        """Cargo = rate produces exactly 24.000000 hours."""
        result = make_engine("10000", "10000", "0", LaytimeTerms.SHINC).compute()
        assert result.allowed_hours_nominated == Decimal("24.000000")
        assert result.days_nominated == Decimal("1.000000")

    def test_capesize_standard_fixture(self):
        """
        Baltic Exchange reference scenario:
        Capesize: 150,000 MT iron ore at 35,000 MT/day SHEX ±10%.
        Nominated: 150000/35000 × 24 = 102.857142... hours
        """
        result = make_engine("150000", "35000", "10", LaytimeTerms.SHEX).compute()
        assert result.allowed_hours_nominated is not None
        # Verify formula: 150000/35000 × 24
        expected = (Decimal("150000") / Decimal("35000") * Decimal("24")).quantize(
            Decimal("0.000001"), rounding=decimal.ROUND_HALF_UP
        )
        assert result.allowed_hours_nominated == expected

    def test_fractional_rate_precision(self):
        """Discharge rate with many decimal places must not lose precision."""
        result = make_engine("75000", "7777.777", "0", LaytimeTerms.SHINC).compute()
        # Manual: 75000/7777.777 × 24
        expected = (Decimal("75000") / Decimal("7777.777") * Decimal("24")).quantize(
            Decimal("0.000001"), rounding=decimal.ROUND_HALF_UP
        )
        assert result.allowed_hours_nominated == expected

    def test_result_is_frozen(self):
        """AllowedLaytimeResult must be immutable (frozen dataclass)."""
        result = make_engine("75000", "8000", "0", LaytimeTerms.SHINC).compute()
        with pytest.raises((AttributeError, TypeError)):
            result.allowed_hours_nominated = Decimal("999")  # type: ignore

    def test_result_str_contains_hours(self):
        result = make_engine("75000", "8000", "0", LaytimeTerms.SHINC).compute()
        s = str(result)
        assert "225.000000" in s
        assert "SHINC" in s


# ===========================================================================
# I. Arithmetic invariants
# ===========================================================================
class TestArithmeticInvariants:

    def test_hours_equals_days_times_24(self):
        """
        allowed_hours_nominated ≈ days_nominated × 24 to within 1 unit at 6dp.
        Note: days_nominated is independently quantized to 6dp, so reconstructing
        hours from it may accumulate one extra rounding step — we allow ≤ 0.000012
        (half-ULP at 6dp × 24) tolerance.
        """
        result = make_engine("73000", "9500", "0", LaytimeTerms.SHINC).compute()
        reconstructed = (result.days_nominated * Decimal("24")).quantize(
            Decimal("0.000001"), rounding=decimal.ROUND_HALF_UP
        )
        assert abs(result.allowed_hours_nominated - reconstructed) <= Decimal("0.000012")

    def test_min_lt_nominated_lt_max_for_nonzero_tolerance(self):
        result = make_engine("75000", "8000", "5", LaytimeTerms.SHINC).compute()
        assert result.allowed_hours_min < result.allowed_hours_nominated
        assert result.allowed_hours_nominated < result.allowed_hours_max

    def test_cargo_mt_min_max_consistency(self):
        result = make_engine("75000", "8000", "5", LaytimeTerms.SHINC).compute()
        # min+max should average to nominated (within 1 unit at 6dp)
        avg_cargo = (result.cargo_mt_min + result.cargo_mt_max) / Decimal("2")
        avg_cargo = avg_cargo.quantize(Decimal("0.000001"), rounding=decimal.ROUND_HALF_UP)
        assert avg_cargo == result.cargo_mt_nominated

    def test_rate_doubling_halves_laytime(self):
        """Doubling discharge rate should halve allowed laytime."""
        r1 = make_engine("75000", "8000",  "0", LaytimeTerms.SHINC).compute()
        r2 = make_engine("75000", "16000", "0", LaytimeTerms.SHINC).compute()
        assert r1.allowed_hours_nominated == r2.allowed_hours_nominated * 2

    def test_cargo_doubling_doubles_laytime(self):
        """Doubling cargo quantity should double allowed laytime."""
        r1 = make_engine("75000",  "8000", "0", LaytimeTerms.SHINC).compute()
        r2 = make_engine("150000", "8000", "0", LaytimeTerms.SHINC).compute()
        assert r2.allowed_hours_nominated == r1.allowed_hours_nominated * 2
