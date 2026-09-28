"""
backend/apps/inventory/apps.py
============================================================
App configuration for Steel Plant Inventory & Runway tracking.
"""

from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inventory"
    verbose_name = "Steel Plant Inventory & Runway"
