"""
backend/apps/contracts/models.py
============================================================
Django Models for Charter Contracts & Vessel Registry.
Maps directly to PostgreSQL 'shipping.vessels' and 'shipping.charter_contracts'.
"""

import uuid
from decimal import Decimal
from django.db import models
from django.core.validators import RegexValidator, MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError

from apps.laytime.engine.constants import LaytimeTerms
from apps.laytime.engine.allowed_laytime import AllowedLaytimeEngine
from apps.laytime.engine.fixture_factory import (
    VesselSizeClass,
    CargoType,
    CharterPartyType,
    ContractStatus,
)


class Vessel(models.Model):
    """
    Vessel Registry model matching shipping.vessels.
    """
    vessel_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vessel_name = models.CharField(max_length=128)
    imo_number = models.CharField(
        max_length=7,
        unique=True,
        validators=[
            RegexValidator(
                regex=r"^\d{7}$",
                message="IMO number must be exactly 7 digits.",
            )
        ],
    )
    flag_state = models.CharField(
        max_length=2,
        validators=[
            RegexValidator(
                regex=r"^[A-Z]{2}$",
                message="Flag state must be 2 uppercase ISO country code characters.",
            )
        ],
    )
    size_class = models.CharField(
        max_length=32,
        choices=[(tag.value, tag.name) for tag in VesselSizeClass],
        default=VesselSizeClass.PANAMAX.value,
    )
    deadweight_tonnage = models.DecimalField(
        max_digits=10, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))]
    )
    gross_tonnage = models.DecimalField(
        max_digits=10, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))]
    )
    net_tonnage = models.DecimalField(
        max_digits=10, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))]
    )
    length_overall_meters = models.DecimalField(
        max_digits=6, decimal_places=2, validators=[MinValueValidator(Decimal("1.00"))]
    )
    beam_meters = models.DecimalField(
        max_digits=5, decimal_places=2, validators=[MinValueValidator(Decimal("1.00"))]
    )
    draft_meters = models.DecimalField(
        max_digits=5, decimal_places=2, validators=[MinValueValidator(Decimal("0.50"))]
    )
    hold_count = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(15)]
    )
    hatch_count = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(15)]
    )
    build_year = models.PositiveIntegerField(
        validators=[MinValueValidator(1970), MaxValueValidator(2035)]
    )
    owner_name = models.CharField(max_length=256)
    service_speed_knots = models.DecimalField(
        max_digits=4, decimal_places=1, default=Decimal("14.0")
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "vessels"
        ordering = ["vessel_name"]

    def clean(self) -> None:
        if self.hatch_count > self.hold_count:
            raise ValidationError(
                {"hatch_count": f"Hatch count ({self.hatch_count}) cannot exceed hold count ({self.hold_count})."}
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.vessel_name} (IMO: {self.imo_number})"


class CharterContract(models.Model):
    """
    Charter Party Contract specification matching shipping.charter_contracts.
    """
    contract_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contract_reference = models.CharField(max_length=64, unique=True)
    charter_party_type = models.CharField(
        max_length=32,
        choices=[(tag.value, tag.name) for tag in CharterPartyType],
        default=CharterPartyType.VOYAGE_CHARTER.value,
    )
    contract_status = models.CharField(
        max_length=32,
        choices=[(tag.value, tag.name) for tag in ContractStatus],
        default=ContractStatus.ACTIVE.value,
    )
    charterer_entity = models.CharField(max_length=256)
    charterer_plant = models.CharField(max_length=128)
    vessel = models.ForeignKey(
        Vessel, on_delete=models.RESTRICT, related_name="contracts"
    )
    cargo_type = models.CharField(
        max_length=32,
        choices=[(tag.value, tag.name) for tag in CargoType],
        default=CargoType.COKING_COAL.value,
    )
    nominated_quantity_mt = models.DecimalField(
        max_digits=10, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))]
    )
    tolerance_pct = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal("5.00"),
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("15.00"))]
    )
    freight_rate_usd_per_mt = models.DecimalField(
        max_digits=8, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    load_port = models.CharField(max_length=128)
    discharge_port = models.CharField(max_length=128)
    discharge_berth_designated = models.CharField(max_length=64, null=True, blank=True)
    discharge_rate_mt_per_day = models.DecimalField(
        max_digits=10, decimal_places=3, validators=[MinValueValidator(Decimal("100.000"))]
    )
    laytime_terms = models.CharField(
        max_length=32,
        choices=[(tag.value, tag.name) for tag in LaytimeTerms],
        default=LaytimeTerms.WWDSHEX.value,
    )
    demurrage_rate_usd_per_day = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))]
    )
    despatch_rate_usd_per_day = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))]
    )
    laycan_start = models.DateField()
    laycan_end = models.DateField()
    turn_time_hours = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("6.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    once_on_demurrage_always_on = models.BooleanField(default=True)
    allowed_laytime_hours = models.DecimalField(
        max_digits=12, decimal_places=6, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "charter_contracts"
        ordering = ["-created_at"]

    def clean(self) -> None:
        if self.laycan_start and self.laycan_end:
            if self.laycan_start > self.laycan_end:
                raise ValidationError(
                    {"laycan_start": f"laycan_start ({self.laycan_start}) cannot be after laycan_end ({self.laycan_end})."}
                )
            if (self.laycan_end - self.laycan_start).days > 31:
                raise ValidationError(
                    {"laycan_end": f"Laycan window cannot exceed 31 days (got {(self.laycan_end - self.laycan_start).days} days)."}
                )

        if self.despatch_rate_usd_per_day > self.demurrage_rate_usd_per_day:
            raise ValidationError(
                {"despatch_rate_usd_per_day": f"despatch_rate ({self.despatch_rate_usd_per_day}) cannot exceed demurrage_rate ({self.demurrage_rate_usd_per_day})."}
            )

    def compute_specifications(self) -> dict:
        """
        Run AllowedLaytimeEngine to calculate mathematical laytime specs.
        """
        engine = AllowedLaytimeEngine(
            cargo_mt_nominated=self.nominated_quantity_mt,
            discharge_rate_mt_day=self.discharge_rate_mt_per_day,
            tolerance_pct=self.tolerance_pct,
            laytime_terms=LaytimeTerms(self.laytime_terms),
            override_allowed_hours=self.allowed_laytime_hours,
        )
        calc_result = engine.compute()
        return {
            "contract_id": str(self.contract_id),
            "contract_reference": self.contract_reference,
            "vessel_name": self.vessel.vessel_name,
            "vessel_imo": self.vessel.imo_number,
            "cargo_type": self.cargo_type,
            "nominated_quantity_mt": str(self.nominated_quantity_mt),
            "tolerance_pct": str(self.tolerance_pct),
            "cargo_min_mt": str(calc_result.cargo_mt_min),
            "cargo_max_mt": str(calc_result.cargo_mt_max),
            "discharge_rate_mt_day": str(self.discharge_rate_mt_per_day),
            "laytime_terms": self.laytime_terms,
            "is_fixed_laytime": calc_result.is_fixed_laytime,
            "allowed_laytime_hours": str(calc_result.allowed_hours_nominated) if calc_result.allowed_hours_nominated else None,
            "allowed_laytime_days": str(calc_result.days_nominated) if calc_result.days_nominated else None,
            "allowed_hours_min": str(calc_result.allowed_hours_min) if calc_result.allowed_hours_min else None,
            "allowed_hours_max": str(calc_result.allowed_hours_max) if calc_result.allowed_hours_max else None,
            "demurrage_rate_usd_day": str(self.demurrage_rate_usd_per_day),
            "despatch_rate_usd_day": str(self.despatch_rate_usd_per_day),
            "hourly_demurrage_rate_usd": str((self.demurrage_rate_usd_per_day / Decimal("24")).quantize(Decimal("0.01"))),
            "hourly_despatch_rate_usd": str((self.despatch_rate_usd_per_day / Decimal("24")).quantize(Decimal("0.01"))),
            "laycan_start": str(self.laycan_start),
            "laycan_end": str(self.laycan_end),
            "laycan_duration_days": (self.laycan_end - self.laycan_start).days + 1,
            "turn_time_hours": str(self.turn_time_hours),
            "once_on_demurrage_always_on": self.once_on_demurrage_always_on,
            "formula_string": calc_result.formula_string,
        }

    def save(self, *args, **kwargs):
        self.clean()
        # Automatically compute allowed laytime if not fixed
        if self.allowed_laytime_hours is None and self.nominated_quantity_mt and self.discharge_rate_mt_per_day:
            engine = AllowedLaytimeEngine(
                cargo_mt_nominated=self.nominated_quantity_mt,
                discharge_rate_mt_day=self.discharge_rate_mt_per_day,
                tolerance_pct=self.tolerance_pct,
                laytime_terms=LaytimeTerms(self.laytime_terms),
            )
            calc = engine.compute()
            if calc.allowed_hours_nominated:
                self.allowed_laytime_hours = calc.allowed_hours_nominated
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.contract_reference} - {self.vessel.vessel_name} ({self.cargo_type})"
