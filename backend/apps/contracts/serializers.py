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


class ContractPerformanceSerializer(serializers.Serializer):
    """Fulfillment and operational scorecard for a single charter contract."""
    contract_id = serializers.CharField()
    contract_reference = serializers.CharField()
    vessel_name = serializers.CharField()
    owner_name = serializers.CharField()
    cargo_type = serializers.CharField()
    charterer_plant = serializers.CharField()

    # Commitments
    nominated_quantity_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    tolerance_pct = serializers.DecimalField(max_digits=5, decimal_places=2)
    min_commitment_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    max_commitment_mt = serializers.DecimalField(max_digits=12, decimal_places=3)

    # Execution
    total_voyages_count = serializers.IntegerField()
    completed_voyages_count = serializers.IntegerField()
    actual_lifted_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    actual_delivered_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    total_shortage_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    fulfillment_pct = serializers.DecimalField(max_digits=6, decimal_places=2)
    fulfillment_status = serializers.CharField()
    remaining_commitment_mt = serializers.DecimalField(max_digits=12, decimal_places=3)

    # Financials
    freight_rate_usd_per_mt = serializers.DecimalField(max_digits=10, decimal_places=2)
    total_freight_paid_usd = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_demurrage_paid_usd = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_despatch_earned_usd = serializers.DecimalField(max_digits=14, decimal_places=2)
    net_laytime_impact_usd = serializers.DecimalField(max_digits=14, decimal_places=2)

    # Quality
    laycan_adherence_rate_pct = serializers.DecimalField(max_digits=6, decimal_places=2)
    as_of = serializers.DateTimeField()


class CarrierScorecardSerializer(serializers.Serializer):
    """Reliability and performance scorecard for a commercial carrier / vessel owner."""
    owner_name = serializers.CharField()
    total_fixtures = serializers.IntegerField()
    total_voyages = serializers.IntegerField()
    completed_voyages = serializers.IntegerField()
    total_carried_mt = serializers.DecimalField(max_digits=14, decimal_places=3)
    total_freight_usd = serializers.DecimalField(max_digits=16, decimal_places=2)
    total_demurrage_usd = serializers.DecimalField(max_digits=16, decimal_places=2)
    total_despatch_usd = serializers.DecimalField(max_digits=16, decimal_places=2)
    net_laytime_usd = serializers.DecimalField(max_digits=16, decimal_places=2)
    demurrage_incidence_pct = serializers.DecimalField(max_digits=6, decimal_places=2)
    laycan_adherence_pct = serializers.DecimalField(max_digits=6, decimal_places=2)
    average_shortage_pct = serializers.DecimalField(max_digits=6, decimal_places=2)
    carrier_reliability_score = serializers.DecimalField(max_digits=6, decimal_places=2)

