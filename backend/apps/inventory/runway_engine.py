"""
backend/apps/inventory/runway_engine.py
============================================================
Cycle 2.3.3 — Blast Furnace Runway & Raw Material Inventory Engine

Provides pure deterministic domain calculation for steel plant inventory,
blast furnace daily burn runway, incoming vessel cargo projection, and
starvation early warning alerts.

Core Mathematical Logic
-----------------------
1. Closing Stock:
   Closing = Opening + Receipts - Consumption
2. Physical Runway (without arrivals):
   Physical Runway Days = Closing Stock / Daily Burn Rate
3. Projected Runway (with incoming bulk vessels):
   Total Runway Days = (Closing Stock + In-Transit Projected Receipts) / Daily Burn Rate
4. Safety Thresholds (per Smart India Hackathon & SAIL specs):
   - CRITICAL:       Runway < 7.0 days   (emergency chartering / diversion required)
   - PRECAUTIONARY:  7.0 <= Runway < 14.0 days (expedite loading / discharge)
   - WATCH:          14.0 <= Runway < 21.0 days (monitor vessel ETA)
   - ADEQUATE:       21.0 <= Runway <= 45.0 days (healthy steady state)
   - SURPLUS:        Runway > 45.0 days  (defer next cargo)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Tuple

from apps.laytime.engine.fixture_factory import VoyageStatus
from .models import PlantInventory, RawMaterialCategory, StockSafetyStatus

_2DP = Decimal("0.01")
_3DP = Decimal("0.001")

SAIL_PLANTS_CONFIG: Dict[str, Tuple[str, str]] = {
    "BSP": ("Bhilai Steel Plant", "Bhilai, Chhattisgarh"),
    "DSP": ("Durgapur Steel Plant", "Durgapur, West Bengal"),
    "RSP": ("Rourkela Steel Plant", "Rourkela, Odisha"),
    "BSL": ("Bokaro Steel Plant", "Bokaro, Jharkhand"),
    "ISP": ("IISCO Steel Plant", "Burnpur, West Bengal"),
}

PLANT_NAME_TO_CODE: Dict[str, str] = {
    "BHILAI": "BSP",
    "BSP": "BSP",
    "DURGAPUR": "DSP",
    "DSP": "DSP",
    "ROURKELA": "RSP",
    "RSP": "RSP",
    "BOKARO": "BSL",
    "BSL": "BSL",
    "IISCO": "ISP",
    "ISP": "ISP",
    "BURNPUR": "ISP",
}


@dataclass(frozen=True)
class InTransitCargoContribution:
    """An incoming bulk vessel carrying raw materials for a steel plant."""
    voyage_id: str
    voyage_reference: str
    vessel_name: str
    cargo_type: str
    quantity_mt: Decimal
    voyage_status: str
    eta_date: datetime
    discharge_port: str


@dataclass(frozen=True)
class RunwayAnalysis:
    """Detailed runway assessment for a single plant and material."""
    plant_code: str
    plant_name: str
    material_category: str
    stock_date: date
    opening_stock_mt: Decimal
    receipts_mt: Decimal
    consumption_mt: Decimal
    closing_stock_mt: Decimal
    daily_consumption_rate_mt: Decimal
    physical_runway_days: Decimal
    projected_receipts_mt: Decimal
    total_runway_days: Decimal
    safety_status: str
    critical_stockout_date: Optional[date]
    recommended_action: str
    incoming_voyages: Tuple[InTransitCargoContribution, ...]


@dataclass(frozen=True)
class PlantHealthSummary:
    """Consolidated raw material health for an entire integrated steel plant."""
    plant_code: str
    plant_name: str
    plant_location: str
    total_materials_tracked: int
    critical_count: int
    precautionary_count: int
    watch_count: int
    adequate_count: int
    surplus_count: int
    bottleneck_material: Optional[str]
    lowest_runway_days: Optional[Decimal]
    overall_health: str         # "CRITICAL", "WARNING", "HEALTHY"
    evaluated_at: datetime


# ---------------------------------------------------------------------------
# In-Transit Projection Engine
# ---------------------------------------------------------------------------

def get_incoming_voyages_for_plant(
    plant_code: str,
    material_category: str,
) -> List[InTransitCargoContribution]:
    """
    Finds active vessels carrying cargo nominated for plant_code matching material_category.
    """
    from apps.voyages.models import VesselVoyage

    clean_code = plant_code.strip().upper()
    active_statuses = [
        VoyageStatus.SCHEDULED.value,
        VoyageStatus.IN_TRANSIT.value,
        VoyageStatus.ARRIVED.value,
        VoyageStatus.NOR_TENDERED.value,
        VoyageStatus.BERTHED.value,
        VoyageStatus.COMMENCING.value,
        VoyageStatus.DISCHARGING.value,
    ]

    voyages = (
        VesselVoyage.objects
        .filter(voyage_status__in=active_statuses)
        .select_related("vessel", "contract")
    )

    matching_contributions: List[InTransitCargoContribution] = []

    for v in voyages:
        contract = getattr(v, "contract", None)
        if not contract:
            continue

        c_plant = (contract.charterer_plant or "").strip().upper()
        resolved_code = PLANT_NAME_TO_CODE.get(c_plant, c_plant)
        if resolved_code != clean_code:
            continue

        c_cargo = (contract.cargo_type or "").strip().upper()
        target_mat = material_category.strip().upper()
        # Direct match or broad coking coal match
        if c_cargo == target_mat or (target_mat == "COKING_COAL" and "COAL" in c_cargo):
            qty = v.quantity_loaded_mt or contract.nominated_quantity_mt or Decimal("0.000")
            matching_contributions.append(
                InTransitCargoContribution(
                    voyage_id=str(v.voyage_id),
                    voyage_reference=v.voyage_reference,
                    vessel_name=v.vessel.vessel_name if v.vessel else "Unknown",
                    cargo_type=c_cargo,
                    quantity_mt=qty,
                    voyage_status=v.voyage_status,
                    eta_date=v.eta_date,
                    discharge_port=v.discharge_port,
                )
            )

    return matching_contributions


# ---------------------------------------------------------------------------
# Runway Calculation Function
# ---------------------------------------------------------------------------

def compute_runway_analysis(
    snapshot: PlantInventory,
    in_transit_override: Optional[List[InTransitCargoContribution]] = None,
) -> RunwayAnalysis:
    """
    Performs full blast furnace runway arithmetic on a PlantInventory record.
    """
    closing_stock = snapshot.closing_stock_mt or (
        snapshot.opening_stock_mt + snapshot.receipts_mt - snapshot.consumption_mt
    )
    burn_rate = snapshot.daily_consumption_rate_mt or Decimal("0.000")

    # In-transit cargo
    if in_transit_override is not None:
        incoming = in_transit_override
    else:
        incoming = get_incoming_voyages_for_plant(
            snapshot.plant_code, snapshot.material_category
        )

    in_transit_qty = sum(
        (c.quantity_mt for c in incoming), Decimal("0.000")
    )
    # If model has explicit projected receipts, take the max to preserve manual entries
    effective_projected = max(snapshot.projected_receipts_mt, in_transit_qty)

    if burn_rate > Decimal("0.000"):
        physical_runway = (closing_stock / burn_rate).quantize(_2DP, rounding=ROUND_HALF_UP)
        total_runway = ((closing_stock + effective_projected) / burn_rate).quantize(
            _2DP, rounding=ROUND_HALF_UP
        )
    else:
        physical_runway = Decimal("999.00")
        total_runway = Decimal("999.00")

    # Safety Classification
    if total_runway < Decimal("7.00"):
        status_val = StockSafetyStatus.CRITICAL.value
        rec_action = "EMERGENCY: Immediate coastal vessel diversion or emergency spot charter required to prevent blast furnace shutdown."
    elif total_runway < Decimal("14.00"):
        status_val = StockSafetyStatus.PRECAUTIONARY.value
        rec_action = "EXPEDITE: Fast-track discharge at port and prioritize railway rake allocation to steel plant."
    elif total_runway < Decimal("21.00"):
        status_val = StockSafetyStatus.WATCH.value
        rec_action = "MONITOR: Track approaching bulk vessel ETA closely. Verify port pilot availability."
    elif total_runway <= Decimal("45.00"):
        status_val = StockSafetyStatus.ADEQUATE.value
        rec_action = "NORMAL: Healthy inventory levels. Replenishment scheduled within laycan parameters."
    else:
        status_val = StockSafetyStatus.SURPLUS.value
        rec_action = "SURPLUS: High inventory runway. Consider slowing discharge or deferring next fixture."

    # Stock-out date calculation
    stockout_date = None
    if burn_rate > Decimal("0.000"):
        days_int = int(total_runway.to_integral_value(ROUND_HALF_UP))
        stockout_date = snapshot.stock_date + timedelta(days=max(days_int, 0))

    return RunwayAnalysis(
        plant_code=snapshot.plant_code,
        plant_name=snapshot.plant_name,
        material_category=snapshot.material_category,
        stock_date=snapshot.stock_date,
        opening_stock_mt=snapshot.opening_stock_mt,
        receipts_mt=snapshot.receipts_mt,
        consumption_mt=snapshot.consumption_mt,
        closing_stock_mt=closing_stock,
        daily_consumption_rate_mt=burn_rate,
        physical_runway_days=physical_runway,
        projected_receipts_mt=effective_projected,
        total_runway_days=total_runway,
        safety_status=status_val,
        critical_stockout_date=stockout_date,
        recommended_action=rec_action,
        incoming_voyages=tuple(incoming),
    )


# ---------------------------------------------------------------------------
# Plant Health Aggregator
# ---------------------------------------------------------------------------

def get_plant_health_summary(plant_code: str) -> PlantHealthSummary:
    """
    Evaluates the overall raw material safety posture of a specific steel plant
    across all tracked metallurgical categories.
    """
    code = plant_code.strip().upper()
    cfg = SAIL_PLANTS_CONFIG.get(code, (f"{code} Steel Plant", "India"))
    plant_name, plant_loc = cfg

    # Latest record per material category
    snapshots: List[PlantInventory] = []
    categories = (
        PlantInventory.objects
        .filter(plant_code__iexact=code)
        .values_list("material_category", flat=True)
        .distinct()
    )

    for cat in categories:
        latest = (
            PlantInventory.objects
            .filter(plant_code__iexact=code, material_category=cat)
            .order_by("-stock_date")
            .first()
        )
        if latest:
            snapshots.append(latest)

    crit = 0
    prec = 0
    watch = 0
    adeq = 0
    surp = 0
    lowest_runway: Optional[Decimal] = None
    bottleneck: Optional[str] = None

    for snap in snapshots:
        analysis = compute_runway_analysis(snap)
        st = analysis.safety_status
        if st == StockSafetyStatus.CRITICAL.value:
            crit += 1
        elif st == StockSafetyStatus.PRECAUTIONARY.value:
            prec += 1
        elif st == StockSafetyStatus.WATCH.value:
            watch += 1
        elif st == StockSafetyStatus.ADEQUATE.value:
            adeq += 1
        elif st == StockSafetyStatus.SURPLUS.value:
            surp += 1

        if lowest_runway is None or analysis.total_runway_days < lowest_runway:
            lowest_runway = analysis.total_runway_days
            bottleneck = analysis.material_category

    if crit > 0:
        overall = "CRITICAL"
    elif prec > 0 or watch > 0:
        overall = "WARNING"
    else:
        overall = "HEALTHY"

    return PlantHealthSummary(
        plant_code=code,
        plant_name=plant_name,
        plant_location=plant_loc,
        total_materials_tracked=len(snapshots),
        critical_count=crit,
        precautionary_count=prec,
        watch_count=watch,
        adequate_count=adeq,
        surplus_count=surp,
        bottleneck_material=bottleneck,
        lowest_runway_days=lowest_runway,
        overall_health=overall,
        evaluated_at=datetime.now(tz=timezone.utc),
    )
