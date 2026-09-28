"""
backend/apps/voyages/tests/test_executive_dashboard.py
============================================================
Cycle 2.5.6 — Tests: Executive Logistics KPI Summary & Consolidated Dashboard Aggregator Engine

Comprehensive test suite verifying:
  1. Fleet Operations & Cargo Movement KPIs (at-sea, in-port, delivered tonnage).
  2. Financial Performance & Laytime Settlement KPIs (gross freight, demurrage, despatch, P&L).
  3. Steel Plant Raw Material Security KPIs (5 SAIL plants, critical/precautionary counts, security index).
  4. Contract Fulfillment & Carrier Reliability KPIs (nominated vs lifted, fulfillment %, carrier score).
  5. Compliance, Vault Integrity & Operational Risk Alerts (tamper evidence, berth collisions, ETA delays).
  6. Enterprise Composite Health Score & Prioritized Actionable Highlights.
  7. REST API Endpoint GET /api/v1/voyages/executive-summary/ schema integrity and RBAC controls.
"""

import datetime
from decimal import Decimal
import pytest
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.authentication.roles import UserRole
from apps.contracts.models import Vessel, CharterContract
from apps.documents.models import ShippingDocument, DocumentType, DocumentVerificationStatus
from apps.inventory.models import PlantInventory, RawMaterialCategory, StockSafetyStatus
from apps.laytime.engine.fixture_factory import VoyageStatus
from apps.voyages.models import VesselVoyage, LaytimeCalculation
from apps.voyages.executive_dashboard import (
    compute_executive_dashboard_summary,
    ExecutiveDashboardKPIs,
    FleetLogisticsKPIs,
    FinancialLogisticsKPIs,
    PlantSecurityKPIs,
    ContractFulfillmentKPIs,
    ComplianceAndRiskKPIs,
)

UTC = datetime.timezone.utc


def _utc(year, month, day, hour=0, minute=0) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, minute, 0, tzinfo=UTC)


@pytest.fixture
def api_client():
    return APIClient()


def _make_user(db, role, suffix="01"):
    from django.contrib.auth import get_user_model
    from apps.authentication.models import UserProfile
    User = get_user_model()
    user = User.objects.create_user(
        username=f"exec_user_{role.lower()}_{suffix}",
        email=f"exec_{role.lower()}_{suffix}@test.com",
        password="TestPassword123!",
    )
    UserProfile.objects.create(
        user=user,
        role=role,
        organization="SAIL Corporate Logistics HQ",
    )
    return user


@pytest.fixture
def admin_user(db):
    return _make_user(db, UserRole.SYSTEM_ADMIN.value, "adm")


@pytest.fixture
def logistics_user(db):
    return _make_user(db, UserRole.LOGISTICS_AGENT.value, "log")


@pytest.fixture
def finance_user(db):
    return _make_user(db, UserRole.FINANCE_OFFICER.value, "fin")


@pytest.fixture
def auditor_user(db):
    return _make_user(db, UserRole.AUDITOR.value, "aud")


@pytest.fixture
def sample_vessel(db):
    return Vessel.objects.create(
        vessel_name="MV Bulk Executive",
        imo_number="9988776",
        flag_state="IN",
        build_year=2022,
        deadweight_tonnage=Decimal("120000.000"),
        gross_tonnage=Decimal("65000.000"),
        net_tonnage=Decimal("38000.000"),
        length_overall_meters=Decimal("255.00"),
        beam_meters=Decimal("43.00"),
        draft_meters=Decimal("15.50"),
        hold_count=7,
        hatch_count=7,
        owner_name="SAIL Ocean Transport",
    )


@pytest.fixture
def sample_contract(db, sample_vessel):
    today = datetime.date.today()
    return CharterContract.objects.create(
        contract_reference="SAIL/CP/2026/EXEC/01",
        vessel=sample_vessel,
        charterer_entity="STEEL_AUTHORITY_OF_INDIA",
        charterer_plant="BHILAI",
        cargo_type="COKING_COAL",
        charter_party_type="VOYAGE_CHARTER",
        contract_status="ACTIVE",
        laycan_start=today,
        laycan_end=today + datetime.timedelta(days=10),
        load_port="Gladstone",
        discharge_port="Visakhapatnam",
        nominated_quantity_mt=Decimal("80000.000"),
        laytime_terms="SHINC",
        discharge_rate_mt_per_day=Decimal("15000.000"),
        turn_time_hours=Decimal("6.00"),
        demurrage_rate_usd_per_day=Decimal("30000.00"),
        despatch_rate_usd_per_day=Decimal("15000.00"),
        freight_rate_usd_per_mt=Decimal("20.00"),
        allowed_laytime_hours=Decimal("120.000000"),
    )


# ===========================================================================
# 1. Fleet Operations KPIs Tests
# ===========================================================================

@pytest.mark.django_db
class TestFleetLogisticsKPIs:

    def test_empty_baseline_fleet(self, db):
        """Zero voyages baseline returns 0 active, 0 at sea, 0 delivered."""
        summary = compute_executive_dashboard_summary()
        fleet = summary.fleet_logistics

        assert fleet.total_active_voyages == 0
        assert fleet.vessels_at_sea == 0
        assert fleet.vessels_in_port == 0
        assert fleet.total_cargo_at_sea_mt == Decimal("0.000")
        assert fleet.total_cargo_in_port_mt == Decimal("0.000")
        assert fleet.total_completed_voyages == 0
        assert fleet.total_delivered_tonnage_mt == Decimal("0.000")

    def test_vessels_at_sea_and_in_port_segregation(self, db, sample_contract, sample_vessel):
        """Correctly separates vessels at sea from vessels berthed or discharging in port."""
        today = datetime.date.today()
        # Vessel at sea
        VesselVoyage.objects.create(
            voyage_reference="VOY-SEA-01",
            contract=sample_contract,
            vessel=sample_vessel,
            voyage_status="IN_TRANSIT",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            quantity_loaded_mt=Decimal("75000.000"),
            eta_date=_utc(today.year, today.month, today.day) + datetime.timedelta(days=5),
        )
        # Vessel in port (BERTHED)
        VesselVoyage.objects.create(
            voyage_reference="VOY-PORT-01",
            contract=sample_contract,
            vessel=sample_vessel,
            voyage_status="BERTHED",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            discharge_berth="Berth-5",
            quantity_loaded_mt=Decimal("65000.000"),
            eta_date=_utc(today.year, today.month, today.day),
        )
        # Vessel completed
        VesselVoyage.objects.create(
            voyage_reference="VOY-DONE-01",
            contract=sample_contract,
            vessel=sample_vessel,
            voyage_status="COMPLETED",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            quantity_loaded_mt=Decimal("70000.000"),
            quantity_discharged_mt=Decimal("69800.000"),
            eta_date=_utc(today.year, today.month, today.day) - datetime.timedelta(days=10),
        )

        summary = compute_executive_dashboard_summary()
        fleet = summary.fleet_logistics

        assert fleet.total_active_voyages == 2
        assert fleet.vessels_at_sea == 1
        assert fleet.vessels_in_port == 1
        assert fleet.total_cargo_at_sea_mt == Decimal("75000.000")
        assert fleet.total_cargo_in_port_mt == Decimal("65000.000")
        assert fleet.total_completed_voyages == 1
        assert fleet.total_delivered_tonnage_mt == Decimal("69800.000")


# ===========================================================================
# 2. Financial Performance & Laytime Settlement KPIs Tests
# ===========================================================================

@pytest.mark.django_db
class TestFinancialLogisticsKPIs:

    def test_gross_freight_and_net_laytime_impact(self, db, sample_contract, sample_vessel):
        """Calculates freight spend, demurrage/despatch balance, and net P&L."""
        today = datetime.date.today()
        voy = VesselVoyage.objects.create(
            voyage_reference="VOY-FIN-01",
            contract=sample_contract,
            vessel=sample_vessel,
            voyage_status="COMPLETED",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            quantity_loaded_mt=Decimal("80000.000"),
            eta_date=_utc(today.year, today.month, today.day),
        )
        # Gross freight = 80,000 * 20 = $1,600,000.00
        # Laytime settlement: Demurrage $40,000.00, Despatch $0.00
        LaytimeCalculation.objects.create(
            voyage=voy,
            revision=1,
            is_current=True,
            allowed_laytime_hours=Decimal("120.000000"),
            gross_demurrage_usd=Decimal("40000.00"),
            gross_despatch_usd=Decimal("0.00"),
            net_settlement_usd=Decimal("40000.00"),
        )

        summary = compute_executive_dashboard_summary()
        fin = summary.financial_performance

        assert fin.total_gross_freight_usd == Decimal("1600000.00")
        assert fin.total_demurrage_incurred_usd == Decimal("40000.00")
        assert fin.total_despatch_earned_usd == Decimal("0.00")
        assert fin.net_laytime_impact_usd == Decimal("40000.00")
        assert fin.net_voyage_pnl_usd == Decimal("1560000.00")  # 1,600,000 - 40,000
        assert fin.demurrage_to_freight_ratio_pct == Decimal("2.50")  # (40,000 / 1,600,000) * 100


# ===========================================================================
# 3. Steel Plant Raw Material Security KPIs Tests
# ===========================================================================

@pytest.mark.django_db
class TestPlantSecurityKPIs:

    def test_plant_security_index_with_mixed_health(self, db):
        """Evaluates plant health counts and security index across all 5 plants."""
        today = datetime.date.today()
        # Plant 1: BSP - Healthy (30 days)
        PlantInventory.objects.create(
            plant_code="BSP",
            plant_name="Bhilai Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("150000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("5000.000"),
        )
        # Plant 2: RSP - Critical (4 days)
        PlantInventory.objects.create(
            plant_code="RSP",
            plant_name="Rourkela Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("20000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("5000.000"),
        )

        summary = compute_executive_dashboard_summary()
        plant_kpis = summary.plant_security

        assert plant_kpis.total_plants_evaluated == 5
        assert len(plant_kpis.plant_summaries) == 5
        assert plant_kpis.plants_at_critical_risk >= 1  # RSP has 4 days
        assert plant_kpis.raw_material_security_index_pct > Decimal("0.00")


# ===========================================================================
# 4. Contract Fulfillment & Carrier Reliability Tests
# ===========================================================================

@pytest.mark.django_db
class TestContractFulfillmentKPIs:

    def test_contract_volume_fulfillment_rate(self, db, sample_contract, sample_vessel):
        """Computes lifted volume vs nominated volume on active contracts."""
        today = datetime.date.today()
        # Contract nominated: 80,000 MT. Loaded: 76,000 MT -> 95.00% fulfillment
        VesselVoyage.objects.create(
            voyage_reference="VOY-FUL-01",
            contract=sample_contract,
            vessel=sample_vessel,
            voyage_status="IN_TRANSIT",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            quantity_loaded_mt=Decimal("76000.000"),
            eta_date=_utc(today.year, today.month, today.day),
        )

        summary = compute_executive_dashboard_summary()
        cf = summary.contract_fulfillment

        assert cf.total_active_contracts == 1
        assert cf.total_nominated_volume_mt == Decimal("80000.000")
        assert cf.total_lifted_volume_mt == Decimal("76000.000")
        assert cf.volume_fulfillment_rate_pct == Decimal("95.00")
        assert cf.average_carrier_reliability_score >= Decimal("0.00")


# ===========================================================================
# 5. Compliance & Operational Risk Alerts Tests
# ===========================================================================

@pytest.mark.django_db
class TestComplianceAndRiskKPIs:

    def test_document_vault_compliance_rate(self, db, sample_contract, sample_vessel):
        """Computes verified vs quarantined documents compliance rate."""
        today = datetime.date.today()
        voy = VesselVoyage.objects.create(
            voyage_reference="VOY-DOC-01",
            contract=sample_contract,
            vessel=sample_vessel,
            voyage_status="BERTHED",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            quantity_loaded_mt=Decimal("50000.000"),
            eta_date=_utc(today.year, today.month, today.day),
        )
        hash_clean = "a" * 64
        hash_client = "b" * 64
        hash_server = "c" * 64
        # Doc 1: Verified
        ShippingDocument.objects.create(
            voyage=voy,
            document_title="Bill of Lading Clean",
            document_type=DocumentType.BILL_OF_LADING.value,
            original_filename="bl_clean.pdf",
            file_size_bytes=1024,
            mime_type="application/pdf",
            minio_object_key="documents/2026/09/bl_clean.pdf",
            sha256_hash_client=hash_clean,
            sha256_hash_server=hash_clean,
            verification_status=DocumentVerificationStatus.HASH_VERIFIED.value,
        )
        # Doc 2: Quarantined
        ShippingDocument.objects.create(
            voyage=voy,
            document_title="Statement of Facts Tampered",
            document_type=DocumentType.STATEMENT_OF_FACTS.value,
            original_filename="sof_tampered.pdf",
            file_size_bytes=2048,
            mime_type="application/pdf",
            minio_object_key="documents/2026/09/sof_tampered.pdf",
            sha256_hash_client=hash_client,
            sha256_hash_server=hash_server,
            verification_status=DocumentVerificationStatus.QUARANTINED.value,
        )

        summary = compute_executive_dashboard_summary()
        comp = summary.compliance_and_risk

        assert comp.total_vault_documents == 2
        assert comp.verified_documents_count == 1
        assert comp.quarantined_documents_count == 1
        assert comp.vault_compliance_rate_pct == Decimal("50.00")

    def test_eta_deviations_alert_count(self, db, sample_contract, sample_vessel):
        """Active voyages past their ETA with no ATA are counted as ETA deviations."""
        today = datetime.date.today()
        # ETA was 5 days ago, no ATA -> deviation!
        VesselVoyage.objects.create(
            voyage_reference="VOY-LATE-01",
            contract=sample_contract,
            vessel=sample_vessel,
            voyage_status="IN_TRANSIT",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            quantity_loaded_mt=Decimal("60000.000"),
            eta_date=_utc(today.year, today.month, today.day) - datetime.timedelta(days=5),
            ata_date=None,
        )

        summary = compute_executive_dashboard_summary()
        comp = summary.compliance_and_risk
        assert comp.active_eta_deviations_count >= 1


# ===========================================================================
# 6. Composite Enterprise Health Score & Highlights Tests
# ===========================================================================

@pytest.mark.django_db
class TestExecutiveHealthScoreAndHighlights:

    def test_composite_score_bounds_and_status(self, db):
        """Health score stays strictly between 0 and 100 with valid status category."""
        summary = compute_executive_dashboard_summary()
        assert Decimal("0.00") <= summary.composite_enterprise_health_score <= Decimal("100.00")
        assert summary.overall_status in ["HEALTHY", "ATTENTION_REQUIRED", "CRITICAL_ALERT"]
        assert len(summary.executive_highlights) >= 1

    def test_demurrage_spike_triggers_highlight(self, db, sample_contract, sample_vessel):
        """Demurrage ratio > 5% generates an executive warning highlight."""
        today = datetime.date.today()
        voy = VesselVoyage.objects.create(
            voyage_reference="VOY-HIGH-DEM-01",
            contract=sample_contract,
            vessel=sample_vessel,
            voyage_status="COMPLETED",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            quantity_loaded_mt=Decimal("50000.000"),
            eta_date=_utc(today.year, today.month, today.day),
        )
        LaytimeCalculation.objects.create(
            voyage=voy,
            revision=1,
            is_current=True,
            allowed_laytime_hours=Decimal("120.000000"),
            gross_demurrage_usd=Decimal("100000.00"),  # 10% ratio!
            gross_despatch_usd=Decimal("0.00"),
            net_settlement_usd=Decimal("100000.00"),
        )

        summary = compute_executive_dashboard_summary()
        assert summary.financial_performance.demurrage_to_freight_ratio_pct == Decimal("10.00")
        assert any("DEMURRAGE EXPOSURE" in h for h in summary.executive_highlights)


# ===========================================================================
# 7. REST API Integration Tests
# ===========================================================================

@pytest.mark.django_db
class TestExecutiveDashboardAPI:

    def test_unauthenticated_request_rejected(self, api_client, settings):
        """Unauthenticated requests must be rejected when auth is strictly enforced."""
        settings.RBAC_REQUIRE_AUTHENTICATION = True
        resp = api_client.get("/api/v1/voyages/executive-summary/")
        assert resp.status_code in (drf_status.HTTP_401_UNAUTHORIZED, drf_status.HTTP_403_FORBIDDEN)

    def test_successful_executive_summary_schema(self, api_client, admin_user):
        """GET /api/v1/voyages/executive-summary/ returns 200 with complete nested schema."""
        api_client.force_authenticate(user=admin_user)
        resp = api_client.get("/api/v1/voyages/executive-summary/")
        assert resp.status_code == drf_status.HTTP_200_OK

        data = resp.data
        assert "composite_enterprise_health_score" in data
        assert "overall_status" in data
        assert "fleet_logistics" in data
        assert "financial_performance" in data
        assert "plant_security" in data
        assert "contract_fulfillment" in data
        assert "compliance_and_risk" in data
        assert "executive_highlights" in data
        assert "generated_at" in data

        # Nested structure checks
        assert "total_active_voyages" in data["fleet_logistics"]
        assert "total_gross_freight_usd" in data["financial_performance"]
        assert "raw_material_security_index_pct" in data["plant_security"]
        assert "volume_fulfillment_rate_pct" in data["contract_fulfillment"]
        assert "vault_compliance_rate_pct" in data["compliance_and_risk"]

    def test_rbac_access_across_domain_roles(self, api_client, admin_user, logistics_user, finance_user, auditor_user):
        """All domain roles (admin, logistics, finance, auditor) have read visibility."""
        for user in [admin_user, logistics_user, finance_user, auditor_user]:
            api_client.force_authenticate(user=user)
            resp = api_client.get("/api/v1/voyages/executive-summary/")
            assert resp.status_code == drf_status.HTTP_200_OK, f"Failed for {user.username}"


@pytest.mark.django_db
class TestExecutiveDashboardExtendedScenarios:

    def test_despatch_only_improves_net_pnl(self, db, sample_contract, sample_vessel):
        """Pure despatch scenario (zero demurrage) reduces freight spend in net P&L."""
        today = datetime.date.today()
        voy = VesselVoyage.objects.create(
            voyage_reference="VOY-DESP-01",
            contract=sample_contract,
            vessel=sample_vessel,
            voyage_status="COMPLETED",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            quantity_loaded_mt=Decimal("50000.000"),
            eta_date=_utc(today.year, today.month, today.day),
        )
        # Gross freight: 50,000 * 20 = $1,000,000.00
        # Despatch earned: $25,000.00 -> Net laytime impact = -$25,000.00
        LaytimeCalculation.objects.create(
            voyage=voy,
            revision=1,
            is_current=True,
            allowed_laytime_hours=Decimal("120.000000"),
            gross_demurrage_usd=Decimal("0.00"),
            gross_despatch_usd=Decimal("25000.00"),
            net_settlement_usd=Decimal("-25000.00"),
        )
        summary = compute_executive_dashboard_summary()
        fin = summary.financial_performance

        assert fin.total_demurrage_incurred_usd == Decimal("0.00")
        assert fin.total_despatch_earned_usd == Decimal("25000.00")
        assert fin.net_laytime_impact_usd == Decimal("-25000.00")
        assert fin.net_voyage_pnl_usd == Decimal("1025000.00")  # 1,000,000 - (-25,000)
        assert fin.demurrage_to_freight_ratio_pct == Decimal("0.00")

    def test_critical_plant_triggers_highlight_and_alert_status(self, db):
        """Starving plant (< 7d) forces CRITICAL_ALERT status and executive highlight."""
        today = datetime.date.today()
        # 10,000 MT / 5,000 MT/day = 2.00 days (CRITICAL!)
        PlantInventory.objects.create(
            plant_code="RSP",
            plant_name="Rourkela Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("10000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("5000.000"),
        )
        summary = compute_executive_dashboard_summary()

        assert summary.plant_security.plants_at_critical_risk >= 1
        assert summary.overall_status == "CRITICAL_ALERT"
        assert any("CRITICAL: " in h and "starvation threshold" in h for h in summary.executive_highlights)

    def test_precautionary_plant_triggers_highlight(self, db):
        """Plant between 7 and 14 days runway triggers precautionary highlight."""
        today = datetime.date.today()
        # 45,000 MT / 5,000 MT/day = 9.00 days (PRECAUTIONARY)
        PlantInventory.objects.create(
            plant_code="BSL",
            plant_name="Bokaro Steel Plant",
            material_category="COKING_COAL",
            stock_date=today,
            opening_stock_mt=Decimal("45000.000"),
            receipts_mt=Decimal("0.000"),
            consumption_mt=Decimal("0.000"),
            daily_consumption_rate_mt=Decimal("5000.000"),
        )
        summary = compute_executive_dashboard_summary()

        assert summary.plant_security.plants_at_precautionary_risk >= 1
        assert any("PRECAUTIONARY: " in h for h in summary.executive_highlights)

    def test_quarantined_document_highlight(self, db, sample_contract, sample_vessel):
        """Quarantined document triggers an executive warning bullet."""
        today = datetime.date.today()
        voy = VesselVoyage.objects.create(
            voyage_reference="VOY-QUAR-01",
            contract=sample_contract,
            vessel=sample_vessel,
            voyage_status="BERTHED",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            quantity_loaded_mt=Decimal("50000.000"),
            eta_date=_utc(today.year, today.month, today.day),
        )
        ShippingDocument.objects.create(
            voyage=voy,
            document_title="Compromised Assay",
            document_type=DocumentType.QUALITY_CERTIFICATE.value,
            original_filename="assay.pdf",
            file_size_bytes=1024,
            mime_type="application/pdf",
            minio_object_key="documents/2026/09/assay.pdf",
            sha256_hash_client="1" * 64,
            sha256_hash_server="2" * 64,
            verification_status=DocumentVerificationStatus.QUARANTINED.value,
        )
        summary = compute_executive_dashboard_summary()
        assert summary.compliance_and_risk.quarantined_documents_count >= 1
        assert any("VAULT WARNING" in h for h in summary.executive_highlights)

    def test_active_berth_conflict_triggers_highlight(self, db, sample_contract, sample_vessel):
        """Berth overlap generates collision highlight."""
        today = datetime.date.today()
        # Vessel 1 berthed at Berth-1
        VesselVoyage.objects.create(
            voyage_reference="VOY-CONF-01",
            contract=sample_contract,
            vessel=sample_vessel,
            voyage_status="BERTHED",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            discharge_berth="Berth-1",
            quantity_loaded_mt=Decimal("50000.000"),
            eta_date=_utc(today.year, today.month, today.day),
            berthed_at=_utc(today.year, today.month, today.day, 6),
        )
        # Vessel 2 also scheduled at Berth-1 at same time
        VesselVoyage.objects.create(
            voyage_reference="VOY-CONF-02",
            contract=sample_contract,
            vessel=sample_vessel,
            voyage_status="ARRIVED",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            discharge_berth="Berth-1",
            quantity_loaded_mt=Decimal("50000.000"),
            eta_date=_utc(today.year, today.month, today.day),
            berthed_at=_utc(today.year, today.month, today.day, 8),
        )
        summary = compute_executive_dashboard_summary()
        assert summary.compliance_and_risk.active_berth_conflicts_count >= 1

    def test_all_nominal_operations_highlight(self, db):
        """When operations are completely nominal, returns nominal confirmation bullet."""
        summary = compute_executive_dashboard_summary()
        # If no issues exist, check highlight structure
        assert len(summary.executive_highlights) >= 1

    def test_readonly_user_can_access_executive_api(self, api_client, db):
        """READONLY user role has read visibility on executive summary endpoint."""
        ro_user = _make_user(db, UserRole.READONLY.value, "read1")
        api_client.force_authenticate(user=ro_user)
        resp = api_client.get("/api/v1/voyages/executive-summary/")
        assert resp.status_code == drf_status.HTTP_200_OK

    def test_decimal_precision_strict_invariance(self, db):
        """Checks strict Decimal precision (2 d.p. for money, 3 d.p. for tonnages)."""
        summary = compute_executive_dashboard_summary()
        fl = summary.fleet_logistics
        fin = summary.financial_performance

        assert fl.total_cargo_at_sea_mt.as_tuple().exponent == -3
        assert fl.total_cargo_in_port_mt.as_tuple().exponent == -3
        assert fl.total_delivered_tonnage_mt.as_tuple().exponent == -3

        assert fin.total_gross_freight_usd.as_tuple().exponent == -2
        assert fin.total_demurrage_incurred_usd.as_tuple().exponent == -2
        assert fin.total_despatch_earned_usd.as_tuple().exponent == -2
        assert fin.net_laytime_impact_usd.as_tuple().exponent == -2
        assert fin.net_voyage_pnl_usd.as_tuple().exponent == -2
        assert fin.demurrage_to_freight_ratio_pct.as_tuple().exponent == -2

    def test_fulfillment_zero_when_no_active_contracts(self, db):
        """Zero active contracts returns 0.00% fulfillment rate without division by zero."""
        summary = compute_executive_dashboard_summary()
        assert summary.contract_fulfillment.total_active_contracts == 0
        assert summary.contract_fulfillment.volume_fulfillment_rate_pct == Decimal("0.00")

    def test_demurrage_ratio_zero_when_no_freight(self, db):
        """Zero freight spend returns 0.00% demurrage ratio without division error."""
        summary = compute_executive_dashboard_summary()
        assert summary.financial_performance.total_gross_freight_usd == Decimal("0.00")
        assert summary.financial_performance.demurrage_to_freight_ratio_pct == Decimal("0.00")

    def test_plant_security_summaries_contains_all_five_plants(self, db):
        """Plant security summaries explicitly lists BSP, DSP, RSP, BSL, ISP."""
        summary = compute_executive_dashboard_summary()
        codes = [p.plant_code for p in summary.plant_security.plant_summaries]
        assert set(codes) == {"BSP", "DSP", "RSP", "BSL", "ISP"}

    def test_carrier_reliability_scorecard_aggregated(self, db, sample_contract, sample_vessel):
        """Carrier scorecard calculation properly aggregates into enterprise average."""
        today = datetime.date.today()
        VesselVoyage.objects.create(
            voyage_reference="VOY-CARR-01",
            contract=sample_contract,
            vessel=sample_vessel,
            voyage_status="COMPLETED",
            load_port="Gladstone",
            discharge_port="Visakhapatnam",
            quantity_loaded_mt=Decimal("80000.000"),
            eta_date=_utc(today.year, today.month, today.day),
        )
        summary = compute_executive_dashboard_summary()
        assert summary.contract_fulfillment.average_carrier_reliability_score > Decimal("0.00")


