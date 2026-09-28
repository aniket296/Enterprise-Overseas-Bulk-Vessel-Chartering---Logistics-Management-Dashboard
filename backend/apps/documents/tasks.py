"""
backend/apps/documents/tasks.py
============================================================
Celery tasks routed to the dedicated 'documents' queue.
Handles background SHA-256 digest computation, MinIO/S3 vault synchronization,
and deterministic document verification state machine transitions.

State machine driven by this worker:
  UPLOAD_COMPLETE  →  HASH_VERIFIED   (client hash matches OR no client hash provided)
  UPLOAD_COMPLETE  →  HASH_MISMATCH   (client hash supplied but differs from server digest)
  HASH_MISMATCH    →  QUARANTINED     (automatic quarantine on repeated failure / explicit trigger)
"""

import hashlib
import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.documents.models import ShippingDocument, DocumentVerificationStatus
from apps.documents.vault_service import DocumentVaultService

logger = logging.getLogger(__name__)


@shared_task(name="apps.documents.tasks.ping_document_worker")
def ping_document_worker():
    """Diagnostic task confirming documents worker queue liveness."""
    return {
        "queue": "documents",
        "status": "ONLINE",
        "timestamp": timezone.now().isoformat(),
    }


@shared_task(
    bind=True,
    name="apps.documents.tasks.verify_document_async",
    queue="documents",
    max_retries=3,
    default_retry_delay=30,
)
def verify_document_async(self, document_id: str):
    """
    Background cryptographic digest verification for uploaded shipping documents.

    Workflow:
      1. Lock the ShippingDocument row with SELECT FOR UPDATE.
      2. Guard: only process UPLOAD_COMPLETE documents (idempotent skip for all others).
      3. Read raw file payload from the air-gapped vault / MinIO storage.
      4. Compute SHA-256 server-side digest (chunk-by-chunk, deterministic).
      5. Compare against stored client digest (sha256_hash_client).
      6. Persist server digest and transition status inside a single atomic transaction:
           • Match (or no client hash) → HASH_VERIFIED  + verified_at timestamp
           • Mismatch              → HASH_MISMATCH   + verification_mismatch_detail
      7. Return a structured result dict that Celery stores as task result.

    Args:
        document_id: UUID string of the ShippingDocument to verify.

    Returns:
        dict: {document_id, verification_status, sha256_hash_server, outcome_reason}
    """
    try:
        with transaction.atomic():
            try:
                doc = ShippingDocument.objects.select_for_update().get(
                    document_id=document_id
                )
            except ShippingDocument.DoesNotExist:
                logger.error(
                    "verify_document_async: document %s not found — aborting.", document_id
                )
                return {
                    "document_id": document_id,
                    "error": "DOCUMENT_NOT_FOUND",
                    "status": "FAILED",
                }

            # Idempotency guard: only verify UPLOAD_COMPLETE documents
            if doc.verification_status != DocumentVerificationStatus.UPLOAD_COMPLETE.value:
                logger.info(
                    "verify_document_async: document %s is in status '%s' — skipping "
                    "(only UPLOAD_COMPLETE documents are processed).",
                    document_id,
                    doc.verification_status,
                )
                return {
                    "document_id": document_id,
                    "verification_status": doc.verification_status,
                    "outcome_reason": "SKIPPED_NOT_UPLOAD_COMPLETE",
                }

            # Read raw payload from vault storage
            try:
                payload = DocumentVaultService.read_file_payload(doc)
            except FileNotFoundError as exc:
                logger.error(
                    "verify_document_async: vault payload not found for document %s: %s",
                    document_id,
                    exc,
                )
                return {
                    "document_id": document_id,
                    "error": "VAULT_PAYLOAD_NOT_FOUND",
                    "detail": str(exc),
                    "status": "FAILED",
                }

            # Deterministic SHA-256 computation
            server_hash = hashlib.sha256(payload).hexdigest().lower()

            client_hash = (doc.sha256_hash_client or "").strip().lower()

            if client_hash and client_hash != server_hash:
                # ── HASH MISMATCH ─────────────────────────────────────────────
                mismatch_detail = (
                    f"Client digest '{client_hash}' does not match "
                    f"server-computed digest '{server_hash}' for document "
                    f"'{doc.original_filename}' (size: {doc.file_size_bytes} bytes)."
                )
                doc.verification_status = DocumentVerificationStatus.HASH_MISMATCH.value
                doc.sha256_hash_server = server_hash
                doc.verification_mismatch_detail = mismatch_detail
                doc.save(skip_clean=False)

                logger.warning(
                    "verify_document_async: HASH_MISMATCH for document %s — %s",
                    document_id,
                    mismatch_detail,
                )
                return {
                    "document_id": document_id,
                    "verification_status": DocumentVerificationStatus.HASH_MISMATCH.value,
                    "sha256_hash_server": server_hash,
                    "sha256_hash_client": client_hash,
                    "outcome_reason": "CLIENT_SERVER_DIGEST_MISMATCH",
                    "mismatch_detail": mismatch_detail,
                }
            else:
                # ── HASH VERIFIED ─────────────────────────────────────────────
                doc.verification_status = DocumentVerificationStatus.HASH_VERIFIED.value
                doc.sha256_hash_server = server_hash
                # If no client hash was provided, record the server hash as the canonical reference
                if not client_hash:
                    doc.sha256_hash_client = server_hash
                doc.verified_at = timezone.now()
                doc.verification_mismatch_detail = None
                doc.save(skip_clean=False)

                outcome_reason = (
                    "DIGEST_MATCH" if client_hash else "SERVER_DIGEST_COMPUTED_NO_CLIENT_HASH"
                )
                logger.info(
                    "verify_document_async: HASH_VERIFIED for document %s (reason: %s).",
                    document_id,
                    outcome_reason,
                )
                return {
                    "document_id": document_id,
                    "verification_status": DocumentVerificationStatus.HASH_VERIFIED.value,
                    "sha256_hash_server": server_hash,
                    "verified_at": doc.verified_at.isoformat(),
                    "outcome_reason": outcome_reason,
                }

    except Exception as exc:
        logger.exception(
            "verify_document_async: unexpected error for document %s: %s", document_id, exc
        )
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {
                "document_id": document_id,
                "error": "MAX_RETRIES_EXCEEDED",
                "detail": str(exc),
                "status": "FAILED",
            }


@shared_task(
    bind=True,
    name="apps.documents.tasks.quarantine_mismatched_document_async",
    queue="documents",
    max_retries=2,
    default_retry_delay=15,
)
def quarantine_mismatched_document_async(self, document_id: str, quarantine_note: str = ""):
    """
    Automatically quarantines a HASH_MISMATCH document.

    State machine transition: HASH_MISMATCH → QUARANTINED.

    Args:
        document_id:     UUID string of the ShippingDocument to quarantine.
        quarantine_note: Optional textual justification recorded in mismatch_detail.

    Returns:
        dict: {document_id, verification_status, outcome_reason}
    """
    try:
        with transaction.atomic():
            try:
                doc = ShippingDocument.objects.select_for_update().get(
                    document_id=document_id
                )
            except ShippingDocument.DoesNotExist:
                logger.error(
                    "quarantine_mismatched_document_async: document %s not found.",
                    document_id,
                )
                return {
                    "document_id": document_id,
                    "error": "DOCUMENT_NOT_FOUND",
                    "status": "FAILED",
                }

            # Idempotency guard: only quarantine HASH_MISMATCH documents
            if doc.verification_status != DocumentVerificationStatus.HASH_MISMATCH.value:
                logger.info(
                    "quarantine_mismatched_document_async: document %s is in status '%s' "
                    "— expected HASH_MISMATCH, skipping.",
                    document_id,
                    doc.verification_status,
                )
                return {
                    "document_id": document_id,
                    "verification_status": doc.verification_status,
                    "outcome_reason": "SKIPPED_NOT_HASH_MISMATCH",
                }

            note_prefix = "[AUTO-QUARANTINE by Celery worker]"
            if quarantine_note:
                note_prefix = f"{note_prefix}: {quarantine_note}"

            existing_detail = doc.verification_mismatch_detail or ""
            doc.verification_mismatch_detail = (
                f"{existing_detail}\n{note_prefix}".strip()
            )
            doc.verification_status = DocumentVerificationStatus.QUARANTINED.value
            doc.save(skip_clean=False)

            logger.warning(
                "quarantine_mismatched_document_async: document %s quarantined.", document_id
            )
            return {
                "document_id": document_id,
                "verification_status": DocumentVerificationStatus.QUARANTINED.value,
                "outcome_reason": "HASH_MISMATCH_AUTO_QUARANTINED",
            }

    except Exception as exc:
        logger.exception(
            "quarantine_mismatched_document_async: unexpected error for document %s: %s",
            document_id,
            exc,
        )
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {
                "document_id": document_id,
                "error": "MAX_RETRIES_EXCEEDED",
                "detail": str(exc),
                "status": "FAILED",
            }
