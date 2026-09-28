"""
backend/apps/inventory/tests/test_starvation_simulator.py
============================================================
Cycle 2.5.5 — Tests: Multi-Plant Stock Starvation Early Warning & Emergency Re-chartering Simulator

Comprehensive test suite verifying:
  1. Multi-Plant Starvation Scanner across all 5 SAIL steel plants (BSP, DSP, RSP, BSL, ISP).
  2. Severity classifications (CRITICAL_URGENT, CRITICAL, PRECAUTIONARY, WATCH, ADEQUATE, SURPLUS).
  3. Physical vs extended runway calculations with in-transit vessels.
  4. In-transit vessel diversion simulation (donor safety, port shifting cost, feasibility scoring).
  5. Emergency spot chartering parcel sizing (Supramax, Panamax, Capesize) and emergency prompt surcharges.
  6. Inter-plant rail transfer calculation (BOXN rakes, freight, and transit hours).
  7. Consolidated primary strategy determination (DIVERSION, INTERPLANT_RAIL, SPOT_RECHARTER, ADEQUATE_NO_ACTION).
  8. REST API endpoints:
     - GET /api/v1/inventory/starvation-matrix/
     - GET /api/v1/inventory/simulate-mitigation/
  9. Input validation and RBAC permissions across all domain roles.
"""

import datetime
from decimal import Decimal
import pytest
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.inventory.models import PlantInventory, RawMaterialCategory, StockSafetyStatus
from apps.inventory.starvation_simulator import (
    scan_multi_plant_starvation,
    simulate_starvation_mitigation,
    StarvationSimulatorError,
    PlantStarvationProfile,
    DiversionOption,
    EmergencyRecharterOption,
    EmergencyInterPlantTransferOption,
    StarvationMitigationSimulation,
)
from apps.contracts.models import Vessel, CharterContract
from apps.voyages.models import VesselVoyage

UTC = datetime.timezone.utc


def _utc(year, month, day, hour=0, minute=0) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, minute, 0, tzinfo=UTC)


@pytest.fixture
def api_client():
    return APIClient()


def _make_user(db, role, suffix="01"):
    from django.contrib.auth import get_user_model
    from apps.authentication.models import UserProfile
    User = get_user_model()
    user = User.objects.create_user(
        username=f"starv_user_{role.lower()}_{suffix}",
        email=f"starv_{role.lower()}_{suffix}@test.com",
        password="TestPassword123!",
    )
    UserProfile.objects.create(
        user=user,
        role=role,
        organization="Rourkela Steel Plant",
    )
    return user


@pytest.fixture
def plant_manager_user(db):
    from apps.authentication.roles import UserRole
    return _make_user(db, UserRole.PLANT_MANAGER.value, "pm")


@pytest.fixture
def logistics_user(db):
    from apps.authentication.roles import UserRole
    return _make_user(db, UserRole.LOGISTICS_AGENT.value, "log")


@pytest.fixture
def auditor_user(db):
    from apps.authentication.roles import UserRole
    return _make_user(db, UserRole.AUDITOR.value, "aud")


@pytest.fixture
def base_vessel(db):
    return Vessel.objects.create(
        vessel_name="MV Steel Carrier",
        imo_number="9123456",
        flag_state="IN",
        build_year=2019,
        deadweight_tonnage=Decimal("82000.000"),
        gross_tonnage=Decimal("43000.000"),
        net_tonnage=Decimal("25000.000"),
        length_overall_meters=Decimal("229.00"),
        beam_meters=Decimal("32.26"),
        draft_meters=Decimal("14.50"),
        hold_count=7,
        hatch_count=7,
        owner_name="Shipping Corporation of India",
    )


# ===========================================================================
# 1. Multi-Plant Starvation Scanner Tests
# ===========================================================================

@pytest.mark.django_db
class TestStarvationScanner:

    def test_scan_all_5_sail_plants_present(self, db):
        """Scanner reports status for all 5 SAIL plants sorted by runway."""
        profiles = scan_multi_plant_starvation("COKING_COAL")
        assert len(profiles) == 5
        codes = {p.plant_code for p in profiles}
        assert codes == {"BSP", "DSP", "RSP", "BSL", "ISP"}

    def test_critical_urgent_severity_detection(self, db):
        """Physical runway < 5 days triggers CRITICAL_URGENT severity."""
        today = datetime.date.today()
        # 12,000 MT / 4,000 MT/day = 3.00 days
        PlantInventory.objects.create(
            plant_code="RSP",
            plant_name="Rourkela Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("12000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("4000.000"),
        )
        profiles = scan_multi_plant_starvation("COKING_COAL")
        rsp = next(p for p in profiles if p.plant_code == "RSP")

        assert rsp.physical_runway_days == Decimal("3.00")
        assert rsp.risk_severity == "CRITICAL_URGENT"
        assert rsp.is_starvation_risk is True

    def test_critical_severity_detection(self, db):
        """Physical runway between 5 and 7 days triggers CRITICAL severity."""
        today = datetime.date.today()
        # 24,000 MT / 4,000 MT/day = 6.00 days
        PlantInventory.objects.create(
            plant_code="BSP",
            plant_name="Bhilai Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("24000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("4000.000"),
        )
        profiles = scan_multi_plant_starvation("COKING_COAL")
        bsp = next(p for p in profiles if p.plant_code == "BSP")

        assert bsp.physical_runway_days == Decimal("6.00")
        assert bsp.risk_severity == "CRITICAL"
        assert bsp.is_starvation_risk is True

    def test_precautionary_severity_detection(self, db):
        """Physical runway between 7 and 14 days triggers PRECAUTIONARY severity."""
        today = datetime.date.today()
        # 40,000 MT / 4,000 MT/day = 10.00 days
        PlantInventory.objects.create(
            plant_code="BSL",
            plant_name="Bokaro Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("40000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("4000.000"),
        )
        profiles = scan_multi_plant_starvation("COKING_COAL")
        bsl = next(p for p in profiles if p.plant_code == "BSL")

        assert bsl.physical_runway_days == Decimal("10.00")
        assert bsl.risk_severity == "PRECAUTIONARY"
        assert bsl.is_starvation_risk is False

    def test_surplus_and_adequate_detection(self, db):
        """Physical runway > 45 days triggers SURPLUS, 21-45 days triggers ADEQUATE."""
        today = datetime.date.today()
        # DSP: 200,000 MT / 4,000 MT/day = 50.00 days -> SURPLUS
        PlantInventory.objects.create(
            plant_code="DSP",
            plant_name="Durgapur Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("200000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("4000.000"),
        )
        # ISP: 100,000 MT / 4,000 MT/day = 25.00 days -> ADEQUATE
        PlantInventory.objects.create(
            plant_code="ISP",
            plant_name="IISCO Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("100000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("4000.000"),
        )
        profiles = scan_multi_plant_starvation("COKING_COAL")
        dsp = next(p for p in profiles if p.plant_code == "DSP")
        isp = next(p for p in profiles if p.plant_code == "ISP")

        assert dsp.risk_severity == "SURPLUS"
        assert isp.risk_severity == "ADEQUATE"

    def test_target_safe_stock_and_deficit_calculation(self, db):
        """Computes safe 21-day target stock and remaining deficit."""
        today = datetime.date.today()
        # Burn rate = 5,000 MT/day -> Safe 21-day stock = 105,000 MT
        # Current stock = 25,000 MT -> Deficit = 80,000 MT
        PlantInventory.objects.create(
            plant_code="RSP",
            plant_name="Rourkela Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("25000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("5000.000"),
        )
        profiles = scan_multi_plant_starvation("COKING_COAL", target_safe_days=Decimal("21.00"))
        rsp = next(p for p in profiles if p.plant_code == "RSP")

        assert rsp.target_safe_stock_mt == Decimal("105000.000")
        assert rsp.deficit_to_safe_stock_mt == Decimal("80000.000")

    def test_incoming_voyages_included_in_extended_runway(self, db, base_vessel):
        """Active incoming voyages extend the plant runway."""
        today = datetime.date.today()
        PlantInventory.objects.create(
            plant_code="BSL",
            plant_name="Bokaro Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("20000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("4000.000"),
        )
        contract = CharterContract.objects.create(
            contract_reference="SAIL/CP/2026/BSL/01",
            vessel=base_vessel,
            charterer_entity="STEEL_AUTHORITY_OF_INDIA",
            charterer_plant="BOKARO",
            cargo_type="COKING_COAL",
            charter_party_type="VOYAGE_CHARTER",
            contract_status="ACTIVE",
            laycan_start=today,
            laycan_end=today + datetime.timedelta(days=5),
            load_port="Gladstone",
            discharge_port="Haldia",
            nominated_quantity_mt=Decimal("60000.000"),
            laytime_terms="SHINC",
            discharge_rate_mt_per_day=Decimal("15000.000"),
            turn_time_hours=Decimal("6.00"),
            demurrage_rate_usd_per_day=Decimal("25000.00"),
            despatch_rate_usd_per_day=Decimal("12500.00"),
            freight_rate_usd_per_mt=Decimal("18.00"),
            allowed_laytime_hours=Decimal("120.000000"),
        )
        VesselVoyage.objects.create(
            voyage_reference="VOY-BSL-01",
            contract=contract,
            vessel=base_vessel,
            voyage_status="IN_TRANSIT",
            load_port="Gladstone",
            discharge_port="Haldia",
            quantity_loaded_mt=Decimal("60000.000"),
            eta_date=_utc(today.year, today.month, today.day) + datetime.timedelta(days=3),
        )

        profiles = scan_multi_plant_starvation("COKING_COAL")
        bsl = next(p for p in profiles if p.plant_code == "BSL")

        assert bsl.physical_runway_days == Decimal("5.00")
        assert bsl.incoming_tonnage_mt == Decimal("60000.000")
        assert bsl.extended_runway_days == Decimal("20.00")  # (20,000 + 60,000) / 4,000 = 20.00


# ===========================================================================
# 2. Cargo Diversion Simulator Tests
# ===========================================================================

@pytest.mark.django_db
class TestVesselDiversionSimulation:

    def test_feasible_diversion_from_surplus_donor(self, db, base_vessel):
        """Simulates successful vessel diversion when donor maintains safe buffer."""
        today = datetime.date.today()
        # Starving recipient: RSP (Rourkela) with 3 days runway (12,000 MT, burn 4,000 MT/d)
        PlantInventory.objects.create(
            plant_code="RSP",
            plant_name="Rourkela Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("12000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("4000.000"),
        )
        # Surplus donor: BSL (Bokaro) with 120,000 MT stock (burn 4,000 MT/d -> 30 days)
        PlantInventory.objects.create(
            plant_code="BSL",
            plant_name="Bokaro Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("120000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("4000.000"),
        )
        # In-transit vessel headed to BSL (Haldia) with 70,000 MT
        contract = CharterContract.objects.create(
            contract_reference="SAIL/CP/BSL/DIV01",
            vessel=base_vessel,
            charterer_entity="STEEL_AUTHORITY_OF_INDIA",
            charterer_plant="BOKARO",
            cargo_type="COKING_COAL",
            charter_party_type="VOYAGE_CHARTER",
            contract_status="ACTIVE",
            laycan_start=today,
            laycan_end=today + datetime.timedelta(days=5),
            load_port="Gladstone",
            discharge_port="Haldia",
            nominated_quantity_mt=Decimal("70000.000"),
            laytime_terms="SHINC",
            discharge_rate_mt_per_day=Decimal("15000.000"),
            turn_time_hours=Decimal("6.00"),
            demurrage_rate_usd_per_day=Decimal("25000.00"),
            despatch_rate_usd_per_day=Decimal("12500.00"),
            freight_rate_usd_per_mt=Decimal("18.00"),
            allowed_laytime_hours=Decimal("120.000000"),
        )
        VesselVoyage.objects.create(
            voyage_reference="VOY-DIV-01",
            contract=contract,
            vessel=base_vessel,
            voyage_status="IN_TRANSIT",
            load_port="Gladstone",
            discharge_port="Haldia",
            quantity_loaded_mt=Decimal("70000.000"),
            eta_date=_utc(today.year, today.month, today.day) + datetime.timedelta(days=2),
        )

        sim = simulate_starvation_mitigation("RSP", "COKING_COAL")

        assert len(sim.diversion_options) >= 1
        opt = sim.diversion_options[0]
        assert opt.voyage_reference == "VOY-DIV-01"
        assert opt.is_donor_safe is True
        assert opt.donor_post_diversion_runway_days >= Decimal("14.00")
        assert opt.runway_days_gained == Decimal("17.50")  # 70,000 / 4,000 = 17.50d
        assert opt.recipient_post_diversion_runway_days == Decimal("20.50") # (12,000 + 70,000) / 4,000 = 20.50d
        assert opt.feasibility_score >= 70
        assert sim.primary_strategy == "DIVERSION"

    def test_unsafe_donor_flagged_as_high_risk(self, db, base_vessel):
        """If diverting the vessel drops donor below safe runway, option is flagged HIGH_RISK."""
        today = datetime.date.today()
        # Starving recipient: RSP (10,000 MT, burn 4,000 -> 2.5d)
        PlantInventory.objects.create(
            plant_code="RSP",
            plant_name="Rourkela Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("10000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("4000.000"),
        )
        # Vulnerable donor: BSL (only 20,000 MT, burn 4,000 -> 5d physical)
        PlantInventory.objects.create(
            plant_code="BSL",
            plant_name="Bokaro Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("20000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("4000.000"),
        )
        contract = CharterContract.objects.create(
            contract_reference="SAIL/CP/BSL/DIV02",
            vessel=base_vessel,
            charterer_entity="STEEL_AUTHORITY_OF_INDIA",
            charterer_plant="BOKARO",
            cargo_type="COKING_COAL",
            charter_party_type="VOYAGE_CHARTER",
            contract_status="ACTIVE",
            laycan_start=today,
            laycan_end=today + datetime.timedelta(days=5),
            load_port="Gladstone",
            discharge_port="Haldia",
            nominated_quantity_mt=Decimal("50000.000"),
            laytime_terms="SHINC",
            discharge_rate_mt_per_day=Decimal("15000.000"),
            turn_time_hours=Decimal("6.00"),
            demurrage_rate_usd_per_day=Decimal("25000.00"),
            despatch_rate_usd_per_day=Decimal("12500.00"),
            freight_rate_usd_per_mt=Decimal("18.00"),
            allowed_laytime_hours=Decimal("120.000000"),
        )
        VesselVoyage.objects.create(
            voyage_reference="VOY-DIV-02",
            contract=contract,
            vessel=base_vessel,
            voyage_status="IN_TRANSIT",
            load_port="Gladstone",
            discharge_port="Haldia",
            quantity_loaded_mt=Decimal("50000.000"),
            eta_date=_utc(today.year, today.month, today.day) + datetime.timedelta(days=2),
        )

        sim = simulate_starvation_mitigation("RSP", "COKING_COAL")
        opt = next(o for o in sim.diversion_options if o.voyage_reference == "VOY-DIV-02")
        assert opt.is_donor_safe is False
        assert "HIGH_RISK" in opt.recommendation

    def test_same_discharge_port_diversion_fee(self, db, base_vessel):
        """Vessels already discharging at recipient's preferred port incur only $5,000 reallocation fee."""
        today = datetime.date.today()
        PlantInventory.objects.create(
            plant_code="RSP",
            plant_name="Rourkela Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("10000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("4000.000"),
        )
        PlantInventory.objects.create(
            plant_code="BSP",
            plant_name="Bhilai Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("150000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("4000.000"),
        )
        # Vessel destined for Bhilai but discharging at PARADIP (same port as RSP)
        contract = CharterContract.objects.create(
            contract_reference="SAIL/CP/SAME/01",
            vessel=base_vessel,
            charterer_entity="STEEL_AUTHORITY_OF_INDIA",
            charterer_plant="BHILAI",
            cargo_type="COKING_COAL",
            charter_party_type="VOYAGE_CHARTER",
            contract_status="ACTIVE",
            laycan_start=today,
            laycan_end=today + datetime.timedelta(days=5),
            load_port="Gladstone",
            discharge_port="PARADIP",
            nominated_quantity_mt=Decimal("60000.000"),
            laytime_terms="SHINC",
            discharge_rate_mt_per_day=Decimal("15000.000"),
            turn_time_hours=Decimal("6.00"),
            demurrage_rate_usd_per_day=Decimal("25000.00"),
            despatch_rate_usd_per_day=Decimal("12500.00"),
            freight_rate_usd_per_mt=Decimal("18.00"),
            allowed_laytime_hours=Decimal("120.000000"),
        )
        VesselVoyage.objects.create(
            voyage_reference="VOY-SAME-01",
            contract=contract,
            vessel=base_vessel,
            voyage_status="IN_TRANSIT",
            load_port="Gladstone",
            discharge_port="PARADIP",
            quantity_loaded_mt=Decimal("60000.000"),
            eta_date=_utc(today.year, today.month, today.day) + datetime.timedelta(days=2),
        )

        sim = simulate_starvation_mitigation("RSP", "COKING_COAL")
        opt = next(o for o in sim.diversion_options if o.voyage_reference == "VOY-SAME-01")
        assert opt.estimated_diversion_cost_usd == Decimal("5000.00")


# ===========================================================================
# 3. Inter-Plant Rail Transfer Tests
# ===========================================================================

@pytest.mark.django_db
class TestEmergencyInterPlantRailTransfer:

    def test_interplant_rail_transfer_allocation(self, db):
        """Simulates Indian Railways BOXN rake allocation from surplus plant."""
        today = datetime.date.today()
        # Starving recipient: RSP (Rourkela) - needs 80,000 MT
        PlantInventory.objects.create(
            plant_code="RSP",
            plant_name="Rourkela Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("20000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("5000.000"),  # 4 days runway
        )
        # Surplus donor: BSL (Bokaro) has 180,000 MT (safe target = 105,000 MT, surplus = 75,000 MT)
        PlantInventory.objects.create(
            plant_code="BSL",
            plant_name="Bokaro Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("180000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("5000.000"),
        )

        sim = simulate_starvation_mitigation("RSP", "COKING_COAL")

        assert len(sim.rail_transfer_options) >= 1
        rail_opt = sim.rail_transfer_options[0]
        assert rail_opt.donor_plant_code == "BSL"
        assert rail_opt.is_transfer_viable is True
        assert rail_opt.rakes_required >= 1
        assert rail_opt.total_transfer_tonnage_mt == (Decimal(str(rail_opt.rakes_required)) * Decimal("3800.000")).quantize(Decimal("0.001"))
        assert rail_opt.estimated_transit_hours == 18  # BSL <-> RSP is ~18 hours


# ===========================================================================
# 4. Emergency Spot Re-chartering Tests
# ===========================================================================

@pytest.mark.django_db
class TestEmergencySpotRechartering:

    def test_recharter_supramax_for_small_deficit(self, db):
        """Deficit <= 60,000 MT recommends SUPRAMAX vessel (55,000 MT)."""
        today = datetime.date.today()
        # Deficit = 105,000 - 60,000 = 45,000 MT
        PlantInventory.objects.create(
            plant_code="ISP",
            plant_name="IISCO Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("60000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("5000.000"),
        )
        sim = simulate_starvation_mitigation("ISP", "COKING_COAL")
        spot = sim.spot_recharter_option

        assert spot is not None
        assert spot.recommended_vessel_class == "SUPRAMAX"
        assert spot.parcel_size_mt == Decimal("55000.000")
        assert spot.estimated_spot_freight_usd_per_mt == Decimal("24.50")
        assert spot.total_estimated_freight_usd == Decimal("1347500.00")

    def test_recharter_panamax_for_medium_deficit(self, db):
        """Deficit between 60,000 and 95,000 MT recommends PANAMAX (75,000 MT)."""
        today = datetime.date.today()
        # Deficit = 105,000 - 25,000 = 80,000 MT
        PlantInventory.objects.create(
            plant_code="RSP",
            plant_name="Rourkela Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("25000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("5000.000"),
        )
        sim = simulate_starvation_mitigation("RSP", "COKING_COAL")
        spot = sim.spot_recharter_option

        assert spot is not None
        assert spot.recommended_vessel_class == "PANAMAX"
        assert spot.parcel_size_mt == Decimal("75000.000")

    def test_recharter_capesize_for_large_deficit(self, db):
        """Deficit > 95,000 MT recommends CAPESIZE (150,000 MT)."""
        today = datetime.date.today()
        # Deficit = 147,000 - 10,000 = 137,000 MT (at 7,000 MT/d burn)
        PlantInventory.objects.create(
            plant_code="BSP",
            plant_name="Bhilai Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("10000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("7000.000"),
        )
        sim = simulate_starvation_mitigation("BSP", "COKING_COAL")
        spot = sim.spot_recharter_option

        assert spot is not None
        assert spot.recommended_vessel_class == "CAPESIZE"
        assert spot.parcel_size_mt == Decimal("150000.000")

    def test_emergency_prompt_fixture_surcharge(self, db):
        """Physical runway < 5 days adds +$3.00/MT prompt fixture premium."""
        today = datetime.date.today()
        # 12,000 MT / 4,000 MT/d = 3.00 days (< 5 days!)
        PlantInventory.objects.create(
            plant_code="RSP",
            plant_name="Rourkela Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("12000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("4000.000"),
        )
        sim = simulate_starvation_mitigation("RSP", "COKING_COAL")
        spot = sim.spot_recharter_option

        # Panamax base rate is $21.00 + $3.00 emergency surcharge = $24.00
        assert spot.estimated_spot_freight_usd_per_mt == Decimal("24.00")
        assert spot.laycan_urgency_days == 3


# ===========================================================================
# 5. Consolidated Strategy & Error Handling Tests
# ===========================================================================

@pytest.mark.django_db
class TestConsolidatedStrategyAndErrors:

    def test_adequate_plant_no_emergency_action_needed(self, db):
        """Healthy plant returns ADEQUATE_NO_ACTION strategy."""
        today = datetime.date.today()
        PlantInventory.objects.create(
            plant_code="BSL",
            plant_name="Bokaro Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("150000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("5000.000"),  # 30 days runway
        )
        sim = simulate_starvation_mitigation("BSL", "COKING_COAL")
        assert sim.primary_strategy == "ADEQUATE_NO_ACTION"
        assert "maintains healthy stock runway" in sim.summary_recommendation

    def test_unknown_plant_raises_error(self, db):
        """Unknown plant code raises StarvationSimulatorError."""
        with pytest.raises(StarvationSimulatorError) as exc_info:
            simulate_starvation_mitigation("MYSORE_STEEL", "COKING_COAL")
        assert exc_info.value.code == "UNKNOWN_PLANT"

    def test_plant_alias_resolution(self, db):
        """'ROURKELA STEEL PLANT' resolves to RSP."""
        sim = simulate_starvation_mitigation("ROURKELA STEEL PLANT", "COKING_COAL")
        assert sim.target_plant_code == "RSP"
        assert sim.target_plant_name == "Rourkela Steel Plant"


# ===========================================================================
# 6. REST API Integration Tests
# ===========================================================================

@pytest.mark.django_db
class TestStarvationRESTAPI:

    def test_unauthenticated_request_rejected(self, api_client, settings):
        """Unauthenticated requests must be rejected when auth is enforced."""
        settings.RBAC_REQUIRE_AUTHENTICATION = True
        resp = api_client.get("/api/v1/inventory/starvation-matrix/")
        assert resp.status_code in (drf_status.HTTP_401_UNAUTHORIZED, drf_status.HTTP_403_FORBIDDEN)

    def test_starvation_matrix_success(self, api_client, plant_manager_user):
        """GET /api/v1/inventory/starvation-matrix/ returns 200 with list of 5 plants."""
        api_client.force_authenticate(user=plant_manager_user)
        resp = api_client.get("/api/v1/inventory/starvation-matrix/")
        assert resp.status_code == drf_status.HTTP_200_OK

        data = resp.data
        assert isinstance(data, list)
        assert len(data) == 5
        first = data[0]
        assert "plant_code" in first
        assert "physical_runway_days" in first
        assert "risk_severity" in first
        assert "deficit_to_safe_stock_mt" in first

    def test_starvation_matrix_invalid_target_days(self, api_client, plant_manager_user):
        """Target safe days < 1 or > 60 returns 400 Bad Request."""
        api_client.force_authenticate(user=plant_manager_user)
        resp = api_client.get("/api/v1/inventory/starvation-matrix/?target_safe_days=99")
        assert resp.status_code == drf_status.HTTP_400_BAD_REQUEST
        assert "between 1 and 60 days" in resp.data["error"]

    def test_simulate_mitigation_missing_plant_code(self, api_client, plant_manager_user):
        """GET /api/v1/inventory/simulate-mitigation/ without plant_code returns 400."""
        api_client.force_authenticate(user=plant_manager_user)
        resp = api_client.get("/api/v1/inventory/simulate-mitigation/")
        assert resp.status_code == drf_status.HTTP_400_BAD_REQUEST
        assert "plant_code" in resp.data["error"]

    def test_simulate_mitigation_unknown_plant_code(self, api_client, plant_manager_user):
        """Unknown plant_code in simulation returns 400 Bad Request."""
        api_client.force_authenticate(user=plant_manager_user)
        resp = api_client.get("/api/v1/inventory/simulate-mitigation/?plant_code=INVALID_PLANT")
        assert resp.status_code == drf_status.HTTP_400_BAD_REQUEST
        assert "UNKNOWN_PLANT" in resp.data["error"]

    def test_simulate_mitigation_success_schema(self, api_client, plant_manager_user):
        """GET /api/v1/inventory/simulate-mitigation/?plant_code=RSP returns complete schema."""
        api_client.force_authenticate(user=plant_manager_user)
        resp = api_client.get("/api/v1/inventory/simulate-mitigation/?plant_code=RSP")
        assert resp.status_code == drf_status.HTTP_200_OK

        data = resp.data
        assert data["target_plant_code"] == "RSP"
        assert data["target_plant_name"] == "Rourkela Steel Plant"
        assert "starvation_profile" in data
        assert "primary_strategy" in data
        assert "summary_recommendation" in data
        assert "diversion_options" in data
        assert "rail_transfer_options" in data
        assert "all_plants_matrix" in data
        assert "simulated_at" in data

    def test_rbac_access_across_domain_roles(self, api_client, plant_manager_user, logistics_user, auditor_user):
        """All operational roles can access starvation matrix and simulation."""
        for user in [plant_manager_user, logistics_user, auditor_user]:
            api_client.force_authenticate(user=user)
            resp = api_client.get("/api/v1/inventory/starvation-matrix/")
            assert resp.status_code == drf_status.HTTP_200_OK, f"Failed for {user.username}"

            resp_sim = api_client.get("/api/v1/inventory/simulate-mitigation/?plant_code=BSP")
            assert resp_sim.status_code == drf_status.HTTP_200_OK, f"Failed sim for {user.username}"
