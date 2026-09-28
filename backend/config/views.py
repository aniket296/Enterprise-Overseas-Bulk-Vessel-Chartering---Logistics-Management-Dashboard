"""
backend/config/views.py
============================================================
System Infrastructure and Telemetry Views.
"""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny
from .celery_topology import get_queue_topology_summary, resolve_task_routing


@api_view(["GET"])
@permission_classes([AllowAny])
def celery_topology_view(request):
    """
    GET /api/v1/system/celery/topology/
    Returns the active Celery worker topology, queue segregation status,
    and routing mappings.
    """
    task_name = request.query_params.get("task_name")
    if task_name:
        routing = resolve_task_routing(task_name)
        return Response(routing, status=status.HTTP_200_OK)

    summary = get_queue_topology_summary()
    return Response(summary, status=status.HTTP_200_OK)
