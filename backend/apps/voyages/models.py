"""
backend/apps/voyages/models.py
============================================================
Django Models for Vessel Voyages & Fleet Tracking.
Maps directly to PostgreSQL 'shipping.vessel_voyages'.
"""

import uuid
from decimal import Decimal
from typing import Optional, List
from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError

from apps.contracts.models import Vessel, CharterContract
from apps.laytime.engine.fixture_factory import VoyageStatus
from apps.laytime.engine.settlement_engine import LaytimeOutcome
from .sof_enums import SOFEventType, IntervalCountStatus


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
    etl_date = models.DateField(null=True, blank=True, help_text="Estimated Time of Loading")
    atl_date = models.DateTimeField(null=True, blank=True, help_text="Actual Time of Loading (UTC)")
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

    # Bill of Lading
    bl_number = models.CharField(max_length=60, null=True, blank=True)
    bl_date = models.DateField(null=True, blank=True)
    bl_quantity_mt = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)

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

    def clean(self) -> None:
        """Enforces status transition legality and milestone chronology."""
        super().clean()
        from .state_machine import VoyageStateMachine

        # Check state transition if updating an existing record
        if self.pk:
            current = (
                VesselVoyage.objects.filter(pk=self.pk)
                .values_list("voyage_status", flat=True)
                .first()
            )
            if current and current != self.voyage_status:
                VoyageStateMachine.validate_transition(current, self.voyage_status)

        # Validate timestamp chronological monotonicity
        VoyageStateMachine.validate_timestamp_chronology(self)

    def save(self, *args, **kwargs) -> None:
        """Runs full_clean before saving unless explicitly skipped."""
        skip_clean = kwargs.pop("skip_clean", False)
        if not skip_clean:
            self.full_clean()
        super().save(*args, **kwargs)

    @property
    def allowed_transitions(self) -> List[str]:
        """Return list of valid target states from current status."""
        from .state_machine import VoyageStateMachine
        return VoyageStateMachine.get_allowed_transitions(self.voyage_status)

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


class StatementOfFacts(models.Model):
    """
    Chronological Statement of Facts (SOF) port event log line item.
    Maps directly to PostgreSQL 'shipping.statement_of_facts'.
    """
    sof_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    voyage = models.ForeignKey(
        VesselVoyage, on_delete=models.CASCADE, related_name="sof_events"
    )
    event_type = models.CharField(
        max_length=64,
        choices=SOFEventType.choices,
        help_text="Standardized maritime port event type",
    )
    event_timestamp = models.DateTimeField(
        help_text="Authoritative UTC timestamp when event occurred"
    )
    sequence_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Monotonically increasing sequence within voyage (auto-assigned if null)",
    )
    event_description = models.TextField(null=True, blank=True)
    recorded_by_agent = models.CharField(max_length=120, null=True, blank=True)
    crane_number = models.PositiveSmallIntegerField(null=True, blank=True)
    hatch_number = models.PositiveSmallIntegerField(null=True, blank=True)
    quantity_discharged_at_event = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True
    )

    # Interruption details
    interruption_cause = models.CharField(max_length=120, null=True, blank=True)
    weather_condition = models.CharField(max_length=60, null=True, blank=True)

    # Laytime calculation audit fields
    count_status = models.CharField(
        max_length=32, choices=IntervalCountStatus.choices, null=True, blank=True
    )
    count_fraction = models.DecimalField(
        max_digits=3, decimal_places=2, null=True, blank=True
    )
    counted_hours = models.DecimalField(
        max_digits=10, decimal_places=6, null=True, blank=True
    )

    # Amendment tracking
    is_amendment = models.BooleanField(default=False)
    amended_sof = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="amendments"
    )
    source_document_id = models.UUIDField(null=True, blank=True)

    submitted_at = models.DateTimeField(auto_now_add=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        db_table = "statement_of_facts"
        ordering = ["sequence_number", "event_timestamp"]
        constraints = [
            models.UniqueConstraint(
                fields=["voyage", "sequence_number"],
                name="uq_sof_voyage_sequence",
            )
        ]

    def clean(self) -> None:
        super().clean()
        if self.sequence_number is not None and self.sequence_number <= 0:
            raise ValidationError({"sequence_number": "Sequence number must be strictly positive."})
        if self.count_fraction is not None and not (Decimal("0.00") <= self.count_fraction <= Decimal("1.00")):
            raise ValidationError({"count_fraction": "Count fraction must be between 0.00 and 1.00."})
        if self.counted_hours is not None and self.counted_hours < 0:
            raise ValidationError({"counted_hours": "Counted hours must be non-negative."})
        if self.crane_number is not None and self.crane_number <= 0:
            raise ValidationError({"crane_number": "Crane number must be strictly positive."})
        if self.hatch_number is not None and self.hatch_number <= 0:
            raise ValidationError({"hatch_number": "Hatch number must be strictly positive."})
        if self.quantity_discharged_at_event is not None and self.quantity_discharged_at_event < 0:
            raise ValidationError({"quantity_discharged_at_event": "Quantity discharged must be non-negative."})
        if self.is_amendment:
            if not self.amended_sof_id:
                raise ValidationError({"amended_sof": "amended_sof is required when is_amendment is True."})
            if self.amended_sof_id == self.sof_id:
                raise ValidationError({"amended_sof": "An SOF event cannot amend itself."})

    def save(self, *args, **kwargs) -> None:
        if self.sequence_number is None:
            max_seq = (
                StatementOfFacts.objects.filter(voyage=self.voyage)
                .aggregate(models.Max("sequence_number"))["sequence_number__max"]
            )
            self.sequence_number = (max_seq or 0) + 1

        skip_clean = kwargs.pop("skip_clean", False)
        if not skip_clean:
            self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"SOF #{self.sequence_number}: {self.event_type} @ {self.event_timestamp.isoformat()}"


class LaytimeCalculation(models.Model):
    """
    Authoritative laytime computation & settlement result per voyage revision.
    Maps directly to PostgreSQL 'shipping.laytime_calculations'.
    """
    calc_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    voyage = models.ForeignKey(
        VesselVoyage, on_delete=models.CASCADE, related_name="laytime_calculations"
    )
    revision = models.PositiveIntegerField(default=1)
    is_current = models.BooleanField(default=True)

    outcome = models.CharField(
        max_length=32,
        choices=[(tag.value, tag.name) for tag in LaytimeOutcome],
        default=LaytimeOutcome.PENDING.value,
    )
    calculated_at = models.DateTimeField(null=True, blank=True)
    celery_task_id = models.CharField(max_length=64, null=True, blank=True)

    # Laytime accounting (fractional hours)
    allowed_laytime_hours = models.DecimalField(max_digits=12, decimal_places=6)
    total_port_hours = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    total_counted_hours = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    total_excepted_hours = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    total_disputed_hours = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal("0.000000"))

    variance_hours = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)

    # Financial settlement (USD)
    demurrage_rate_usd_per_day = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    despatch_rate_usd_per_day = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    gross_demurrage_usd = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    gross_despatch_usd = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    net_settlement_usd = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)

    demurrage_commenced_at = models.DateTimeField(null=True, blank=True)

    # SOF audit summary
    sof_event_count = models.PositiveIntegerField(null=True, blank=True)
    first_sof_event_at = models.DateTimeField(null=True, blank=True)
    last_sof_event_at = models.DateTimeField(null=True, blank=True)

    # Settlement details
    settled_at = models.DateTimeField(null=True, blank=True)
    settled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    settlement_notes = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "laytime_calculations"
        ordering = ["voyage", "-revision"]
        constraints = [
            models.UniqueConstraint(
                fields=["voyage"],
                condition=models.Q(is_current=True),
                name="uq_laytime_one_current_per_voyage",
            ),
            models.UniqueConstraint(
                fields=["voyage", "revision"],
                name="uq_laytime_voyage_revision",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.revision is not None and self.revision <= 0:
            raise ValidationError({"revision": "Revision must be positive."})
        if self.allowed_laytime_hours is not None and self.allowed_laytime_hours <= 0:
            raise ValidationError({"allowed_laytime_hours": "Allowed laytime hours must be positive."})
        if self.total_counted_hours is not None and self.total_counted_hours < 0:
            raise ValidationError({"total_counted_hours": "Total counted hours cannot be negative."})
        if self.total_excepted_hours is not None and self.total_excepted_hours < 0:
            raise ValidationError({"total_excepted_hours": "Total excepted hours cannot be negative."})
        if (
            self.gross_demurrage_usd is not None
            and self.gross_despatch_usd is not None
            and self.gross_demurrage_usd > 0
            and self.gross_despatch_usd > 0
        ):
            raise ValidationError("Gross demurrage and gross despatch cannot both be non-zero.")

    def save(self, *args, **kwargs) -> None:
        skip_clean = kwargs.pop("skip_clean", False)
        if not skip_clean:
            self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Laytime #{self.revision} [{self.outcome}] - {self.voyage.voyage_reference}"


LaytimeRecord = LaytimeCalculation


