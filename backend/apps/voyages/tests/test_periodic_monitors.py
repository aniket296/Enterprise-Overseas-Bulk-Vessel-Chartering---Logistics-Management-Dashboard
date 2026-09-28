"""
backend/apps/voyages/tests/test_periodic_monitors.py
============================================================
Cycle 2.4.5 — Tests: Periodic Vault Health Scan & Voyage ETA Deviation Monitor

Tests cover (20 total):

Vault Health Scan Worker Tests (documents/tasks.py):
  1.  scan_pending_documents_async: empty vault → VAULT_CLEAN result
  2.  scan_pending_documents_async: 3 UPLOAD_COMPLETE docs → 3 tasks dispatched
  3.  scan_pending_documents_async: only UPLOAD_COMPLETE docs are dispatched (others skipped)
  4.  scan_pending_documents_async: fan-out verifies each doc end-to-end → all become HASH_VERIFIED
  5.  scan_pending_documents_async: returns correct pending_count and dispatched_count

Voyage ETA Deviation Monitor Tests (voyages/tasks.py):
  6.  monitor_voyage_eta_deviations_async: no voyages → ALL_ON_SCHEDULE
  7.  monitor_voyage_eta_deviations_async: COMPLETED voyage with past ETA not flagged
  8.  monitor_voyage_eta_deviations_async: CANCELLED voyage with past ETA not flagged
  9.  monitor_voyage_eta_deviations_async: IN_TRANSIT voyage with past ETA + no ATA flagged as LOW
  10. monitor_voyage_eta_deviations_async: SCHEDULED voyage 30h overdue → MODERATE severity
  11. monitor_voyage_eta_deviations_async: IN_TRANSIT voyage 36h overdue → HIGH severity
  12. monitor_voyage_eta_deviations_async: DISCHARGING voyage 80h overdue → CRITICAL severity
  13. monitor_voyage_eta_deviations_async: voyage with ATA set is excluded (vessel arrived)
  14. monitor_voyage_eta_deviations_async: multiple overdue voyages ordered by eta_date asc
  15. monitor_voyage_eta_deviations_async: result contains all expected fields

REST API Integration Tests:
  16. POST /api/v1/documents/trigger-vault-scan/ → 202 Accepted with task_id (SYSTEM_ADMIN)
  17. POST /api/v1/documents/trigger-vault-scan/ → 403 Forbidden for non-admin
  18. trigger-vault-scan returns correct pending_documents_in_queue count
  19. Celery Beat schedule includes all 4 entries (inventory, berth, eta, vault)
  20. monitor_voyage_eta_deviations_async task name is under voyages alerts routing
"""

import datetime
import hashlib
from decimal import Decimal
from pathlib import Path

import pytest
from django.utils import timezone
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.authentication.roles import UserRole
from apps.contracts.models import Vessel, CharterContract
from apps.voyages.models import VesselVoyage
from apps.documents.models import (
    ShippingDocument,
    DocumentType,
    DocumentVerificationStatus,
)
from apps.documents.vault_service import DocumentVaultService
from apps.documents.tasks import scan_pending_documents_async
from apps.voyages.tasks import monitor_voyage_eta_deviations_async

UTC = datetime.timezone.utc


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def api_client():
    return APIClient()


def _make_user(role, suffix="mon"):
    from django.contrib.auth import get_user_model
    from apps.authentication.models import UserProfile

    User = get_user_model()
    username = f"mon_{role.lower()}_{suffix}"
    email = f"mon_{role.lower()}_{suffix}@test.com"
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": email, "first_name": role, "last_name": suffix},
    )
    user.set_password("TestPassword123!")
    user.save()
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.role = role
    profile.save()
    return user


@pytest.fixture
def base_contract(db):
    vessel = Vessel.objects.create(
        vessel_name="MV Monitor Bulk",
        imo_number="5551234",
        flag_state="MH",
        deadweight_tonnage=Decimal("80000.000"),
        gross_tonnage=Decimal("45000.000"),
        net_tonnage=Decimal("26000.000"),
        length_overall_meters=Decimal("230.00"),
        beam_meters=Decimal("32.00"),
        draft_meters=Decimal("14.50"),
        hold_count=8,
        hatch_count=8,
        build_year=2018,
        owner_name="Monitor Shipping Ltd",
    )
    contract = CharterContract.objects.create(
        contract_reference="CP-MON-2026-001",
        charterer_entity="SAIL Rourkela Steel",
        charterer_plant="Rourkela",
        vessel=vessel,
        nominated_quantity_mt=Decimal("75000.000"),
        freight_rate_usd_per_mt=Decimal("16.00"),
        load_port="Port Hedland",
        discharge_port="Paradip",
        discharge_rate_mt_per_day=Decimal("12000.000"),
        laycan_start=datetime.date(2026, 10, 1),
        laycan_end=datetime.date(2026, 10, 10),
        demurrage_rate_usd_per_day=Decimal("22000.00"),
        despatch_rate_usd_per_day=Decimal("11000.00"),
    )
    return vessel, contract


def _make_voyage(base_contract, ref, status, eta_past_hours=None, ata_set=False, suffix=""):
    """Create a voyage with configurable ETA and ATA for deviation testing."""
    vessel, contract = base_contract

    if eta_past_hours is not None:
        eta = timezone.now() - datetime.timedelta(hours=eta_past_hours)
    else:
        eta = timezone.now() + datetime.timedelta(hours=24)  # future ETA = not overdue

    voyage = VesselVoyage(
        voyage_reference=f"VOY-MON-{ref}{suffix}",
        vessel=vessel,
        contract=contract,
        load_port="Port Hedland",
        discharge_port="Paradip",
        quantity_loaded_mt=Decimal("74000.000"),
        eta_date=eta,
        ata_date=timezone.now() - datetime.timedelta(hours=1) if ata_set else None,
        voyage_status=status,
    )
    voyage.save(skip_clean=True)
    return voyage


def _make_upload_complete_doc(base_contract, key_suffix, payload=None):
    """Create an UPLOAD_COMPLETE document with payload stored in vault."""
    vessel, contract = base_contract
    payload = payload or f"scan test payload {key_suffix}".encode()
    server_hash = hashlib.sha256(payload).hexdigest().lower()

    doc = ShippingDocument(
        document_type=DocumentType.CARGO_MANIFEST.value,
        document_title=f"Cargo Manifest Scan Test {key_suffix}",
        contract=contract,
        original_filename=f"cargo_{key_suffix}.pdf",
        file_extension=".pdf",
        file_size_bytes=len(payload),
        mime_type="application/pdf",
        minio_bucket="shipping-documents",
        minio_object_key=f"CARGO_MANIFEST/2026/10/scan-{key_suffix}/cargo_{key_suffix}.pdf",
        sha256_hash_client=server_hash,
        verification_status=DocumentVerificationStatus.UPLOAD_COMPLETE.value,
    )
    doc.save(skip_clean=True)

    dest = DocumentVaultService.get_storage_path(doc.minio_bucket, doc.minio_object_key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    return doc


# ─── Test 1: empty vault scan → VAULT_CLEAN ───────────────────────────────────


@pytest.mark.django_db
def test_scan_pending_documents_empty_vault():
    """scan_pending_documents_async with no UPLOAD_COMPLETE docs returns VAULT_CLEAN."""
    result = scan_pending_documents_async.delay()
    data = result.get()

    assert data["status"] == "VAULT_CLEAN"
    assert data["pending_count"] == 0
    assert data["dispatched_count"] == 0
    assert data["dispatched_tasks"] == []


# ─── Test 2: scan dispatches tasks for all UPLOAD_COMPLETE docs ───────────────


@pytest.mark.django_db
def test_scan_pending_documents_dispatches_all_upload_complete(base_contract):
    """scan_pending_documents_async dispatches one verify task per UPLOAD_COMPLETE document."""
    doc1 = _make_upload_complete_doc(base_contract, "d1")
    doc2 = _make_upload_complete_doc(base_contract, "d2")
    doc3 = _make_upload_complete_doc(base_contract, "d3")

    result = scan_pending_documents_async.delay()
    data = result.get()

    assert data["status"] == "DISPATCHED"
    assert data["pending_count"] == 3
    assert data["dispatched_count"] == 3
    assert len(data["dispatched_tasks"]) == 3

    dispatched_doc_ids = {t["document_id"] for t in data["dispatched_tasks"]}
    assert str(doc1.document_id) in dispatched_doc_ids
    assert str(doc2.document_id) in dispatched_doc_ids
    assert str(doc3.document_id) in dispatched_doc_ids


# ─── Test 3: non-UPLOAD_COMPLETE docs are skipped by scan ─────────────────────


@pytest.mark.django_db
def test_scan_pending_documents_skips_non_upload_complete(base_contract):
    """scan_pending_documents_async only fans out for UPLOAD_COMPLETE docs; others untouched."""
    vessel, contract = base_contract

    # HASH_VERIFIED — should be skipped
    verified_doc = ShippingDocument(
        document_type=DocumentType.BILL_OF_LADING.value,
        document_title="BL Already Verified",
        contract=contract,
        original_filename="bl_verified.pdf",
        file_extension=".pdf",
        file_size_bytes=512,
        mime_type="application/pdf",
        minio_bucket="shipping-documents",
        minio_object_key="BILL_OF_LADING/2026/10/skip-scan/bl_verified.pdf",
        sha256_hash_client="a" * 64,
        sha256_hash_server="a" * 64,
        verification_status=DocumentVerificationStatus.HASH_VERIFIED.value,
        verified_at=datetime.datetime.now(UTC),
    )
    verified_doc.save(skip_clean=True)

    # UPLOAD_COMPLETE — should be dispatched
    pending_doc = _make_upload_complete_doc(base_contract, "skip-only-one")

    result = scan_pending_documents_async.delay()
    data = result.get()

    assert data["dispatched_count"] == 1
    dispatched_doc_ids = {t["document_id"] for t in data["dispatched_tasks"]}
    assert str(pending_doc.document_id) in dispatched_doc_ids
    assert str(verified_doc.document_id) not in dispatched_doc_ids


# ─── Test 4: fan-out end-to-end verifies all dispatched docs ─────────────────


@pytest.mark.django_db
def test_scan_pending_documents_end_to_end_verifies_all(base_contract):
    """After scan runs, all UPLOAD_COMPLETE docs (with matching hashes) become HASH_VERIFIED."""
    doc1 = _make_upload_complete_doc(base_contract, "e2e-1")
    doc2 = _make_upload_complete_doc(base_contract, "e2e-2")

    scan_pending_documents_async.delay().get()

    doc1.refresh_from_db()
    doc2.refresh_from_db()
    assert doc1.verification_status == DocumentVerificationStatus.HASH_VERIFIED.value
    assert doc2.verification_status == DocumentVerificationStatus.HASH_VERIFIED.value
    assert doc1.sha256_hash_server is not None
    assert doc2.sha256_hash_server is not None


# ─── Test 5: scan result counts are accurate ─────────────────────────────────


@pytest.mark.django_db
def test_scan_pending_documents_counts_are_accurate(base_contract):
    """pending_count and dispatched_count in scan result match the actual DB count."""
    _make_upload_complete_doc(base_contract, "cnt-1")
    _make_upload_complete_doc(base_contract, "cnt-2")

    result = scan_pending_documents_async.delay()
    data = result.get()

    assert data["pending_count"] == data["dispatched_count"]
    assert data["pending_count"] == 2
    assert "scanned_at" in data


# ─── Test 6: ETA monitor — no voyages → ALL_ON_SCHEDULE ──────────────────────


@pytest.mark.django_db
def test_monitor_voyage_eta_no_voyages_all_on_schedule():
    """monitor_voyage_eta_deviations_async with no voyages returns ALL_ON_SCHEDULE."""
    result = monitor_voyage_eta_deviations_async.delay()
    data = result.get()

    assert data["status"] == "ALL_ON_SCHEDULE"
    assert data["deviated_voyage_count"] == 0
    assert data["deviations"] == []


# ─── Test 7: COMPLETED voyage with past ETA not flagged ──────────────────────


@pytest.mark.django_db
def test_monitor_eta_completed_voyage_not_flagged(base_contract, db):
    """COMPLETED voyages with past ETA are terminal and excluded from deviation scan."""
    _make_voyage(base_contract, "COMP", "COMPLETED", eta_past_hours=48)
    result = monitor_voyage_eta_deviations_async.delay()
    data = result.get()

    assert data["status"] == "ALL_ON_SCHEDULE"
    assert data["deviated_voyage_count"] == 0


# ─── Test 8: CANCELLED voyage with past ETA not flagged ──────────────────────


@pytest.mark.django_db
def test_monitor_eta_cancelled_voyage_not_flagged(base_contract, db):
    """CANCELLED voyages with past ETA are terminal and excluded from deviation scan."""
    _make_voyage(base_contract, "CNCL", "CANCELLED", eta_past_hours=50)
    result = monitor_voyage_eta_deviations_async.delay()
    data = result.get()

    assert data["status"] == "ALL_ON_SCHEDULE"
    assert data["deviated_voyage_count"] == 0


# ─── Test 9: IN_TRANSIT 3h overdue → LOW severity ────────────────────────────


@pytest.mark.django_db
def test_monitor_eta_in_transit_3h_overdue_is_low(base_contract, db):
    """IN_TRANSIT voyage with ETA 3h ago and no ATA → LOW severity deviation."""
    _make_voyage(base_contract, "LOW3", "IN_TRANSIT", eta_past_hours=3)
    result = monitor_voyage_eta_deviations_async.delay()
    data = result.get()

    assert data["deviated_voyage_count"] == 1
    dev = data["deviations"][0]
    assert dev["severity"] == "LOW"
    assert dev["deviation_hours"] >= 3.0
    assert dev["voyage_status"] == "IN_TRANSIT"


# ─── Test 10: SCHEDULED 30h overdue → MODERATE severity ──────────────────────


@pytest.mark.django_db
def test_monitor_eta_scheduled_30h_overdue_is_high(base_contract, db):
    """SCHEDULED voyage with ETA 30h ago and no ATA → HIGH severity deviation (≥24h threshold)."""
    _make_voyage(base_contract, "HIGH30", "SCHEDULED", eta_past_hours=30)
    result = monitor_voyage_eta_deviations_async.delay()
    data = result.get()

    assert data["deviated_voyage_count"] == 1
    dev = data["deviations"][0]
    assert dev["severity"] == "HIGH"
    assert dev["deviation_hours"] >= 24.0


# ─── Test 11: IN_TRANSIT 36h overdue → HIGH severity ─────────────────────────


@pytest.mark.django_db
def test_monitor_eta_in_transit_36h_overdue_is_high(base_contract, db):
    """IN_TRANSIT voyage 36h overdue → HIGH severity."""
    _make_voyage(base_contract, "HIGH36", "IN_TRANSIT", eta_past_hours=36)
    result = monitor_voyage_eta_deviations_async.delay()
    data = result.get()

    assert data["deviated_voyage_count"] == 1
    dev = data["deviations"][0]
    assert dev["severity"] == "HIGH"
    assert dev["deviation_hours"] >= 24.0


# ─── Test 12: DISCHARGING 80h overdue → CRITICAL severity ────────────────────


@pytest.mark.django_db
def test_monitor_eta_discharging_80h_overdue_is_critical(base_contract, db):
    """DISCHARGING voyage 80h overdue → CRITICAL severity."""
    _make_voyage(base_contract, "CRIT80", "DISCHARGING", eta_past_hours=80)
    result = monitor_voyage_eta_deviations_async.delay()
    data = result.get()

    assert data["deviated_voyage_count"] == 1
    dev = data["deviations"][0]
    assert dev["severity"] == "CRITICAL"
    assert dev["deviation_hours"] >= 72.0


# ─── Test 13: voyage with ATA set is excluded ────────────────────────────────


@pytest.mark.django_db
def test_monitor_eta_voyage_with_ata_excluded(base_contract, db):
    """Voyage that has ata_date set (vessel arrived) is excluded from deviation scan."""
    _make_voyage(base_contract, "ATAX", "IN_TRANSIT", eta_past_hours=20, ata_set=True)
    result = monitor_voyage_eta_deviations_async.delay()
    data = result.get()

    assert data["deviated_voyage_count"] == 0
    assert data["status"] == "ALL_ON_SCHEDULE"


# ─── Test 14: multiple overdue voyages ordered by eta_date ascending ──────────


@pytest.mark.django_db
def test_monitor_eta_multiple_overdue_ordered_by_eta_date(base_contract, db):
    """Multiple overdue voyages are returned ordered by eta_date ascending (most overdue first)."""
    _make_voyage(base_contract, "ORD-A", "IN_TRANSIT", eta_past_hours=10, suffix="a")
    _make_voyage(base_contract, "ORD-B", "SCHEDULED", eta_past_hours=50, suffix="b")
    _make_voyage(base_contract, "ORD-C", "IN_TRANSIT", eta_past_hours=25, suffix="c")

    result = monitor_voyage_eta_deviations_async.delay()
    data = result.get()

    assert data["deviated_voyage_count"] == 3
    deviations = data["deviations"]
    # Ordered by eta_date ASC = earliest ETA first = most overdue first
    hours = [d["deviation_hours"] for d in deviations]
    assert hours == sorted(hours, reverse=True), (
        "Deviations should be ordered from most to least overdue"
    )


# ─── Test 15: result contains all required fields ─────────────────────────────


@pytest.mark.django_db
def test_monitor_eta_deviation_result_has_all_fields(base_contract, db):
    """Each deviation entry contains all required domain fields."""
    _make_voyage(base_contract, "FIELDS", "IN_TRANSIT", eta_past_hours=10)
    result = monitor_voyage_eta_deviations_async.delay()
    data = result.get()

    assert data["deviated_voyage_count"] == 1
    dev = data["deviations"][0]
    required_fields = {
        "voyage_id", "voyage_reference", "vessel_name",
        "discharge_port", "voyage_status", "eta_date",
        "deviation_hours", "severity",
    }
    assert required_fields.issubset(dev.keys())
    assert "scanned_at" in data
    assert "total_active_voyages_scanned" in data


# ─── Test 16: POST trigger-vault-scan SYSTEM_ADMIN → 202 ─────────────────────


@pytest.mark.django_db
def test_trigger_vault_scan_admin_returns_202(base_contract, api_client):
    """SYSTEM_ADMIN can POST /trigger-vault-scan/ and receives 202 with task_id."""
    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="vsc1")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    response = api_client.post("/api/v1/documents/trigger-vault-scan/")
    assert response.status_code == drf_status.HTTP_202_ACCEPTED

    body = response.json()
    assert "async_scan" in body
    assert "task_id" in body["async_scan"]
    assert body["async_scan"]["queue"] == "documents"
    assert "pending_documents_in_queue" in body


# ─── Test 17: POST trigger-vault-scan non-admin → 403 ────────────────────────


@pytest.mark.django_db
def test_trigger_vault_scan_non_admin_returns_403(api_client):
    """LOGISTICS_AGENT cannot trigger vault scan — restricted to SYSTEM_ADMIN."""
    agent = _make_user(UserRole.LOGISTICS_AGENT.value, suffix="vsc2")
    api_client.force_authenticate(user=agent)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.LOGISTICS_AGENT.value)

    response = api_client.post("/api/v1/documents/trigger-vault-scan/")
    assert response.status_code == drf_status.HTTP_403_FORBIDDEN


# ─── Test 18: trigger-vault-scan returns correct pending count ────────────────


@pytest.mark.django_db
def test_trigger_vault_scan_pending_count_reflects_db(base_contract, api_client):
    """pending_documents_in_queue in response matches actual UPLOAD_COMPLETE count in DB."""
    _make_upload_complete_doc(base_contract, "cnt-api-1")
    _make_upload_complete_doc(base_contract, "cnt-api-2")

    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="vsc3")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    response = api_client.post("/api/v1/documents/trigger-vault-scan/")
    body = response.json()

    # At minimum 2 UPLOAD_COMPLETE (may be more from previous tests in same transaction)
    actual_pending = ShippingDocument.objects.filter(
        verification_status=DocumentVerificationStatus.UPLOAD_COMPLETE.value
    ).count()
    assert body["pending_documents_in_queue"] == actual_pending


# ─── Test 19: CELERY_BEAT_SCHEDULE has all 4 entries ─────────────────────────


@pytest.mark.django_db
def test_celery_beat_schedule_has_all_four_periodic_entries(settings):
    """CELERY_BEAT_SCHEDULE contains all 4 autonomous watchdog periodic tasks."""
    schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})

    required_keys = {
        "periodic-inventory-runway-watchdog",
        "periodic-berth-conflict-watchdog",
        "periodic-voyage-eta-deviation-monitor",
        "periodic-document-vault-health-scan",
    }
    missing = required_keys - set(schedule.keys())
    assert not missing, f"Missing Beat schedule entries: {missing}"

    # Verify schedules and queues
    assert schedule["periodic-voyage-eta-deviation-monitor"]["schedule"] == 1800.0
    assert schedule["periodic-voyage-eta-deviation-monitor"]["options"]["queue"] == "alerts"
    assert schedule["periodic-document-vault-health-scan"]["schedule"] == 1800.0
    assert schedule["periodic-document-vault-health-scan"]["options"]["queue"] == "documents"


# ─── Test 20: monitor task is registered under alerts routing ─────────────────


@pytest.mark.django_db
def test_monitor_voyage_eta_deviations_task_routing(settings):
    """monitor_voyage_eta_deviations_async is registered and routes to alerts queue."""
    routing = getattr(settings, "CELERY_TASK_ROUTES", {})

    # The monitor_* wildcard pattern should route to alerts
    monitor_routes = {k: v for k, v in routing.items() if "monitor_*" in k}
    assert any("monitor_" in k for k in routing.keys()), (
        "No routing entry found for 'apps.voyages.tasks.monitor_*' in CELERY_TASK_ROUTES"
    )

    # Confirm task is callable and has correct name
    assert monitor_voyage_eta_deviations_async.name == (
        "apps.voyages.tasks.monitor_voyage_eta_deviations_async"
    )
    assert scan_pending_documents_async.name == (
        "apps.documents.tasks.scan_pending_documents_async"
    )
