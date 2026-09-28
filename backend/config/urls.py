"""
Root URL Configuration for Enterprise Overseas Bulk Vessel Chartering Dashboard.
"""

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.contracts.urls")),
    path("api/v1/", include("apps.voyages.urls")),
]
