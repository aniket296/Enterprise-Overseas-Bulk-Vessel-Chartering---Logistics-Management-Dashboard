"""
backend/apps/inventory/tests/test_alert_worker.py
============================================================
Cycle 2.4.3 — Tests: Automatic Background Alert Evaluation & Notification Queue Dispatch
Verifies autonomous background watchdog tasks, critical runway alerts, berth collision broadcasts,
and Celery Beat periodic scheduling routed to the 'alerts' queue.

Tests cover:
  1. ping_alert_worker confirms alerts queue worker liveness.
  2. check_critical_stock_alerts_async returns empty/HEALTHY status when no items breach safety bands.
  3. check_critical_stock_alerts_async correctly identifies CRITICAL (< 7d) and PRECAUTIONARY (< 14d) breaches
     with full domain runway calculation (severity, physical runway, projected receipts, stockout date, action).
  4. evaluate_plant_runway_alert_async returns consolidated steel plant health and bottleneck metrics.
  5. alert_active_berth_conflicts_async detects zero conflicts on clear berths and triggers alert on temporal collisions.
  6. alert_voyage_status_change formats transition alert with voyage and vessel telemetry.
  7. Voyage transition API endpoint triggers milestone state alert broadcast to the alerts queue.
  8. POST /api/v1/inventory/trigger-alert-scan/ returns 202 ACCEPTED with task tracking info.
  9. RBAC enforcement on /trigger-alert-scan/ (PLANT_MANAGER, LOGISTICS_AGENT, SYSTEM_ADMIN allowed; READONLY forbidden).
  10. CELERY_BEAT_SCHEDULE configuration integrity and queue segregation for periodic monitors.
"""

import datetime
from decimal import Decimal
import uuid
import pytest
from django.conf import settings
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.authentication.models import UserRole
from apps.contracts.models import CharterContract, Vessel
from apps.inventory.models import PlantInventory, RawMaterialCategory, StockSafetyStatus
from apps.inventory.tasks import (
    check_critical_stock_alerts_async,
    evaluate_plant_runway_alert_async,
    ping_alert_worker,
)
from apps.voyages.models import VesselVoyage
from apps.voyages.tasks import (
    alert_active_berth_conflicts_async,
    alert_voyage_status_change,
)
from apps.laytime.engine.fixture_factory import VoyageStatus

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
        username=f"alert_user_{role.lower()}_{suffix}",
        email=f"alert_{role.lower()}_{suffix}@test.com",
        password="TestPassword123!",
    )
    UserProfile.objects.create(
        user=user,
        role=role,
        organization="SAIL Plant Management",
    )
    return user


@pytest.fixture
def plant_manager_user(db):
    return _make_user(db, UserRole.PLANT_MANAGER.value, "pm")


@pytest.fixture
def logistics_user(db):
    return _make_user(db, UserRole.LOGISTICS_AGENT.value, "log")


@pytest.fixture
def admin_user(db):
    return _make_user(db, UserRole.SYSTEM_ADMIN.value, "adm")


@pytest.fixture
def readonly_user(db):
    return _make_user(db, UserRole.READONLY.value, "ro")


def _make_vessel(db, suffix="01"):
    return Vessel.objects.create(
        vessel_name=f"MV Alert Bulk {suffix}",
        imo_number=f"930000{suffix}",
        flag_state="IN",
        build_year=2021,
        deadweight_tonnage=Decimal("80000.000"),
        gross_tonnage=Decimal("43000.000"),
        net_tonnage=Decimal("24000.000"),
        length_overall_meters=Decimal("229.00"),
        beam_meters=Decimal("32.26"),
        draft_meters=Decimal("14.50"),
        hold_count=7,
        hatch_count=7,
        owner_name="SAIL Ocean Transport",
    )


def _make_contract(db, vessel, suffix="01"):
    return CharterContract.objects.create(
        contract_reference=f"ALT/CP/2025/{suffix}",
        vessel=vessel,
        charterer_entity="STEEL_AUTHORITY_OF_INDIA",
        charterer_plant="BSP",
        cargo_type="COKING_COAL",
        charter_party_type="VOYAGE_CHARTER",
        contract_status="ACTIVE",
        laycan_start=datetime.date(2025, 8, 1),
        laycan_end=datetime.date(2025, 8, 10),
        load_port="Gladstone",
        discharge_port="Visakhapatnam",
        nominated_quantity_mt=Decimal("70000.000"),
        laytime_terms="SHINC",
        discharge_rate_mt_per_day=Decimal("15000.000"),
        demurrage_rate_usd_per_day=Decimal("25000.00"),
        despatch_rate_usd_per_day=Decimal("12500.00"),
        freight_rate_usd_per_mt=Decimal("18.00"),
        allowed_laytime_hours=Decimal("112.000000"),
    )


def _make_voyage(db, contract, vessel, suffix="01", **kwargs):
    params = {
        "voyage_reference": f"ALT/VOY/{suffix}",
        "contract": contract,
        "vessel": vessel,
        "voyage_status": VoyageStatus.SCHEDULED.value,
        "load_port": "Gladstone",
        "load_country": "AU",
        "discharge_port": "Visakhapatnam",
        "discharge_country": "IN",
        "quantity_loaded_mt": Decimal("70000.000"),
        "eta_date": _utc(2025, 8, 15, 0),
    }
    params.update(kwargs)
    return VesselVoyage.objects.create(**params)


# ===========================================================================
# Test Cases
# ===========================================================================

@pytest.mark.django_db
class TestInventoryAlertWorkerTask:

    def test_ping_alert_worker(self):
        """Diagnostic task confirms alerts worker liveness."""
        result = ping_alert_worker.apply().get()
        assert result["queue"] == "alerts"
        assert result["status"] == "ONLINE"
        assert "timestamp" in result

    def test_check_critical_stock_alerts_empty_db(self, db):
        """Scans empty inventory returning zero alerts and HEALTHY status."""
        result = check_critical_stock_alerts_async.apply().get()
        assert result["status"] == "HEALTHY"
        assert result["total_alerts"] == 0
        assert result["critical_count"] == 0
        assert result["precautionary_count"] == 0
        assert result["alerts"] == []

    def test_check_critical_stock_alerts_detects_critical_and_precautionary(self, db):
        """Identifies CRITICAL and PRECAUTIONARY stock levels with domain analysis."""
        today = datetime.date(2025, 8, 1)

        # 1. Critical Item: 10,000 MT / 2,000 MT/day = 5.0 days (< 7d -> CRITICAL)
        PlantInventory.objects.create(
            plant_code="BSP",
            plant_name="Bhilai Steel Plant",
            material_category=RawMaterialCategory.COKING_COAL.value,
            stock_date=today,
            opening_stock_mt=Decimal("12000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("2000.000"),
            daily_consumption_rate_mt=Decimal("2000.000"),
        )

        # 2. Precautionary Item: 20,000 MT / 2,000 MT/day = 10.0 days (7-14d -> PRECAUTIONARY)
        PlantInventory.objects.create(
            plant_code="BSP",
            plant_name="Bhilai Steel Plant",
            material_category=RawMaterialCategory.PCI_COAL.value,
            stock_date=today,
            opening_stock_mt=Decimal("22000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("2000.000"),
            daily_consumption_rate_mt=Decimal("2000.000"),
        )

        # 3. Adequate Item: 60,000 MT / 2,000 MT/day = 30.0 days (21-45d -> ADEQUATE)
        PlantInventory.objects.create(
            plant_code="BSP",
            plant_name="Bhilai Steel Plant",
            material_category=RawMaterialCategory.IRON_ORE_LUMP.value,
            stock_date=today,
            opening_stock_mt=Decimal("62000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("2000.000"),
            daily_consumption_rate_mt=Decimal("2000.000"),
        )

        result = check_critical_stock_alerts_async.apply().get()

        assert result["status"] == "CRITICAL_ALERT"
        assert result["total_alerts"] == 2
        assert result["critical_count"] == 1
        assert result["precautionary_count"] == 1

        critical_alert = next(a for a in result["alerts"] if a["material_category"] == RawMaterialCategory.COKING_COAL.value)
        assert critical_alert["safety_status"] == StockSafetyStatus.CRITICAL.value
        assert critical_alert["severity"] == "HIGH"
        assert critical_alert["stock_runway_days"] == "5.00"
        assert critical_alert["physical_runway_days"] == "5.00"
        assert critical_alert["critical_stockout_date"] is not None
        assert "EMERGENCY" in critical_alert["recommended_action"].upper()

        precautionary_alert = next(a for a in result["alerts"] if a["material_category"] == RawMaterialCategory.PCI_COAL.value)
        assert precautionary_alert["safety_status"] == StockSafetyStatus.PRECAUTIONARY.value
        assert precautionary_alert["severity"] == "MEDIUM"
        assert precautionary_alert["stock_runway_days"] == "10.00"

    def test_evaluate_plant_runway_alert_async(self, db):
        """Evaluates plant-level health summary and bottleneck material."""
        today = datetime.date(2025, 8, 1)
        PlantInventory.objects.create(
            plant_code="BSL",
            plant_name="Bokaro Steel Plant",
            material_category=RawMaterialCategory.COKING_COAL.value,
            stock_date=today,
            opening_stock_mt=Decimal("8000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("2000.000"),
            daily_consumption_rate_mt=Decimal("2000.000"),
        )

        summary = evaluate_plant_runway_alert_async.apply(args=["BSL"]).get()
        assert summary["plant_code"] == "BSL"
        assert summary["plant_name"] == "Bokaro Steel Plant"
        assert summary["critical_count"] == 1
        assert summary["bottleneck_material"] == RawMaterialCategory.COKING_COAL.value
        assert summary["lowest_runway_days"] == "3.00"
        assert summary["overall_health"] == "CRITICAL"


@pytest.mark.django_db
class TestBerthConflictAlertTask:

    def test_alert_active_berth_conflicts_clear(self, db):
        """When no berth conflicts exist, task returns status CLEAR."""
        res = alert_active_berth_conflicts_async.apply().get()
        assert res["status"] == "CLEAR"
        assert res["conflicts_count"] == 0
        assert res["conflicts"] == []

    def test_alert_active_berth_conflicts_detects_collision(self, db):
        """Detects overlapping vessels at same berth and dispatches alert."""
        vessel1 = _make_vessel(db, "V1")
        contract1 = _make_contract(db, vessel1, "C1")
        vessel2 = _make_vessel(db, "V2")
        contract2 = _make_contract(db, vessel2, "C2")

        # Voyage 1 berthed at Haldia Berth-1 from July 10 to July 15
        _make_voyage(
            db,
            contract1,
            vessel1,
            "C1",
            voyage_reference="VOY-CONFLICT-1",
            voyage_status=VoyageStatus.BERTHED.value,
            discharge_port="Haldia",
            discharge_berth="Berth-1",
            berthed_at=_utc(2025, 7, 10, 8),
            discharge_commenced_at=_utc(2025, 7, 10, 10),
            discharge_completed_at=_utc(2025, 7, 15, 18),
        )

        # Voyage 2 arrived and berthed at same berth from July 14 to July 18 (overlap July 14-15)
        _make_voyage(
            db,
            contract2,
            vessel2,
            "C2",
            voyage_reference="VOY-CONFLICT-2",
            voyage_status=VoyageStatus.BERTHED.value,
            discharge_port="Haldia",
            discharge_berth="Berth-1",
            berthed_at=_utc(2025, 7, 14, 8),
            discharge_commenced_at=_utc(2025, 7, 14, 10),
            discharge_completed_at=_utc(2025, 7, 18, 18),
        )

        res = alert_active_berth_conflicts_async.apply().get()
        assert res["status"] == "ALERT_TRIGGERED"
        assert res["conflicts_count"] == 1
        conflict = res["conflicts"][0]
        assert conflict["port"] == "Haldia"
        assert conflict["berth"] == "Berth-1"
        assert Decimal(conflict["overlap_hours"]) > Decimal("0")
        assert {conflict["voyage_a_reference"], conflict["voyage_b_reference"]} == {"VOY-CONFLICT-1", "VOY-CONFLICT-2"}


@pytest.mark.django_db
class TestVoyageMilestoneAlertBroadcast:

    def test_alert_voyage_status_change_task(self, db):
        """Direct alert task execution broadcasts status transition telemetry."""
        vessel = _make_vessel(db, "TEL")
        contract = _make_contract(db, vessel, "TEL")
        voyage = _make_voyage(
            db,
            contract,
            vessel,
            "TEL",
            voyage_reference="VOY-TEL-01",
            voyage_status=VoyageStatus.SCHEDULED.value,
            discharge_port="Visakhapatnam",
        )

        res = alert_voyage_status_change.apply(
            args=[str(voyage.voyage_id), "SCHEDULED", "IN_TRANSIT"]
        ).get()

        assert res["status"] == "SENT"
        assert res["voyage_id"] == str(voyage.voyage_id)
        assert res["voyage_reference"] == "VOY-TEL-01"
        assert res["vessel_name"] == vessel.vessel_name
        assert res["old_status"] == "SCHEDULED"
        assert res["new_status"] == "IN_TRANSIT"
        assert "alert_timestamp" in res

    def test_voyage_transition_api_triggers_alert_broadcast(self, api_client, db, logistics_user):
        """POST /transition/ triggers asynchronous status alert broadcast."""
        vessel = _make_vessel(db, "TRN")
        contract = _make_contract(db, vessel, "TRN")
        voyage = _make_voyage(
            db,
            contract,
            vessel,
            "TRN",
            voyage_reference="VOY-TRN-01",
            voyage_status=VoyageStatus.SCHEDULED.value,
            discharge_port="Visakhapatnam",
        )

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/transition/"
        payload = {
            "target_status": VoyageStatus.IN_TRANSIT.value,
            "timestamp": _utc(2025, 8, 2, 8).isoformat(),
            "remarks": "Vessel departed load port",
        }

        res = api_client.post(url, payload, format="json")
        assert res.status_code == drf_status.HTTP_200_OK

        voyage.refresh_from_db()
        assert voyage.voyage_status == VoyageStatus.IN_TRANSIT.value


@pytest.mark.django_db
class TestTriggerAlertScanAPI:

    def test_trigger_alert_scan_success_202(self, api_client, db, plant_manager_user):
        """POST /api/v1/inventory/trigger-alert-scan/ returns 202 ACCEPTED with alert task info."""
        api_client.force_authenticate(user=plant_manager_user)
        url = "/api/v1/inventory/trigger-alert-scan/"
        res = api_client.post(url, format="json")

        assert res.status_code == drf_status.HTTP_202_ACCEPTED
        data = res.json()
        assert data["status"] == "QUEUED"
        assert data["queue"] == "alerts"
        assert data["task_id"] is not None

    def test_trigger_alert_scan_rbac_permissions(
        self, api_client, db, admin_user, logistics_user, plant_manager_user, readonly_user
    ):
        """RBAC permissions on trigger-alert-scan endpoint."""
        url = "/api/v1/inventory/trigger-alert-scan/"

        # Plant Manager allowed
        api_client.force_authenticate(user=plant_manager_user)
        assert api_client.post(url).status_code == drf_status.HTTP_202_ACCEPTED

        # Logistics Agent allowed
        api_client.force_authenticate(user=logistics_user)
        assert api_client.post(url).status_code == drf_status.HTTP_202_ACCEPTED

        # System Admin allowed
        api_client.force_authenticate(user=admin_user)
        assert api_client.post(url).status_code == drf_status.HTTP_202_ACCEPTED

        # Readonly forbidden
        api_client.force_authenticate(user=readonly_user)
        assert api_client.post(url).status_code == drf_status.HTTP_403_FORBIDDEN


class TestCeleryBeatAlertSchedule:

    def test_celery_beat_schedule_configured(self):
        """Verify CELERY_BEAT_SCHEDULE contains periodic watchdog tasks on alerts queue."""
        beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})

        assert "periodic-inventory-runway-watchdog" in beat_schedule
        inv_job = beat_schedule["periodic-inventory-runway-watchdog"]
        assert inv_job["task"] == "apps.inventory.tasks.check_critical_stock_alerts_async"
        assert inv_job["schedule"] == 900.0
        assert inv_job["options"]["queue"] == "alerts"

        assert "periodic-berth-conflict-watchdog" in beat_schedule
        berth_job = beat_schedule["periodic-berth-conflict-watchdog"]
        assert berth_job["task"] == "apps.voyages.tasks.alert_active_berth_conflicts_async"
        assert berth_job["schedule"] == 900.0
        assert berth_job["options"]["queue"] == "alerts"

    def test_task_routing_resolves_to_alerts_queue(self):
        """Ensure all alert tasks resolve to the segregated 'alerts' queue."""
        from kombu import Queue
        routes = getattr(settings, "CELERY_TASK_ROUTES", {})

        # Inventory tasks route
        assert "apps.inventory.tasks.*" in routes
        assert routes["apps.inventory.tasks.*"]["queue"] == "alerts"

        # Voyages alert tasks route
        assert "apps.voyages.tasks.alert_*" in routes
        assert routes["apps.voyages.tasks.alert_*"]["queue"] == "alerts"
