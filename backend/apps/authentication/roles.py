"""
backend/apps/authentication/roles.py
============================================================
Domain User Roles and Capability Groupings.
Strictly maps to PostgreSQL enum 'shipping.user_role' (V001 DDL).
"""

from typing import List, Dict, Set
from django.db import models


class UserRole(models.TextChoices):
    """
    Standard user roles per PostgreSQL shipping.user_role.
    """
    SYSTEM_ADMIN = "SYSTEM_ADMIN", "System Administrator"
    PROCUREMENT_OFFICER = "PROCUREMENT_OFFICER", "Procurement Officer"
    LOGISTICS_AGENT = "LOGISTICS_AGENT", "Logistics & Port Agent"
    CHARTER_BROKER = "CHARTER_BROKER", "Charter Broker"
    FINANCE_OFFICER = "FINANCE_OFFICER", "Finance Officer"
    PLANT_MANAGER = "PLANT_MANAGER", "Plant Inventory Manager"
    AUDITOR = "AUDITOR", "Internal / Statutory Auditor"
    READONLY = "READONLY", "Read-Only Observer"

    @classmethod
    def all_roles(cls) -> List[str]:
        return [choice[0] for choice in cls.choices]


# Role Permission Clusters
CONTRACT_WRITE_ROLES: Set[str] = {
    UserRole.SYSTEM_ADMIN.value,
    UserRole.PROCUREMENT_OFFICER.value,
}

VOYAGE_WRITE_ROLES: Set[str] = {
    UserRole.SYSTEM_ADMIN.value,
    UserRole.LOGISTICS_AGENT.value,
    UserRole.PROCUREMENT_OFFICER.value,
}

VOYAGE_TRANSITION_ROLES: Set[str] = {
    UserRole.SYSTEM_ADMIN.value,
    UserRole.LOGISTICS_AGENT.value,
}

FINANCE_SETTLEMENT_ROLES: Set[str] = {
    UserRole.SYSTEM_ADMIN.value,
    UserRole.FINANCE_OFFICER.value,
}

PLANT_INVENTORY_ROLES: Set[str] = {
    UserRole.SYSTEM_ADMIN.value,
    UserRole.PLANT_MANAGER.value,
}

AUDIT_LOG_ROLES: Set[str] = {
    UserRole.SYSTEM_ADMIN.value,
    UserRole.AUDITOR.value,
}

DOCUMENT_WRITE_ROLES: Set[str] = {
    UserRole.SYSTEM_ADMIN.value,
    UserRole.LOGISTICS_AGENT.value,
    UserRole.PROCUREMENT_OFFICER.value,
    UserRole.CHARTER_BROKER.value,
}

DOCUMENT_APPROVE_ROLES: Set[str] = {
    UserRole.SYSTEM_ADMIN.value,
    UserRole.AUDITOR.value,
    UserRole.FINANCE_OFFICER.value,
}

ALL_READ_ROLES: Set[str] = set(UserRole.all_roles())


def get_role_capabilities(role: str) -> Dict[str, bool]:
    """
    Returns an actionable capabilities matrix for frontend UI permission gates.
    """
    is_admin = role == UserRole.SYSTEM_ADMIN.value
    return {
        "can_manage_contracts": is_admin or role == UserRole.PROCUREMENT_OFFICER.value,
        "can_manage_vessels": is_admin or role == UserRole.PROCUREMENT_OFFICER.value,
        "can_transition_voyages": is_admin or role == UserRole.LOGISTICS_AGENT.value,
        "can_enter_sof": is_admin or role == UserRole.LOGISTICS_AGENT.value,
        "can_settle_laytime": is_admin or role == UserRole.FINANCE_OFFICER.value,
        "can_manage_inventory": is_admin or role == UserRole.PLANT_MANAGER.value,
        "can_view_audit_logs": is_admin or role == UserRole.AUDITOR.value,
        "can_upload_documents": is_admin or role in DOCUMENT_WRITE_ROLES,
        "can_verify_documents": is_admin or role in DOCUMENT_APPROVE_ROLES,
        "is_read_only": role in (UserRole.AUDITOR.value, UserRole.READONLY.value),
    }

