"""
backend/apps/voyages/tests/test_sof_api.py
============================================================
Cycle 2.2.1 — Unit & Integration Tests: Statement of Facts Ingestion API
Validates:
  1. StatementOfFacts model check constraints & auto-sequencing
  2. Single SOF event ingestion (POST /api/v1/voyages/{id}/sof/)
  3. Batch SOF events ingestion (list & dict formats)
  4. Operational milestone auto-sync (NOR, Berthing, Discharge)
  5. Timeline inspection & summary metrics (GET /api/v1/voyages/{id}/sof/)
  6. Schema validation & rejection of malformed/invalid event types
  7. RBAC permissions (Logistics Agent vs Auditor)
"""

import uuid
import datetime
from decimal import Decimal
import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.authentication.models import UserProfile
from apps.authentication.roles import UserRole
from apps.contracts.models import Vessel, CharterContract
from apps.voyages.models import VesselVoyage, StatementOfFacts
from apps.voyages.sof_enums import SOFEventType, IntervalCountStatus
from apps.laytime.engine.fixture_factory import VoyageStatus

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def base_vessel(db):
    return Vessel.objects.create(
        vessel_name="MV SOF Carrier",
        imo_number="9988776",
        flag_state="IN",
        build_year=2021,
        deadweight_tonnage=Decimal("82000.000"),
        gross_tonnage=Decimal("43000.000"),
        net_tonnage=Decimal("25000.000"),
        length_overall_meters=Decimal("229.00"),
        beam_meters=Decimal("32.26"),
        draft_meters=Decimal("14.50"),
        hold_count=7,
        hatch_count=7,
        owner_name="Shipping Corporation of India Ltd",
    )


@pytest.fixture
def base_contract(db, base_vessel):
    return CharterContract.objects.create(
        contract_reference="SAIL/CP/2025/SOF-001",
        vessel=base_vessel,
        charterer_entity="STEEL_AUTHORITY_OF_INDIA",
        charterer_plant="BOKARO",
        cargo_type="COKING_COAL",
        charter_party_type="VOYAGE_CHARTER",
        contract_status="ACTIVE",
        laycan_start=datetime.date(2025, 6, 1),
        laycan_end=datetime.date(2025, 6, 10),
        load_port="Hay Point",
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
def base_voyage(db, base_contract, base_vessel):
    eta = timezone.now() + datetime.timedelta(days=2)
    return VesselVoyage.objects.create(
        voyage_reference="SAIL/VOY/SOF/001",
        contract=base_contract,
        vessel=base_vessel,
        voyage_status=VoyageStatus.SCHEDULED.value,
        load_port="Hay Point",
        load_country="AU",
        discharge_port="Haldia",
        discharge_country="IN",
        quantity_loaded_mt=Decimal("75000.000"),
        eta_date=eta,
    )


@pytest.fixture
def logistics_user(db):
    user = User.objects.create_user(
        username="agent_bothra",
        email="agent@bothra.com",
        password="TestPassword123!",
    )
    UserProfile.objects.create(
        user=user,
        role=UserRole.LOGISTICS_AGENT.value,
        organization="Bothra Shipping Services",
    )
    return user


@pytest.fixture
def auditor_user(db):
    user = User.objects.create_user(
        username="statutory_auditor",
        email="auditor@cag.gov.in",
        password="TestPassword123!",
    )
    UserProfile.objects.create(
        user=user,
        role=UserRole.AUDITOR.value,
        organization="Comptroller & Auditor General",
    )
    return user


# =============================================================================
# 1. StatementOfFacts Model Unit Tests
# =============================================================================

@pytest.mark.django_db
class TestStatementOfFactsModel:

    def test_auto_sequence_number_assignment(self, base_voyage):
        now = timezone.now()
        sof1 = StatementOfFacts.objects.create(
            voyage=base_voyage,
            event_type=SOFEventType.VESSEL_ARRIVED_PILOT_STATION.value,
            event_timestamp=now,
        )
        assert sof1.sequence_number == 1

        sof2 = StatementOfFacts.objects.create(
            voyage=base_voyage,
            event_type=SOFEventType.VESSEL_ARRIVED_ANCHORAGE.value,
            event_timestamp=now + datetime.timedelta(hours=1),
        )
        assert sof2.sequence_number == 2

    def test_explicit_sequence_number_honored(self, base_voyage):
        now = timezone.now()
        sof = StatementOfFacts.objects.create(
            voyage=base_voyage,
            event_type=SOFEventType.NOR_TENDERED.value,
            event_timestamp=now,
            sequence_number=10,
        )
        assert sof.sequence_number == 10

    def test_invalid_fraction_range_rejected(self, base_voyage):
        sof = StatementOfFacts(
            voyage=base_voyage,
            event_type=SOFEventType.RAIN_COMMENCED.value,
            event_timestamp=timezone.now(),
            count_fraction=Decimal("1.50"),  # > 1.00!
        )
        with pytest.raises(Exception):
            sof.full_clean()

    def test_amendment_requires_amended_sof(self, base_voyage):
        sof = StatementOfFacts(
            voyage=base_voyage,
            event_type=SOFEventType.NOR_TENDERED.value,
            event_timestamp=timezone.now(),
            is_amendment=True,
            amended_sof=None,  # missing parent!
        )
        with pytest.raises(Exception):
            sof.full_clean()


# =============================================================================
# 2. SOF Ingestion API Integration Tests
# =============================================================================

@pytest.mark.django_db
class TestSOFIngestionAPI:

    def test_single_event_ingestion(self, api_client, base_voyage, logistics_user):
        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{base_voyage.voyage_id}/sof/"
        payload = {
            "event_type": "NOR_TENDERED",
            "event_timestamp": "2025-06-15T08:00:00Z",
            "event_description": "NOR tendered at Haldia roads pilot station",
            "recorded_by_agent": "Bothra Shipping Services",
            "weather_condition": "Wind BF3, fine",
        }
        res = api_client.post(url, payload, format="json")

        assert res.status_code == status.HTTP_201_CREATED
        data = res.json()
        assert data["ingested_count"] == 1
        assert len(data["events"]) == 1
        created = data["events"][0]
        assert created["event_type"] == "NOR_TENDERED"
        assert created["sequence_number"] == 1
        assert created["recorded_by_agent"] == "Bothra Shipping Services"

        # Verify DB persistence
        assert StatementOfFacts.objects.filter(voyage=base_voyage).count() == 1

    def test_batch_events_ingestion_list(self, api_client, base_voyage, logistics_user):
        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{base_voyage.voyage_id}/sof/"
        payload = [
            {
                "event_type": "VESSEL_ARRIVED_PILOT_STATION",
                "event_timestamp": "2025-06-15T06:00:00Z",
            },
            {
                "event_type": "VESSEL_ARRIVED_ANCHORAGE",
                "event_timestamp": "2025-06-15T07:30:00Z",
            },
            {
                "event_type": "NOR_TENDERED",
                "event_timestamp": "2025-06-15T08:00:00Z",
            },
            {
                "event_type": "ALL_FAST",
                "event_timestamp": "2025-06-15T14:00:00Z",
            },
        ]
        res = api_client.post(url, payload, format="json")

        assert res.status_code == status.HTTP_201_CREATED
        data = res.json()
        assert data["ingested_count"] == 4
        assert [e["sequence_number"] for e in data["events"]] == [1, 2, 3, 4]
        assert StatementOfFacts.objects.filter(voyage=base_voyage).count() == 4

    def test_batch_events_ingestion_dict_wrapper(self, api_client, base_voyage, logistics_user):
        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{base_voyage.voyage_id}/sof/"
        payload = {
            "events": [
                {
                    "event_type": "DISCHARGE_COMMENCED",
                    "event_timestamp": "2025-06-15T16:00:00Z",
                    "crane_number": 1,
                    "hatch_number": 2,
                    "quantity_discharged_at_event": "1500.000",
                },
                {
                    "event_type": "RAIN_COMMENCED",
                    "event_timestamp": "2025-06-15T18:00:00Z",
                    "interruption_cause": "Tropical Monsoon Downpour",
                    "weather_condition": "Heavy Rain",
                },
            ]
        }
        res = api_client.post(url, payload, format="json")

        assert res.status_code == status.HTTP_201_CREATED
        assert res.json()["ingested_count"] == 2

    def test_milestone_synchronization_from_sof(self, api_client, base_voyage, logistics_user):
        """Authoritative operational SOF events sync milestone timestamps on VesselVoyage."""
        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{base_voyage.voyage_id}/sof/"
        nor_ts = "2025-06-15T08:00:00Z"
        all_fast_ts = "2025-06-15T14:00:00Z"
        disch_comm_ts = "2025-06-15T16:00:00Z"
        payload = [
            {"event_type": "NOR_TENDERED", "event_timestamp": nor_ts},
            {"event_type": "ALL_FAST", "event_timestamp": all_fast_ts},
            {
                "event_type": "DISCHARGE_COMMENCED",
                "event_timestamp": disch_comm_ts,
                "quantity_discharged_at_event": "5000.000",
            },
        ]
        res = api_client.post(url, payload, format="json")
        assert res.status_code == status.HTTP_201_CREATED

        base_voyage.refresh_from_db()
        assert base_voyage.nor_tendered_at is not None
        assert base_voyage.berthed_at is not None
        assert base_voyage.discharge_commenced_at is not None
        assert base_voyage.quantity_discharged_mt == Decimal("5000.000")

    def test_get_sof_timeline_and_summary_metrics(self, api_client, base_voyage, logistics_user):
        """GET /api/v1/voyages/{id}/sof/ returns complete timeline and KPI statistics."""
        # Seed events
        now = timezone.now()
        StatementOfFacts.objects.create(
            voyage=base_voyage,
            event_type=SOFEventType.NOR_TENDERED.value,
            event_timestamp=now,
            sequence_number=1,
        )
        StatementOfFacts.objects.create(
            voyage=base_voyage,
            event_type=SOFEventType.RAIN_COMMENCED.value,
            event_timestamp=now + datetime.timedelta(hours=2),
            sequence_number=2,
            interruption_cause="Heavy Rain",
        )
        StatementOfFacts.objects.create(
            voyage=base_voyage,
            event_type=SOFEventType.RAIN_CEASED.value,
            event_timestamp=now + datetime.timedelta(hours=4),
            sequence_number=3,
        )
        StatementOfFacts.objects.create(
            voyage=base_voyage,
            event_type=SOFEventType.DISCHARGE_COMPLETED.value,
            event_timestamp=now + datetime.timedelta(days=2),
            sequence_number=4,
            quantity_discharged_at_event=Decimal("74900.000"),
        )

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{base_voyage.voyage_id}/sof/"
        res = api_client.get(url)

        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["voyage_id"] == str(base_voyage.voyage_id)
        assert data["summary"]["total_events_count"] == 4
        assert data["summary"]["interruption_events_count"] == 2  # RAIN_COMMENCED and RAIN_CEASED
        assert Decimal(str(data["summary"]["latest_discharged_mt"])) == Decimal("74900.000")
        assert len(data["events"]) == 4

    def test_invalid_event_type_rejected(self, api_client, base_voyage, logistics_user):
        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{base_voyage.voyage_id}/sof/"
        payload = {
            "event_type": "UNKNOWN_BOGUS_EVENT",
            "event_timestamp": "2025-06-15T08:00:00Z",
        }
        res = api_client.post(url, payload, format="json")
        assert res.status_code == status.HTTP_400_BAD_REQUEST

    def test_non_existent_voyage_returns_404(self, api_client, logistics_user):
        api_client.force_authenticate(user=logistics_user)
        random_id = uuid.uuid4()
        url = f"/api/v1/voyages/{random_id}/sof/"
        res = api_client.get(url)
        assert res.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# 3. RBAC Permissions on SOF Endpoints
# =============================================================================

@pytest.mark.django_db
class TestSOFRBACPermissions:

    def test_auditor_forbidden_from_ingesting_sof(self, api_client, base_voyage, auditor_user):
        api_client.force_authenticate(user=auditor_user)
        url = f"/api/v1/voyages/{base_voyage.voyage_id}/sof/"
        payload = {
            "event_type": "NOR_TENDERED",
            "event_timestamp": "2025-06-15T08:00:00Z",
        }
        res = api_client.post(url, payload, format="json")
        assert res.status_code == status.HTTP_403_FORBIDDEN
        data = res.json()
        assert data["error"] == "FORBIDDEN"
        assert "AUDITOR" in data["detail"]

    def test_auditor_allowed_to_view_sof_timeline(self, api_client, base_voyage, auditor_user):
        api_client.force_authenticate(user=auditor_user)
        url = f"/api/v1/voyages/{base_voyage.voyage_id}/sof/"
        res = api_client.get(url)
        assert res.status_code == status.HTTP_200_OK
