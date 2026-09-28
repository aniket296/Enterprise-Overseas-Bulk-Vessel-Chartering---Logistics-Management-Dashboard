"""
backend/apps/voyages/tasks.py
============================================================
Celery tasks routed to 'calculations' and 'alerts' queues.
Handles background berth conflict monitoring and voyage milestone telemetry.
"""

from celery import shared_task
from django.utils import timezone
from apps.voyages.berth_conflict import sweep_all_active_berth_conflicts


@shared_task(name="apps.voyages.tasks.compute_berth_conflicts_async")
def compute_berth_conflicts_async(discharge_port: str = None, discharge_berth: str = None):
    """
    Asynchronous berth conflict sweep.
    Routed to 'calculations' queue.
    """
    conflicts = sweep_all_active_berth_conflicts(discharge_port=discharge_port, discharge_berth=discharge_berth)
    return {
        "total_conflicts": len(conflicts),
        "conflicts": [
            {
                "berth": c.berth,
                "port": c.port,
                "overlap_hours": str(c.overlap_duration_hours),
                "voyage_a_id": str(c.voyage_a.voyage_id),
                "voyage_b_id": str(c.voyage_b.voyage_id),
            }
            for c in conflicts
        ],
        "scanned_at": timezone.now().isoformat(),
    }


@shared_task(name="apps.voyages.tasks.alert_voyage_status_change")
def alert_voyage_status_change(voyage_id: str, old_status: str, new_status: str):
    """
    Asynchronous alert broadcast for voyage status state machine transitions.
    Routed to 'alerts' queue.
    """
    from apps.voyages.models import VesselVoyage
    try:
        voyage = VesselVoyage.objects.select_related("vessel", "contract").get(voyage_id=voyage_id)
        return {
            "voyage_reference": voyage.voyage_reference,
            "vessel_name": voyage.vessel.vessel_name,
            "old_status": old_status,
            "new_status": new_status,
            "alert_timestamp": timezone.now().isoformat(),
        }
    except Exception as exc:
        return {"voyage_id": voyage_id, "error": str(exc), "status": "FAILED"}
