"""
backend/apps/voyages/views.py
============================================================
DRF ViewSets for Vessel Voyages, Active Fleet Tracking,
and Operational Milestone State Machine Transitions.
"""

from decimal import Decimal
from django.db import transaction, models
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import VesselVoyage, StatementOfFacts
from .sof_enums import ALL_INTERRUPTION_EVENTS
from .sof_normalizer import (
    normalize_sof_batch,
    build_existing_interruption_state,
)
from .serializers import (
    VesselVoyageSerializer,
    ActiveFleetVoyageSerializer,
    VoyageTransitionRequestSerializer,
    VoyageTransitionResponseSerializer,
    StatementOfFactsSerializer,
    SOFIngestionItemSerializer,
    SOFResponseWrapperSerializer,
)
from .state_machine import (
    VoyageStateMachine,
    InvalidStateTransitionError,
    MilestoneChronologyError,
)
from apps.laytime.engine.fixture_factory import VoyageStatus
from apps.authentication.roles import UserRole


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
      - GET /api/v1/voyages/{voyage_id}/sof/
      - POST /api/v1/voyages/{voyage_id}/sof/
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

    action_roles = {
        "list": UserRole.all_roles(),
        "retrieve": UserRole.all_roles(),
        "active_fleet": UserRole.all_roles(),
        "create": [UserRole.SYSTEM_ADMIN.value, UserRole.LOGISTICS_AGENT.value, UserRole.PROCUREMENT_OFFICER.value],
        "update": [UserRole.SYSTEM_ADMIN.value, UserRole.LOGISTICS_AGENT.value],
        "partial_update": [UserRole.SYSTEM_ADMIN.value, UserRole.LOGISTICS_AGENT.value],
        "destroy": [UserRole.SYSTEM_ADMIN.value],
        "transition": [UserRole.SYSTEM_ADMIN.value, UserRole.LOGISTICS_AGENT.value],
        "sof": [UserRole.SYSTEM_ADMIN.value, UserRole.LOGISTICS_AGENT.value],
    }

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

    @action(detail=True, methods=["get", "post"], url_path="sof")
    def sof(self, request, pk=None):
        """
        GET  /api/v1/voyages/{voyage_id}/sof/
          Retrieve the complete chronological Statement of Facts (SOF) port event log
          with timeline summary KPI metrics.

        POST /api/v1/voyages/{voyage_id}/sof/
          Ingest new SOF port event(s) for the voyage. Accepts either:
            - A single event JSON object
            - A JSON list of event objects
            - A JSON object with an 'events' list
          Auto-assigns monotonic sequence numbers and syncs milestone timestamps.
        """
        voyage = self.get_object()

        if request.method == "GET":
            sof_events = voyage.sof_events.all().order_by("sequence_number", "event_timestamp")
            first_event = sof_events.first()
            last_event = sof_events.last()

            interruption_count = sum(1 for e in sof_events if e.event_type in ALL_INTERRUPTION_EVENTS)
            latest_qty = None
            for e in reversed(list(sof_events)):
                if e.quantity_discharged_at_event is not None:
                    latest_qty = e.quantity_discharged_at_event
                    break

            summary_data = {
                "total_events_count": sof_events.count(),
                "first_event_timestamp": first_event.event_timestamp if first_event else None,
                "last_event_timestamp": last_event.event_timestamp if last_event else None,
                "interruption_events_count": interruption_count,
                "latest_discharged_mt": latest_qty,
            }

            response_data = {
                "voyage_id": voyage.voyage_id,
                "voyage_reference": voyage.voyage_reference,
                "vessel_name": voyage.vessel.vessel_name,
                "summary": summary_data,
                "events": StatementOfFactsSerializer(sof_events, many=True).data,
            }
            return Response(response_data, status=status.HTTP_200_OK)

        # POST: Ingestion
        data = request.data
        if isinstance(data, list):
            events_data = data
        elif isinstance(data, dict) and "events" in data and isinstance(data["events"], list):
            events_data = data["events"]
        elif isinstance(data, dict):
            events_data = [data]
        else:
            return Response(
                {"error": "VALIDATION_ERROR", "detail": "Expected a single event object or list of events."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not events_data:
            return Response(
                {"error": "VALIDATION_ERROR", "detail": "No event items provided for ingestion."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = SOFIngestionItemSerializer(data=events_data, many=True)
        serializer.is_valid(raise_exception=True)

        # ------------------------------------------------------------------
        # Cycle 2.2.2: SOF Timeline Normalization
        # Performed BEFORE the atomic DB transaction:
        #   1. UTC normalization of all event_timestamp values
        #   2. Chronological sort (timestamp ASC, then sequence_number ASC)
        #   3. Duplicate event detection
        #   4. Retrograde timestamp guard against already-committed events
        #   5. Interruption pair validation (orphaned END / unclosed START)
        #   6. Phase precedence invariant (arrival/discharge before departure)
        # ------------------------------------------------------------------
        existing_sof_qs = voyage.sof_events.all().order_by("event_timestamp", "sequence_number")
        existing_timestamps = list(existing_sof_qs.values_list("event_timestamp", flat=True))
        existing_intr_state = build_existing_interruption_state(existing_sof_qs)

        normalization_result = normalize_sof_batch(
            serializer.validated_data,
            existing_timestamps=existing_timestamps,
            existing_interruption_state=existing_intr_state,
        )

        if not normalization_result.is_valid:
            return Response(
                {
                    "error": "SOF_NORMALIZATION_ERRORS",
                    "detail": (
                        "One or more timeline normalization violations were detected. "
                        "No events were ingested."
                    ),
                    "violations": [
                        {
                            "code": err.code,
                            "detail": err.detail,
                            "event_index": err.event_index,
                            "event_type": err.event_type,
                        }
                        for err in normalization_result.errors
                    ],
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None

        created_records = []
        with transaction.atomic():
            current_max_seq = (
                StatementOfFacts.objects.filter(voyage=voyage)
                .aggregate(models.Max("sequence_number"))["sequence_number__max"]
                or 0
            )

            # Iterate over chronologically-sorted, UTC-normalized events
            for norm_item in normalization_result.events:
                seq = norm_item.sequence_number
                if seq is None:
                    current_max_seq += 1
                    seq = current_max_seq
                else:
                    if seq > current_max_seq:
                        current_max_seq = seq

                sof = StatementOfFacts(
                    voyage=voyage,
                    event_type=norm_item.event_type,
                    event_timestamp=norm_item.event_timestamp,   # UTC-normalized
                    sequence_number=seq,
                    event_description=norm_item.event_description,
                    recorded_by_agent=norm_item.recorded_by_agent,
                    crane_number=norm_item.crane_number,
                    hatch_number=norm_item.hatch_number,
                    quantity_discharged_at_event=norm_item.quantity_discharged_at_event,
                    interruption_cause=norm_item.interruption_cause,
                    weather_condition=norm_item.weather_condition,
                    is_amendment=norm_item.is_amendment,
                    amended_sof_id=norm_item.amended_sof_id,
                    source_document_id=norm_item.source_document_id,
                    submitted_by=user,
                )
                sof.full_clean()
                sof.save(skip_clean=True)
                created_records.append(sof)

                # Authoritative operational milestone sync
                ev_type = sof.event_type
                if ev_type == "NOR_TENDERED" and not voyage.nor_tendered_at:
                    voyage.nor_tendered_at = sof.event_timestamp
                elif ev_type == "NOR_ACCEPTED" and not voyage.nor_accepted_at:
                    voyage.nor_accepted_at = sof.event_timestamp
                elif ev_type == "ALL_FAST" and not voyage.berthed_at:
                    voyage.berthed_at = sof.event_timestamp
                elif ev_type == "DISCHARGE_COMMENCED" and not voyage.discharge_commenced_at:
                    voyage.discharge_commenced_at = sof.event_timestamp
                elif ev_type == "DISCHARGE_COMPLETED" and not voyage.discharge_completed_at:
                    voyage.discharge_completed_at = sof.event_timestamp

                if sof.quantity_discharged_at_event is not None:
                    voyage.quantity_discharged_mt = sof.quantity_discharged_at_event

            voyage.save(skip_clean=True)

        return Response(
            {
                "voyage_id": voyage.voyage_id,
                "ingested_count": len(created_records),
                "events": StatementOfFactsSerializer(created_records, many=True).data,
            },
            status=status.HTTP_201_CREATED,
        )
