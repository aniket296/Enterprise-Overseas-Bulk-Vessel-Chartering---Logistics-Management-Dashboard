"""
backend/apps/voyages/tests/test_voyage_pnl.py
============================================================
Cycle 2.5.1 — Tests: Freight Revenue & Voyage P&L Summary Engine

Tests cover (20 total):

Pure Math Engine (pnl_engine.py):
  1.  Standard voyage without laytime calc: freight_revenue = rate * loaded, pnl = freight_revenue
  2.  Voyage with demurrage settlement: net_voyage_pnl = gross_freight - demurrage_cost
  3.  Voyage with despatch settlement: net_voyage_pnl = gross_freight + despatch_credit
  4.  Cargo outturn shortage calculation (discharged < loaded -> shortage_mt > 0)
  5.  Cargo outturn no shortage (discharged >= loaded -> shortage_mt = 0.000)
  6.  Undischarged voyage: shortage_mt and efficiency are None
  7.  Missing/zero freight rate raises VoyagePnLError(MISSING_FREIGHT_RATE)
  8.  Missing/zero loaded quantity raises VoyagePnLError(MISSING_LOADED_QUANTITY)
  9.  Negative/zero nominated quantity raises VoyagePnLError(NEGATIVE_NOMINATED)
  10. Fixed-point Decimal rounding invariance (USD ROUND_HALF_UP)
  11. Settlement finality metadata flag (is_settlement_final = True on SETTLED/DISPUTED)

Database Model Wrapper (compute_voyage_pnl_from_db):
  12. ORM voyage with no laytime calculation returns ZERO_NO_CALC snapshot
  13. ORM voyage with current demurrage calculation uses calculation figures
  14. ORM voyage with current despatch calculation uses calculation figures
  15. ORM voyage with multiple revisions respects is_current=True filter

REST API Integration (GET /api/v1/voyages/{voyage_id}/pnl/):
  16. GET /pnl/ returns 200 OK with complete JSON structure
  17. GET /pnl/ values match snapshot fields accurately
  18. GET /pnl/ on voyage with laytime calculation serializes calc fields
  19. GET /pnl/ with non-existent voyage UUID returns 404 NOT FOUND
  20. RBAC permissions: SYSTEM_ADMIN, LOGISTICS_AGENT, FINANCE_OFFICER, AUDITOR, READONLY can access
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
from apps.voyages.pnl_engine import (
    compute_voyage_pnl,
    compute_voyage_pnl_from_db,
    VoyagePnLError,
)
from apps.laytime.engine.fixture_factory import VoyageStatus
from apps.laytime.engine.settlement_engine import LaytimeOutcome

UTC = datetime.timezone.utc


# ─── Fixtures & Helpers ───────────────────────────────────────────────────────


@pytest.fixture
def api_client():
    return APIClient()


def _make_user(role: str, suffix: str = "pnl"):
    from django.contrib.auth import get_user_model
    from apps.authentication.models import UserProfile

    User = get_user_model()
    username = f"pnl_{role.lower()}_{suffix}"
    email = f"pnl_{role.lower()}_{suffix}@test.com"
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
def base_contract(db):
    vessel = Vessel.objects.create(
        vessel_name="MV PnL Pioneer",
        imo_number="7771234",
        flag_state="IN",
        deadweight_tonnage=Decimal("82000.000"),
        gross_tonnage=Decimal("44000.000"),
        net_tonnage=Decimal("25000.000"),
        length_overall_meters=Decimal("229.00"),
        beam_meters=Decimal("32.26"),
        draft_meters=Decimal("14.50"),
        hold_count=7,
        hatch_count=7,
        build_year=2020,
        owner_name="Steel Logistics Navigation Ltd",
    )
    contract = CharterContract.objects.create(
        contract_reference="CP-PNL-2026-001",
        charterer_entity="Steel Authority of India Ltd",
        charterer_plant="Bhilai Steel Plant",
        vessel=vessel,
        nominated_quantity_mt=Decimal("75000.000"),
        freight_rate_usd_per_mt=Decimal("16.50"),
        load_port="Port Hedland",
        discharge_port="Dhamra",
        discharge_rate_mt_per_day=Decimal("12500.000"),
        laycan_start=datetime.date(2026, 11, 1),
        laycan_end=datetime.date(2026, 11, 10),
        demurrage_rate_usd_per_day=Decimal("24000.00"),
        despatch_rate_usd_per_day=Decimal("12000.00"),
    )
    return vessel, contract


def _make_test_voyage(base_contract, ref="001", loaded=Decimal("74500.000"), discharged=None):
    vessel, contract = base_contract
    voyage = VesselVoyage(
        voyage_reference=f"VOY-PNL-{ref}",
        vessel=vessel,
        contract=contract,
        load_port="Port Hedland",
        discharge_port="Dhamra",
        quantity_loaded_mt=loaded,
        quantity_discharged_mt=discharged,
        eta_date=timezone.now() + datetime.timedelta(days=5),
        voyage_status=VoyageStatus.SCHEDULED.value,
    )
    voyage.save(skip_clean=True)
    return voyage


# ─── 1. Pure Math: Standard Voyage without Laytime Calc ───────────────────────


def test_compute_voyage_pnl_standard_no_calc():
    """Standard voyage P&L without laytime calc: pnl equals gross freight revenue."""
    snapshot = compute_voyage_pnl(
        voyage_id="voy-1",
        voyage_reference="VOY-2026-01",
        vessel_name="MV Ocean Trader",
        cargo_type="COKING_COAL",
        load_port="Gladstone",
        discharge_port="Paradip",
        nominated_quantity_mt=Decimal("75000.000"),
        freight_rate_usd_per_mt=Decimal("16.50"),
        quantity_loaded_mt=Decimal("74500.000"),
    )

    # 74,500 MT * $16.50 = $1,229,250.00
    assert snapshot.gross_freight_revenue_usd == Decimal("1229250.00")
    assert snapshot.net_settlement_usd == Decimal("0.00")
    assert snapshot.net_voyage_pnl_usd == Decimal("1229250.00")
    assert snapshot.calculation_basis == "ZERO_NO_CALC"
    # Freight efficiency = 74500 / 75000 * 100 = 99.33%
    assert snapshot.freight_efficiency_pct == Decimal("99.33")


# ─── 2. Pure Math: Demurrage Settlement ───────────────────────────────────────


def test_compute_voyage_pnl_with_demurrage():
    """Demurrage is a cost that reduces net voyage P&L."""
    snapshot = compute_voyage_pnl(
        voyage_id="voy-2",
        voyage_reference="VOY-2026-02",
        vessel_name="MV Bulk Giant",
        cargo_type="IRON_ORE_FINES",
        load_port="Dampier",
        discharge_port="Visakhapatnam",
        nominated_quantity_mt=Decimal("80000.000"),
        freight_rate_usd_per_mt=Decimal("15.00"),
        quantity_loaded_mt=Decimal("80000.000"),
        laytime_outcome="DEMURRAGE",
        gross_demurrage_usd=Decimal("36000.00"),
        net_settlement_usd_from_calc=Decimal("36000.00"),
        laytime_calc_revision=1,
    )

    # Gross freight = 80,000 * $15.00 = $1,200,000.00
    assert snapshot.gross_freight_revenue_usd == Decimal("1200000.00")
    assert snapshot.gross_demurrage_usd == Decimal("36000.00")
    assert snapshot.gross_despatch_usd == Decimal("0.00")
    assert snapshot.net_settlement_usd == Decimal("36000.00")
    # Net P&L = $1,200,000.00 - $36,000.00 = $1,164,000.00
    assert snapshot.net_voyage_pnl_usd == Decimal("1164000.00")
    assert snapshot.calculation_basis == "LAYTIME_CALCULATION"


# ─── 3. Pure Math: Despatch Settlement ────────────────────────────────────────


def test_compute_voyage_pnl_with_despatch():
    """Despatch is a credit that increases net voyage P&L."""
    snapshot = compute_voyage_pnl(
        voyage_id="voy-3",
        voyage_reference="VOY-2026-03",
        vessel_name="MV Fast Turn",
        cargo_type="COKING_COAL",
        load_port="Hay Point",
        discharge_port="Haldia",
        nominated_quantity_mt=Decimal("70000.000"),
        freight_rate_usd_per_mt=Decimal("18.00"),
        quantity_loaded_mt=Decimal("70000.000"),
        laytime_outcome="DESPATCH",
        gross_despatch_usd=Decimal("18000.00"),
        net_settlement_usd_from_calc=Decimal("-18000.00"),
        laytime_calc_revision=2,
    )

    # Gross freight = 70,000 * $18.00 = $1,260,000.00
    assert snapshot.gross_freight_revenue_usd == Decimal("1260000.00")
    assert snapshot.gross_despatch_usd == Decimal("18000.00")
    assert snapshot.net_settlement_usd == Decimal("-18000.00")
    # Net P&L = $1,260,000.00 - (-$18,000.00) = $1,278,000.00
    assert snapshot.net_voyage_pnl_usd == Decimal("1278000.00")


# ─── 4. Pure Math: Cargo Outturn Shortage ─────────────────────────────────────


def test_compute_voyage_pnl_outturn_shortage():
    """Shortage is calculated when discharged is less than loaded."""
    snapshot = compute_voyage_pnl(
        voyage_id="voy-4",
        voyage_reference="VOY-2026-04",
        vessel_name="MV Cargo Loss",
        cargo_type="MET_COKE",
        load_port="Newcastle",
        discharge_port="Dhamra",
        nominated_quantity_mt=Decimal("60000.000"),
        freight_rate_usd_per_mt=Decimal("20.00"),
        quantity_loaded_mt=Decimal("60000.000"),
        quantity_discharged_mt=Decimal("59450.250"),
    )

    # Shortage = 60000 - 59450.250 = 549.7500 MT
    assert snapshot.shortage_mt == Decimal("549.7500")
    # Outturn efficiency = (59450.250 / 60000) * 100 = 99.08%
    assert snapshot.cargo_outturn_efficiency_pct == Decimal("99.08")


# ─── 5. Pure Math: Cargo Outturn No Shortage (Excess) ─────────────────────────


def test_compute_voyage_pnl_outturn_no_shortage():
    """When discharged exceeds or equals loaded, shortage is floored at 0."""
    snapshot = compute_voyage_pnl(
        voyage_id="voy-5",
        voyage_reference="VOY-2026-05",
        vessel_name="MV Extra Cargo",
        cargo_type="COKING_COAL",
        load_port="Port Hedland",
        discharge_port="Paradip",
        nominated_quantity_mt=Decimal("75000.000"),
        freight_rate_usd_per_mt=Decimal("16.00"),
        quantity_loaded_mt=Decimal("75000.000"),
        quantity_discharged_mt=Decimal("75050.000"),
    )

    assert snapshot.shortage_mt == Decimal("0.0000")
    assert snapshot.cargo_outturn_efficiency_pct == Decimal("100.07")


# ─── 6. Pure Math: Undischarged Voyage ────────────────────────────────────────


def test_compute_voyage_pnl_undischarged():
    """When voyage is underway without discharge data, shortage fields are None."""
    snapshot = compute_voyage_pnl(
        voyage_id="voy-6",
        voyage_reference="VOY-2026-06",
        vessel_name="MV En Route",
        cargo_type="COKING_COAL",
        load_port="Port Hedland",
        discharge_port="Paradip",
        nominated_quantity_mt=Decimal("75000.000"),
        freight_rate_usd_per_mt=Decimal("16.00"),
        quantity_loaded_mt=Decimal("75000.000"),
        quantity_discharged_mt=None,
    )

    assert snapshot.quantity_discharged_mt is None
    assert snapshot.shortage_mt is None
    assert snapshot.cargo_outturn_efficiency_pct is None


# ─── 7. Validation: Missing Freight Rate ──────────────────────────────────────


def test_compute_voyage_pnl_missing_freight_rate_raises():
    """Zero or missing freight rate raises VoyagePnLError with code MISSING_FREIGHT_RATE."""
    with pytest.raises(VoyagePnLError) as exc_info:
        compute_voyage_pnl(
            voyage_id="voy-err",
            voyage_reference="VOY-ERR",
            vessel_name="MV Error",
            cargo_type="COKING_COAL",
            load_port="Port Hedland",
            discharge_port="Paradip",
            nominated_quantity_mt=Decimal("75000.000"),
            freight_rate_usd_per_mt=Decimal("0.00"),
            quantity_loaded_mt=Decimal("75000.000"),
        )
    assert exc_info.value.code == "MISSING_FREIGHT_RATE"


# ─── 8. Validation: Missing Loaded Quantity ───────────────────────────────────


def test_compute_voyage_pnl_missing_loaded_quantity_raises():
    """Zero or missing loaded quantity raises VoyagePnLError with code MISSING_LOADED_QUANTITY."""
    with pytest.raises(VoyagePnLError) as exc_info:
        compute_voyage_pnl(
            voyage_id="voy-err",
            voyage_reference="VOY-ERR",
            vessel_name="MV Error",
            cargo_type="COKING_COAL",
            load_port="Port Hedland",
            discharge_port="Paradip",
            nominated_quantity_mt=Decimal("75000.000"),
            freight_rate_usd_per_mt=Decimal("16.00"),
            quantity_loaded_mt=Decimal("0.000"),
        )
    assert exc_info.value.code == "MISSING_LOADED_QUANTITY"


# ─── 9. Validation: Negative Nominated Quantity ───────────────────────────────


def test_compute_voyage_pnl_negative_nominated_raises():
    """Negative or zero nominated quantity raises VoyagePnLError with code NEGATIVE_NOMINATED."""
    with pytest.raises(VoyagePnLError) as exc_info:
        compute_voyage_pnl(
            voyage_id="voy-err",
            voyage_reference="VOY-ERR",
            vessel_name="MV Error",
            cargo_type="COKING_COAL",
            load_port="Port Hedland",
            discharge_port="Paradip",
            nominated_quantity_mt=Decimal("-100.000"),
            freight_rate_usd_per_mt=Decimal("16.00"),
            quantity_loaded_mt=Decimal("75000.000"),
        )
    assert exc_info.value.code == "NEGATIVE_NOMINATED"


# ─── 10. Precision: Fixed-point Decimal Rounding Invariance ───────────────────


def test_compute_voyage_pnl_rounding_precision():
    """Fractional cents round deterministically using ROUND_HALF_UP."""
    # 74,321.125 MT * $16.333/MT = 1,213,886.934625 -> rounds to 1,213,886.93
    snapshot = compute_voyage_pnl(
        voyage_id="voy-prec",
        voyage_reference="VOY-PREC",
        vessel_name="MV Precision",
        cargo_type="COKING_COAL",
        load_port="Port Hedland",
        discharge_port="Paradip",
        nominated_quantity_mt=Decimal("75000.000"),
        freight_rate_usd_per_mt=Decimal("16.33"),
        quantity_loaded_mt=Decimal("74321.125"),
    )

    from decimal import ROUND_HALF_UP
    expected = (Decimal("16.33") * Decimal("74321.125")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    assert snapshot.gross_freight_revenue_usd == expected
    assert str(snapshot.gross_freight_revenue_usd).endswith(tuple(f".{i:02d}" for i in range(100)))


# ─── 11. Metadata: Settlement Finality Flag ───────────────────────────────────


def test_compute_voyage_pnl_is_settlement_final_flag():
    """is_settlement_final correctly propagates from input parameter."""
    s1 = compute_voyage_pnl(
        voyage_id="voy-f1",
        voyage_reference="VOY-F1",
        vessel_name="MV Ship 1",
        cargo_type="COKING_COAL",
        load_port="Port Hedland",
        discharge_port="Paradip",
        nominated_quantity_mt=Decimal("75000.000"),
        freight_rate_usd_per_mt=Decimal("16.00"),
        quantity_loaded_mt=Decimal("75000.000"),
        is_settlement_final=True,
    )
    assert s1.is_settlement_final is True

    s2 = compute_voyage_pnl(
        voyage_id="voy-f2",
        voyage_reference="VOY-F2",
        vessel_name="MV Ship 2",
        cargo_type="COKING_COAL",
        load_port="Port Hedland",
        discharge_port="Paradip",
        nominated_quantity_mt=Decimal("75000.000"),
        freight_rate_usd_per_mt=Decimal("16.00"),
        quantity_loaded_mt=Decimal("75000.000"),
        is_settlement_final=False,
    )
    assert s2.is_settlement_final is False


# ─── 12. DB Wrapper: Voyage with No Laytime Calculation ───────────────────────


@pytest.mark.django_db
def test_db_wrapper_no_laytime_calc(base_contract):
    """ORM wrapper on voyage without laytime calculation returns clean zero settlement."""
    voyage = _make_test_voyage(base_contract, ref="db1", loaded=Decimal("74000.000"))

    snapshot = compute_voyage_pnl_from_db(voyage)

    # 74,000 * 16.50 = 1,221,000.00
    assert snapshot.gross_freight_revenue_usd == Decimal("1221000.00")
    assert snapshot.net_settlement_usd == Decimal("0.00")
    assert snapshot.net_voyage_pnl_usd == Decimal("1221000.00")
    assert snapshot.calculation_basis == "ZERO_NO_CALC"
    assert snapshot.laytime_outcome is None


# ─── 13. DB Wrapper: Current Demurrage Calculation ────────────────────────────


@pytest.mark.django_db
def test_db_wrapper_with_current_laytime_demurrage(base_contract):
    """ORM wrapper correctly integrates existing current demurrage calculation."""
    voyage = _make_test_voyage(base_contract, ref="db2", loaded=Decimal("75000.000"))

    LaytimeCalculation.objects.create(
        voyage=voyage,
        revision=1,
        is_current=True,
        outcome=LaytimeOutcome.DEMURRAGE.value,
        allowed_laytime_hours=Decimal("144.000000"),
        total_counted_hours=Decimal("168.000000"),
        gross_demurrage_usd=Decimal("24000.00"),
        gross_despatch_usd=Decimal("0.00"),
        net_settlement_usd=Decimal("24000.00"),
    )

    snapshot = compute_voyage_pnl_from_db(voyage)

    # 75,000 * 16.50 = 1,237,500.00
    assert snapshot.gross_freight_revenue_usd == Decimal("1237500.00")
    assert snapshot.net_settlement_usd == Decimal("24000.00")
    # P&L = 1,237,500 - 24,000 = 1,213,500.00
    assert snapshot.net_voyage_pnl_usd == Decimal("1213500.00")
    assert snapshot.laytime_outcome == "DEMURRAGE"
    assert snapshot.calculation_basis == "LAYTIME_CALCULATION"


# ─── 14. DB Wrapper: Current Despatch Calculation ─────────────────────────────


@pytest.mark.django_db
def test_db_wrapper_with_current_laytime_despatch(base_contract):
    """ORM wrapper correctly integrates existing current despatch calculation."""
    voyage = _make_test_voyage(base_contract, ref="db3", loaded=Decimal("75000.000"))

    LaytimeCalculation.objects.create(
        voyage=voyage,
        revision=1,
        is_current=True,
        outcome=LaytimeOutcome.DESPATCH.value,
        allowed_laytime_hours=Decimal("144.000000"),
        total_counted_hours=Decimal("120.000000"),
        gross_demurrage_usd=Decimal("0.00"),
        gross_despatch_usd=Decimal("12000.00"),
        net_settlement_usd=Decimal("-12000.00"),
    )

    snapshot = compute_voyage_pnl_from_db(voyage)

    # 75,000 * 16.50 = 1,237,500.00
    assert snapshot.gross_freight_revenue_usd == Decimal("1237500.00")
    assert snapshot.net_settlement_usd == Decimal("-12000.00")
    # P&L = 1,237,500 - (-12,000) = 1,249,500.00
    assert snapshot.net_voyage_pnl_usd == Decimal("1249500.00")
    assert snapshot.laytime_outcome == "DESPATCH"


# ─── 15. DB Wrapper: Multiple Revisions Selects is_current=True ───────────────


@pytest.mark.django_db
def test_db_wrapper_multiple_revisions_selects_current(base_contract):
    """ORM wrapper queries only the is_current=True revision among multiple revisions."""
    voyage = _make_test_voyage(base_contract, ref="db4", loaded=Decimal("75000.000"))

    # Revision 1: superseded
    LaytimeCalculation.objects.create(
        voyage=voyage,
        revision=1,
        is_current=False,
        outcome=LaytimeOutcome.DEMURRAGE.value,
        allowed_laytime_hours=Decimal("144.000000"),
        gross_demurrage_usd=Decimal("50000.00"),
        net_settlement_usd=Decimal("50000.00"),
    )
    # Revision 2: current authoritative
    LaytimeCalculation.objects.create(
        voyage=voyage,
        revision=2,
        is_current=True,
        outcome=LaytimeOutcome.DEMURRAGE.value,
        allowed_laytime_hours=Decimal("144.000000"),
        gross_demurrage_usd=Decimal("20000.00"),
        net_settlement_usd=Decimal("20000.00"),
    )

    snapshot = compute_voyage_pnl_from_db(voyage)
    assert snapshot.laytime_calc_revision == 2
    assert snapshot.net_settlement_usd == Decimal("20000.00")


# ─── 16. REST API: GET /pnl/ Returns 200 OK ──────────────────────────────────


@pytest.mark.django_db
def test_api_get_pnl_success_200(base_contract, api_client):
    """GET /api/v1/voyages/{voyage_id}/pnl/ returns 200 OK with expected top-level keys."""
    voyage = _make_test_voyage(base_contract, ref="api1", loaded=Decimal("74000.000"))

    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="api1")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    url = f"/api/v1/voyages/{voyage.voyage_id}/pnl/"
    response = api_client.get(url)

    assert response.status_code == drf_status.HTTP_200_OK
    data = response.json()

    assert data["voyage_id"] == str(voyage.voyage_id)
    assert data["voyage_reference"] == voyage.voyage_reference
    assert "gross_freight_revenue_usd" in data
    assert "net_voyage_pnl_usd" in data
    assert "freight_efficiency_pct" in data


# ─── 17. REST API: Field Value Accuracy ──────────────────────────────────────


@pytest.mark.django_db
def test_api_get_pnl_fields_accuracy(base_contract, api_client):
    """API response contains accurate financial and quantity values formatted as decimals."""
    voyage = _make_test_voyage(
        base_contract, ref="api2", loaded=Decimal("75000.000"), discharged=Decimal("74800.000")
    )

    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="api2")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    url = f"/api/v1/voyages/{voyage.voyage_id}/pnl/"
    response = api_client.get(url)
    assert response.status_code == drf_status.HTTP_200_OK

    data = response.json()
    # 75,000 * 16.50 = 1,237,500.00
    assert data["gross_freight_revenue_usd"] == "1237500.00"
    assert data["net_voyage_pnl_usd"] == "1237500.00"
    assert data["shortage_mt"] == "200.0000"
    assert data["cargo_outturn_efficiency_pct"] == "99.73"
    assert data["freight_efficiency_pct"] == "100.00"


# ─── 18. REST API: Voyage with Laytime Calculation ────────────────────────────


@pytest.mark.django_db
def test_api_get_pnl_with_laytime_calc(base_contract, api_client):
    """API returns laytime calculation details and adjusted net P&L."""
    voyage = _make_test_voyage(base_contract, ref="api3", loaded=Decimal("75000.000"))

    LaytimeCalculation.objects.create(
        voyage=voyage,
        revision=1,
        is_current=True,
        outcome=LaytimeOutcome.DEMURRAGE.value,
        allowed_laytime_hours=Decimal("144.000000"),
        gross_demurrage_usd=Decimal("30000.00"),
        net_settlement_usd=Decimal("30000.00"),
    )

    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="api3")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    url = f"/api/v1/voyages/{voyage.voyage_id}/pnl/"
    response = api_client.get(url)
    assert response.status_code == drf_status.HTTP_200_OK

    data = response.json()
    assert data["laytime_outcome"] == "DEMURRAGE"
    assert data["gross_demurrage_usd"] == "30000.00"
    assert data["net_settlement_usd"] == "30000.00"
    # Net P&L = 1,237,500 - 30,000 = 1,207,500.00
    assert data["net_voyage_pnl_usd"] == "1207500.00"
    assert data["calculation_basis"] == "LAYTIME_CALCULATION"


# ─── 19. REST API: Non-existent Voyage ID Returns 404 ─────────────────────────


@pytest.mark.django_db
def test_api_get_pnl_not_found_404(api_client):
    """GET /pnl/ with unknown voyage UUID returns 404 NOT FOUND."""
    admin = _make_user(UserRole.SYSTEM_ADMIN.value, suffix="404")
    api_client.force_authenticate(user=admin)
    api_client.credentials(HTTP_X_USER_ROLE=UserRole.SYSTEM_ADMIN.value)

    fake_id = uuid.uuid4()
    url = f"/api/v1/voyages/{fake_id}/pnl/"
    response = api_client.get(url)

    assert response.status_code == drf_status.HTTP_404_NOT_FOUND


# ─── 20. REST API: RBAC Permissions for Multiple Roles ────────────────────────


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
def test_api_get_pnl_rbac_roles_allowed(base_contract, api_client, role):
    """All authorized organizational roles have read visibility on the P&L endpoint."""
    voyage = _make_test_voyage(base_contract, ref=f"rbac_{role.lower()}")

    user = _make_user(role, suffix=f"rb_{role.lower()}")
    api_client.force_authenticate(user=user)
    api_client.credentials(HTTP_X_USER_ROLE=role)

    url = f"/api/v1/voyages/{voyage.voyage_id}/pnl/"
    response = api_client.get(url)

    assert response.status_code == drf_status.HTTP_200_OK
    assert response.json()["voyage_id"] == str(voyage.voyage_id)
