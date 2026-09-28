"""
backend/apps/inventory/serializers.py
============================================================
DRF Serializers for Steel Plant Inventory & Blast Furnace Runway.
"""

from decimal import Decimal
from rest_framework import serializers

from .models import PlantInventory, RawMaterialCategory, StockSafetyStatus
from .runway_engine import (
    SAIL_PLANTS_CONFIG,
    InTransitCargoContribution,
    RunwayAnalysis,
    PlantHealthSummary,
    compute_runway_analysis,
)


class PlantInventorySerializer(serializers.ModelSerializer):
    """
    Standard serializer for PlantInventory records.
    """
    recorded_by_username = serializers.ReadOnlyField(source="recorded_by.username", default=None)
    contributing_voyage_ref = serializers.ReadOnlyField(source="contributing_voyage.voyage_reference", default=None)

    class Meta:
        model = PlantInventory
        fields = [
            "inventory_id",
            "plant_code",
            "plant_name",
            "plant_location",
            "material_category",
            "material_description",
            "stock_date",
            "opening_stock_mt",
            "receipts_mt",
            "consumption_mt",
            "closing_stock_mt",
            "daily_consumption_rate_mt",
            "projected_receipts_mt",
            "stock_runway_days",
            "safety_status",
            "contributing_voyage",
            "contributing_voyage_ref",
            "recorded_by",
            "recorded_by_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "inventory_id",
            "closing_stock_mt",
            "stock_runway_days",
            "safety_status",
            "created_at",
            "updated_at",
        ]


class PlantInventoryCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating or updating a daily stock snapshot.
    Automatically assigns plant_name and plant_location if omitted.
    """
    class Meta:
        model = PlantInventory
        fields = [
            "inventory_id",
            "plant_code",
            "plant_name",
            "plant_location",
            "material_category",
            "material_description",
            "stock_date",
            "opening_stock_mt",
            "receipts_mt",
            "consumption_mt",
            "closing_stock_mt",
            "daily_consumption_rate_mt",
            "projected_receipts_mt",
            "stock_runway_days",
            "safety_status",
            "contributing_voyage",
        ]
        read_only_fields = [
            "inventory_id",
            "closing_stock_mt",
            "stock_runway_days",
            "safety_status",
        ]
        extra_kwargs = {
            "plant_name": {"required": False},
            "plant_location": {"required": False},
        }

    def validate(self, attrs):
        code = attrs.get("plant_code", "").strip().upper()
        attrs["plant_code"] = code

        # Auto-populate plant name and location from configuration if missing
        if not attrs.get("plant_name") and code in SAIL_PLANTS_CONFIG:
            attrs["plant_name"], attrs["plant_location"] = SAIL_PLANTS_CONFIG[code]

        # Invariant check: receipts + opening >= consumption
        opening = attrs.get("opening_stock_mt", Decimal("0.000"))
        receipts = attrs.get("receipts_mt", Decimal("0.000"))
        consumption = attrs.get("consumption_mt", Decimal("0.000"))

        if (opening + receipts) < consumption:
            raise serializers.ValidationError(
                {"consumption_mt": "Consumption cannot exceed total available stock (opening + receipts)."}
            )

        return attrs


class InTransitCargoContributionSerializer(serializers.Serializer):
    """Incoming vessel contributing to projected runway."""
    voyage_id = serializers.CharField()
    voyage_reference = serializers.CharField()
    vessel_name = serializers.CharField()
    cargo_type = serializers.CharField()
    quantity_mt = serializers.DecimalField(max_digits=10, decimal_places=3)
    voyage_status = serializers.CharField()
    eta_date = serializers.DateTimeField()
    discharge_port = serializers.CharField()


class RunwayAnalysisSerializer(serializers.Serializer):
    """Comprehensive runway report for a plant material."""
    plant_code = serializers.CharField()
    plant_name = serializers.CharField()
    material_category = serializers.CharField()
    stock_date = serializers.DateField()
    opening_stock_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    receipts_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    consumption_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    closing_stock_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    daily_consumption_rate_mt = serializers.DecimalField(max_digits=10, decimal_places=3)
    physical_runway_days = serializers.DecimalField(max_digits=8, decimal_places=2)
    projected_receipts_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    total_runway_days = serializers.DecimalField(max_digits=8, decimal_places=2)
    safety_status = serializers.CharField()
    critical_stockout_date = serializers.DateField(allow_null=True)
    recommended_action = serializers.CharField()
    incoming_voyages = InTransitCargoContributionSerializer(many=True)


class PlantHealthSummarySerializer(serializers.Serializer):
    """Plant-wide overview and bottleneck material summary."""
    plant_code = serializers.CharField()
    plant_name = serializers.CharField()
    plant_location = serializers.CharField()
    total_materials_tracked = serializers.IntegerField()
    critical_count = serializers.IntegerField()
    precautionary_count = serializers.IntegerField()
    watch_count = serializers.IntegerField()
    adequate_count = serializers.IntegerField()
    surplus_count = serializers.IntegerField()
    bottleneck_material = serializers.CharField(allow_null=True)
    lowest_runway_days = serializers.DecimalField(max_digits=8, decimal_places=2, allow_null=True)
    overall_health = serializers.CharField()
    evaluated_at = serializers.DateTimeField()


class DailyRunwayPointSerializer(serializers.Serializer):
    """Daily projected stock and runway state in forward simulation."""
    day_index = serializers.IntegerField()
    simulation_date = serializers.DateField()
    opening_stock_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    daily_burn_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    inbound_receipts_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    closing_stock_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    runway_days_remaining = serializers.DecimalField(max_digits=8, decimal_places=2)
    safety_status = serializers.CharField()
    events = serializers.ListField(child=serializers.CharField())


class PlantForwardRunwayForecastSerializer(serializers.Serializer):
    """Multi-day inventory depletion and vessel replenishment forecast serializer."""
    plant_code = serializers.CharField()
    plant_name = serializers.CharField()
    plant_location = serializers.CharField()
    material_category = serializers.CharField()
    horizon_days = serializers.IntegerField()
    as_of_date = serializers.DateField()

    current_stock_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    daily_consumption_rate_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    baseline_runway_days = serializers.DecimalField(max_digits=8, decimal_places=2)

    total_inbound_tonnage_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    inbound_vessels_count = serializers.IntegerField()
    extended_runway_days = serializers.DecimalField(max_digits=8, decimal_places=2)

    stockout_date = serializers.DateField(allow_null=True)
    critical_breach_date = serializers.DateField(allow_null=True)
    precautionary_breach_date = serializers.DateField(allow_null=True)
    minimum_projected_stock_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    minimum_stock_date = serializers.DateField(allow_null=True)
    is_starvation_risk = serializers.BooleanField()

    timeline = DailyRunwayPointSerializer(many=True)
    inbound_vessels = InTransitCargoContributionSerializer(many=True)
    generated_at = serializers.DateTimeField()


class PlantStarvationProfileSerializer(serializers.Serializer):
    """Individual plant raw material status and starvation vulnerability."""
    plant_code = serializers.CharField()
    plant_name = serializers.CharField()
    plant_location = serializers.CharField()
    material_category = serializers.CharField()
    current_stock_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    daily_burn_rate_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    physical_runway_days = serializers.DecimalField(max_digits=8, decimal_places=2)
    incoming_vessels_count = serializers.IntegerField()
    incoming_tonnage_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    extended_runway_days = serializers.DecimalField(max_digits=8, decimal_places=2)
    target_safe_stock_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    deficit_to_safe_stock_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    risk_severity = serializers.CharField()
    is_starvation_risk = serializers.BooleanField()


class DiversionOptionSerializer(serializers.Serializer):
    """Simulated diversion of an active in-transit vessel to the starving plant."""
    voyage_id = serializers.CharField()
    voyage_reference = serializers.CharField()
    vessel_name = serializers.CharField()
    cargo_type = serializers.CharField()
    quantity_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    current_destination_plant = serializers.CharField()
    current_discharge_port = serializers.CharField()
    target_plant = serializers.CharField()
    target_discharge_port = serializers.CharField()
    donor_current_runway_days = serializers.DecimalField(max_digits=8, decimal_places=2)
    donor_post_diversion_runway_days = serializers.DecimalField(max_digits=8, decimal_places=2)
    is_donor_safe = serializers.BooleanField()
    recipient_post_diversion_runway_days = serializers.DecimalField(max_digits=8, decimal_places=2)
    runway_days_gained = serializers.DecimalField(max_digits=8, decimal_places=2)
    estimated_diversion_cost_usd = serializers.DecimalField(max_digits=12, decimal_places=2)
    feasibility_score = serializers.IntegerField()
    recommendation = serializers.CharField()


class EmergencyRecharterOptionSerializer(serializers.Serializer):
    """Spot chartering recommendation to eliminate plant stock deficit."""
    recommended_vessel_class = serializers.CharField()
    parcel_size_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    estimated_spot_freight_usd_per_mt = serializers.DecimalField(max_digits=8, decimal_places=2)
    total_estimated_freight_usd = serializers.DecimalField(max_digits=14, decimal_places=2)
    laycan_urgency_days = serializers.IntegerField()
    runway_days_restored = serializers.DecimalField(max_digits=8, decimal_places=2)
    target_runway_days = serializers.DecimalField(max_digits=8, decimal_places=2)


class EmergencyInterPlantTransferOptionSerializer(serializers.Serializer):
    """Rail rake transfer from an adjacent surplus SAIL steel plant."""
    donor_plant_code = serializers.CharField()
    donor_plant_name = serializers.CharField()
    donor_surplus_stock_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    rakes_required = serializers.IntegerField()
    total_transfer_tonnage_mt = serializers.DecimalField(max_digits=12, decimal_places=3)
    estimated_transit_hours = serializers.IntegerField()
    estimated_rail_freight_usd = serializers.DecimalField(max_digits=12, decimal_places=2)
    recipient_runway_days_restored = serializers.DecimalField(max_digits=8, decimal_places=2)
    is_transfer_viable = serializers.BooleanField()


class StarvationMitigationSimulationSerializer(serializers.Serializer):
    """Consolidated emergency response and mitigation plan for a plant."""
    target_plant_code = serializers.CharField()
    target_plant_name = serializers.CharField()
    material_category = serializers.CharField()
    starvation_profile = PlantStarvationProfileSerializer()
    primary_strategy = serializers.CharField()
    summary_recommendation = serializers.CharField()
    diversion_options = DiversionOptionSerializer(many=True)
    spot_recharter_option = EmergencyRecharterOptionSerializer(allow_null=True)
    rail_transfer_options = EmergencyInterPlantTransferOptionSerializer(many=True)
    all_plants_matrix = PlantStarvationProfileSerializer(many=True)
    simulated_at = serializers.DateTimeField()


