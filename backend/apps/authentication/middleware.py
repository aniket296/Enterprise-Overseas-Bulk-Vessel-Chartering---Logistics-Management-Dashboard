"""
backend/apps/authentication/middleware.py
============================================================
Middleware for RBAC context resolution and PostgreSQL Audit logging.
Injects 'app.current_user_id', 'app.current_user_role', and 'app.client_ip'
session settings consumed by trigger 'shipping.fn_audit_log'.
"""

from typing import Callable
from django.db import connection
from django.http import HttpRequest, HttpResponse

from .models import get_user_role
from .roles import UserRole


class AuditSessionContextMiddleware:
    """
    Sets session variables in PostgreSQL so database audit triggers
    (shipping.fn_audit_log) capture the authenticated user, role, and client IP.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # 1. Resolve role (header override or user profile)
        header_role = request.headers.get("X-User-Role")
        if header_role and header_role in UserRole.values:
            request.user_role = header_role
        else:
            request.user_role = get_user_role(getattr(request, "user", None))

        # 2. Extract Client IP
        x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded:
            client_ip = x_forwarded.split(",")[0].strip()
        else:
            client_ip = request.META.get("REMOTE_ADDR", "127.0.0.1")
        request.client_ip = client_ip

        # 3. Inject PostgreSQL session variables if using postgres engine
        engine = connection.settings_dict.get("ENGINE", "")
        if "postgresql" in engine:
            user_id = ""
            if getattr(request, "user", None) and request.user.is_authenticated:
                user_id = str(request.user.id)
            user_role = request.user_role or ""

            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL app.current_user_id = %s", [user_id])
                    cursor.execute("SET LOCAL app.current_user_role = %s", [user_role])
                    if client_ip:
                        cursor.execute("SET LOCAL app.client_ip = %s", [client_ip])
            except Exception:
                # Do not block request if database does not support custom settings
                pass

        return self.get_response(request)
