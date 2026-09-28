"""
backend/apps/documents/views.py
============================================================
REST API ViewSet for Shipping Documents Vault.
"""

from typing import List, Optional
from django.http import HttpResponse
from django.db.models import Q, Sum, Count
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.exceptions import PermissionDenied, NotFound

from apps.authentication.roles import (
    UserRole,
    ALL_READ_ROLES,
    DOCUMENT_WRITE_ROLES,
    DOCUMENT_APPROVE_ROLES,
)
from apps.voyages.models import VesselVoyage
from apps.contracts.models import CharterContract
from .models import (
    ShippingDocument,
    DocumentType,
    DocumentVerificationStatus,
)
from .serializers import (
    ShippingDocumentSerializer,
    ShippingDocumentUploadSerializer,
    DocumentApprovalSerializer,
    DocumentSupersedeSerializer,
)
from .vault_service import DocumentVaultService
from . import tasks as document_tasks


class ShippingDocumentViewSet(viewsets.ModelViewSet):
    """
    Cryptographic Shipping Document Vault API.
    Provides immutable storage, SHA-256 verification, and RBAC governance.
    """
    queryset = ShippingDocument.objects.select_related("voyage", "contract", "uploaded_by", "approved_by").all()
    serializer_class = ShippingDocumentSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    action_roles = {
        "create": list(DOCUMENT_WRITE_ROLES),
        "approve": list(DOCUMENT_APPROVE_ROLES),
        "supersede": list(DOCUMENT_WRITE_ROLES),
        "verify": list(DOCUMENT_WRITE_ROLES),
        "trigger_vault_scan": [UserRole.SYSTEM_ADMIN.value],
        "destroy": [UserRole.SYSTEM_ADMIN.value],
        "download": list(ALL_READ_ROLES),
        "stats": list(ALL_READ_ROLES),
    }

    def get_queryset(self):
        user_role = getattr(self.request, "user_role", None)
        qs = super().get_queryset()

        # Confidentiality filtering on list action:
        # Non-admins only see non-confidential docs OR docs where their role is listed
        if self.action == "list":
            if user_role and user_role != UserRole.SYSTEM_ADMIN.value and not getattr(self.request.user, "is_superuser", False):
                doc_ids = [
                    doc.document_id for doc in qs
                    if not doc.is_confidential or (user_role in (doc.accessible_roles or []))
                ]
                qs = qs.filter(document_id__in=doc_ids)

        # Filters
        voyage_id = self.request.query_params.get("voyage_id")
        if voyage_id:
            qs = qs.filter(voyage_id=voyage_id)

        contract_id = self.request.query_params.get("contract_id")
        if contract_id:
            qs = qs.filter(contract_id=contract_id)

        doc_type = self.request.query_params.get("document_type")
        if doc_type:
            qs = qs.filter(document_type=doc_type)

        v_status = self.request.query_params.get("verification_status")
        if v_status:
            qs = qs.filter(verification_status=v_status)

        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(document_title__icontains=search)
                | Q(original_filename__icontains=search)
                | Q(document_description__icontains=search)
            )

        return qs

    def create(self, request, *args, **kwargs):
        """
        Uploads and verifies a new document to the vault.
        """
        serializer = ShippingDocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        voyage = None
        if data.get("voyage_id"):
            voyage = VesselVoyage.objects.get(voyage_id=data["voyage_id"])

        contract = None
        if data.get("contract_id"):
            contract = CharterContract.objects.get(contract_id=data["contract_id"])

        file_obj = data["file"]
        user = request.user if request.user and request.user.is_authenticated else None

        document = DocumentVaultService.upload_and_verify(
            file_obj=file_obj,
            document_title=data["document_title"],
            document_type=data["document_type"],
            original_filename=file_obj.name,
            user=user,
            client_sha256=data.get("client_sha256"),
            voyage=voyage,
            contract=contract,
            document_description=data.get("document_description"),
            document_date=data.get("document_date"),
            issue_date=data.get("issue_date"),
            expiry_date=data.get("expiry_date"),
            is_confidential=data.get("is_confidential", False),
            accessible_roles=data.get("accessible_roles", []),
            bucket=data.get("bucket"),
        )

        output_serializer = ShippingDocumentSerializer(document, context={"request": request})
        response_data = output_serializer.data

        # Dispatch background verification worker for UPLOAD_COMPLETE documents
        async_task = None
        if document.verification_status == DocumentVerificationStatus.UPLOAD_COMPLETE.value:
            task = document_tasks.verify_document_async.delay(str(document.document_id))
            async_task = {"task_id": task.id, "queue": "documents"}

        if async_task:
            response_data = dict(response_data)
            response_data["async_verification"] = async_task

        return Response(response_data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, *args, **kwargs):
        """
        Retrieves document metadata, enforcing confidentiality checks.
        """
        document = self.get_object()
        user_role = getattr(request, "user_role", None)
        if (
            document.is_confidential
            and user_role != UserRole.SYSTEM_ADMIN.value
            and not getattr(request.user, "is_superuser", False)
            and user_role not in (document.accessible_roles or [])
        ):
            raise PermissionDenied("You do not have authorization to view this confidential document.")
        serializer = self.get_serializer(document)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        """
        Streams the raw document payload from the vault with cryptographic integrity headers.
        """
        document = self.get_object()

        # Confidentiality check
        user_role = getattr(request, "user_role", None)
        if (
            document.is_confidential
            and user_role != UserRole.SYSTEM_ADMIN.value
            and not getattr(request.user, "is_superuser", False)
            and user_role not in (document.accessible_roles or [])
        ):
            raise PermissionDenied("You do not have authorization to download this confidential document.")

        try:
            payload = DocumentVaultService.read_file_payload(document)
        except Exception as e:
            return Response(
                {"error": "FILE_RETRIEVAL_ERROR", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        response = HttpResponse(payload, content_type=document.mime_type or "application/octet-stream")
        response["Content-Disposition"] = f'attachment; filename="{document.original_filename}"'
        response["X-SHA256-Checksum"] = document.sha256_hash_server or ""
        response["X-Document-Type"] = document.document_type
        response["X-Verification-Status"] = document.verification_status
        return response

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """
        Approves, quarantines, or marks document deleted based on compliance audit.
        """
        document = self.get_object()
        serializer = DocumentApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        action_type = serializer.validated_data["action"]
        notes = serializer.validated_data.get("notes")
        user = request.user if request.user and request.user.is_authenticated else None

        if action_type == "APPROVE":
            doc = DocumentVaultService.approve_document(document, user=user, notes=notes)
        elif action_type == "QUARANTINE":
            doc = DocumentVaultService.quarantine_document(document, user=user, notes=notes)
        elif action_type == "DELETE":
            doc = DocumentVaultService.soft_delete_document(document, user=user)
        else:
            return Response(
                {"error": "INVALID_ACTION", "detail": f"Action '{action_type}' not recognized."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output = ShippingDocumentSerializer(doc, context={"request": request})
        return Response(output.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def supersede(self, request, pk=None):
        """
        Links a newer document version to supersede this existing verified document.
        """
        old_document = self.get_object()
        serializer = DocumentSupersedeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_doc_id = serializer.validated_data["superseded_by_document_id"]
        try:
            new_document = ShippingDocument.objects.get(document_id=new_doc_id)
        except ShippingDocument.DoesNotExist:
            raise NotFound(f"Superseding document {new_doc_id} not found.")

        old_doc, new_doc = DocumentVaultService.supersede_document(old_document, new_document)
        return Response(
            {
                "message": "Document successfully superseded.",
                "superseded_document": ShippingDocumentSerializer(old_doc, context={"request": request}).data,
                "active_document": ShippingDocumentSerializer(new_doc, context={"request": request}).data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"])
    def stats(self, request):
        """
        Aggregated summary metrics across the document vault.
        """
        qs = ShippingDocument.objects.all()
        total_count = qs.count()
        total_size = qs.aggregate(s=Sum("file_size_bytes"))["s"] or 0
        verified_count = qs.filter(verification_status=DocumentVerificationStatus.HASH_VERIFIED.value).count()
        approved_count = qs.filter(verification_status=DocumentVerificationStatus.APPROVED.value).count()
        mismatch_count = qs.filter(verification_status=DocumentVerificationStatus.HASH_MISMATCH.value).count()
        quarantined_count = qs.filter(verification_status=DocumentVerificationStatus.QUARANTINED.value).count()
        deleted_count = qs.filter(verification_status=DocumentVerificationStatus.DELETED.value).count()

        return Response(
            {
                "total_documents": total_count,
                "total_storage_bytes": total_size,
                "verified_documents": verified_count,
                "approved_documents": approved_count,
                "mismatch_documents": mismatch_count,
                "quarantined_documents": quarantined_count,
                "deleted_documents": deleted_count,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        """
        Dispatches the document verification Celery task for on-demand SHA-256 digest
        recomputation and status state machine transition.

        Only UPLOAD_COMPLETE documents can be submitted for (re-)verification.
        Returns 202 Accepted with the dispatched Celery task ID.

        URL: POST /api/v1/documents/{id}/verify/
        """
        document = self.get_object()

        if document.verification_status != DocumentVerificationStatus.UPLOAD_COMPLETE.value:
            return Response(
                {
                    "error": "INVALID_STATUS_FOR_VERIFICATION",
                    "detail": (
                        f"Document is in status '{document.verification_status}'. "
                        f"Only UPLOAD_COMPLETE documents can be submitted for verification."
                    ),
                    "current_status": document.verification_status,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        task = document_tasks.verify_document_async.delay(str(document.document_id))

        return Response(
            {
                "document_id": str(document.document_id),
                "message": "Document verification task dispatched.",
                "async_verification": {
                    "task_id": task.id,
                    "queue": "documents",
                },
                "current_status": document.verification_status,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["post"], url_path="trigger-vault-scan")
    def trigger_vault_scan(self, request):
        """
        Triggers an on-demand vault health scan that fans out SHA-256 verification
        tasks for every UPLOAD_COMPLETE document in the system.

        Restricted to SYSTEM_ADMIN. Returns 202 Accepted with the dispatched
        orchestrator task ID.

        URL: POST /api/v1/documents/trigger-vault-scan/
        """
        task = document_tasks.scan_pending_documents_async.delay()

        pending_count = ShippingDocument.objects.filter(
            verification_status=DocumentVerificationStatus.UPLOAD_COMPLETE.value
        ).count()

        return Response(
            {
                "message": "Document vault health scan dispatched.",
                "async_scan": {
                    "task_id": task.id,
                    "queue": "documents",
                },
                "pending_documents_in_queue": pending_count,
            },
            status=status.HTTP_202_ACCEPTED,
        )
