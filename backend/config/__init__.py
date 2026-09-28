"""
backend/config/__init__.py
============================================================
Export Celery application instance so shared tasks can resolve it.
"""

from .celery import app as celery_app

__all__ = ("celery_app",)
