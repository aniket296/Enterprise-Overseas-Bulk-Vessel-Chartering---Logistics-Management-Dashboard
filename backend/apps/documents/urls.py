"""
backend/apps/documents/urls.py
============================================================
URL Routing for Shipping Documents Vault API.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ShippingDocumentViewSet

router = DefaultRouter()
router.register(r"documents", ShippingDocumentViewSet, basename="shipping-documents")

urlpatterns = [
    path("", include(router.urls)),
]
