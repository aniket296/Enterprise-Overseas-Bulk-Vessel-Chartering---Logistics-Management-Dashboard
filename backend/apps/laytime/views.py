"""
backend/apps/laytime/views.py
============================================================
Pure Deterministic Calculation API Endpoints for BIMCO Laytime Settlement.
Enforces Rate Limiting (30 req/min), RBAC Role Permissions, and Strict Decimal Math.
"""

from decimal import Decimal, ROUND_HALF_UP
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.authentication.permissions import RoleBasedAccessControl
from apps.authentication.roles import UserRole
from apps.laytime.engine.allowed_laytime import AllowedLaytimeEngine, LaytimeTerms
from apps.laytime.serializers import ComputeLaytimeRequestSerializer


class ComputeLaytimeAPIView(APIView):
    """
    POST /api/v1/calculations/compute-laytime
    Computes contractually allowed laytime and financial settlement estimates
    using strict 28-precision Python Decimal arithmetic with zero float drift.
    """
    permission_classes = [RoleBasedAccessControl]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "calculations"

    action_roles = {
        "post": UserRole.all_roles(),
    }

    def post(self, request, *args, **kwargs):
        serializer = ComputeLaytimeRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "error": "BAD_REQUEST",
                    "detail": "Invalid calculation parameters provided.",
                    "validation_errors": serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        cargo_mt = data["cargo_quantity_mt"]
        rate_mt = data["discharge_rate_mt_day"]
        terms_str = data["laytime_terms"]
        tolerance_pct = data.get("tolerance_pct", Decimal("5.00"))
        demurrage_rate = data.get("demurrage_rate_usd_day", Decimal("0.00"))
        despatch_rate = data.get("despatch_rate_usd_day", Decimal("0.00"))
        counted_hours = data.get("counted_hours")

        # Run mathematical AllowedLaytimeEngine
        engine = AllowedLaytimeEngine(
            cargo_mt_nominated=cargo_mt,
            discharge_rate_mt_day=rate_mt,
            tolerance_pct=tolerance_pct,
            laytime_terms=LaytimeTerms(terms_str),
        )
        result = engine.compute()

        allowed_hours = result.allowed_hours_nominated
        allowed_days = result.days_nominated

        response_payload = {
            "status": "COMPUTED",
            "cargo_quantity_mt": str(cargo_mt),
            "discharge_rate_mt_day": str(rate_mt),
            "laytime_terms": terms_str,
            "tolerance_pct": str(tolerance_pct),
            "allowed_hours_nominated": str(allowed_hours) if allowed_hours is not None else None,
            "allowed_days_nominated": str(allowed_days) if allowed_days is not None else None,
            "formula": result.formula_string,
            "precision_context_digits": result.precision_context_prec,
        }

        # If counted_hours provided, evaluate financial variance
        if counted_hours is not None and allowed_hours is not None:
            variance_hours = allowed_hours - counted_hours
            if variance_hours < 0:
                # Demurrage
                dem_hours = abs(variance_hours)
                gross_dem = (demurrage_rate / Decimal("24") * dem_hours).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                response_payload["outcome"] = "DEMURRAGE"
                response_payload["demurrage_hours"] = str(dem_hours)
                response_payload["despatch_hours"] = "0.000000"
                response_payload["gross_demurrage_usd"] = str(gross_dem)
                response_payload["gross_despatch_usd"] = "0.00"
                response_payload["net_settlement_usd"] = str(gross_dem)
            elif variance_hours > 0:
                # Despatch
                desp_hours = variance_hours
                gross_desp = (despatch_rate / Decimal("24") * desp_hours).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                response_payload["outcome"] = "DESPATCH"
                response_payload["demurrage_hours"] = "0.000000"
                response_payload["despatch_hours"] = str(desp_hours)
                response_payload["gross_demurrage_usd"] = "0.00"
                response_payload["gross_despatch_usd"] = str(gross_desp)
                response_payload["net_settlement_usd"] = str(-gross_desp)
            else:
                response_payload["outcome"] = "BALANCED"
                response_payload["net_settlement_usd"] = "0.00"

        return Response(response_payload, status=status.HTTP_200_OK)
