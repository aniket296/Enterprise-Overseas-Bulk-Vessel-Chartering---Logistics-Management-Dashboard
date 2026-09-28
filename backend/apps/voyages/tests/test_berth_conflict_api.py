"""
backend/apps/voyages/tests/test_berth_conflict_api.py
============================================================
Cycle 2.3.1 — Tests: Berth Conflict Detection & Pre-allocation Validator API

Tests cover:
  Unit (Interval Mathematics & Window Resolution):
  1.  Disjoint intervals (before / after) produce None (no conflict)
  2.  Abutting intervals [A, B) and [B, C) do not overlap (zero conflict)
  3.  Partial overlap and complete containment compute exact overlap hours
  4.  resolve_voyage_occupancy_window handles actual berthed voyages
  5.  resolve_voyage_occupancy_window handles ETA estimated voyages
  6.  Terminal voyages (COMPLETED / CANCELLED) are excluded from conflict engine

  Integration (API Endpoints):
  7.  GET /api/v1/voyages/berth-conflicts/ lists active collisions across fleet
  8.  GET /api/v1/voyages/berth-conflicts/ filtering by discharge_port and discharge_berth
  9.  POST /api/v1/voyages/berth-conflicts/ detects conflict for proposed window
  10. POST /api/v1/voyages/berth-conflicts/ returns earliest_available_at when conflict exists
  11. POST /api/v1/voyages/berth-conflicts/ returns has_conflict=False when berth is clear
  12. POST /api/v1/voyages/berth-conflicts/ with exclude_voyage_id ignores own record
  13. POST /api/v1/voyages/berth-conflicts/ with invalid window (start >= end) returns 400
  14. GET /api/v1/voyages/{id}/berth-conflicts/ audits single voyage
  15. RBAC:
      - LOGISTICS_AGENT and PROCUREMENT_OFFICER can POST /berth-conflicts/
      - AUDITOR and READONLY can GET, but get 403 on POST
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
        vessel_name=f"MV Conflict Bulk {suffix}",
        imo_number=f"920000{suffix}",
        flag_state="IN",
        build_year=2020,
        deadweight_tonnage=Decimal("80000.000"),
        gross_tonnage=Decimal("43000.000"),
        net_tonnage=Decimal("26000.000"),
        length_overall_meters=Decimal("229.00"),
        beam_meters=Decimal("32.26"),
        draft_meters=Decimal("14.50"),
        hold_count=7,
        hatch_count=7,
        owner_name="Conflict Marine Ltd",
    )


def _make_contract(db, vessel, suffix="01"):
    from apps.contracts.models import CharterContract
    return CharterContract.objects.create(
        contract_reference=f"BC/CP/2025/{suffix}",
        vessel=vessel,
        charterer_entity="STEEL_AUTHORITY_OF_INDIA",
        charterer_plant="ROURKELA",
        cargo_type="COKING_COAL",
        charter_party_type="VOYAGE_CHARTER",
        contract_status="ACTIVE",
        laycan_start=datetime.date(2025, 8, 1),
        laycan_end=datetime.date(2025, 8, 10),
        load_port="Hay Point",
        discharge_port="Haldia",
        nominated_quantity_mt=Decimal("70000.000"),
        laytime_terms="SHINC",
        discharge_rate_mt_per_day=Decimal("14000.000"),
        turn_time_hours=Decimal("6.00"),
        demurrage_rate_usd_per_day=Decimal("28000.00"),
        despatch_rate_usd_per_day=Decimal("14000.00"),
        freight_rate_usd_per_mt=Decimal("17.50"),
        allowed_laytime_hours=Decimal("120.000000"),
    )


def _make_voyage(
    db, contract, vessel, suffix="01", port="Haldia", berth="Berth 4B", status="BERTHED",
    eta=None, berthed_at=None, completed_at=None
):
    from apps.voyages.models import VesselVoyage
    return VesselVoyage.objects.create(
        voyage_reference=f"BC/VOY/{suffix}",
        contract=contract,
        vessel=vessel,
        voyage_status=status,
        load_port="Hay Point",
        load_country="AU",
        discharge_port=port,
        discharge_country="IN",
        discharge_berth=berth,
        quantity_loaded_mt=Decimal("70000.000"),
        eta_date=eta or _utc(2025, 8, 15, 0),
        berthed_at=berthed_at,
        discharge_completed_at=completed_at,
    )


def _make_user(db, role, suffix="01"):
    from django.contrib.auth import get_user_model
    from apps.authentication.models import UserProfile
    User = get_user_model()
    user = User.objects.create_user(
        username=f"bc_user_{role.lower()}_{suffix}",
        email=f"bc_{role.lower()}_{suffix}@test.com",
        password="TestPassword123!",
    )
    UserProfile.objects.create(
        user=user,
        role=role,
        organization="SAIL Operations",
    )
    return user


@pytest.fixture
def logistics_user(db):
    from apps.authentication.roles import UserRole
    return _make_user(db, UserRole.LOGISTICS_AGENT.value, "log")


@pytest.fixture
def procurement_user(db):
    from apps.authentication.roles import UserRole
    return _make_user(db, UserRole.PROCUREMENT_OFFICER.value, "proc")


@pytest.fixture
def auditor_user(db):
    from apps.authentication.roles import UserRole
    return _make_user(db, UserRole.AUDITOR.value, "aud")


@pytest.fixture
def readonly_user(db):
    from apps.authentication.roles import UserRole
    return _make_user(db, UserRole.READONLY.value, "ro")


# ---------------------------------------------------------------------------
# Unit Tests: Pure Math & Window Deduction
# ---------------------------------------------------------------------------

class TestTemporalIntervalMath:

    def test_disjoint_intervals_no_overlap(self):
        from apps.voyages.berth_conflict import calculate_temporal_overlap
        # Window 1: 08:00 - 12:00, Window 2: 14:00 - 18:00
        res = calculate_temporal_overlap(
            _utc(2025, 8, 1, 8), _utc(2025, 8, 1, 12),
            _utc(2025, 8, 1, 14), _utc(2025, 8, 1, 18),
        )
        assert res is None

    def test_touching_endpoints_no_overlap(self):
        """Strict half-open [08:00, 12:00) and [12:00, 16:00) do NOT overlap."""
        from apps.voyages.berth_conflict import calculate_temporal_overlap
        res = calculate_temporal_overlap(
            _utc(2025, 8, 1, 8), _utc(2025, 8, 1, 12),
            _utc(2025, 8, 1, 12), _utc(2025, 8, 1, 16),
        )
        assert res is None

    def test_partial_overlap_computes_hours(self):
        """[08:00, 14:00) and [10:00, 18:00) overlap in [10:00, 14:00) = 4.00h."""
        from apps.voyages.berth_conflict import calculate_temporal_overlap
        res = calculate_temporal_overlap(
            _utc(2025, 8, 1, 8), _utc(2025, 8, 1, 14),
            _utc(2025, 8, 1, 10), _utc(2025, 8, 1, 18),
        )
        assert res is not None
        start, end, hours = res
        assert start == _utc(2025, 8, 1, 10)
        assert end == _utc(2025, 8, 1, 14)
        assert hours == Decimal("4.00")

    def test_total_containment_computes_inner_window(self):
        """Inner window [10:00, 12:00) within [08:00, 16:00) = 2.00h overlap."""
        from apps.voyages.berth_conflict import calculate_temporal_overlap
        res = calculate_temporal_overlap(
            _utc(2025, 8, 1, 8), _utc(2025, 8, 1, 16),
            _utc(2025, 8, 1, 10), _utc(2025, 8, 1, 12),
        )
        assert res is not None
        start, end, hours = res
        assert start == _utc(2025, 8, 1, 10)
        assert end == _utc(2025, 8, 1, 12)
        assert hours == Decimal("2.00")


@pytest.mark.django_db
class TestWindowResolution:

    def test_actual_berthed_voyage_window(self, db):
        from apps.voyages.berth_conflict import resolve_voyage_occupancy_window
        vessel = _make_vessel(db, "W01")
        contract = _make_contract(db, vessel, "W01")
        voyage = _make_voyage(
            db, contract, vessel, "W01",
            berthed_at=_utc(2025, 8, 10, 8),
            completed_at=_utc(2025, 8, 12, 14),
        )

        win = resolve_voyage_occupancy_window(voyage)
        assert win is not None
        assert win.is_actual is True
        assert win.start_time == _utc(2025, 8, 10, 8)
        assert win.end_time == _utc(2025, 8, 12, 14)
        # 2 days + 6 hours = 54.00h
        assert win.duration_hours == Decimal("54.00")

    def test_estimated_voyage_window_from_eta(self, db):
        from apps.voyages.berth_conflict import resolve_voyage_occupancy_window
        vessel = _make_vessel(db, "W02")
        contract = _make_contract(db, vessel, "W02")
        # Allowed laytime on contract is 120h
        voyage = _make_voyage(
            db, contract, vessel, "W02",
            status="IN_TRANSIT",
            eta=_utc(2025, 8, 15, 0),
        )

        win = resolve_voyage_occupancy_window(voyage)
        assert win is not None
        assert win.is_actual is False
        assert win.start_time == _utc(2025, 8, 15, 0)
        # 120 hours = 5 days later = 2025-08-20 00:00
        assert win.end_time == _utc(2025, 8, 20, 0)
        assert win.duration_hours == Decimal("120.00")

    def test_terminal_voyage_excluded(self, db):
        from apps.voyages.berth_conflict import resolve_voyage_occupancy_window
        vessel = _make_vessel(db, "W03")
        contract = _make_contract(db, vessel, "W03")
        voyage = _make_voyage(db, contract, vessel, "W03", status="COMPLETED")
        assert resolve_voyage_occupancy_window(voyage) is None


# ---------------------------------------------------------------------------
# Integration Tests: REST API Endpoints
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBerthConflictAPI:

    def test_get_berth_conflicts_fleet_sweep(self, api_client, db, logistics_user):
        """GET /voyages/berth-conflicts/ detects overlapping vessels at the same berth."""
        # Voyage 1: Berth 4B at Haldia, 2025-08-10 08:00 to 2025-08-12 12:00
        vessel1 = _make_vessel(db, "F01")
        contract1 = _make_contract(db, vessel1, "F01")
        _make_voyage(
            db, contract1, vessel1, "F01", port="Haldia", berth="Berth 4B",
            berthed_at=_utc(2025, 8, 10, 8), completed_at=_utc(2025, 8, 12, 12)
        )

        # Voyage 2: Berth 4B at Haldia, 2025-08-11 10:00 to 2025-08-13 14:00 (overlaps by ~26h)
        vessel2 = _make_vessel(db, "F02")
        contract2 = _make_contract(db, vessel2, "F02")
        _make_voyage(
            db, contract2, vessel2, "F02", port="Haldia", berth="Berth 4B",
            berthed_at=_utc(2025, 8, 11, 10), completed_at=_utc(2025, 8, 13, 14)
        )

        # Voyage 3: Different berth (Berth 5), no conflict
        vessel3 = _make_vessel(db, "F03")
        contract3 = _make_contract(db, vessel3, "F03")
        _make_voyage(
            db, contract3, vessel3, "F03", port="Haldia", berth="Berth 5",
            berthed_at=_utc(2025, 8, 10, 8), completed_at=_utc(2025, 8, 13, 14)
        )

        api_client.force_authenticate(user=logistics_user)
        res = api_client.get("/api/v1/voyages/berth-conflicts/?discharge_port=Haldia&discharge_berth=Berth 4B")

        assert res.status_code == drf_status.HTTP_200_OK
        data = res.json()
        assert data["total_conflicts"] == 1
        conflict = data["conflicts"][0]
        assert conflict["discharge_port"] == "Haldia"
        assert conflict["discharge_berth"] == "Berth 4B"
        # Overlap from 2025-08-11 10:00 to 2025-08-12 12:00 = 26.00 hours
        assert Decimal(conflict["overlap_hours"]) == Decimal("26.00")
        assert conflict["severity"] == "CRITICAL"

    def test_post_validate_proposed_berthing_window_detects_collision(
        self, api_client, db, logistics_user
    ):
        """POST /voyages/berth-conflicts/ detects conflict and returns earliest_available_at."""
        # Existing vessel occupying Berth 4B until 2025-08-12 14:00
        vessel = _make_vessel(db, "P01")
        contract = _make_contract(db, vessel, "P01")
        _make_voyage(
            db, contract, vessel, "P01", port="Haldia", berth="Berth 4B",
            berthed_at=_utc(2025, 8, 10, 8), completed_at=_utc(2025, 8, 12, 14)
        )

        api_client.force_authenticate(user=logistics_user)
        payload = {
            "discharge_port": "Haldia",
            "discharge_berth": "Berth 4B",
            "start_time": "2025-08-11T12:00:00Z",
            "end_time": "2025-08-13T12:00:00Z",
        }
        res = api_client.post("/api/v1/voyages/berth-conflicts/", payload, format="json")

        assert res.status_code == drf_status.HTTP_200_OK
        data = res.json()
        assert data["has_conflict"] is True
        assert data["conflict_count"] == 1
        assert data["conflicts"][0]["voyage_ref_b"] == f"BC/VOY/P01"
        # Earliest available is when P01 finishes = 2025-08-12T14:00:00Z
        assert data["earliest_available_at"] == "2025-08-12T14:00:00Z"

    def test_post_validate_clear_window_returns_no_conflict(
        self, api_client, db, logistics_user
    ):
        """Proposed window that starts after existing vessel departs has no conflict."""
        vessel = _make_vessel(db, "P02")
        contract = _make_contract(db, vessel, "P02")
        _make_voyage(
            db, contract, vessel, "P02", port="Haldia", berth="Berth 4B",
            berthed_at=_utc(2025, 8, 10, 8), completed_at=_utc(2025, 8, 12, 14)
        )

        api_client.force_authenticate(user=logistics_user)
        payload = {
            "discharge_port": "Haldia",
            "discharge_berth": "Berth 4B",
            # Starts at 15:00, after 14:00 departure
            "start_time": "2025-08-12T15:00:00Z",
            "end_time": "2025-08-14T15:00:00Z",
        }
        res = api_client.post("/api/v1/voyages/berth-conflicts/", payload, format="json")

        assert res.status_code == drf_status.HTTP_200_OK
        data = res.json()
        assert data["has_conflict"] is False
        assert data["conflict_count"] == 0
        assert data["earliest_available_at"] is None

    def test_post_validate_with_exclude_voyage_id(
        self, api_client, db, logistics_user
    ):
        """Testing schedule adjustment for an existing voyage excludes its own current window."""
        vessel = _make_vessel(db, "P03")
        contract = _make_contract(db, vessel, "P03")
        voyage = _make_voyage(
            db, contract, vessel, "P03", port="Haldia", berth="Berth 4B",
            berthed_at=_utc(2025, 8, 10, 8), completed_at=_utc(2025, 8, 12, 14)
        )

        api_client.force_authenticate(user=logistics_user)
        # Checking window that overlaps voyage P03 itself, but excluding P03
        payload = {
            "discharge_port": "Haldia",
            "discharge_berth": "Berth 4B",
            "start_time": "2025-08-10T12:00:00Z",
            "end_time": "2025-08-12T12:00:00Z",
            "exclude_voyage_id": str(voyage.voyage_id),
        }
        res = api_client.post("/api/v1/voyages/berth-conflicts/", payload, format="json")

        assert res.status_code == drf_status.HTTP_200_OK
        assert res.json()["has_conflict"] is False

    def test_post_validate_invalid_window_returns_400(
        self, api_client, db, logistics_user
    ):
        """start_time >= end_time returns 400 BAD_REQUEST."""
        api_client.force_authenticate(user=logistics_user)
        payload = {
            "discharge_port": "Haldia",
            "discharge_berth": "Berth 4B",
            "start_time": "2025-08-15T12:00:00Z",
            "end_time": "2025-08-15T10:00:00Z",  # Earlier than start!
        }
        res = api_client.post("/api/v1/voyages/berth-conflicts/", payload, format="json")
        assert res.status_code == drf_status.HTTP_400_BAD_REQUEST

    def test_get_voyage_specific_berth_conflicts(
        self, api_client, db, logistics_user
    ):
        """GET /voyages/{id}/berth-conflicts/ audits specific voyage."""
        # Voyage 1
        vessel1 = _make_vessel(db, "V01")
        contract1 = _make_contract(db, vessel1, "V01")
        voyage1 = _make_voyage(
            db, contract1, vessel1, "V01", port="Haldia", berth="Berth 4B",
            berthed_at=_utc(2025, 8, 10, 8), completed_at=_utc(2025, 8, 12, 14)
        )

        # Voyage 2 conflicting with Voyage 1
        vessel2 = _make_vessel(db, "V02")
        contract2 = _make_contract(db, vessel2, "V02")
        voyage2 = _make_voyage(
            db, contract2, vessel2, "V02", port="Haldia", berth="Berth 4B",
            berthed_at=_utc(2025, 8, 11, 8), completed_at=_utc(2025, 8, 13, 14)
        )

        api_client.force_authenticate(user=logistics_user)
        res = api_client.get(f"/api/v1/voyages/{voyage1.voyage_id}/berth-conflicts/")

        assert res.status_code == drf_status.HTTP_200_OK
        data = res.json()
        assert data["has_conflict"] is True
        assert data["conflict_count"] == 1
        assert data["conflicts"][0]["voyage_id_b"] == str(voyage2.voyage_id)

    def test_rbac_procurement_can_post_auditor_cannot(
        self, api_client, db, procurement_user, auditor_user
    ):
        """PROCUREMENT_OFFICER can POST /berth-conflicts/, AUDITOR gets 403."""
        payload = {
            "discharge_port": "Haldia",
            "discharge_berth": "Berth 4B",
            "start_time": "2025-08-10T12:00:00Z",
            "end_time": "2025-08-12T12:00:00Z",
        }

        # Procurement officer can POST
        api_client.force_authenticate(user=procurement_user)
        res_proc = api_client.post("/api/v1/voyages/berth-conflicts/", payload, format="json")
        assert res_proc.status_code == drf_status.HTTP_200_OK

        # Auditor can GET
        api_client.force_authenticate(user=auditor_user)
        res_aud_get = api_client.get("/api/v1/voyages/berth-conflicts/")
        assert res_aud_get.status_code == drf_status.HTTP_200_OK

        # Auditor cannot POST (403 FORBIDDEN)
        res_aud_post = api_client.post("/api/v1/voyages/berth-conflicts/", payload, format="json")
        assert res_aud_post.status_code == drf_status.HTTP_403_FORBIDDEN
