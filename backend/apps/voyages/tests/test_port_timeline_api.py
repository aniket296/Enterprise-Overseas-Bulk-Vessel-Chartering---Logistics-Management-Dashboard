"""
backend/apps/voyages/tests/test_port_timeline_api.py
============================================================
Cycle 2.3.2 — Tests: Berth Allocation & Port Congestion Timeline API

Tests cover:
  Engine Unit Tests:
  1.  get_port_congestion_kpis computes correct cargo sums, counts, and wait hours
  2.  get_port_congestion_kpis correctly classifies congestion_level (LOW, MODERATE, HIGH)
  3.  get_port_berth_timeline groups by berth and flags conflicting pairs
  4.  get_port_berth_timeline populates anchorage_queue for waiting vessels
  5.  assign_berth_to_voyage assigns clear berth without conflict
  6.  assign_berth_to_voyage raises BerthAllocationError on conflict when force=False
  7.  assign_berth_to_voyage succeeds on conflict when force=True (override)
  8.  assign_berth_to_voyage clearing berth sets discharge_berth to None

  REST API Integration Tests:
  9.  GET /api/v1/voyages/berth-timeline/ returns Gantt schedule and anchorage queue (200 OK)
  10. GET /api/v1/voyages/port-congestion/ returns multi-port congestion overview (200 OK)
  11. GET /api/v1/voyages/port-congestion/?discharge_port=Haldia returns single port KPIs (200 OK)
  12. POST /api/v1/voyages/{id}/assign-berth/ assigns clear berth (200 OK)
  13. POST /api/v1/voyages/{id}/assign-berth/ on conflict returns 409 CONFLICT (BERTH_CONFLICT)
  14. POST /api/v1/voyages/{id}/assign-berth/ with force=True overrides conflict (200 OK)
  15. RBAC permissions:
      - LOGISTICS_AGENT and PROCUREMENT_OFFICER can POST /assign-berth/
      - AUDITOR and READONLY can GET timeline/congestion, but get 403 on POST /assign-berth/
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
        vessel_name=f"MV Timeline Bulk {suffix}",
        imo_number=f"930000{suffix}",
        flag_state="IN",
        build_year=2021,
        deadweight_tonnage=Decimal("85000.000"),
        gross_tonnage=Decimal("45000.000"),
        net_tonnage=Decimal("27000.000"),
        length_overall_meters=Decimal("229.00"),
        beam_meters=Decimal("32.26"),
        draft_meters=Decimal("14.50"),
        hold_count=7,
        hatch_count=7,
        owner_name="Timeline Marine Ltd",
    )


def _make_contract(db, vessel, suffix="01"):
    from apps.contracts.models import CharterContract
    return CharterContract.objects.create(
        contract_reference=f"TL/CP/2025/{suffix}",
        vessel=vessel,
        charterer_entity="STEEL_AUTHORITY_OF_INDIA",
        charterer_plant="DURGAPUR",
        cargo_type="COKING_COAL",
        charter_party_type="VOYAGE_CHARTER",
        contract_status="ACTIVE",
        laycan_start=datetime.date(2025, 9, 1),
        laycan_end=datetime.date(2025, 9, 10),
        load_port="Newcastle",
        discharge_port="Haldia",
        nominated_quantity_mt=Decimal("75000.000"),
        laytime_terms="SHINC",
        discharge_rate_mt_per_day=Decimal("15000.000"),
        turn_time_hours=Decimal("6.00"),
        demurrage_rate_usd_per_day=Decimal("30000.00"),
        despatch_rate_usd_per_day=Decimal("15000.00"),
        freight_rate_usd_per_mt=Decimal("18.00"),
        allowed_laytime_hours=Decimal("120.000000"),
    )


def _make_voyage(
    db, contract, vessel, suffix="01", port="Haldia", berth="Berth 1", status="BERTHED",
    eta=None, ata=None, nor=None, berthed_at=None, completed_at=None, qty=Decimal("75000.000")
):
    from apps.voyages.models import VesselVoyage
    return VesselVoyage.objects.create(
        voyage_reference=f"TL/VOY/{suffix}",
        contract=contract,
        vessel=vessel,
        voyage_status=status,
        load_port="Newcastle",
        load_country="AU",
        discharge_port=port,
        discharge_country="IN",
        discharge_berth=berth,
        quantity_loaded_mt=qty,
        eta_date=eta or _utc(2025, 9, 15, 0),
        ata_date=ata,
        nor_tendered_at=nor,
        berthed_at=berthed_at,
        discharge_completed_at=completed_at,
    )


def _make_user(db, role, suffix="01"):
    from django.contrib.auth import get_user_model
    from apps.authentication.models import UserProfile
    User = get_user_model()
    user = User.objects.create_user(
        username=f"tl_user_{role.lower()}_{suffix}",
        email=f"tl_{role.lower()}_{suffix}@test.com",
        password="TestPassword123!",
    )
    UserProfile.objects.create(
        user=user,
        role=role,
        organization="Haldia Port Ops",
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
# Unit Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPortTimelineEngine:

    def test_get_port_congestion_kpis_calculation(self, db):
        from apps.voyages.port_timeline import get_port_congestion_kpis

        # 1 berthed vessel
        v1 = _make_vessel(db, "K01")
        c1 = _make_contract(db, v1, "K01")
        _make_voyage(
            db, c1, v1, "K01", port="Haldia", berth="Berth 1", status="DISCHARGING",
            ata=_utc(2025, 9, 10, 8),
            nor=_utc(2025, 9, 10, 10),
            berthed_at=_utc(2025, 9, 11, 10),  # waited 24h
            qty=Decimal("60000.000")
        )

        # 1 vessel at anchorage
        v2 = _make_vessel(db, "K02")
        c2 = _make_contract(db, v2, "K02")
        _make_voyage(
            db, c2, v2, "K02", port="Haldia", berth=None, status="NOR_TENDERED",
            ata=_utc(2025, 9, 12, 6),
            nor=_utc(2025, 9, 12, 8),
            qty=Decimal("70000.000")
        )

        kpis = get_port_congestion_kpis("Haldia")

        assert kpis.discharge_port == "Haldia"
        assert kpis.total_active_vessels == 2
        assert kpis.vessels_berthed_count == 1
        assert kpis.vessels_at_anchorage_count == 1
        assert kpis.vessels_unassigned_berth_count == 1
        assert kpis.total_cargo_berthed_mt == Decimal("60000.000")
        assert kpis.total_cargo_waiting_mt == Decimal("70000.000")
        # Waited from 10:00 to 10:00 next day = 24.00h
        assert kpis.average_anchorage_wait_hours == Decimal("24.00")
        assert kpis.congestion_level == "MODERATE"

    def test_congestion_level_high_when_queue_ge_3(self, db):
        from apps.voyages.port_timeline import get_port_congestion_kpis

        # Create 3 anchorage vessels
        for i in range(1, 4):
            v = _make_vessel(db, f"Q0{i}")
            c = _make_contract(db, v, f"Q0{i}")
            _make_voyage(
                db, c, v, f"Q0{i}", port="Paradip", berth=None, status="ARRIVED",
                ata=_utc(2025, 9, 12, 6),
            )

        kpis = get_port_congestion_kpis("Paradip")
        assert kpis.vessels_at_anchorage_count == 3
        assert kpis.congestion_level == "HIGH"

    def test_get_port_berth_timeline_groups_and_flags_conflict(self, db):
        from apps.voyages.port_timeline import get_port_berth_timeline

        # Voyage 1 at Berth 1: Sep 10 08:00 to Sep 12 12:00
        v1 = _make_vessel(db, "T01")
        c1 = _make_contract(db, v1, "T01")
        voy1 = _make_voyage(
            db, c1, v1, "T01", port="Haldia", berth="Berth 1",
            berthed_at=_utc(2025, 9, 10, 8), completed_at=_utc(2025, 9, 12, 12)
        )

        # Voyage 2 at Berth 1: Sep 11 10:00 to Sep 13 14:00 (overlaps with Voyage 1)
        v2 = _make_vessel(db, "T02")
        c2 = _make_contract(db, v2, "T02")
        voy2 = _make_voyage(
            db, c2, v2, "T02", port="Haldia", berth="Berth 1",
            berthed_at=_utc(2025, 9, 11, 10), completed_at=_utc(2025, 9, 13, 14)
        )

        # Voyage 3 at Anchorage (no berth)
        v3 = _make_vessel(db, "T03")
        c3 = _make_contract(db, v3, "T03")
        _make_voyage(
            db, c3, v3, "T03", port="Haldia", berth=None, status="ARRIVED",
            ata=_utc(2025, 9, 12, 8)
        )

        report = get_port_berth_timeline("Haldia")

        assert "Berth 1" in report.berths
        schedule = report.berths["Berth 1"]
        assert len(schedule) == 2

        # Both schedule items should flag each other
        item1 = next(item for item in schedule if item.voyage_reference == voy1.voyage_reference)
        item2 = next(item for item in schedule if item.voyage_reference == voy2.voyage_reference)
        assert item1.has_conflict is True
        assert voy2.voyage_reference in item1.conflicts_with
        assert item2.has_conflict is True
        assert voy1.voyage_reference in item2.conflicts_with

        assert len(report.anchorage_queue) == 1
        assert report.anchorage_queue[0].vessel_name == v3.vessel_name
        assert report.total_conflicts_detected == 1

    def test_assign_berth_to_voyage_clear_berth(self, db):
        from apps.voyages.port_timeline import assign_berth_to_voyage

        v = _make_vessel(db, "A01")
        c = _make_contract(db, v, "A01")
        voyage = _make_voyage(db, c, v, "A01", port="Haldia", berth=None, status="ARRIVED")

        updated_voyage, report = assign_berth_to_voyage(voyage, "Berth 2")
        assert updated_voyage.discharge_berth == "Berth 2"
        assert report is not None
        assert report.has_conflict is False

    def test_assign_berth_to_voyage_conflict_without_force_raises(self, db):
        from apps.voyages.port_timeline import assign_berth_to_voyage, BerthAllocationError

        # Occupant of Berth 2 until Sep 15
        v1 = _make_vessel(db, "A02")
        c1 = _make_contract(db, v1, "A02")
        _make_voyage(
            db, c1, v1, "A02", port="Haldia", berth="Berth 2",
            berthed_at=_utc(2025, 9, 10, 8), completed_at=_utc(2025, 9, 15, 12)
        )

        # Incoming vessel arriving Sep 12 (overlaps)
        v2 = _make_vessel(db, "A03")
        c2 = _make_contract(db, v2, "A03")
        voyage2 = _make_voyage(
            db, c2, v2, "A03", port="Haldia", berth=None, status="IN_TRANSIT",
            eta=_utc(2025, 9, 12, 0)
        )

        with pytest.raises(BerthAllocationError) as exc_info:
            assign_berth_to_voyage(voyage2, "Berth 2", force=False)

        assert exc_info.value.conflict_report.has_conflict is True
        assert exc_info.value.conflict_report.conflict_count == 1

    def test_assign_berth_to_voyage_conflict_with_force_succeeds(self, db):
        from apps.voyages.port_timeline import assign_berth_to_voyage

        # Occupant of Berth 2
        v1 = _make_vessel(db, "A04")
        c1 = _make_contract(db, v1, "A04")
        _make_voyage(
            db, c1, v1, "A04", port="Haldia", berth="Berth 2",
            berthed_at=_utc(2025, 9, 10, 8), completed_at=_utc(2025, 9, 15, 12)
        )

        v2 = _make_vessel(db, "A05")
        c2 = _make_contract(db, v2, "A05")
        voyage2 = _make_voyage(
            db, c2, v2, "A05", port="Haldia", berth=None, status="IN_TRANSIT",
            eta=_utc(2025, 9, 12, 0)
        )

        updated_voyage, report = assign_berth_to_voyage(voyage2, "Berth 2", force=True)
        assert updated_voyage.discharge_berth == "Berth 2"
        assert report.has_conflict is True


# ---------------------------------------------------------------------------
# API Integration Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPortTimelineAPI:

    def test_get_berth_timeline_api(self, api_client, db, logistics_user):
        """GET /voyages/berth-timeline/ returns Gantt schedule and KPIs."""
        v = _make_vessel(db, "R01")
        c = _make_contract(db, v, "R01")
        _make_voyage(
            db, c, v, "R01", port="Haldia", berth="Berth 4A",
            berthed_at=_utc(2025, 9, 10, 8), completed_at=_utc(2025, 9, 12, 14)
        )

        api_client.force_authenticate(user=logistics_user)
        res = api_client.get("/api/v1/voyages/berth-timeline/?discharge_port=Haldia")

        assert res.status_code == drf_status.HTTP_200_OK
        data = res.json()
        assert data["discharge_port"] == "Haldia"
        assert "kpis" in data
        assert "berths" in data
        assert "Berth 4A" in data["berths"]
        assert len(data["berths"]["Berth 4A"]) == 1
        item = data["berths"]["Berth 4A"][0]
        assert item["vessel_name"] == v.vessel_name
        assert item["has_conflict"] is False

    def test_get_port_congestion_api_multi_and_single_port(self, api_client, db, logistics_user):
        """GET /voyages/port-congestion/ supports multi-port and single port queries."""
        v = _make_vessel(db, "R02")
        c = _make_contract(db, v, "R02")
        _make_voyage(db, c, v, "R02", port="Paradip", berth="Berth 1", status="ARRIVED", ata=_utc(2025, 9, 10, 8))

        api_client.force_authenticate(user=logistics_user)

        # Single port
        res_single = api_client.get("/api/v1/voyages/port-congestion/?discharge_port=Paradip")
        assert res_single.status_code == drf_status.HTTP_200_OK
        assert res_single.json()["discharge_port"] == "Paradip"
        assert res_single.json()["vessels_at_anchorage_count"] == 1

        # Multi-port overview
        res_multi = api_client.get("/api/v1/voyages/port-congestion/")
        assert res_multi.status_code == drf_status.HTTP_200_OK
        assert isinstance(res_multi.json(), list)
        ports = [p["discharge_port"] for p in res_multi.json()]
        assert "Paradip" in ports

    def test_post_assign_berth_clear_success(self, api_client, db, logistics_user):
        """POST /voyages/{id}/assign-berth/ assigns clear berth."""
        v = _make_vessel(db, "R03")
        c = _make_contract(db, v, "R03")
        voyage = _make_voyage(db, c, v, "R03", port="Haldia", berth=None, status="IN_TRANSIT", eta=_utc(2025, 9, 20, 0))

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/assign-berth/"
        res = api_client.post(url, {"discharge_berth": "Berth 5"}, format="json")

        assert res.status_code == drf_status.HTTP_200_OK
        data = res.json()
        assert data["assigned_berth"] == "Berth 5"
        assert data["conflict_override"] is False

    def test_post_assign_berth_conflict_rejected(self, api_client, db, logistics_user):
        """POST /voyages/{id}/assign-berth/ returns 409 CONFLICT if berth is occupied."""
        # Vessel 1 occupies Berth 3
        v1 = _make_vessel(db, "R04")
        c1 = _make_contract(db, v1, "R04")
        _make_voyage(
            db, c1, v1, "R04", port="Haldia", berth="Berth 3",
            berthed_at=_utc(2025, 9, 10, 8), completed_at=_utc(2025, 9, 15, 12)
        )

        # Vessel 2 arriving during occupancy
        v2 = _make_vessel(db, "R05")
        c2 = _make_contract(db, v2, "R05")
        voyage2 = _make_voyage(
            db, c2, v2, "R05", port="Haldia", berth=None, status="IN_TRANSIT",
            eta=_utc(2025, 9, 12, 0)
        )

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage2.voyage_id}/assign-berth/"
        res = api_client.post(url, {"discharge_berth": "Berth 3", "force": False}, format="json")

        assert res.status_code == drf_status.HTTP_409_CONFLICT
        data = res.json()
        assert data["error"] == "BERTH_CONFLICT"
        assert data["conflicts"]["has_conflict"] is True
        assert data["conflicts"]["conflict_count"] == 1

    def test_post_assign_berth_conflict_with_force_override(self, api_client, db, procurement_user):
        """POST /voyages/{id}/assign-berth/ with force=True succeeds with override flag."""
        v1 = _make_vessel(db, "R06")
        c1 = _make_contract(db, v1, "R06")
        _make_voyage(
            db, c1, v1, "R06", port="Haldia", berth="Berth 3",
            berthed_at=_utc(2025, 9, 10, 8), completed_at=_utc(2025, 9, 15, 12)
        )

        v2 = _make_vessel(db, "R07")
        c2 = _make_contract(db, v2, "R07")
        voyage2 = _make_voyage(
            db, c2, v2, "R07", port="Haldia", berth=None, status="IN_TRANSIT",
            eta=_utc(2025, 9, 12, 0)
        )

        api_client.force_authenticate(user=procurement_user)
        url = f"/api/v1/voyages/{voyage2.voyage_id}/assign-berth/"
        res = api_client.post(url, {"discharge_berth": "Berth 3", "force": True}, format="json")

        assert res.status_code == drf_status.HTTP_200_OK
        data = res.json()
        assert data["assigned_berth"] == "Berth 3"
        assert data["conflict_override"] is True

    def test_rbac_auditor_can_get_timeline_but_cannot_assign_berth(
        self, api_client, db, auditor_user
    ):
        """AUDITOR can read timeline and congestion, but gets 403 on assign-berth."""
        v = _make_vessel(db, "R08")
        c = _make_contract(db, v, "R08")
        voyage = _make_voyage(db, c, v, "R08", port="Haldia", berth=None, status="ARRIVED")

        api_client.force_authenticate(user=auditor_user)

        # GET timeline is permitted
        res_timeline = api_client.get("/api/v1/voyages/berth-timeline/?discharge_port=Haldia")
        assert res_timeline.status_code == drf_status.HTTP_200_OK

        # POST assign-berth is forbidden
        res_assign = api_client.post(
            f"/api/v1/voyages/{voyage.voyage_id}/assign-berth/",
            {"discharge_berth": "Berth 1"},
            format="json",
        )
        assert res_assign.status_code == drf_status.HTTP_403_FORBIDDEN
