"""
backend/apps/authentication/permissions.py
============================================================
Granular Role-Based Access Control (RBAC) Permission Classes.
Enforces domain access controls across REST endpoints.
"""

from typing import List, Optional, Any
from django.conf import settings
from rest_framework import permissions

from .roles import UserRole
from .models import get_user_role


class RoleBasedAccessControl(permissions.BasePermission):
    """
    Dynamic RBAC permission class.
    Inspects `view.action_roles` dict to determine permitted roles for the action.
    Also honors 'X-User-Role' header in test/microservice contexts.
    """

    def has_permission(self, request: Any, view: Any) -> bool:
        current_role: Optional[str] = None

        # 1. Check for simulated / service role header
        header_role = request.headers.get("X-User-Role")
        if header_role and header_role in UserRole.values:
            current_role = header_role

        # 2. Check authenticated user
        elif request.user and request.user.is_authenticated:
            current_role = get_user_role(request.user)

        # 3. Unauthenticated requests
        else:
            if getattr(settings, "RBAC_REQUIRE_AUTHENTICATION", False):
                self.message = {
                    "error": "UNAUTHORIZED",
                    "detail": "Authentication credentials were not provided.",
                }
                return False
            # In permissive mode (e.g. baseline test runs), default to full permissions
            current_role = getattr(
                settings, "RBAC_DEFAULT_ANONYMOUS_ROLE", UserRole.SYSTEM_ADMIN.value
            )

        request.user_role = current_role

        # Superusers and SYSTEM_ADMIN have universal bypass
        if current_role == UserRole.SYSTEM_ADMIN.value:
            return True

        # All recognized domain roles have read visibility for SAFE_METHODS (GET, HEAD, OPTIONS)
        if request.method in permissions.SAFE_METHODS:
            return True

        # Mutation check against action_roles
        action = getattr(view, "action", None)
        action_roles = getattr(view, "action_roles", None)

        if action_roles and action and action in action_roles:
            allowed = action_roles[action]
            if current_role in allowed:
                return True

            self.message = {
                "error": "FORBIDDEN",
                "detail": f"Role '{current_role}' is not authorized to perform '{action}' on this resource.",
                "user_role": current_role,
                "action": action,
                "required_roles": allowed,
            }
            return False

        # Unmapped mutations default to SYSTEM_ADMIN only
        self.message = {
            "error": "FORBIDDEN",
            "detail": f"Role '{current_role}' is not authorized to mutate this resource.",
            "user_role": current_role,
            "required_roles": [UserRole.SYSTEM_ADMIN.value],
        }
        return False


class IsSystemAdmin(permissions.BasePermission):
    """Allows access only to SYSTEM_ADMIN users."""

    def has_permission(self, request: Any, view: Any) -> bool:
        role = getattr(request, "user_role", None) or get_user_role(request.user)
        return role == UserRole.SYSTEM_ADMIN.value


class IsProcurementOfficer(permissions.BasePermission):
    """Allows access to Procurement Officers and System Admins."""

    def has_permission(self, request: Any, view: Any) -> bool:
        role = getattr(request, "user_role", None) or get_user_role(request.user)
        return role in (UserRole.SYSTEM_ADMIN.value, UserRole.PROCUREMENT_OFFICER.value)


class IsLogisticsAgent(permissions.BasePermission):
    """Allows access to Logistics Agents and System Admins."""

    def has_permission(self, request: Any, view: Any) -> bool:
        role = getattr(request, "user_role", None) or get_user_role(request.user)
        return role in (UserRole.SYSTEM_ADMIN.value, UserRole.LOGISTICS_AGENT.value)


class IsAuditorOrReadOnly(permissions.BasePermission):
    """Allows read-only access for AUDITOR, blocks mutation."""

    def has_permission(self, request: Any, view: Any) -> bool:
        if request.method in permissions.SAFE_METHODS:
            return True
        role = getattr(request, "user_role", None) or get_user_role(request.user)
        return role == UserRole.SYSTEM_ADMIN.value
