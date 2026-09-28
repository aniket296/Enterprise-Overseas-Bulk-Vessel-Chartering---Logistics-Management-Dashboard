"""
backend/apps/documents/models.py
============================================================
Django Models for Shipping Documents Vault & S3/MinIO Integration.
Maps directly to PostgreSQL 'shipping.shipping_documents'.
"""

import uuid
from typing import Set, Optional
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models


class DocumentType(models.TextChoices):
    """Shipping document classification matching PostgreSQL shipping.document_type."""
    CHARTER_PARTY = "CHARTER_PARTY", "Charter Party Contract"
    FIXTURE_RECAP = "FIXTURE_RECAP", "Fixture Recap"
    BILL_OF_LADING = "BILL_OF_LADING", "Bill of Lading (B/L)"
    NOTICE_OF_READINESS = "NOTICE_OF_READINESS", "Notice of Readiness (NOR)"
    STATEMENT_OF_FACTS = "STATEMENT_OF_FACTS", "Statement of Facts (SOF)"
    LAYTIME_STATEMENT = "LAYTIME_STATEMENT", "Laytime Statement / Timesheet"
    DEMURRAGE_CLAIM = "DEMURRAGE_CLAIM", "Demurrage Claim Invoice"
    DESPATCH_CREDIT_NOTE = "DESPATCH_CREDIT_NOTE", "Despatch Credit Note"
    CARGO_MANIFEST = "CARGO_MANIFEST", "Cargo Manifest"
    DRAFT_SURVEY_REPORT = "DRAFT_SURVEY_REPORT", "Draft Survey Report"
    CUSTOMS_ENTRY = "CUSTOMS_ENTRY", "Customs Entry / Clearance"
    LETTER_OF_INDEMNITY = "LETTER_OF_INDEMNITY", "Letter of Indemnity (LOI)"
    INSURANCE_CERTIFICATE = "INSURANCE_CERTIFICATE", "Marine Cargo Insurance"
    WEIGHT_CERTIFICATE = "WEIGHT_CERTIFICATE", "Shore Scale / Weighbridge Certificate"
    QUALITY_CERTIFICATE = "QUALITY_CERTIFICATE", "Assay / Quality Certificate"
    PHYTOSANITARY_CERTIFICATE = "PHYTOSANITARY_CERTIFICATE", "Phytosanitary Certificate"
    OTHER = "OTHER", "Miscellaneous Document"


class DocumentVerificationStatus(models.TextChoices):
    """Document integrity verification status matching PostgreSQL shipping.document_verification_status."""
    PENDING_UPLOAD = "PENDING_UPLOAD", "Pending Upload"
    UPLOAD_COMPLETE = "UPLOAD_COMPLETE", "Upload Complete"
    HASH_VERIFIED = "HASH_VERIFIED", "Hash Verified"
    HASH_MISMATCH = "HASH_MISMATCH", "Hash Mismatch"
    QUARANTINED = "QUARANTINED", "Quarantined"
    APPROVED = "APPROVED", "Approved"
    SUPERSEDED = "SUPERSEDED", "Superseded"
    DELETED = "DELETED", "Deleted"


LEGAL_DOCUMENT_STATUS_TRANSITIONS = {
    DocumentVerificationStatus.PENDING_UPLOAD.value: {
        DocumentVerificationStatus.UPLOAD_COMPLETE.value,
        DocumentVerificationStatus.DELETED.value,
    },
    DocumentVerificationStatus.UPLOAD_COMPLETE.value: {
        DocumentVerificationStatus.HASH_VERIFIED.value,
        DocumentVerificationStatus.HASH_MISMATCH.value,
        DocumentVerificationStatus.DELETED.value,
    },
    DocumentVerificationStatus.HASH_VERIFIED.value: {
        DocumentVerificationStatus.APPROVED.value,
        DocumentVerificationStatus.SUPERSEDED.value,
        DocumentVerificationStatus.DELETED.value,
    },
    DocumentVerificationStatus.HASH_MISMATCH.value: {
        DocumentVerificationStatus.QUARANTINED.value,
        DocumentVerificationStatus.DELETED.value,
    },
    DocumentVerificationStatus.QUARANTINED.value: {
        DocumentVerificationStatus.DELETED.value,
    },
    DocumentVerificationStatus.APPROVED.value: {
        DocumentVerificationStatus.SUPERSEDED.value,
        DocumentVerificationStatus.DELETED.value,
    },
    DocumentVerificationStatus.SUPERSEDED.value: {
        DocumentVerificationStatus.DELETED.value,
    },
    DocumentVerificationStatus.DELETED.value: set(),
}

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/tiff",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/csv",
    "text/plain",
}

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".tiff",
    ".tif",
    ".xlsx",
    ".xls",
    ".csv",
    ".txt",
}


class ShippingDocument(models.Model):
    """
    Document vault: tracks every maritime/shipping document uploaded to MinIO/S3.
    Provides cryptographic tamper evidence via client & server SHA-256 digests.
    """
    document_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Document classification
    document_type = models.CharField(
        max_length=64,
        choices=DocumentType.choices,
        help_text="Classification of shipping document",
    )
    document_title = models.CharField(max_length=255)
    document_description = models.TextField(null=True, blank=True)

    # Domain links (at least one must be non-null)
    voyage = models.ForeignKey(
        "voyages.VesselVoyage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documents",
        help_text="Voyage this document relates to",
    )
    contract = models.ForeignKey(
        "contracts.CharterContract",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documents",
        help_text="Charter party contract this document relates to",
    )

    # File metadata
    original_filename = models.CharField(max_length=255)
    file_extension = models.CharField(max_length=20, null=True, blank=True)
    file_size_bytes = models.BigIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text="File size in bytes (max 100MB)",
    )
    mime_type = models.CharField(max_length=120, null=True, blank=True)

    # MinIO / S3 Object Storage coordinates
    minio_bucket = models.CharField(
        max_length=63,
        default="shipping-documents",
        validators=[
            RegexValidator(
                regex=r"^[a-z0-9][a-z0-9\-]{1,61}[a-z0-9]$",
                message="Bucket name must follow S3 naming conventions.",
            )
        ],
    )
    minio_object_key = models.CharField(max_length=1024)
    minio_version_id = models.CharField(max_length=64, null=True, blank=True)

    # Cryptographic integrity & verification
    sha256_hash_client = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        validators=[
            RegexValidator(
                regex=r"^[0-9a-f]{64}$",
                message="Client SHA-256 must be exactly 64 lowercase hexadecimal characters.",
            )
        ],
    )
    sha256_hash_server = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        validators=[
            RegexValidator(
                regex=r"^[0-9a-f]{64}$",
                message="Server SHA-256 must be exactly 64 lowercase hexadecimal characters.",
            )
        ],
    )
    verification_status = models.CharField(
        max_length=32,
        choices=DocumentVerificationStatus.choices,
        default=DocumentVerificationStatus.PENDING_UPLOAD.value,
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_mismatch_detail = models.TextField(null=True, blank=True)

    # Document dates
    document_date = models.DateField(null=True, blank=True, help_text="Date on face of document")
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    # Versioning & immutability
    version_number = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Monotonically increasing version number",
    )
    supersedes_document = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="superseded_by",
        help_text="Previous version of this document superseded by this record",
    )

    # Access control
    is_confidential = models.BooleanField(default=False)
    accessible_roles = models.JSONField(
        default=list,
        blank=True,
        help_text="Roles permitted to view when is_confidential is True",
    )

    # Audit
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_documents",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_documents",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "shipping_documents"
        ordering = ["-uploaded_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(voyage__isnull=False) | models.Q(contract__isnull=False),
                name="chk_documents_voyage_or_contract",
            ),
        ]

    def clean(self) -> None:
        super().clean()

        # At least one of voyage or contract must be present
        if not self.voyage_id and not self.contract_id:
            raise ValidationError(
                {"voyage": "Document must be linked to at least one Vessel Voyage or Charter Contract."}
            )

        # Self-reference check
        if self.supersedes_document_id and self.supersedes_document_id == self.document_id:
            raise ValidationError(
                {"supersedes_document": "A document cannot supersede itself."}
            )

        # File size constraint (100 MB max)
        if self.file_size_bytes is not None and self.file_size_bytes > 104857600:
            raise ValidationError(
                {"file_size_bytes": "File size exceeds the 100 MB maximum threshold."}
            )

        # Hash mismatch detail requirement
        if (
            self.verification_status == DocumentVerificationStatus.HASH_MISMATCH.value
            and not self.verification_mismatch_detail
        ):
            raise ValidationError(
                {"verification_mismatch_detail": "Detail is mandatory when status is HASH_MISMATCH."}
            )

        # Approver requirement
        if self.approved_at and not self.approved_by_id:
            raise ValidationError(
                {"approved_by": "Approving officer is mandatory when approved_at timestamp is set."}
            )

        # State transition validation (if updating existing record)
        if self.pk:
            try:
                original = ShippingDocument.objects.get(pk=self.pk)
                if original.verification_status != self.verification_status:
                    allowed = LEGAL_DOCUMENT_STATUS_TRANSITIONS.get(original.verification_status, set())
                    if self.verification_status not in allowed:
                        raise ValidationError(
                            {
                                "verification_status": (
                                    f"Illegal transition from {original.verification_status} "
                                    f"to {self.verification_status}. Allowed: {sorted(allowed)}"
                                )
                            }
                        )
            except ShippingDocument.DoesNotExist:
                pass

    def save(self, *args, **kwargs) -> None:
        skip_clean = kwargs.pop("skip_clean", False)
        if not skip_clean:
            self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.document_title} ({self.document_type}) [{self.verification_status}]"
