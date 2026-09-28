"""
backend/apps/voyages/analytics_engine.py
============================================================
Cycle 2.5.2 — Demurrage & Despatch Analytics & Fleet Historical Performance Engine

Pure deterministic analytical engine computing enterprise-level shipping performance,
financial laytime impact, port efficiency, and cargo outturn metrics for
the Ministry of Steel (SAIL) chartering dashboard.

Calculated Dimensions:
  - Aggregate fleet voyage metrics: total voyages, completed vs active, total MT moved
  - Freight spend: aggregate gross freight revenue/expenditure
  - Laytime financials: total demurrage incurred, total despatch earned, net settlement
  - Demurrage risk ratio: (total demurrage / gross freight) * 100
  - Cargo outturn shortage: aggregate loaded vs discharged, shortage MT, outturn efficiency %
  - Port-level breakdown: volume, laytime turnaround, demurrage exposure per port
  - Cargo-level breakdown: volume, freight, demurrage per commodity type

Strict Decimal arithmetic throughout (ROUND_HALF_UP) with zero floating-point drift.
Air-gapped and deterministic: operates on real PostgreSQL database querysets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, List, Dict, Any
import datetime
from django.utils import timezone
from django.db.models import QuerySet

# Precision constants
_2DP = Decimal("0.01")
_3DP = Decimal("0.001")
_4DP = Decimal("0.0001")
_6DP = Decimal("0.000001")
HOURS_PER_DAY = Decimal("24")


@dataclass(frozen=True)
class PortPerformanceMetric:
    """Aggregated operational and financial metrics for a single discharge port."""
    port_name: str
    voyage_count: int
    total_loaded_mt: Decimal
    total_discharged_mt: Decimal
    total_shortage_mt: Decimal
    total_gross_freight_usd: Decimal
    total_demurrage_usd: Decimal
    total_despatch_usd: Decimal
    net_laytime_usd: Decimal
    average_port_hours: Decimal


@dataclass(frozen=True)
class CargoPerformanceMetric:
    """Aggregated volume and laytime impact for a single cargo category."""
    cargo_type: str
    voyage_count: int
    total_loaded_mt: Decimal
    total_gross_freight_usd: Decimal
    total_demurrage_usd: Decimal
    total_despatch_usd: Decimal
    net_laytime_usd: Decimal


@dataclass(frozen=True)
class FleetAnalyticsSummary:
    """
    Authoritative enterprise shipping analytics snapshot.
    All monetary quantities are in USD (2 decimal places).
    All tonnage quantities are in MT (3 decimal places).
    """
    total_voyages: int
    completed_voyages: int
    active_voyages: int

    total_loaded_mt: Decimal
    total_discharged_mt: Decimal
    total_shortage_mt: Decimal
    overall_outturn_efficiency_pct: Decimal

    total_gross_freight_usd: Decimal
    total_demurrage_usd: Decimal
    total_despatch_usd: Decimal
    net_laytime_usd: Decimal
    net_voyage_pnl_usd: Decimal
    demurrage_freight_ratio_pct: Decimal

    port_breakdown: List[PortPerformanceMetric]
    cargo_breakdown: List[CargoPerformanceMetric]
    as_of: datetime.datetime


def compute_fleet_analytics(
    voyages_qs: QuerySet,
) -> FleetAnalyticsSummary:
    """
    Compute enterprise fleet analytics from a pre-filtered VesselVoyage queryset.

    Parameters
    ----------
    voyages_qs : QuerySet of VesselVoyage, ideally with .select_related("contract", "vessel")
                 and prefetch_related("laytime_calculations").

    Returns
    -------
    FleetAnalyticsSummary
        Immutable snapshot containing overall fleet, port, and cargo aggregates.
    """
    from apps.laytime.engine.fixture_factory import VoyageStatus

    now = timezone.now()

    # Prefetch voyages with contract, vessel, and laytime calculations
    voyages = list(
        voyages_qs.select_related("contract", "vessel")
        .prefetch_related("laytime_calculations")
    )

    total_voyages = len(voyages)
    completed_count = 0
    active_count = 0

    total_loaded_mt = Decimal("0.000")
    total_discharged_mt = Decimal("0.000")
    total_shortage_mt = Decimal("0.000")

    total_gross_freight_usd = Decimal("0.00")
    total_demurrage_usd = Decimal("0.00")
    total_despatch_usd = Decimal("0.00")
    total_net_laytime_usd = Decimal("0.00")

    # Grouping accumulators
    ports_map: Dict[str, Dict[str, Any]] = {}
    cargo_map: Dict[str, Dict[str, Any]] = {}

    for v in voyages:
        status_val = v.voyage_status
        if status_val == VoyageStatus.COMPLETED.value:
            completed_count += 1
        elif status_val != VoyageStatus.CANCELLED.value:
            active_count += 1

        # Tonnage
        loaded = v.quantity_loaded_mt or Decimal("0.000")
        discharged = v.quantity_discharged_mt or Decimal("0.000")
        shortage = max(loaded - discharged, Decimal("0.000")) if v.quantity_discharged_mt is not None else Decimal("0.000")

        total_loaded_mt += loaded
        total_discharged_mt += discharged
        total_shortage_mt += shortage

        # Freight
        contract = v.contract
        freight_rate = contract.freight_rate_usd_per_mt if contract else Decimal("0.00")
        gross_freight = (loaded * freight_rate).quantize(_2DP, rounding=ROUND_HALF_UP)
        total_gross_freight_usd += gross_freight

        # Laytime settlement from current calculation
        current_calc = None
        for calc in v.laytime_calculations.all():
            if calc.is_current:
                current_calc = calc
                break

        dem_usd = Decimal("0.00")
        des_usd = Decimal("0.00")
        net_laytime = Decimal("0.00")
        port_hours = Decimal("0.000000")

        if current_calc:
            dem_usd = (current_calc.gross_demurrage_usd or Decimal("0.00")).quantize(_2DP, rounding=ROUND_HALF_UP)
            des_usd = (current_calc.gross_despatch_usd or Decimal("0.00")).quantize(_2DP, rounding=ROUND_HALF_UP)
            net_laytime = (current_calc.net_settlement_usd or Decimal("0.00")).quantize(_2DP, rounding=ROUND_HALF_UP)
            port_hours = current_calc.total_port_hours or Decimal("0.000000")

        total_demurrage_usd += dem_usd
        total_despatch_usd += des_usd
        total_net_laytime_usd += net_laytime

        # Port breakdown aggregation
        port = v.discharge_port or "UNKNOWN"
        if port not in ports_map:
            ports_map[port] = {
                "voyage_count": 0,
                "loaded_mt": Decimal("0.000"),
                "discharged_mt": Decimal("0.000"),
                "shortage_mt": Decimal("0.000"),
                "freight_usd": Decimal("0.00"),
                "demurrage_usd": Decimal("0.00"),
                "despatch_usd": Decimal("0.00"),
                "net_laytime_usd": Decimal("0.00"),
                "total_port_hours": Decimal("0.000000"),
                "port_hours_count": 0,
            }
        p_entry = ports_map[port]
        p_entry["voyage_count"] += 1
        p_entry["loaded_mt"] += loaded
        p_entry["discharged_mt"] += discharged
        p_entry["shortage_mt"] += shortage
        p_entry["freight_usd"] += gross_freight
        p_entry["demurrage_usd"] += dem_usd
        p_entry["despatch_usd"] += des_usd
        p_entry["net_laytime_usd"] += net_laytime
        if port_hours > Decimal("0"):
            p_entry["total_port_hours"] += port_hours
            p_entry["port_hours_count"] += 1

        # Cargo breakdown aggregation
        cargo = contract.cargo_type if contract else "UNKNOWN"
        if cargo not in cargo_map:
            cargo_map[cargo] = {
                "voyage_count": 0,
                "loaded_mt": Decimal("0.000"),
                "freight_usd": Decimal("0.00"),
                "demurrage_usd": Decimal("0.00"),
                "despatch_usd": Decimal("0.00"),
                "net_laytime_usd": Decimal("0.00"),
            }
        c_entry = cargo_map[cargo]
        c_entry["voyage_count"] += 1
        c_entry["loaded_mt"] += loaded
        c_entry["freight_usd"] += gross_freight
        c_entry["demurrage_usd"] += dem_usd
        c_entry["despatch_usd"] += des_usd
        c_entry["net_laytime_usd"] += net_laytime

    # Derived overall metrics
    overall_outturn_pct = (
        (total_discharged_mt / total_loaded_mt * Decimal("100")).quantize(_2DP, rounding=ROUND_HALF_UP)
        if total_loaded_mt > Decimal("0") and total_discharged_mt > Decimal("0")
        else Decimal("100.00") if total_loaded_mt > Decimal("0") and total_discharged_mt == Decimal("0")
        else Decimal("0.00")
    )

    net_voyage_pnl_usd = (total_gross_freight_usd - total_net_laytime_usd).quantize(_2DP, rounding=ROUND_HALF_UP)

    demurrage_freight_ratio = (
        (total_demurrage_usd / total_gross_freight_usd * Decimal("100")).quantize(_2DP, rounding=ROUND_HALF_UP)
        if total_gross_freight_usd > Decimal("0")
        else Decimal("0.00")
    )

    # Build port metrics list
    port_breakdown: List[PortPerformanceMetric] = []
    for port_name, p_data in sorted(ports_map.items()):
        avg_hrs = (
            (p_data["total_port_hours"] / Decimal(str(p_data["port_hours_count"]))).quantize(_2DP, rounding=ROUND_HALF_UP)
            if p_data["port_hours_count"] > 0
            else Decimal("0.00")
        )
        port_breakdown.append(
            PortPerformanceMetric(
                port_name=port_name,
                voyage_count=p_data["voyage_count"],
                total_loaded_mt=p_data["loaded_mt"].quantize(_3DP, rounding=ROUND_HALF_UP),
                total_discharged_mt=p_data["discharged_mt"].quantize(_3DP, rounding=ROUND_HALF_UP),
                total_shortage_mt=p_data["shortage_mt"].quantize(_3DP, rounding=ROUND_HALF_UP),
                total_gross_freight_usd=p_data["freight_usd"].quantize(_2DP, rounding=ROUND_HALF_UP),
                total_demurrage_usd=p_data["demurrage_usd"].quantize(_2DP, rounding=ROUND_HALF_UP),
                total_despatch_usd=p_data["despatch_usd"].quantize(_2DP, rounding=ROUND_HALF_UP),
                net_laytime_usd=p_data["net_laytime_usd"].quantize(_2DP, rounding=ROUND_HALF_UP),
                average_port_hours=avg_hrs,
            )
        )

    # Build cargo metrics list
    cargo_breakdown: List[CargoPerformanceMetric] = []
    for cargo_name, c_data in sorted(cargo_map.items()):
        cargo_breakdown.append(
            CargoPerformanceMetric(
                cargo_type=cargo_name,
                voyage_count=c_data["voyage_count"],
                total_loaded_mt=c_data["loaded_mt"].quantize(_3DP, rounding=ROUND_HALF_UP),
                total_gross_freight_usd=c_data["freight_usd"].quantize(_2DP, rounding=ROUND_HALF_UP),
                total_demurrage_usd=c_data["demurrage_usd"].quantize(_2DP, rounding=ROUND_HALF_UP),
                total_despatch_usd=c_data["despatch_usd"].quantize(_2DP, rounding=ROUND_HALF_UP),
                net_laytime_usd=c_data["net_laytime_usd"].quantize(_2DP, rounding=ROUND_HALF_UP),
            )
        )

    return FleetAnalyticsSummary(
        total_voyages=total_voyages,
        completed_voyages=completed_count,
        active_voyages=active_count,
        total_loaded_mt=total_loaded_mt.quantize(_3DP, rounding=ROUND_HALF_UP),
        total_discharged_mt=total_discharged_mt.quantize(_3DP, rounding=ROUND_HALF_UP),
        total_shortage_mt=total_shortage_mt.quantize(_3DP, rounding=ROUND_HALF_UP),
        overall_outturn_efficiency_pct=overall_outturn_pct,
        total_gross_freight_usd=total_gross_freight_usd.quantize(_2DP, rounding=ROUND_HALF_UP),
        total_demurrage_usd=total_demurrage_usd.quantize(_2DP, rounding=ROUND_HALF_UP),
        total_despatch_usd=total_despatch_usd.quantize(_2DP, rounding=ROUND_HALF_UP),
        net_laytime_usd=total_net_laytime_usd.quantize(_2DP, rounding=ROUND_HALF_UP),
        net_voyage_pnl_usd=net_voyage_pnl_usd,
        demurrage_freight_ratio_pct=demurrage_freight_ratio,
        port_breakdown=port_breakdown,
        cargo_breakdown=cargo_breakdown,
        as_of=now,
    )
