"""
backend/apps/inventory/views.py
============================================================
DRF ViewSets for Steel Plant Inventory & Blast Furnace Runway API.
"""

from decimal import Decimal, ROUND_HALF_UP
from django.utils import timezone
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.authentication.permissions import RoleBasedAccessControl
from apps.authentication.roles import UserRole
from .models import PlantInventory, StockSafetyStatus
from .inbound_forecast import simulate_forward_runway, InboundForecastError
from .starvation_simulator import (
    scan_multi_plant_starvation,
    simulate_starvation_mitigation,
    StarvationSimulatorError,
)
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
    PlantForwardRunwayForecastSerializer,
    PlantStarvationProfileSerializer,
    StarvationMitigationSimulationSerializer,
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
        "inbound_forecast": UserRole.all_roles(),
        "starvation_matrix": UserRole.all_roles(),
        "simulate_mitigation": UserRole.all_roles(),
        "reconciliation": UserRole.all_roles(),
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

    # ──────────────────────────────────────────────────────────────────────
    # GET /api/v1/inventory/inbound-forecast/
    # Forward-looking daily inventory depletion & vessel replenishment simulation
    # ──────────────────────────────────────────────────────────────────────
    @action(
        detail=False,
        methods=["get"],
        url_path="inbound-forecast",
        url_name="inbound-forecast",
    )
    def inbound_forecast(self, request):
        """
        GET /api/v1/inventory/inbound-forecast/?plant_code=BSP&material_category=COKING_COAL&horizon_days=30
        Forward-looking day-by-day inventory runway simulation.
        """
        plant_code = request.query_params.get("plant_code")
        if not plant_code or not plant_code.strip():
            return Response(
                {"error": "plant_code query parameter is required (e.g. ?plant_code=BSP)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        material_category = request.query_params.get("material_category", "COKING_COAL").strip().upper()
        horizon_days_raw = request.query_params.get("horizon_days", "30")

        try:
            horizon_days = int(horizon_days_raw)
            if horizon_days < 1 or horizon_days > 120:
                return Response(
                    {"error": "horizon_days must be an integer between 1 and 120."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except (ValueError, TypeError):
            return Response(
                {"error": "horizon_days must be a valid integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            forecast = simulate_forward_runway(
                plant_code=plant_code.strip().upper(),
                material_category=material_category,
                horizon_days=horizon_days,
            )
            serializer = PlantForwardRunwayForecastSerializer(forecast)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except InboundForecastError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {"error": f"Failed to compute inbound runway forecast: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ──────────────────────────────────────────────────────────────────────
    # GET /api/v1/inventory/starvation-matrix/
    # Multi-plant raw material stock runway scanner & imbalance matrix
    # ──────────────────────────────────────────────────────────────────────
    @action(
        detail=False,
        methods=["get"],
        url_path="starvation-matrix",
        url_name="starvation-matrix",
    )
    def starvation_matrix(self, request):
        """
        GET /api/v1/inventory/starvation-matrix/?material_category=COKING_COAL&target_safe_days=21
        Scans all 5 SAIL plants for inventory deficits and starvation risks.
        """
        from decimal import Decimal, InvalidOperation

        material_category = request.query_params.get("material_category", "COKING_COAL").strip().upper()
        target_days_raw = request.query_params.get("target_safe_days", "21")

        try:
            target_days = Decimal(str(target_days_raw))
            if target_days < Decimal("1.00") or target_days > Decimal("60.00"):
                return Response(
                    {"error": "target_safe_days must be between 1 and 60 days."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except (InvalidOperation, ValueError, TypeError):
            return Response(
                {"error": "target_safe_days must be a valid numeric decimal."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            profiles = scan_multi_plant_starvation(
                material_category=material_category,
                target_safe_days=target_days,
            )
            serializer = PlantStarvationProfileSerializer(profiles, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {"error": f"Failed to scan multi-plant starvation matrix: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ──────────────────────────────────────────────────────────────────────
    # GET /api/v1/inventory/simulate-mitigation/
    # Emergency Cargo Diversion, Spot Re-chartering & Rail Transfer Simulator
    # ──────────────────────────────────────────────────────────────────────
    @action(
        detail=False,
        methods=["get"],
        url_path="simulate-mitigation",
        url_name="simulate-mitigation",
    )
    def simulate_mitigation(self, request):
        """
        GET /api/v1/inventory/simulate-mitigation/?plant_code=RSP&material_category=COKING_COAL&target_safe_days=21
        Simulates emergency vessel diversion, spot re-chartering, and inter-plant rail transfers.
        """
        from decimal import Decimal, InvalidOperation

        plant_code = request.query_params.get("plant_code")
        if not plant_code or not plant_code.strip():
            return Response(
                {"error": "plant_code query parameter is required (e.g. ?plant_code=RSP)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        material_category = request.query_params.get("material_category", "COKING_COAL").strip().upper()
        target_days_raw = request.query_params.get("target_safe_days", "21")

        try:
            target_days = Decimal(str(target_days_raw))
            if target_days < Decimal("1.00") or target_days > Decimal("60.00"):
                return Response(
                    {"error": "target_safe_days must be between 1 and 60 days."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except (InvalidOperation, ValueError, TypeError):
            return Response(
                {"error": "target_safe_days must be a valid numeric decimal."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            simulation = simulate_starvation_mitigation(
                plant_code=plant_code.strip().upper(),
                material_category=material_category,
                target_safe_days=target_days,
            )
            serializer = StarvationMitigationSimulationSerializer(simulation)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except StarvationSimulatorError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {"error": f"Failed to simulate starvation mitigation: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["get"], url_path="reconciliation")
    def reconciliation(self, request):
        """
        GET /api/v1/inventory/reconciliation/
        Physical vs in-transit delivery inventory reconciliation across SAIL steel plants.
        """
        plant_param = request.query_params.get("plant") or request.query_params.get("plant_code")
        category_param = request.query_params.get("material_category", "COKING_COAL").strip().upper()

        qs = PlantInventory.objects.all().order_by("plant_code", "-stock_date")
        if plant_param and plant_param.strip().upper() != "ALL":
            qs = qs.filter(plant_code=plant_param.strip().upper())
        if category_param:
            qs = qs.filter(material_category=category_param)

        # Get latest record per plant
        seen_plants = set()
        latest_records = []
        for record in qs:
            if record.plant_code not in seen_plants:
                seen_plants.add(record.plant_code)
                latest_records.append(record)

        total_physical_stock = Decimal("0.000")
        total_burn_rate = Decimal("0.000")
        total_pipeline = Decimal("0.000")
        plant_reconciliations = []

        for r in latest_records:
            closing = r.closing_stock_mt or Decimal("0.000")
            opening = r.opening_stock_mt or Decimal("0.000")
            receipts = r.receipts_mt or Decimal("0.000")
            consumption = r.consumption_mt or Decimal("0.000")
            pipeline = r.projected_receipts_mt or Decimal("0.000")
            burn_rate = r.daily_consumption_rate_mt or Decimal("0.001")

            theoretical = opening + receipts - consumption
            variance = closing - theoretical
            runway = (closing / burn_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if burn_rate > 0 else Decimal("0.00")

            total_physical_stock += closing
            total_burn_rate += burn_rate
            total_pipeline += pipeline

            plant_reconciliations.append({
                "plant_code": r.plant_code,
                "plant_name": r.plant_name,
                "plant_location": r.plant_location,
                "material_category": r.material_category,
                "stock_date": r.stock_date.isoformat(),
                "opening_stock_mt": str(opening),
                "receipts_mt": str(receipts),
                "consumption_mt": str(consumption),
                "closing_stock_mt": str(closing),
                "theoretical_balance_mt": str(theoretical),
                "variance_mt": str(variance),
                "is_balanced": variance == Decimal("0.000"),
                "daily_burn_rate_mt": str(burn_rate),
                "projected_pipeline_mt": str(pipeline),
                "stock_runway_days": str(runway),
                "safety_status": r.safety_status,
            })

        avg_runway = (
            (total_physical_stock / total_burn_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if total_burn_rate > 0 else Decimal("0.00")
        )

        return Response({
            "status": "RECONCILED",
            "material_category": category_param,
            "total_plants_audited": len(plant_reconciliations),
            "network_totals": {
                "total_physical_stock_mt": str(total_physical_stock),
                "total_burn_rate_mt": str(total_burn_rate),
                "total_pipeline_inbound_mt": str(total_pipeline),
                "network_average_runway_days": str(avg_runway),
            },
            "plants": plant_reconciliations,
            "reconciled_at": timezone.now().isoformat(),
        }, status=status.HTTP_200_OK)



