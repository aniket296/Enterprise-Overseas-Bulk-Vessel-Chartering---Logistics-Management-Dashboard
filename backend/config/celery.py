"""
backend/config/celery.py
============================================================
Celery Application and Worker Topology for Bulk Vessel Chartering.
Provides segregated queues:
  - 'calculations': Heavy mathematical laytime and settlement operations
  - 'documents': Asynchronous SHA-256 digest computation & vault sync
  - 'alerts': High-priority operational alerts and stock watchdog triggers
  - 'default': General maintenance and fallback tasks
"""

import os
from celery import Celery
from kombu import Queue, Exchange

# Set default Django settings module
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("bulk_vessel_chartering")

# Load configuration from Django settings using the 'CELERY_' prefix
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks from all installed Django apps
app.autodiscover_tasks()


@app.task(bind=True, name="config.celery.debug_task", ignore_result=True)
def debug_task(self):
    """Diagnostic task for worker liveness checks."""
    print(f"Request: {self.request!r}")
    return {"status": "ok", "task_id": self.request.id}
