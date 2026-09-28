"""
backend/apps/voyages/tests/test_fleet_analytics.py
============================================================
Cycle 2.5.2 — Tests: Demurrage & Despatch Analytics & Fleet Historical Performance Engine

Tests cover (22 total):

Pure Engine Calculations (analytics_engine.py):
  1.  Empty queryset returns clean zeroed snapshot with empty port/cargo breakdowns
  2.  Single voyage without laytime calc computes gross freight and sets status counts
  3.  Single voyage with demurrage adds to total_demurrage_usd and net_laytime_usd
  4.  Single voyage with despatch adds to total_despatch_usd and reduces net_laytime_usd
  5.  Multi-voyage completed vs active count partitioning
  6.  Cargo shortage aggregation across multiple discharging voyages
  7.  Overall outturn efficiency percentage calculation
  8.  Demurrage-to-freight risk ratio calculation ((demurrage / freight) * 100)
  9.  Net voyage P&L calculation (gross freight - net laytime)
  10. Port breakdown: groups multiple voyages at the same port correctly
  11. Port breakdown: computes average port turnaround hours
  12. Port breakdown: segregates distinct ports (Dhamra vs Paradip)
  13. Cargo breakdown: groups by commodity (COKING_COAL vs IRON_ORE_FINES)
  14. Decimal precision and half-up rounding invariance

REST API Integration (GET /api/v1/voyages/analytics/):
  15. GET /analytics/ returns 200 OK with full summary structure
  16. Empty fleet returns 200 OK with 0 totals and empty breakdown arrays
  17. Populated fleet returns exact aggregated totals matching DB
  18. Filter by discharge_port returns only matching port records
  19. Filter by cargo_type returns only matching commodity records
  20. Filter by plant returns only matching charterer plant records
  21. Filter by date_from and date_to restricts by eta_date range
  22. RBAC access: SYSTEM_ADMIN, LOGISTICS_AGENT, FINANCE_OFFICER, AUDITOR, READONLY all have read access
"""

import datetime
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.authentication.roles import UserRole
from apps.contracts.models import Vessel, CharterContract
from apps.voyages.models import VesselVoyage, LaytimeCalculation
from apps.voyages.analytics_engine import compute_fleet_analytics
from apps.laytime.engine.fixture_factory import VoyageStatus, CargoType
from apps.laytime.engine.settlement_engine import LaytimeOutcome

UTC = datetime.timezone.utc


# ─── Fixtures & Helpers ───────────────────────────────────────────────────────


@pytest.fixture
def api_client():
    return APIClient()


def _make_user(role: str, suffix: str = "fa"):
    from django.contrib.auth import get_user_model
    from apps.authentication.models import UserProfile

    User = get_user_model()
    username = f"fa_{role.lower()}_{suffix}"
    email = f"fa_{role.lower()}_{suffix}@test.com"
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": email, "first_name": role, "last_name": suffix},
    )
    user.set_password("TestPassword123!")
    user.save()
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.role = role
    profile.save()
    return user


@pytest.fixture
def base_fixture(db):
    vessel1 = Vessel.objects.create(
        vessel_name="MV Dhamra Star",
        imo_number="9111111",
        flag_state="IN",
        deadweight_tonnage=Decimal("80000.000"),
        gross_tonnage=Decimal("42000.000"),
        net_tonnage=Decimal("24000.000"),
        length_overall_meters=Decimal("225.00"),
        beam_meters=Decimal("32.20"),
        draft_meters=Decimal("14.20"),
        hold_count=7,
        hatch_count=7,
        build_year=2019,
        owner_name="Indian Ocean Lines",
    )
    vessel2 = Vessel.objects.create(
        vessel_name="MV Paradip Pride",
        imo_number="9222222",
        flag_state="SG",
        deadweight_tonnage=Decimal("75000.000"),
        gross_tonnage=Decimal("40000.000"),
        net_tonnage=Decimal("23000.000"),
        length_overall_meters=Decimal("220.00"),
        beam_meters=Decimal("32.00"),
        draft_meters=Decimal("14.00"),
        hold_count=7,
        hatch_count=7,
        build_year=2021,
        owner_name="Singapore Bulk Navigation",
    )

    contract1 = CharterContract.objects.create(
        contract_reference="CP-FA-COAL-01",
        charterer_entity="SAIL",
        charterer_plant="Rourkela Steel Plant",
        vessel=vessel1,
        cargo_type=CargoType.COKING_COAL.value,
        nominated_quantity_mt=Decimal("75000.000"),
        freight_rate_usd_per_mt=Decimal("16.00"),
        load_port="Port Hedland",
        discharge_port="Dhamra",
        discharge_rate_mt_per_day=Decimal("12000.000"),
        laycan_start=datetime.date(2026, 11, 1),
        laycan_end=datetime.date(2026, 11, 10),
        demurrage_rate_usd_per_day=Decimal("24000.00"),
        despatch_rate_usd_per_day=Decimal("12000.00"),
    )

    contract2 = CharterContract.objects.create(
        contract_reference="CP-FA-ORE-02",
        charterer_entity="SAIL",
        charterer_plant="Bhilai Steel Plant",
        vessel=vessel2,
        cargo_type=CargoType.IRON_ORE_FINES.value,
        nominated_quantity_mt=Decimal("70000.000"),
        freight_rate_usd_per_mt=Decimal("14.00"),
        load_port="Dampier",
        discharge_port="Paradip",
        discharge_rate_mt_per_day=Decimal("15000.000"),
        laycan_start=datetime.date(2026, 11, 5),
        laycan_end=datetime.date(2026, 11, 15),
        demurrage_rate_usd_per_day=Decimal("20000.00"),
        despatch_rate_usd_per_day=Decimal("10000.00"),
    )

    return vessel1, vessel2, contract1, contract2


def _make_voyage(
    vessel,
    contract,
    ref="V1",
    status="SCHEDULED",
    port="Dhamra",
    loaded=Decimal("75000.000"),
    discharged=None,
    eta_days=5,
):
    voyage = VesselVoyage(
        voyage_reference=f"VOY-FA-{ref}",
        vessel=vessel,
        contract=contract,
        load_port=contract.load_port,
        discharge_port=port,
        quantity_loaded_mt=loaded,
        quantity_discharged_mt=discharged,
        eta_date=timezone.now() + datetime.timedelta(days=eta_days),
        voyage_status=status,
    )
    voyage.save(skip_clean=True)
    return voyage


# ─── 1. Empty Queryset ────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_compute_fleet_analytics_empty_qs():
    """Empty queryset produces a clean summary with 0 values and empty breakdowns."""
    qs = VesselVoyage.objects.none()
    summary = compute_fleet_analytics(qs)

    assert summary.total_voyages == 0
    assert summary.completed_voyages == 0
    assert summary.active_voyages == 0
    assert summary.total_loaded_mt == Decimal("0.000")
    assert summary.total_discharged_mt == Decimal("0.000")
    assert summary.total_gross_freight_usd == Decimal("0.00")
    assert summary.total_demurrage_usd == Decimal("0.00")
    assert summary.total_despatch_usd == Decimal("0.00")
    assert summary.net_laytime_usd == Decimal("0.00")
    assert summary.demurrage_freight_ratio_pct == Decimal("0.00")
    assert summary.port_breakdown == []
    assert summary.cargo_breakdown == []


# ─── 2. Single Voyage Baseline ────────────────────────────────────────────────


@pytest.mark.django_db
def test_compute_fleet_analytics_single_voyage_baseline(base_fixture):
    """Single voyage without laytime calc calculates freight and active counts accurately."""
    v1, _, c1, _ = base_fixture
    _make_voyage(v1, c1, ref="base", loaded=Decimal("74000.000"), status="SCHEDULED")

    qs = VesselVoyage.objects.all()
    summary = compute_fleet_analytics(qs)

    assert summary.total_voyages == 1
    assert summary.active_voyages == 1
    assert summary.completed_voyages == 0
    assert summary.total_loaded_mt == Decimal("74000.000")
    # 74,000 * $16.00 = $1,184,000.00
    assert summary.total_gross_freight_usd == Decimal("1184000.00")
    assert summary.total_demurrage_usd == Decimal("0.00")
    assert summary.net_voyage_pnl_usd == Decimal("1184000.00")


# ─── 3. Single Voyage with Demurrage ──────────────────────────────────────────


@pytest.mark.django_db
def test_compute_fleet_analytics_with_demurrage(base_fixture):
    """Demurrage from current laytime calc increments total demurrage and net laytime."""
    v1, _, c1, _ = base_fixture
    voy = _make_voyage(v1, c1, ref="dem", loaded=Decimal("75000.000"), status="COMPLETED")

    LaytimeCalculation.objects.create(
        voyage=voy,
        revision=1,
        is_current=True,
        outcome=LaytimeOutcome.DEMURRAGE.value,
        allowed_laytime_hours=Decimal("150.000000"),
        gross_demurrage_usd=Decimal("30000.00"),
        net_settlement_usd=Decimal("30000.00"),
        total_port_hours=Decimal("180.000000"),
    )

    summary = compute_fleet_analytics(VesselVoyage.objects.all())

    # 75,000 * 16 = 1,200,000.00
    assert summary.total_gross_freight_usd == Decimal("1200000.00")
    assert summary.total_demurrage_usd == Decimal("30000.00")
    assert summary.total_despatch_usd == Decimal("0.00")
    assert summary.net_laytime_usd == Decimal("30000.00")
    # P&L = 1,200,000 - 30,000 = 1,170,000.00
    assert summary.net_voyage_pnl_usd == Decimal("1170000.00")


# ─── 4. Single Voyage with Despatch ───────────────────────────────────────────


@pytest.mark.django_db
def test_compute_fleet_analytics_with_despatch(base_fixture):
    """Despatch from current laytime calc increments total despatch and reduces net laytime."""
    v1, _, c1, _ = base_fixture
    voy = _make_voyage(v1, c1, ref="des", loaded=Decimal("75000.000"), status="COMPLETED")

    LaytimeCalculation.objects.create(
        voyage=voy,
        revision=1,
        is_current=True,
        outcome=LaytimeOutcome.DESPATCH.value,
        allowed_laytime_hours=Decimal("150.000000"),
        gross_despatch_usd=Decimal("15000.00"),
        net_settlement_usd=Decimal("-15000.00"),
        total_port_hours=Decimal("120.000000"),
    )

    summary = compute_fleet_analytics(VesselVoyage.objects.all())

    assert summary.total_despatch_usd == Decimal("15000.00")
    assert summary.total_demurrage_usd == Decimal("0.00")
    assert summary.net_laytime_usd == Decimal("-15000.00")
    # P&L = 1,200,000 - (-15,000) = 1,215,000.00
    assert summary.net_voyage_pnl_usd == Decimal("1215000.00")


# ─── 5. Completed vs Active Count Partitioning ────────────────────────────────


@pytest.mark.django_db
def test_compute_fleet_analytics_status_partitioning(base_fixture):
    """Correctly tallies COMPLETED vs active statuses (IN_TRANSIT, ARRIVED, BERTHED)."""
    v1, _, c1, _ = base_fixture
    _make_voyage(v1, c1, ref="s1", status="COMPLETED")
    _make_voyage(v1, c1, ref="s2", status="COMPLETED")
    _make_voyage(v1, c1, ref="s3", status="IN_TRANSIT")
    _make_voyage(v1, c1, ref="s4", status="BERTHED")

    summary = compute_fleet_analytics(VesselVoyage.objects.all())

    assert summary.total_voyages == 4
    assert summary.completed_voyages == 2
    assert summary.active_voyages == 2


# ─── 6. Cargo Shortage Aggregation ────────────────────────────────────────────


@pytest.mark.django_db
def test_compute_fleet_analytics_shortage_aggregation(base_fixture):
    """Aggregates outturn shortage across multiple voyages."""
    v1, _, c1, _ = base_fixture
    # Voyage 1: 75,000 loaded, 74,500 discharged -> 500 shortage
    _make_voyage(v1, c1, ref="sh1", loaded=Decimal("75000.000"), discharged=Decimal("74500.000"))
    # Voyage 2: 70,000 loaded, 69,700 discharged -> 300 shortage
    _make_voyage(v1, c1, ref="sh2", loaded=Decimal("70000.000"), discharged=Decimal("69700.000"))

    summary = compute_fleet_analytics(VesselVoyage.objects.all())

    assert summary.total_loaded_mt == Decimal("145000.000")
    assert summary.total_discharged_mt == Decimal("144200.000")
    assert summary.total_shortage_mt == Decimal("800.000")


# ─── 7. Overall Outturn Efficiency Percentage ─────────────────────────────────


@pytest.mark.django_db
def test_compute_fleet_analytics_outturn_efficiency(base_fixture):
    """Overall outturn efficiency % = (144,200 / 145,000) * 100 = 99.45%."""
    v1, _, c1, _ = base_fixture
    _make_voyage(v1, c1, ref="eff1", loaded=Decimal("75000.000"), discharged=Decimal("74500.000"))
    _make_voyage(v1, c1, ref="eff2", loaded=Decimal("70000.000"), discharged=Decimal("69700.000"))

    summary = compute_fleet_analytics(VesselVoyage.objects.all())
    # 144200 / 145000 * 100 = 99.44827... -> 99.45%
    assert summary.overall_outturn_efficiency_pct == Decimal("99.45")


# ─── 8. Demurrage-to-Freight Risk Ratio ────────────────────────────────────────


@pytest.mark.django_db
def test_compute_fleet_analytics_demurrage_freight_ratio(base_fixture):
    """demurrage_freight_ratio_pct = (demurrage / freight) * 100."""
    v1, _, c1, _ = base_fixture
    voy = _make_voyage(v1, c1, ref="rat", loaded=Decimal("75000.000"))
    # freight = 75,000 * 16 = 1,200,000
    # demurrage = 60,000 -> ratio = 60,000 / 1,200,000 * 100 = 5.00%
    LaytimeCalculation.objects.create(
        voyage=voy,
        revision=1,
        is_current=True,
        outcome=LaytimeOutcome.DEMURRAGE.value,
        allowed_laytime_hours=Decimal("150.000000"),
        gross_demurrage_usd=Decimal("60000.00"),
        net_settlement_usd=Decimal("60000.00"),
    )

    summary = compute_fleet_analytics(VesselVoyage.objects.all())
    assert summary.demurrage_freight_ratio_pct == Decimal("5.00")


# ─── 9. Net Voyage P&L Aggregate ──────────────────────────────────────────────


@pytest.mark.django_db
def test_compute_fleet_analytics_net_voyage_pnl(base_fixture):
    """net_voyage_pnl_usd is gross freight minus net laytime."""
    v1, _, c1, _ = base_fixture
    voy1 = _make_voyage(v1, c1, ref="pnl1", loaded=Decimal("75000.000"))  # $1,200,000
    voy2 = _make_voyage(v1, c1, ref="pnl2", loaded=Decimal("75000.000"))  # $1,200,000

    # voy1 has demurrage $20,000 (cost)
    LaytimeCalculation.objects.create(
        voyage=voy1,
        revision=1,
        is_current=True,
        outcome=LaytimeOutcome.DEMURRAGE.value,
        allowed_laytime_hours=Decimal("150.000000"),
        gross_demurrage_usd=Decimal("20000.00"),
        net_settlement_usd=Decimal("20000.00"),
    )
    # voy2 has despatch $10,000 (credit)
    LaytimeCalculation.objects.create(
        voyage=voy2,
        revision=1,
        is_current=True,
        outcome=LaytimeOutcome.DESPATCH.value,
        allowed_laytime_hours=Decimal("150.000000"),
        gross_despatch_usd=Decimal("10000.00"),
        net_settlement_usd=Decimal("-10000.00"),
    )

    summary = compute_fleet_analytics(VesselVoyage.objects.all())
    # Gross freight = $2,400,000
    # Net laytime = $20,000 - $10,000 = $10,000
    # Net P&L = $2,400,000 - $10,000 = $2,390,000.00
    assert summary.total_gross_freight_usd == Decimal("2400000.00")
    assert summary.net_laytime_usd == Decimal("10000.00")
    assert summary.net_voyage_pnl_usd == Decimal("2390000.00")


# ─── 10. Port Breakdown Aggregation ───────────────────────────────────────────


@pytest.mark.django_db
def test_compute_fleet_analytics_port_breakdown_single_port(base_fixture):
    """Port breakdown groups multiple voyages for Dhamra port accurately."""
    v1, _, c1, _ = base_fixture
    _make_voyage(v1, c1, ref="pt1", port="Dhamra", loaded=Decimal("75000.000"))
    _make_voyage(v1, c1, ref="pt2", port="Dhamra", loaded=Decimal("75000.000"))

    summary = compute_fleet_analytics(VesselVoyage.objects.all())
    assert len(summary.port_breakdown) == 1

    p = summary.port_breakdown[0]
    assert p.port_name == "Dhamra"
    assert p.voyage_count == 2
    assert p.total_loaded_mt == Decimal("150000.000")


# ─── 11. Port Breakdown Average Port Turnaround Hours ─────────────────────────


@pytest.mark.django_db
def test_compute_fleet_analytics_port_average_hours(base_fixture):
    """Computes average port turnaround hours per port."""
    v1, _, c1, _ = base_fixture
    voy1 = _make_voyage(v1, c1, ref="h1", port="Dhamra", loaded=Decimal("75000.000"))
    voy2 = _make_voyage(v1, c1, ref="h2", port="Dhamra", loaded=Decimal("75000.000"))

    # Voy 1 had 100 port hours, Voy 2 had 150 port hours -> avg 125.00
    LaytimeCalculation.objects.create(
        voyage=voy1,
        revision=1,
        is_current=True,
        allowed_laytime_hours=Decimal("150.000000"),
        total_port_hours=Decimal("100.000000"),
    )
    LaytimeCalculation.objects.create(
        voyage=voy2,
        revision=1,
        is_current=True,
        allowed_laytime_hours=Decimal("150.000000"),
        total_port_hours=Decimal("150.000000"),
    )

    summary = compute_fleet_analytics(VesselVoyage.objects.all())
    p = summary.port_breakdown[0]
    assert p.average_port_hours == Decimal("125.00")


# ─── 12. Port Breakdown Distinct Ports ────────────────────────────────────────


@pytest.mark.django_db
def test_compute_fleet_analytics_multiple_ports(base_fixture):
    """Segregates metrics across multiple distinct ports (Dhamra and Paradip)."""
    v1, v2, c1, c2 = base_fixture
    _make_voyage(v1, c1, ref="dham", port="Dhamra", loaded=Decimal("75000.000"))
    _make_voyage(v2, c2, ref="par", port="Paradip", loaded=Decimal("70000.000"))

    summary = compute_fleet_analytics(VesselVoyage.objects.all())
    assert len(summary.port_breakdown) == 2

    port_names = {p.port_name for p in summary.port_breakdown}
    assert port_names == {"Dhamra", "Paradip"}


# ─── 13. Cargo Breakdown Aggregation ──────────────────────────────────────────


@pytest.mark.django_db
def test_compute_fleet_analytics_cargo_breakdown(base_fixture):
    """Aggregates metrics by commodity (COKING_COAL vs IRON_ORE_FINES)."""
    v1, v2, c1, c2 = base_fixture
    _make_voyage(v1, c1, ref="c1", loaded=Decimal("75000.000"))  # COKING_COAL
    _make_voyage(v2, c2, ref="c2", loaded=Decimal("70000.000"))  # IRON_ORE_FINES

    summary = compute_fleet_analytics(VesselVoyage.objects.all())
    assert len(summary.cargo_breakdown) == 2

    cargos = {c.cargo_type: c for c in summary.cargo_breakdown}
    assert CargoType.COKING_COAL.value in cargos
    assert CargoType.IRON_ORE_FINES.value in cargos

    assert cargos[CargoType.COKING_COAL.value].total_loaded_mt == Decimal("75000.000")
    assert cargos[CargoType.IRON_ORE_FINES.value].total_loaded_mt == Decimal("70000.000")


# ─── 14. Decimal Precision and Rounding Invariance ────────────────────────────


@pytest.mark.django_db
def test_compute_fleet_analytics_decimal_precision(base_fixture):
    """All numerical results conform to standard decimal precision without float rounding errors."""
    v1, _, c1, _ = base_fixture
    _make_voyage(v1, c1, ref="prec", loaded=Decimal("74321.125"), discharged=Decimal("74111.555"))

    summary = compute_fleet_analytics(VesselVoyage.objects.all())

    # Shortage = 74321.125 - 74111.555 = 209.570 MT
    assert summary.total_shortage_mt == Decimal("209.570")
    assert isinstance(summary.total_gross_freight_usd, Decimal)
    assert isinstance(summary.net_voyage_pnl_usd, Decimal)


# ─── 15. REST API: GET /analytics/ 200 OK ─────────────────────────────────────


@pytest.mark.django_db
def test_api_get_analytics_success_200(base_fixture, api_client):
    """GET /api/v1/voyages/analytics/ returns 200 OK with expected schema keys."""
    v1, _, c1, _ = base_fixture
    _make_voyage(v1, c1, ref="api_ok")

    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="ok")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    response = api_client.get("/api/v1/voyages/analytics/")
    assert response.status_code == drf_status.HTTP_200_OK

    data = response.json()
    assert "total_voyages" in data
    assert "completed_voyages" in data
    assert "active_voyages" in data
    assert "total_loaded_mt" in data
    assert "total_gross_freight_usd" in data
    assert "port_breakdown" in data
    assert "cargo_breakdown" in data


# ─── 16. REST API: Empty Fleet Returns 200 OK ─────────────────────────────────


@pytest.mark.django_db
def test_api_get_analytics_empty_fleet(api_client):
    """GET /analytics/ on empty database returns 200 OK with zero metrics."""
    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="empty")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    response = api_client.get("/api/v1/voyages/analytics/")
    assert response.status_code == drf_status.HTTP_200_OK

    data = response.json()
    assert data["total_voyages"] == 0
    assert data["port_breakdown"] == []
    assert data["cargo_breakdown"] == []


# ─── 17. REST API: Populated Fleet Field Accuracy ─────────────────────────────


@pytest.mark.django_db
def test_api_get_analytics_field_accuracy(base_fixture, api_client):
    """Values in REST API response accurately represent underlying database entities."""
    v1, _, c1, _ = base_fixture
    voy = _make_voyage(v1, c1, ref="facc", loaded=Decimal("75000.000"), status="COMPLETED")

    LaytimeCalculation.objects.create(
        voyage=voy,
        revision=1,
        is_current=True,
        outcome=LaytimeOutcome.DEMURRAGE.value,
        allowed_laytime_hours=Decimal("150.000000"),
        gross_demurrage_usd=Decimal("36000.00"),
        net_settlement_usd=Decimal("36000.00"),
    )

    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="facc")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    response = api_client.get("/api/v1/voyages/analytics/")
    assert response.status_code == drf_status.HTTP_200_OK

    data = response.json()
    assert data["total_voyages"] == 1
    assert data["completed_voyages"] == 1
    assert data["total_loaded_mt"] == "75000.000"
    assert data["total_gross_freight_usd"] == "1200000.00"
    assert data["total_demurrage_usd"] == "36000.00"
    assert data["net_voyage_pnl_usd"] == "1164000.00"


# ─── 18. REST API: Filter by discharge_port ───────────────────────────────────


@pytest.mark.django_db
def test_api_get_analytics_filter_by_port(base_fixture, api_client):
    """Query parameter discharge_port restricts analytics to the specified port."""
    v1, v2, c1, c2 = base_fixture
    _make_voyage(v1, c1, ref="f_dham", port="Dhamra")
    _make_voyage(v2, c2, ref="f_par", port="Paradip")

    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="port_f")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    response = api_client.get("/api/v1/voyages/analytics/?discharge_port=Dhamra")
    assert response.status_code == drf_status.HTTP_200_OK

    data = response.json()
    assert data["total_voyages"] == 1
    assert len(data["port_breakdown"]) == 1
    assert data["port_breakdown"][0]["port_name"] == "Dhamra"


# ─── 19. REST API: Filter by cargo_type ───────────────────────────────────────


@pytest.mark.django_db
def test_api_get_analytics_filter_by_cargo(base_fixture, api_client):
    """Query parameter cargo_type restricts analytics to the specified commodity."""
    v1, v2, c1, c2 = base_fixture
    _make_voyage(v1, c1, ref="f_coal")  # COKING_COAL
    _make_voyage(v2, c2, ref="f_ore")   # IRON_ORE_FINES

    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="cargo_f")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    response = api_client.get(f"/api/v1/voyages/analytics/?cargo_type={CargoType.COKING_COAL.value}")
    assert response.status_code == drf_status.HTTP_200_OK

    data = response.json()
    assert data["total_voyages"] == 1
    assert len(data["cargo_breakdown"]) == 1
    assert data["cargo_breakdown"][0]["cargo_type"] == CargoType.COKING_COAL.value


# ─── 20. REST API: Filter by plant ────────────────────────────────────────────


@pytest.mark.django_db
def test_api_get_analytics_filter_by_plant(base_fixture, api_client):
    """Query parameter plant restricts analytics to the specified charterer plant."""
    v1, v2, c1, c2 = base_fixture
    _make_voyage(v1, c1, ref="f_rourk")  # Rourkela
    _make_voyage(v2, c2, ref="f_bhil")   # Bhilai

    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="plant_f")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    response = api_client.get("/api/v1/voyages/analytics/?plant=Rourkela")
    assert response.status_code == drf_status.HTTP_200_OK

    data = response.json()
    assert data["total_voyages"] == 1


# ─── 21. REST API: Filter by date range ───────────────────────────────────────


@pytest.mark.django_db
def test_api_get_analytics_filter_by_date_range(base_fixture, api_client):
    """date_from and date_to parameters filter voyages by eta_date."""
    v1, _, c1, _ = base_fixture
    # Voyage in 2026-11
    _make_voyage(v1, c1, ref="d1", eta_days=10)
    # Voyage in 2026-12 (approx +45 days)
    _make_voyage(v1, c1, ref="d2", eta_days=45)

    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="date_f")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    # Filter with a narrow window around voyage 1
    now_date = timezone.now().date()
    d_from = (now_date + datetime.timedelta(days=5)).isoformat()
    d_to = (now_date + datetime.timedelta(days=20)).isoformat()

    response = api_client.get(f"/api/v1/voyages/analytics/?date_from={d_from}&date_to={d_to}")
    assert response.status_code == drf_status.HTTP_200_OK

    data = response.json()
    assert data["total_voyages"] == 1


# ─── 22. REST API: RBAC Permissions for Multiple Roles ────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize(
    "role",
    [
        UserRole.SYSTEM_ADMIN.value,
        UserRole.LOGISTICS_AGENT.value,
        UserRole.FINANCE_OFFICER.value,
        UserRole.AUDITOR.value,
        UserRole.READONLY.value,
    ],
)
def test_api_get_analytics_rbac_roles_allowed(base_fixture, api_client, role):
    """All organizational roles have visibility into high-level fleet analytics."""
    v1, _, c1, _ = base_fixture
    _make_voyage(v1, c1, ref=f"rbac_{role.lower()}")

    user = _make_user(role, suffix=f"ar_{role.lower()}")
    api_client.force_authenticate(user=user)
    api_client.credentials(HTTP_X_USER_ROLE=role)

    response = api_client.get("/api/v1/voyages/analytics/")
    assert response.status_code == drf_status.HTTP_200_OK
    assert response.json()["total_voyages"] >= 1
