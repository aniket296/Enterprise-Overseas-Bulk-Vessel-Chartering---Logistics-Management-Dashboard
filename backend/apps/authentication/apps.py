"""
backend/apps/authentication/apps.py
"""

from django.apps import AppConfig


class AuthenticationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.authentication"
    verbose_name = "Authentication & Role-Based Access Control"
