"""
backend/apps/voyages/serializers.py
============================================================
DRF Serializers for Vessel Voyages, Active Fleet Tracking,
and Operational Milestone State Machine Transitions.
"""

from typing import List
from django.utils import timezone
from rest_framework import serializers

from .models import VesselVoyage, StatementOfFacts, LaytimeCalculation
from .sof_enums import SOFEventType, IntervalCountStatus
from apps.laytime.engine.fixture_factory import VoyageStatus


class VesselVoyageSerializer(serializers.ModelSerializer):
    """
    Standard serializer for VesselVoyage CRUD.
    """
    vessel_name = serializers.ReadOnlyField(source="vessel.vessel_name")
    vessel_imo = serializers.ReadOnlyField(source="vessel.imo_number")
    contract_reference = serializers.ReadOnlyField(source="contract.contract_reference")
    cargo_type = serializers.ReadOnlyField(source="contract.cargo_type")
    shortage_mt = serializers.ReadOnlyField()
    progress_pct = serializers.ReadOnlyField()
    is_active = serializers.ReadOnlyField()
    allowed_transitions = serializers.SerializerMethodField()

    class Meta:
        model = VesselVoyage
        fields = [
            "voyage_id",
            "voyage_reference",
            "contract",
            "contract_reference",
            "vessel",
            "vessel_name",
            "vessel_imo",
            "cargo_type",
            "voyage_status",
            "voyage_direction",
            "load_port",
            "load_country",
            "load_berth",
            "load_terminal",
            "etl_date",
            "atl_date",
            "quantity_loaded_mt",
            "discharge_port",
            "discharge_country",
            "discharge_berth",
            "discharge_terminal",
            "quantity_discharged_mt",
            "shortage_mt",
            "progress_pct",
            "is_active",
            "allowed_transitions",
            "eta_date",
            "ata_date",
            "nor_tendered_at",
            "nor_accepted_at",
            "berthed_at",
            "discharge_commenced_at",
            "discharge_completed_at",
            "completed_at",
            "bl_number",
            "bl_date",
            "bl_quantity_mt",
            "last_position_lat",
            "last_position_lon",
            "last_position_updated",
            "speed_over_ground",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["voyage_id", "created_at", "updated_at"]

    def get_allowed_transitions(self, obj: VesselVoyage) -> List[str]:
        return obj.allowed_transitions


class ActiveFleetVoyageSerializer(serializers.ModelSerializer):
    """
    Comprehensive serializer for the Active Fleet Dashboard & Map Tracker.
    Optimized for GET /api/v1/voyages/active.
    """
    vessel_name = serializers.ReadOnlyField(source="vessel.vessel_name")
    vessel_imo = serializers.ReadOnlyField(source="vessel.imo_number")
    vessel_size_class = serializers.ReadOnlyField(source="vessel.size_class")
    vessel_flag_state = serializers.ReadOnlyField(source="vessel.flag_state")
    vessel_dwt = serializers.ReadOnlyField(source="vessel.deadweight_tonnage")

    contract_reference = serializers.ReadOnlyField(source="contract.contract_reference")
    charterer_entity = serializers.ReadOnlyField(source="contract.charterer_entity")
    charterer_plant = serializers.ReadOnlyField(source="contract.charterer_plant")
    cargo_type = serializers.ReadOnlyField(source="contract.cargo_type")
    laytime_terms = serializers.ReadOnlyField(source="contract.laytime_terms")
    allowed_laytime_hours = serializers.ReadOnlyField(source="contract.allowed_laytime_hours")
    demurrage_rate_usd_day = serializers.ReadOnlyField(source="contract.demurrage_rate_usd_per_day")
    despatch_rate_usd_day = serializers.ReadOnlyField(source="contract.despatch_rate_usd_per_day")

    shortage_mt = serializers.ReadOnlyField()
    progress_pct = serializers.ReadOnlyField()
    allowed_transitions = serializers.SerializerMethodField()

    class Meta:
        model = VesselVoyage
        fields = [
            "voyage_id",
            "voyage_reference",
            "voyage_status",
            "progress_pct",
            "allowed_transitions",
            # Vessel particulars
            "vessel_id",
            "vessel_name",
            "vessel_imo",
            "vessel_size_class",
            "vessel_flag_state",
            "vessel_dwt",
            # Contract & Plant metadata
            "contract_id",
            "contract_reference",
            "charterer_entity",
            "charterer_plant",
            "cargo_type",
            "laytime_terms",
            "allowed_laytime_hours",
            "demurrage_rate_usd_day",
            "despatch_rate_usd_day",
            # Ports & Berthing
            "load_port",
            "discharge_port",
            "discharge_berth",
            # Cargo Outturn
            "quantity_loaded_mt",
            "quantity_discharged_mt",
            "shortage_mt",
            # Milestones & Positioning
            "eta_date",
            "ata_date",
            "nor_tendered_at",
            "nor_accepted_at",
            "berthed_at",
            "discharge_commenced_at",
            "last_position_lat",
            "last_position_lon",
            "speed_over_ground",
            "updated_at",
        ]

    def get_allowed_transitions(self, obj: VesselVoyage) -> List[str]:
        return obj.allowed_transitions


class MilestoneTimestampsSerializer(serializers.Serializer):
    """Serializer representation of operational milestone timestamps."""
    eta_date = serializers.DateTimeField(allow_null=True)
    ata_date = serializers.DateTimeField(allow_null=True)
    nor_tendered_at = serializers.DateTimeField(allow_null=True)
    nor_accepted_at = serializers.DateTimeField(allow_null=True)
    berthed_at = serializers.DateTimeField(allow_null=True)
    discharge_commenced_at = serializers.DateTimeField(allow_null=True)
    discharge_completed_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)


class VoyageTransitionRequestSerializer(serializers.Serializer):
    """
    Request payload schema for executing a voyage milestone transition.
    POST /api/v1/voyages/{voyage_id}/transition/
    """
    target_status = serializers.ChoiceField(
        choices=[(tag.value, tag.name) for tag in VoyageStatus],
        help_text="Target operational status for the voyage",
    )
    timestamp = serializers.DateTimeField(
        required=False,
        default=timezone.now,
        help_text="Timestamp of the transition event (defaults to current UTC time)",
    )
    remarks = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Operational remarks or reasons for deviation/cancellation",
    )
    nor_accepted_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
        help_text="Optional NOR accepted timestamp",
    )
    quantity_discharged_mt = serializers.DecimalField(
        max_digits=10,
        decimal_places=3,
        required=False,
        allow_null=True,
        help_text="Cumulative or final discharged quantity in MT",
    )


class VoyageTransitionResponseSerializer(serializers.Serializer):
    """
    Response schema returning transition result and updated state machine state.
    """
    voyage_id = serializers.UUIDField()
    voyage_reference = serializers.CharField()
    previous_status = serializers.CharField()
    current_status = serializers.CharField()
    progress_pct = serializers.FloatField()
    allowed_transitions = serializers.ListField(child=serializers.CharField())
    milestones = MilestoneTimestampsSerializer()
    remarks = serializers.CharField(allow_blank=True)
    transitioned_at = serializers.DateTimeField()


class StatementOfFactsSerializer(serializers.ModelSerializer):
    """Full representation of an SOF port log line."""
    submitted_by_username = serializers.ReadOnlyField(source="submitted_by.username")
    event_type_display = serializers.CharField(source="get_event_type_display", read_only=True)
    count_status_display = serializers.CharField(source="get_count_status_display", read_only=True)

    class Meta:
        model = StatementOfFacts
        fields = [
            "sof_id",
            "voyage",
            "event_type",
            "event_type_display",
            "event_timestamp",
            "sequence_number",
            "event_description",
            "recorded_by_agent",
            "crane_number",
            "hatch_number",
            "quantity_discharged_at_event",
            "interruption_cause",
            "weather_condition",
            "count_status",
            "count_status_display",
            "count_fraction",
            "counted_hours",
            "is_amendment",
            "amended_sof",
            "source_document_id",
            "submitted_at",
            "submitted_by",
            "submitted_by_username",
        ]
        read_only_fields = ["sof_id", "submitted_at"]


class SOFIngestionItemSerializer(serializers.Serializer):
    """Validates an incoming SOF event payload."""
    event_type = serializers.ChoiceField(choices=SOFEventType.choices)
    event_timestamp = serializers.DateTimeField()
    sequence_number = serializers.IntegerField(required=False, allow_null=True)
    event_description = serializers.CharField(required=False, allow_blank=True, default="")
    recorded_by_agent = serializers.CharField(required=False, allow_blank=True, default="")
    crane_number = serializers.IntegerField(required=False, allow_null=True)
    hatch_number = serializers.IntegerField(required=False, allow_null=True)
    quantity_discharged_at_event = serializers.DecimalField(
        max_digits=10, decimal_places=3, required=False, allow_null=True
    )
    interruption_cause = serializers.CharField(required=False, allow_blank=True, default="")
    weather_condition = serializers.CharField(required=False, allow_blank=True, default="")
    is_amendment = serializers.BooleanField(required=False, default=False)
    amended_sof_id = serializers.UUIDField(required=False, allow_null=True)
    source_document_id = serializers.UUIDField(required=False, allow_null=True)


class SOFSummarySerializer(serializers.Serializer):
    """Summary KPI metrics for a voyage's SOF timeline."""
    total_events_count = serializers.IntegerField()
    first_event_timestamp = serializers.DateTimeField(allow_null=True)
    last_event_timestamp = serializers.DateTimeField(allow_null=True)
    interruption_events_count = serializers.IntegerField()
    latest_discharged_mt = serializers.DecimalField(max_digits=10, decimal_places=3, allow_null=True)


class SOFResponseWrapperSerializer(serializers.Serializer):
    """Response payload for GET /api/v1/voyages/{voyage_id}/sof/."""
    voyage_id = serializers.UUIDField()
    voyage_reference = serializers.CharField()
    vessel_name = serializers.CharField()
    summary = SOFSummarySerializer()
    events = StatementOfFactsSerializer(many=True)


class LaytimeCalculationSerializer(serializers.ModelSerializer):
    """
    Serializer for authoritative laytime settlement records.
    """
    voyage_id = serializers.UUIDField(source="voyage.voyage_id", read_only=True)
    voyage_reference = serializers.ReadOnlyField(source="voyage.voyage_reference")
    settled_by_username = serializers.ReadOnlyField(source="settled_by.username", default=None)

    class Meta:
        model = LaytimeCalculation
        fields = [
            "calc_id",
            "voyage_id",
            "voyage_reference",
            "revision",
            "is_current",
            "outcome",
            "calculated_at",
            "celery_task_id",
            "allowed_laytime_hours",
            "total_port_hours",
            "total_counted_hours",
            "total_excepted_hours",
            "total_disputed_hours",
            "variance_hours",
            "demurrage_rate_usd_per_day",
            "despatch_rate_usd_per_day",
            "gross_demurrage_usd",
            "gross_despatch_usd",
            "net_settlement_usd",
            "demurrage_commenced_at",
            "sof_event_count",
            "first_sof_event_at",
            "last_sof_event_at",
            "settled_at",
            "settled_by",
            "settled_by_username",
            "settlement_notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "calc_id",
            "voyage_id",
            "voyage_reference",
            "revision",
            "is_current",
            "calculated_at",
            "created_at",
            "updated_at",
        ]


class BerthConflictDetailSerializer(serializers.Serializer):
    """Pairwise conflict detail between two voyages."""
    voyage_id_a = serializers.CharField(allow_null=True)
    voyage_ref_a = serializers.CharField(allow_null=True)
    voyage_id_b = serializers.CharField()
    voyage_ref_b = serializers.CharField()
    vessel_name_b = serializers.CharField()
    discharge_port = serializers.CharField()
    discharge_berth = serializers.CharField()
    overlap_start = serializers.DateTimeField()
    overlap_end = serializers.DateTimeField()
    overlap_hours = serializers.DecimalField(max_digits=8, decimal_places=2)
    severity = serializers.CharField()


class BerthConflictReportSerializer(serializers.Serializer):
    """Consolidated conflict report for a berth or window."""
    discharge_port = serializers.CharField()
    discharge_berth = serializers.CharField()
    has_conflict = serializers.BooleanField()
    conflict_count = serializers.IntegerField()
    conflicts = BerthConflictDetailSerializer(many=True)
    earliest_available_at = serializers.DateTimeField(allow_null=True)
    evaluated_at = serializers.DateTimeField()


class BerthConflictValidationRequestSerializer(serializers.Serializer):
    """Validates payload when testing a proposed berthing window."""
    discharge_port = serializers.CharField(max_length=120)
    discharge_berth = serializers.CharField(max_length=60)
    start_time = serializers.DateTimeField()
    end_time = serializers.DateTimeField()
    exclude_voyage_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        if attrs["start_time"] >= attrs["end_time"]:
            raise serializers.ValidationError({"end_time": "end_time must be strictly after start_time."})
        return attrs


class PortCongestionKPIsSerializer(serializers.Serializer):
    """Real-time congestion KPIs for a bulk discharge port."""
    discharge_port = serializers.CharField()
    total_active_vessels = serializers.IntegerField()
    vessels_berthed_count = serializers.IntegerField()
    vessels_at_anchorage_count = serializers.IntegerField()
    vessels_unassigned_berth_count = serializers.IntegerField()
    total_cargo_berthed_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    total_cargo_waiting_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    average_anchorage_wait_hours = serializers.DecimalField(max_digits=8, decimal_places=2, allow_null=True)
    congestion_level = serializers.CharField()
    as_of = serializers.DateTimeField()


class BerthScheduleItemSerializer(serializers.Serializer):
    """Gantt occupancy block for a single vessel at a berth."""
    voyage_id = serializers.CharField()
    voyage_reference = serializers.CharField()
    vessel_name = serializers.CharField()
    vessel_imo = serializers.CharField()
    cargo_type = serializers.CharField()
    quantity_mt = serializers.DecimalField(max_digits=10, decimal_places=3)
    voyage_status = serializers.CharField()
    is_actual = serializers.BooleanField()
    start_time = serializers.DateTimeField()
    end_time = serializers.DateTimeField()
    duration_hours = serializers.DecimalField(max_digits=8, decimal_places=2)
    has_conflict = serializers.BooleanField()
    conflicts_with = serializers.ListField(child=serializers.CharField())


class AnchorageQueueItemSerializer(serializers.Serializer):
    """Vessel awaiting a berth at anchorage."""
    voyage_id = serializers.CharField()
    voyage_reference = serializers.CharField()
    vessel_name = serializers.CharField()
    vessel_imo = serializers.CharField()
    cargo_type = serializers.CharField()
    quantity_mt = serializers.DecimalField(max_digits=10, decimal_places=3)
    voyage_status = serializers.CharField()
    eta_date = serializers.DateTimeField()
    ata_date = serializers.DateTimeField(allow_null=True)
    nor_tendered_at = serializers.DateTimeField(allow_null=True)
    assigned_berth = serializers.CharField(allow_null=True)
    waiting_hours = serializers.DecimalField(max_digits=8, decimal_places=2, allow_null=True)


class PortBerthTimelineReportSerializer(serializers.Serializer):
    """Full Gantt timeline and queue report for a port."""
    discharge_port = serializers.CharField()
    kpis = PortCongestionKPIsSerializer()
    berths = serializers.DictField(child=BerthScheduleItemSerializer(many=True))
    anchorage_queue = AnchorageQueueItemSerializer(many=True)
    total_conflicts_detected = serializers.IntegerField()


class BerthAssignmentRequestSerializer(serializers.Serializer):
    """Request payload for assigning or moving a voyage's berth."""
    discharge_berth = serializers.CharField(max_length=60, allow_blank=True, allow_null=True, required=False)
    force = serializers.BooleanField(default=False, required=False)




