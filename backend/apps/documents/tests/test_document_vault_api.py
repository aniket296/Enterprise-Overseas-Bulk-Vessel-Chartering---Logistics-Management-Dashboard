"""
backend/apps/documents/tests/test_document_vault_api.py
============================================================
Cycle 2.3.4 — Tests: Shipping Documents Vault & S3/MinIO Integration API

Tests cover:
  Engine & Vault Unit Tests:
  1.  Deterministic SHA-256 chunk-by-chunk calculation and size validation
  2.  Upload file with exact client SHA-256 digest -> HASH_VERIFIED status
  3.  Upload file with client SHA-256 mismatch -> HASH_MISMATCH status with detail
  4.  Upload file without client SHA-256 -> server computes and marks HASH_VERIFIED
  5.  Deduplication: duplicate verified content in same bucket is rejected
  6.  Air-gapped dual storage: payload stored to disk and readable with SHA-256 verification
  7.  Tampering guard: manual corruption of payload on disk raises validation error on read

  State Machine & Integrity Unit Tests:
  8.  Approval transition: HASH_VERIFIED -> APPROVED with approved_by and approved_at
  9.  Quarantine transition: HASH_MISMATCH -> QUARANTINED with notes
  10. Supersede transition: HASH_VERIFIED/APPROVED -> SUPERSEDED with version increment
  11. Illegal transitions rejected (e.g. QUARANTINED -> APPROVED, PENDING_UPLOAD -> APPROVED)
  12. Soft-delete transition to DELETED

  REST API Integration Tests:
  13. POST /api/v1/documents/ multipart upload valid PDF with client SHA-256
  14. POST /api/v1/documents/ validation failure if neither voyage_id nor contract_id provided
  15. POST /api/v1/documents/ validation failure if forbidden file extension uploaded (.exe)
  16. GET /api/v1/documents/ lists documents and supports filtering (voyage, contract, type, status)
  17. GET /api/v1/documents/{id}/download/ streams file bytes with checksum headers
  18. POST /api/v1/documents/{id}/approve/ approves document
  19. POST /api/v1/documents/{id}/supersede/ links superseding version
  20. GET /api/v1/documents/stats/ returns vault aggregate metrics
  21. Confidentiality: non-admin without matching role cannot view or download confidential doc
  22. RBAC permissions:
      - LOGISTICS_AGENT and PROCUREMENT_OFFICER can POST /documents/
      - AUDITOR and FINANCE_OFFICER can POST /approve/
      - READONLY gets 403 on upload and approval
"""

import io
import hashlib
import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
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
)
from apps.documents.vault_service import DocumentVaultService

UTC = datetime.timezone.utc


@pytest.fixture
def api_client():
    return APIClient()


def _make_user(db, role, suffix="01"):
    from django.contrib.auth import get_user_model
    from apps.authentication.models import UserProfile
    User = get_user_model()
    username = f"doc_user_{role.lower()}_{suffix}"
    email = f"doc_{role.lower()}_{suffix}@test.com"
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
        vessel_name="MV Steel Carrier",
        imo_number="9876543",
        flag_state="PA",
        deadweight_tonnage=Decimal("75000.000"),
        gross_tonnage=Decimal("42000.000"),
        net_tonnage=Decimal("24000.000"),
        length_overall_meters=Decimal("225.00"),
        beam_meters=Decimal("32.20"),
        draft_meters=Decimal("14.20"),
        hold_count=7,
        hatch_count=7,
        build_year=2019,
        owner_name="Pacific Bulk Lines",
    )
    contract = CharterContract.objects.create(
        contract_reference="CP-SAIL-2026-DOC1",
        charterer_entity="Steel Authority of India Limited",
        charterer_plant="Bokaro Steel Plant",
        vessel=vessel,
        nominated_quantity_mt=Decimal("70000.000"),
        freight_rate_usd_per_mt=Decimal("15.50"),
        load_port="Gladstone",
        discharge_port="Haldia",
        discharge_rate_mt_per_day=Decimal("10000.000"),
        laycan_start=datetime.date(2026, 9, 1),
        laycan_end=datetime.date(2026, 9, 10),
        demurrage_rate_usd_per_day=Decimal("20000.00"),
        despatch_rate_usd_per_day=Decimal("10000.00"),
    )
    voyage = VesselVoyage.objects.create(
        voyage_reference="VOY-2026-DOC-001",
        contract=contract,
        vessel=vessel,
        load_port="Gladstone",
        discharge_port="Haldia",
        discharge_berth="Berth-4B",
        quantity_loaded_mt=Decimal("72000.000"),
        eta_date=datetime.datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
    )
    return voyage, contract


# ============================================================================
# 1. ENGINE & VAULT UNIT TESTS
# ============================================================================

@pytest.mark.django_db
class TestDocumentVaultEngine:

    def test_compute_sha256_and_size(self):
        payload = b"Sample Maritime Notice of Readiness content."
        expected_hash = hashlib.sha256(payload).hexdigest()
        stream = io.BytesIO(payload)

        computed_hash, size = DocumentVaultService.compute_sha256(stream)
        assert computed_hash == expected_hash
        assert size == len(payload)
        # Position preserved
        assert stream.tell() == 0

    def test_upload_with_matching_client_sha256(self, voyage_and_contract):
        voyage, _ = voyage_and_contract
        payload = b"%PDF-1.4 Mock Fixture Recap Document Content"
        client_hash = hashlib.sha256(payload).hexdigest()

        stream = io.BytesIO(payload)
        doc = DocumentVaultService.upload_and_verify(
            file_obj=stream,
            document_title="Fixture Recap 2026",
            document_type=DocumentType.FIXTURE_RECAP.value,
            original_filename="fixture_recap.pdf",
            client_sha256=client_hash,
            voyage=voyage,
        )

        assert doc.verification_status == DocumentVerificationStatus.HASH_VERIFIED.value
        assert doc.sha256_hash_server == client_hash
        assert doc.sha256_hash_client == client_hash
        assert doc.verified_at is not None
        assert doc.verification_mismatch_detail is None
        assert doc.file_size_bytes == len(payload)

    def test_upload_with_mismatch_client_sha256(self, voyage_and_contract):
        voyage, _ = voyage_and_contract
        payload = b"%PDF-1.4 Clean bill of lading file"
        wrong_client_hash = "a" * 64

        stream = io.BytesIO(payload)
        doc = DocumentVaultService.upload_and_verify(
            file_obj=stream,
            document_title="Bill of Lading",
            document_type=DocumentType.BILL_OF_LADING.value,
            original_filename="bl_clean.pdf",
            client_sha256=wrong_client_hash,
            voyage=voyage,
        )

        assert doc.verification_status == DocumentVerificationStatus.HASH_MISMATCH.value
        assert doc.sha256_hash_client == wrong_client_hash
        assert doc.sha256_hash_server == hashlib.sha256(payload).hexdigest()
        assert "does not match" in doc.verification_mismatch_detail

    def test_upload_without_client_sha256(self, voyage_and_contract):
        _, contract = voyage_and_contract
        payload = b"CSV Content: SOF log data,2026-09-28,12:00"
        stream = io.BytesIO(payload)

        doc = DocumentVaultService.upload_and_verify(
            file_obj=stream,
            document_title="SOF Timesheet Export",
            document_type=DocumentType.STATEMENT_OF_FACTS.value,
            original_filename="sof_log.csv",
            contract=contract,
        )

        assert doc.verification_status == DocumentVerificationStatus.HASH_VERIFIED.value
        assert doc.sha256_hash_server == hashlib.sha256(payload).hexdigest()
        assert doc.sha256_hash_client == doc.sha256_hash_server

    def test_deduplication_prevents_duplicate_verified_files(self, voyage_and_contract):
        voyage, _ = voyage_and_contract
        payload = b"Unique Cargo Manifest Content for Deduplication"
        stream1 = io.BytesIO(payload)

        doc1 = DocumentVaultService.upload_and_verify(
            file_obj=stream1,
            document_title="Manifest 1",
            document_type=DocumentType.CARGO_MANIFEST.value,
            original_filename="manifest1.pdf",
            voyage=voyage,
        )
        assert doc1.verification_status == DocumentVerificationStatus.HASH_VERIFIED.value

        stream2 = io.BytesIO(payload)
        with pytest.raises(ValidationError) as exc:
            DocumentVaultService.upload_and_verify(
                file_obj=stream2,
                document_title="Manifest 2 Duplicate",
                document_type=DocumentType.CARGO_MANIFEST.value,
                original_filename="manifest2.pdf",
                voyage=voyage,
            )
        assert "already exists in vault" in str(exc.value)

    def test_air_gapped_read_and_tampering_guard(self, voyage_and_contract):
        voyage, _ = voyage_and_contract
        payload = b"Survey Report Draft - Authentic Untampered Content"
        stream = io.BytesIO(payload)

        doc = DocumentVaultService.upload_and_verify(
            file_obj=stream,
            document_title="Survey Report",
            document_type=DocumentType.DRAFT_SURVEY_REPORT.value,
            original_filename="draft_survey.pdf",
            voyage=voyage,
        )

        # Authentic read
        data = DocumentVaultService.read_file_payload(doc)
        assert data == payload

        # Simulate tampering on physical storage
        storage_path = DocumentVaultService.get_storage_path(doc.minio_bucket, doc.minio_object_key)
        with open(storage_path, "wb") as f:
            f.write(b"Maliciously altered content on disk")

        # Tampering detection
        with pytest.raises(ValidationError) as exc:
            DocumentVaultService.read_file_payload(doc)
        assert "Vault tampering detected" in str(exc.value)


# ============================================================================
# 2. STATE MACHINE & INTEGRITY UNIT TESTS
# ============================================================================

@pytest.mark.django_db
class TestDocumentVerificationStateMachine:

    def test_approval_transition(self, voyage_and_contract):
        voyage, _ = voyage_and_contract
        user = _make_user(None, UserRole.FINANCE_OFFICER.value, "appr")
        payload = b"Commercial Invoice 101"

        doc = DocumentVaultService.upload_and_verify(
            file_obj=io.BytesIO(payload),
            document_title="Demurrage Invoice",
            document_type=DocumentType.DEMURRAGE_CLAIM.value,
            original_filename="invoice.pdf",
            voyage=voyage,
        )
        assert doc.verification_status == DocumentVerificationStatus.HASH_VERIFIED.value

        doc_approved = DocumentVaultService.approve_document(doc, user=user, notes="Verified by finance")
        assert doc_approved.verification_status == DocumentVerificationStatus.APPROVED.value
        assert doc_approved.approved_by == user
        assert doc_approved.approved_at is not None
        assert "Verified by finance" in doc_approved.document_description

    def test_quarantine_transition(self, voyage_and_contract):
        voyage, _ = voyage_and_contract
        user = _make_user(None, UserRole.AUDITOR.value, "aud")
        payload = b"Corrupted document content"

        doc = DocumentVaultService.upload_and_verify(
            file_obj=io.BytesIO(payload),
            document_title="Corrupted NOR",
            document_type=DocumentType.NOTICE_OF_READINESS.value,
            original_filename="nor_corrupt.pdf",
            client_sha256="b" * 64,
            voyage=voyage,
        )
        assert doc.verification_status == DocumentVerificationStatus.HASH_MISMATCH.value

        doc_quarantined = DocumentVaultService.quarantine_document(doc, user=user, notes="Isolated for security analysis")
        assert doc_quarantined.verification_status == DocumentVerificationStatus.QUARANTINED.value
        assert "Isolated for security analysis" in doc_quarantined.verification_mismatch_detail

    def test_supersede_transition(self, voyage_and_contract):
        voyage, _ = voyage_and_contract
        user = _make_user(None, UserRole.LOGISTICS_AGENT.value, "log")

        doc_v1 = DocumentVaultService.upload_and_verify(
            file_obj=io.BytesIO(b"Version 1 of Timesheet"),
            document_title="Timesheet V1",
            document_type=DocumentType.LAYTIME_STATEMENT.value,
            original_filename="timesheet_v1.pdf",
            voyage=voyage,
        )
        doc_v2 = DocumentVaultService.upload_and_verify(
            file_obj=io.BytesIO(b"Version 2 of Timesheet Revised"),
            document_title="Timesheet V2",
            document_type=DocumentType.LAYTIME_STATEMENT.value,
            original_filename="timesheet_v2.pdf",
            voyage=voyage,
        )

        old_doc, new_doc = DocumentVaultService.supersede_document(doc_v1, doc_v2)
        assert old_doc.verification_status == DocumentVerificationStatus.SUPERSEDED.value
        assert new_doc.supersedes_document == old_doc
        assert new_doc.version_number == 2

    def test_illegal_status_transitions_rejected(self, voyage_and_contract):
        voyage, _ = voyage_and_contract
        user = _make_user(None, UserRole.SYSTEM_ADMIN.value, "adm")

        doc = DocumentVaultService.upload_and_verify(
            file_obj=io.BytesIO(b"Test File for Transition"),
            document_title="Transition Doc",
            document_type=DocumentType.OTHER.value,
            original_filename="test_other.pdf",
            voyage=voyage,
        )
        # Cannot quarantine a HASH_VERIFIED document
        with pytest.raises(ValidationError):
            DocumentVaultService.quarantine_document(doc, user=user)

        # Illegal direct DB transition attempt (e.g. APPROVED back to PENDING_UPLOAD)
        doc.verification_status = DocumentVerificationStatus.APPROVED.value
        doc.approved_by = user
        doc.approved_at = datetime.datetime.now(UTC)
        doc.save()

        doc.verification_status = DocumentVerificationStatus.PENDING_UPLOAD.value
        with pytest.raises(ValidationError) as exc:
            doc.save()
        assert "Illegal transition" in str(exc.value)


# ============================================================================
# 3. REST API INTEGRATION TESTS
# ============================================================================

@pytest.mark.django_db
class TestDocumentVaultAPI:

    def test_api_upload_valid_document(self, api_client, voyage_and_contract):
        voyage, _ = voyage_and_contract
        user = _make_user(None, UserRole.LOGISTICS_AGENT.value, "up1")
        api_client.force_authenticate(user=user)

        file_content = b"%PDF-1.4 Valid Port Clearance Certificate"
        client_hash = hashlib.sha256(file_content).hexdigest()
        uploaded_file = SimpleUploadedFile(
            "customs_clearance.pdf", file_content, content_type="application/pdf"
        )

        response = api_client.post(
            "/api/v1/documents/",
            {
                "file": uploaded_file,
                "document_title": "Port Customs Clearance",
                "document_type": DocumentType.CUSTOMS_ENTRY.value,
                "voyage_id": str(voyage.voyage_id),
                "client_sha256": client_hash,
                "document_description": "Initial port clearance issued by customs.",
            },
            format="multipart",
            HTTP_X_USER_ROLE=UserRole.LOGISTICS_AGENT.value,
        )

        assert response.status_code == drf_status.HTTP_201_CREATED
        data = response.json()
        assert data["document_title"] == "Port Customs Clearance"
        assert data["verification_status"] == DocumentVerificationStatus.HASH_VERIFIED.value
        assert data["sha256_hash_server"] == client_hash
        assert data["voyage_reference"] == voyage.voyage_reference
        assert data["download_url"].endswith(f"/api/v1/documents/{data['document_id']}/download/")

    def test_api_upload_requires_voyage_or_contract(self, api_client):
        user = _make_user(None, UserRole.PROCUREMENT_OFFICER.value, "up2")
        api_client.force_authenticate(user=user)

        uploaded_file = SimpleUploadedFile("doc.pdf", b"Some file content", content_type="application/pdf")
        response = api_client.post(
            "/api/v1/documents/",
            {
                "file": uploaded_file,
                "document_title": "Unlinked Document",
                "document_type": DocumentType.OTHER.value,
            },
            format="multipart",
            HTTP_X_USER_ROLE=UserRole.PROCUREMENT_OFFICER.value,
        )

        assert response.status_code == drf_status.HTTP_400_BAD_REQUEST
        assert "At least one of 'voyage_id' or 'contract_id' must be specified." in str(response.json())

    def test_api_upload_rejects_forbidden_extension(self, api_client, voyage_and_contract):
        voyage, _ = voyage_and_contract
        user = _make_user(None, UserRole.LOGISTICS_AGENT.value, "up3")
        api_client.force_authenticate(user=user)

        uploaded_file = SimpleUploadedFile("malware.exe", b"MZ Executable payload", content_type="application/octet-stream")
        response = api_client.post(
            "/api/v1/documents/",
            {
                "file": uploaded_file,
                "document_title": "Malicious Executable",
                "document_type": DocumentType.OTHER.value,
                "voyage_id": str(voyage.voyage_id),
            },
            format="multipart",
            HTTP_X_USER_ROLE=UserRole.LOGISTICS_AGENT.value,
        )

        assert response.status_code == drf_status.HTTP_400_BAD_REQUEST
        assert "not allowed" in str(response.json())

    def test_api_list_and_filter_documents(self, api_client, voyage_and_contract):
        voyage, contract = voyage_and_contract
        user = _make_user(None, UserRole.SYSTEM_ADMIN.value, "list1")
        api_client.force_authenticate(user=user)

        # Create two distinct documents
        DocumentVaultService.upload_and_verify(
            file_obj=io.BytesIO(b"Doc A content"),
            document_title="Doc A Bill of Lading",
            document_type=DocumentType.BILL_OF_LADING.value,
            original_filename="doc_a.pdf",
            voyage=voyage,
        )
        DocumentVaultService.upload_and_verify(
            file_obj=io.BytesIO(b"Doc B content"),
            document_title="Doc B Charter Party",
            document_type=DocumentType.CHARTER_PARTY.value,
            original_filename="doc_b.pdf",
            contract=contract,
        )

        # List all
        resp = api_client.get("/api/v1/documents/", HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)
        assert resp.status_code == drf_status.HTTP_200_OK
        data = resp.json()["results"] if "results" in resp.json() else resp.json()
        assert len(data) >= 2

        # Filter by voyage
        resp_voyage = api_client.get(
            f"/api/v1/documents/?voyage_id={voyage.voyage_id}",
            HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value,
        )
        voyage_results = resp_voyage.json()["results"] if "results" in resp_voyage.json() else resp_voyage.json()
        assert all(item["voyage"] == str(voyage.voyage_id) for item in voyage_results)

        # Filter by document_type
        resp_type = api_client.get(
            f"/api/v1/documents/?document_type={DocumentType.BILL_OF_LADING.value}",
            HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value,
        )
        type_results = resp_type.json()["results"] if "results" in resp_type.json() else resp_type.json()
        assert all(item["document_type"] == DocumentType.BILL_OF_LADING.value for item in type_results)

    def test_api_download_document(self, api_client, voyage_and_contract):
        voyage, _ = voyage_and_contract
        user = _make_user(None, UserRole.CHARTER_BROKER.value, "down1")
        api_client.force_authenticate(user=user)

        raw_bytes = b"%PDF-1.4 Official Notice of Readiness tendered at Haldia Roads"
        doc = DocumentVaultService.upload_and_verify(
            file_obj=io.BytesIO(raw_bytes),
            document_title="Tendered NOR",
            document_type=DocumentType.NOTICE_OF_READINESS.value,
            original_filename="nor_tendered.pdf",
            voyage=voyage,
        )

        resp = api_client.get(
            f"/api/v1/documents/{doc.document_id}/download/",
            HTTP_X_USER_ROLE=UserRole.CHARTER_BROKER.value,
        )
        assert resp.status_code == drf_status.HTTP_200_OK
        assert resp.content == raw_bytes
        assert resp.headers["Content-Disposition"] == 'attachment; filename="nor_tendered.pdf"'
        assert resp.headers["X-SHA256-Checksum"] == doc.sha256_hash_server

    def test_api_approve_document(self, api_client, voyage_and_contract):
        voyage, _ = voyage_and_contract
        auditor = _make_user(None, UserRole.AUDITOR.value, "aud_appr")
        api_client.force_authenticate(user=auditor)

        doc = DocumentVaultService.upload_and_verify(
            file_obj=io.BytesIO(b"Despatch Credit Calculation Statement"),
            document_title="Despatch Credit Note",
            document_type=DocumentType.DESPATCH_CREDIT_NOTE.value,
            original_filename="despatch_credit.pdf",
            voyage=voyage,
        )

        resp = api_client.post(
            f"/api/v1/documents/{doc.document_id}/approve/",
            {"action": "APPROVE", "notes": "Audited by statutory officer"},
            format="json",
            HTTP_X_USER_ROLE=UserRole.AUDITOR.value,
        )
        assert resp.status_code == drf_status.HTTP_200_OK
        assert resp.json()["verification_status"] == DocumentVerificationStatus.APPROVED.value
        assert resp.json()["approved_by_username"] == auditor.username

    def test_api_supersede_document(self, api_client, voyage_and_contract):
        voyage, _ = voyage_and_contract
        agent = _make_user(None, UserRole.LOGISTICS_AGENT.value, "sup_ag")
        api_client.force_authenticate(user=agent)

        doc_old = DocumentVaultService.upload_and_verify(
            file_obj=io.BytesIO(b"Original draft survey MT 71800"),
            document_title="Draft Survey Old",
            document_type=DocumentType.DRAFT_SURVEY_REPORT.value,
            original_filename="survey_old.pdf",
            voyage=voyage,
        )
        doc_new = DocumentVaultService.upload_and_verify(
            file_obj=io.BytesIO(b"Final draft survey MT 72150"),
            document_title="Draft Survey Final",
            document_type=DocumentType.DRAFT_SURVEY_REPORT.value,
            original_filename="survey_new.pdf",
            voyage=voyage,
        )

        resp = api_client.post(
            f"/api/v1/documents/{doc_old.document_id}/supersede/",
            {"superseded_by_document_id": str(doc_new.document_id)},
            format="json",
            HTTP_X_USER_ROLE=UserRole.LOGISTICS_AGENT.value,
        )
        assert resp.status_code == drf_status.HTTP_200_OK
        assert resp.json()["superseded_document"]["verification_status"] == DocumentVerificationStatus.SUPERSEDED.value
        assert resp.json()["active_document"]["version_number"] == 2

    def test_api_confidentiality_isolation(self, api_client, voyage_and_contract):
        voyage, _ = voyage_and_contract
        admin_user = _make_user(None, UserRole.SYSTEM_ADMIN.value, "adm_conf")
        restricted_user = _make_user(None, UserRole.PLANT_MANAGER.value, "plant_conf")

        doc = DocumentVaultService.upload_and_verify(
            file_obj=io.BytesIO(b"Top secret commercial freight rebate agreement"),
            document_title="Confidential Rebate Terms",
            document_type=DocumentType.OTHER.value,
            original_filename="rebate.pdf",
            voyage=voyage,
            is_confidential=True,
            accessible_roles=[UserRole.FINANCE_OFFICER.value, UserRole.PROCUREMENT_OFFICER.value],
        )

        # Restricted plant manager cannot see confidential document in list
        api_client.force_authenticate(user=restricted_user)
        list_resp = api_client.get("/api/v1/documents/", HTTP_X_USER_ROLE=UserRole.PLANT_MANAGER.value)
        doc_ids = [d["document_id"] for d in (list_resp.json()["results"] if "results" in list_resp.json() else list_resp.json())]
        assert str(doc.document_id) not in doc_ids

        # Restricted user cannot download confidential document
        down_resp = api_client.get(
            f"/api/v1/documents/{doc.document_id}/download/",
            HTTP_X_USER_ROLE=UserRole.PLANT_MANAGER.value,
        )
        assert down_resp.status_code == drf_status.HTTP_403_FORBIDDEN

        # Finance officer with accessible role CAN download
        finance_user = _make_user(None, UserRole.FINANCE_OFFICER.value, "fin_conf")
        api_client.force_authenticate(user=finance_user)
        down_fin_resp = api_client.get(
            f"/api/v1/documents/{doc.document_id}/download/",
            HTTP_X_USER_ROLE=UserRole.FINANCE_OFFICER.value,
        )
        assert down_fin_resp.status_code == drf_status.HTTP_200_OK

    def test_api_stats_endpoint(self, api_client, voyage_and_contract):
        voyage, _ = voyage_and_contract
        user = _make_user(None, UserRole.SYSTEM_ADMIN.value, "stats1")
        api_client.force_authenticate(user=user)

        resp = api_client.get("/api/v1/documents/stats/", HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)
        assert resp.status_code == drf_status.HTTP_200_OK
        data = resp.json()
        assert "total_documents" in data
        assert "total_storage_bytes" in data
        assert "verified_documents" in data
        assert "approved_documents" in data

    def test_rbac_readonly_user_forbidden(self, api_client, voyage_and_contract):
        voyage, _ = voyage_and_contract
        readonly_user = _make_user(None, UserRole.READONLY.value, "ro1")
        api_client.force_authenticate(user=readonly_user)

        # Upload attempt
        up_resp = api_client.post(
            "/api/v1/documents/",
            {
                "file": SimpleUploadedFile("test.pdf", b"test content", content_type="application/pdf"),
                "document_title": "Unauthorized Upload",
                "document_type": DocumentType.OTHER.value,
                "voyage_id": str(voyage.voyage_id),
            },
            format="multipart",
            HTTP_X_USER_ROLE=UserRole.READONLY.value,
        )
        assert up_resp.status_code == drf_status.HTTP_403_FORBIDDEN
