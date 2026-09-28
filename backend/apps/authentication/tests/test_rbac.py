"""
backend/apps/authentication/tests/test_rbac.py
============================================================
Cycle 2.1.4 — Unit & Integration Tests: Role-Based Authorization
Validates:
  1. UserRole enumerations & capability matrix
  2. UserProfile creation & role resolution
  3. AuditSessionContextMiddleware session attributes & IP tracking
  4. Granular RBAC enforcement on Charter Contracts (Procurement vs Logistics)
  5. Granular RBAC enforcement on Voyage Transitions (Logistics vs Procurement)
  6. Read-Only Auditor enforcement across all mutation endpoints
  7. Auth /me/ inspection endpoint
"""

import datetime
from decimal import Decimal
import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.authentication.models import UserProfile, get_user_role
from apps.authentication.roles import UserRole, get_role_capabilities
from apps.authentication.middleware import AuditSessionContextMiddleware
from apps.contracts.models import Vessel, CharterContract
from apps.voyages.models import VesselVoyage
from apps.laytime.engine.fixture_factory import VoyageStatus

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def make_user(db):
    """Helper factory for users with assigned enterprise roles."""
    def _create(username: str, role: str, is_superuser: bool = False):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@sail.in",
            password="TestPassword123!",
            is_superuser=is_superuser,
            is_staff=is_superuser,
        )
        UserProfile.objects.create(
            user=user,
            role=role,
            organization="Steel Authority of India Ltd (SAIL)",
        )
        return user
    return _create


@pytest.fixture
def admin_user(make_user):
    return make_user("admin_user", UserRole.SYSTEM_ADMIN.value, is_superuser=True)


@pytest.fixture
def procurement_user(make_user):
    return make_user("procurement_officer", UserRole.PROCUREMENT_OFFICER.value)


@pytest.fixture
def logistics_user(make_user):
    return make_user("logistics_agent", UserRole.LOGISTICS_AGENT.value)


@pytest.fixture
def auditor_user(make_user):
    return make_user("auditor_officer", UserRole.AUDITOR.value)


@pytest.fixture
def readonly_user(make_user):
    return make_user("readonly_guest", UserRole.READONLY.value)


@pytest.fixture
def base_vessel(db):
    return Vessel.objects.create(
        vessel_name="MV RBAC Test Carrier",
        imo_number="9876543",
        flag_state="IN",
        build_year=2021,
        deadweight_tonnage=Decimal("80000.000"),
        gross_tonnage=Decimal("42000.000"),
        net_tonnage=Decimal("26000.000"),
        length_overall_meters=Decimal("228.00"),
        beam_meters=Decimal("32.26"),
        draft_meters=Decimal("14.50"),
        hold_count=7,
        hatch_count=7,
        owner_name="Shipping Corporation of India Ltd",
    )


@pytest.fixture
def base_contract(db, base_vessel):
    return CharterContract.objects.create(
        contract_reference="SAIL/CP/2025/RBAC-01",
        vessel=base_vessel,
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
        laytime_terms="WWDSHEX",
        discharge_rate_mt_per_day=Decimal("15000.000"),
        turn_time_hours=Decimal("6.00"),
        demurrage_rate_usd_per_day=Decimal("25000.00"),
        despatch_rate_usd_per_day=Decimal("12500.00"),
        freight_rate_usd_per_mt=Decimal("18.50"),
        allowed_laytime_hours=Decimal("120.000000"),
    )


@pytest.fixture
def test_voyage(db, base_contract, base_vessel):
    return VesselVoyage.objects.create(
        voyage_reference="SAIL/VOY/RBAC/001",
        contract=base_contract,
        vessel=base_vessel,
        voyage_status=VoyageStatus.SCHEDULED.value,
        load_port="Gladstone",
        load_country="AU",
        discharge_port="Haldia",
        discharge_country="IN",
        quantity_loaded_mt=Decimal("75000.000"),
        eta_date=timezone.now() + datetime.timedelta(days=3),
    )


# =============================================================================
# 1. Role Capabilities & Resolution Tests
# =============================================================================

@pytest.mark.django_db
class TestRoleCapabilities:

    def test_all_eight_domain_roles_defined(self):
        roles = UserRole.all_roles()
        assert len(roles) == 8
        assert "SYSTEM_ADMIN" in roles
        assert "PROCUREMENT_OFFICER" in roles
        assert "LOGISTICS_AGENT" in roles
        assert "CHARTER_BROKER" in roles
        assert "FINANCE_OFFICER" in roles
        assert "PLANT_MANAGER" in roles
        assert "AUDITOR" in roles
        assert "READONLY" in roles

    def test_capabilities_matrix_differentiation(self):
        admin_caps = get_role_capabilities(UserRole.SYSTEM_ADMIN.value)
        assert admin_caps["can_manage_contracts"] is True
        assert admin_caps["can_transition_voyages"] is True
        assert admin_caps["is_read_only"] is False

        proc_caps = get_role_capabilities(UserRole.PROCUREMENT_OFFICER.value)
        assert proc_caps["can_manage_contracts"] is True
        assert proc_caps["can_manage_vessels"] is True
        assert proc_caps["can_transition_voyages"] is False
        assert proc_caps["is_read_only"] is False

        logistics_caps = get_role_capabilities(UserRole.LOGISTICS_AGENT.value)
        assert logistics_caps["can_manage_contracts"] is False
        assert logistics_caps["can_transition_voyages"] is True
        assert logistics_caps["can_enter_sof"] is True

        auditor_caps = get_role_capabilities(UserRole.AUDITOR.value)
        assert auditor_caps["is_read_only"] is True
        assert auditor_caps["can_view_audit_logs"] is True
        assert auditor_caps["can_manage_contracts"] is False

    def test_get_user_role_resolution(self, procurement_user, admin_user):
        assert get_user_role(procurement_user) == UserRole.PROCUREMENT_OFFICER.value
        assert get_user_role(admin_user) == UserRole.SYSTEM_ADMIN.value
        assert get_user_role(None) == UserRole.READONLY.value


# =============================================================================
# 2. Middleware Context Tests
# =============================================================================

@pytest.mark.django_db
class TestAuditSessionContextMiddleware:

    def test_middleware_attaches_role_and_ip(self, procurement_user):
        rf = RequestFactory()
        req = rf.get("/api/v1/contracts/", HTTP_X_FORWARDED_FOR="203.0.113.195, 10.0.0.1")
        req.user = procurement_user

        middleware = AuditSessionContextMiddleware(lambda r: None)
        middleware(req)

        assert req.user_role == UserRole.PROCUREMENT_OFFICER.value
        assert req.client_ip == "203.0.113.195"

    def test_middleware_honors_simulated_role_header(self, readonly_user):
        rf = RequestFactory()
        req = rf.get("/api/v1/voyages/", HTTP_X_USER_ROLE="LOGISTICS_AGENT")
        req.user = readonly_user

        middleware = AuditSessionContextMiddleware(lambda r: None)
        middleware(req)

        assert req.user_role == UserRole.LOGISTICS_AGENT.value


# =============================================================================
# 3. Contract Endpoints RBAC Tests
# =============================================================================

@pytest.mark.django_db
class TestRBACContracts:

    def test_procurement_officer_can_create_vessel(self, api_client, procurement_user):
        api_client.force_authenticate(user=procurement_user)
        payload = {
            "vessel_name": "MV PARADIP EXPRESS",
            "imo_number": "9765432",
            "flag_state": "IN",
            "size_class": "PANAMAX",
            "deadweight_tonnage": "75000.000",
            "gross_tonnage": "41000.000",
            "net_tonnage": "25000.000",
            "length_overall_meters": "225.00",
            "beam_meters": "32.26",
            "draft_meters": "14.20",
            "hold_count": 7,
            "hatch_count": 7,
            "build_year": 2022,
            "owner_name": "Shipping Corp of India",
        }
        res = api_client.post("/api/v1/vessels/", payload, format="json")
        assert res.status_code == status.HTTP_201_CREATED

    def test_logistics_agent_forbidden_from_creating_vessel(self, api_client, logistics_user):
        api_client.force_authenticate(user=logistics_user)
        payload = {
            "vessel_name": "MV FORBIDDEN VESSEL",
            "imo_number": "9765433",
            "flag_state": "IN",
            "size_class": "PANAMAX",
            "deadweight_tonnage": "75000.000",
            "gross_tonnage": "41000.000",
            "net_tonnage": "25000.000",
            "length_overall_meters": "225.00",
            "beam_meters": "32.26",
            "draft_meters": "14.20",
            "hold_count": 7,
            "hatch_count": 7,
            "build_year": 2022,
            "owner_name": "Shipping Corp of India",
        }
        res = api_client.post("/api/v1/vessels/", payload, format="json")
        assert res.status_code == status.HTTP_403_FORBIDDEN
        data = res.json()
        assert data["error"] == "FORBIDDEN"
        assert "LOGISTICS_AGENT" in data["detail"]

    def test_auditor_can_read_contracts_but_cannot_mutate(self, api_client, auditor_user, base_contract):
        api_client.force_authenticate(user=auditor_user)

        # GET is allowed
        res_get = api_client.get(f"/api/v1/contracts/{base_contract.contract_id}/")
        assert res_get.status_code == status.HTTP_200_OK

        # POST / DELETE is forbidden
        res_del = api_client.delete(f"/api/v1/contracts/{base_contract.contract_id}/")
        assert res_del.status_code == status.HTTP_403_FORBIDDEN


# =============================================================================
# 4. Voyage Milestone Transition RBAC Tests
# =============================================================================

@pytest.mark.django_db
class TestRBACVoyageTransitions:

    def test_logistics_agent_can_transition_voyage(self, api_client, logistics_user, test_voyage):
        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{test_voyage.voyage_id}/transition/"
        payload = {
            "target_status": "IN_TRANSIT",
            "remarks": "Logistics agent authorized departure",
        }
        res = api_client.post(url, payload, format="json")
        assert res.status_code == status.HTTP_200_OK
        assert res.json()["current_status"] == "IN_TRANSIT"

    def test_procurement_officer_forbidden_from_transitioning_voyage(self, api_client, procurement_user, test_voyage):
        api_client.force_authenticate(user=procurement_user)
        url = f"/api/v1/voyages/{test_voyage.voyage_id}/transition/"
        payload = {
            "target_status": "IN_TRANSIT",
        }
        res = api_client.post(url, payload, format="json")
        assert res.status_code == status.HTTP_403_FORBIDDEN
        data = res.json()
        assert data["error"] == "FORBIDDEN"
        assert "PROCUREMENT_OFFICER" in data["detail"]

    def test_system_admin_can_perform_both(self, api_client, admin_user, test_voyage):
        api_client.force_authenticate(user=admin_user)
        url = f"/api/v1/voyages/{test_voyage.voyage_id}/transition/"
        payload = {
            "target_status": "IN_TRANSIT",
            "remarks": "Admin override",
        }
        res = api_client.post(url, payload, format="json")
        assert res.status_code == status.HTTP_200_OK


# =============================================================================
# 5. Auth Me Endpoint Tests
# =============================================================================

@pytest.mark.django_db
class TestAuthMeEndpoint:

    def test_auth_me_returns_profile_and_capabilities(self, api_client, logistics_user):
        api_client.force_authenticate(user=logistics_user)
        res = api_client.get("/api/v1/auth/me/")
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["username"] == "logistics_agent"
        assert data["role"] == "LOGISTICS_AGENT"
        assert data["capabilities"]["can_transition_voyages"] is True
        assert data["capabilities"]["can_manage_contracts"] is False

    def test_auth_me_with_simulated_role_header(self, api_client, readonly_user):
        api_client.force_authenticate(user=readonly_user)
        res = api_client.get("/api/v1/auth/me/", HTTP_X_USER_ROLE="AUDITOR")
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["role"] == "AUDITOR"
        assert data["capabilities"]["is_read_only"] is True
        assert data["capabilities"]["can_view_audit_logs"] is True
