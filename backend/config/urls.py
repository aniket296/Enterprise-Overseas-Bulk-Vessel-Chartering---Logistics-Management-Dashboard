"""
Root URL Configuration for Enterprise Overseas Bulk Vessel Chartering Dashboard.
"""

from django.contrib import admin
from django.urls import path, include
from .views import celery_topology_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.contracts.urls")),
    path("api/v1/", include("apps.voyages.urls")),
    path("api/v1/", include("apps.inventory.urls")),
    path("api/v1/", include("apps.documents.urls")),
    path("api/v1/", include("apps.laytime.urls")),
    path("api/v1/auth/", include("apps.authentication.urls")),
    path("api/v1/system/celery/topology/", celery_topology_view, name="celery-topology"),
]
