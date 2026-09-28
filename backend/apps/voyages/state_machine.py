"""
backend/apps/voyages/state_machine.py
============================================================
Operational Milestone State Machine for Vessel Voyages.
Enforces the 10-state lifecycle graph per PostgreSQL trigger
'shipping.fn_voyage_status_guard' and DB integrity constraints.

Valid transitions:
  SCHEDULED    -> IN_TRANSIT, CANCELLED
  IN_TRANSIT   -> ARRIVED, DEVIATED, CANCELLED
  ARRIVED      -> NOR_TENDERED, DEVIATED, CANCELLED
  NOR_TENDERED -> BERTHED, ARRIVED (NOR rejected / re-anchor)
  BERTHED      -> COMMENCING
  COMMENCING   -> DISCHARGING
  DISCHARGING  -> COMPLETED, DEVIATED
  DEVIATED     -> IN_TRANSIT, ARRIVED, CANCELLED
  COMPLETED    -> (terminal)
  CANCELLED    -> (terminal)
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Any, TYPE_CHECKING
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.laytime.engine.fixture_factory import VoyageStatus

if TYPE_CHECKING:
    from .models import VesselVoyage


class InvalidStateTransitionError(ValidationError):
    """Raised when an illegal voyage state transition is attempted."""

    def __init__(
        self,
        current_status: str,
        target_status: str,
        allowed_transitions: List[str],
        message: Optional[str] = None,
    ):
        self.current_status = current_status
        self.target_status = target_status
        self.allowed_transitions = allowed_transitions
        msg = (
            message
            or f"Invalid voyage status transition: '{current_status}' -> '{target_status}'. "
            f"Allowed next states: {allowed_transitions}."
        )
        super().__init__(msg)


class MilestoneChronologyError(ValidationError):
    """Raised when milestone timestamps violate temporal monotonicity."""

    def __init__(
        self,
        prior_milestone: str,
        subsequent_milestone: str,
        prior_time: datetime.datetime,
        subsequent_time: datetime.datetime,
        message: Optional[str] = None,
    ):
        self.prior_milestone = prior_milestone
        self.subsequent_milestone = subsequent_milestone
        self.prior_time = prior_time
        self.subsequent_time = subsequent_time
        msg = (
            message
            or f"Milestone chronology violation: '{subsequent_milestone}' ({subsequent_time.isoformat()}) "
            f"cannot precede '{prior_milestone}' ({prior_time.isoformat()})."
        )
        super().__init__(msg)


class VoyageStateMachine:
    """
    State machine validator and transition engine for bulk vessel voyages.
    Strictly mirrors DB trigger 'shipping.fn_voyage_status_guard' and
    chk_voyages_* integrity constraints.
    """

    ALLOWED_TRANSITIONS: Dict[str, List[str]] = {
        VoyageStatus.SCHEDULED.value: [
            VoyageStatus.IN_TRANSIT.value,
            VoyageStatus.CANCELLED.value,
        ],
        VoyageStatus.IN_TRANSIT.value: [
            VoyageStatus.ARRIVED.value,
            VoyageStatus.DEVIATED.value,
            VoyageStatus.CANCELLED.value,
        ],
        VoyageStatus.ARRIVED.value: [
            VoyageStatus.NOR_TENDERED.value,
            VoyageStatus.DEVIATED.value,
            VoyageStatus.CANCELLED.value,
        ],
        VoyageStatus.NOR_TENDERED.value: [
            VoyageStatus.BERTHED.value,
            VoyageStatus.ARRIVED.value,  # NOR rejected -> return to anchorage
        ],
        VoyageStatus.BERTHED.value: [
            VoyageStatus.COMMENCING.value,
        ],
        VoyageStatus.COMMENCING.value: [
            VoyageStatus.DISCHARGING.value,
        ],
        VoyageStatus.DISCHARGING.value: [
            VoyageStatus.COMPLETED.value,
            VoyageStatus.DEVIATED.value,
        ],
        VoyageStatus.DEVIATED.value: [
            VoyageStatus.IN_TRANSIT.value,
            VoyageStatus.ARRIVED.value,
            VoyageStatus.CANCELLED.value,
        ],
        VoyageStatus.COMPLETED.value: [],
        VoyageStatus.CANCELLED.value: [],
    }

    @classmethod
    def get_allowed_transitions(cls, current_status: str) -> List[str]:
        """Return list of valid next status values from the given current status."""
        return cls.ALLOWED_TRANSITIONS.get(current_status, [])

    @classmethod
    def is_valid_transition(cls, current_status: str, target_status: str) -> bool:
        """Return True if transition from current_status to target_status is permitted."""
        if current_status == target_status:
            return True
        allowed = cls.get_allowed_transitions(current_status)
        return target_status in allowed

    @classmethod
    def validate_transition(cls, current_status: str, target_status: str) -> None:
        """
        Validate transition; raises InvalidStateTransitionError if illegal.
        """
        if current_status == target_status:
            return

        allowed = cls.get_allowed_transitions(current_status)
        if target_status not in allowed:
            raise InvalidStateTransitionError(
                current_status=current_status,
                target_status=target_status,
                allowed_transitions=allowed,
            )

    @classmethod
    def validate_timestamp_chronology(cls, voyage: VesselVoyage) -> None:
        """
        Validates that all recorded milestones preserve strict chronological monotonicity
        as enforced by PostgreSQL chk_voyages_* constraints.

        Rule 1: ata_date >= eta_date - 7 days (sanity window)
        Rule 2: nor_tendered_at >= ata_date
        Rule 3: nor_accepted_at >= nor_tendered_at
        Rule 4: berthed_at >= ata_date and (if nor_accepted_at) berthed_at >= nor_accepted_at
        Rule 5: discharge_commenced_at >= berthed_at
        Rule 6: discharge_completed_at >= discharge_commenced_at
        Rule 7: completed_at >= discharge_completed_at
        """
        # Rule 1: ATA vs ETA sanity check (within 7 days earlier)
        if voyage.ata_date and voyage.eta_date:
            min_ata = voyage.eta_date - datetime.timedelta(days=7)
            if voyage.ata_date < min_ata:
                raise MilestoneChronologyError(
                    prior_milestone="eta_date - 7d",
                    subsequent_milestone="ata_date",
                    prior_time=min_ata,
                    subsequent_time=voyage.ata_date,
                    message=f"ata_date ({voyage.ata_date.isoformat()}) cannot precede eta_date by more than 7 days ({voyage.eta_date.isoformat()}).",
                )

        # Rule 2: NOR tendered >= ATA
        if voyage.nor_tendered_at and voyage.ata_date:
            if voyage.nor_tendered_at < voyage.ata_date:
                raise MilestoneChronologyError(
                    prior_milestone="ata_date",
                    subsequent_milestone="nor_tendered_at",
                    prior_time=voyage.ata_date,
                    subsequent_time=voyage.nor_tendered_at,
                )

        # Rule 3: NOR accepted >= NOR tendered
        if voyage.nor_accepted_at and voyage.nor_tendered_at:
            if voyage.nor_accepted_at < voyage.nor_tendered_at:
                raise MilestoneChronologyError(
                    prior_milestone="nor_tendered_at",
                    subsequent_milestone="nor_accepted_at",
                    prior_time=voyage.nor_tendered_at,
                    subsequent_time=voyage.nor_accepted_at,
                )

        # Rule 4: Berthed >= ATA and Berthed >= NOR accepted (if set)
        if voyage.berthed_at and voyage.ata_date:
            if voyage.berthed_at < voyage.ata_date:
                raise MilestoneChronologyError(
                    prior_milestone="ata_date",
                    subsequent_milestone="berthed_at",
                    prior_time=voyage.ata_date,
                    subsequent_time=voyage.berthed_at,
                )
        if voyage.berthed_at and voyage.nor_accepted_at:
            if voyage.berthed_at < voyage.nor_accepted_at:
                raise MilestoneChronologyError(
                    prior_milestone="nor_accepted_at",
                    subsequent_milestone="berthed_at",
                    prior_time=voyage.nor_accepted_at,
                    subsequent_time=voyage.berthed_at,
                )

        # Rule 5: Discharge commenced >= Berthed
        if voyage.discharge_commenced_at and voyage.berthed_at:
            if voyage.discharge_commenced_at < voyage.berthed_at:
                raise MilestoneChronologyError(
                    prior_milestone="berthed_at",
                    subsequent_milestone="discharge_commenced_at",
                    prior_time=voyage.berthed_at,
                    subsequent_time=voyage.discharge_commenced_at,
                )

        # Rule 6: Discharge completed >= Discharge commenced
        if voyage.discharge_completed_at and voyage.discharge_commenced_at:
            if voyage.discharge_completed_at < voyage.discharge_commenced_at:
                raise MilestoneChronologyError(
                    prior_milestone="discharge_commenced_at",
                    subsequent_milestone="discharge_completed_at",
                    prior_time=voyage.discharge_commenced_at,
                    subsequent_time=voyage.discharge_completed_at,
                )

        # Rule 7: Completed at >= Discharge completed
        if voyage.completed_at and voyage.discharge_completed_at:
            if voyage.completed_at < voyage.discharge_completed_at:
                raise MilestoneChronologyError(
                    prior_milestone="discharge_completed_at",
                    subsequent_milestone="completed_at",
                    prior_time=voyage.discharge_completed_at,
                    subsequent_time=voyage.completed_at,
                )

    @classmethod
    def apply_transition(
        cls,
        voyage: VesselVoyage,
        target_status: str,
        timestamp: Optional[datetime.datetime] = None,
        remarks: Optional[str] = None,
        **kwargs: Any,
    ) -> VesselVoyage:
        """
        Applies a state transition to the given voyage instance:
          1. Validates legal transition from current status.
          2. Auto-populates or updates milestone timestamps for target status.
          3. Validates timestamp chronology.
          4. Saves the voyage.
          5. Returns the updated instance.
        """
        current_status = voyage.voyage_status
        cls.validate_transition(current_status, target_status)

        ts = timestamp or timezone.now()

        # Update milestone timestamps according to target state
        if target_status == VoyageStatus.ARRIVED.value:
            if not voyage.ata_date:
                voyage.ata_date = kwargs.get("ata_date", ts)

        elif target_status == VoyageStatus.NOR_TENDERED.value:
            if not voyage.nor_tendered_at:
                voyage.nor_tendered_at = kwargs.get("nor_tendered_at", ts)
            if "nor_accepted_at" in kwargs and kwargs["nor_accepted_at"]:
                voyage.nor_accepted_at = kwargs["nor_accepted_at"]

        elif target_status == VoyageStatus.BERTHED.value:
            if not voyage.berthed_at:
                voyage.berthed_at = kwargs.get("berthed_at", ts)

        elif target_status == VoyageStatus.COMMENCING.value:
            if not voyage.discharge_commenced_at:
                voyage.discharge_commenced_at = kwargs.get("discharge_commenced_at", ts)

        elif target_status == VoyageStatus.DISCHARGING.value:
            if "quantity_discharged_mt" in kwargs and kwargs["quantity_discharged_mt"] is not None:
                voyage.quantity_discharged_mt = Decimal(str(kwargs["quantity_discharged_mt"]))

        elif target_status == VoyageStatus.COMPLETED.value:
            if not voyage.discharge_completed_at:
                voyage.discharge_completed_at = kwargs.get("discharge_completed_at", ts)
            if not voyage.completed_at:
                voyage.completed_at = kwargs.get("completed_at", ts)
            if "quantity_discharged_mt" in kwargs and kwargs["quantity_discharged_mt"] is not None:
                voyage.quantity_discharged_mt = Decimal(str(kwargs["quantity_discharged_mt"]))

        # Apply any explicit timestamp overrides passed in kwargs
        for ts_field in [
            "ata_date",
            "nor_tendered_at",
            "nor_accepted_at",
            "berthed_at",
            "discharge_commenced_at",
            "discharge_completed_at",
            "completed_at",
        ]:
            if ts_field in kwargs and kwargs[ts_field] is not None:
                setattr(voyage, ts_field, kwargs[ts_field])

        voyage.voyage_status = target_status

        # Validate chronological integrity
        cls.validate_timestamp_chronology(voyage)

        # Save to database (full_clean will also execute)
        voyage.save()
        return voyage
