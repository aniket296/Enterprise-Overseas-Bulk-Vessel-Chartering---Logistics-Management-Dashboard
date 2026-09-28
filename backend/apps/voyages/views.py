"""
backend/apps/voyages/views.py
============================================================
DRF ViewSets for Vessel Voyages, Active Fleet Tracking,
and Operational Milestone State Machine Transitions.
"""

import datetime
from decimal import Decimal
from django.db import transaction, models
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import VesselVoyage, StatementOfFacts, LaytimeCalculation
from .sof_enums import ALL_INTERRUPTION_EVENTS
from .sof_normalizer import (
    normalize_sof_batch,
    build_existing_interruption_state,
)
from .sof_auto_classifier import auto_classify_sof_interruptions
from .laytime_balance import compute_laytime_balance, LaytimeBalanceError
from .serializers import (
    VesselVoyageSerializer,
    ActiveFleetVoyageSerializer,
    VoyageTransitionRequestSerializer,
    VoyageTransitionResponseSerializer,
    StatementOfFactsSerializer,
    SOFIngestionItemSerializer,
    SOFResponseWrapperSerializer,
    LaytimeCalculationSerializer,
    BerthConflictDetailSerializer,
    BerthConflictReportSerializer,
    BerthConflictValidationRequestSerializer,
    PortCongestionKPIsSerializer,
    PortBerthTimelineReportSerializer,
    BerthAssignmentRequestSerializer,
    VoyagePnLSerializer,
    FleetAnalyticsSummarySerializer,
    ExecutiveDashboardKPIsSerializer,
)
from .pnl_engine import compute_voyage_pnl_from_db, VoyagePnLError
from .analytics_engine import compute_fleet_analytics
from .executive_dashboard import compute_executive_dashboard_summary
from .berth_conflict import (
    detect_berth_conflicts_for_window,
    detect_conflicts_for_voyage,
    sweep_all_active_berth_conflicts,
)
from .port_timeline import (
    get_port_berth_timeline,
    get_port_congestion_kpis,
    assign_berth_to_voyage,
    BerthAllocationError,
)
from .state_machine import (
    VoyageStateMachine,
    InvalidStateTransitionError,
    MilestoneChronologyError,
)
from apps.laytime.engine.fixture_factory import VoyageStatus
from apps.laytime.engine.settlement_engine import LaytimeOutcome
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
        "laytime": UserRole.all_roles(),
        "pnl": UserRole.all_roles(),
        "analytics": UserRole.all_roles(),
        "settle": [UserRole.SYSTEM_ADMIN.value, UserRole.FINANCE_OFFICER.value, UserRole.LOGISTICS_AGENT.value],
        "recalculate": [UserRole.SYSTEM_ADMIN.value, UserRole.LOGISTICS_AGENT.value, UserRole.FINANCE_OFFICER.value],
        "berth_conflicts": [UserRole.SYSTEM_ADMIN.value, UserRole.LOGISTICS_AGENT.value, UserRole.PROCUREMENT_OFFICER.value],
        "voyage_berth_conflicts": UserRole.all_roles(),
        "berth_timeline": UserRole.all_roles(),
        "port_congestion": UserRole.all_roles(),
        "assign_berth": [UserRole.SYSTEM_ADMIN.value, UserRole.LOGISTICS_AGENT.value, UserRole.PROCUREMENT_OFFICER.value],
        "executive_summary": UserRole.all_roles(),
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

        # ------------------------------------------------------------------
        # Cycle 2.4.3: Operational Milestone State Transition Alert
        # Broadcast milestone transition notification to the 'alerts' Celery queue
        # ------------------------------------------------------------------
        from apps.voyages.tasks import alert_voyage_status_change
        alert_voyage_status_change.delay(
            str(updated_voyage.voyage_id),
            previous_status,
            updated_voyage.voyage_status,
        )

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

        # ------------------------------------------------------------------
        # Cycle 2.2.3: Auto-classify interruption intervals
        # Runs synchronously AFTER the atomic DB write.  Stamps count_status,
        # count_fraction, and counted_hours on completed interruption START records.
        # ------------------------------------------------------------------
        # Refresh voyage so milestone timestamps include just-committed values
        voyage.refresh_from_db()
        classification_result = auto_classify_sof_interruptions(voyage)

        # Re-fetch created records to include any auto-updated count fields
        created_ids = [r.sof_id for r in created_records]
        from apps.voyages.models import StatementOfFacts as SOFModel
        refreshed_records = list(SOFModel.objects.filter(sof_id__in=created_ids).order_by("sequence_number"))

        # ------------------------------------------------------------------
        # Cycle 2.4.2: Asynchronous Laytime Worker Task
        # Offload laytime balance computation to the 'calculations' Celery queue
        # ------------------------------------------------------------------
        from apps.laytime.tasks import recalculate_laytime_async
        async_task = recalculate_laytime_async.delay(
            str(voyage.voyage_id),
            trigger_source="SOF_INGESTION",
        )

        return Response(
            {
                "voyage_id": voyage.voyage_id,
                "ingested_count": len(created_records),
                "events": StatementOfFactsSerializer(refreshed_records, many=True).data,
                "classification_summary": {
                    "classified_interruption_pairs": classification_result.classified_count,
                    "stamped_sof_ids": classification_result.updated_sof_ids,
                    "skipped_open_intervals": classification_result.skipped_open_count,
                },
                "async_calculation": {
                    "task_id": async_task.id,
                    "status": "QUEUED",
                    "queue": "calculations",
                },
            },
            status=status.HTTP_201_CREATED,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Cycle 2.2.4 — GET /api/v1/voyages/{voyage_id}/laytime/
    # Live Laytime Balance API
    # ──────────────────────────────────────────────────────────────────────
    @action(
        detail=True,
        methods=["get"],
        url_path="laytime",
        url_name="laytime-balance",
    )
    def laytime(self, request, pk=None):
        """
        Compute and return the live laytime balance for a voyage.

        Runs the full 4-stage engine on demand (read-only, no DB writes):
          Stage 1 — AllowedLaytimeEngine: contractual allowed hours
          Stage 2 — TimeSliceDiscretizationEngine: SOF timeline segmentation
          Stage 3 — InterruptionRulesEvaluator: per-slice counting fractions
          Stage 4 — LaytimeSettlementEngine: cumulative balance + USD financials

        Returns
        -------
        200 OK  — LaytimeBalanceSnapshot serialized to JSON
        409 CONFLICT — prerequisite data missing (no SOF events, no laytime start, etc.)
        """
        voyage = self.get_object()

        try:
            snapshot = compute_laytime_balance(voyage)
        except LaytimeBalanceError as exc:
            return Response(
                {"error": exc.code, "detail": exc.detail},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            {
                "voyage_id": snapshot.voyage_id,
                "voyage_reference": snapshot.voyage_reference,
                "outcome": snapshot.outcome,
                "is_final": snapshot.is_final,
                "is_indefinite_laytime": snapshot.is_indefinite_laytime,

                # Hours
                "allowed_hours": str(snapshot.allowed_hours) if snapshot.allowed_hours is not None else None,
                "counted_hours": str(snapshot.counted_hours),
                "excepted_hours": str(snapshot.excepted_hours),
                "balance_hours": str(snapshot.balance_hours),
                "demurrage_hours": str(snapshot.demurrage_hours),
                "despatch_hours": str(snapshot.despatch_hours),

                # Financial
                "gross_demurrage_usd": str(snapshot.gross_demurrage_usd),
                "gross_despatch_usd": str(snapshot.gross_despatch_usd),
                "net_settlement_usd": str(snapshot.net_settlement_usd),

                # Timestamps
                "laytime_commenced_at": snapshot.laytime_commenced_at.isoformat() if snapshot.laytime_commenced_at else None,
                "demurrage_commenced_at": snapshot.demurrage_commenced_at.isoformat() if snapshot.demurrage_commenced_at else None,
                "discharge_completed_at": snapshot.discharge_completed_at.isoformat() if snapshot.discharge_completed_at else None,
                "as_of_timestamp": snapshot.as_of_timestamp.isoformat(),

                # Contract terms (echo for audit)
                "laytime_terms": snapshot.laytime_terms,
                "demurrage_rate_usd_per_day": str(snapshot.demurrage_rate_usd_per_day),
                "despatch_rate_usd_per_day": str(snapshot.despatch_rate_usd_per_day),

                # Audit trail
                "audit_formula": snapshot.audit_formula,
                "allowed_laytime_formula": snapshot.allowed_laytime_formula,
                "slice_count": snapshot.slice_count,
            },
            status=status.HTTP_200_OK,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Cycle 2.2.5 — POST / GET /api/v1/voyages/{voyage_id}/settle/
    # Demurrage Settlement API
    # ──────────────────────────────────────────────────────────────────────
    @action(
        detail=True,
        methods=["get", "post"],
        url_path="settle",
        url_name="settle",
    )
    def settle(self, request, pk=None):
        """
        GET /api/v1/voyages/{voyage_id}/settle/
          Retrieve the current authoritative laytime calculation / settlement record.
          Returns 200 with record, or 404 if not yet settled.

        POST /api/v1/voyages/{voyage_id}/settle/
          Compute and persist authoritative laytime settlement figures.
          Prerequisites:
            - DISCHARGE_COMPLETED milestone recorded (voyage.discharge_completed_at is set).
            - Idempotent: re-settlement re-runs engine and updates record if not yet SETTLED / DISPUTED.
          Returns 201 (if created) or 200 (if updated).
        """
        voyage = self.get_object()

        if request.method == "GET":
            calc = voyage.laytime_calculations.filter(is_current=True).first()
            if not calc:
                return Response(
                    {
                        "error": "NOT_SETTLED",
                        "detail": f"Voyage {voyage.voyage_reference} does not have an authoritative settlement record.",
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )
            return Response(LaytimeCalculationSerializer(calc).data, status=status.HTTP_200_OK)

        # POST branch
        # Prerequisite check: DISCHARGE_COMPLETED
        if not voyage.discharge_completed_at:
            return Response(
                {
                    "error": "DISCHARGE_NOT_COMPLETED",
                    "detail": "Laytime settlement can only be locked after DISCHARGE_COMPLETED milestone is recorded.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Idempotency check:
        existing = voyage.laytime_calculations.filter(is_current=True).first()
        if existing and existing.outcome in (LaytimeOutcome.SETTLED.value, LaytimeOutcome.DISPUTED.value):
            return Response(
                {
                    "error": "SETTLEMENT_LOCKED",
                    "detail": f"Settlement for voyage {voyage.voyage_reference} is already '{existing.outcome}' and cannot be recomputed.",
                    "calculation": LaytimeCalculationSerializer(existing).data,
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Compute laytime balance snapshot
        try:
            snapshot = compute_laytime_balance(voyage)
        except LaytimeBalanceError as exc:
            return Response(
                {"error": exc.code, "detail": exc.detail},
                status=status.HTTP_409_CONFLICT,
            )

        settlement_notes = request.data.get("settlement_notes", "")
        req_outcome = request.data.get("outcome")

        # Total counted hours includes base counted laytime plus demurrage hours
        total_counted = snapshot.counted_hours + snapshot.demurrage_hours
        total_port = total_counted + snapshot.excepted_hours
        variance = snapshot.balance_hours

        sof_qs = voyage.sof_events.all().order_by("event_timestamp")
        sof_count = sof_qs.count()
        first_sof = sof_qs.first()
        last_sof = sof_qs.last()

        user = request.user if request.user and request.user.is_authenticated else None
        now = timezone.now()

        # Target outcome: if client specifies valid outcome (e.g. SETTLED / DISPUTED), honor it; else snapshot outcome
        final_outcome = req_outcome if req_outcome in [tag.value for tag in LaytimeOutcome] else snapshot.outcome

        with transaction.atomic():
            if existing:
                existing.outcome = final_outcome
                existing.calculated_at = now
                existing.allowed_laytime_hours = snapshot.allowed_hours
                existing.total_port_hours = total_port
                existing.total_counted_hours = total_counted
                existing.total_excepted_hours = snapshot.excepted_hours
                existing.variance_hours = variance
                existing.demurrage_rate_usd_per_day = snapshot.demurrage_rate_usd_per_day
                existing.despatch_rate_usd_per_day = snapshot.despatch_rate_usd_per_day
                existing.gross_demurrage_usd = snapshot.gross_demurrage_usd
                existing.gross_despatch_usd = snapshot.gross_despatch_usd
                existing.net_settlement_usd = snapshot.net_settlement_usd
                existing.demurrage_commenced_at = snapshot.demurrage_commenced_at
                existing.sof_event_count = sof_count
                existing.first_sof_event_at = first_sof.event_timestamp if first_sof else None
                existing.last_sof_event_at = last_sof.event_timestamp if last_sof else None
                existing.settled_at = now
                existing.settled_by = user
                if settlement_notes:
                    existing.settlement_notes = settlement_notes
                existing.save()
                calc_record = existing
                res_status = status.HTTP_200_OK
            else:
                calc_record = LaytimeCalculation.objects.create(
                    voyage=voyage,
                    revision=1,
                    is_current=True,
                    outcome=final_outcome,
                    calculated_at=now,
                    allowed_laytime_hours=snapshot.allowed_hours,
                    total_port_hours=total_port,
                    total_counted_hours=total_counted,
                    total_excepted_hours=snapshot.excepted_hours,
                    variance_hours=variance,
                    demurrage_rate_usd_per_day=snapshot.demurrage_rate_usd_per_day,
                    despatch_rate_usd_per_day=snapshot.despatch_rate_usd_per_day,
                    gross_demurrage_usd=snapshot.gross_demurrage_usd,
                    gross_despatch_usd=snapshot.gross_despatch_usd,
                    net_settlement_usd=snapshot.net_settlement_usd,
                    demurrage_commenced_at=snapshot.demurrage_commenced_at,
                    sof_event_count=sof_count,
                    first_sof_event_at=first_sof.event_timestamp if first_sof else None,
                    last_sof_event_at=last_sof.event_timestamp if last_sof else None,
                    settled_at=now,
                    settled_by=user,
                    settlement_notes=settlement_notes,
                )
                res_status = status.HTTP_201_CREATED

        return Response(LaytimeCalculationSerializer(calc_record).data, status=res_status)

    # ──────────────────────────────────────────────────────────────────────
    # Cycle 2.4.2 — POST /api/v1/voyages/{voyage_id}/recalculate/
    # Asynchronous Laytime Recalculate Worker Action
    # ──────────────────────────────────────────────────────────────────────
    @action(
        detail=True,
        methods=["post"],
        url_path="recalculate",
        url_name="recalculate-laytime",
    )
    def recalculate(self, request, pk=None):
        """
        POST /api/v1/voyages/{voyage_id}/recalculate/
        Dispatches an asynchronous laytime recalculation task to the Celery 'calculations' worker queue.
        Offloads batch settlement calculation from the main HTTP thread.
        """
        voyage = self.get_object()
        from apps.laytime.tasks import recalculate_laytime_async

        async_task = recalculate_laytime_async.delay(
            str(voyage.voyage_id),
            trigger_source="ON_DEMAND_API",
        )
        return Response(
            {
                "voyage_id": voyage.voyage_id,
                "voyage_reference": voyage.voyage_reference,
                "task_id": async_task.id,
                "status": "QUEUED",
                "queue": "calculations",
            },
            status=status.HTTP_202_ACCEPTED,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Cycle 2.3.1 — GET / POST /api/v1/voyages/berth-conflicts/
    # Berth Conflict Detection & Pre-allocation Validator API
    # ──────────────────────────────────────────────────────────────────────
    @action(
        detail=False,
        methods=["get", "post"],
        url_path="berth-conflicts",
        url_name="berth-conflicts",
    )
    def berth_conflicts(self, request):
        """
        GET /api/v1/voyages/berth-conflicts/
          Fleet-wide sweep of all active berth collisions.
          Optional query parameters: discharge_port, discharge_berth.

        POST /api/v1/voyages/berth-conflicts/
          Pre-allocation validator: tests whether a proposed berthing window [start_time, end_time)
          conflicts with any currently assigned vessel at (discharge_port, discharge_berth).
        """
        if request.method == "GET":
            port = request.query_params.get("discharge_port")
            berth = request.query_params.get("discharge_berth")
            conflicts = sweep_all_active_berth_conflicts(discharge_port=port, discharge_berth=berth)
            serializer = BerthConflictDetailSerializer(conflicts, many=True)
            return Response(
                {
                    "total_conflicts": len(conflicts),
                    "discharge_port": port,
                    "discharge_berth": berth,
                    "conflicts": serializer.data,
                },
                status=status.HTTP_200_OK,
            )

        # POST: Validate proposed berthing window
        val_serializer = BerthConflictValidationRequestSerializer(data=request.data)
        val_serializer.is_valid(raise_exception=True)
        data = val_serializer.validated_data

        report = detect_berth_conflicts_for_window(
            discharge_port=data["discharge_port"],
            discharge_berth=data["discharge_berth"],
            start_time=data["start_time"],
            end_time=data["end_time"],
            exclude_voyage_id=str(data["exclude_voyage_id"]) if data.get("exclude_voyage_id") else None,
        )
        return Response(BerthConflictReportSerializer(report).data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["get"],
        url_path="berth-conflicts",
        url_name="voyage-berth-conflicts",
    )
    def voyage_berth_conflicts(self, request, pk=None):
        """
        GET /api/v1/voyages/{voyage_id}/berth-conflicts/
          Audit berth conflicts specifically involving this voyage.
        """
        voyage = self.get_object()
        report = detect_conflicts_for_voyage(voyage)
        return Response(BerthConflictReportSerializer(report).data, status=status.HTTP_200_OK)

    # ──────────────────────────────────────────────────────────────────────
    # Cycle 2.3.2 — GET /api/v1/voyages/berth-timeline/
    # Gantt-Ready Berth Occupancy Schedule & Anchorage Queue API
    # ──────────────────────────────────────────────────────────────────────
    @action(
        detail=False,
        methods=["get"],
        url_path="berth-timeline",
        url_name="berth-timeline",
    )
    def berth_timeline(self, request):
        """
        GET /api/v1/voyages/berth-timeline/
          Returns complete Gantt-ready berth occupancy blocks, anchorage queue,
          and congestion KPIs for a specific discharge port.
        """
        port = request.query_params.get("discharge_port", "Haldia")
        timeline_report = get_port_berth_timeline(discharge_port=port)
        return Response(PortBerthTimelineReportSerializer(timeline_report).data, status=status.HTTP_200_OK)

    # ──────────────────────────────────────────────────────────────────────
    # Cycle 2.3.2 — GET /api/v1/voyages/port-congestion/
    # Port Congestion Analytics & Anchorage Waiting KPIs
    # ──────────────────────────────────────────────────────────────────────
    @action(
        detail=False,
        methods=["get"],
        url_path="port-congestion",
        url_name="port-congestion",
    )
    def port_congestion(self, request):
        """
        GET /api/v1/voyages/port-congestion/
          Returns port congestion KPIs (queue depth, cargo waiting, avg wait hours).
          Optional query parameter: discharge_port. If omitted, returns all active ports.
        """
        port = request.query_params.get("discharge_port")
        if port:
            kpis = get_port_congestion_kpis(discharge_port=port)
            return Response(PortCongestionKPIsSerializer(kpis).data, status=status.HTTP_200_OK)

        # Multi-port overview
        active_ports = list(
            VesselVoyage.objects
            .exclude(voyage_status__in=["COMPLETED", "CANCELLED"])
            .values_list("discharge_port", flat=True)
            .distinct()
        )
        if not active_ports:
            active_ports = ["Haldia", "Visakhapatnam", "Paradip", "Dhamra"]

        kpi_list = [get_port_congestion_kpis(p) for p in sorted(set(active_ports))]
        return Response(PortCongestionKPIsSerializer(kpi_list, many=True).data, status=status.HTTP_200_OK)

    # ──────────────────────────────────────────────────────────────────────
    # Cycle 2.3.2 — POST /api/v1/voyages/{voyage_id}/assign-berth/
    # Conflict-Guarded Berth Allocation API
    # ──────────────────────────────────────────────────────────────────────
    @action(
        detail=True,
        methods=["post"],
        url_path="assign-berth",
        url_name="assign-berth",
    )
    def assign_berth(self, request, pk=None):
        """
        POST /api/v1/voyages/{voyage_id}/assign-berth/
          Assign or modify a vessel's discharge berth with temporal conflict guard.
          Payload: {"discharge_berth": "Berth 4B", "force": false}
        """
        voyage = self.get_object()
        serializer = BerthAssignmentRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        target_berth = serializer.validated_data.get("discharge_berth")
        force = serializer.validated_data.get("force", False)

        try:
            updated_voyage, conflict_report = assign_berth_to_voyage(
                voyage, berth_name=target_berth, force=force
            )
        except BerthAllocationError as exc:
            return Response(
                {
                    "error": "BERTH_CONFLICT",
                    "detail": str(exc),
                    "conflicts": BerthConflictReportSerializer(exc.conflict_report).data,
                },
                status=status.HTTP_409_CONFLICT,
            )

        response_data = {
            "voyage": VesselVoyageSerializer(updated_voyage).data,
            "assigned_berth": updated_voyage.discharge_berth,
            "conflict_override": force if (conflict_report and conflict_report.has_conflict) else False,
            "conflict_report": BerthConflictReportSerializer(conflict_report).data if conflict_report else None,
        }
        return Response(response_data, status=status.HTTP_200_OK)

    # ──────────────────────────────────────────────────────────────────────
    # Cycle 2.5.1 — GET /api/v1/voyages/{voyage_id}/pnl/
    # Freight Revenue & Voyage P&L Summary API
    # ──────────────────────────────────────────────────────────────────────
    @action(
        detail=True,
        methods=["get"],
        url_path="pnl",
        url_name="voyage-pnl",
    )
    def pnl(self, request, pk=None):
        """
        Compute and return the voyage Profit & Loss (P&L) summary.
        Calculates gross freight revenue, laytime settlement impact (demurrage/despatch),
        cargo outturn efficiency & shortage, and net voyage P&L.
        """
        voyage = self.get_object()
        try:
            snapshot = compute_voyage_pnl_from_db(voyage)
        except VoyagePnLError as exc:
            return Response(
                {"error": exc.code, "detail": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = VoyagePnLSerializer(snapshot)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # ──────────────────────────────────────────────────────────────────────
    # Cycle 2.5.2 — GET /api/v1/voyages/analytics/
    # Demurrage & Despatch Analytics & Fleet Historical Performance API
    # ──────────────────────────────────────────────────────────────────────
    @action(
        detail=False,
        methods=["get"],
        url_path="analytics",
        url_name="fleet-analytics",
    )
    def analytics(self, request):
        """
        GET /api/v1/voyages/analytics/
        Aggregate and return historical shipping performance, demurrage vs despatch
        laytime settlement impact, port efficiency, and cargo outturn metrics.

        Optional Query Parameters:
          - discharge_port : filter by destination port (case-insensitive substring)
          - cargo_type     : filter by cargo commodity (exact match)
          - plant          : filter by charterer plant (case-insensitive substring)
          - status         : filter by voyage status (SCHEDULED, COMPLETED, etc.)
          - date_from      : filter voyages with eta_date >= date_from (YYYY-MM-DD)
          - date_to        : filter voyages with eta_date <= date_to (YYYY-MM-DD)
        """
        qs = self.get_queryset()

        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")

        if date_from:
            try:
                d_from = datetime.date.fromisoformat(date_from)
                qs = qs.filter(eta_date__date__gte=d_from)
            except ValueError:
                pass

        if date_to:
            try:
                d_to = datetime.date.fromisoformat(date_to)
                qs = qs.filter(eta_date__date__lte=d_to)
            except ValueError:
                pass

        summary = compute_fleet_analytics(qs)
        serializer = FleetAnalyticsSummarySerializer(summary)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # ──────────────────────────────────────────────────────────────────────
    # GET /api/v1/voyages/executive-summary/
    # Consolidated Enterprise Logistics KPI Summary Dashboard
    # ──────────────────────────────────────────────────────────────────────
    @action(
        detail=False,
        methods=["get"],
        url_path="executive-summary",
        url_name="executive-summary",
    )
    def executive_summary(self, request):
        """
        GET /api/v1/voyages/executive-summary/
        Consolidated enterprise logistics summary spanning fleet operations,
        freight finances, raw material runway, charter fulfillment, and document vault.
        """
        kpis = compute_executive_dashboard_summary()
        serializer = ExecutiveDashboardKPIsSerializer(kpis)
        return Response(serializer.data, status=status.HTTP_200_OK)






