"""
backend/apps/inventory/tasks.py
============================================================
Celery tasks routed to the dedicated 'alerts' queue.
Handles background stock runway watchdog checks, critical inventory alerts,
and blast furnace replenishment triage.
"""

from celery import shared_task
from django.utils import timezone
from apps.inventory.models import PlantInventory, StockSafetyStatus
from apps.inventory.runway_engine import compute_runway_analysis, get_plant_health_summary


@shared_task(name="apps.inventory.tasks.ping_alert_worker", queue="alerts")
def ping_alert_worker():
    """Diagnostic task confirming alerts worker queue liveness."""
    return {
        "queue": "alerts",
        "status": "ONLINE",
        "timestamp": timezone.now().isoformat(),
    }


@shared_task(
    name="apps.inventory.tasks.check_critical_stock_alerts_async",
    queue="alerts",
)
def check_critical_stock_alerts_async():
    """
    Scans plant inventory for critical stock safety statuses and returns active warnings.
    Matches partial index idx_inventory_safety_status.
    Evaluates blast furnace starvation risks using pure domain runway math.
    """
    critical_items = PlantInventory.objects.filter(
        safety_status__in=[
            StockSafetyStatus.CRITICAL.value,
            StockSafetyStatus.PRECAUTIONARY.value,
        ]
    ).select_related("contributing_voyage").order_by("stock_runway_days")

    alerts = []
    critical_count = 0
    precautionary_count = 0

    for item in critical_items:
        analysis = compute_runway_analysis(item)
        is_critical = item.safety_status == StockSafetyStatus.CRITICAL.value
        if is_critical:
            critical_count += 1
            severity = "HIGH"
        else:
            precautionary_count += 1
            severity = "MEDIUM"

        alerts.append({
            "inventory_id": str(item.inventory_id),
            "plant_code": item.plant_code,
            "plant_name": item.plant_name,
            "material_category": item.material_category,
            "safety_status": item.safety_status,
            "severity": severity,
            "stock_runway_days": str(item.stock_runway_days) if item.stock_runway_days is not None else None,
            "physical_runway_days": str(analysis.physical_runway_days),
            "total_runway_days": str(analysis.total_runway_days),
            "closing_stock_mt": str(item.closing_stock_mt),
            "daily_consumption_rate_mt": str(item.daily_consumption_rate_mt) if item.daily_consumption_rate_mt else None,
            "critical_stockout_date": analysis.critical_stockout_date.isoformat() if analysis.critical_stockout_date else None,
            "recommended_action": analysis.recommended_action,
            "incoming_voyages_count": len(analysis.incoming_voyages),
        })

    if critical_count > 0:
        overall_status = "CRITICAL_ALERT"
    elif precautionary_count > 0:
        overall_status = "WARNING"
    else:
        overall_status = "HEALTHY"

    return {
        "status": overall_status,
        "total_alerts": len(alerts),
        "critical_count": critical_count,
        "precautionary_count": precautionary_count,
        "alerts": alerts,
        "scanned_at": timezone.now().isoformat(),
    }


@shared_task(
    name="apps.inventory.tasks.evaluate_plant_runway_alert_async",
    queue="alerts",
)
def evaluate_plant_runway_alert_async(plant_code: str):
    """
    Evaluates consolidated raw material health summary for a specific integrated steel plant.
    """
    code = plant_code.strip().upper()
    summary = get_plant_health_summary(code)
    return {
        "plant_code": summary.plant_code,
        "plant_name": summary.plant_name,
        "plant_location": summary.plant_location,
        "overall_health": summary.overall_health,
        "total_materials_tracked": summary.total_materials_tracked,
        "critical_count": summary.critical_count,
        "precautionary_count": summary.precautionary_count,
        "bottleneck_material": summary.bottleneck_material,
        "lowest_runway_days": str(summary.lowest_runway_days) if summary.lowest_runway_days is not None else None,
        "evaluated_at": summary.evaluated_at.isoformat(),
    }
