"""
backend/config/celery_topology.py
============================================================
Celery Worker Topology & Queue Router Introspection.
Verifies queue segregation between:
  - 'calculations' (heavy mathematical tasks)
  - 'documents' (asynchronous digest verification)
  - 'alerts' (high-priority notifications)
  - 'default' (system maintenance)
"""

from typing import Dict, Any, Optional
from django.conf import settings
from config.celery import app as celery_app


def resolve_task_routing(task_name: str) -> Dict[str, str]:
    """
    Determines the target queue and routing key for a given task name
    by evaluating CELERY_TASK_ROUTES against the Celery router.
    """
    router = celery_app.amqp.router
    options = router.route(options={}, name=task_name)
    queue = options.get("queue")
    if not queue:
        # Fallback to default queue setting
        queue = getattr(settings, "CELERY_TASK_DEFAULT_QUEUE", "default")
    routing_key = options.get("routing_key", f"{queue}.#")
    return {
        "task_name": task_name,
        "queue": queue.name if hasattr(queue, "name") else str(queue),
        "routing_key": routing_key,
    }


def get_queue_topology_summary() -> Dict[str, Any]:
    """
    Extracts authoritative configuration metadata describing the Celery broker
    and worker topology.
    """
    queues = []
    for q in getattr(settings, "CELERY_TASK_QUEUES", []):
        queues.append({
            "name": q.name,
            "exchange": q.exchange.name if q.exchange else None,
            "routing_key": q.routing_key,
        })

    return {
        "broker_url": getattr(settings, "CELERY_BROKER_URL", ""),
        "result_backend": getattr(settings, "CELERY_RESULT_BACKEND", ""),
        "default_queue": getattr(settings, "CELERY_TASK_DEFAULT_QUEUE", "default"),
        "task_serializer": getattr(settings, "CELERY_TASK_SERIALIZER", "json"),
        "result_serializer": getattr(settings, "CELERY_RESULT_SERIALIZER", "json"),
        "timezone": getattr(settings, "CELERY_TIMEZONE", "UTC"),
        "acks_late": getattr(settings, "CELERY_TASK_ACKS_LATE", True),
        "prefetch_multiplier": getattr(settings, "CELERY_WORKER_PREFETCH_MULTIPLIER", 1),
        "always_eager": getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False),
        "queues": queues,
        "registered_tasks": sorted(list(celery_app.tasks.keys())),
    }
