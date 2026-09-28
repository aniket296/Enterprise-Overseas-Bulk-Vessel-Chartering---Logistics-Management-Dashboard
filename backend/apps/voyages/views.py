"""
backend/apps/voyages/views.py
============================================================
DRF ViewSets for Vessel Voyages, Active Fleet Tracking,
and Operational Milestone State Machine Transitions.
"""

from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import VesselVoyage
from .serializers import (
    VesselVoyageSerializer,
    ActiveFleetVoyageSerializer,
    VoyageTransitionRequestSerializer,
    VoyageTransitionResponseSerializer,
)
from .state_machine import (
    VoyageStateMachine,
    InvalidStateTransitionError,
    MilestoneChronologyError,
)
from apps.laytime.engine.fixture_factory import VoyageStatus


class VesselVoyageViewSet(viewsets.ModelViewSet):
    """
    CRUD API for Vessel Voyages, Active Fleet Query, and Milestone Transitions.
    Endpoints:
      - GET /api/v1/voyages/
      - POST /api/v1/voyages/
      - GET /api/v1/voyages/{voyage_id}/
      - GET /api/v1/voyages/active/
      - GET /api/v1/voyages/{voyage_id}/transition/
      - POST /api/v1/voyages/{voyage_id}/transition/
    """
    queryset = VesselVoyage.objects.select_related("vessel", "contract").all()
    serializer_class = VesselVoyageSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "voyage_reference",
        "vessel__vessel_name",
        "vessel__imo_number",
        "contract__contract_reference",
        "contract__charterer_plant",
        "discharge_port",
        "load_port",
    ]
    ordering_fields = ["eta_date", "ata_date", "quantity_loaded_mt", "created_at"]
    ordering = ["-eta_date"]

    def get_queryset(self):
        qs = super().get_queryset()
        status_param = self.request.query_params.get("status")
        port_param = self.request.query_params.get("discharge_port")
        plant_param = self.request.query_params.get("plant")
        cargo_param = self.request.query_params.get("cargo_type")

        if status_param:
            qs = qs.filter(voyage_status=status_param.upper())
        if port_param:
            qs = qs.filter(discharge_port__icontains=port_param)
        if plant_param:
            qs = qs.filter(contract__charterer_plant__icontains=plant_param)
        if cargo_param:
            qs = qs.filter(contract__cargo_type=cargo_param.upper())

        return qs

    @action(detail=False, methods=["get"], url_path="active")
    def active_fleet(self, request):
        """
        GET /api/v1/voyages/active/
        Query all active bulk vessels underway or currently discharging at port.
        Excludes COMPLETED and CANCELLED voyages.
        Returns fleet data grid rows + executive summary metrics.
        """
        active_qs = self.get_queryset().exclude(
            voyage_status__in=[VoyageStatus.COMPLETED.value, VoyageStatus.CANCELLED.value]
        )

        serializer = ActiveFleetVoyageSerializer(active_qs, many=True)
        fleet_data = serializer.data

        # Aggregate executive KPI metrics
        total_tonnage = sum(
            (Decimal(str(v["quantity_loaded_mt"])) for v in fleet_data if v["quantity_loaded_mt"]),
            Decimal("0.000"),
        )
        at_anchor = sum(
            1 for v in fleet_data if v["voyage_status"] in (VoyageStatus.ARRIVED.value, VoyageStatus.NOR_TENDERED.value)
        )
        at_berth = sum(
            1 for v in fleet_data if v["voyage_status"] in (
                VoyageStatus.BERTHED.value,
                VoyageStatus.COMMENCING.value,
                VoyageStatus.DISCHARGING.value,
            )
        )
        in_transit = sum(
            1 for v in fleet_data if v["voyage_status"] == VoyageStatus.IN_TRANSIT.value
        )

        response_payload = {
            "summary": {
                "total_active_voyages": len(fleet_data),
                "total_loaded_mt_underway": str(total_tonnage),
                "vessels_in_transit_count": in_transit,
                "vessels_at_anchor_count": at_anchor,
                "vessels_at_berth_count": at_berth,
            },
            "fleet": fleet_data,
        }
        return Response(response_payload, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "post"], url_path="transition")
    def transition(self, request, pk=None):
        """
        GET  /api/v1/voyages/{voyage_id}/transition/
          Inspect current state and permitted next transitions.

        POST /api/v1/voyages/{voyage_id}/transition/
          Execute a deterministic operational milestone state transition.
          Enforces the 10-state lifecycle graph and milestone chronology invariants.
        """
        voyage = self.get_object()

        if request.method == "GET":
            return Response(
                {
                    "voyage_id": str(voyage.voyage_id),
                    "voyage_reference": voyage.voyage_reference,
                    "current_status": voyage.voyage_status,
                    "progress_pct": voyage.progress_pct,
                    "allowed_transitions": voyage.allowed_transitions,
                },
                status=status.HTTP_200_OK,
            )

        # POST execution
        serializer = VoyageTransitionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        target_status = data["target_status"]
        event_time = data.get("timestamp") or timezone.now()
        remarks = data.get("remarks", "")

        kwargs = {}
        if "nor_accepted_at" in data and data["nor_accepted_at"]:
            kwargs["nor_accepted_at"] = data["nor_accepted_at"]
        if "quantity_discharged_mt" in data and data["quantity_discharged_mt"] is not None:
            kwargs["quantity_discharged_mt"] = data["quantity_discharged_mt"]

        previous_status = voyage.voyage_status

        try:
            with transaction.atomic():
                updated_voyage = VoyageStateMachine.apply_transition(
                    voyage=voyage,
                    target_status=target_status,
                    timestamp=event_time,
                    remarks=remarks,
                    **kwargs,
                )
        except InvalidStateTransitionError as e:
            return Response(
                {
                    "error": "INVALID_STATE_TRANSITION",
                    "detail": str(e),
                    "current_status": e.current_status,
                    "target_status": e.target_status,
                    "allowed_transitions": e.allowed_transitions,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except MilestoneChronologyError as e:
            return Response(
                {
                    "error": "CHRONOLOGY_VIOLATION",
                    "detail": str(e),
                    "prior_milestone": e.prior_milestone,
                    "subsequent_milestone": e.subsequent_milestone,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ValidationError as e:
            detail = e.message_dict if hasattr(e, "message_dict") else getattr(e, "messages", [str(e)])
            return Response(
                {
                    "error": "VALIDATION_ERROR",
                    "detail": detail,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        response_data = {
            "voyage_id": updated_voyage.voyage_id,
            "voyage_reference": updated_voyage.voyage_reference,
            "previous_status": previous_status,
            "current_status": updated_voyage.voyage_status,
            "progress_pct": updated_voyage.progress_pct,
            "allowed_transitions": updated_voyage.allowed_transitions,
            "milestones": {
                "eta_date": updated_voyage.eta_date,
                "ata_date": updated_voyage.ata_date,
                "nor_tendered_at": updated_voyage.nor_tendered_at,
                "nor_accepted_at": updated_voyage.nor_accepted_at,
                "berthed_at": updated_voyage.berthed_at,
                "discharge_commenced_at": updated_voyage.discharge_commenced_at,
                "discharge_completed_at": updated_voyage.discharge_completed_at,
                "completed_at": updated_voyage.completed_at,
            },
            "remarks": remarks,
            "transitioned_at": event_time,
        }
        return Response(VoyageTransitionResponseSerializer(response_data).data, status=status.HTTP_200_OK)
