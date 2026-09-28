"""
backend/apps/voyages/serializers.py
============================================================
DRF Serializers for Vessel Voyages & Active Fleet Tracking.
"""

from rest_framework import serializers
from .models import VesselVoyage


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
            "quantity_loaded_mt",
            "discharge_port",
            "discharge_country",
            "discharge_berth",
            "discharge_terminal",
            "quantity_discharged_mt",
            "shortage_mt",
            "progress_pct",
            "is_active",
            "eta_date",
            "ata_date",
            "nor_tendered_at",
            "nor_accepted_at",
            "berthed_at",
            "discharge_commenced_at",
            "discharge_completed_at",
            "completed_at",
            "last_position_lat",
            "last_position_lon",
            "last_position_updated",
            "speed_over_ground",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["voyage_id", "created_at", "updated_at"]


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

    class Meta:
        model = VesselVoyage
        fields = [
            "voyage_id",
            "voyage_reference",
            "voyage_status",
            "progress_pct",
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
