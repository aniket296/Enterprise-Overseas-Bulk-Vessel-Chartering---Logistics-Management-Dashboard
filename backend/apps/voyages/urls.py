"""
backend/apps/voyages/urls.py
============================================================
URL routing for Vessel Voyages and Active Fleet.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import VesselVoyageViewSet

router = DefaultRouter()
router.register(r"voyages", VesselVoyageViewSet, basename="voyage")

urlpatterns = [
    path("", include(router.urls)),
]
