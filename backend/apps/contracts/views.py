"""
backend/apps/contracts/views.py
============================================================
DRF ViewSets for Vessel Registry and Charter Contracts.
"""

from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from django.shortcuts import get_object_or_404

from .models import Vessel, CharterContract
from .serializers import (
    VesselSerializer,
    CharterContractSerializer,
    ContractSpecificationSerializer,
    ContractPerformanceSerializer,
    CarrierScorecardSerializer,
)
from .scorecard_engine import (
    compute_contract_performance,
    compute_carrier_scorecards,
)
from apps.laytime.engine.fixture_factory import MaritimeFixtureFactory
from apps.authentication.roles import UserRole


class VesselViewSet(viewsets.ModelViewSet):
    """
    CRUD API for Vessel Registry.
    Endpoint: /api/v1/vessels/
    """
    queryset = Vessel.objects.all()
    serializer_class = VesselSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["vessel_name", "imo_number", "flag_state", "owner_name"]
    ordering_fields = ["vessel_name", "deadweight_tonnage", "build_year"]
    ordering = ["vessel_name"]

    action_roles = {
        "list": UserRole.all_roles(),
        "retrieve": UserRole.all_roles(),
        "lookup_by_imo": UserRole.all_roles(),
        "create": [UserRole.SYSTEM_ADMIN.value, UserRole.PROCUREMENT_OFFICER.value],
        "update": [UserRole.SYSTEM_ADMIN.value, UserRole.PROCUREMENT_OFFICER.value],
        "partial_update": [UserRole.SYSTEM_ADMIN.value, UserRole.PROCUREMENT_OFFICER.value],
        "destroy": [UserRole.SYSTEM_ADMIN.value],
    }

    @action(detail=False, methods=["get"], url_path=r"lookup/(?P<imo>\d{7})")
    def lookup_by_imo(self, request, imo=None):
        """Quick lookup of vessel particulars by 7-digit IMO number."""
        vessel = get_object_or_404(Vessel, imo_number=imo)
        serializer = self.get_serializer(vessel)
        return Response(serializer.data)


class CharterContractViewSet(viewsets.ModelViewSet):
    """
    CRUD API for Charter Party Contracts & Specifications.
    Endpoint: /api/v1/contracts/
    """
    queryset = CharterContract.objects.select_related("vessel").all()
    serializer_class = CharterContractSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "contract_reference",
        "charterer_entity",
        "charterer_plant",
        "discharge_port",
        "vessel__vessel_name",
        "vessel__imo_number",
    ]
    ordering_fields = [
        "created_at",
        "laycan_start",
        "nominated_quantity_mt",
        "contract_reference",
    ]
    ordering = ["-created_at"]

    action_roles = {
        "list": UserRole.all_roles(),
        "retrieve": UserRole.all_roles(),
        "specifications": UserRole.all_roles(),
        "performance": UserRole.all_roles(),
        "carrier_scorecard": UserRole.all_roles(),
        "create": [UserRole.SYSTEM_ADMIN.value, UserRole.PROCUREMENT_OFFICER.value],
        "update": [UserRole.SYSTEM_ADMIN.value, UserRole.PROCUREMENT_OFFICER.value],
        "partial_update": [UserRole.SYSTEM_ADMIN.value, UserRole.PROCUREMENT_OFFICER.value],
        "destroy": [UserRole.SYSTEM_ADMIN.value],
        "seed_baltic_fixtures": [UserRole.SYSTEM_ADMIN.value, UserRole.PROCUREMENT_OFFICER.value],
    }

    def get_queryset(self):
        qs = super().get_queryset()
        status_param = self.request.query_params.get("status")
        cargo_param = self.request.query_params.get("cargo_type")
        plant_param = self.request.query_params.get("plant")
        port_param = self.request.query_params.get("discharge_port")

        if status_param:
            qs = qs.filter(contract_status=status_param.upper())
        if cargo_param:
            qs = qs.filter(cargo_type=cargo_param.upper())
        if plant_param:
            qs = qs.filter(charterer_plant__icontains=plant_param)
        if port_param:
            qs = qs.filter(discharge_port__icontains=port_param)

        return qs

    @action(detail=True, methods=["get"], url_path="specifications")
    def specifications(self, request, pk=None):
        """
        Derive and return full mathematical charter party specifications.
        GET /api/v1/contracts/{id}/specifications/
        """
        contract = self.get_object()
        specs = contract.compute_specifications()
        serializer = ContractSpecificationSerializer(data=specs)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)

    @action(detail=False, methods=["post"], url_path="seed-baltic-fixtures")
    def seed_baltic_fixtures(self, request):
        """
        Seed database with Baltic Exchange standard Capesize, Panamax, and Supramax fixtures.
        POST /api/v1/contracts/seed-baltic-fixtures/
        """
        scenarios = MaritimeFixtureFactory.get_all_scenarios()
        created_count = 0

        for sc in scenarios:
            vf = sc.vessel
            vessel_obj, _ = Vessel.objects.get_or_create(
                imo_number=vf.imo_number,
                defaults={
                    "vessel_name": vf.vessel_name,
                    "flag_state": vf.flag_state,
                    "size_class": vf.size_class.value,
                    "deadweight_tonnage": vf.deadweight_tonnage,
                    "gross_tonnage": vf.gross_tonnage,
                    "net_tonnage": vf.net_tonnage,
                    "length_overall_meters": vf.length_overall_meters,
                    "beam_meters": vf.beam_meters,
                    "draft_meters": vf.draft_meters,
                    "hold_count": vf.hold_count,
                    "hatch_count": vf.hatch_count,
                    "build_year": vf.build_year,
                    "owner_name": vf.owner_name,
                    "service_speed_knots": vf.service_speed_knots,
                    "is_active": vf.is_active,
                },
            )

            cf = sc.contract
            _, created = CharterContract.objects.get_or_create(
                contract_reference=cf.contract_reference,
                defaults={
                    "charter_party_type": cf.charter_party_type.value,
                    "contract_status": cf.contract_status.value,
                    "charterer_entity": cf.charterer_entity,
                    "charterer_plant": cf.charterer_plant,
                    "vessel": vessel_obj,
                    "cargo_type": cf.cargo_type.value,
                    "nominated_quantity_mt": cf.nominated_quantity_mt,
                    "tolerance_pct": cf.tolerance_pct,
                    "freight_rate_usd_per_mt": cf.freight_rate_usd_per_mt,
                    "load_port": sc.voyage.load_port,
                    "discharge_port": cf.discharge_port,
                    "discharge_berth_designated": cf.discharge_berth_designated,
                    "discharge_rate_mt_per_day": cf.discharge_rate_mt_per_day,
                    "laytime_terms": cf.laytime_terms.value,
                    "demurrage_rate_usd_per_day": cf.demurrage_rate_usd_per_day,
                    "despatch_rate_usd_per_day": cf.despatch_rate_usd_per_day,
                    "laycan_start": cf.laycan_start,
                    "laycan_end": cf.laycan_end,
                    "turn_time_hours": cf.turn_time_hours,
                    "once_on_demurrage_always_on": cf.once_on_demurrage_always_on,
                },
            )
            if created:
                created_count += 1

        return Response(
            {"message": f"Successfully seeded {created_count} Baltic Exchange fixture contract(s).", "total_scenarios": len(scenarios)},
            status=status.HTTP_201_CREATED,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Cycle 2.5.3 — GET /api/v1/contracts/{id}/performance/
    # Charter Contract Fulfillment Scorecard
    # ──────────────────────────────────────────────────────────────────────
    @action(detail=True, methods=["get"], url_path="performance")
    def performance(self, request, pk=None):
        """
        Derive contract commitment fulfillment, lifted volume vs tolerance band,
        remaining commitments, and financial laytime summary.
        GET /api/v1/contracts/{id}/performance/
        """
        contract = self.get_object()
        snapshot = compute_contract_performance(contract)
        serializer = ContractPerformanceSerializer(snapshot)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # ──────────────────────────────────────────────────────────────────────
    # Cycle 2.5.3 — GET /api/v1/contracts/carrier-scorecard/
    # Carrier / Vessel Owner Reliability Ranking Scorecard
    # ──────────────────────────────────────────────────────────────────────
    @action(detail=False, methods=["get"], url_path="carrier-scorecard")
    def carrier_scorecard(self, request):
        """
        Ranked performance and reliability scorecard across all vessel owners / carriers.
        GET /api/v1/contracts/carrier-scorecard/

        Optional Query Parameters:
          - owner       : filter by carrier/owner name (case-insensitive substring)
          - min_voyages : filter out carriers with fewer voyages (integer)
        """
        scorecards = compute_carrier_scorecards()

        owner_param = request.query_params.get("owner")
        min_voyages_param = request.query_params.get("min_voyages")

        if owner_param:
            scorecards = [s for s in scorecards if owner_param.lower() in s.owner_name.lower()]

        if min_voyages_param:
            try:
                min_v = int(min_voyages_param)
                scorecards = [s for s in scorecards if s.total_voyages >= min_v]
            except ValueError:
                pass

        serializer = CarrierScorecardSerializer(scorecards, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

