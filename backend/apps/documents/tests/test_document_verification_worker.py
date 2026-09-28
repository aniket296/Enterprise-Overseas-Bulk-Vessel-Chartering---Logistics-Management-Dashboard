"""
backend/apps/documents/tests/test_document_verification_worker.py
============================================================
Cycle 2.4.4 — Tests: Document Verification & Digest Generation Async Worker

Tests cover (20 total):

Worker Unit Tests (tasks.py):
  1.  ping_document_worker returns ONLINE status with timestamp
  2.  verify_document_async: UPLOAD_COMPLETE + matching client hash → HASH_VERIFIED
  3.  verify_document_async: UPLOAD_COMPLETE + no client hash → HASH_VERIFIED with server digest
  4.  verify_document_async: UPLOAD_COMPLETE + mismatched client hash → HASH_MISMATCH with detail
  5.  verify_document_async: non-UPLOAD_COMPLETE document is skipped (idempotent)
  6.  verify_document_async: document not found returns DOCUMENT_NOT_FOUND error
  7.  verify_document_async: persisted server hash matches deterministic SHA-256 of stored payload
  8.  quarantine_mismatched_document_async: HASH_MISMATCH → QUARANTINED with note appended
  9.  quarantine_mismatched_document_async: non-HASH_MISMATCH document is skipped (idempotent)
  10. quarantine_mismatched_document_async: document not found returns DOCUMENT_NOT_FOUND error

State Machine Integrity Tests:
  11. HASH_VERIFIED → only APPROVED, SUPERSEDED, DELETED are legal next states
  12. HASH_MISMATCH → only QUARANTINED, DELETED are legal next states
  13. QUARANTINED → only DELETED is legal next state
  14. Verified_at timestamp is set only on HASH_VERIFIED, not on HASH_MISMATCH

REST API Integration Tests:
  15. POST /api/v1/documents/{id}/verify/ on UPLOAD_COMPLETE → 202 Accepted with task_id
  16. POST /api/v1/documents/{id}/verify/ on HASH_VERIFIED doc → 400 Bad Request (wrong status)
  17. POST /api/v1/documents/{id}/verify/ RBAC: LOGISTICS_AGENT allowed, READONLY forbidden
  18. Worker execution drives DB: verify then check DB status reflects HASH_VERIFIED
  19. Worker execution drives DB: verify with mismatch then check DB status reflects HASH_MISMATCH
  20. Celery task names are correctly registered under 'documents' queue routing
"""

import hashlib
import io
import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.authentication.roles import UserRole
from apps.contracts.models import Vessel, CharterContract
from apps.voyages.models import VesselVoyage
from apps.documents.models import (
    ShippingDocument,
    DocumentType,
    DocumentVerificationStatus,
    LEGAL_DOCUMENT_STATUS_TRANSITIONS,
)
from apps.documents.vault_service import DocumentVaultService
from apps.documents.tasks import (
    ping_document_worker,
    verify_document_async,
    quarantine_mismatched_document_async,
)

UTC = datetime.timezone.utc


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def api_client():
    return APIClient()


def _make_user(db, role, suffix="wkr"):
    from django.contrib.auth import get_user_model
    from apps.authentication.models import UserProfile

    User = get_user_model()
    username = f"doc_wkr_{role.lower()}_{suffix}"
    email = f"doc_wkr_{role.lower()}_{suffix}@test.com"
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
def voyage_and_contract(db):
    vessel = Vessel.objects.create(
        vessel_name="MV Verify Vessel",
        imo_number="7654321",
        flag_state="SG",
        deadweight_tonnage=Decimal("60000.000"),
        gross_tonnage=Decimal("35000.000"),
        net_tonnage=Decimal("20000.000"),
        length_overall_meters=Decimal("210.00"),
        beam_meters=Decimal("30.00"),
        draft_meters=Decimal("13.50"),
        hold_count=6,
        hatch_count=6,
        build_year=2020,
        owner_name="Verify Shipping Co",
    )
    contract = CharterContract.objects.create(
        contract_reference="CP-VERIFY-2026-001",
        charterer_entity="SAIL Bhilai Steel Plant",
        charterer_plant="Bhilai",
        vessel=vessel,
        nominated_quantity_mt=Decimal("55000.000"),
        freight_rate_usd_per_mt=Decimal("14.00"),
        load_port="Richards Bay",
        discharge_port="Vizag",
        discharge_rate_mt_per_day=Decimal("8000.000"),
        laycan_start=datetime.date(2026, 10, 1),
        laycan_end=datetime.date(2026, 10, 10),
        demurrage_rate_usd_per_day=Decimal("18000.00"),
        despatch_rate_usd_per_day=Decimal("9000.00"),
    )
    voyage = VesselVoyage(
        voyage_reference="VOY-2026-VERIFY-001",
        vessel=vessel,
        contract=contract,
        load_port="Richards Bay",
        quantity_loaded_mt=Decimal("54000.000"),
        eta_date=datetime.date(2026, 10, 15),
    )
    voyage.save(skip_clean=True)
    return voyage, contract


def _make_document_upload_complete(voyage_and_contract, payload: bytes = None) -> ShippingDocument:
    """
    Creates a ShippingDocument in UPLOAD_COMPLETE status with a real payload
    stored in the air-gapped vault. This simulates an upload that has been
    received but not yet hash-verified by the async worker.
    """
    voyage, contract = voyage_and_contract
    if payload is None:
        payload = b"SAIL coking coal shipment manifest data block - Richards Bay to Vizag 2026"

    server_hash = hashlib.sha256(payload).hexdigest().lower()
    file_size = len(payload)

    doc = ShippingDocument(
        document_type=DocumentType.CARGO_MANIFEST.value,
        document_title="Cargo Manifest - Verify Test",
        voyage=voyage,
        original_filename="cargo_manifest_test.pdf",
        file_extension=".pdf",
        file_size_bytes=file_size,
        mime_type="application/pdf",
        minio_bucket="shipping-documents",
        minio_object_key=f"CARGO_MANIFEST/2026/10/test-verify-doc/cargo_manifest_test.pdf",
        sha256_hash_server=server_hash,
        sha256_hash_client=server_hash,
        verification_status=DocumentVerificationStatus.UPLOAD_COMPLETE.value,
    )
    doc.save(skip_clean=True)

    # Store payload in vault storage
    dest_path = DocumentVaultService.get_storage_path(doc.minio_bucket, doc.minio_object_key)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(payload)

    # Clear hashes to simulate the state: file is stored, but worker hasn't verified yet
    doc.sha256_hash_server = None
    doc.sha256_hash_client = server_hash
    doc.save(skip_clean=True)

    return doc


# ─── Test 1: ping_document_worker liveness ────────────────────────────────────


@pytest.mark.django_db
def test_ping_document_worker_returns_online():
    """Worker queue liveness diagnostic returns expected shape."""
    result = ping_document_worker.delay()
    data = result.get()
    assert data["queue"] == "documents"
    assert data["status"] == "ONLINE"
    assert "timestamp" in data


# ─── Test 2: verify with matching client hash → HASH_VERIFIED ─────────────────


@pytest.mark.django_db
def test_verify_document_async_matching_hash_gives_verified(voyage_and_contract, settings):
    """UPLOAD_COMPLETE + matching client hash → HASH_VERIFIED with server hash + verified_at."""
    payload = b"exact payload content for test case 2"
    correct_hash = hashlib.sha256(payload).hexdigest().lower()

    voyage, contract = voyage_and_contract
    doc = ShippingDocument(
        document_type=DocumentType.BILL_OF_LADING.value,
        document_title="B/L Matching Hash",
        voyage=voyage,
        original_filename="bl_matching.pdf",
        file_extension=".pdf",
        file_size_bytes=len(payload),
        mime_type="application/pdf",
        minio_bucket="shipping-documents",
        minio_object_key="BILL_OF_LADING/2026/10/match-test/bl_matching.pdf",
        sha256_hash_client=correct_hash,
        verification_status=DocumentVerificationStatus.UPLOAD_COMPLETE.value,
    )
    doc.save(skip_clean=True)

    dest = DocumentVaultService.get_storage_path(doc.minio_bucket, doc.minio_object_key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)

    result = verify_document_async.delay(str(doc.document_id))
    data = result.get()

    assert data["verification_status"] == DocumentVerificationStatus.HASH_VERIFIED.value
    assert data["sha256_hash_server"] == correct_hash
    assert data["outcome_reason"] == "DIGEST_MATCH"

    doc.refresh_from_db()
    assert doc.verification_status == DocumentVerificationStatus.HASH_VERIFIED.value
    assert doc.sha256_hash_server == correct_hash
    assert doc.verified_at is not None


# ─── Test 3: verify with no client hash → HASH_VERIFIED ──────────────────────


@pytest.mark.django_db
def test_verify_document_async_no_client_hash_gives_verified(voyage_and_contract):
    """UPLOAD_COMPLETE + no client hash → server computes digest and marks HASH_VERIFIED."""
    payload = b"B/L document payload without client hash"

    voyage, contract = voyage_and_contract
    expected_hash = hashlib.sha256(payload).hexdigest().lower()

    doc = ShippingDocument(
        document_type=DocumentType.BILL_OF_LADING.value,
        document_title="B/L No Client Hash",
        voyage=voyage,
        original_filename="bl_no_client_hash.pdf",
        file_extension=".pdf",
        file_size_bytes=len(payload),
        mime_type="application/pdf",
        minio_bucket="shipping-documents",
        minio_object_key="BILL_OF_LADING/2026/10/no-hash-test/bl_no_hash.pdf",
        sha256_hash_client=None,
        verification_status=DocumentVerificationStatus.UPLOAD_COMPLETE.value,
    )
    doc.save(skip_clean=True)

    dest = DocumentVaultService.get_storage_path(doc.minio_bucket, doc.minio_object_key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)

    result = verify_document_async.delay(str(doc.document_id))
    data = result.get()

    assert data["verification_status"] == DocumentVerificationStatus.HASH_VERIFIED.value
    assert data["outcome_reason"] == "SERVER_DIGEST_COMPUTED_NO_CLIENT_HASH"

    doc.refresh_from_db()
    assert doc.verification_status == DocumentVerificationStatus.HASH_VERIFIED.value
    assert doc.sha256_hash_server == expected_hash
    assert doc.sha256_hash_client == expected_hash  # mirrored from server


# ─── Test 4: verify with mismatched client hash → HASH_MISMATCH ──────────────


@pytest.mark.django_db
def test_verify_document_async_mismatched_hash_gives_mismatch(voyage_and_contract):
    """UPLOAD_COMPLETE + wrong client hash → HASH_MISMATCH with mismatch_detail populated."""
    real_payload = b"actual payload bytes for mismatch test"
    real_hash = hashlib.sha256(real_payload).hexdigest().lower()
    fake_client_hash = "a" * 64  # deliberately wrong

    voyage, contract = voyage_and_contract
    doc = ShippingDocument(
        document_type=DocumentType.CARGO_MANIFEST.value,
        document_title="Cargo Manifest Mismatch",
        voyage=voyage,
        original_filename="manifest_mismatch.csv",
        file_extension=".csv",
        file_size_bytes=len(real_payload),
        mime_type="text/csv",
        minio_bucket="shipping-documents",
        minio_object_key="CARGO_MANIFEST/2026/10/mismatch-test/manifest_mismatch.csv",
        sha256_hash_client=fake_client_hash,
        verification_status=DocumentVerificationStatus.UPLOAD_COMPLETE.value,
    )
    doc.save(skip_clean=True)

    dest = DocumentVaultService.get_storage_path(doc.minio_bucket, doc.minio_object_key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(real_payload)

    result = verify_document_async.delay(str(doc.document_id))
    data = result.get()

    assert data["verification_status"] == DocumentVerificationStatus.HASH_MISMATCH.value
    assert data["sha256_hash_server"] == real_hash
    assert data["sha256_hash_client"] == fake_client_hash
    assert data["outcome_reason"] == "CLIENT_SERVER_DIGEST_MISMATCH"
    assert "mismatch_detail" in data

    doc.refresh_from_db()
    assert doc.verification_status == DocumentVerificationStatus.HASH_MISMATCH.value
    assert doc.sha256_hash_server == real_hash
    assert doc.verification_mismatch_detail is not None
    assert fake_client_hash in doc.verification_mismatch_detail
    assert real_hash in doc.verification_mismatch_detail


# ─── Test 5: non-UPLOAD_COMPLETE document is skipped ─────────────────────────


@pytest.mark.django_db
def test_verify_document_async_skips_non_upload_complete(voyage_and_contract):
    """verify_document_async is idempotent: non-UPLOAD_COMPLETE documents are skipped."""
    voyage, contract = voyage_and_contract
    payload = b"already verified document content"
    server_hash = hashlib.sha256(payload).hexdigest().lower()

    doc = ShippingDocument(
        document_type=DocumentType.LAYTIME_STATEMENT.value,
        document_title="Already Verified Laytime Statement",
        voyage=voyage,
        original_filename="laytime_stmt.pdf",
        file_extension=".pdf",
        file_size_bytes=len(payload),
        mime_type="application/pdf",
        minio_bucket="shipping-documents",
        minio_object_key="LAYTIME_STATEMENT/2026/10/skip-test/laytime_stmt.pdf",
        sha256_hash_client=server_hash,
        sha256_hash_server=server_hash,
        verification_status=DocumentVerificationStatus.HASH_VERIFIED.value,
        verified_at=datetime.datetime.now(UTC),
    )
    doc.save(skip_clean=True)

    result = verify_document_async.delay(str(doc.document_id))
    data = result.get()

    assert data["outcome_reason"] == "SKIPPED_NOT_UPLOAD_COMPLETE"
    assert data["verification_status"] == DocumentVerificationStatus.HASH_VERIFIED.value

    # DB must be unchanged
    doc.refresh_from_db()
    assert doc.verification_status == DocumentVerificationStatus.HASH_VERIFIED.value


# ─── Test 6: document not found returns error ─────────────────────────────────


@pytest.mark.django_db
def test_verify_document_async_nonexistent_document():
    """verify_document_async gracefully handles missing document_id."""
    import uuid
    nonexistent_id = str(uuid.uuid4())
    result = verify_document_async.delay(nonexistent_id)
    data = result.get()

    assert data["error"] == "DOCUMENT_NOT_FOUND"
    assert data["status"] == "FAILED"
    assert data["document_id"] == nonexistent_id


# ─── Test 7: server hash determinism ─────────────────────────────────────────


@pytest.mark.django_db
def test_verify_document_async_server_hash_is_deterministic(voyage_and_contract):
    """Server hash computed by the worker matches direct hashlib.sha256 calculation."""
    payload = b"determinism check: SAIL Vizag port SOF data chunk 2026-Q4"
    expected_hash = hashlib.sha256(payload).hexdigest().lower()

    voyage, contract = voyage_and_contract
    doc = ShippingDocument(
        document_type=DocumentType.STATEMENT_OF_FACTS.value,
        document_title="SOF Determinism Test",
        voyage=voyage,
        original_filename="sof_det.txt",
        file_extension=".txt",
        file_size_bytes=len(payload),
        mime_type="text/plain",
        minio_bucket="shipping-documents",
        minio_object_key="STATEMENT_OF_FACTS/2026/10/det-test/sof_det.txt",
        sha256_hash_client=expected_hash,
        verification_status=DocumentVerificationStatus.UPLOAD_COMPLETE.value,
    )
    doc.save(skip_clean=True)

    dest = DocumentVaultService.get_storage_path(doc.minio_bucket, doc.minio_object_key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)

    result = verify_document_async.delay(str(doc.document_id))
    data = result.get()

    assert data["sha256_hash_server"] == expected_hash
    doc.refresh_from_db()
    assert doc.sha256_hash_server == expected_hash


# ─── Test 8: quarantine HASH_MISMATCH → QUARANTINED ──────────────────────────


@pytest.mark.django_db
def test_quarantine_mismatched_document_transitions_to_quarantined(voyage_and_contract):
    """quarantine_mismatched_document_async transitions HASH_MISMATCH → QUARANTINED."""
    voyage, contract = voyage_and_contract
    doc = ShippingDocument(
        document_type=DocumentType.LETTER_OF_INDEMNITY.value,
        document_title="LOI Quarantine Test",
        voyage=voyage,
        original_filename="loi_quar.pdf",
        file_extension=".pdf",
        file_size_bytes=1024,
        mime_type="application/pdf",
        minio_bucket="shipping-documents",
        minio_object_key="LETTER_OF_INDEMNITY/2026/10/quar-test/loi_quar.pdf",
        sha256_hash_client="b" * 64,
        sha256_hash_server="c" * 64,
        verification_status=DocumentVerificationStatus.HASH_MISMATCH.value,
        verification_mismatch_detail="Client 'b'*64 != server 'c'*64.",
    )
    doc.save(skip_clean=True)

    note = "Escalated by customs compliance officer — file tampering suspected."
    result = quarantine_mismatched_document_async.delay(
        str(doc.document_id), quarantine_note=note
    )
    data = result.get()

    assert data["verification_status"] == DocumentVerificationStatus.QUARANTINED.value
    assert data["outcome_reason"] == "HASH_MISMATCH_AUTO_QUARANTINED"

    doc.refresh_from_db()
    assert doc.verification_status == DocumentVerificationStatus.QUARANTINED.value
    assert note in doc.verification_mismatch_detail
    assert "[AUTO-QUARANTINE by Celery worker]" in doc.verification_mismatch_detail


# ─── Test 9: quarantine on non-HASH_MISMATCH is skipped ──────────────────────


@pytest.mark.django_db
def test_quarantine_mismatched_document_skips_non_mismatch(voyage_and_contract):
    """quarantine_mismatched_document_async is idempotent on non-HASH_MISMATCH documents."""
    voyage, contract = voyage_and_contract
    doc = ShippingDocument(
        document_type=DocumentType.INSURANCE_CERTIFICATE.value,
        document_title="Insurance Cert Skip Quarantine",
        voyage=voyage,
        original_filename="insure_skip.pdf",
        file_extension=".pdf",
        file_size_bytes=512,
        mime_type="application/pdf",
        minio_bucket="shipping-documents",
        minio_object_key="INSURANCE_CERTIFICATE/2026/10/skip-quar/insure_skip.pdf",
        sha256_hash_client="d" * 64,
        sha256_hash_server="d" * 64,
        verification_status=DocumentVerificationStatus.HASH_VERIFIED.value,
        verified_at=datetime.datetime.now(UTC),
    )
    doc.save(skip_clean=True)

    result = quarantine_mismatched_document_async.delay(str(doc.document_id))
    data = result.get()

    assert data["outcome_reason"] == "SKIPPED_NOT_HASH_MISMATCH"
    assert data["verification_status"] == DocumentVerificationStatus.HASH_VERIFIED.value

    doc.refresh_from_db()
    assert doc.verification_status == DocumentVerificationStatus.HASH_VERIFIED.value


# ─── Test 10: quarantine with missing document ────────────────────────────────


@pytest.mark.django_db
def test_quarantine_mismatched_document_nonexistent_document():
    """quarantine_mismatched_document_async handles missing document_id gracefully."""
    import uuid
    bad_id = str(uuid.uuid4())
    result = quarantine_mismatched_document_async.delay(bad_id)
    data = result.get()

    assert data["error"] == "DOCUMENT_NOT_FOUND"
    assert data["status"] == "FAILED"


# ─── Test 11: legal transitions from HASH_VERIFIED ───────────────────────────


@pytest.mark.django_db
def test_state_machine_legal_transitions_from_hash_verified(voyage_and_contract):
    """HASH_VERIFIED only allows APPROVED, SUPERSEDED, DELETED as next states."""
    allowed = LEGAL_DOCUMENT_STATUS_TRANSITIONS[DocumentVerificationStatus.HASH_VERIFIED.value]
    assert DocumentVerificationStatus.APPROVED.value in allowed
    assert DocumentVerificationStatus.SUPERSEDED.value in allowed
    assert DocumentVerificationStatus.DELETED.value in allowed
    # Must NOT allow direct jump to QUARANTINED from VERIFIED
    assert DocumentVerificationStatus.QUARANTINED.value not in allowed
    assert DocumentVerificationStatus.HASH_MISMATCH.value not in allowed


# ─── Test 12: legal transitions from HASH_MISMATCH ───────────────────────────


@pytest.mark.django_db
def test_state_machine_legal_transitions_from_hash_mismatch(voyage_and_contract):
    """HASH_MISMATCH only allows QUARANTINED and DELETED as next states."""
    allowed = LEGAL_DOCUMENT_STATUS_TRANSITIONS[DocumentVerificationStatus.HASH_MISMATCH.value]
    assert DocumentVerificationStatus.QUARANTINED.value in allowed
    assert DocumentVerificationStatus.DELETED.value in allowed
    assert DocumentVerificationStatus.HASH_VERIFIED.value not in allowed
    assert DocumentVerificationStatus.APPROVED.value not in allowed


# ─── Test 13: legal transitions from QUARANTINED ─────────────────────────────


@pytest.mark.django_db
def test_state_machine_legal_transitions_from_quarantined():
    """QUARANTINED only allows DELETED as next state."""
    allowed = LEGAL_DOCUMENT_STATUS_TRANSITIONS[DocumentVerificationStatus.QUARANTINED.value]
    assert allowed == {DocumentVerificationStatus.DELETED.value}


# ─── Test 14: verified_at only set on HASH_VERIFIED ──────────────────────────


@pytest.mark.django_db
def test_verified_at_set_only_on_hash_verified_not_mismatch(voyage_and_contract):
    """verified_at is only populated on HASH_VERIFIED, not on HASH_MISMATCH."""
    real_payload = b"verified_at stamp test payload"
    real_hash = hashlib.sha256(real_payload).hexdigest().lower()
    fake_client_hash = "f" * 64

    voyage, contract = voyage_and_contract
    doc = ShippingDocument(
        document_type=DocumentType.DRAFT_SURVEY_REPORT.value,
        document_title="Draft Survey Mismatch Timestamp Test",
        voyage=voyage,
        original_filename="draft_survey_ts.pdf",
        file_extension=".pdf",
        file_size_bytes=len(real_payload),
        mime_type="application/pdf",
        minio_bucket="shipping-documents",
        minio_object_key="DRAFT_SURVEY_REPORT/2026/10/ts-test/draft_survey_ts.pdf",
        sha256_hash_client=fake_client_hash,
        verification_status=DocumentVerificationStatus.UPLOAD_COMPLETE.value,
    )
    doc.save(skip_clean=True)

    dest = DocumentVaultService.get_storage_path(doc.minio_bucket, doc.minio_object_key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(real_payload)

    result = verify_document_async.delay(str(doc.document_id))
    result.get()

    doc.refresh_from_db()
    assert doc.verification_status == DocumentVerificationStatus.HASH_MISMATCH.value
    # verified_at MUST remain NULL on mismatch
    assert doc.verified_at is None


# ─── Test 15: POST /verify/ on UPLOAD_COMPLETE → 202 Accepted ────────────────


@pytest.mark.django_db
def test_verify_endpoint_on_upload_complete_returns_202(voyage_and_contract, api_client):
    """POST /verify/ on UPLOAD_COMPLETE document returns 202 with task_id."""
    from django.contrib.auth import get_user_model
    from apps.authentication.models import UserProfile

    User = get_user_model()
    user = _make_user(None, UserRole.LOGISTICS_AGENT.value, suffix="v01")
    user.save()

    api_client.force_authenticate(user=user)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.LOGISTICS_AGENT.value)

    voyage, contract = voyage_and_contract
    payload = b"verify endpoint test payload v01"
    server_hash = hashlib.sha256(payload).hexdigest().lower()

    doc = ShippingDocument(
        document_type=DocumentType.NOTICE_OF_READINESS.value,
        document_title="NOR Verify Endpoint Test",
        voyage=voyage,
        original_filename="nor_endpoint.pdf",
        file_extension=".pdf",
        file_size_bytes=len(payload),
        mime_type="application/pdf",
        minio_bucket="shipping-documents",
        minio_object_key="NOTICE_OF_READINESS/2026/10/ep-test/nor_endpoint.pdf",
        sha256_hash_client=server_hash,
        verification_status=DocumentVerificationStatus.UPLOAD_COMPLETE.value,
    )
    doc.save(skip_clean=True)

    dest = DocumentVaultService.get_storage_path(doc.minio_bucket, doc.minio_object_key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)

    url = f"/api/v1/documents/{doc.document_id}/verify/"
    response = api_client.post(url)

    assert response.status_code == drf_status.HTTP_202_ACCEPTED
    body = response.json()
    assert body["document_id"] == str(doc.document_id)
    assert "async_verification" in body
    assert "task_id" in body["async_verification"]
    assert body["async_verification"]["queue"] == "documents"
    assert body["current_status"] == DocumentVerificationStatus.UPLOAD_COMPLETE.value


# ─── Test 16: POST /verify/ on already-verified doc → 400 Bad Request ─────────


@pytest.mark.django_db
def test_verify_endpoint_on_hash_verified_returns_400(voyage_and_contract, api_client):
    """POST /verify/ on a HASH_VERIFIED document returns 400 with INVALID_STATUS error."""
    user = _make_user(None, UserRole.LOGISTICS_AGENT.value, suffix="v02")
    user.save()
    api_client.force_authenticate(user=user)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.LOGISTICS_AGENT.value)

    voyage, contract = voyage_and_contract
    doc = ShippingDocument(
        document_type=DocumentType.CHARTER_PARTY.value,
        document_title="Charter Party Already Verified",
        voyage=voyage,
        original_filename="cp_verified.pdf",
        file_extension=".pdf",
        file_size_bytes=1024,
        mime_type="application/pdf",
        minio_bucket="shipping-documents",
        minio_object_key="CHARTER_PARTY/2026/10/v400-test/cp_verified.pdf",
        sha256_hash_client="e" * 64,
        sha256_hash_server="e" * 64,
        verification_status=DocumentVerificationStatus.HASH_VERIFIED.value,
        verified_at=datetime.datetime.now(UTC),
    )
    doc.save(skip_clean=True)

    url = f"/api/v1/documents/{doc.document_id}/verify/"
    response = api_client.post(url)

    assert response.status_code == drf_status.HTTP_400_BAD_REQUEST
    body = response.json()
    assert body["error"] == "INVALID_STATUS_FOR_VERIFICATION"
    assert "current_status" in body
    assert body["current_status"] == DocumentVerificationStatus.HASH_VERIFIED.value


# ─── Test 17: /verify/ RBAC ───────────────────────────────────────────────────


@pytest.mark.django_db
def test_verify_endpoint_rbac_logistics_allowed_readonly_forbidden(
    voyage_and_contract, api_client
):
    """LOGISTICS_AGENT can POST /verify/, READONLY_VIEWER gets 403."""
    voyage, contract = voyage_and_contract
    payload = b"rbac verify test content"
    server_hash = hashlib.sha256(payload).hexdigest().lower()

    doc = ShippingDocument(
        document_type=DocumentType.CUSTOMS_ENTRY.value,
        document_title="Customs Entry RBAC Test",
        voyage=voyage,
        original_filename="customs_rbac.pdf",
        file_extension=".pdf",
        file_size_bytes=len(payload),
        mime_type="application/pdf",
        minio_bucket="shipping-documents",
        minio_object_key="CUSTOMS_ENTRY/2026/10/rbac-test/customs_rbac.pdf",
        sha256_hash_client=server_hash,
        verification_status=DocumentVerificationStatus.UPLOAD_COMPLETE.value,
    )
    doc.save(skip_clean=True)
    dest = DocumentVaultService.get_storage_path(doc.minio_bucket, doc.minio_object_key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)

    url = f"/api/v1/documents/{doc.document_id}/verify/"

    # LOGISTICS_AGENT → allowed
    agent_user = _make_user(None, UserRole.LOGISTICS_AGENT.value, suffix="rbac1")
    agent_user.save()
    api_client.force_authenticate(user=agent_user)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.LOGISTICS_AGENT.value)
    response = api_client.post(url)
    assert response.status_code == drf_status.HTTP_202_ACCEPTED

    # Re-set to UPLOAD_COMPLETE for the next attempt
    doc.verification_status = DocumentVerificationStatus.UPLOAD_COMPLETE.value
    doc.sha256_hash_server = None
    doc.verified_at = None
    doc.save(skip_clean=True)

    # READONLY_VIEWER → forbidden
    readonly_user = _make_user(None, UserRole.READONLY.value, suffix="rbac2")
    readonly_user.save()
    api_client.force_authenticate(user=readonly_user)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.READONLY.value)
    response = api_client.post(url)
    assert response.status_code == drf_status.HTTP_403_FORBIDDEN


# ─── Test 18: worker execution drives DB to HASH_VERIFIED ─────────────────────


@pytest.mark.django_db
def test_worker_end_to_end_drives_db_to_hash_verified(voyage_and_contract):
    """Full worker execution: task runs → DB row has HASH_VERIFIED + server hash + verified_at."""
    payload = b"end-to-end verified document payload for Cycle 2.4.4"
    expected_hash = hashlib.sha256(payload).hexdigest().lower()

    voyage, contract = voyage_and_contract
    doc = ShippingDocument(
        document_type=DocumentType.WEIGHT_CERTIFICATE.value,
        document_title="Weight Certificate E2E",
        voyage=voyage,
        original_filename="weight_cert_e2e.pdf",
        file_extension=".pdf",
        file_size_bytes=len(payload),
        mime_type="application/pdf",
        minio_bucket="shipping-documents",
        minio_object_key="WEIGHT_CERTIFICATE/2026/10/e2e-verified/weight_cert_e2e.pdf",
        sha256_hash_client=expected_hash,
        verification_status=DocumentVerificationStatus.UPLOAD_COMPLETE.value,
    )
    doc.save(skip_clean=True)
    dest = DocumentVaultService.get_storage_path(doc.minio_bucket, doc.minio_object_key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)

    verify_document_async.delay(str(doc.document_id)).get()

    doc.refresh_from_db()
    assert doc.verification_status == DocumentVerificationStatus.HASH_VERIFIED.value
    assert doc.sha256_hash_server == expected_hash
    assert doc.verified_at is not None
    assert doc.verification_mismatch_detail is None


# ─── Test 19: worker execution drives DB to HASH_MISMATCH ─────────────────────


@pytest.mark.django_db
def test_worker_end_to_end_drives_db_to_hash_mismatch(voyage_and_contract):
    """Full worker execution with mismatch: DB row has HASH_MISMATCH + detail recorded."""
    real_payload = b"real payload that differs from client expectation"
    real_hash = hashlib.sha256(real_payload).hexdigest().lower()
    fake_hash = "0" * 64

    voyage, contract = voyage_and_contract
    doc = ShippingDocument(
        document_type=DocumentType.QUALITY_CERTIFICATE.value,
        document_title="Quality Certificate E2E Mismatch",
        voyage=voyage,
        original_filename="quality_cert_mismatch.pdf",
        file_extension=".pdf",
        file_size_bytes=len(real_payload),
        mime_type="application/pdf",
        minio_bucket="shipping-documents",
        minio_object_key="QUALITY_CERTIFICATE/2026/10/e2e-mismatch/quality_cert_mismatch.pdf",
        sha256_hash_client=fake_hash,
        verification_status=DocumentVerificationStatus.UPLOAD_COMPLETE.value,
    )
    doc.save(skip_clean=True)
    dest = DocumentVaultService.get_storage_path(doc.minio_bucket, doc.minio_object_key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(real_payload)

    verify_document_async.delay(str(doc.document_id)).get()

    doc.refresh_from_db()
    assert doc.verification_status == DocumentVerificationStatus.HASH_MISMATCH.value
    assert doc.sha256_hash_server == real_hash
    assert fake_hash in doc.verification_mismatch_detail
    assert real_hash in doc.verification_mismatch_detail
    assert doc.verified_at is None


# ─── Test 20: Celery task names registered under documents queue ──────────────


@pytest.mark.django_db
def test_celery_task_names_registered_for_documents_queue(settings):
    """All documents tasks are declared and routed to the 'documents' queue in settings."""
    routing = getattr(settings, "CELERY_TASK_ROUTES", {})

    # Verify routing exists for the documents task module
    documents_routes = {
        k: v for k, v in routing.items() if "documents" in k
    }
    # At minimum the documents wildcard pattern should be present
    assert any("documents" in k for k in routing.keys()), (
        "No routing entry found for 'apps.documents.tasks.*' in CELERY_TASK_ROUTES"
    )

    # Confirm task names are accessible and callable
    assert verify_document_async.name == "apps.documents.tasks.verify_document_async"
    assert quarantine_mismatched_document_async.name == (
        "apps.documents.tasks.quarantine_mismatched_document_async"
    )
    assert ping_document_worker.name == "apps.documents.tasks.ping_document_worker"
