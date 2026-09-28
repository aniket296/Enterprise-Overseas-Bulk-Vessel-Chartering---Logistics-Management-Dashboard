"""
backend/apps/laytime/tasks.py
============================================================
Celery tasks routed to the dedicated 'calculations' queue.
Handles asynchronous laytime calculations and drift invariance checks.
"""

import uuid
from celery import shared_task
from django.db import models, transaction
from django.utils import timezone


@shared_task(name="apps.laytime.tasks.ping_calculation_worker")
def ping_calculation_worker():
    """Diagnostic task confirming calculations worker queue liveness."""
    return {
        "queue": "calculations",
        "status": "ONLINE",
        "timestamp": timezone.now().isoformat(),
    }


@shared_task(
    name="apps.laytime.tasks.recalculate_laytime_async",
    bind=True,
    queue="calculations",
)
def recalculate_laytime_async(self, voyage_id: str, trigger_source: str = "CELERY_EVENT"):
    """
    Asynchronous laytime recomputation task offloaded from the HTTP request thread.
    Executed when new SOF events are ingested or requested on-demand via the API.
    
    Atomically calculates laytime balance via the pure mathematical engine,
    records a new revision of LaytimeCalculation, maintains previous revisions,
    and updates is_current flags.
    """
    from apps.voyages.models import VesselVoyage, LaytimeCalculation
    from apps.voyages.laytime_balance import compute_laytime_balance, LaytimeBalanceError
    from apps.laytime.engine.settlement_engine import LaytimeOutcome

    task_id = None
    if hasattr(self, "request") and self.request and self.request.id:
        task_id = str(self.request.id)
    if not task_id:
        task_id = str(uuid.uuid4())

    try:
        with transaction.atomic():
            try:
                voyage = (
                    VesselVoyage.objects.select_for_update()
                    .select_related("contract", "vessel")
                    .get(voyage_id=voyage_id)
                )
            except VesselVoyage.DoesNotExist:
                return {
                    "voyage_id": str(voyage_id),
                    "error": f"Voyage '{voyage_id}' does not exist.",
                    "status": "FAILED",
                    "trigger_source": trigger_source,
                }

            try:
                snapshot = compute_laytime_balance(voyage)
            except LaytimeBalanceError as lbe:
                return {
                    "voyage_id": str(voyage.voyage_id),
                    "error": lbe.detail,
                    "error_code": lbe.code,
                    "trigger_source": trigger_source,
                    "status": "SKIPPED_PREREQUISITES",
                }

            if snapshot.allowed_hours is None:
                return {
                    "voyage_id": str(voyage.voyage_id),
                    "error": "Contract has indefinite laytime terms (e.g. CQD). Allowed laytime hours cannot be computed.",
                    "error_code": "INDEFINITE_LAYTIME",
                    "trigger_source": trigger_source,
                    "status": "SKIPPED_PREREQUISITES",
                }

            existing_current = (
                LaytimeCalculation.objects.filter(voyage=voyage, is_current=True)
                .order_by("-revision")
                .first()
            )

            # Preserve locked settlement outcome and audit fields if already finalized
            if existing_current and existing_current.outcome in (
                LaytimeOutcome.SETTLED.value,
                LaytimeOutcome.DISPUTED.value,
            ):
                calc_outcome = existing_current.outcome
                settled_at = existing_current.settled_at
                settled_by = existing_current.settled_by
                settlement_notes = existing_current.settlement_notes
            else:
                calc_outcome = snapshot.outcome
                settled_at = None
                settled_by = None
                settlement_notes = None

            total_counted = snapshot.counted_hours + snapshot.demurrage_hours
            total_port = total_counted + snapshot.excepted_hours
            variance = snapshot.balance_hours

            sof_qs = voyage.sof_events.all().order_by("sequence_number", "event_timestamp")
            sof_count = sof_qs.count()
            first_sof = sof_qs.first()
            last_sof = sof_qs.last()

            max_rev = LaytimeCalculation.objects.filter(voyage=voyage).aggregate(
                models.Max("revision")
            )["revision__max"]
            next_rev = (max_rev or 0) + 1

            # Demote any current active calculations for this voyage
            LaytimeCalculation.objects.filter(voyage=voyage, is_current=True).update(is_current=False)

            calculation_record = LaytimeCalculation.objects.create(
                voyage=voyage,
                revision=next_rev,
                is_current=True,
                outcome=calc_outcome,
                calculated_at=snapshot.as_of_timestamp,
                celery_task_id=task_id,
                allowed_laytime_hours=snapshot.allowed_hours,
                total_port_hours=total_port,
                total_counted_hours=total_counted,
                total_excepted_hours=snapshot.excepted_hours,
                variance_hours=variance,
                demurrage_rate_usd_per_day=snapshot.demurrage_rate_usd_per_day,
                despatch_rate_usd_per_day=snapshot.despatch_rate_usd_per_day,
                gross_demurrage_usd=snapshot.gross_demurrage_usd,
                gross_despatch_usd=snapshot.gross_despatch_usd,
                net_settlement_usd=snapshot.net_settlement_usd,
                demurrage_commenced_at=snapshot.demurrage_commenced_at,
                sof_event_count=sof_count,
                first_sof_event_at=first_sof.event_timestamp if first_sof else None,
                last_sof_event_at=last_sof.event_timestamp if last_sof else None,
                settled_at=settled_at,
                settled_by=settled_by,
                settlement_notes=settlement_notes,
            )

            return {
                "voyage_id": str(voyage.voyage_id),
                "calculation_id": str(calculation_record.calc_id),
                "revision": calculation_record.revision,
                "outcome": calculation_record.outcome,
                "balance_hours": str(calculation_record.variance_hours),
                "net_settlement_usd": str(calculation_record.net_settlement_usd),
                "celery_task_id": task_id,
                "trigger_source": trigger_source,
                "calculated_at": calculation_record.calculated_at.isoformat() if calculation_record.calculated_at else None,
                "status": "SUCCESS",
            }
    except Exception as exc:
        return {
            "voyage_id": str(voyage_id),
            "error": str(exc),
            "trigger_source": trigger_source,
            "status": "FAILED",
        }
