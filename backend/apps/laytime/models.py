"""
backend/apps/laytime/models.py
============================================================
Re-exports LaytimeCalculation and LaytimeRecord from apps.voyages.models
for cross-app domain consistency.
"""

from apps.voyages.models import LaytimeCalculation, LaytimeRecord

__all__ = ["LaytimeCalculation", "LaytimeRecord"]
