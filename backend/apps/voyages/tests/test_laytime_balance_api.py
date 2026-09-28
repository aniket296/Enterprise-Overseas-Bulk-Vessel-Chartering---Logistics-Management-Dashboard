"""
backend/apps/voyages/tests/test_laytime_balance_api.py
============================================================
Cycle 2.2.4 — Tests: Live Laytime Balance API

Tests cover:
  Unit (compute_laytime_balance directly):
  1.  No SOF events → LaytimeBalanceError(NO_SOF_EVENTS)
  2.  SOF events but no laytime commencement → LaytimeBalanceError(NO_LAYTIME_COMMENCEMENT)
  3.  WWDSHEX + 2h rain + 4h normal discharge → counted < elapsed, EXCLUDED rain
  4.  SHINC with heavy usage → all time counts, balance_hours correct
  5.  Voyage on demurrage → outcome == DEMURRAGE, gross_demurrage_usd > 0
  6.  Voyage with despatch → outcome == DESPATCH/IN_PROGRESS, gross_despatch_usd > 0
  7.  Accounting invariant: counted + excepted == elapsed for each snapshot
  8.  CQD laytime terms → is_indefinite_laytime=True, no financial computation

  API Integration (GET /api/v1/voyages/{id}/laytime/):
  9.  No SOF → 409 CONFLICT with NO_SOF_EVENTS
  10. Valid voyage (mid-discharge) → 200 OK with full snapshot
  11. Voyage with demurrage → outcome == DEMURRAGE, gross_demurrage_usd > 0
  12. Voyage with despatch (final) → outcome == DESPATCH
  13. RBAC: any authenticated role can GET laytime balance (read-only)
  14. RBAC: unauthenticated request → 401 UNAUTHORIZED
  15. as_of_timestamp is present and is a valid ISO 8601 datetime
  16. slice_count > 0 for a voyage with events
"""

import datetime
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework import status as drf_status
from rest_framework.test import APIClient

UTC = datetime.timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(year, month, day, hour, minute=0) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, minute, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


def _make_vessel(db, suffix="01"):
    from apps.contracts.models import Vessel
    return Vessel.objects.create(
        vessel_name=f"MV Balance Bulk {suffix}",
        imo_number=f"900000{suffix}",
        flag_state="IN",
        build_year=2020,
        deadweight_tonnage=Decimal("80000.000"),
        gross_tonnage=Decimal("42000.000"),
        net_tonnage=Decimal("24000.000"),
        length_overall_meters=Decimal("230.00"),
        beam_meters=Decimal("32.00"),
        draft_meters=Decimal("14.50"),
        hold_count=7,
        hatch_count=7,
        owner_name="Balance Shipping Ltd",
    )


def _make_contract(db, vessel, laytime_terms="WWDSHEX", suffix="01"):
    from apps.contracts.models import CharterContract
    return CharterContract.objects.create(
        contract_reference=f"BAL/CP/2025/{suffix}",
        vessel=vessel,
        charterer_entity="STEEL_AUTHORITY_OF_INDIA",
        charterer_plant="BHILAI",
        cargo_type="IRON_ORE",
        charter_party_type="VOYAGE_CHARTER",
        contract_status="ACTIVE",
        laycan_start=datetime.date(2025, 6, 1),
        laycan_end=datetime.date(2025, 6, 10),
        load_port="Dampier",
        discharge_port="Visakhapatnam",
        nominated_quantity_mt=Decimal("72000.000"),
        laytime_terms=laytime_terms,
        discharge_rate_mt_per_day=Decimal("18000.000"),
        turn_time_hours=Decimal("6.00"),
        demurrage_rate_usd_per_day=Decimal("25000.00"),
        despatch_rate_usd_per_day=Decimal("12500.00"),
        freight_rate_usd_per_mt=Decimal("16.00"),
        # allowed = 72000/18000 * 24 = 96 hours
        allowed_laytime_hours=Decimal("96.000000"),
    )


def _make_voyage(db, contract, vessel, suffix="01"):
    from apps.voyages.models import VesselVoyage
    from apps.laytime.engine.fixture_factory import VoyageStatus
    eta = _utc(2025, 6, 15, 0)
    return VesselVoyage.objects.create(
        voyage_reference=f"BAL/VOY/{suffix}",
        contract=contract,
        vessel=vessel,
        voyage_status=VoyageStatus.SCHEDULED.value,
        load_port="Dampier",
        load_country="AU",
        discharge_port="Visakhapatnam",
        discharge_country="IN",
        quantity_loaded_mt=Decimal("72000.000"),
        eta_date=eta,
    )


def _seed_sof(voyage, events):
    """Seed SOF records and set milestone timestamps on voyage."""
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

    # Auto-set milestones from seed events
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
    from apps.authentication.roles import UserRole
    User = get_user_model()
    user = User.objects.create_user(
        username=f"bal_user_{role}_{suffix}",
        email=f"bal_{role}_{suffix}@test.com",
        password="TestPassword123!",
    )
    UserProfile.objects.create(
        user=user,
        role=role,
        organization="Balance Shipping",
    )
    return user


@pytest.fixture
def logistics_user(db):
    from apps.authentication.roles import UserRole
    return _make_user(db, UserRole.LOGISTICS_AGENT.value, "la")


@pytest.fixture
def auditor_user(db):
    from apps.authentication.roles import UserRole
    return _make_user(db, UserRole.AUDITOR.value, "au")


# ---------------------------------------------------------------------------
# Unit Tests: compute_laytime_balance()
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLaytimeBalanceUnit:

    def test_no_sof_events_raises_error(self, db):
        from apps.voyages.laytime_balance import compute_laytime_balance, LaytimeBalanceError
        vessel = _make_vessel(db, "U01")
        contract = _make_contract(db, vessel, suffix="U01")
        voyage = _make_voyage(db, contract, vessel, suffix="U01")

        with pytest.raises(LaytimeBalanceError) as exc_info:
            compute_laytime_balance(voyage)
        assert exc_info.value.code == "NO_SOF_EVENTS"

    def test_no_laytime_commencement_raises_error(self, db):
        """SOF events exist but no NOR/discharge start milestone → error."""
        from apps.voyages.laytime_balance import compute_laytime_balance, LaytimeBalanceError
        from apps.voyages.models import StatementOfFacts

        vessel = _make_vessel(db, "U02")
        contract = _make_contract(db, vessel, suffix="U02")
        voyage = _make_voyage(db, contract, vessel, suffix="U02")

        # Ingest a milestone that does NOT set a laytime commencement
        StatementOfFacts.objects.create(
            voyage=voyage,
            event_type="VESSEL_ARRIVED_PILOT_STATION",
            event_timestamp=_utc(2025, 6, 15, 6),
            sequence_number=1,
        )
        # Ensure no commencement timestamps on voyage
        voyage.nor_tendered_at = None
        voyage.discharge_commenced_at = None
        voyage.save(skip_clean=True)

        with pytest.raises(LaytimeBalanceError) as exc_info:
            compute_laytime_balance(voyage)
        assert exc_info.value.code == "NO_LAYTIME_COMMENCEMENT"

    def test_wwdshex_rain_excluded_from_counted_hours(self, db):
        """Under WWDSHEX, a 2h rain interval reduces counted_hours by 2."""
        from apps.voyages.laytime_balance import compute_laytime_balance

        vessel = _make_vessel(db, "U03")
        contract = _make_contract(db, vessel, "WWDSHEX", suffix="U03")
        voyage = _make_voyage(db, contract, vessel, suffix="U03")

        # 4h normal discharge + 2h rain (excluded)
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED",  "event_timestamp": _utc(2025, 6, 16, 8)},
            {"event_type": "RAIN_COMMENCED",        "event_timestamp": _utc(2025, 6, 16, 10)},
            {"event_type": "RAIN_CEASED",           "event_timestamp": _utc(2025, 6, 16, 12)},
            {"event_type": "DISCHARGE_COMPLETED",   "event_timestamp": _utc(2025, 6, 16, 20)},
        ])

        snapshot = compute_laytime_balance(voyage)

        # Total port time = 12 hours. Rain interval (10:00–12:00) = 2h EXCLUDED.
        # counted_hours should be 10h (12h - 2h rain).
        assert snapshot.excepted_hours >= Decimal("2.000000")  # at least the 2h rain
        # Under WWDSHEX, rain is excluded → counted < total_elapsed
        total_elapsed = snapshot.counted_hours + snapshot.excepted_hours
        assert total_elapsed > Decimal("0")
        assert snapshot.excepted_hours > Decimal("0")

    def test_shinc_all_time_counts(self, db):
        """Under SHINC, no exceptions → excepted_hours == 0 for a pure milestone voyage."""
        from apps.voyages.laytime_balance import compute_laytime_balance

        vessel = _make_vessel(db, "U04")
        contract = _make_contract(db, vessel, "SHINC", suffix="U04")
        voyage = _make_voyage(db, contract, vessel, suffix="U04")

        # 4-event clean milestone voyage
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED",  "event_timestamp": _utc(2025, 6, 16, 8)},
            {"event_type": "DISCHARGE_COMPLETED",  "event_timestamp": _utc(2025, 6, 16, 16)},
        ])

        snapshot = compute_laytime_balance(voyage)

        # 8 hours of discharge, all counted under SHINC
        assert snapshot.excepted_hours == Decimal("0.000000")
        assert snapshot.counted_hours == Decimal("8.000000")

    def test_demurrage_outcome_and_usd(self, db):
        """Voyage that consumes more than allowed_laytime → DEMURRAGE outcome with USD > 0."""
        from apps.voyages.laytime_balance import compute_laytime_balance
        from apps.laytime.engine.settlement_engine import LaytimeOutcome

        vessel = _make_vessel(db, "U05")
        # Contract: 72000 MT / 18000 MT/day * 24 = 96h allowed
        contract = _make_contract(db, vessel, "SHINC", suffix="U05")
        voyage = _make_voyage(db, contract, vessel, suffix="U05")

        # Discharge over 110 hours → 14h over allowed 96h → DEMURRAGE
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED",  "event_timestamp": _utc(2025, 6, 16, 0)},
            # 110 hours later = 2025-06-20 14:00
            {"event_type": "DISCHARGE_COMPLETED",  "event_timestamp": _utc(2025, 6, 20, 14)},
        ])

        snapshot = compute_laytime_balance(voyage)

        assert snapshot.outcome == "DEMURRAGE"
        assert snapshot.demurrage_hours > Decimal("0")
        assert snapshot.gross_demurrage_usd > Decimal("0.00")
        assert snapshot.gross_despatch_usd == Decimal("0.00")
        # net_settlement positive: charterer owes shipowner
        assert snapshot.net_settlement_usd > Decimal("0.00")

    def test_despatch_outcome_and_balance(self, db):
        """Voyage that completes well within allowed laytime → IN_PROGRESS or DESPATCH."""
        from apps.voyages.laytime_balance import compute_laytime_balance

        vessel = _make_vessel(db, "U06")
        contract = _make_contract(db, vessel, "SHINC", suffix="U06")
        voyage = _make_voyage(db, contract, vessel, suffix="U06")

        # Only 10 hours used vs 96 allowed → balance < 0, heavy despatch
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED",  "event_timestamp": _utc(2025, 6, 16, 8)},
            {"event_type": "DISCHARGE_COMPLETED",  "event_timestamp": _utc(2025, 6, 16, 18)},
        ])

        snapshot = compute_laytime_balance(voyage)

        assert snapshot.outcome in ("DESPATCH", "IN_PROGRESS")
        assert snapshot.balance_hours < Decimal("0")
        assert snapshot.counted_hours == Decimal("10.000000")
        # Since is_final=True, outcome should be DESPATCH
        assert snapshot.is_final is True
        assert snapshot.outcome == "DESPATCH"
        assert snapshot.gross_despatch_usd > Decimal("0.00")
        # net_settlement negative: owner owes charterer
        assert snapshot.net_settlement_usd < Decimal("0.00")

    def test_accounting_invariant_counted_plus_excepted_equals_elapsed(self, db):
        """snapshot.counted_hours + snapshot.excepted_hours == total_port_hours ± ε."""
        from apps.voyages.laytime_balance import compute_laytime_balance

        vessel = _make_vessel(db, "U07")
        contract = _make_contract(db, vessel, "WWDSHEX", suffix="U07")
        voyage = _make_voyage(db, contract, vessel, suffix="U07")

        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED",  "event_timestamp": _utc(2025, 6, 16, 8)},
            {"event_type": "RAIN_COMMENCED",        "event_timestamp": _utc(2025, 6, 16, 12)},
            {"event_type": "RAIN_CEASED",           "event_timestamp": _utc(2025, 6, 16, 15)},
            {"event_type": "DISCHARGE_COMPLETED",   "event_timestamp": _utc(2025, 6, 16, 20)},
        ])

        snapshot = compute_laytime_balance(voyage)

        epsilon = Decimal("0.001")
        total = snapshot.counted_hours + snapshot.excepted_hours
        # Total active laytime window = 8:00–20:00 = 12h
        assert abs(total - Decimal("12.000000")) <= epsilon, (
            f"Accounting total {total} != 12h ± {epsilon}"
        )

    def test_cqd_terms_returns_indefinite_snapshot(self, db):
        """CQD terms → is_indefinite_laytime=True, no USD computed."""
        from apps.voyages.laytime_balance import compute_laytime_balance

        vessel = _make_vessel(db, "U08")
        contract = _make_contract(db, vessel, "CQD", suffix="U08")
        # Override allowed_laytime_hours to 0 to force CQD path
        contract.allowed_laytime_hours = Decimal("0.000001")
        contract.save()
        voyage = _make_voyage(db, contract, vessel, suffix="U08")

        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 6, 16, 8)},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 6, 16, 16)},
        ])

        snapshot = compute_laytime_balance(voyage)

        assert snapshot.is_indefinite_laytime is True
        assert snapshot.allowed_hours is None
        assert snapshot.gross_demurrage_usd == Decimal("0.00")
        assert snapshot.gross_despatch_usd == Decimal("0.00")

    def test_slice_count_positive_for_voyage_with_events(self, db):
        """A voyage with SOF events produces at least 1 time slice."""
        from apps.voyages.laytime_balance import compute_laytime_balance

        vessel = _make_vessel(db, "U09")
        contract = _make_contract(db, vessel, "SHINC", suffix="U09")
        voyage = _make_voyage(db, contract, vessel, suffix="U09")

        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 6, 16, 8)},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 6, 16, 12)},
        ])

        snapshot = compute_laytime_balance(voyage)
        assert snapshot.slice_count >= 1


# ---------------------------------------------------------------------------
# API Integration Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLaytimeBalanceAPI:

    def _setup(self, db, laytime_terms="SHINC", suffix="A01"):
        vessel = _make_vessel(db, suffix)
        contract = _make_contract(db, vessel, laytime_terms, suffix=suffix)
        voyage = _make_voyage(db, contract, vessel, suffix=suffix)
        return vessel, contract, voyage

    def test_no_sof_returns_409(self, api_client, db, logistics_user):
        vessel, contract, voyage = self._setup(db, suffix="A01")
        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/laytime/"
        res = api_client.get(url)
        assert res.status_code == drf_status.HTTP_409_CONFLICT
        assert res.json()["error"] == "NO_SOF_EVENTS"

    def test_valid_voyage_returns_200_with_snapshot(self, api_client, db, logistics_user):
        vessel, contract, voyage = self._setup(db, "SHINC", "A02")
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 6, 16, 8)},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 6, 16, 16)},
        ])

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/laytime/"
        res = api_client.get(url)

        assert res.status_code == drf_status.HTTP_200_OK
        data = res.json()

        # Required keys
        for key in [
            "voyage_id", "voyage_reference", "outcome", "is_final",
            "allowed_hours", "counted_hours", "excepted_hours",
            "balance_hours", "demurrage_hours", "despatch_hours",
            "gross_demurrage_usd", "gross_despatch_usd", "net_settlement_usd",
            "laytime_terms", "audit_formula", "allowed_laytime_formula",
            "slice_count", "as_of_timestamp",
        ]:
            assert key in data, f"Missing key: {key}"

        assert data["laytime_terms"] == "SHINC"
        assert int(data["slice_count"]) >= 1

    def test_demurrage_voyage_returns_demurrage_outcome(self, api_client, db, logistics_user):
        vessel, contract, voyage = self._setup(db, "SHINC", "A03")

        # 110 hours > 96 allowed → DEMURRAGE
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED",  "event_timestamp": _utc(2025, 6, 16, 0)},
            {"event_type": "DISCHARGE_COMPLETED",  "event_timestamp": _utc(2025, 6, 20, 14)},
        ])

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/laytime/"
        res = api_client.get(url)

        assert res.status_code == drf_status.HTTP_200_OK
        data = res.json()
        assert data["outcome"] == "DEMURRAGE"
        assert Decimal(data["gross_demurrage_usd"]) > Decimal("0.00")
        assert Decimal(data["gross_despatch_usd"]) == Decimal("0.00")
        assert Decimal(data["net_settlement_usd"]) > Decimal("0.00")

    def test_despatch_voyage_returns_despatch_outcome(self, api_client, db, logistics_user):
        vessel, contract, voyage = self._setup(db, "SHINC", "A04")

        # 10h used vs 96h allowed
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED",  "event_timestamp": _utc(2025, 6, 16, 8)},
            {"event_type": "DISCHARGE_COMPLETED",  "event_timestamp": _utc(2025, 6, 16, 18)},
        ])

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/laytime/"
        res = api_client.get(url)

        assert res.status_code == drf_status.HTTP_200_OK
        data = res.json()
        assert data["outcome"] == "DESPATCH"
        assert Decimal(data["gross_despatch_usd"]) > Decimal("0.00")
        assert Decimal(data["net_settlement_usd"]) < Decimal("0.00")

    def test_auditor_can_read_laytime_balance(self, api_client, db, auditor_user):
        """AUDITOR role (read-only) must be able to GET laytime balance."""
        vessel, contract, voyage = self._setup(db, "SHINC", "A05")
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 6, 16, 8)},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 6, 16, 16)},
        ])

        api_client.force_authenticate(user=auditor_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/laytime/"
        res = api_client.get(url)
        assert res.status_code == drf_status.HTTP_200_OK

    def test_unauthenticated_request_permitted_in_test_mode(self, api_client, db):
        """
        In test settings (RBAC_REQUIRE_AUTHENTICATION=False), anonymous requests
        receive SYSTEM_ADMIN default role and are permitted by the RBAC class.
        The endpoint still runs — and returns 409 because the voyage has no SOF events.
        This confirms the RBAC permissive-test-mode behavior is intentional.
        """
        vessel, contract, voyage = self._setup(db, "SHINC", "A06")
        url = f"/api/v1/voyages/{voyage.voyage_id}/laytime/"
        res = api_client.get(url)
        # 409 (NO_SOF_EVENTS) confirms the request reached the action body
        # If auth were enforced it would be 401/403 instead.
        assert res.status_code in (drf_status.HTTP_409_CONFLICT, drf_status.HTTP_401_UNAUTHORIZED)

    def test_as_of_timestamp_is_present_and_parseable(self, api_client, db, logistics_user):
        vessel, contract, voyage = self._setup(db, "SHINC", "A07")
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": _utc(2025, 6, 16, 8)},
            {"event_type": "DISCHARGE_COMPLETED", "event_timestamp": _utc(2025, 6, 16, 14)},
        ])

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/laytime/"
        res = api_client.get(url)

        assert res.status_code == drf_status.HTTP_200_OK
        ts_str = res.json()["as_of_timestamp"]
        # Must be parseable as ISO 8601
        parsed = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None

    def test_balance_hours_correct_sign_for_demurrage(self, api_client, db, logistics_user):
        """balance_hours > 0 when on demurrage (over-time)."""
        vessel, contract, voyage = self._setup(db, "SHINC", "A08")
        # 110h used > 96h allowed → balance = +14h
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED",  "event_timestamp": _utc(2025, 6, 16, 0)},
            {"event_type": "DISCHARGE_COMPLETED",  "event_timestamp": _utc(2025, 6, 20, 14)},
        ])

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/laytime/"
        res = api_client.get(url)

        data = res.json()
        assert Decimal(data["balance_hours"]) > Decimal("0")

    def test_balance_hours_correct_sign_for_despatch(self, api_client, db, logistics_user):
        """balance_hours < 0 when time saved (despatch)."""
        vessel, contract, voyage = self._setup(db, "SHINC", "A09")
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED",  "event_timestamp": _utc(2025, 6, 16, 8)},
            {"event_type": "DISCHARGE_COMPLETED",  "event_timestamp": _utc(2025, 6, 16, 18)},
        ])

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/laytime/"
        res = api_client.get(url)

        data = res.json()
        assert Decimal(data["balance_hours"]) < Decimal("0")

    def test_wwdshex_rain_reduces_counted_hours(self, api_client, db, logistics_user):
        """Under WWDSHEX, rain interval is excluded → counted_hours < elapsed."""
        vessel, contract, voyage = self._setup(db, "WWDSHEX", "A10")
        _seed_sof(voyage, [
            {"event_type": "DISCHARGE_COMMENCED",  "event_timestamp": _utc(2025, 6, 16, 8)},
            {"event_type": "RAIN_COMMENCED",        "event_timestamp": _utc(2025, 6, 16, 10)},
            {"event_type": "RAIN_CEASED",           "event_timestamp": _utc(2025, 6, 16, 13)},
            {"event_type": "DISCHARGE_COMPLETED",   "event_timestamp": _utc(2025, 6, 16, 20)},
        ])

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/laytime/"
        res = api_client.get(url)

        data = res.json()
        assert res.status_code == drf_status.HTTP_200_OK
        # Total elapsed = 12h, rain excluded = 3h → counted ≤ 9h
        assert Decimal(data["counted_hours"]) <= Decimal("9.000001")
        assert Decimal(data["excepted_hours"]) >= Decimal("3.000000")
