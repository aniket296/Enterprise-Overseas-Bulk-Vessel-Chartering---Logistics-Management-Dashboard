"""
backend/apps/voyages/tests/test_state_machine.py
============================================================
Exhaustive Test Suite for Operational Milestone State Machine.
Validates:
  1. Valid state transitions and linear lifecycle execution
  2. Non-linear branches (NOR rejection, deviation recovery, cancellations)
  3. Terminal state immutability (COMPLETED, CANCELLED)
  4. Illegal state skips and backward transitions
  5. Milestone timestamp chronological monotonicity
  6. RESTful transition API endpoints (GET & POST)
"""

import uuid
import datetime
from decimal import Decimal
import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.contracts.models import Vessel, CharterContract
from apps.voyages.models import VesselVoyage
from apps.voyages.state_machine import (
    VoyageStateMachine,
    InvalidStateTransitionError,
    MilestoneChronologyError,
)
from apps.laytime.engine.fixture_factory import VoyageStatus


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def base_vessel(db):
    return Vessel.objects.create(
        vessel_name="MV State Pioneer",
        imo_number="9871111",
        flag_state="PA",
        build_year=2020,
        deadweight_tonnage=Decimal("82000.000"),
        gross_tonnage=Decimal("43000.000"),
        net_tonnage=Decimal("25000.000"),
        length_overall_meters=Decimal("229.00"),
        beam_meters=Decimal("32.26"),
        draft_meters=Decimal("14.50"),
        hold_count=7,
        hatch_count=7,
        owner_name="Pacific Shipping Co.",
        service_speed_knots=Decimal("14.2"),
    )


@pytest.fixture
def base_contract(db, base_vessel):
    return CharterContract.objects.create(
        contract_reference="SAIL/COA/2025/TEST-001",
        vessel=base_vessel,
        charterer_entity="STEEL_AUTHORITY_OF_INDIA",
        charterer_plant="BOKARO",
        cargo_type="COKING_COAL",
        charter_party_type="VOYAGE_CHARTER",
        contract_status="ACTIVE",
        laycan_start=datetime.date(2025, 6, 1),
        laycan_end=datetime.date(2025, 6, 10),
        load_port="Port Hedland",
        discharge_port="Haldia",
        nominated_quantity_mt=Decimal("75000.000"),
        laytime_terms="WWDSHEX",
        discharge_rate_mt_per_day=Decimal("15000.000"),
        turn_time_hours=Decimal("6.00"),
        demurrage_rate_usd_per_day=Decimal("25000.00"),
        despatch_rate_usd_per_day=Decimal("12500.00"),
        freight_rate_usd_per_mt=Decimal("18.50"),
        allowed_laytime_hours=Decimal("120.000000"),
    )


@pytest.fixture
def scheduled_voyage(db, base_contract, base_vessel):
    eta = timezone.now() + datetime.timedelta(days=2)
    return VesselVoyage.objects.create(
        voyage_reference="SAIL/VOY/TEST/001",
        contract=base_contract,
        vessel=base_vessel,
        voyage_status=VoyageStatus.SCHEDULED.value,
        load_port="Port Hedland",
        load_country="AU",
        discharge_port="Haldia",
        discharge_country="IN",
        quantity_loaded_mt=Decimal("75000.000"),
        eta_date=eta,
    )


# =============================================================================
# 1. State Machine Transitions Unit Tests
# =============================================================================

@pytest.mark.django_db
class TestStateMachineTransitions:

    def test_valid_linear_lifecycle(self, scheduled_voyage):
        """
        Verify the complete golden path:
        SCHEDULED -> IN_TRANSIT -> ARRIVED -> NOR_TENDERED -> BERTHED -> COMMENCING -> DISCHARGING -> COMPLETED
        """
        v = scheduled_voyage
        base_time = timezone.now()

        # Step 1: SCHEDULED -> IN_TRANSIT
        t1 = base_time + datetime.timedelta(hours=1)
        v = VoyageStateMachine.apply_transition(v, VoyageStatus.IN_TRANSIT.value, timestamp=t1)
        assert v.voyage_status == VoyageStatus.IN_TRANSIT.value
        assert v.progress_pct == 25.0

        # Step 2: IN_TRANSIT -> ARRIVED
        t2 = base_time + datetime.timedelta(days=1)
        v = VoyageStateMachine.apply_transition(v, VoyageStatus.ARRIVED.value, timestamp=t2)
        assert v.voyage_status == VoyageStatus.ARRIVED.value
        assert v.ata_date == t2
        assert v.progress_pct == 50.0

        # Step 3: ARRIVED -> NOR_TENDERED
        t3 = t2 + datetime.timedelta(hours=2)
        v = VoyageStateMachine.apply_transition(v, VoyageStatus.NOR_TENDERED.value, timestamp=t3)
        assert v.voyage_status == VoyageStatus.NOR_TENDERED.value
        assert v.nor_tendered_at == t3
        assert v.progress_pct == 50.0

        # Step 4: NOR_TENDERED -> BERTHED
        t4 = t3 + datetime.timedelta(hours=8)
        v = VoyageStateMachine.apply_transition(v, VoyageStatus.BERTHED.value, timestamp=t4)
        assert v.voyage_status == VoyageStatus.BERTHED.value
        assert v.berthed_at == t4
        assert v.progress_pct == 65.0

        # Step 5: BERTHED -> COMMENCING
        t5 = t4 + datetime.timedelta(hours=3)
        v = VoyageStateMachine.apply_transition(v, VoyageStatus.COMMENCING.value, timestamp=t5)
        assert v.voyage_status == VoyageStatus.COMMENCING.value
        assert v.discharge_commenced_at == t5
        assert v.progress_pct == 65.0

        # Step 6: COMMENCING -> DISCHARGING
        t6 = t5 + datetime.timedelta(hours=1)
        v = VoyageStateMachine.apply_transition(
            v, VoyageStatus.DISCHARGING.value, timestamp=t6, quantity_discharged_mt=Decimal("37500.000")
        )
        assert v.voyage_status == VoyageStatus.DISCHARGING.value
        assert v.quantity_discharged_mt == Decimal("37500.000")
        assert v.progress_pct == 80.0  # 65 + 30 * 0.5

        # Step 7: DISCHARGING -> COMPLETED
        t7 = t6 + datetime.timedelta(days=3)
        v = VoyageStateMachine.apply_transition(
            v, VoyageStatus.COMPLETED.value, timestamp=t7, quantity_discharged_mt=Decimal("74850.000")
        )
        assert v.voyage_status == VoyageStatus.COMPLETED.value
        assert v.discharge_completed_at == t7
        assert v.completed_at == t7
        assert v.quantity_discharged_mt == Decimal("74850.000")
        assert v.shortage_mt == Decimal("150.000")
        assert v.progress_pct == 100.0
        assert not v.is_active

    def test_nor_rejection_branch(self, scheduled_voyage):
        """NOR rejected at berth leads back from NOR_TENDERED to ARRIVED."""
        v = scheduled_voyage
        now = timezone.now()
        VoyageStateMachine.apply_transition(v, VoyageStatus.IN_TRANSIT.value, timestamp=now)
        VoyageStateMachine.apply_transition(v, VoyageStatus.ARRIVED.value, timestamp=now + datetime.timedelta(days=1))
        VoyageStateMachine.apply_transition(v, VoyageStatus.NOR_TENDERED.value, timestamp=now + datetime.timedelta(days=1, hours=2))

        # Rejection -> back to ARRIVED
        VoyageStateMachine.apply_transition(v, VoyageStatus.ARRIVED.value, timestamp=now + datetime.timedelta(days=1, hours=4))
        assert v.voyage_status == VoyageStatus.ARRIVED.value

        # Re-tender NOR -> back to NOR_TENDERED
        VoyageStateMachine.apply_transition(v, VoyageStatus.NOR_TENDERED.value, timestamp=now + datetime.timedelta(days=1, hours=6))
        assert v.voyage_status == VoyageStatus.NOR_TENDERED.value

    def test_deviation_and_recovery(self, scheduled_voyage):
        """Test vessel deviation and recovery back to transit or arrived."""
        v = scheduled_voyage
        now = timezone.now()
        VoyageStateMachine.apply_transition(v, VoyageStatus.IN_TRANSIT.value, timestamp=now)

        # Deviate due to cyclone
        VoyageStateMachine.apply_transition(v, VoyageStatus.DEVIATED.value, timestamp=now + datetime.timedelta(days=2))
        assert v.voyage_status == VoyageStatus.DEVIATED.value

        # Resume transit
        VoyageStateMachine.apply_transition(v, VoyageStatus.IN_TRANSIT.value, timestamp=now + datetime.timedelta(days=4))
        assert v.voyage_status == VoyageStatus.IN_TRANSIT.value

    def test_cancellation_paths(self, scheduled_voyage):
        """Cancellation is valid from SCHEDULED, IN_TRANSIT, ARRIVED, or DEVIATED."""
        # 1. From SCHEDULED
        v1 = scheduled_voyage
        VoyageStateMachine.apply_transition(v1, VoyageStatus.CANCELLED.value)
        assert v1.voyage_status == VoyageStatus.CANCELLED.value

        # 2. From IN_TRANSIT
        v2 = VesselVoyage.objects.create(
            voyage_reference="SAIL/VOY/TEST/CANCEL-02",
            contract=v1.contract,
            vessel=v1.vessel,
            voyage_status=VoyageStatus.SCHEDULED.value,
            load_port="Port Hedland",
            discharge_port="Haldia",
            quantity_loaded_mt=Decimal("50000.000"),
            eta_date=timezone.now() + datetime.timedelta(days=5),
        )
        VoyageStateMachine.apply_transition(v2, VoyageStatus.IN_TRANSIT.value)
        VoyageStateMachine.apply_transition(v2, VoyageStatus.CANCELLED.value)
        assert v2.voyage_status == VoyageStatus.CANCELLED.value

    def test_terminal_states_reject_any_further_transitions(self, scheduled_voyage):
        """COMPLETED and CANCELLED are strictly terminal."""
        v = scheduled_voyage
        VoyageStateMachine.apply_transition(v, VoyageStatus.CANCELLED.value)
        assert v.allowed_transitions == []

        for target in [VoyageStatus.SCHEDULED, VoyageStatus.IN_TRANSIT, VoyageStatus.ARRIVED]:
            with pytest.raises(InvalidStateTransitionError) as exc_info:
                VoyageStateMachine.apply_transition(v, target.value)
            assert exc_info.value.current_status == VoyageStatus.CANCELLED.value
            assert exc_info.value.target_status == target.value

    def test_illegal_skips_rejected(self, scheduled_voyage):
        """Direct skips across multiple stages must be rejected."""
        v = scheduled_voyage
        # From SCHEDULED cannot go directly to BERTHED or DISCHARGING
        with pytest.raises(InvalidStateTransitionError):
            VoyageStateMachine.apply_transition(v, VoyageStatus.BERTHED.value)

        with pytest.raises(InvalidStateTransitionError):
            VoyageStateMachine.apply_transition(v, VoyageStatus.DISCHARGING.value)

        with pytest.raises(InvalidStateTransitionError):
            VoyageStateMachine.apply_transition(v, VoyageStatus.COMPLETED.value)

    def test_backward_transitions_rejected(self, scheduled_voyage):
        """Cannot jump backward to unapproved prior states."""
        v = scheduled_voyage
        now = timezone.now()
        VoyageStateMachine.apply_transition(v, VoyageStatus.IN_TRANSIT.value, timestamp=now)
        VoyageStateMachine.apply_transition(v, VoyageStatus.ARRIVED.value, timestamp=now + datetime.timedelta(days=1))
        VoyageStateMachine.apply_transition(v, VoyageStatus.NOR_TENDERED.value, timestamp=now + datetime.timedelta(days=1, hours=2))
        VoyageStateMachine.apply_transition(v, VoyageStatus.BERTHED.value, timestamp=now + datetime.timedelta(days=1, hours=6))

        # BERTHED cannot jump back to SCHEDULED or IN_TRANSIT
        with pytest.raises(InvalidStateTransitionError):
            VoyageStateMachine.apply_transition(v, VoyageStatus.SCHEDULED.value)

        with pytest.raises(InvalidStateTransitionError):
            VoyageStateMachine.apply_transition(v, VoyageStatus.IN_TRANSIT.value)


# =============================================================================
# 2. Milestone Chronology Invariant Tests
# =============================================================================

@pytest.mark.django_db
class TestMilestoneChronology:

    def test_ata_exceeding_eta_7_day_sanity_window_raises(self, scheduled_voyage):
        """ata_date cannot precede eta_date by more than 7 days (Rule 1)."""
        v = scheduled_voyage
        v.eta_date = timezone.now() + datetime.timedelta(days=10)
        v.ata_date = timezone.now()  # 10 days before eta_date (> 7 days)

        with pytest.raises(MilestoneChronologyError) as exc_info:
            VoyageStateMachine.validate_timestamp_chronology(v)
        assert exc_info.value.prior_milestone == "eta_date - 7d"
        assert exc_info.value.subsequent_milestone == "ata_date"

    def test_nor_tendered_prior_to_ata_raises(self, scheduled_voyage):
        """nor_tendered_at cannot precede ata_date."""
        v = scheduled_voyage
        now = timezone.now()
        v.ata_date = now
        v.nor_tendered_at = now - datetime.timedelta(hours=2)  # Inverted!

        with pytest.raises(MilestoneChronologyError) as exc_info:
            VoyageStateMachine.validate_timestamp_chronology(v)
        assert exc_info.value.prior_milestone == "ata_date"
        assert exc_info.value.subsequent_milestone == "nor_tendered_at"

    def test_nor_accepted_prior_to_tendered_raises(self, scheduled_voyage):
        """nor_accepted_at cannot precede nor_tendered_at."""
        v = scheduled_voyage
        now = timezone.now()
        v.ata_date = now
        v.nor_tendered_at = now + datetime.timedelta(hours=1)
        v.nor_accepted_at = now  # Inverted!

        with pytest.raises(MilestoneChronologyError) as exc_info:
            VoyageStateMachine.validate_timestamp_chronology(v)
        assert exc_info.value.prior_milestone == "nor_tendered_at"
        assert exc_info.value.subsequent_milestone == "nor_accepted_at"

    def test_berthed_prior_to_ata_raises(self, scheduled_voyage):
        """berthed_at cannot precede ata_date."""
        v = scheduled_voyage
        now = timezone.now()
        v.ata_date = now
        v.berthed_at = now - datetime.timedelta(hours=3)

        with pytest.raises(MilestoneChronologyError) as exc_info:
            VoyageStateMachine.validate_timestamp_chronology(v)
        assert exc_info.value.prior_milestone == "ata_date"
        assert exc_info.value.subsequent_milestone == "berthed_at"

    def test_discharge_commenced_prior_to_berthed_raises(self, scheduled_voyage):
        """discharge_commenced_at cannot precede berthed_at."""
        v = scheduled_voyage
        now = timezone.now()
        v.ata_date = now
        v.berthed_at = now + datetime.timedelta(hours=2)
        v.discharge_commenced_at = now + datetime.timedelta(hours=1)  # Inverted!

        with pytest.raises(MilestoneChronologyError) as exc_info:
            VoyageStateMachine.validate_timestamp_chronology(v)
        assert exc_info.value.prior_milestone == "berthed_at"
        assert exc_info.value.subsequent_milestone == "discharge_commenced_at"

    def test_discharge_completed_prior_to_commenced_raises(self, scheduled_voyage):
        """discharge_completed_at cannot precede discharge_commenced_at."""
        v = scheduled_voyage
        now = timezone.now()
        v.ata_date = now
        v.berthed_at = now + datetime.timedelta(hours=2)
        v.discharge_commenced_at = now + datetime.timedelta(hours=4)
        v.discharge_completed_at = now + datetime.timedelta(hours=3)  # Inverted!

        with pytest.raises(MilestoneChronologyError) as exc_info:
            VoyageStateMachine.validate_timestamp_chronology(v)
        assert exc_info.value.prior_milestone == "discharge_commenced_at"
        assert exc_info.value.subsequent_milestone == "discharge_completed_at"

    def test_completed_at_prior_to_discharge_completed_raises(self, scheduled_voyage):
        """completed_at cannot precede discharge_completed_at."""
        v = scheduled_voyage
        now = timezone.now()
        v.ata_date = now
        v.berthed_at = now + datetime.timedelta(hours=2)
        v.discharge_commenced_at = now + datetime.timedelta(hours=4)
        v.discharge_completed_at = now + datetime.timedelta(hours=10)
        v.completed_at = now + datetime.timedelta(hours=8)  # Inverted!

        with pytest.raises(MilestoneChronologyError) as exc_info:
            VoyageStateMachine.validate_timestamp_chronology(v)
        assert exc_info.value.prior_milestone == "discharge_completed_at"
        assert exc_info.value.subsequent_milestone == "completed_at"


# =============================================================================
# 3. RESTful Transition API Tests
# =============================================================================

@pytest.mark.django_db
class TestVoyageTransitionAPI:

    def test_api_get_transition_inspection(self, api_client, scheduled_voyage):
        """GET /api/v1/voyages/{voyage_id}/transition/ returns current status & allowed targets."""
        url = f"/api/v1/voyages/{scheduled_voyage.voyage_id}/transition/"
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["voyage_id"] == str(scheduled_voyage.voyage_id)
        assert data["current_status"] == VoyageStatus.SCHEDULED.value
        assert "IN_TRANSIT" in data["allowed_transitions"]
        assert "CANCELLED" in data["allowed_transitions"]
        assert data["progress_pct"] == 0.0

    def test_api_post_valid_transition(self, api_client, scheduled_voyage):
        """POST /api/v1/voyages/{voyage_id}/transition/ successfully advances status."""
        url = f"/api/v1/voyages/{scheduled_voyage.voyage_id}/transition/"
        payload = {
            "target_status": "IN_TRANSIT",
            "remarks": "Vessel departed Port Hedland fully laden",
        }
        response = api_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["previous_status"] == "SCHEDULED"
        assert data["current_status"] == "IN_TRANSIT"
        assert data["progress_pct"] == 25.0
        assert "ARRIVED" in data["allowed_transitions"]
        assert data["remarks"] == "Vessel departed Port Hedland fully laden"

        # Verify DB persisted
        scheduled_voyage.refresh_from_db()
        assert scheduled_voyage.voyage_status == "IN_TRANSIT"

    def test_api_post_illegal_transition_returns_400(self, api_client, scheduled_voyage):
        """POST /api/v1/voyages/{voyage_id}/transition/ with illegal target returns 400."""
        url = f"/api/v1/voyages/{scheduled_voyage.voyage_id}/transition/"
        payload = {
            "target_status": "DISCHARGING",  # Illegal from SCHEDULED!
        }
        response = api_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["error"] == "INVALID_STATE_TRANSITION"
        assert data["current_status"] == "SCHEDULED"
        assert data["target_status"] == "DISCHARGING"
        assert data["allowed_transitions"] == ["IN_TRANSIT", "CANCELLED"]

    def test_api_post_chronology_violation_returns_400(self, api_client, scheduled_voyage):
        """POST with milestone inverted against prior timestamps returns 400."""
        # Set to ARRIVED
        scheduled_voyage.voyage_status = VoyageStatus.ARRIVED.value
        scheduled_voyage.ata_date = timezone.now()
        scheduled_voyage.save(skip_clean=True)

        url = f"/api/v1/voyages/{scheduled_voyage.voyage_id}/transition/"
        # Inverted: nor_tendered_at earlier than ata_date
        inverted_ts = (scheduled_voyage.ata_date - datetime.timedelta(hours=2)).isoformat()
        payload = {
            "target_status": "NOR_TENDERED",
            "timestamp": inverted_ts,
        }
        response = api_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["error"] == "CHRONOLOGY_VIOLATION"
        assert data["prior_milestone"] == "ata_date"
        assert data["subsequent_milestone"] == "nor_tendered_at"

    def test_api_non_existent_voyage_returns_404(self, api_client):
        """Invalid voyage UUID returns 404."""
        random_id = uuid.uuid4()
        url = f"/api/v1/voyages/{random_id}/transition/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND
