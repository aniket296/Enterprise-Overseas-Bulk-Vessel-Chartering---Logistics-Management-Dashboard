"""
backend/apps/authentication/views.py
============================================================
Authentication & RBAC Inspection Views.
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import get_user_model

from .models import get_user_role
from .roles import UserRole, get_role_capabilities
from .serializers import CurrentUserSerializer

User = get_user_model()


class CurrentUserView(APIView):
    """
    GET /api/v1/auth/me/
    Returns the authenticated user identity, role, and frontend capabilities matrix.
    """

    def get(self, request):
        user = request.user
        role = getattr(request, "user_role", None) or get_user_role(user)

        if user and user.is_authenticated:
            profile = getattr(user, "profile", None)
            organization = profile.organization if profile else "Steel Authority of India Ltd (SAIL)"
            email = user.email
            username = user.username
            user_id = user.id
        else:
            organization = "Unauthenticated Session"
            email = ""
            username = "anonymous"
            user_id = None

        payload = {
            "user_id": user_id,
            "username": username,
            "email": email,
            "role": role,
            "organization": organization,
            "capabilities": get_role_capabilities(role),
        }
        return Response(CurrentUserSerializer(payload).data, status=status.HTTP_200_OK)
