"""
backend/apps/inventory/urls.py
============================================================
URL routing for Steel Plant Inventory & Blast Furnace Runway API.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PlantInventoryViewSet

router = DefaultRouter()
router.register(r"inventory", PlantInventoryViewSet, basename="inventory")

urlpatterns = [
    path("", include(router.urls)),
]
