"""
backend/apps/voyages/tests/test_settlement_api.py
============================================================
Cycle 2.2.5 — Tests: Demurrage Settlement API

Tests cover:
  1. POST /settle/ before DISCHARGE_COMPLETED → 409 CONFLICT (DISCHARGE_NOT_COMPLETED)
  2. GET /settle/ before settlement exists → 404 NOT_FOUND (NOT_SETTLED)
  3. POST /settle/ on completed demurrage voyage → 201 CREATED + DB record created
  4. GET /settle/ returns current authoritative settlement record (200 OK)
  5. Idempotent re-settlement: repeated POST updates existing calculation (200 OK)
  6. POST /settle/ on completed despatch voyage → 201 CREATED + DESPATCH outcome
  7. Locking settlement (outcome="SETTLED") → subsequent POST returns 409 (SETTLEMENT_LOCKED)
  8. Disputing settlement (outcome="DISPUTED") → subsequent POST returns 409 (SETTLEMENT_LOCKED)
  9. RBAC permissions:
     - FINANCE_OFFICER can POST /settle/
     - LOGISTICS_AGENT can POST /settle/
     - AUDITOR can GET /settle/, but 403 on POST /settle/
     - READONLY can GET /settle/, but 403 on POST /settle/
  10. Model alias LaytimeRecord works seamlessly with LaytimeCalculation
  11. Accounting invariants verified on persisted model instance
"""

import datetime
from decimal import Decimal

import pytest
from rest_framework import status as drf_status
from rest_framework.test import APIClient

UTC = datetime.timezone.utc


def _utc(year, month, day, hour, minute=0) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, minute, 0, tzinfo=UTC)


@pytest.fixture
def api_client():
    return APIClient()


def _make_vessel(db, suffix="01"):
    from apps.contracts.models import Vessel
    return Vessel.objects.create(
        vessel_name=f"MV Settle Bulk {suffix}",
        imo_number=f"910000{suffix}",
        flag_state="IN",
        build_year=2021,
        deadweight_tonnage=Decimal("82000.000"),
        gross_tonnage=Decimal("44000.000"),
        net_tonnage=Decimal("25000.000"),
        length_overall_meters=Decimal("229.00"),
        beam_meters=Decimal("32.26"),
        draft_meters=Decimal("14.50"),
        hold_count=7,
        hatch_count=7,
        owner_name="Settle Shipping Ltd",
    )


def _make_contract(db, vessel, laytime_terms="SHINC", suffix="01"):
    from apps.contracts.models import CharterContract
    return CharterContract.objects.create(
        contract_reference=f"SET/CP/2025/{suffix}",
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
        demurrage_rate_usd_per_day=Decimal("30000.00"),
        despatch_rate_usd_per_day=Decimal("15000.00"),
        freight_rate_usd_per_mt=Decimal("18.00"),
        # allowed = 75000 / 15000 * 24 = 120 hours
        allowed_laytime_hours=Decimal("120.000000"),
    )


def _make_voyage(db, contract, vessel, suffix="01"):
    from apps.voyages.models import VesselVoyage
    from apps.laytime.engine.fixture_factory import VoyageStatus
    eta = _utc(2025, 7, 15, 0)
    return VesselVoyage.objects.create(
        voyage_reference=f"SET/VOY/{suffix}",
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
    from apps.voyages.models import StatementOfFacts
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
        username=f"settle_user_{role.lower()}_{suffix}",
        email=f"settle_{role.lower()}_{suffix}@test.com",
        password="TestPassword123!",
    )
    UserProfile.objects.create(
        user=user,
        role=role,
        organization="SAIL Logistics",
    )
    return user


@pytest.fixture
def finance_user(db):
    from apps.authentication.roles import UserRole
    return _make_user(db, UserRole.FINANCE_OFFICER.value, "fin")


@pytest.fixture
def logistics_user(db):
    from apps.authentication.roles import UserRole
    return _make_user(db, UserRole.LOGISTICS_AGENT.value, "log")


@pytest.fixture
def auditor_user(db):
    from apps.authentication.roles import UserRole
    return _make_user(db, UserRole.AUDITOR.value, "aud")


@pytest.fixture
def readonly_user(db):
    from apps.authentication.roles import UserRole
    return _make_user(db, UserRole.READONLY.value, "ro")


# ---------------------------------------------------------------------------
# Test Suite
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDemurrageSettlementAPI:

    def _setup_voyage(self, db, suffix="S01", laytime_terms="SHINC"):
        vessel = _make_vessel(db, suffix)
        contract = _make_contract(db, vessel, laytime_terms=laytime_terms, suffix=suffix)
        voyage = _make_voyage(db, contract, vessel, suffix=suffix)
        return vessel, contract, voyage

    def test_post_settle_before_discharge_completed_returns_409(
        self, api_client, db, finance_user
    ):
        """Discharge not yet completed → 409 CONFLICT (DISCHARGE_NOT_COMPLETED)."""
        _, _, voyage = self._setup_voyage(db, "S01")
        # Ingest only commencement milestone, not completed
        _seed_sof(voyage, [
            {"event_type": "NOR_TENDERED", "event_timestamp": _utc(2025, 7, 16, 8)},
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 7, 16, 14)},
        ])

        api_client.force_authenticate(user=finance_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/settle/"
        res = api_client.post(url, {}, format="json")

        assert res.status_code == drf_status.HTTP_409_CONFLICT
        assert res.json()["error"] == "DISCHARGE_NOT_COMPLETED"

    def test_get_settle_before_settlement_returns_404(
        self, api_client, db, finance_user
    ):
        """GET /settle/ on voyage without saved settlement returns 404 NOT_FOUND."""
        _, _, voyage = self._setup_voyage(db, "S02")
        api_client.force_authenticate(user=finance_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/settle/"
        res = api_client.get(url)

        assert res.status_code == drf_status.HTTP_404_NOT_FOUND
        assert res.json()["error"] == "NOT_SETTLED"

    def test_post_settle_demurrage_creates_record(
        self, api_client, db, finance_user
    ):
        """Completed voyage on demurrage persists authoritative LaytimeCalculation."""
        from apps.voyages.models import LaytimeCalculation

        _, _, voyage = self._setup_voyage(db, "S03", "SHINC")
        # 120h allowed. 140h used (from 2025-07-16 00:00 to 2025-07-21 20:00 = 140h) → 20h Demurrage
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 7, 16, 0)},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 7, 21, 20)},
        ])

        api_client.force_authenticate(user=finance_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/settle/"
        res = api_client.post(
            url,
            {"settlement_notes": "Agreed demurrage claim for 20 hours over allowed."},
            format="json",
        )

        assert res.status_code == drf_status.HTTP_201_CREATED
        data = res.json()

        assert data["outcome"] == "DEMURRAGE"
        assert data["is_current"] is True
        assert data["revision"] == 1
        assert Decimal(data["allowed_laytime_hours"]) == Decimal("120.000000")
        assert Decimal(data["total_counted_hours"]) == Decimal("140.000000")
        assert Decimal(data["variance_hours"]) == Decimal("20.000000")
        assert Decimal(data["gross_demurrage_usd"]) > Decimal("0.00")
        assert Decimal(data["gross_despatch_usd"]) == Decimal("0.00")
        assert Decimal(data["net_settlement_usd"]) > Decimal("0.00")
        assert data["demurrage_commenced_at"] is not None
        assert data["settlement_notes"] == "Agreed demurrage claim for 20 hours over allowed."
        assert data["settled_by_username"] == finance_user.username

        # Verify DB
        calc = LaytimeCalculation.objects.get(voyage=voyage, is_current=True)
        assert calc.outcome == "DEMURRAGE"
        assert calc.variance_hours == Decimal("20.000000")

    def test_get_settle_after_settlement_returns_200(
        self, api_client, db, finance_user
    ):
        """GET /settle/ returns the saved authoritative calculation."""
        _, _, voyage = self._setup_voyage(db, "S04", "SHINC")
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 7, 16, 0)},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 7, 21, 20)},
        ])

        api_client.force_authenticate(user=finance_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/settle/"
        # First POST
        api_client.post(url, {}, format="json")

        # Now GET
        res = api_client.get(url)
        assert res.status_code == drf_status.HTTP_200_OK
        data = res.json()
        assert data["outcome"] == "DEMURRAGE"
        assert data["voyage_reference"] == voyage.voyage_reference

    def test_idempotent_resettlement_updates_existing_record(
        self, api_client, db, finance_user
    ):
        """Repeated POST /settle/ updates existing calculation without duplicating rows."""
        from apps.voyages.models import LaytimeCalculation

        _, _, voyage = self._setup_voyage(db, "S05", "SHINC")
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 7, 16, 0)},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 7, 21, 20)},
        ])

        api_client.force_authenticate(user=finance_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/settle/"

        # 1st call → 201
        res1 = api_client.post(url, {"settlement_notes": "Initial draft"}, format="json")
        assert res1.status_code == drf_status.HTTP_201_CREATED
        calc_id_1 = res1.json()["calc_id"]

        # 2nd call → 200 (updated in place)
        res2 = api_client.post(url, {"settlement_notes": "Updated notes after review"}, format="json")
        assert res2.status_code == drf_status.HTTP_200_OK
        calc_id_2 = res2.json()["calc_id"]

        assert calc_id_1 == calc_id_2
        assert res2.json()["settlement_notes"] == "Updated notes after review"
        assert LaytimeCalculation.objects.filter(voyage=voyage).count() == 1

    def test_post_settle_despatch_outcome(
        self, api_client, db, finance_user
    ):
        """Completed voyage finishing before allowed laytime creates DESPATCH calculation."""
        _, _, voyage = self._setup_voyage(db, "S06", "SHINC")
        # Allowed = 120h. Used = 40h (from 2025-07-16 08:00 to 2025-07-18 00:00 = 40h) → Despatch 80h
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 7, 16, 8)},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 7, 18, 0)},
        ])

        api_client.force_authenticate(user=finance_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/settle/"
        res = api_client.post(url, {}, format="json")

        assert res.status_code == drf_status.HTTP_201_CREATED
        data = res.json()
        assert data["outcome"] == "DESPATCH"
        assert Decimal(data["variance_hours"]) == Decimal("-80.000000")
        assert Decimal(data["gross_despatch_usd"]) > Decimal("0.00")
        assert Decimal(data["gross_demurrage_usd"]) == Decimal("0.00")
        assert Decimal(data["net_settlement_usd"]) < Decimal("0.00")

    def test_locking_settlement_prevents_further_recomputation(
        self, api_client, db, finance_user
    ):
        """Once outcome is SETTLED, subsequent POST /settle/ returns 409 SETTLEMENT_LOCKED."""
        _, _, voyage = self._setup_voyage(db, "S07", "SHINC")
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 7, 16, 0)},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 7, 21, 20)},
        ])

        api_client.force_authenticate(user=finance_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/settle/"

        # Settle and lock
        res1 = api_client.post(url, {"outcome": "SETTLED", "settlement_notes": "Charterer agreed to figures"}, format="json")
        assert res1.status_code == drf_status.HTTP_201_CREATED
        assert res1.json()["outcome"] == "SETTLED"

        # Try to recompute
        res2 = api_client.post(url, {"settlement_notes": "Attempting recomputation"}, format="json")
        assert res2.status_code == drf_status.HTTP_409_CONFLICT
        assert res2.json()["error"] == "SETTLEMENT_LOCKED"

    def test_disputing_settlement_prevents_further_recomputation(
        self, api_client, db, finance_user
    ):
        """Once outcome is DISPUTED, subsequent POST /settle/ returns 409 SETTLEMENT_LOCKED."""
        _, _, voyage = self._setup_voyage(db, "S08", "SHINC")
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 7, 16, 0)},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 7, 21, 20)},
        ])

        api_client.force_authenticate(user=finance_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/settle/"

        # Mark disputed
        res1 = api_client.post(url, {"outcome": "DISPUTED", "settlement_notes": "Owner disputes weather allowance"}, format="json")
        assert res1.status_code == drf_status.HTTP_201_CREATED
        assert res1.json()["outcome"] == "DISPUTED"

        # Try to recompute
        res2 = api_client.post(url, {}, format="json")
        assert res2.status_code == drf_status.HTTP_409_CONFLICT
        assert res2.json()["error"] == "SETTLEMENT_LOCKED"

    def test_logistics_agent_can_settle(
        self, api_client, db, logistics_user
    ):
        """LOGISTICS_AGENT is permitted to post settlement."""
        _, _, voyage = self._setup_voyage(db, "S09", "SHINC")
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 7, 16, 8)},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 7, 18, 0)},
        ])

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/settle/"
        res = api_client.post(url, {}, format="json")
        assert res.status_code == drf_status.HTTP_201_CREATED

    def test_auditor_can_get_but_cannot_post(
        self, api_client, db, finance_user, auditor_user
    ):
        """AUDITOR can read settlement, but is forbidden from mutating (403 FORBIDDEN)."""
        _, _, voyage = self._setup_voyage(db, "S10", "SHINC")
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 7, 16, 8)},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 7, 18, 0)},
        ])

        # Settle first as finance
        api_client.force_authenticate(user=finance_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/settle/"
        api_client.post(url, {}, format="json")

        # Switch to auditor
        api_client.force_authenticate(user=auditor_user)
        res_get = api_client.get(url)
        assert res_get.status_code == drf_status.HTTP_200_OK

        res_post = api_client.post(url, {}, format="json")
        assert res_post.status_code == drf_status.HTTP_403_FORBIDDEN

    def test_readonly_user_cannot_post_settle(
        self, api_client, db, readonly_user
    ):
        """READONLY user is rejected with 403 FORBIDDEN on POST /settle/."""
        _, _, voyage = self._setup_voyage(db, "S11", "SHINC")
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 7, 16, 8)},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 7, 18, 0)},
        ])

        api_client.force_authenticate(user=readonly_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/settle/"
        res = api_client.post(url, {}, format="json")
        assert res.status_code == drf_status.HTTP_403_FORBIDDEN

    def test_laytime_record_alias_and_db_accounting_invariants(
        self, api_client, db, finance_user
    ):
        """Verify LaytimeRecord alias and that accounting invariants hold on persisted row."""
        from apps.voyages.models import LaytimeRecord, LaytimeCalculation

        assert LaytimeRecord is LaytimeCalculation

        _, _, voyage = self._setup_voyage(db, "S12", "WWDSHEX")
        # WWDSHEX: 120h allowed. 10h rain (excluded) + 130h discharge = 140h port time, 130h counted
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 7, 16, 0)},
            {"event_type": "RAIN_COMMENCED", "event_timestamp": _utc(2025, 7, 16, 10)},
            {"event_type": "RAIN_CEASED", "event_timestamp": _utc(2025, 7, 16, 20)},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 7, 21, 20)},
        ])

        api_client.force_authenticate(user=finance_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/settle/"
        res = api_client.post(url, {}, format="json")
        assert res.status_code == drf_status.HTTP_201_CREATED

        rec = LaytimeRecord.objects.get(voyage=voyage, is_current=True)
        # Accounting invariant: counted + excepted <= port + 0.001
        assert (rec.total_counted_hours + rec.total_excepted_hours) <= (rec.total_port_hours + Decimal("0.001"))
        # Financial invariant: not both demurrage and despatch non-zero
        assert rec.gross_demurrage_usd == Decimal("0.00") or rec.gross_despatch_usd == Decimal("0.00")

    def test_logistics_agent_cannot_approve_settlement_vertical_privilege_escalation(
        self, api_client, db, logistics_user
    ):
        """Port Logistics Agent is blocked (403) from approving or locking financial settlement."""
        _, _, voyage = self._setup_voyage(db, "S13", "SHINC")
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 7, 16, 8)},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 7, 18, 0)},
        ])

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/settle/"
        res = api_client.post(url, {"outcome": "SETTLED", "settlement_notes": "Attempting agent lock"}, format="json")
        assert res.status_code == drf_status.HTTP_403_FORBIDDEN
        assert "Port Logistics Agents cannot approve" in res.json()["detail"]

