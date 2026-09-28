"""
backend/apps/inventory/views.py
============================================================
DRF ViewSets for Steel Plant Inventory & Blast Furnace Runway API.
"""

from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.authentication.permissions import RoleBasedAccessControl
from apps.authentication.roles import UserRole
from .models import PlantInventory, StockSafetyStatus
from .runway_engine import (
    SAIL_PLANTS_CONFIG,
    compute_runway_analysis,
    get_plant_health_summary,
)
from .serializers import (
    PlantInventorySerializer,
    PlantInventoryCreateSerializer,
    RunwayAnalysisSerializer,
    PlantHealthSummarySerializer,
)


class PlantInventoryViewSet(viewsets.ModelViewSet):
    """
    Endpoints for Steel Plant Raw Material Inventory & Blast Furnace Runway tracking.
    """
    queryset = PlantInventory.objects.all().select_related("recorded_by", "contributing_voyage")
    serializer_class = PlantInventorySerializer
    permission_classes = [RoleBasedAccessControl]

    action_roles = {
        "list": UserRole.all_roles(),
        "retrieve": UserRole.all_roles(),
        "runway": UserRole.all_roles(),
        "critical_alerts": UserRole.all_roles(),
        "analysis": UserRole.all_roles(),
        "trigger_alert_scan": [UserRole.SYSTEM_ADMIN.value, UserRole.PLANT_MANAGER.value, UserRole.LOGISTICS_AGENT.value],
        "create": [UserRole.SYSTEM_ADMIN.value, UserRole.PLANT_MANAGER.value],
        "update": [UserRole.SYSTEM_ADMIN.value, UserRole.PLANT_MANAGER.value],
        "partial_update": [UserRole.SYSTEM_ADMIN.value, UserRole.PLANT_MANAGER.value],
        "destroy": [UserRole.SYSTEM_ADMIN.value, UserRole.PLANT_MANAGER.value],
    }

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return PlantInventoryCreateSerializer
        return PlantInventorySerializer

    def perform_create(self, serializer):
        user = self.request.user if self.request.user and self.request.user.is_authenticated else None
        serializer.save(recorded_by=user)

    def get_queryset(self):
        qs = super().get_queryset()
        plant = self.request.query_params.get("plant_code")
        material = self.request.query_params.get("material_category")
        safety = self.request.query_params.get("safety_status")
        stock_date = self.request.query_params.get("stock_date")

        if plant:
            qs = qs.filter(plant_code__iexact=plant.strip())
        if material:
            qs = qs.filter(material_category__iexact=material.strip())
        if safety:
            qs = qs.filter(safety_status__iexact=safety.strip())
        if stock_date:
            qs = qs.filter(stock_date=stock_date)

        return qs

    # ──────────────────────────────────────────────────────────────────────
    # GET /api/v1/inventory/runway/
    # Plant-wide or multi-plant runway assessment
    # ──────────────────────────────────────────────────────────────────────
    @action(detail=False, methods=["get"], url_path="runway", url_name="runway")
    def runway(self, request):
        """
        GET /api/v1/inventory/runway/
          If ?plant_code=BSP is provided: returns detailed material runway analyses for that plant.
          If plant_code is omitted: returns PlantHealthSummary across all SAIL plants.
        """
        plant_code = request.query_params.get("plant_code")

        if plant_code:
            code = plant_code.strip().upper()
            # Fetch latest snapshot per material category for this plant
            categories = (
                PlantInventory.objects
                .filter(plant_code__iexact=code)
                .values_list("material_category", flat=True)
                .distinct()
            )
            analyses = []
            for cat in categories:
                latest = (
                    PlantInventory.objects
                    .filter(plant_code__iexact=code, material_category=cat)
                    .order_by("-stock_date")
                    .first()
                )
                if latest:
                    analyses.append(compute_runway_analysis(latest))

            return Response(RunwayAnalysisSerializer(analyses, many=True).data, status=status.HTTP_200_OK)

        # Multi-plant health overview
        summaries = [get_plant_health_summary(code) for code in SAIL_PLANTS_CONFIG.keys()]
        return Response(PlantHealthSummarySerializer(summaries, many=True).data, status=status.HTTP_200_OK)

    # ──────────────────────────────────────────────────────────────────────
    # GET /api/v1/inventory/critical-alerts/
    # Filtered view of materials approaching blast furnace starvation
    # ──────────────────────────────────────────────────────────────────────
    @action(detail=False, methods=["get"], url_path="critical-alerts", url_name="critical-alerts")
    def critical_alerts(self, request):
        """
        GET /api/v1/inventory/critical-alerts/
          Returns snapshots currently in CRITICAL (< 7d) or PRECAUTIONARY (< 14d) status.
        """
        critical_statuses = [StockSafetyStatus.CRITICAL.value, StockSafetyStatus.PRECAUTIONARY.value]
        qs = (
            PlantInventory.objects
            .filter(safety_status__in=critical_statuses)
            .order_by("stock_runway_days")
        )
        analyses = [compute_runway_analysis(item) for item in qs]
        return Response(RunwayAnalysisSerializer(analyses, many=True).data, status=status.HTTP_200_OK)

    # ──────────────────────────────────────────────────────────────────────
    # GET /api/v1/inventory/{id}/analysis/
    # Detailed runway breakdown for a specific snapshot
    # ──────────────────────────────────────────────────────────────────────
    @action(detail=True, methods=["get"], url_path="analysis", url_name="analysis")
    def analysis(self, request, pk=None):
        """
        GET /api/v1/inventory/{id}/analysis/
          Detailed runway and incoming vessel supply chain linkage for this record.
        """
        snapshot = self.get_object()
        analysis_data = compute_runway_analysis(snapshot)
        return Response(RunwayAnalysisSerializer(analysis_data).data, status=status.HTTP_200_OK)

    # ──────────────────────────────────────────────────────────────────────
    # POST /api/v1/inventory/trigger-alert-scan/
    # Asynchronously dispatch critical inventory watchdog scan to 'alerts' queue
    # ──────────────────────────────────────────────────────────────────────
    @action(
        detail=False,
        methods=["post"],
        url_path="trigger-alert-scan",
        url_name="trigger-alert-scan",
    )
    def trigger_alert_scan(self, request):
        """
        POST /api/v1/inventory/trigger-alert-scan/
        Dispatches an asynchronous critical stock alerts watchdog sweep to the 'alerts' Celery queue.
        """
        from apps.inventory.tasks import check_critical_stock_alerts_async
        async_task = check_critical_stock_alerts_async.delay()
        return Response(
            {
                "task_id": async_task.id,
                "status": "QUEUED",
                "queue": "alerts",
            },
            status=status.HTTP_202_ACCEPTED,
        )
