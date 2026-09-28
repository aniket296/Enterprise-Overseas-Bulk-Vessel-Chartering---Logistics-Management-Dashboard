"""
backend/apps/contracts/tests/test_carrier_scorecard.py
============================================================
Cycle 2.5.3 — Tests: Charter Contract Fulfillment & Carrier Reliability Scorecard Engine

Tests cover (22 total):

Contract Performance Engine (compute_contract_performance):
  1.  Contract with zero voyages: PENDING status, 0% fulfillment, remaining = min commitment
  2.  Tolerance band calculation (75,000 MT ± 5% -> min 71,250 MT, max 78,750 MT)
  3.  Compliant lifted quantity: min <= lifted <= max -> COMPLIANT status
  4.  Under-fulfilled contract: lifted < min -> UNDER_FULFILLED status
  5.  Over-fulfilled contract: lifted > max -> OVER_FULFILLED status
  6.  Freight spend calculation: actual_lifted * freight_rate_usd_per_mt
  7.  Laytime financials aggregation: demurrage paid and despatch earned from current calcs
  8.  Laycan adherence rate (% of voyages arriving on/before laycan_end)
  9.  Cargo outturn shortage calculation across contract voyages

Carrier Scorecard Engine (compute_carrier_scorecards):
  10. Single carrier single voyage metrics (tonnage, freight, count)
  11. Demurrage incidence rate calculation (% of voyages with demurrage > 0)
  12. Laycan adherence rate calculation across carrier fleet
  13. Average transit cargo shortage percentage
  14. Composite Carrier Reliability Score index (0-100)
  15. Multi-carrier ranking sorted by composite reliability score descending
  16. Unique fixture contracts count per carrier

REST API Endpoints:
  17. GET /api/v1/contracts/{id}/performance/ returns 200 OK with complete schema
  18. GET /api/v1/contracts/{id}/performance/ field accuracy verification
  19. GET /api/v1/contracts/{id}/performance/ with unknown UUID returns 404 NOT FOUND
  20. GET /api/v1/contracts/carrier-scorecard/ returns 200 OK with ranked array
  21. GET /api/v1/contracts/carrier-scorecard/ filters by ?owner= and ?min_voyages=
  22. Multi-role RBAC access: SYSTEM_ADMIN, PROCUREMENT_OFFICER, LOGISTICS_AGENT, FINANCE_OFFICER, AUDITOR, READONLY
"""

import datetime
import uuid
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.authentication.roles import UserRole
from apps.contracts.models import Vessel, CharterContract
from apps.voyages.models import VesselVoyage, LaytimeCalculation
from apps.contracts.scorecard_engine import (
    compute_contract_performance,
    compute_carrier_scorecards,
)
from apps.laytime.engine.fixture_factory import VoyageStatus, CargoType
from apps.laytime.engine.settlement_engine import LaytimeOutcome

UTC = datetime.timezone.utc


# ─── Fixtures & Helpers ───────────────────────────────────────────────────────


@pytest.fixture
def api_client():
    return APIClient()


def _make_user(role: str, suffix: str = "cs"):
    from django.contrib.auth import get_user_model
    from apps.authentication.models import UserProfile

    User = get_user_model()
    username = f"cs_{role.lower()}_{suffix}"
    email = f"cs_{role.lower()}_{suffix}@test.com"
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
def base_carrier_fixture(db):
    vessel1 = Vessel.objects.create(
        vessel_name="MV Bharat Pioneer",
        imo_number="9871111",
        flag_state="IN",
        deadweight_tonnage=Decimal("82000.000"),
        gross_tonnage=Decimal("43000.000"),
        net_tonnage=Decimal("25000.000"),
        length_overall_meters=Decimal("229.00"),
        beam_meters=Decimal("32.26"),
        draft_meters=Decimal("14.50"),
        hold_count=7,
        hatch_count=7,
        build_year=2020,
        owner_name="Shipping Corporation of India Ltd",
    )
    vessel2 = Vessel.objects.create(
        vessel_name="MV Global Carrier",
        imo_number="9872222",
        flag_state="SG",
        deadweight_tonnage=Decimal("76000.000"),
        gross_tonnage=Decimal("41000.000"),
        net_tonnage=Decimal("24000.000"),
        length_overall_meters=Decimal("225.00"),
        beam_meters=Decimal("32.20"),
        draft_meters=Decimal("14.20"),
        hold_count=7,
        hatch_count=7,
        build_year=2018,
        owner_name="Oldendorff Carriers GmbH",
    )

    contract1 = CharterContract.objects.create(
        contract_reference="CP-CS-001",
        charterer_entity="SAIL",
        charterer_plant="Bhilai Steel Plant",
        vessel=vessel1,
        cargo_type=CargoType.COKING_COAL.value,
        nominated_quantity_mt=Decimal("75000.000"),
        tolerance_pct=Decimal("5.00"),
        freight_rate_usd_per_mt=Decimal("16.00"),
        load_port="Port Hedland",
        discharge_port="Dhamra",
        discharge_rate_mt_per_day=Decimal("12500.000"),
        laycan_start=datetime.date(2026, 11, 1),
        laycan_end=datetime.date(2026, 11, 10),
        demurrage_rate_usd_per_day=Decimal("24000.00"),
        despatch_rate_usd_per_day=Decimal("12000.00"),
    )

    contract2 = CharterContract.objects.create(
        contract_reference="CP-CS-002",
        charterer_entity="SAIL",
        charterer_plant="Rourkela Steel Plant",
        vessel=vessel2,
        cargo_type=CargoType.IRON_ORE_FINES.value,
        nominated_quantity_mt=Decimal("70000.000"),
        tolerance_pct=Decimal("5.00"),
        freight_rate_usd_per_mt=Decimal("14.50"),
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
    loaded=Decimal("75000.000"),
    discharged=None,
    ata_date=None,
    status="COMPLETED",
):
    voy = VesselVoyage(
        voyage_reference=f"VOY-CS-{ref}",
        vessel=vessel,
        contract=contract,
        load_port=contract.load_port,
        discharge_port=contract.discharge_port,
        quantity_loaded_mt=loaded,
        quantity_discharged_mt=discharged,
        eta_date=timezone.now() + datetime.timedelta(days=5),
        ata_date=ata_date,
        voyage_status=status,
    )
    voy.save(skip_clean=True)
    return voy


# ─── 1. Contract Performance: Zero Voyages ───────────────────────────────────


@pytest.mark.django_db
def test_contract_performance_zero_voyages(base_carrier_fixture):
    """Contract without executed voyages returns PENDING status and full commitment."""
    _, _, c1, _ = base_carrier_fixture
    perf = compute_contract_performance(c1)

    assert perf.contract_reference == "CP-CS-001"
    assert perf.total_voyages_count == 0
    assert perf.actual_lifted_mt == Decimal("0.000")
    assert perf.fulfillment_pct == Decimal("0.00")
    assert perf.fulfillment_status == "PENDING"
    # min_commitment = 75,000 * 0.95 = 71,250 MT
    assert perf.remaining_commitment_mt == Decimal("71250.000")


# ─── 2. Contract Performance: Tolerance Band Calculation ─────────────────────


@pytest.mark.django_db
def test_contract_performance_tolerance_bands(base_carrier_fixture):
    """Tolerance band correctly calculates min and max commitment thresholds."""
    _, _, c1, _ = base_carrier_fixture
    perf = compute_contract_performance(c1)

    assert perf.min_commitment_mt == Decimal("71250.000")
    assert perf.max_commitment_mt == Decimal("78750.000")


# ─── 3. Contract Performance: Compliant Fulfillment ──────────────────────────


@pytest.mark.django_db
def test_contract_performance_compliant(base_carrier_fixture):
    """Lifted volume between min and max commitments marks contract COMPLIANT."""
    v1, _, c1, _ = base_carrier_fixture
    _make_voyage(v1, c1, ref="comp", loaded=Decimal("74000.000"))

    perf = compute_contract_performance(c1)

    assert perf.actual_lifted_mt == Decimal("74000.000")
    assert perf.fulfillment_status == "COMPLIANT"
    assert perf.remaining_commitment_mt == Decimal("0.000")
    # 74,000 / 75,000 * 100 = 98.67%
    assert perf.fulfillment_pct == Decimal("98.67")


# ─── 4. Contract Performance: Under-Fulfilled ─────────────────────────────────


@pytest.mark.django_db
def test_contract_performance_under_fulfilled(base_carrier_fixture):
    """Lifted volume below min commitment marks contract UNDER_FULFILLED."""
    v1, _, c1, _ = base_carrier_fixture
    _make_voyage(v1, c1, ref="under", loaded=Decimal("65000.000"))

    perf = compute_contract_performance(c1)

    assert perf.actual_lifted_mt == Decimal("65000.000")
    assert perf.fulfillment_status == "UNDER_FULFILLED"
    # Remaining = 71,250 - 65,000 = 6,250 MT
    assert perf.remaining_commitment_mt == Decimal("6250.000")


# ─── 5. Contract Performance: Over-Fulfilled ──────────────────────────────────


@pytest.mark.django_db
def test_contract_performance_over_fulfilled(base_carrier_fixture):
    """Lifted volume above max commitment marks contract OVER_FULFILLED."""
    v1, _, c1, _ = base_carrier_fixture
    _make_voyage(v1, c1, ref="over", loaded=Decimal("80000.000"))

    perf = compute_contract_performance(c1)

    assert perf.actual_lifted_mt == Decimal("80000.000")
    assert perf.fulfillment_status == "OVER_FULFILLED"
    assert perf.remaining_commitment_mt == Decimal("0.000")


# ─── 6. Contract Performance: Freight Spend Calculation ───────────────────────


@pytest.mark.django_db
def test_contract_performance_freight_paid(base_carrier_fixture):
    """Total freight paid equals actual lifted MT multiplied by contract freight rate."""
    v1, _, c1, _ = base_carrier_fixture
    # 75,000 MT * $16.00/MT = $1,200,000.00
    _make_voyage(v1, c1, ref="frt", loaded=Decimal("75000.000"))

    perf = compute_contract_performance(c1)
    assert perf.total_freight_paid_usd == Decimal("1200000.00")


# ─── 7. Contract Performance: Laytime Financials ──────────────────────────────


@pytest.mark.django_db
def test_contract_performance_laytime_financials(base_carrier_fixture):
    """Demurrage and despatch settle across contract voyages."""
    v1, _, c1, _ = base_carrier_fixture
    voy = _make_voyage(v1, c1, ref="lay", loaded=Decimal("75000.000"))

    LaytimeCalculation.objects.create(
        voyage=voy,
        revision=1,
        is_current=True,
        outcome=LaytimeOutcome.DEMURRAGE.value,
        allowed_laytime_hours=Decimal("150.000000"),
        gross_demurrage_usd=Decimal("28000.00"),
        net_settlement_usd=Decimal("28000.00"),
    )

    perf = compute_contract_performance(c1)
    assert perf.total_demurrage_paid_usd == Decimal("28000.00")
    assert perf.net_laytime_impact_usd == Decimal("28000.00")


# ─── 8. Contract Performance: Laycan Adherence ────────────────────────────────


@pytest.mark.django_db
def test_contract_performance_laycan_adherence(base_carrier_fixture):
    """Laycan adherence % correctly detects whether ATA is within the laycan window."""
    v1, _, c1, _ = base_carrier_fixture

    # c1 laycan_end is 2026-11-10
    # Voyage 1: on time (ATA 2026-11-08)
    _make_voyage(v1, c1, ref="lc1", ata_date=datetime.datetime(2026, 11, 8, 10, 0, tzinfo=UTC))
    # Voyage 2: late (ATA 2026-11-14)
    _make_voyage(v1, c1, ref="lc2", ata_date=datetime.datetime(2026, 11, 14, 15, 0, tzinfo=UTC))

    perf = compute_contract_performance(c1)
    # 1 out of 2 on time = 50.00%
    assert perf.laycan_adherence_rate_pct == Decimal("50.00")


# ─── 9. Contract Performance: Cargo Shortage ──────────────────────────────────


@pytest.mark.django_db
def test_contract_performance_shortage(base_carrier_fixture):
    """Cargo shortage MT across contract voyages accumulates correctly."""
    v1, _, c1, _ = base_carrier_fixture
    _make_voyage(v1, c1, ref="sh1", loaded=Decimal("75000.000"), discharged=Decimal("74600.000"))

    perf = compute_contract_performance(c1)
    assert perf.actual_delivered_mt == Decimal("74600.000")
    assert perf.total_shortage_mt == Decimal("400.000")


# ─── 10. Carrier Scorecard: Single Carrier Baseline ───────────────────────────


@pytest.mark.django_db
def test_carrier_scorecard_single_carrier(base_carrier_fixture):
    """Carrier scorecard computes volume, freight, and voyage counts for shipowner."""
    v1, _, c1, _ = base_carrier_fixture
    _make_voyage(v1, c1, ref="sc1", loaded=Decimal("75000.000"))

    cards = compute_carrier_scorecards()
    sci_card = next(c for c in cards if "Shipping Corporation of India" in c.owner_name)

    assert sci_card.total_voyages == 1
    assert sci_card.total_carried_mt == Decimal("75000.000")
    assert sci_card.total_freight_usd == Decimal("1200000.00")


# ─── 11. Carrier Scorecard: Demurrage Incidence Rate ──────────────────────────


@pytest.mark.django_db
def test_carrier_scorecard_demurrage_incidence(base_carrier_fixture):
    """demurrage_incidence_pct = (voyages with demurrage / total voyages) * 100."""
    v1, _, c1, _ = base_carrier_fixture
    voy1 = _make_voyage(v1, c1, ref="di1", loaded=Decimal("75000.000"))
    _make_voyage(v1, c1, ref="di2", loaded=Decimal("75000.000"))

    # Only voy1 triggered demurrage
    LaytimeCalculation.objects.create(
        voyage=voy1,
        revision=1,
        is_current=True,
        outcome=LaytimeOutcome.DEMURRAGE.value,
        allowed_laytime_hours=Decimal("150.000000"),
        gross_demurrage_usd=Decimal("20000.00"),
        net_settlement_usd=Decimal("20000.00"),
    )

    cards = compute_carrier_scorecards()
    sci_card = next(c for c in cards if "Shipping Corporation of India" in c.owner_name)

    # 1 out of 2 voyages incurred demurrage = 50.00%
    assert sci_card.demurrage_incidence_pct == Decimal("50.00")


# ─── 12. Carrier Scorecard: Laycan Adherence Rate ─────────────────────────────


@pytest.mark.django_db
def test_carrier_scorecard_laycan_adherence(base_carrier_fixture):
    """laycan_adherence_pct reflects fleet arrival punctuality against contractual laycans."""
    v1, _, c1, _ = base_carrier_fixture
    # laycan_end = 2026-11-10
    _make_voyage(v1, c1, ref="la1", ata_date=datetime.datetime(2026, 11, 5, tzinfo=UTC))
    _make_voyage(v1, c1, ref="la2", ata_date=datetime.datetime(2026, 11, 9, tzinfo=UTC))

    cards = compute_carrier_scorecards()
    sci_card = next(c for c in cards if "Shipping Corporation of India" in c.owner_name)

    # Both on time = 100.00%
    assert sci_card.laycan_adherence_pct == Decimal("100.00")


# ─── 13. Carrier Scorecard: Average Shortage Percentage ───────────────────────


@pytest.mark.django_db
def test_carrier_scorecard_average_shortage(base_carrier_fixture):
    """average_shortage_pct averages the percentage cargo loss across discharged voyages."""
    v1, _, c1, _ = base_carrier_fixture
    # 75,000 loaded, 74,250 discharged -> 750 shortage (1.00%)
    _make_voyage(v1, c1, ref="as1", loaded=Decimal("75000.000"), discharged=Decimal("74250.000"))

    cards = compute_carrier_scorecards()
    sci_card = next(c for c in cards if "Shipping Corporation of India" in c.owner_name)

    assert sci_card.average_shortage_pct == Decimal("1.00")


# ─── 14. Carrier Scorecard: Composite Reliability Rating ──────────────────────


@pytest.mark.django_db
def test_carrier_scorecard_composite_score(base_carrier_fixture):
    """Carrier reliability score produces a normalized 0-100 index based on performance."""
    v1, _, c1, _ = base_carrier_fixture
    # Perfect voyage: on time (100% laycan), zero demurrage (0% dem), zero shortage (0% short)
    # Score: (100 * 0.40) + (100 * 0.35) + (100 * 0.25) = 40 + 35 + 25 = 100.00
    _make_voyage(
        v1, c1, ref="perf",
        loaded=Decimal("75000.000"),
        discharged=Decimal("75000.000"),
        ata_date=datetime.datetime(2026, 11, 5, tzinfo=UTC),
    )

    cards = compute_carrier_scorecards()
    sci_card = next(c for c in cards if "Shipping Corporation of India" in c.owner_name)

    assert sci_card.carrier_reliability_score == Decimal("100.00")


# ─── 15. Carrier Scorecard: Multi-Carrier Ranking ─────────────────────────────


@pytest.mark.django_db
def test_carrier_scorecard_multi_carrier_ranking(base_carrier_fixture):
    """Returns carriers ranked in descending order of their composite reliability score."""
    v1, v2, c1, c2 = base_carrier_fixture

    # Carrier 1 (SCI): perfect execution -> score 100
    _make_voyage(
        v1, c1, ref="sci",
        loaded=Decimal("75000.000"),
        discharged=Decimal("75000.000"),
        ata_date=datetime.datetime(2026, 11, 5, tzinfo=UTC),
    )

    # Carrier 2 (Oldendorff): late arrival (laycan_end was 2026-11-15, arrived 2026-11-20) + demurrage
    voy2 = _make_voyage(
        v2, c2, ref="old",
        loaded=Decimal("70000.000"),
        discharged=Decimal("69000.000"),
        ata_date=datetime.datetime(2026, 11, 20, tzinfo=UTC),
    )
    LaytimeCalculation.objects.create(
        voyage=voy2,
        revision=1,
        is_current=True,
        outcome=LaytimeOutcome.DEMURRAGE.value,
        allowed_laytime_hours=Decimal("150.000000"),
        gross_demurrage_usd=Decimal("50000.00"),
        net_settlement_usd=Decimal("50000.00"),
    )

    cards = compute_carrier_scorecards()
    assert len(cards) >= 2
    # First should have higher score than second
    assert cards[0].carrier_reliability_score >= cards[1].carrier_reliability_score
    assert cards[0].owner_name == "Shipping Corporation of India Ltd"


# ─── 16. Carrier Scorecard: Fixture Count ─────────────────────────────────────


@pytest.mark.django_db
def test_carrier_scorecard_fixture_count(base_carrier_fixture):
    """Correctly tallies unique charter fixtures associated with carrier."""
    v1, _, c1, _ = base_carrier_fixture
    _make_voyage(v1, c1, ref="fix1")
    _make_voyage(v1, c1, ref="fix2")

    cards = compute_carrier_scorecards()
    sci_card = next(c for c in cards if "Shipping Corporation of India" in c.owner_name)
    assert sci_card.total_fixtures == 1
    assert sci_card.total_voyages == 2


# ─── 17. REST API: GET /contracts/{id}/performance/ 200 OK ───────────────────


@pytest.mark.django_db
def test_api_get_contract_performance_success_200(base_carrier_fixture, api_client):
    """GET /api/v1/contracts/{id}/performance/ returns 200 OK with expected top-level keys."""
    _, _, c1, _ = base_carrier_fixture

    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="p200")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    url = f"/api/v1/contracts/{c1.contract_id}/performance/"
    response = api_client.get(url)

    assert response.status_code == drf_status.HTTP_200_OK
    data = response.json()
    assert data["contract_id"] == str(c1.contract_id)
    assert data["contract_reference"] == c1.contract_reference
    assert "nominated_quantity_mt" in data
    assert "fulfillment_status" in data
    assert "remaining_commitment_mt" in data


# ─── 18. REST API: Contract Performance Field Accuracy ────────────────────────


@pytest.mark.django_db
def test_api_get_contract_performance_field_accuracy(base_carrier_fixture, api_client):
    """Serialized JSON response fields accurately match contract performance values."""
    v1, _, c1, _ = base_carrier_fixture
    _make_voyage(v1, c1, ref="acc", loaded=Decimal("75000.000"), discharged=Decimal("74800.000"))

    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="pacc")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    url = f"/api/v1/contracts/{c1.contract_id}/performance/"
    response = api_client.get(url)
    assert response.status_code == drf_status.HTTP_200_OK

    data = response.json()
    assert data["actual_lifted_mt"] == "75000.000"
    assert data["actual_delivered_mt"] == "74800.000"
    assert data["total_shortage_mt"] == "200.000"
    assert data["fulfillment_status"] == "COMPLIANT"
    assert data["total_freight_paid_usd"] == "1200000.00"


# ─── 19. REST API: Performance 404 on Unknown Contract ────────────────────────


@pytest.mark.django_db
def test_api_get_contract_performance_not_found_404(api_client):
    """GET /performance/ on non-existent contract UUID returns 404 NOT FOUND."""
    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="p404")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    fake_id = uuid.uuid4()
    url = f"/api/v1/contracts/{fake_id}/performance/"
    response = api_client.get(url)

    assert response.status_code == drf_status.HTTP_404_NOT_FOUND


# ─── 20. REST API: GET /contracts/carrier-scorecard/ 200 OK ───────────────────


@pytest.mark.django_db
def test_api_get_carrier_scorecard_success_200(base_carrier_fixture, api_client):
    """GET /api/v1/contracts/carrier-scorecard/ returns 200 OK with list of carrier records."""
    v1, _, c1, _ = base_carrier_fixture
    _make_voyage(v1, c1, ref="card_api")

    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="card200")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    url = "/api/v1/contracts/carrier-scorecard/"
    response = api_client.get(url)

    assert response.status_code == drf_status.HTTP_200_OK
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "owner_name" in data[0]
    assert "carrier_reliability_score" in data[0]


# ─── 21. REST API: Carrier Scorecard Query Filtering ──────────────────────────


@pytest.mark.django_db
def test_api_get_carrier_scorecard_filtering(base_carrier_fixture, api_client):
    """Filters carrier scorecards by ?owner= and ?min_voyages= parameters."""
    v1, v2, c1, c2 = base_carrier_fixture
    # SCI has 2 voyages
    _make_voyage(v1, c1, ref="sci1")
    _make_voyage(v1, c1, ref="sci2")
    # Oldendorff has 1 voyage
    _make_voyage(v2, c2, ref="old1")

    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="card_filt")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    # 1. Filter by owner
    resp_owner = api_client.get("/api/v1/contracts/carrier-scorecard/?owner=Oldendorff")
    assert resp_owner.status_code == drf_status.HTTP_200_OK
    assert len(resp_owner.json()) == 1
    assert "Oldendorff" in resp_owner.json()[0]["owner_name"]

    # 2. Filter by min_voyages=2
    resp_min = api_client.get("/api/v1/contracts/carrier-scorecard/?min_voyages=2")
    assert resp_min.status_code == drf_status.HTTP_200_OK
    for c in resp_min.json():
        assert c["total_voyages"] >= 2


# ─── 22. REST API: Multi-Role RBAC Authorization ──────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize(
    "role",
    [
        UserRole.SYSTEM_ADMIN.value,
        UserRole.PROCUREMENT_OFFICER.value,
        UserRole.LOGISTICS_AGENT.value,
        UserRole.FINANCE_OFFICER.value,
        UserRole.AUDITOR.value,
        UserRole.READONLY.value,
    ],
)
def test_api_get_scorecards_rbac_roles_allowed(base_carrier_fixture, api_client, role):
    """All organizational roles have visibility into contract performance and carrier scorecards."""
    _, _, c1, _ = base_carrier_fixture

    user = _make_user(role, suffix=f"scr_{role.lower()}")
    api_client.force_authenticate(user=user)
    api_client.credentials(HTTP_X_USER_ROLE=role)

    # Performance
    resp_perf = api_client.get(f"/api/v1/contracts/{c1.contract_id}/performance/")
    assert resp_perf.status_code == drf_status.HTTP_200_OK

    # Carrier Scorecard
    resp_card = api_client.get("/api/v1/contracts/carrier-scorecard/")
    assert resp_card.status_code == drf_status.HTTP_200_OK
