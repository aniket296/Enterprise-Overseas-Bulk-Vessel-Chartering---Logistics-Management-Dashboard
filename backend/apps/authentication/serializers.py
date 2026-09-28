"""
backend/apps/authentication/serializers.py
============================================================
Serializers for User Authentication & RBAC Profiles.
"""

from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import UserProfile, get_user_role
from .roles import get_role_capabilities

User = get_user_model()


class UserProfileSerializer(serializers.ModelSerializer):
    username = serializers.ReadOnlyField(source="user.username")
    email = serializers.ReadOnlyField(source="user.email")
    first_name = serializers.ReadOnlyField(source="user.first_name")
    last_name = serializers.ReadOnlyField(source="user.last_name")
    capabilities = serializers.SerializerMethodField()

    class Meta:
        model = UserProfile
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "role",
            "organization",
            "phone_number",
            "is_active",
            "capabilities",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_capabilities(self, obj: UserProfile) -> dict:
        return get_role_capabilities(obj.role)


class CurrentUserSerializer(serializers.Serializer):
    """Serializer for the /auth/me/ inspection endpoint."""
    user_id = serializers.IntegerField(allow_null=True)
    username = serializers.CharField()
    email = serializers.CharField(allow_blank=True)
    role = serializers.CharField()
    organization = serializers.CharField()
    capabilities = serializers.DictField()
