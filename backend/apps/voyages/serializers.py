"""
backend/apps/voyages/serializers.py
============================================================
DRF Serializers for Vessel Voyages, Active Fleet Tracking,
and Operational Milestone State Machine Transitions.
"""

from typing import List
from django.utils import timezone
from rest_framework import serializers

from .models import VesselVoyage, StatementOfFacts
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

