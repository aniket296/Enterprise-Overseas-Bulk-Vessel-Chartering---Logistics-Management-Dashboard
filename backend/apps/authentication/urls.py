"""
backend/apps/authentication/urls.py
============================================================
URL Routing for Authentication & RBAC.
"""

from django.urls import path
from .views import CurrentUserView

app_name = "authentication"

urlpatterns = [
    path("me/", CurrentUserView.as_view(), name="current-user"),
]
