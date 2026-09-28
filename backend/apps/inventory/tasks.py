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


@shared_task(name="apps.inventory.tasks.ping_alert_worker")
def ping_alert_worker():
    """Diagnostic task confirming alerts worker queue liveness."""
    return {
        "queue": "alerts",
        "status": "ONLINE",
        "timestamp": timezone.now().isoformat(),
    }


@shared_task(name="apps.inventory.tasks.check_critical_stock_alerts_async")
def check_critical_stock_alerts_async():
    """
    Scans plant inventory for critical stock safety statuses and returns active warnings.
    Matches partial index idx_inventory_safety_status.
    """
    critical_items = PlantInventory.objects.filter(
        safety_status__in=[
            StockSafetyStatus.CRITICAL.value,
            StockSafetyStatus.PRECAUTIONARY.value,
        ]
    ).select_related("contributing_voyage")

    alerts = []
    for item in critical_items:
        alerts.append({
            "inventory_id": str(item.inventory_id),
            "plant_code": item.plant_code,
            "plant_name": item.plant_name,
            "material_category": item.material_category,
            "safety_status": item.safety_status,
            "stock_runway_days": str(item.stock_runway_days) if item.stock_runway_days else None,
            "closing_stock_mt": str(item.closing_stock_mt),
        })

    return {
        "critical_count": len(alerts),
        "alerts": alerts,
        "scanned_at": timezone.now().isoformat(),
    }
