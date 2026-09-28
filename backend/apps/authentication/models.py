"""
backend/apps/authentication/models.py
============================================================
User Profile and Role Assignment.
Links standard Django User to domain-specific enterprise roles.
"""

from typing import Optional
from django.db import models
from django.conf import settings
from django.contrib.auth import get_user_model

from .roles import UserRole


class UserProfile(models.Model):
    """
    Profile extension linking auth_user to enterprise shipping role.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    role = models.CharField(
        max_length=32,
        choices=UserRole.choices,
        default=UserRole.READONLY.value,
        help_text="Operational RBAC role within the chartering & logistics workflow",
    )
    organization = models.CharField(
        max_length=128,
        default="Steel Authority of India Ltd (SAIL)",
        help_text="Organization or external company name",
    )
    phone_number = models.CharField(max_length=32, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_profiles"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user.username} [{self.role}] - {self.organization}"


def get_user_role(user: Optional[object]) -> str:
    """
    Safely resolves the UserRole string for any user instance or anonymous user.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return UserRole.READONLY.value

    if getattr(user, "is_superuser", False):
        return UserRole.SYSTEM_ADMIN.value

    profile = getattr(user, "profile", None)
    if profile and profile.role:
        return profile.role

    return UserRole.READONLY.value
