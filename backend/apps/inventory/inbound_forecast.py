"""
backend/apps/inventory/inbound_forecast.py
============================================================
Cycle 2.5.4 — Inbound Supply Chain Forward Runway Forecaster Engine

Provides forward-looking multi-day inventory depletion and replenishment
simulation for integrated steel plants (Bhilai, Rourkela, Bokaro, etc.)
by combining:
  1. Authoritative daily stockyard inventory snapshot (PlantInventory)
  2. Blast furnace daily burn rate (daily_consumption_rate_mt)
  3. Active incoming bulk vessel shipments (VesselVoyage at sea / in port)
  4. Port-to-plant rail transit lead time (default: 3 days)

Simulation Outputs:
  - Day-by-day projected stock, burn, arrivals, remaining runway, safety status
  - Stockout risk date (first date when stock reaches 0 MT)
  - Critical safety breach date (first date runway drops below 7 days)
  - Precautionary safety breach date (first date runway drops below 14 days)
  - Minimum projected stock level and trough date
  - Starvation risk indicator (is_starvation_risk)

All arithmetic strictly uses Python Decimal with ROUND_HALF_UP (zero float drift).
Air-gapped and deterministic: operates on real PostgreSQL database models.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, List, Dict, Any, Tuple
from django.utils import timezone

from apps.laytime.engine.fixture_factory import VoyageStatus
from .models import PlantInventory, RawMaterialCategory, StockSafetyStatus
from .runway_engine import (
    SAIL_PLANTS_CONFIG,
    PLANT_NAME_TO_CODE,
    InTransitCargoContribution,
    get_incoming_voyages_for_plant,
)

_2DP = Decimal("0.01")
_3DP = Decimal("0.001")
DEFAULT_RAIL_TRANSIT_LEAD_DAYS = 3


class InboundForecastError(ValueError):
    """Raised when simulation parameters are invalid."""
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"[{code}] {detail}")


@dataclass(frozen=True)
class DailyRunwayPoint:
    """Projected stock and runway state for a single calendar day."""
    day_index: int
    simulation_date: date
    opening_stock_mt: Decimal
    daily_burn_mt: Decimal
    inbound_receipts_mt: Decimal
    closing_stock_mt: Decimal
    runway_days_remaining: Decimal
    safety_status: str
    events: Tuple[str, ...]


@dataclass(frozen=True)
class PlantForwardRunwayForecast:
    """Consolidated forward-looking multi-day inventory projection."""
    plant_code: str
    plant_name: str
    plant_location: str
    material_category: str
    horizon_days: int
    as_of_date: date

    # Base state
    current_stock_mt: Decimal
    daily_consumption_rate_mt: Decimal
    baseline_runway_days: Decimal

    # Inbound projection
    total_inbound_tonnage_mt: Decimal
    inbound_vessels_count: int
    extended_runway_days: Decimal

    # Risk Milestones
    stockout_date: Optional[date]
    critical_breach_date: Optional[date]
    precautionary_breach_date: Optional[date]
    minimum_projected_stock_mt: Decimal
    minimum_stock_date: Optional[date]
    is_starvation_risk: bool

    # Drilldown
    timeline: List[DailyRunwayPoint]
    inbound_vessels: List[InTransitCargoContribution]
    generated_at: datetime


def simulate_forward_runway(
    plant_code: str,
    material_category: str,
    horizon_days: int = 30,
    rail_transit_lead_days: int = DEFAULT_RAIL_TRANSIT_LEAD_DAYS,
    custom_stock_mt: Optional[Decimal] = None,
    custom_burn_rate_mt: Optional[Decimal] = None,
) -> PlantForwardRunwayForecast:
    """
    Run forward-looking daily inventory depletion & vessel replenishment simulation.

    Parameters
    ----------
    plant_code             : str      SAIL plant code (BSP, RSP, BSL, DSP, ISP).
    material_category      : str      Raw material category (e.g. COKING_COAL).
    horizon_days           : int      Number of forward simulation days (default: 30, 1-90).
    rail_transit_lead_days : int      Days from vessel ETA to plant stockyard receipt (default: 3).
    custom_stock_mt        : Decimal  Optional override for opening stock.
    custom_burn_rate_mt    : Decimal  Optional override for daily burn rate.

    Returns
    -------
    PlantForwardRunwayForecast
    """
    clean_code = plant_code.strip().upper()
    if clean_code not in SAIL_PLANTS_CONFIG:
        resolved = PLANT_NAME_TO_CODE.get(clean_code)
        if not resolved:
            for name_prefix, code_val in PLANT_NAME_TO_CODE.items():
                if name_prefix in clean_code or clean_code in name_prefix:
                    resolved = code_val
                    break
        if not resolved:
            raise InboundForecastError(
                "UNKNOWN_PLANT",
                f"Unknown plant code '{plant_code}'. Valid codes: {list(SAIL_PLANTS_CONFIG.keys())}",
            )
        clean_code = resolved

    if horizon_days < 1 or horizon_days > 120:
        raise InboundForecastError(
            "INVALID_HORIZON",
            f"horizon_days must be between 1 and 120 (got {horizon_days}).",
        )

    plant_info = SAIL_PLANTS_CONFIG[clean_code]
    plant_name = plant_info[0]
    plant_location = plant_info[1]

    # Resolve baseline inventory snapshot
    latest_snapshot = (
        PlantInventory.objects
        .filter(plant_code__iexact=clean_code, material_category__iexact=material_category)
        .order_by("-stock_date")
        .first()
    )

    if custom_stock_mt is not None:
        current_stock = custom_stock_mt
    elif latest_snapshot:
        current_stock = latest_snapshot.closing_stock_mt
    else:
        current_stock = Decimal("0.000")

    if custom_burn_rate_mt is not None:
        burn_rate = custom_burn_rate_mt
    elif latest_snapshot and latest_snapshot.daily_consumption_rate_mt:
        burn_rate = latest_snapshot.daily_consumption_rate_mt
    else:
        burn_rate = Decimal("5000.000")  # Default industrial baseline (5,000 MT/day)

    burn_rate = max(burn_rate, Decimal("0.001"))

    # Baseline runway (without inbound shipments)
    baseline_runway = (current_stock / burn_rate).quantize(_2DP, rounding=ROUND_HALF_UP)

    # Fetch active incoming vessels
    incoming_vessels = get_incoming_voyages_for_plant(clean_code, material_category)
    total_inbound_tonnage = sum((v.quantity_mt for v in incoming_vessels), Decimal("0.000"))
    extended_runway = ((current_stock + total_inbound_tonnage) / burn_rate).quantize(_2DP, rounding=ROUND_HALF_UP)

    now = timezone.now()
    start_date = now.date()

    # Map vessel deliveries to plant stockyard arrival dates: ETA + rail transit
    deliveries_by_date: Dict[date, List[Tuple[str, Decimal]]] = {}
    for iv in incoming_vessels:
        v_eta = iv.eta_date.date() if isinstance(iv.eta_date, datetime) else iv.eta_date
        arrival_date = v_eta + timedelta(days=rail_transit_lead_days)
        # If arrival is in the past, assign to today
        effective_date = max(arrival_date, start_date)
        if effective_date not in deliveries_by_date:
            deliveries_by_date[effective_date] = []
        deliveries_by_date[effective_date].append(
            (f"{iv.vessel_name} ({iv.voyage_reference}) [{iv.quantity_mt:,.0f} MT at {iv.discharge_port}]", iv.quantity_mt)
        )

    # Run day-by-day forward simulation
    sim_stock = current_stock
    timeline: List[DailyRunwayPoint] = []

    stockout_date: Optional[date] = None
    critical_breach_date: Optional[date] = None
    precautionary_breach_date: Optional[date] = None

    minimum_stock_mt = sim_stock
    minimum_stock_date: Optional[date] = start_date

    for day_i in range(1, horizon_days + 1):
        sim_date = start_date + timedelta(days=day_i)
        opening = sim_stock

        # Inbound arrivals on this date
        receipts = Decimal("0.000")
        day_events: List[str] = []
        if sim_date in deliveries_by_date:
            for desc, qty in deliveries_by_date[sim_date]:
                receipts += qty
                day_events.append(f"Replenishment: {desc}")

        # Compute closing stock
        closing = max(opening + receipts - burn_rate, Decimal("0.000")).quantize(_3DP, rounding=ROUND_HALF_UP)
        runway_rem = (closing / burn_rate).quantize(_2DP, rounding=ROUND_HALF_UP)

        # Safety status
        if runway_rem < Decimal("7.00"):
            status_val = StockSafetyStatus.CRITICAL.value
        elif runway_rem < Decimal("14.00"):
            status_val = StockSafetyStatus.PRECAUTIONARY.value
        elif runway_rem < Decimal("21.00"):
            status_val = StockSafetyStatus.WATCH.value
        elif runway_rem <= Decimal("45.00"):
            status_val = StockSafetyStatus.ADEQUATE.value
        else:
            status_val = StockSafetyStatus.SURPLUS.value

        # Track milestone breaches
        if closing == Decimal("0.000") and stockout_date is None:
            stockout_date = sim_date
            day_events.append("ALERT: Stockout reached (0 MT available)")

        if runway_rem < Decimal("7.00") and critical_breach_date is None:
            critical_breach_date = sim_date
            day_events.append(f"WARNING: Critical stock runway breach ({runway_rem}d < 7d)")

        if runway_rem < Decimal("14.00") and precautionary_breach_date is None:
            precautionary_breach_date = sim_date

        if closing < minimum_stock_mt:
            minimum_stock_mt = closing
            minimum_stock_date = sim_date

        timeline.append(
            DailyRunwayPoint(
                day_index=day_i,
                simulation_date=sim_date,
                opening_stock_mt=opening.quantize(_3DP, rounding=ROUND_HALF_UP),
                daily_burn_mt=burn_rate.quantize(_3DP, rounding=ROUND_HALF_UP),
                inbound_receipts_mt=receipts.quantize(_3DP, rounding=ROUND_HALF_UP),
                closing_stock_mt=closing,
                runway_days_remaining=runway_rem,
                safety_status=status_val,
                events=tuple(day_events),
            )
        )

        sim_stock = closing

    is_starvation = (stockout_date is not None) or (critical_breach_date is not None)

    return PlantForwardRunwayForecast(
        plant_code=clean_code,
        plant_name=plant_name,
        plant_location=plant_location,
        material_category=material_category,
        horizon_days=horizon_days,
        as_of_date=start_date,
        current_stock_mt=current_stock.quantize(_3DP, rounding=ROUND_HALF_UP),
        daily_consumption_rate_mt=burn_rate.quantize(_3DP, rounding=ROUND_HALF_UP),
        baseline_runway_days=baseline_runway,
        total_inbound_tonnage_mt=total_inbound_tonnage.quantize(_3DP, rounding=ROUND_HALF_UP),
        inbound_vessels_count=len(incoming_vessels),
        extended_runway_days=extended_runway,
        stockout_date=stockout_date,
        critical_breach_date=critical_breach_date,
        precautionary_breach_date=precautionary_breach_date,
        minimum_projected_stock_mt=minimum_stock_mt.quantize(_3DP, rounding=ROUND_HALF_UP),
        minimum_stock_date=minimum_stock_date,
        is_starvation_risk=is_starvation,
        timeline=timeline,
        inbound_vessels=incoming_vessels,
        generated_at=now,
    )
