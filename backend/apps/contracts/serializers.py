"""
backend/apps/contracts/serializers.py
============================================================
DRF Serializers for Vessel Registry and Charter Contracts.
"""

from rest_framework import serializers
from .models import Vessel, CharterContract


class VesselSerializer(serializers.ModelSerializer):
    """
    Serializer for Vessel Registry.
    """
    class Meta:
        model = Vessel
        fields = [
            "vessel_id",
            "vessel_name",
            "imo_number",
            "flag_state",
            "size_class",
            "deadweight_tonnage",
            "gross_tonnage",
            "net_tonnage",
            "length_overall_meters",
            "beam_meters",
            "draft_meters",
            "hold_count",
            "hatch_count",
            "build_year",
            "owner_name",
            "service_speed_knots",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["vessel_id", "created_at", "updated_at"]

    def validate(self, attrs):
        hold_count = attrs.get("hold_count", getattr(self.instance, "hold_count", None))
        hatch_count = attrs.get("hatch_count", getattr(self.instance, "hatch_count", None))
        if hold_count and hatch_count and hatch_count > hold_count:
            raise serializers.ValidationError(
                {"hatch_count": f"Hatch count ({hatch_count}) cannot exceed hold count ({hold_count})."}
            )
        return attrs


class CharterContractSerializer(serializers.ModelSerializer):
    """
    Serializer for Charter Party Contracts with validation.
    """
    vessel_name = serializers.ReadOnlyField(source="vessel.vessel_name")
    vessel_imo = serializers.ReadOnlyField(source="vessel.imo_number")

    class Meta:
        model = CharterContract
        fields = [
            "contract_id",
            "contract_reference",
            "charter_party_type",
            "contract_status",
            "charterer_entity",
            "charterer_plant",
            "vessel",
            "vessel_name",
            "vessel_imo",
            "cargo_type",
            "nominated_quantity_mt",
            "tolerance_pct",
            "freight_rate_usd_per_mt",
            "load_port",
            "discharge_port",
            "discharge_berth_designated",
            "discharge_rate_mt_per_day",
            "laytime_terms",
            "demurrage_rate_usd_per_day",
            "despatch_rate_usd_per_day",
            "laycan_start",
            "laycan_end",
            "turn_time_hours",
            "once_on_demurrage_always_on",
            "allowed_laytime_hours",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["contract_id", "created_at", "updated_at"]

    def validate(self, attrs):
        laycan_start = attrs.get("laycan_start", getattr(self.instance, "laycan_start", None))
        laycan_end = attrs.get("laycan_end", getattr(self.instance, "laycan_end", None))

        if laycan_start and laycan_end:
            if laycan_start > laycan_end:
                raise serializers.ValidationError(
                    {"laycan_start": f"laycan_start ({laycan_start}) cannot be after laycan_end ({laycan_end})."}
                )
            if (laycan_end - laycan_start).days > 31:
                raise serializers.ValidationError(
                    {"laycan_end": f"Laycan window cannot exceed 31 days (got {(laycan_end - laycan_start).days} days)."}
                )

        dem_rate = attrs.get("demurrage_rate_usd_per_day", getattr(self.instance, "demurrage_rate_usd_per_day", None))
        des_rate = attrs.get("despatch_rate_usd_per_day", getattr(self.instance, "despatch_rate_usd_per_day", None))

        if dem_rate is not None and des_rate is not None:
            if des_rate > dem_rate:
                raise serializers.ValidationError(
                    {"despatch_rate_usd_per_day": f"despatch_rate ({des_rate}) cannot exceed demurrage_rate ({dem_rate})."}
                )

        return attrs


class ContractSpecificationSerializer(serializers.Serializer):
    """
    Serializer for the computed laytime contract specifications.
    """
    contract_id = serializers.UUIDField()
    contract_reference = serializers.CharField()
    vessel_name = serializers.CharField()
    vessel_imo = serializers.CharField()
    cargo_type = serializers.CharField()
    nominated_quantity_mt = serializers.CharField()
    tolerance_pct = serializers.CharField()
    cargo_min_mt = serializers.CharField()
    cargo_max_mt = serializers.CharField()
    discharge_rate_mt_day = serializers.CharField()
    laytime_terms = serializers.CharField()
    is_fixed_laytime = serializers.BooleanField()
    allowed_laytime_hours = serializers.CharField(allow_null=True)
    allowed_laytime_days = serializers.CharField(allow_null=True)
    allowed_hours_min = serializers.CharField(allow_null=True)
    allowed_hours_max = serializers.CharField(allow_null=True)
    demurrage_rate_usd_day = serializers.CharField()
    despatch_rate_usd_day = serializers.CharField()
    hourly_demurrage_rate_usd = serializers.CharField()
    hourly_despatch_rate_usd = serializers.CharField()
    laycan_start = serializers.CharField()
    laycan_end = serializers.CharField()
    laycan_duration_days = serializers.IntegerField()
    turn_time_hours = serializers.CharField()
    once_on_demurrage_always_on = serializers.BooleanField()
    formula_string = serializers.CharField()
