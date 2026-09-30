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
    path("voyages/active", VesselVoyageViewSet.as_view({"get": "active_fleet"}), name="voyage-active-noslash"),
    path("voyages/<uuid:pk>/sof", VesselVoyageViewSet.as_view({"get": "sof", "post": "sof"}), name="voyage-sof-noslash"),
    path("voyages/<uuid:pk>/berth", VesselVoyageViewSet.as_view({"patch": "berth", "post": "berth"}), name="voyage-berth-noslash"),
    path("", include(router.urls)),
]
