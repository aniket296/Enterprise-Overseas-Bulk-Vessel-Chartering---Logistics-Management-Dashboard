"""
backend/apps/inventory/models.py
============================================================
Django Models for Steel Plant Raw Material Inventory & Runway Tracking.
Maps directly to PostgreSQL 'shipping.plant_inventory'.
"""

import uuid
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models


class RawMaterialCategory(models.TextChoices):
    """Raw material category matching PostgreSQL shipping.raw_material_category."""
    COKING_COAL = "COKING_COAL", "Coking Coal"
    PCI_COAL = "PCI_COAL", "PCI Coal"
    IRON_ORE_FINES = "IRON_ORE_FINES", "Iron Ore Fines"
    IRON_ORE_LUMP = "IRON_ORE_LUMP", "Iron Ore Lump"
    IRON_ORE_PELLETS = "IRON_ORE_PELLETS", "Iron Ore Pellets"
    SINTER_FEED = "SINTER_FEED", "Sinter Feed"
    LIMESTONE = "LIMESTONE", "Limestone"
    DOLOMITE = "DOLOMITE", "Dolomite"
    MANGANESE_ORE = "MANGANESE_ORE", "Manganese Ore"
    QUARTZITE = "QUARTZITE", "Quartzite"


class StockSafetyStatus(models.TextChoices):
    """Runway safety classification matching PostgreSQL shipping.stock_safety_status."""
    CRITICAL = "CRITICAL", "Critical (< 7 days)"
    PRECAUTIONARY = "PRECAUTIONARY", "Precautionary (7-14 days)"
    WATCH = "WATCH", "Watch (14-21 days)"
    ADEQUATE = "ADEQUATE", "Adequate (21-45 days)"
    SURPLUS = "SURPLUS", "Surplus (> 45 days)"


class PlantInventory(models.Model):
    """
    Daily raw material stock snapshot per integrated steel plant location.
    Maps directly to PostgreSQL 'shipping.plant_inventory'.
    """
    inventory_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Plant identification
    plant_code = models.CharField(
        max_length=20,
        validators=[
            RegexValidator(
                regex=r"^[A-Z0-9]{2,10}$",
                message="Plant code must be 2-10 uppercase alphanumeric characters.",
            )
        ],
        help_text="SAIL plant code: BSP=Bhilai, DSP=Durgapur, RSP=Rourkela, BSL=Bokaro, ISP=IISCO.",
    )
    plant_name = models.CharField(max_length=120)
    plant_location = models.CharField(max_length=120, null=True, blank=True)

    # Material
    material_category = models.CharField(
        max_length=32,
        choices=RawMaterialCategory.choices,
        help_text="Primary metallurgical raw material category",
    )
    material_description = models.CharField(max_length=120, null=True, blank=True)

    # Stock quantities (MT)
    stock_date = models.DateField(help_text="Authoritative date of daily stock take")
    opening_stock_mt = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(Decimal("0.000"))],
    )
    receipts_mt = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(Decimal("0.000"))],
        help_text="Discharged cargo received at plant today",
    )
    consumption_mt = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(Decimal("0.000"))],
        help_text="Blast furnace / sinter plant consumption today",
    )
    closing_stock_mt = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(Decimal("0.000"))],
        help_text="Closing stock = opening + receipts - consumption",
    )

    # Demand & Runway Forecasting
    daily_consumption_rate_mt = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.001"))],
        help_text="Rolling average daily blast furnace burn rate (MT/day)",
    )
    projected_receipts_mt = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(Decimal("0.000"))],
        help_text="Tonnage arriving from incoming vessels in transit",
    )
    stock_runway_days = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Days until stock-out at current burn rate, including projected receipts",
    )
    safety_status = models.CharField(
        max_length=32,
        choices=StockSafetyStatus.choices,
        default=StockSafetyStatus.ADEQUATE.value,
        help_text="Runway safety status evaluated from stock_runway_days",
    )

    # Supply Chain Drilldown
    contributing_voyage = models.ForeignKey(
        "voyages.VesselVoyage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_snapshots",
        help_text="Incoming vessel contributing to today's receipts or projected receipts",
    )

    # Audit
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "plant_inventory"
        ordering = ["-stock_date", "plant_code", "material_category"]
        constraints = [
            models.UniqueConstraint(
                fields=["plant_code", "material_category", "stock_date"],
                name="uq_plant_inventory_daily_snapshot",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        # Compute closing stock
        self.closing_stock_mt = self.opening_stock_mt + self.receipts_mt - self.consumption_mt
        if self.closing_stock_mt < Decimal("0.000"):
            raise ValidationError(
                {"consumption_mt": "Consumption cannot exceed available stock (opening + receipts)."}
            )

        # Compute stock runway days and safety status
        if self.daily_consumption_rate_mt and self.daily_consumption_rate_mt > Decimal("0.000"):
            total_avail = self.closing_stock_mt + (self.projected_receipts_mt or Decimal("0.000"))
            self.stock_runway_days = (total_avail / self.daily_consumption_rate_mt).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            # Evaluate safety status
            if self.stock_runway_days < Decimal("7.00"):
                self.safety_status = StockSafetyStatus.CRITICAL.value
            elif self.stock_runway_days < Decimal("14.00"):
                self.safety_status = StockSafetyStatus.PRECAUTIONARY.value
            elif self.stock_runway_days < Decimal("21.00"):
                self.safety_status = StockSafetyStatus.WATCH.value
            elif self.stock_runway_days <= Decimal("45.00"):
                self.safety_status = StockSafetyStatus.ADEQUATE.value
            else:
                self.safety_status = StockSafetyStatus.SURPLUS.value
        else:
            self.stock_runway_days = None

    def save(self, *args, **kwargs) -> None:
        skip_clean = kwargs.pop("skip_clean", False)
        if not skip_clean:
            self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.plant_code} [{self.material_category}] @ {self.stock_date}: {self.closing_stock_mt} MT ({self.stock_runway_days}d)"
