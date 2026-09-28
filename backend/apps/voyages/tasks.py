"""
backend/apps/voyages/tasks.py
============================================================
Celery tasks routed to 'calculations' and 'alerts' queues.
Handles background berth conflict monitoring and voyage milestone telemetry.
"""

from celery import shared_task
from django.utils import timezone
from apps.voyages.berth_conflict import sweep_all_active_berth_conflicts


@shared_task(
    name="apps.voyages.tasks.compute_berth_conflicts_async",
    queue="calculations",
)
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
                "berth": c.discharge_berth,
                "port": c.discharge_port,
                "overlap_hours": str(c.overlap_hours),
                "voyage_a_id": str(c.voyage_id_a),
                "voyage_b_id": str(c.voyage_id_b),
            }
            for c in conflicts
        ],
        "scanned_at": timezone.now().isoformat(),
    }


@shared_task(
    name="apps.voyages.tasks.alert_active_berth_conflicts_async",
    queue="alerts",
)
def alert_active_berth_conflicts_async(discharge_port: str = None, discharge_berth: str = None):
    """
    Periodic and event-driven watchdog task for berth collisions and scheduling overlaps.
    Routed to 'alerts' queue.
    """
    conflicts = sweep_all_active_berth_conflicts(discharge_port=discharge_port, discharge_berth=discharge_berth)
    return {
        "conflicts_count": len(conflicts),
        "conflicts": [
            {
                "berth": c.discharge_berth,
                "port": c.discharge_port,
                "overlap_hours": str(c.overlap_hours),
                "voyage_a_id": str(c.voyage_id_a),
                "voyage_a_reference": c.voyage_ref_a,
                "voyage_b_id": str(c.voyage_id_b),
                "voyage_b_reference": c.voyage_ref_b,
                "vessel_b_name": c.vessel_name_b,
                "overlap_start": c.overlap_start.isoformat(),
                "overlap_end": c.overlap_end.isoformat(),
                "severity": c.severity,
            }
            for c in conflicts
        ],
        "scanned_at": timezone.now().isoformat(),
        "status": "ALERT_TRIGGERED" if conflicts else "CLEAR",
    }


@shared_task(
    name="apps.voyages.tasks.alert_voyage_status_change",
    queue="alerts",
)
def alert_voyage_status_change(voyage_id: str, old_status: str, new_status: str):
    """
    Asynchronous alert broadcast for voyage status state machine transitions.
    Routed to 'alerts' queue.
    """
    from apps.voyages.models import VesselVoyage
    try:
        voyage = VesselVoyage.objects.select_related("vessel", "contract").get(voyage_id=voyage_id)
        return {
            "voyage_id": str(voyage.voyage_id),
            "voyage_reference": voyage.voyage_reference,
            "vessel_name": voyage.vessel.vessel_name,
            "old_status": old_status,
            "new_status": new_status,
            "alert_timestamp": timezone.now().isoformat(),
            "status": "SENT",
        }
    except Exception as exc:
        return {"voyage_id": voyage_id, "error": str(exc), "status": "FAILED"}


@shared_task(
    name="apps.voyages.tasks.monitor_voyage_eta_deviations_async",
    queue="alerts",
)
def monitor_voyage_eta_deviations_async():
    """
    Periodic watchdog that identifies voyages whose ETA has elapsed while the
    voyage is still in an active (non-terminal) operational status.

    A voyage is considered ETA-deviated when:
      - voyage_status is in the set of active (non-terminal) statuses, AND
      - eta_date < now()  (the ETA window has passed), AND
      - ata_date is NULL  (the vessel has not actually arrived yet)

    Terminal statuses excluded from the scan: COMPLETED, CANCELLED.

    Returns a structured deviation report listing all overdue voyages with
    the deviation magnitude in hours, suitable for alert dispatch and
    downstream notification integration.

    Routed to 'alerts' queue.
    """
    from apps.voyages.models import VesselVoyage
    from apps.laytime.engine.fixture_factory import VoyageStatus

    ACTIVE_STATUSES = [
        VoyageStatus.SCHEDULED.value,
        VoyageStatus.IN_TRANSIT.value,
        VoyageStatus.ARRIVED.value,
        VoyageStatus.NOR_TENDERED.value,
        VoyageStatus.BERTHED.value,
        VoyageStatus.COMMENCING.value,
        VoyageStatus.DISCHARGING.value,
    ]

    now = timezone.now()

    overdue_voyages = (
        VesselVoyage.objects.select_related("vessel", "contract")
        .filter(
            voyage_status__in=ACTIVE_STATUSES,
            eta_date__lt=now,
            ata_date__isnull=True,
        )
        .order_by("eta_date")
    )

    deviations = []
    for voyage in overdue_voyages:
        deviation_hours = (now - voyage.eta_date).total_seconds() / 3600.0
        deviations.append({
            "voyage_id": str(voyage.voyage_id),
            "voyage_reference": voyage.voyage_reference,
            "vessel_name": voyage.vessel.vessel_name,
            "discharge_port": voyage.discharge_port,
            "voyage_status": voyage.voyage_status,
            "eta_date": voyage.eta_date.isoformat(),
            "deviation_hours": round(deviation_hours, 2),
            "severity": (
                "CRITICAL" if deviation_hours >= 72
                else "HIGH" if deviation_hours >= 24
                else "MODERATE" if deviation_hours >= 6
                else "LOW"
            ),
        })

    return {
        "scanned_at": now.isoformat(),
        "total_active_voyages_scanned": overdue_voyages.count(),
        "deviated_voyage_count": len(deviations),
        "deviations": deviations,
        "status": "DEVIATIONS_DETECTED" if deviations else "ALL_ON_SCHEDULE",
    }

