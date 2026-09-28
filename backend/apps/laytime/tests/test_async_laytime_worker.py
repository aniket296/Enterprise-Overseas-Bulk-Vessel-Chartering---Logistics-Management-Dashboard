"""
backend/apps/laytime/tests/test_async_laytime_worker.py
============================================================
Cycle 2.4.2 — Tests: Asynchronous Laytime Worker Task
Offloading heavy batch calculations from main HTTP thread to the Celery calculations queue.

Tests cover:
  1. recalculate_laytime_async creates authoritative LaytimeCalculation row with full audit trail.
  2. Sequential task executions increment revision number and atomically demote previous is_current=True.
  3. Locked SETTLED or DISPUTED outcomes are preserved across subsequent background recomputations.
  4. Graceful handling of uncomputable voyages (missing SOF events, missing laytime commencement) returning SKIPPED_PREREQUISITES.
  5. Indefinite laytime terms (e.g. CQD) return SKIPPED_PREREQUISITES without corrupting DB state.
  6. Non-existent voyage IDs return status FAILED.
  7. POST /api/v1/voyages/{voyage_id}/sof/ triggers async recalculate task and echoes async_calculation in response.
  8. POST /api/v1/voyages/{voyage_id}/recalculate/ on-demand endpoint returns 202 ACCEPTED with task metadata.
  9. RBAC enforcement on /recalculate/ endpoint (LOGISTICS_AGENT, FINANCE_OFFICER, SYSTEM_ADMIN allowed; READONLY forbidden).
  10. Strict mathematical and database accounting invariants hold on worker-generated calculations.
"""

import datetime
from decimal import Decimal
import uuid
import pytest
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.authentication.models import UserRole
from apps.voyages.models import LaytimeCalculation, StatementOfFacts, VesselVoyage
from apps.laytime.tasks import recalculate_laytime_async

UTC = datetime.timezone.utc


def _utc(year, month, day, hour, minute=0) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, minute, 0, tzinfo=UTC)


@pytest.fixture
def api_client():
    return APIClient()


def _make_vessel(db, suffix="01"):
    from apps.contracts.models import Vessel
    return Vessel.objects.create(
        vessel_name=f"MV Worker Bulk {suffix}",
        imo_number=f"920000{suffix}",
        flag_state="IN",
        build_year=2022,
        deadweight_tonnage=Decimal("85000.000"),
        gross_tonnage=Decimal("46000.000"),
        net_tonnage=Decimal("26000.000"),
        length_overall_meters=Decimal("229.00"),
        beam_meters=Decimal("32.26"),
        draft_meters=Decimal("14.50"),
        hold_count=7,
        hatch_count=7,
        owner_name="Async Logistics Marine",
    )


def _make_contract(db, vessel, laytime_terms="SHINC", suffix="01"):
    from apps.contracts.models import CharterContract
    return CharterContract.objects.create(
        contract_reference=f"WRK/CP/2025/{suffix}",
        vessel=vessel,
        charterer_entity="STEEL_AUTHORITY_OF_INDIA",
        charterer_plant="BOKARO",
        cargo_type="COKING_COAL",
        charter_party_type="VOYAGE_CHARTER",
        contract_status="ACTIVE",
        laycan_start=datetime.date(2025, 7, 1),
        laycan_end=datetime.date(2025, 7, 10),
        load_port="Gladstone",
        discharge_port="Haldia",
        nominated_quantity_mt=Decimal("75000.000"),
        laytime_terms=laytime_terms,
        discharge_rate_mt_per_day=Decimal("15000.000"),
        turn_time_hours=Decimal("6.00"),
        demurrage_rate_usd_per_day=Decimal("24000.00"),
        despatch_rate_usd_per_day=Decimal("12000.00"),
        freight_rate_usd_per_mt=Decimal("18.00"),
        # allowed = 75000 / 15000 * 24 = 120.000000 hours
        allowed_laytime_hours=Decimal("120.000000"),
    )


def _make_voyage(db, contract, vessel, suffix="01"):
    from apps.laytime.engine.fixture_factory import VoyageStatus
    eta = _utc(2025, 7, 15, 0)
    return VesselVoyage.objects.create(
        voyage_reference=f"WRK/VOY/{suffix}",
        contract=contract,
        vessel=vessel,
        voyage_status=VoyageStatus.SCHEDULED.value,
        load_port="Gladstone",
        load_country="AU",
        discharge_port="Haldia",
        discharge_country="IN",
        quantity_loaded_mt=Decimal("75000.000"),
        eta_date=eta,
    )


def _seed_sof(voyage, events):
    """Seed StatementOfFacts and auto-sync milestone timestamps onto voyage."""
    created = []
    for i, ev in enumerate(events, start=1):
        rec = StatementOfFacts.objects.create(
            voyage=voyage,
            event_type=ev["event_type"],
            event_timestamp=ev["event_timestamp"],
            sequence_number=i,
        )
        created.append(rec)

    ev_map = {e["event_type"]: e["event_timestamp"] for e in events}
    if "NOR_TENDERED" in ev_map:
        voyage.nor_tendered_at = ev_map["NOR_TENDERED"]
    if "NOR_ACCEPTED" in ev_map:
        voyage.nor_accepted_at = ev_map["NOR_ACCEPTED"]
    if "DISCHARGE_COMMENCED" in ev_map:
        voyage.discharge_commenced_at = ev_map["DISCHARGE_COMMENCED"]
    if "DISCHARGE_COMPLETED" in ev_map:
        voyage.discharge_completed_at = ev_map["DISCHARGE_COMPLETED"]
    voyage.save(skip_clean=True)
    return created


def _make_user(db, role, suffix="01"):
    from django.contrib.auth import get_user_model
    from apps.authentication.models import UserProfile
    User = get_user_model()
    user = User.objects.create_user(
        username=f"worker_user_{role.lower()}_{suffix}",
        email=f"worker_{role.lower()}_{suffix}@test.com",
        password="TestPassword123!",
    )
    UserProfile.objects.create(
        user=user,
        role=role,
        organization="SAIL Logistics",
    )
    return user


@pytest.fixture
def logistics_user(db):
    return _make_user(db, UserRole.LOGISTICS_AGENT.value, "logistics")


@pytest.fixture
def finance_user(db):
    return _make_user(db, UserRole.FINANCE_OFFICER.value, "finance")


@pytest.fixture
def admin_user(db):
    return _make_user(db, UserRole.SYSTEM_ADMIN.value, "admin")


@pytest.fixture
def readonly_user(db):
    return _make_user(db, UserRole.READONLY.value, "readonly")


@pytest.fixture
def demurrage_voyage(db):
    vessel = _make_vessel(db, "DEM")
    contract = _make_contract(db, vessel, "SHINC", "DEM")
    voyage = _make_voyage(db, contract, vessel, "DEM")
    # Allowed: 120h. Discharged in 140h -> 20h Demurrage
    _seed_sof(voyage, [
        {"event_type": "NOR_TENDERED", "event_timestamp": _utc(2025, 7, 15, 6)},
        {"event_type": "ALL_FAST", "event_timestamp": _utc(2025, 7, 15, 12)},
        {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 7, 15, 12)},
        {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 7, 21, 8)},
    ])
    return voyage


@pytest.fixture
def despatch_voyage(db):
    vessel = _make_vessel(db, "DES")
    contract = _make_contract(db, vessel, "SHINC", "DES")
    voyage = _make_voyage(db, contract, vessel, "DES")
    # Allowed: 120h. Discharged in 80h -> 40h Despatch
    _seed_sof(voyage, [
        {"event_type": "NOR_TENDERED", "event_timestamp": _utc(2025, 7, 15, 6)},
        {"event_type": "ALL_FAST", "event_timestamp": _utc(2025, 7, 15, 12)},
        {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 7, 15, 12)},
        {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 7, 18, 20)},
    ])
    return voyage


# ===========================================================================
# Test Cases
# ===========================================================================

@pytest.mark.django_db
class TestAsyncLaytimeWorkerTask:

    def test_recalculate_laytime_async_creates_authoritative_calculation(self, demurrage_voyage):
        """Worker task computes laytime and creates authoritative LaytimeCalculation row."""
        result = recalculate_laytime_async.apply(
            args=[str(demurrage_voyage.voyage_id), "UNIT_TEST"]
        ).get()

        assert result["status"] == "SUCCESS"
        assert result["voyage_id"] == str(demurrage_voyage.voyage_id)
        assert result["revision"] == 1
        assert result["outcome"] == "DEMURRAGE"
        assert result["trigger_source"] == "UNIT_TEST"
        assert result["celery_task_id"] is not None

        calc = LaytimeCalculation.objects.get(voyage=demurrage_voyage, is_current=True)
        assert calc.revision == 1
        assert calc.is_current is True
        assert calc.outcome == "DEMURRAGE"
        assert calc.allowed_laytime_hours == Decimal("120.000000")
        assert calc.total_counted_hours == Decimal("140.000000")
        assert calc.variance_hours == Decimal("20.000000")
        # 20 hours demurrage @ 24,000 USD/day = 20/24 * 24,000 = 20,000.00 USD
        assert calc.net_settlement_usd == Decimal("20000.00")
        assert calc.gross_demurrage_usd == Decimal("20000.00")
        assert calc.gross_despatch_usd == Decimal("0.00")
        assert calc.celery_task_id == result["celery_task_id"]

    def test_recalculate_laytime_async_revision_increment_and_demotion(self, demurrage_voyage):
        """Sequential calculations increment revision and demote previous active calculation."""
        res1 = recalculate_laytime_async.apply(args=[str(demurrage_voyage.voyage_id)]).get()
        assert res1["revision"] == 1

        calc1 = LaytimeCalculation.objects.get(calc_id=res1["calculation_id"])
        assert calc1.is_current is True
        assert calc1.revision == 1

        res2 = recalculate_laytime_async.apply(args=[str(demurrage_voyage.voyage_id)]).get()
        assert res2["revision"] == 2

        calc1.refresh_from_db()
        calc2 = LaytimeCalculation.objects.get(calc_id=res2["calculation_id"])

        assert calc1.is_current is False
        assert calc2.is_current is True
        assert calc2.revision == 2

        all_calcs = LaytimeCalculation.objects.filter(voyage=demurrage_voyage).order_by("revision")
        assert all_calcs.count() == 2
        assert [c.is_current for c in all_calcs] == [False, True]

    def test_recalculate_laytime_async_preserves_locked_settled_status(self, demurrage_voyage, finance_user):
        """Locked SETTLED status and settlement notes are preserved during async recalculation."""
        # Initial calculation
        recalculate_laytime_async.apply(args=[str(demurrage_voyage.voyage_id)]).get()
        initial_calc = LaytimeCalculation.objects.get(voyage=demurrage_voyage, is_current=True)

        # Lock the settlement
        initial_calc.outcome = "SETTLED"
        initial_calc.settled_at = timezone_now = datetime.datetime.now(tz=UTC)
        initial_calc.settled_by = finance_user
        initial_calc.settlement_notes = "Mutually accepted by owner and charterer."
        initial_calc.save()

        # Trigger async recomputation
        res = recalculate_laytime_async.apply(args=[str(demurrage_voyage.voyage_id)]).get()
        assert res["status"] == "SUCCESS"
        assert res["outcome"] == "SETTLED"

        new_calc = LaytimeCalculation.objects.get(voyage=demurrage_voyage, is_current=True)
        assert new_calc.revision == 2
        assert new_calc.outcome == "SETTLED"
        assert new_calc.settlement_notes == "Mutually accepted by owner and charterer."
        assert new_calc.settled_by == finance_user

    def test_recalculate_laytime_async_despatch_outcome(self, despatch_voyage):
        """Worker task calculates DESPATCH outcome accurately."""
        res = recalculate_laytime_async.apply(args=[str(despatch_voyage.voyage_id)]).get()
        assert res["status"] == "SUCCESS"
        assert res["outcome"] == "DESPATCH"

        calc = LaytimeCalculation.objects.get(voyage=despatch_voyage, is_current=True)
        assert calc.outcome == "DESPATCH"
        assert calc.variance_hours == Decimal("-40.000000")
        # 40 hours saved @ 12,000 USD/day = 40/24 * 12000 = 20,000.00 USD
        assert calc.gross_despatch_usd == Decimal("20000.00")
        assert calc.net_settlement_usd == Decimal("-20000.00")

    def test_recalculate_laytime_async_skipped_no_sof_events(self, db):
        """Voyage with zero SOF events returns SKIPPED_PREREQUISITES without failing."""
        vessel = _make_vessel(db, "EMPTY")
        contract = _make_contract(db, vessel, "SHINC", "EMPTY")
        voyage = _make_voyage(db, contract, vessel, "EMPTY")

        res = recalculate_laytime_async.apply(args=[str(voyage.voyage_id)]).get()
        assert res["status"] == "SKIPPED_PREREQUISITES"
        assert res["error_code"] == "NO_SOF_EVENTS"
        assert LaytimeCalculation.objects.filter(voyage=voyage).count() == 0

    def test_recalculate_laytime_async_skipped_no_commencement(self, db):
        """Voyage with events prior to laytime commencement returns SKIPPED_PREREQUISITES."""
        vessel = _make_vessel(db, "NOCOMM")
        contract = _make_contract(db, vessel, "SHINC", "NOCOMM")
        voyage = _make_voyage(db, contract, vessel, "NOCOMM")
        StatementOfFacts.objects.create(
            voyage=voyage,
            event_type="VESSEL_ARRIVED_PILOT_STATION",
            event_timestamp=_utc(2025, 7, 15, 0),
            sequence_number=1,
        )

        res = recalculate_laytime_async.apply(args=[str(voyage.voyage_id)]).get()
        assert res["status"] == "SKIPPED_PREREQUISITES"
        assert res["error_code"] == "NO_LAYTIME_COMMENCEMENT"
        assert LaytimeCalculation.objects.filter(voyage=voyage).count() == 0

    def test_recalculate_laytime_async_skipped_indefinite_laytime(self, db):
        """CQD contract without fixed allowed laytime returns SKIPPED_PREREQUISITES."""
        vessel = _make_vessel(db, "CQD")
        contract = _make_contract(db, vessel, "CQD", "CQD")
        contract.allowed_laytime_hours = None
        contract.save()

        voyage = _make_voyage(db, contract, vessel, "CQD")
        _seed_sof(voyage, [
            {"event_type": "NOR_TENDERED", "event_timestamp": _utc(2025, 7, 15, 6)},
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 7, 15, 12)},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 7, 18, 12)},
        ])

        res = recalculate_laytime_async.apply(args=[str(voyage.voyage_id)]).get()
        assert res["status"] == "SKIPPED_PREREQUISITES"
        assert res["error_code"] == "INDEFINITE_LAYTIME"
        assert LaytimeCalculation.objects.filter(voyage=voyage).count() == 0

    def test_recalculate_laytime_async_nonexistent_voyage(self, db):
        """Invalid voyage ID returns FAILED gracefully."""
        random_id = str(uuid.uuid4())
        res = recalculate_laytime_async.apply(args=[random_id]).get()
        assert res["status"] == "FAILED"
        assert "does not exist" in res["error"]


@pytest.mark.django_db
class TestSOFIngestionCeleryDispatch:

    def test_sof_ingestion_api_triggers_async_calculation(self, api_client, db, logistics_user):
        """POST /sof/ enqueues recalculate task and returns async_calculation metadata."""
        vessel = _make_vessel(db, "SOF1")
        contract = _make_contract(db, vessel, "SHINC", "SOF1")
        voyage = _make_voyage(db, contract, vessel, "SOF1")

        api_client.force_authenticate(user=logistics_user)
        payload = [
            {"event_type": "NOR_TENDERED", "event_timestamp": _utc(2025, 7, 15, 6).isoformat()},
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 7, 15, 12).isoformat()},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 7, 21, 8).isoformat()},
        ]

        url = f"/api/v1/voyages/{voyage.voyage_id}/sof/"
        res = api_client.post(url, payload, format="json")

        assert res.status_code == drf_status.HTTP_201_CREATED
        data = res.json()
        assert "async_calculation" in data
        assert data["async_calculation"]["status"] == "QUEUED"
        assert data["async_calculation"]["queue"] == "calculations"
        assert data["async_calculation"]["task_id"] is not None

        # In test mode with CELERY_TASK_ALWAYS_EAGER=True, task executed immediately
        calc = LaytimeCalculation.objects.filter(voyage=voyage, is_current=True).first()
        assert calc is not None
        assert calc.outcome == "DEMURRAGE"


@pytest.mark.django_db
class TestRecalculateAPIEndpoint:

    def test_recalculate_endpoint_success_202_accepted(self, api_client, demurrage_voyage, logistics_user):
        """POST /api/v1/voyages/{id}/recalculate/ returns 202 ACCEPTED with task tracking info."""
        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{demurrage_voyage.voyage_id}/recalculate/"
        res = api_client.post(url, format="json")

        assert res.status_code == drf_status.HTTP_202_ACCEPTED
        data = res.json()
        assert data["voyage_id"] == str(demurrage_voyage.voyage_id)
        assert data["status"] == "QUEUED"
        assert data["queue"] == "calculations"
        assert data["task_id"] is not None

        calc = LaytimeCalculation.objects.get(voyage=demurrage_voyage, is_current=True)
        assert calc.outcome == "DEMURRAGE"

    def test_recalculate_endpoint_rbac_permissions(self, api_client, demurrage_voyage, db, admin_user, finance_user, readonly_user):
        """Verify role permissions on POST /recalculate/."""
        url = f"/api/v1/voyages/{demurrage_voyage.voyage_id}/recalculate/"

        # System Admin allowed
        api_client.force_authenticate(user=admin_user)
        res_admin = api_client.post(url, format="json")
        assert res_admin.status_code == drf_status.HTTP_202_ACCEPTED

        # Finance Officer allowed
        api_client.force_authenticate(user=finance_user)
        res_fin = api_client.post(url, format="json")
        assert res_fin.status_code == drf_status.HTTP_202_ACCEPTED

        # Readonly forbidden
        api_client.force_authenticate(user=readonly_user)
        res_ro = api_client.post(url, format="json")
        assert res_ro.status_code == drf_status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestLaytimeWorkerInvariants:

    def test_mathematical_and_db_accounting_invariants(self, demurrage_voyage):
        """Ensure math and schema invariants hold on worker-generated LaytimeCalculation."""
        recalculate_laytime_async.apply(args=[str(demurrage_voyage.voyage_id)]).get()
        calc = LaytimeCalculation.objects.get(voyage=demurrage_voyage, is_current=True)

        # Invariant 1: Total counted + excepted == total port hours (within 6dp precision)
        assert (calc.total_counted_hours + calc.total_excepted_hours) == calc.total_port_hours

        # Invariant 2: Mutually exclusive financial outcomes
        assert calc.gross_demurrage_usd == Decimal("0.00") or calc.gross_despatch_usd == Decimal("0.00")

        # Invariant 3: Single active calculation per voyage
        assert LaytimeCalculation.objects.filter(voyage=demurrage_voyage, is_current=True).count() == 1
