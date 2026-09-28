"""
backend/apps/contracts/urls.py
============================================================
URL routing for Vessels and Charter Contracts.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import VesselViewSet, CharterContractViewSet

router = DefaultRouter()
router.register(r"vessels", VesselViewSet, basename="vessel")
router.register(r"contracts", CharterContractViewSet, basename="contract")

urlpatterns = [
    path("", include(router.urls)),
]
