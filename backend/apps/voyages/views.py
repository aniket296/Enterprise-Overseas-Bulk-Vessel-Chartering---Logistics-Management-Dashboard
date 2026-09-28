"""
backend/apps/voyages/views.py
============================================================
DRF ViewSets for Vessel Voyages & Active Fleet Tracking.
"""

from decimal import Decimal
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import VesselVoyage
from .serializers import VesselVoyageSerializer, ActiveFleetVoyageSerializer
from apps.laytime.engine.fixture_factory import VoyageStatus


class VesselVoyageViewSet(viewsets.ModelViewSet):
    """
    CRUD API for Vessel Voyages and Active Fleet Query.
    Endpoints:
      - /api/v1/voyages/
      - /api/v1/voyages/active/
    """
    queryset = VesselVoyage.objects.select_related("vessel", "contract").all()
    serializer_class = VesselVoyageSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "voyage_reference",
        "vessel__vessel_name",
        "vessel__imo_number",
        "contract__contract_reference",
        "contract__charterer_plant",
        "discharge_port",
        "load_port",
    ]
    ordering_fields = ["eta_date", "ata_date", "quantity_loaded_mt", "created_at"]
    ordering = ["-eta_date"]

    def get_queryset(self):
        qs = super().get_queryset()
        status_param = self.request.query_params.get("status")
        port_param = self.request.query_params.get("discharge_port")
        plant_param = self.request.query_params.get("plant")
        cargo_param = self.request.query_params.get("cargo_type")

        if status_param:
            qs = qs.filter(voyage_status=status_param.upper())
        if port_param:
            qs = qs.filter(discharge_port__icontains=port_param)
        if plant_param:
            qs = qs.filter(contract__charterer_plant__icontains=plant_param)
        if cargo_param:
            qs = qs.filter(contract__cargo_type=cargo_param.upper())

        return qs

    @action(detail=False, methods=["get"], url_path="active")
    def active_fleet(self, request):
        """
        GET /api/v1/voyages/active/
        Query all active bulk vessels underway or currently discharging at port.
        Excludes COMPLETED and CANCELLED voyages.
        Returns fleet data grid rows + executive summary metrics.
        """
        active_qs = self.get_queryset().exclude(
            voyage_status__in=[VoyageStatus.COMPLETED.value, VoyageStatus.CANCELLED.value]
        )

        serializer = ActiveFleetVoyageSerializer(active_qs, many=True)
        fleet_data = serializer.data

        # Aggregate executive KPI metrics
        total_tonnage = sum(
            (Decimal(str(v["quantity_loaded_mt"])) for v in fleet_data if v["quantity_loaded_mt"]),
            Decimal("0.000"),
        )
        at_anchor = sum(
            1 for v in fleet_data if v["voyage_status"] in (VoyageStatus.ARRIVED.value, VoyageStatus.NOR_TENDERED.value)
        )
        at_berth = sum(
            1 for v in fleet_data if v["voyage_status"] in (
                VoyageStatus.BERTHED.value,
                VoyageStatus.COMMENCING.value,
                VoyageStatus.DISCHARGING.value,
            )
        )
        in_transit = sum(
            1 for v in fleet_data if v["voyage_status"] == VoyageStatus.IN_TRANSIT.value
        )

        response_payload = {
            "summary": {
                "total_active_voyages": len(fleet_data),
                "total_loaded_mt_underway": str(total_tonnage),
                "vessels_in_transit_count": in_transit,
                "vessels_at_anchor_count": at_anchor,
                "vessels_at_berth_count": at_berth,
            },
            "fleet": fleet_data,
        }
        return Response(response_payload, status=status.HTTP_200_OK)
