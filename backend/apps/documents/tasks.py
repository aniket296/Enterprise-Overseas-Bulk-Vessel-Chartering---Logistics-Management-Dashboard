"""
backend/apps/documents/tasks.py
============================================================
Celery tasks routed to the dedicated 'documents' queue.
Handles background SHA-256 digest computation, MinIO S3 sync, and deduplication.
"""

from celery import shared_task
from django.utils import timezone
from apps.documents.models import ShippingDocument, DocumentVerificationStatus
from apps.documents.vault_service import DocumentVaultService


@shared_task(name="apps.documents.tasks.ping_document_worker")
def ping_document_worker():
    """Diagnostic task confirming documents worker queue liveness."""
    return {
        "queue": "documents",
        "status": "ONLINE",
        "timestamp": timezone.now().isoformat(),
    }


@shared_task(name="apps.documents.tasks.verify_document_hash_async")
def verify_document_hash_async(document_id: str):
    """
    Background cryptographic digest verification for uploaded shipping documents.
    """
    try:
        doc = ShippingDocument.objects.get(document_id=document_id)
        payload = DocumentVaultService.read_file_payload(doc)
        import hashlib
        server_hash = hashlib.sha256(payload).hexdigest().lower()

        if doc.sha256_hash_client and doc.sha256_hash_client.lower() != server_hash:
            doc.verification_status = DocumentVerificationStatus.HASH_MISMATCH.value
            doc.verification_mismatch_detail = (
                f"Client hash '{doc.sha256_hash_client}' does not match server digest '{server_hash}'."
            )
        else:
            doc.verification_status = DocumentVerificationStatus.HASH_VERIFIED.value
            doc.sha256_hash_server = server_hash
            doc.verified_at = timezone.now()

        doc.save()
        return {
            "document_id": str(doc.document_id),
            "verification_status": doc.verification_status,
            "sha256_hash_server": doc.sha256_hash_server,
        }
    except Exception as exc:
        return {
            "document_id": document_id,
            "error": str(exc),
            "status": "FAILED",
        }
