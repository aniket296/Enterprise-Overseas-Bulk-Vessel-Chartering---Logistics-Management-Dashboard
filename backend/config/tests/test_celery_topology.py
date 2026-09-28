"""
backend/config/tests/test_celery_topology.py
============================================================
Cycle 2.4.1 — Tests: Redis Broker & Celery Worker Topology Setup

Tests cover:
  1. Celery Application Initialization & Configuration
  2. Queue Segregation Topology (calculations, documents, alerts, default)
  3. Task Routing Resolution Verification
  4. Task Execution across all queues (eager/synchronous mode)
  5. Laytime async calculation worker execution
  6. Inventory stock runway alert watchdog execution
  7. Berth conflict async evaluation task execution
  8. REST API /api/v1/system/celery/topology/ status and task routing endpoint
"""

import datetime
from decimal import Decimal
import pytest
from django.conf import settings
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from config.celery import app as celery_app, debug_task
from config.celery_topology import resolve_task_routing, get_queue_topology_summary
from apps.laytime.tasks import ping_calculation_worker, recalculate_laytime_async
from apps.documents.tasks import ping_document_worker
from apps.inventory.tasks import ping_alert_worker, check_critical_stock_alerts_async
from apps.voyages.tasks import compute_berth_conflicts_async, alert_voyage_status_change
from apps.contracts.models import Vessel, CharterContract
from apps.voyages.models import VesselVoyage
from apps.inventory.models import PlantInventory, RawMaterialCategory, StockSafetyStatus

UTC = datetime.timezone.utc


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def voyage_fixture(db):
    vessel = Vessel.objects.create(
        vessel_name="MV Celery Carrier",
        imo_number="9123456",
        flag_state="IN",
        deadweight_tonnage=Decimal("78000.000"),
        gross_tonnage=Decimal("43000.000"),
        net_tonnage=Decimal("25000.000"),
        length_overall_meters=Decimal("228.00"),
        beam_meters=Decimal("32.25"),
        draft_meters=Decimal("14.40"),
        hold_count=7,
        hatch_count=7,
        build_year=2021,
        owner_name="Shipping Corporation of India",
    )
    contract = CharterContract.objects.create(
        contract_reference="CP-CELERY-2026-001",
        charterer_entity="Steel Authority of India Limited",
        charterer_plant="Bokaro Steel Plant",
        vessel=vessel,
        nominated_quantity_mt=Decimal("75000.000"),
        freight_rate_usd_per_mt=Decimal("16.00"),
        load_port="Gladstone",
        discharge_port="Haldia",
        discharge_rate_mt_per_day=Decimal("12000.000"),
        laycan_start=datetime.date(2026, 9, 1),
        laycan_end=datetime.date(2026, 9, 10),
        demurrage_rate_usd_per_day=Decimal("25000.00"),
        despatch_rate_usd_per_day=Decimal("12500.00"),
    )
    voyage = VesselVoyage.objects.create(
        voyage_reference="VOY-CELERY-2026-001",
        contract=contract,
        vessel=vessel,
        load_port="Gladstone",
        discharge_port="Haldia",
        discharge_berth="Berth-4A",
        quantity_loaded_mt=Decimal("75000.000"),
        eta_date=datetime.datetime(2026, 9, 20, 10, 0, tzinfo=UTC),
    )
    return voyage


# ============================================================================
# 1. Celery Application & Configuration Tests
# ============================================================================

class TestCeleryAppConfiguration:

    def test_celery_app_instance_name(self):
        assert celery_app.main == "bulk_vessel_chartering"

    def test_serialization_and_time_settings(self):
        assert celery_app.conf.task_serializer == "json"
        assert celery_app.conf.result_serializer == "json"
        assert celery_app.conf.accept_content == ["json"]
        assert celery_app.conf.timezone == "UTC"
        assert celery_app.conf.enable_utc is True

    def test_worker_reliability_settings(self):
        assert celery_app.conf.task_acks_late is True
        assert celery_app.conf.worker_prefetch_multiplier == 1
        assert celery_app.conf.task_track_started is True
        assert celery_app.conf.task_time_limit == 300
        assert celery_app.conf.task_soft_time_limit == 240

    def test_queue_segregation_defined(self):
        queues = {q.name for q in celery_app.conf.task_queues}
        assert "calculations" in queues
        assert "documents" in queues
        assert "alerts" in queues
        assert "default" in queues


# ============================================================================
# 2. Task Routing Resolution Tests
# ============================================================================

class TestTaskRoutingResolution:

    def test_laytime_tasks_route_to_calculations(self):
        r1 = resolve_task_routing("apps.laytime.tasks.recalculate_laytime_async")
        assert r1["queue"] == "calculations"

        r2 = resolve_task_routing("apps.laytime.tasks.ping_calculation_worker")
        assert r2["queue"] == "calculations"

    def test_voyages_compute_tasks_route_to_calculations(self):
        r = resolve_task_routing("apps.voyages.tasks.compute_berth_conflicts_async")
        assert r["queue"] == "calculations"

    def test_documents_tasks_route_to_documents(self):
        r1 = resolve_task_routing("apps.documents.tasks.verify_document_hash_async")
        assert r1["queue"] == "documents"

        r2 = resolve_task_routing("apps.documents.tasks.ping_document_worker")
        assert r2["queue"] == "documents"

    def test_inventory_tasks_route_to_alerts(self):
        r1 = resolve_task_routing("apps.inventory.tasks.check_critical_stock_alerts_async")
        assert r1["queue"] == "alerts"

        r2 = resolve_task_routing("apps.inventory.tasks.ping_alert_worker")
        assert r2["queue"] == "alerts"

    def test_voyages_alert_tasks_route_to_alerts(self):
        r = resolve_task_routing("apps.voyages.tasks.alert_voyage_status_change")
        assert r["queue"] == "alerts"

    def test_default_tasks_route_to_default(self):
        r = resolve_task_routing("config.celery.debug_task")
        assert r["queue"] == "default"


# ============================================================================
# 3. Synchronous / Eager Task Execution Tests
# ============================================================================

@pytest.mark.django_db
class TestCeleryTaskExecution:

    def test_ping_calculation_worker(self):
        result = ping_calculation_worker.apply()
        assert result.successful()
        val = result.get()
        assert val["queue"] == "calculations"
        assert val["status"] == "ONLINE"

    def test_ping_document_worker(self):
        result = ping_document_worker.apply()
        assert result.successful()
        val = result.get()
        assert val["queue"] == "documents"
        assert val["status"] == "ONLINE"

    def test_ping_alert_worker(self):
        result = ping_alert_worker.apply()
        assert result.successful()
        val = result.get()
        assert val["queue"] == "alerts"
        assert val["status"] == "ONLINE"

    def test_debug_task_execution(self):
        result = debug_task.apply()
        assert result.successful()

    def test_recalculate_laytime_async_task(self, voyage_fixture):
        from apps.voyages.models import StatementOfFacts
        # Seed realistic SOF events
        t0 = datetime.datetime(2026, 9, 21, 6, 0, tzinfo=UTC)
        t1 = datetime.datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
        t2 = datetime.datetime(2026, 9, 21, 14, 0, tzinfo=UTC)
        t3 = datetime.datetime(2026, 9, 23, 18, 0, tzinfo=UTC)

        StatementOfFacts.objects.create(
            voyage=voyage_fixture, event_type="NOR_TENDERED", event_timestamp=t0, sequence_number=1
        )
        StatementOfFacts.objects.create(
            voyage=voyage_fixture, event_type="NOR_ACCEPTED", event_timestamp=t1, sequence_number=2
        )
        StatementOfFacts.objects.create(
            voyage=voyage_fixture, event_type="DISCHARGE_COMMENCED", event_timestamp=t2, sequence_number=3
        )
        StatementOfFacts.objects.create(
            voyage=voyage_fixture, event_type="DISCHARGE_COMPLETED", event_timestamp=t3, sequence_number=4
        )

        voyage_fixture.nor_tendered_at = t0
        voyage_fixture.nor_accepted_at = t1
        voyage_fixture.discharge_commenced_at = t2
        voyage_fixture.discharge_completed_at = t3
        voyage_fixture.save(skip_clean=True)

        result = recalculate_laytime_async.apply(args=[str(voyage_fixture.voyage_id)])
        assert result.successful()
        val = result.get()
        assert val.get("status") == "SUCCESS", f"Failed with val: {val}"
        assert val["voyage_id"] == str(voyage_fixture.voyage_id)
        assert "outcome" in val
        assert "balance_hours" in val

    def test_check_critical_stock_alerts_async_task(self, db):
        # Create a critical inventory item
        PlantInventory.objects.create(
            plant_code="BSL",
            plant_name="Bokaro Steel Plant",
            material_category=RawMaterialCategory.COKING_COAL.value,
            stock_date=datetime.date(2026, 9, 25),
            opening_stock_mt=Decimal("15000.000"),
            consumption_mt=Decimal("5000.000"),
            daily_consumption_rate_mt=Decimal("2500.000"),
        )
        result = check_critical_stock_alerts_async.apply()
        assert result.successful()
        val = result.get()
        assert val["critical_count"] >= 1
        assert any(a["plant_code"] == "BSL" for a in val["alerts"])

    def test_compute_berth_conflicts_async_task(self, voyage_fixture):
        result = compute_berth_conflicts_async.apply()
        assert result.successful()
        val = result.get()
        assert "total_conflicts" in val
        assert isinstance(val["conflicts"], list)

    def test_alert_voyage_status_change_task(self, voyage_fixture):
        result = alert_voyage_status_change.apply(
            args=[str(voyage_fixture.voyage_id), "IN_TRANSIT", "ARRIVED"]
        )
        assert result.successful()
        val = result.get()
        assert val["voyage_reference"] == voyage_fixture.voyage_reference
        assert val["old_status"] == "IN_TRANSIT"
        assert val["new_status"] == "ARRIVED"


# ============================================================================
# 4. REST API Endpoint Tests
# ============================================================================

@pytest.mark.django_db
class TestCeleryTopologyEndpoint:

    def test_get_topology_summary(self, api_client):
        resp = api_client.get("/api/v1/system/celery/topology/")
        assert resp.status_code == drf_status.HTTP_200_OK
        data = resp.json()

        assert "default_queue" in data
        assert data["default_queue"] == "default"
        assert "queues" in data
        queue_names = [q["name"] for q in data["queues"]]
        assert "calculations" in queue_names
        assert "documents" in queue_names
        assert "alerts" in queue_names
        assert "default" in queue_names

    def test_get_task_routing_query_param(self, api_client):
        resp = api_client.get(
            "/api/v1/system/celery/topology/?task_name=apps.laytime.tasks.recalculate_laytime_async"
        )
        assert resp.status_code == drf_status.HTTP_200_OK
        data = resp.json()
        assert data["task_name"] == "apps.laytime.tasks.recalculate_laytime_async"
        assert data["queue"] == "calculations"

        resp_doc = api_client.get(
            "/api/v1/system/celery/topology/?task_name=apps.documents.tasks.verify_document_hash_async"
        )
        assert resp_doc.json()["queue"] == "documents"

        resp_alert = api_client.get(
            "/api/v1/system/celery/topology/?task_name=apps.inventory.tasks.check_critical_stock_alerts_async"
        )
        assert resp_alert.json()["queue"] == "alerts"
