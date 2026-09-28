"""
backend/apps/laytime/tasks.py
============================================================
Celery tasks routed to the dedicated 'calculations' queue.
Handles asynchronous laytime calculations and drift invariance checks.
"""

from celery import shared_task
from django.utils import timezone


@shared_task(name="apps.laytime.tasks.ping_calculation_worker")
def ping_calculation_worker():
    """Diagnostic task confirming calculations worker queue liveness."""
    return {
        "queue": "calculations",
        "status": "ONLINE",
        "timestamp": timezone.now().isoformat(),
    }


@shared_task(name="apps.laytime.tasks.recalculate_laytime_async")
def recalculate_laytime_async(voyage_id: str, trigger_source: str = "CELERY_EVENT"):
    """
    Asynchronous laytime recomputation task offloaded from the HTTP request thread.
    Executed when new SOF events are ingested.
    """
    from apps.voyages.models import VesselVoyage
    from apps.voyages.laytime_balance import compute_laytime_balance, LaytimeBalanceError

    try:
        voyage = VesselVoyage.objects.select_related("contract", "vessel").get(voyage_id=voyage_id)
        snapshot = compute_laytime_balance(voyage)
        return {
            "voyage_id": str(voyage.voyage_id),
            "outcome": snapshot.outcome,
            "balance_hours": str(snapshot.balance_hours),
            "net_settlement_usd": str(snapshot.net_settlement_usd),
            "trigger_source": trigger_source,
            "calculated_at": timezone.now().isoformat(),
            "status": "SUCCESS",
        }
    except LaytimeBalanceError as lbe:
        return {
            "voyage_id": voyage_id,
            "error": lbe.detail,
            "error_code": lbe.code,
            "status": "SKIPPED_PREREQUISITES",
        }
    except Exception as exc:
        return {
            "voyage_id": voyage_id,
            "error": str(exc),
            "status": "FAILED",
        }
