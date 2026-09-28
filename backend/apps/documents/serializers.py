"""
backend/apps/documents/serializers.py
============================================================
REST API Serializers for Shipping Documents Vault.
"""

from rest_framework import serializers
from apps.voyages.models import VesselVoyage
from apps.contracts.models import CharterContract
from .models import (
    ShippingDocument,
    DocumentType,
    DocumentVerificationStatus,
    ALLOWED_EXTENSIONS,
)


class ShippingDocumentSerializer(serializers.ModelSerializer):
    """Authoritative representation of a shipping document vault item."""
    document_type_display = serializers.CharField(source="get_document_type_display", read_only=True)
    voyage_reference = serializers.CharField(source="voyage.voyage_reference", read_only=True)
    contract_reference = serializers.CharField(source="contract.contract_reference", read_only=True)
    uploaded_by_username = serializers.CharField(source="uploaded_by.username", read_only=True)
    approved_by_username = serializers.CharField(source="approved_by.username", read_only=True)
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = ShippingDocument
        fields = [
            "document_id",
            "document_type",
            "document_type_display",
            "document_title",
            "document_description",
            "voyage",
            "voyage_reference",
            "contract",
            "contract_reference",
            "original_filename",
            "file_extension",
            "file_size_bytes",
            "mime_type",
            "minio_bucket",
            "minio_object_key",
            "minio_version_id",
            "sha256_hash_client",
            "sha256_hash_server",
            "verification_status",
            "verified_at",
            "verification_mismatch_detail",
            "document_date",
            "issue_date",
            "expiry_date",
            "version_number",
            "supersedes_document",
            "is_confidential",
            "accessible_roles",
            "uploaded_by",
            "uploaded_by_username",
            "uploaded_at",
            "approved_by",
            "approved_by_username",
            "approved_at",
            "created_at",
            "updated_at",
            "download_url",
        ]
        read_only_fields = fields

    def get_download_url(self, obj: ShippingDocument) -> str:
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(f"/api/v1/documents/{obj.document_id}/download/")
        return f"/api/v1/documents/{obj.document_id}/download/"


class ShippingDocumentUploadSerializer(serializers.Serializer):
    """Input serializer for document file upload."""
    file = serializers.FileField(required=True)
    document_title = serializers.CharField(max_length=255, required=True)
    document_type = serializers.ChoiceField(choices=DocumentType.choices, required=True)
    voyage_id = serializers.UUIDField(required=False, allow_null=True)
    contract_id = serializers.UUIDField(required=False, allow_null=True)
    document_description = serializers.CharField(required=False, allow_blank=True)
    client_sha256 = serializers.CharField(required=False, allow_blank=True, max_length=64)
    document_date = serializers.DateField(required=False, allow_null=True)
    issue_date = serializers.DateField(required=False, allow_null=True)
    expiry_date = serializers.DateField(required=False, allow_null=True)
    is_confidential = serializers.BooleanField(required=False, default=False)
    accessible_roles = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    bucket = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        voyage_id = attrs.get("voyage_id")
        contract_id = attrs.get("contract_id")
        if not voyage_id and not contract_id:
            raise serializers.ValidationError(
                "At least one of 'voyage_id' or 'contract_id' must be specified."
            )

        if voyage_id and not VesselVoyage.objects.filter(voyage_id=voyage_id).exists():
            raise serializers.ValidationError({"voyage_id": f"Voyage with id {voyage_id} not found."})

        if contract_id and not CharterContract.objects.filter(contract_id=contract_id).exists():
            raise serializers.ValidationError({"contract_id": f"Contract with id {contract_id} not found."})

        uploaded_file = attrs.get("file")
        if uploaded_file:
            import os
            ext = os.path.splitext(uploaded_file.name)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise serializers.ValidationError(
                    {"file": f"File extension '{ext}' not allowed. Allowed: {sorted(ALLOWED_EXTENSIONS)}"}
                )

        client_hash = attrs.get("client_sha256")
        if client_hash and len(client_hash) != 64:
            raise serializers.ValidationError(
                {"client_sha256": "Client SHA-256 digest must be exactly 64 hexadecimal characters."}
            )

        return attrs


class DocumentApprovalSerializer(serializers.Serializer):
    """Input serializer for document verification / approval / quarantine."""
    action = serializers.ChoiceField(choices=["APPROVE", "QUARANTINE", "DELETE"])
    notes = serializers.CharField(required=False, allow_blank=True)


class DocumentSupersedeSerializer(serializers.Serializer):
    """Input serializer to record superseding document relationship."""
    superseded_by_document_id = serializers.UUIDField(required=True)
