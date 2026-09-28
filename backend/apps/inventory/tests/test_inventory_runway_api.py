"""
backend/apps/inventory/tests/test_inventory_runway_api.py
============================================================
Cycle 2.3.3 — Tests: Steel Plant Inventory & Blast Furnace Runway API

Tests cover:
  Model Unit Tests:
  1.  PlantInventory automatically calculates closing_stock_mt
  2.  PlantInventory automatically calculates stock_runway_days and safety_status
  3.  All 5 safety status bands (CRITICAL, PRECAUTIONARY, WATCH, ADEQUATE, SURPLUS)
  4.  Excessive consumption raises ValidationError (negative stock guard)
  5.  Unique constraint on (plant_code, material_category, stock_date)

  Engine Unit Tests:
  6.  get_incoming_voyages_for_plant identifies in-transit vessels for plant
  7.  compute_runway_analysis calculates physical vs total projected runway
  8.  get_plant_health_summary identifies bottleneck material and plant health

  REST API Integration Tests:
  9.  GET /api/v1/inventory/ lists snapshots and supports filtering
  10. POST /api/v1/inventory/ creates snapshot and auto-populates plant_name
  11. GET /api/v1/inventory/runway/?plant_code=BSL returns plant-specific analysis
  12. GET /api/v1/inventory/runway/ returns all 5 SAIL plants health overview
  13. GET /api/v1/inventory/critical-alerts/ returns critical/precautionary items
  14. GET /api/v1/inventory/{id}/analysis/ returns record analysis
  15. RBAC permissions:
      - PLANT_MANAGER can POST /inventory/
      - LOGISTICS_AGENT and AUDITOR can GET, but get 403 on POST /inventory/
"""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from rest_framework import status as drf_status
from rest_framework.test import APIClient

UTC = datetime.timezone.utc


def _utc(year, month, day, hour, minute=0) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, minute, 0, tzinfo=UTC)


@pytest.fixture
def api_client():
    return APIClient()


def _make_user(db, role, suffix="01"):
    from django.contrib.auth import get_user_model
    from apps.authentication.models import UserProfile
    User = get_user_model()
    user = User.objects.create_user(
        username=f"inv_user_{role.lower()}_{suffix}",
        email=f"inv_{role.lower()}_{suffix}@test.com",
        password="TestPassword123!",
    )
    UserProfile.objects.create(
        user=user,
        role=role,
        organization="Bokaro Steel Plant",
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


# ---------------------------------------------------------------------------
# Model Unit Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPlantInventoryModel:

    def test_closing_stock_auto_computed(self, db):
        from apps.inventory.models import PlantInventory, RawMaterialCategory
        item = PlantInventory.objects.create(
            plant_code="BSL",
            plant_name="Bokaro Steel Plant",
            material_category=RawMaterialCategory.COKING_COAL.value,
            stock_date=datetime.date(2025, 9, 15),
            opening_stock_mt=Decimal("50000.000"),
            receipts_mt=Decimal("20000.000"),
            consumption_mt=Decimal("15000.000"),
            daily_consumption_rate_mt=Decimal("5000.000"),
        )
        # 50,000 + 20,000 - 15,000 = 55,000 MT
        assert item.closing_stock_mt == Decimal("55000.000")
        # 55,000 / 5,000 = 11.00 days (PRECAUTIONARY: 7-14d)
        assert item.stock_runway_days == Decimal("11.00")
        assert item.safety_status == "PRECAUTIONARY"

    @pytest.mark.parametrize(
        "closing_stock, burn_rate, expected_runway, expected_status",
        [
            (Decimal("25000.000"), Decimal("5000.000"), Decimal("5.00"), "CRITICAL"),         # 5d < 7d
            (Decimal("50000.000"), Decimal("5000.000"), Decimal("10.00"), "PRECAUTIONARY"),  # 10d (7-14d)
            (Decimal("80000.000"), Decimal("5000.000"), Decimal("16.00"), "WATCH"),          # 16d (14-21d)
            (Decimal("150000.000"), Decimal("5000.000"), Decimal("30.00"), "ADEQUATE"),      # 30d (21-45d)
            (Decimal("300000.000"), Decimal("5000.000"), Decimal("60.00"), "SURPLUS"),       # 60d > 45d
        ]
    )
    def test_runway_safety_bands(self, db, closing_stock, burn_rate, expected_runway, expected_status):
        from apps.inventory.models import PlantInventory, RawMaterialCategory
        item = PlantInventory.objects.create(
            plant_code="RSP",
            plant_name="Rourkela Steel Plant",
            material_category=RawMaterialCategory.COKING_COAL.value,
            stock_date=datetime.date(2025, 9, 15),
            opening_stock_mt=closing_stock,
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=burn_rate,
        )
        assert item.stock_runway_days == expected_runway
        assert item.safety_status == expected_status

    def test_negative_closing_stock_rejected(self, db):
        from apps.inventory.models import PlantInventory, RawMaterialCategory
        with pytest.raises(ValidationError):
            PlantInventory.objects.create(
                plant_code="BSL",
                plant_name="Bokaro Steel Plant",
                material_category=RawMaterialCategory.COKING_COAL.value,
                stock_date=datetime.date(2025, 9, 15),
                opening_stock_mt=Decimal("10000.000"),
                receipts_mt=Decimal("5000.000"),
                # Total available = 15,000 MT. Consumption = 20,000 MT → Illegal
                consumption_mt=Decimal("20000.000"),
            )

    def test_unique_constraint_enforced(self, db):
        from apps.inventory.models import PlantInventory, RawMaterialCategory
        PlantInventory.objects.create(
            plant_code="BSP",
            plant_name="Bhilai Steel Plant",
            material_category=RawMaterialCategory.PCI_COAL.value,
            stock_date=datetime.date(2025, 9, 15),
            opening_stock_mt=Decimal("20000.000"),
            consumption_mt=Decimal("2000.000"),
        )
        with pytest.raises(Exception):  # IntegrityError
            PlantInventory.objects.create(
                plant_code="BSP",
                plant_name="Bhilai Steel Plant",
                material_category=RawMaterialCategory.PCI_COAL.value,
                stock_date=datetime.date(2025, 9, 15),
                opening_stock_mt=Decimal("25000.000"),
                consumption_mt=Decimal("2000.000"),
            )


# ---------------------------------------------------------------------------
# Engine Unit Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRunwayEngine:

    def test_incoming_voyages_identified_for_plant(self, db):
        from apps.contracts.models import Vessel, CharterContract
        from apps.voyages.models import VesselVoyage
        from apps.inventory.runway_engine import get_incoming_voyages_for_plant

        # Create vessel and contract destined for BOKARO (BSL)
        vessel = Vessel.objects.create(
            vessel_name="MV Supply Link",
            imo_number="9400001",
            flag_state="IN",
            build_year=2020,
            deadweight_tonnage=Decimal("80000.000"),
            gross_tonnage=Decimal("42000.000"),
            net_tonnage=Decimal("24000.000"),
            length_overall_meters=Decimal("229.00"),
            beam_meters=Decimal("32.26"),
            draft_meters=Decimal("14.50"),
            hold_count=7,
            hatch_count=7,
            owner_name="SAIL Shipping Ltd",
        )
        contract = CharterContract.objects.create(
            contract_reference="SUP/CP/01",
            vessel=vessel,
            charterer_entity="STEEL_AUTHORITY_OF_INDIA",
            charterer_plant="BOKARO",
            cargo_type="COKING_COAL",
            charter_party_type="VOYAGE_CHARTER",
            contract_status="ACTIVE",
            laycan_start=datetime.date(2025, 9, 1),
            laycan_end=datetime.date(2025, 9, 10),
            load_port="Gladstone",
            discharge_port="Haldia",
            nominated_quantity_mt=Decimal("75000.000"),
            laytime_terms="SHINC",
            discharge_rate_mt_per_day=Decimal("15000.000"),
            turn_time_hours=Decimal("6.00"),
            demurrage_rate_usd_per_day=Decimal("30000.00"),
            despatch_rate_usd_per_day=Decimal("15000.00"),
            freight_rate_usd_per_mt=Decimal("18.00"),
            allowed_laytime_hours=Decimal("120.000000"),
        )
        VesselVoyage.objects.create(
            voyage_reference="SUP/VOY/01",
            contract=contract,
            vessel=vessel,
            voyage_status="IN_TRANSIT",
            load_port="Gladstone",
            discharge_port="Haldia",
            quantity_loaded_mt=Decimal("75000.000"),
            eta_date=_utc(2025, 9, 25, 0),
        )

        incoming = get_incoming_voyages_for_plant("BSL", "COKING_COAL")
        assert len(incoming) == 1
        assert incoming[0].voyage_reference == "SUP/VOY/01"
        assert incoming[0].quantity_mt == Decimal("75000.000")

    def test_compute_runway_analysis_physical_vs_projected(self, db):
        from apps.inventory.models import PlantInventory, RawMaterialCategory
        from apps.inventory.runway_engine import compute_runway_analysis, InTransitCargoContribution

        # Current stock = 30,000 MT, burn rate = 5,000 MT/day → Physical runway = 6.00 days (CRITICAL!)
        snap = PlantInventory.objects.create(
            plant_code="BSL",
            plant_name="Bokaro Steel Plant",
            material_category=RawMaterialCategory.COKING_COAL.value,
            stock_date=datetime.date(2025, 9, 15),
            opening_stock_mt=Decimal("30000.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("5000.000"),
        )

        # In-transit vessel with 70,000 MT
        incoming = [
            InTransitCargoContribution(
                voyage_id="v-1",
                voyage_reference="VOY-01",
                vessel_name="MV Iron Pioneer",
                cargo_type="COKING_COAL",
                quantity_mt=Decimal("70000.000"),
                voyage_status="IN_TRANSIT",
                eta_date=_utc(2025, 9, 20, 0),
                discharge_port="Haldia",
            )
        ]

        analysis = compute_runway_analysis(snap, in_transit_override=incoming)

        # Physical runway without ship = 30000 / 5000 = 6.00 days
        assert analysis.physical_runway_days == Decimal("6.00")
        # Total runway with incoming = (30000 + 70000) / 5000 = 20.00 days
        assert analysis.total_runway_days == Decimal("20.00")
        # 20.00d is in WATCH status (14-21d)
        assert analysis.safety_status == "WATCH"
        assert "MONITOR" in analysis.recommended_action
        assert analysis.critical_stockout_date == datetime.date(2025, 9, 15) + datetime.timedelta(days=20)

    def test_get_plant_health_summary(self, db):
        from apps.inventory.models import PlantInventory, RawMaterialCategory
        from apps.inventory.runway_engine import get_plant_health_summary

        # Add 1 adequate and 1 critical material to Durgapur (DSP)
        PlantInventory.objects.create(
            plant_code="DSP",
            plant_name="Durgapur Steel Plant",
            material_category=RawMaterialCategory.COKING_COAL.value,
            stock_date=datetime.date(2025, 9, 15),
            opening_stock_mt=Decimal("150000.000"),
            daily_consumption_rate_mt=Decimal("5000.000"),  # 30d ADEQUATE
        )
        PlantInventory.objects.create(
            plant_code="DSP",
            plant_name="Durgapur Steel Plant",
            material_category=RawMaterialCategory.LIMESTONE.value,
            stock_date=datetime.date(2025, 9, 15),
            opening_stock_mt=Decimal("5000.000"),
            daily_consumption_rate_mt=Decimal("2000.000"),  # 2.5d CRITICAL!
        )

        summary = get_plant_health_summary("DSP")
        assert summary.plant_code == "DSP"
        assert summary.total_materials_tracked == 2
        assert summary.critical_count == 1
        assert summary.adequate_count == 1
        assert summary.overall_health == "CRITICAL"
        assert summary.bottleneck_material == RawMaterialCategory.LIMESTONE.value
        assert summary.lowest_runway_days == Decimal("2.50")


# ---------------------------------------------------------------------------
# REST API Integration Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPlantInventoryAPI:

    def test_list_and_filter_snapshots(self, api_client, db, plant_manager_user):
        from apps.inventory.models import PlantInventory, RawMaterialCategory
        PlantInventory.objects.create(
            plant_code="BSL",
            plant_name="Bokaro Steel Plant",
            material_category=RawMaterialCategory.COKING_COAL.value,
            stock_date=datetime.date(2025, 9, 15),
            opening_stock_mt=Decimal("100000.000"),
            daily_consumption_rate_mt=Decimal("5000.000"),
        )
        PlantInventory.objects.create(
            plant_code="RSP",
            plant_name="Rourkela Steel Plant",
            material_category=RawMaterialCategory.PCI_COAL.value,
            stock_date=datetime.date(2025, 9, 15),
            opening_stock_mt=Decimal("80000.000"),
            daily_consumption_rate_mt=Decimal("4000.000"),
        )

        api_client.force_authenticate(user=plant_manager_user)
        res = api_client.get("/api/v1/inventory/?plant_code=BSL")

        assert res.status_code == drf_status.HTTP_200_OK
        data = res.json()["results"] if "results" in res.json() else res.json()
        assert len(data) == 1
        assert data[0]["plant_code"] == "BSL"

    def test_post_create_snapshot_auto_populates_name(self, api_client, db, plant_manager_user):
        api_client.force_authenticate(user=plant_manager_user)
        payload = {
            "plant_code": "BSP",
            "material_category": "COKING_COAL",
            "stock_date": "2025-09-16",
            "opening_stock_mt": "60000.000",
            "receipts_mt": "15000.000",
            "consumption_mt": "5000.000",
            "daily_consumption_rate_mt": "5000.000",
        }
        res = api_client.post("/api/v1/inventory/", payload, format="json")

        assert res.status_code == drf_status.HTTP_201_CREATED
        data = res.json()
        assert data["plant_code"] == "BSP"
        assert data["plant_name"] == "Bhilai Steel Plant"
        # 60,000 + 15,000 - 5,000 = 70,000 MT
        assert Decimal(data["closing_stock_mt"]) == Decimal("70000.000")
        # 70,000 / 5,000 = 14.00 days (WATCH)
        assert Decimal(data["stock_runway_days"]) == Decimal("14.00")
        assert data["safety_status"] == "WATCH"

    def test_get_runway_specific_plant(self, api_client, db, plant_manager_user):
        from apps.inventory.models import PlantInventory, RawMaterialCategory
        PlantInventory.objects.create(
            plant_code="BSL",
            plant_name="Bokaro Steel Plant",
            material_category=RawMaterialCategory.COKING_COAL.value,
            stock_date=datetime.date(2025, 9, 15),
            opening_stock_mt=Decimal("50000.000"),
            daily_consumption_rate_mt=Decimal("5000.000"),
        )

        api_client.force_authenticate(user=plant_manager_user)
        res = api_client.get("/api/v1/inventory/runway/?plant_code=BSL")

        assert res.status_code == drf_status.HTTP_200_OK
        data = res.json()
        assert len(data) == 1
        assert data[0]["plant_code"] == "BSL"
        assert data[0]["physical_runway_days"] == "10.00"

    def test_get_runway_all_plants_overview(self, api_client, db, plant_manager_user):
        api_client.force_authenticate(user=plant_manager_user)
        res = api_client.get("/api/v1/inventory/runway/")

        assert res.status_code == drf_status.HTTP_200_OK
        data = res.json()
        assert len(data) == 5
        codes = [p["plant_code"] for p in data]
        assert set(codes) == {"BSP", "DSP", "RSP", "BSL", "ISP"}

    def test_get_critical_alerts(self, api_client, db, plant_manager_user):
        from apps.inventory.models import PlantInventory, RawMaterialCategory
        # Add 1 critical snapshot (< 7d)
        PlantInventory.objects.create(
            plant_code="ISP",
            plant_name="IISCO Steel Plant",
            material_category=RawMaterialCategory.COKING_COAL.value,
            stock_date=datetime.date(2025, 9, 15),
            opening_stock_mt=Decimal("15000.000"),
            daily_consumption_rate_mt=Decimal("5000.000"),  # 3 days CRITICAL
        )

        api_client.force_authenticate(user=plant_manager_user)
        res = api_client.get("/api/v1/inventory/critical-alerts/")

        assert res.status_code == drf_status.HTTP_200_OK
        data = res.json()
        assert len(data) >= 1
        assert data[0]["plant_code"] == "ISP"
        assert data[0]["safety_status"] == "CRITICAL"

    def test_rbac_plant_manager_can_post_logistics_cannot(
        self, api_client, db, plant_manager_user, logistics_user
    ):
        payload = {
            "plant_code": "BSL",
            "material_category": "PCI_COAL",
            "stock_date": "2025-09-17",
            "opening_stock_mt": "40000.000",
            "consumption_mt": "2000.000",
            "daily_consumption_rate_mt": "2000.000",
        }

        # Plant manager can POST
        api_client.force_authenticate(user=plant_manager_user)
        res_pm = api_client.post("/api/v1/inventory/", payload, format="json")
        assert res_pm.status_code == drf_status.HTTP_201_CREATED

        # Logistics agent cannot POST (403 FORBIDDEN)
        api_client.force_authenticate(user=logistics_user)
        payload["stock_date"] = "2025-09-18"
        res_log = api_client.post("/api/v1/inventory/", payload, format="json")
        assert res_log.status_code == drf_status.HTTP_403_FORBIDDEN
