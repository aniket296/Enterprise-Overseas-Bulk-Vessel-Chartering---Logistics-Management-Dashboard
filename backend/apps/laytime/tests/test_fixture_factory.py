"""
backend/apps/laytime/tests/test_fixture_factory.py
============================================================
Cycle 1.3.1 — Unit Tests: MaritimeFixtureFactory

Test categories:
  A. Baltic standard fixture preset validity (Capesize, Panamax, Supramax)
  B. Mathematical allowed laytime agreement with AllowedLaytimeEngine
  C. Constraint enforcement (IMO, Flag State, Laycan Window, Despatch <= Demurrage)
  D. Serialization integrity (to_dict produces clean structure for DB seeds)
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import pytest

from apps.laytime.engine.fixture_factory import (
    MaritimeFixtureFactory,
    VesselFixture,
    CharterContractFixture,
    VoyageFixture,
    CompleteMaritimeFixture,
    VesselSizeClass,
    CargoType,
    CharterPartyType,
    ContractStatus,
    VoyageStatus,
)
from apps.laytime.engine.allowed_laytime import AllowedLaytimeEngine
from apps.laytime.engine.constants import LaytimeTerms


# ===========================================================================
# A. Baltic Standard Fixture Presets
# ===========================================================================
class TestBalticPresets:

    def test_capesize_australia_scenario(self):
        scenario = MaritimeFixtureFactory.capesize_australia_to_india_coal()
        assert scenario.vessel.size_class == VesselSizeClass.CAPESIZE
        assert scenario.contract.cargo_type == CargoType.COKING_COAL
        assert scenario.contract.discharge_port == "Paradip"
        assert scenario.expected_allowed_laytime_hours == Decimal("144.000000")

    def test_panamax_indonesia_scenario(self):
        scenario = MaritimeFixtureFactory.panamax_indonesia_to_india_thermal()
        assert scenario.vessel.size_class == VesselSizeClass.PANAMAX
        assert scenario.contract.cargo_type == CargoType.THERMAL_COAL
        assert scenario.contract.discharge_port == "Dhamra"
        assert scenario.expected_allowed_laytime_hours == Decimal("120.000000")

    def test_supramax_south_africa_scenario(self):
        scenario = MaritimeFixtureFactory.supramax_south_africa_to_india_metcoke()
        assert scenario.vessel.size_class == VesselSizeClass.SUPRAMAX
        assert scenario.contract.cargo_type == CargoType.MET_COKE
        assert scenario.contract.discharge_port == "Visakhapatnam"
        assert scenario.expected_allowed_laytime_hours == Decimal("144.000000")

    def test_get_all_scenarios_returns_distinct_fixtures(self):
        scenarios = MaritimeFixtureFactory.get_all_scenarios()
        assert len(scenarios) == 3
        imo_numbers = [s.vessel.imo_number for s in scenarios]
        assert len(set(imo_numbers)) == 3  # All unique IMO numbers


# ===========================================================================
# B. Mathematical Agreement with AllowedLaytimeEngine
# ===========================================================================
class TestMathematicalAgreement:

    @pytest.mark.parametrize("scenario_generator", [
        MaritimeFixtureFactory.capesize_australia_to_india_coal,
        MaritimeFixtureFactory.panamax_indonesia_to_india_thermal,
        MaritimeFixtureFactory.supramax_south_africa_to_india_metcoke,
    ])
    def test_fixture_allowed_laytime_matches_engine(self, scenario_generator):
        """
        Verify that each fixture's expected_allowed_laytime_hours strictly equals
        the output of AllowedLaytimeEngine.compute().
        """
        scenario = scenario_generator()
        engine = AllowedLaytimeEngine(
            cargo_mt_nominated=scenario.contract.nominated_quantity_mt,
            discharge_rate_mt_day=scenario.contract.discharge_rate_mt_per_day,
            tolerance_pct=scenario.contract.tolerance_pct,
            laytime_terms=scenario.contract.laytime_terms,
        )
        res = engine.compute()
        assert res.allowed_hours_nominated == scenario.expected_allowed_laytime_hours


# ===========================================================================
# C. Constraint Enforcement
# ===========================================================================
class TestConstraintEnforcement:

    def test_invalid_imo_number_raises(self):
        """IMO must be exactly 7 digits."""
        with pytest.raises(ValueError, match="must be exactly 7 digits"):
            VesselFixture(
                vessel_name="BAD IMO",
                imo_number="12345",  # Only 5 digits
                flag_state="IN",
                size_class=VesselSizeClass.CAPESIZE,
                deadweight_tonnage=Decimal("150000"),
                gross_tonnage=Decimal("80000"),
                net_tonnage=Decimal("50000"),
                length_overall_meters=Decimal("290"),
                beam_meters=Decimal("45"),
                draft_meters=Decimal("18"),
                hold_count=9,
                hatch_count=9,
                build_year=2020,
                owner_name="Test Owner",
            )

    def test_invalid_flag_state_raises(self):
        """Flag state must be 2 uppercase characters."""
        with pytest.raises(ValueError, match="2 uppercase characters"):
            VesselFixture(
                vessel_name="BAD FLAG",
                imo_number="9876543",
                flag_state="ind",  # Lowercase / 3 letters
                size_class=VesselSizeClass.PANAMAX,
                deadweight_tonnage=Decimal("75000"),
                gross_tonnage=Decimal("40000"),
                net_tonnage=Decimal("25000"),
                length_overall_meters=Decimal("225"),
                beam_meters=Decimal("32"),
                draft_meters=Decimal("14"),
                hold_count=7,
                hatch_count=7,
                build_year=2018,
                owner_name="Test Owner",
            )

    def test_hatch_count_greater_than_hold_count_raises(self):
        with pytest.raises(ValueError, match="cannot exceed hold_count"):
            VesselFixture(
                vessel_name="TOO MANY HATCHES",
                imo_number="9876543",
                flag_state="PA",
                size_class=VesselSizeClass.PANAMAX,
                deadweight_tonnage=Decimal("75000"),
                gross_tonnage=Decimal("40000"),
                net_tonnage=Decimal("25000"),
                length_overall_meters=Decimal("225"),
                beam_meters=Decimal("32"),
                draft_meters=Decimal("14"),
                hold_count=5,
                hatch_count=7,  # > 5
                build_year=2018,
                owner_name="Test Owner",
            )

    def test_laycan_window_exceeding_31_days_raises(self):
        """Laycan window max 31 days constraint (V003)."""
        with pytest.raises(ValueError, match="cannot exceed 31 days"):
            CharterContractFixture(
                contract_reference="TEST/001",
                charter_party_type=CharterPartyType.VOYAGE_CHARTER,
                contract_status=ContractStatus.ACTIVE,
                charterer_entity="SAIL",
                charterer_plant="Bokaro",
                cargo_type=CargoType.COKING_COAL,
                nominated_quantity_mt=Decimal("75000"),
                tolerance_pct=Decimal("5"),
                freight_rate_usd_per_mt=Decimal("15"),
                discharge_port="Paradip",
                discharge_rate_mt_per_day=Decimal("10000"),
                laytime_terms=LaytimeTerms.SHINC,
                demurrage_rate_usd_per_day=Decimal("15000"),
                despatch_rate_usd_per_day=Decimal("7500"),
                laycan_start=date(2025, 6, 1),
                laycan_end=date(2025, 7, 15),  # 44 days!
            )

    def test_despatch_rate_greater_than_demurrage_raises(self):
        """Despatch rate must be <= demurrage rate (V003)."""
        with pytest.raises(ValueError, match="cannot exceed demurrage"):
            CharterContractFixture(
                contract_reference="TEST/002",
                charter_party_type=CharterPartyType.VOYAGE_CHARTER,
                contract_status=ContractStatus.ACTIVE,
                charterer_entity="SAIL",
                charterer_plant="Bokaro",
                cargo_type=CargoType.COKING_COAL,
                nominated_quantity_mt=Decimal("75000"),
                tolerance_pct=Decimal("5"),
                freight_rate_usd_per_mt=Decimal("15"),
                discharge_port="Paradip",
                discharge_rate_mt_per_day=Decimal("10000"),
                laytime_terms=LaytimeTerms.SHINC,
                demurrage_rate_usd_per_day=Decimal("10000"),
                despatch_rate_usd_per_day=Decimal("15000"),  # > 10000
                laycan_start=date(2025, 6, 1),
                laycan_end=date(2025, 6, 10),
            )


# ===========================================================================
# D. Serialization Integrity
# ===========================================================================
class TestSerializationIntegrity:

    def test_to_dict_structure(self):
        scenario = MaritimeFixtureFactory.capesize_australia_to_india_coal()
        d = scenario.to_dict()
        assert d["scenario_id"] == "BALTIC_CAPE_AUS_IND_COAL_01"
        assert "vessel" in d
        assert "contract" in d
        assert "voyage" in d
        assert d["vessel"]["imo_number"] == "9456789"
        assert d["contract"]["contract_reference"] == "SAIL/CP/2025/CAPE-01"
        assert d["voyage"]["voyage_reference"] == "VOY-SAIL-2025-01"
