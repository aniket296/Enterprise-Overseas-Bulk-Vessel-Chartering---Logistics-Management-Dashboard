"""
backend/apps/laytime/urls.py
============================================================
URL routing for Laytime & Demurrage Calculations API.
"""

from django.urls import path
from .views import ComputeLaytimeAPIView

urlpatterns = [
    path("calculations/compute-laytime", ComputeLaytimeAPIView.as_view(), name="compute-laytime-noslash"),
    path("calculations/compute-laytime/", ComputeLaytimeAPIView.as_view(), name="compute-laytime"),
]
