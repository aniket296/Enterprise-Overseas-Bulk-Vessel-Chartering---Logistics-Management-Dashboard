"""
backend/apps/voyages/models.py
============================================================
Django Models for Vessel Voyages & Fleet Tracking.
Maps directly to PostgreSQL 'shipping.vessel_voyages'.
"""

import uuid
from decimal import Decimal
from typing import Optional
from django.db import models
from django.core.validators import MinValueValidator

from apps.contracts.models import Vessel, CharterContract
from apps.laytime.engine.fixture_factory import VoyageStatus


class VesselVoyage(models.Model):
    """
    A single voyage leg executed under a charter contract.
    Matching shipping.vessel_voyages.
    """
    voyage_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    voyage_reference = models.CharField(max_length=80, unique=True)
    contract = models.ForeignKey(
        CharterContract, on_delete=models.RESTRICT, related_name="voyages"
    )
    vessel = models.ForeignKey(
        Vessel, on_delete=models.RESTRICT, related_name="voyages"
    )
    voyage_status = models.CharField(
        max_length=32,
        choices=[(tag.value, tag.name) for tag in VoyageStatus],
        default=VoyageStatus.SCHEDULED.value,
    )
    voyage_direction = models.CharField(max_length=16, default="LADEN")

    # Load Port
    load_port = models.CharField(max_length=120)
    load_country = models.CharField(max_length=2, default="AU")
    load_berth = models.CharField(max_length=60, null=True, blank=True)
    load_terminal = models.CharField(max_length=120, null=True, blank=True)
    quantity_loaded_mt = models.DecimalField(
        max_digits=10, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))]
    )

    # Discharge Port
    discharge_port = models.CharField(max_length=120)
    discharge_country = models.CharField(max_length=2, default="IN")
    discharge_berth = models.CharField(max_length=60, null=True, blank=True)
    discharge_terminal = models.CharField(max_length=120, null=True, blank=True)
    quantity_discharged_mt = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True
    )

    # Timestamps (UTC)
    eta_date = models.DateTimeField(help_text="Estimated Time of Arrival at discharge port")
    ata_date = models.DateTimeField(null=True, blank=True, help_text="Actual Time of Arrival")
    nor_tendered_at = models.DateTimeField(null=True, blank=True)
    nor_accepted_at = models.DateTimeField(null=True, blank=True)
    berthed_at = models.DateTimeField(null=True, blank=True)
    discharge_commenced_at = models.DateTimeField(null=True, blank=True)
    discharge_completed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Vessel Position
    last_position_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_position_lon = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_position_updated = models.DateTimeField(null=True, blank=True)
    speed_over_ground = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "vessel_voyages"
        ordering = ["-eta_date"]

    @property
    def shortage_mt(self) -> Optional[Decimal]:
        """Computed shortage: loaded - discharged, floored at 0."""
        if self.quantity_loaded_mt is not None and self.quantity_discharged_mt is not None:
            return max(self.quantity_loaded_mt - self.quantity_discharged_mt, Decimal("0.000"))
        return None

    @property
    def is_active(self) -> bool:
        """True if voyage is underway or at port operations."""
        return self.voyage_status not in (VoyageStatus.COMPLETED.value, VoyageStatus.CANCELLED.value)

    @property
    def progress_pct(self) -> float:
        """
        Dynamically calculate milestone completion percentage:
          - SCHEDULED: 0%
          - IN_TRANSIT: 25%
          - ARRIVED / NOR_TENDERED: 50%
          - BERTHED / COMMENCING: 65%
          - DISCHARGING: 65% - 95% based on outturn discharge
          - COMPLETED: 100%
        """
        st = self.voyage_status
        if st == VoyageStatus.SCHEDULED.value:
            return 0.0
        elif st == VoyageStatus.IN_TRANSIT.value:
            return 25.0
        elif st in (VoyageStatus.ARRIVED.value, VoyageStatus.NOR_TENDERED.value):
            return 50.0
        elif st in (VoyageStatus.BERTHED.value, VoyageStatus.COMMENCING.value):
            return 65.0
        elif st == VoyageStatus.DISCHARGING.value:
            if self.quantity_loaded_mt and self.quantity_discharged_mt:
                discharge_fraction = float(self.quantity_discharged_mt / self.quantity_loaded_mt)
                return min(round(65.0 + 30.0 * discharge_fraction, 1), 98.0)
            return 75.0
        elif st == VoyageStatus.COMPLETED.value:
            return 100.0
        return 0.0

    def __str__(self) -> str:
        return f"{self.voyage_reference} - {self.vessel.vessel_name} [{self.voyage_status}]"
