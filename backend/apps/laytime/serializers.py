"""
backend/apps/laytime/serializers.py
============================================================
Strict Serializers for Laytime & Demurrage Calculation API.
Pillar I & Pillar II Verification & Validation.
"""

from decimal import Decimal
from rest_framework import serializers
from apps.laytime.engine.constants import LaytimeTerms


class ComputeLaytimeRequestSerializer(serializers.Serializer):
    """
    Validates request payload for POST /api/v1/calculations/compute-laytime.
    Enforces strict decimal bounds, ISO datetime parsing, and valid maritime laytime terms.
    """
    cargo_quantity_mt = serializers.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.001"),
        required=True,
        help_text="Nominated cargo quantity in metric tonnes",
    )
    discharge_rate_mt_day = serializers.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.001"),
        required=True,
        help_text="Contractual discharge rate in MT per weather working day",
    )
    laytime_terms = serializers.ChoiceField(
        choices=[(t.value, t.name) for t in LaytimeTerms],
        default=LaytimeTerms.SHINC.value,
        help_text="Governing charter party laytime terms (e.g. SHINC, SHEX, WWDSHEX)",
    )
    tolerance_pct = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0.00"),
        max_value=Decimal("20.00"),
        default=Decimal("5.00"),
        required=False,
    )
    demurrage_rate_usd_day = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
        default=Decimal("0.00"),
    )
    despatch_rate_usd_day = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
        default=Decimal("0.00"),
    )
    counted_hours = serializers.DecimalField(
        max_digits=12,
        decimal_places=6,
        min_value=Decimal("0.000000"),
        required=False,
        default=None,
        allow_null=True,
    )

    def validate(self, attrs):
        dem = attrs.get("demurrage_rate_usd_day", Decimal("0.00"))
        des = attrs.get("despatch_rate_usd_day", Decimal("0.00"))
        if des > dem and dem > 0:
            raise serializers.ValidationError({
                "despatch_rate_usd_day": "Despatch rate cannot exceed demurrage rate."
            })
        return attrs
