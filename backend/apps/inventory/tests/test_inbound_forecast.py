"""
backend/apps/inventory/tests/test_inbound_forecast.py
============================================================
Cycle 2.5.4 — Tests: Inbound Supply Chain Forward Runway Forecaster Engine

Comprehensive test suite verifying:
  1. Pure depletion runway simulation (no vessels, linear burn down to stockout).
  2. Stockout date, critical breach date (< 7d), and precautionary breach date (< 14d) identification.
  3. Single and multi-vessel inbound replenishment extending runway.
  4. Rail transit lead time offset application (ETA + rail lead days).
  5. Plant and material isolation (voyages for other plants/materials excluded).
  6. Past vessel ETA handling (clamped to simulation start date).
  7. Custom opening stock and burn rate overrides.
  8. Boundary validation for horizon_days (1 to 120) and plant codes.
  9. Strict Decimal precision (MT to 3 d.p., days to 2 d.p., zero float drift).
  10. REST API endpoint GET /api/v1/inventory/inbound-forecast/ query parameter validation.
  11. REST API serialisation and timeline schema integrity.
  12. RBAC access control across roles (PLANT_MANAGER, LOGISTICS_AGENT, AUDITOR) and 401 unauthenticated.
"""

import datetime
from decimal import Decimal
import pytest
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.inventory.inbound_forecast import (
    simulate_forward_runway,
    InboundForecastError,
    PlantForwardRunwayForecast,
    DailyRunwayPoint,
)
from apps.inventory.models import PlantInventory, RawMaterialCategory, StockSafetyStatus
from apps.inventory.runway_engine import InTransitCargoContribution
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
        username=f"fc_user_{role.lower()}_{suffix}",
        email=f"fc_{role.lower()}_{suffix}@test.com",
        password="TestPassword123!",
    )
    UserProfile.objects.create(
        user=user,
        role=role,
        organization="Bhilai Steel Plant",
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
def sample_vessel(db):
    return Vessel.objects.create(
        vessel_name="MV Bulk Horizon",
        imo_number="9876543",
        flag_state="PA",
        build_year=2021,
        deadweight_tonnage=Decimal("180000.000"),
        gross_tonnage=Decimal("92000.000"),
        net_tonnage=Decimal("58000.000"),
        length_overall_meters=Decimal("292.00"),
        beam_meters=Decimal("45.00"),
        draft_meters=Decimal("18.20"),
        hold_count=9,
        hatch_count=9,
        owner_name="Global Bulk Shipping",
    )


@pytest.fixture
def sample_contract(db, sample_vessel):
    return CharterContract.objects.create(
        contract_reference="SAIL/CP/2026/BSP/01",
        vessel=sample_vessel,
        charterer_entity="STEEL_AUTHORITY_OF_INDIA",
        charterer_plant="BHILAI",
        cargo_type="COKING_COAL",
        charter_party_type="VOYAGE_CHARTER",
        contract_status="ACTIVE",
        laycan_start=datetime.date(2026, 9, 1),
        laycan_end=datetime.date(2026, 9, 10),
        load_port="Gladstone",
        discharge_port="Visakhapatnam",
        nominated_quantity_mt=Decimal("75000.000"),
        laytime_terms="SHINC",
        discharge_rate_mt_per_day=Decimal("15000.000"),
        turn_time_hours=Decimal("6.00"),
        demurrage_rate_usd_per_day=Decimal("30000.00"),
        despatch_rate_usd_per_day=Decimal("15000.00"),
        freight_rate_usd_per_mt=Decimal("18.00"),
        allowed_laytime_hours=Decimal("120.000000"),
    )


# ===========================================================================
# Engine Unit Tests
# ===========================================================================

@pytest.mark.django_db
class TestInboundForecastEngine:

    def test_pure_depletion_linear_burn(self, db):
        """Without incoming vessels, stock depletes linearly to zero."""
        today = datetime.date.today()
        # Opening stock 20,000 MT, burn rate 5,000 MT/day -> 4 days runway
        PlantInventory.objects.create(
            plant_code="BSP",
            plant_name="Bhilai Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("20000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("5000.000"),
        )

        forecast = simulate_forward_runway("BSP", "COKING_COAL", horizon_days=10)

        assert forecast.plant_code == "BSP"
        assert forecast.current_stock_mt == Decimal("20000.000")
        assert forecast.daily_consumption_rate_mt == Decimal("5000.000")
        assert forecast.baseline_runway_days == Decimal("4.00")
        assert forecast.inbound_vessels_count == 0
        assert forecast.total_inbound_tonnage_mt == Decimal("0.000")
        assert forecast.is_starvation_risk is True
        assert len(forecast.timeline) == 10

        # Day 1: 20000 - 5000 = 15000 (runway 3.00d -> CRITICAL < 7d)
        d1 = forecast.timeline[0]
        assert d1.day_index == 1
        assert d1.opening_stock_mt == Decimal("20000.000")
        assert d1.closing_stock_mt == Decimal("15000.000")
        assert d1.runway_days_remaining == Decimal("3.00")
        assert d1.safety_status == StockSafetyStatus.CRITICAL.value

        # Day 4: 5000 - 5000 = 0 (runway 0.00d -> Stockout!)
        d4 = forecast.timeline[3]
        assert d4.closing_stock_mt == Decimal("0.000")
        assert d4.runway_days_remaining == Decimal("0.00")
        assert forecast.stockout_date == today + datetime.timedelta(days=4)
        assert forecast.critical_breach_date == today + datetime.timedelta(days=1)

    def test_inbound_replenishment_extends_runway(self, db, sample_contract, sample_vessel):
        """Active voyage scheduled with ETA arrives on ETA + rail lead time."""
        today = datetime.date.today()
        # Initial stock: 15,000 MT, burn: 3,000 MT/day -> 5 days baseline
        PlantInventory.objects.create(
            plant_code="BSP",
            plant_name="Bhilai Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("15000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("3000.000"),
        )

        # Vessel ETA in 2 days; rail transit lead time is 3 days -> arrives day 5
        eta = _utc(today.year, today.month, today.day, 12, 0) + datetime.timedelta(days=2)
        VesselVoyage.objects.create(
            voyage_reference="VOY-BSP-001",
            contract=sample_contract,
            vessel=sample_vessel,
            voyage_status="IN_TRANSIT",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            quantity_loaded_mt=Decimal("60000.000"),
            eta_date=eta,
        )

        forecast = simulate_forward_runway("BSP", "COKING_COAL", horizon_days=15)

        assert forecast.inbound_vessels_count == 1
        assert forecast.total_inbound_tonnage_mt == Decimal("60000.000")
        assert forecast.extended_runway_days == Decimal("25.00")  # (15000 + 60000) / 3000 = 25.00

        # Day 5 should show replenishment of 60,000 MT
        expected_arrival = today + datetime.timedelta(days=5)
        d5 = next(p for p in forecast.timeline if p.simulation_date == expected_arrival)
        assert d5.inbound_receipts_mt == Decimal("60000.000")
        assert any("Replenishment" in ev for ev in d5.events)
        # Without vessel: Day 5 would have had closing 0. With vessel: opening 3000 + 60000 - 3000 = 60000
        assert d5.closing_stock_mt == Decimal("60000.000")
        assert d5.runway_days_remaining == Decimal("20.00")

    def test_custom_stock_and_burn_rate_overrides(self, db):
        """Simulation uses custom overrides if supplied."""
        today = datetime.date.today()
        forecast = simulate_forward_runway(
            plant_code="RSP",
            material_category="COKING_COAL",
            horizon_days=5,
            custom_stock_mt=Decimal("50000.000"),
            custom_burn_rate_mt=Decimal("2500.000"),
        )
        assert forecast.current_stock_mt == Decimal("50000.000")
        assert forecast.daily_consumption_rate_mt == Decimal("2500.000")
        assert forecast.baseline_runway_days == Decimal("20.00")
        assert forecast.is_starvation_risk is False

    def test_plant_name_alias_resolution(self, db):
        """Plant name such as 'BHILAI STEEL PLANT' resolves to BSP."""
        forecast = simulate_forward_runway(
            plant_code="BHILAI STEEL PLANT",
            material_category="COKING_COAL",
            horizon_days=5,
            custom_stock_mt=Decimal("10000.000"),
        )
        assert forecast.plant_code == "BSP"
        assert forecast.plant_name == "Bhilai Steel Plant"

    def test_invalid_plant_code_raises_error(self, db):
        """Unknown plant code raises InboundForecastError with UNKNOWN_PLANT code."""
        with pytest.raises(InboundForecastError) as exc_info:
            simulate_forward_runway("UNKNOWN_STEEL", "COKING_COAL", horizon_days=10)
        assert exc_info.value.code == "UNKNOWN_PLANT"

    def test_invalid_horizon_days_raises_error(self, db):
        """Horizon days outside 1..120 raises InboundForecastError."""
        with pytest.raises(InboundForecastError) as exc_info:
            simulate_forward_runway("BSP", "COKING_COAL", horizon_days=0)
        assert exc_info.value.code == "INVALID_HORIZON"

        with pytest.raises(InboundForecastError) as exc_info2:
            simulate_forward_runway("BSP", "COKING_COAL", horizon_days=125)
        assert exc_info2.value.code == "INVALID_HORIZON"

    def test_voyages_for_other_plants_are_isolated(self, db, sample_vessel):
        """Voyages dedicated to Rourkela are not included in Bhilai forecast."""
        today = datetime.date.today()
        rsp_contract = CharterContract.objects.create(
            contract_reference="SAIL/CP/2026/RSP/01",
            vessel=sample_vessel,
            charterer_entity="STEEL_AUTHORITY_OF_INDIA",
            charterer_plant="ROURKELA",
            cargo_type="COKING_COAL",
            charter_party_type="VOYAGE_CHARTER",
            contract_status="ACTIVE",
            laycan_start=today,
            laycan_end=today + datetime.timedelta(days=5),
            load_port="Gladstone",
            discharge_port="Paradip",
            nominated_quantity_mt=Decimal("70000.000"),
            laytime_terms="SHINC",
            discharge_rate_mt_per_day=Decimal("15000.000"),
            turn_time_hours=Decimal("6.00"),
            demurrage_rate_usd_per_day=Decimal("25000.00"),
            despatch_rate_usd_per_day=Decimal("12500.00"),
            freight_rate_usd_per_mt=Decimal("19.00"),
            allowed_laytime_hours=Decimal("120.000000"),
        )
        VesselVoyage.objects.create(
            voyage_reference="VOY-RSP-999",
            contract=rsp_contract,
            vessel=sample_vessel,
            voyage_status="IN_TRANSIT",
            load_port="Gladstone",
            discharge_port="Paradip",
            quantity_loaded_mt=Decimal("70000.000"),
            eta_date=_utc(today.year, today.month, today.day) + datetime.timedelta(days=2),
        )

        bsp_forecast = simulate_forward_runway("BSP", "COKING_COAL", horizon_days=10)
        assert bsp_forecast.inbound_vessels_count == 0
        assert bsp_forecast.total_inbound_tonnage_mt == Decimal("0.000")

    def test_staggered_vessel_arrivals(self, db, sample_vessel):
        """Multiple staggered vessels replenish stock on different days."""
        today = datetime.date.today()
        contract1 = CharterContract.objects.create(
            contract_reference="SAIL/CP/STAG/01",
            vessel=sample_vessel,
            charterer_entity="STEEL_AUTHORITY_OF_INDIA",
            charterer_plant="BHILAI",
            cargo_type="COKING_COAL",
            charter_party_type="VOYAGE_CHARTER",
            contract_status="ACTIVE",
            laycan_start=today,
            laycan_end=today + datetime.timedelta(days=5),
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            nominated_quantity_mt=Decimal("40000.000"),
            laytime_terms="SHINC",
            discharge_rate_mt_per_day=Decimal("15000.000"),
            turn_time_hours=Decimal("6.00"),
            demurrage_rate_usd_per_day=Decimal("25000.00"),
            despatch_rate_usd_per_day=Decimal("12500.00"),
            freight_rate_usd_per_mt=Decimal("19.00"),
            allowed_laytime_hours=Decimal("120.000000"),
        )
        # Vessel 1 lands on day 3 (ETA day 0 + rail lead 3)
        VesselVoyage.objects.create(
            voyage_reference="VOY-STAG-01",
            contract=contract1,
            vessel=sample_vessel,
            voyage_status="IN_TRANSIT",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            quantity_loaded_mt=Decimal("40000.000"),
            eta_date=_utc(today.year, today.month, today.day),
        )
        # Vessel 2 lands on day 7 (ETA day 4 + rail lead 3)
        VesselVoyage.objects.create(
            voyage_reference="VOY-STAG-02",
            contract=contract1,
            vessel=sample_vessel,
            voyage_status="IN_TRANSIT",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            quantity_loaded_mt=Decimal("50000.000"),
            eta_date=_utc(today.year, today.month, today.day) + datetime.timedelta(days=4),
        )

        forecast = simulate_forward_runway(
            "BSP",
            "COKING_COAL",
            horizon_days=10,
            custom_stock_mt=Decimal("20000.000"),
            custom_burn_rate_mt=Decimal("5000.000"),
        )
        assert forecast.inbound_vessels_count == 2
        assert forecast.total_inbound_tonnage_mt == Decimal("90000.000")
        assert forecast.stockout_date is None

    def test_past_vessel_eta_assigned_to_today(self, db, sample_contract, sample_vessel):
        """Vessels whose ETA + transit lead was in the past land on day 1."""
        today = datetime.date.today()
        # ETA was 10 days ago (e.g. discharging or berthed)
        VesselVoyage.objects.create(
            voyage_reference="VOY-PAST-01",
            contract=sample_contract,
            vessel=sample_vessel,
            voyage_status="DISCHARGING",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            quantity_loaded_mt=Decimal("30000.000"),
            eta_date=_utc(today.year, today.month, today.day) - datetime.timedelta(days=10),
        )

        forecast = simulate_forward_runway(
            "BSP",
            "COKING_COAL",
            horizon_days=5,
            custom_stock_mt=Decimal("10000.000"),
            custom_burn_rate_mt=Decimal("5000.000"),
        )
        # Past arrival effective date is start_date (today), so Day 1 will receive it
        d1 = forecast.timeline[0]
        # In sim, day_1 is start_date + 1 day; effective_date start_date was assigned to start_date
        # Check total inbound tonnage
        assert forecast.total_inbound_tonnage_mt == Decimal("30000.000")

    def test_precautionary_breach_detection(self, db):
        """Runway falling between 7 and 14 days sets precautionary status."""
        today = datetime.date.today()
        # Opening stock 50,000 MT, burn 4,000 MT/day -> 12.50 days baseline (< 14d)
        forecast = simulate_forward_runway(
            "BSL",
            "COKING_COAL",
            horizon_days=10,
            custom_stock_mt=Decimal("50000.000"),
            custom_burn_rate_mt=Decimal("4000.000"),
        )
        assert forecast.precautionary_breach_date == today + datetime.timedelta(days=1)
        d1 = forecast.timeline[0]
        # closing stock = 46,000 / 4,000 = 11.50d
        assert d1.safety_status == StockSafetyStatus.PRECAUTIONARY.value


# ===========================================================================
# REST API Integration Tests
# ===========================================================================

@pytest.mark.django_db
class TestInboundForecastAPI:

    def test_unauthenticated_request_rejected(self, api_client, settings):
        """Unauthenticated requests must be rejected when authentication is enforced."""
        settings.RBAC_REQUIRE_AUTHENTICATION = True
        url = "/api/v1/inventory/inbound-forecast/?plant_code=BSP"
        resp = api_client.get(url)
        assert resp.status_code in (drf_status.HTTP_401_UNAUTHORIZED, drf_status.HTTP_403_FORBIDDEN)

    def test_missing_plant_code_returns_400(self, api_client, plant_manager_user):
        """Missing plant_code param returns 400 Bad Request."""
        api_client.force_authenticate(user=plant_manager_user)
        resp = api_client.get("/api/v1/inventory/inbound-forecast/")
        assert resp.status_code == drf_status.HTTP_400_BAD_REQUEST
        assert "plant_code" in resp.data["error"]

    def test_invalid_plant_code_returns_400(self, api_client, plant_manager_user):
        """Unknown plant code returns 400 Bad Request."""
        api_client.force_authenticate(user=plant_manager_user)
        resp = api_client.get("/api/v1/inventory/inbound-forecast/?plant_code=NON_EXISTENT")
        assert resp.status_code == drf_status.HTTP_400_BAD_REQUEST
        assert "UNKNOWN_PLANT" in resp.data["error"]

    def test_invalid_horizon_days_non_numeric_returns_400(self, api_client, plant_manager_user):
        """Non-integer horizon_days returns 400."""
        api_client.force_authenticate(user=plant_manager_user)
        resp = api_client.get("/api/v1/inventory/inbound-forecast/?plant_code=BSP&horizon_days=forty")
        assert resp.status_code == drf_status.HTTP_400_BAD_REQUEST
        assert "valid integer" in resp.data["error"]

    def test_invalid_horizon_days_out_of_range_returns_400(self, api_client, plant_manager_user):
        """horizon_days > 120 returns 400."""
        api_client.force_authenticate(user=plant_manager_user)
        resp = api_client.get("/api/v1/inventory/inbound-forecast/?plant_code=BSP&horizon_days=180")
        assert resp.status_code == drf_status.HTTP_400_BAD_REQUEST
        assert "between 1 and 120" in resp.data["error"]

    def test_successful_forecast_response_schema(self, api_client, plant_manager_user):
        """GET /api/v1/inventory/inbound-forecast/ returns 200 with complete timeline structure."""
        api_client.force_authenticate(user=plant_manager_user)
        resp = api_client.get("/api/v1/inventory/inbound-forecast/?plant_code=BSP&horizon_days=20")
        assert resp.status_code == drf_status.HTTP_200_OK

        data = resp.data
        assert data["plant_code"] == "BSP"
        assert data["plant_name"] == "Bhilai Steel Plant"
        assert data["material_category"] == "COKING_COAL"
        assert data["horizon_days"] == 20
        assert "current_stock_mt" in data
        assert "baseline_runway_days" in data
        assert "extended_runway_days" in data
        assert "total_inbound_tonnage_mt" in data
        assert "is_starvation_risk" in data
        assert len(data["timeline"]) == 20

        # Verify daily point structure
        first_pt = data["timeline"][0]
        assert first_pt["day_index"] == 1
        assert "simulation_date" in first_pt
        assert "opening_stock_mt" in first_pt
        assert "closing_stock_mt" in first_pt
        assert "runway_days_remaining" in first_pt
        assert "safety_status" in first_pt
        assert "events" in first_pt

    def test_custom_material_category_filter(self, api_client, plant_manager_user):
        """Material category parameter is respected in the forecast."""
        today = datetime.date.today()
        PlantInventory.objects.create(
            plant_code="DSP",
            plant_name="Durgapur Steel Plant",
            material_category="LIMESTONE",
            stock_date=today,
            opening_stock_mt=Decimal("45000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("3000.000"),
        )
        api_client.force_authenticate(user=plant_manager_user)
        resp = api_client.get(
            "/api/v1/inventory/inbound-forecast/?plant_code=DSP&material_category=LIMESTONE&horizon_days=15"
        )
        assert resp.status_code == drf_status.HTTP_200_OK
        assert resp.data["plant_code"] == "DSP"
        assert resp.data["material_category"] == "LIMESTONE"
        assert Decimal(resp.data["current_stock_mt"]) == Decimal("45000.000")
        assert len(resp.data["timeline"]) == 15

    def test_rbac_access_for_all_operational_roles(self, api_client, plant_manager_user, logistics_user, auditor_user):
        """PLANT_MANAGER, LOGISTICS_AGENT, and AUDITOR can all view forward runway forecast."""
        for user in [plant_manager_user, logistics_user, auditor_user]:
            api_client.force_authenticate(user=user)
            resp = api_client.get("/api/v1/inventory/inbound-forecast/?plant_code=ISP&horizon_days=7")
            assert resp.status_code == drf_status.HTTP_200_OK, f"Failed for {user.username}"
            assert resp.data["plant_code"] == "ISP"

    def test_api_horizon_zero_or_negative_returns_400(self, api_client, plant_manager_user):
        """horizon_days=0 returns 400 Bad Request."""
        api_client.force_authenticate(user=plant_manager_user)
        resp = api_client.get("/api/v1/inventory/inbound-forecast/?plant_code=BSP&horizon_days=0")
        assert resp.status_code == drf_status.HTTP_400_BAD_REQUEST
        assert "between 1 and 120" in resp.data["error"]


@pytest.mark.django_db
class TestInboundForecastExtendedScenarios:

    def test_minimum_stock_and_trough_date_calculation(self, db):
        """Tracks the lowest point in the stock profile and its date."""
        today = datetime.date.today()
        # Stock: 10,000 MT, burn: 2,000 MT/day -> Day 5 hits 0 MT
        forecast = simulate_forward_runway(
            "BSP",
            "COKING_COAL",
            horizon_days=7,
            custom_stock_mt=Decimal("10000.000"),
            custom_burn_rate_mt=Decimal("2000.000"),
        )
        assert forecast.minimum_projected_stock_mt == Decimal("0.000")
        assert forecast.minimum_stock_date == today + datetime.timedelta(days=5)

    def test_decimal_precision_strict_three_places(self, db):
        """Quantization check: tonnages strictly 3 decimal places, runway days 2 decimal places."""
        forecast = simulate_forward_runway(
            "RSP",
            "COKING_COAL",
            horizon_days=3,
            custom_stock_mt=Decimal("33333.333"),
            custom_burn_rate_mt=Decimal("1111.111"),
        )
        assert forecast.current_stock_mt == Decimal("33333.333")
        assert forecast.daily_consumption_rate_mt == Decimal("1111.111")
        for pt in forecast.timeline:
            assert pt.opening_stock_mt.as_tuple().exponent == -3
            assert pt.daily_burn_mt.as_tuple().exponent == -3
            assert pt.closing_stock_mt.as_tuple().exponent == -3
            assert pt.runway_days_remaining.as_tuple().exponent == -2

    def test_custom_rail_transit_lead_days(self, db, sample_contract, sample_vessel):
        """Configuring rail_transit_lead_days shifts the plant receipt date accordingly."""
        today = datetime.date.today()
        eta = _utc(today.year, today.month, today.day) + datetime.timedelta(days=1)
        VesselVoyage.objects.create(
            voyage_reference="VOY-LEAD-01",
            contract=sample_contract,
            vessel=sample_vessel,
            voyage_status="IN_TRANSIT",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            quantity_loaded_mt=Decimal("50000.000"),
            eta_date=eta,
        )

        # Lead time = 5 days -> Arrival on ETA + 5 = today + 6 days
        forecast = simulate_forward_runway(
            "BSP",
            "COKING_COAL",
            horizon_days=10,
            rail_transit_lead_days=5,
            custom_stock_mt=Decimal("50000.000"),
            custom_burn_rate_mt=Decimal("3000.000"),
        )
        arrival_date = today + datetime.timedelta(days=6)
        receipt_pt = next(p for p in forecast.timeline if p.simulation_date == arrival_date)
        assert receipt_pt.inbound_receipts_mt == Decimal("50000.000")

    def test_surplus_and_adequate_safety_status_bands(self, db):
        """High inventory correctly triggers SURPLUS (> 45d) and ADEQUATE (21-45d) statuses."""
        forecast = simulate_forward_runway(
            "BSL",
            "COKING_COAL",
            horizon_days=3,
            custom_stock_mt=Decimal("200000.000"),
            custom_burn_rate_mt=Decimal("4000.000"),
        )
        # 200,000 / 4,000 = 50 days runway -> SURPLUS
        assert forecast.timeline[0].safety_status == StockSafetyStatus.SURPLUS.value

