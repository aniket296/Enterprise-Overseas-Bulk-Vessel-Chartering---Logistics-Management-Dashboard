"""
backend/apps/inventory/starvation_simulator.py
============================================================
Cycle 2.5.5 — Multi-Plant Stock Starvation Early Warning & Emergency Re-chartering Simulator

Provides automated risk evaluation and emergency response simulation across
all 5 SAIL steel plants (Bhilai, Durgapur, Rourkela, Bokaro, IISCO) to prevent
blast furnace starvation.

Core Components:
  1. Multi-Plant Starvation Scanner:
     - Scans on-ground physical stock and incoming vessel supply chain.
     - Evaluates physical vs. extended runway and classifies risk severity.
     - Identifies multi-plant inventory imbalances (surplus vs starving plants).

  2. In-Transit Vessel Diversion Simulator:
     - Identifies active vessels destined for surplus plants.
     - Simulates port diversion to starving plants, calculating donor impact,
       steaming/shifting costs, and net runway restoration.

  3. Emergency Spot Re-chartering Simulator:
     - Calculates raw material tonnage deficits to reach safe stock (21 days).
     - Determines optimal vessel parcel size (Supramax, Panamax, Capesize).
     - Estimates spot freight costs and urgent fixture laycan windows.

  4. Inter-Plant Rail Rake Transfer Simulator:
     - Identifies adjacent SAIL plants with transferable surplus stock.
     - Computes Indian Railways BOXN rake allocations (3,800 MT/rake),
       transit times, and freight transfer economics.

All arithmetic strictly uses Python Decimal with ROUND_HALF_UP (zero float drift).
Air-gapped and deterministic: operates on real PostgreSQL database models.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Tuple

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

# Standard operational parameters
TARGET_SAFE_RUNWAY_DAYS = Decimal("21.00")      # Adequate buffer (21 days)
CRITICAL_STARVATION_THRESHOLD = Decimal("7.00") # Emergency starvation (< 7 days)
RAKE_CAPACITY_MT = Decimal("3800.000")          # Standard Indian Railways BOXN rake
RAIL_FREIGHT_USD_PER_MT = Decimal("12.00")      # Average inter-plant rail freight ($12/MT ~ ₹1,000/MT)


class StarvationSimulatorError(ValueError):
    """Raised when simulation parameters or plant selections are invalid."""
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"[{code}] {detail}")


@dataclass(frozen=True)
class PlantStarvationProfile:
    """Individual plant raw material status and starvation vulnerability."""
    plant_code: str
    plant_name: str
    plant_location: str
    material_category: str
    current_stock_mt: Decimal
    daily_burn_rate_mt: Decimal
    physical_runway_days: Decimal
    incoming_vessels_count: int
    incoming_tonnage_mt: Decimal
    extended_runway_days: Decimal
    target_safe_stock_mt: Decimal
    deficit_to_safe_stock_mt: Decimal
    risk_severity: str     # CRITICAL_URGENT (< 5d), CRITICAL (5-7d), PRECAUTIONARY (7-14d), WATCH (14-21d), ADEQUATE (21-45d), SURPLUS (> 45d)
    is_starvation_risk: bool


@dataclass(frozen=True)
class DiversionOption:
    """Simulated diversion of an active in-transit vessel to the starving plant."""
    voyage_id: str
    voyage_reference: str
    vessel_name: str
    cargo_type: str
    quantity_mt: Decimal
    current_destination_plant: str
    current_discharge_port: str
    target_plant: str
    target_discharge_port: str
    donor_current_runway_days: Decimal
    donor_post_diversion_runway_days: Decimal
    is_donor_safe: bool
    recipient_post_diversion_runway_days: Decimal
    runway_days_gained: Decimal
    estimated_diversion_cost_usd: Decimal
    feasibility_score: int  # 0 to 100
    recommendation: str


@dataclass(frozen=True)
class EmergencyRecharterOption:
    """Spot chartering recommendation to eliminate plant stock deficit."""
    recommended_vessel_class: str   # SUPRAMAX, PANAMAX, CAPESIZE
    parcel_size_mt: Decimal
    estimated_spot_freight_usd_per_mt: Decimal
    total_estimated_freight_usd: Decimal
    laycan_urgency_days: int
    runway_days_restored: Decimal
    target_runway_days: Decimal


@dataclass(frozen=True)
class EmergencyInterPlantTransferOption:
    """Rail rake transfer from an adjacent surplus SAIL steel plant."""
    donor_plant_code: str
    donor_plant_name: str
    donor_surplus_stock_mt: Decimal
    rakes_required: int
    total_transfer_tonnage_mt: Decimal
    estimated_transit_hours: int
    estimated_rail_freight_usd: Decimal
    recipient_runway_days_restored: Decimal
    is_transfer_viable: bool


@dataclass(frozen=True)
class StarvationMitigationSimulation:
    """Consolidated emergency response and mitigation plan for a plant."""
    target_plant_code: str
    target_plant_name: str
    material_category: str
    starvation_profile: PlantStarvationProfile
    primary_strategy: str   # DIVERSION, INTERPLANT_RAIL, SPOT_RECHARTER, ADEQUATE_NO_ACTION
    summary_recommendation: str
    diversion_options: List[DiversionOption]
    spot_recharter_option: Optional[EmergencyRecharterOption]
    rail_transfer_options: List[EmergencyInterPlantTransferOption]
    all_plants_matrix: List[PlantStarvationProfile]
    simulated_at: datetime


# ---------------------------------------------------------------------------
# Multi-Plant Starvation Scanner
# ---------------------------------------------------------------------------

def scan_multi_plant_starvation(
    material_category: str = "COKING_COAL",
    target_safe_days: Decimal = TARGET_SAFE_RUNWAY_DAYS,
) -> List[PlantStarvationProfile]:
    """
    Scans all 5 SAIL plants to assess raw material inventory runway,
    incoming vessel shipments, and starvation vulnerability.
    """
    profiles: List[PlantStarvationProfile] = []
    clean_mat = material_category.strip().upper()

    for plant_code, (name, location) in SAIL_PLANTS_CONFIG.items():
        # Fetch latest inventory snapshot
        latest = (
            PlantInventory.objects
            .filter(plant_code__iexact=plant_code, material_category__iexact=clean_mat)
            .order_by("-stock_date")
            .first()
        )

        if latest:
            current_stock = latest.closing_stock_mt
            burn_rate = latest.daily_consumption_rate_mt or Decimal("5000.000")
        else:
            current_stock = Decimal("0.000")
            burn_rate = Decimal("5000.000")

        burn_rate = max(burn_rate, Decimal("0.001"))
        physical_runway = (current_stock / burn_rate).quantize(_2DP, rounding=ROUND_HALF_UP)

        # Incoming vessel contributions
        incoming_vessels = get_incoming_voyages_for_plant(plant_code, clean_mat)
        incoming_tonnage = sum((v.quantity_mt for v in incoming_vessels), Decimal("0.000")).quantize(_3DP, rounding=ROUND_HALF_UP)
        extended_runway = ((current_stock + incoming_tonnage) / burn_rate).quantize(_2DP, rounding=ROUND_HALF_UP)

        target_stock = (burn_rate * target_safe_days).quantize(_3DP, rounding=ROUND_HALF_UP)
        deficit = max(Decimal("0.000"), target_stock - (current_stock + incoming_tonnage)).quantize(_3DP, rounding=ROUND_HALF_UP)

        # Severity band
        if physical_runway < Decimal("5.00"):
            severity = "CRITICAL_URGENT"
        elif physical_runway < Decimal("7.00"):
            severity = "CRITICAL"
        elif physical_runway < Decimal("14.00"):
            severity = "PRECAUTIONARY"
        elif physical_runway < Decimal("21.00"):
            severity = "WATCH"
        elif physical_runway <= Decimal("45.00"):
            severity = "ADEQUATE"
        else:
            severity = "SURPLUS"

        is_starvation = (physical_runway < CRITICAL_STARVATION_THRESHOLD) or (extended_runway < CRITICAL_STARVATION_THRESHOLD)

        profiles.append(
            PlantStarvationProfile(
                plant_code=plant_code,
                plant_name=name,
                plant_location=location,
                material_category=clean_mat,
                current_stock_mt=current_stock.quantize(_3DP, rounding=ROUND_HALF_UP),
                daily_burn_rate_mt=burn_rate.quantize(_3DP, rounding=ROUND_HALF_UP),
                physical_runway_days=physical_runway,
                incoming_vessels_count=len(incoming_vessels),
                incoming_tonnage_mt=incoming_tonnage,
                extended_runway_days=extended_runway,
                target_safe_stock_mt=target_stock,
                deficit_to_safe_stock_mt=deficit,
                risk_severity=severity,
                is_starvation_risk=is_starvation,
            )
        )

    # Sort so most vulnerable plants appear first
    profiles.sort(key=lambda p: (p.physical_runway_days, p.extended_runway_days))
    return profiles


# ---------------------------------------------------------------------------
# Port Pair Diversion Distance & Cost Model
# ---------------------------------------------------------------------------

_PORT_DIVERSION_COST_TABLE: Dict[Tuple[str, str], Tuple[int, Decimal]] = {
    # (From Port, To Port) -> (steaming_hours, base_shifting_and_port_cost_usd)
    ("HALDIA", "PARADIP"): (12, Decimal("25000.00")),
    ("PARADIP", "HALDIA"): (12, Decimal("25000.00")),
    ("VISAKHAPATNAM", "PARADIP"): (18, Decimal("30000.00")),
    ("PARADIP", "VISAKHAPATNAM"): (18, Decimal("30000.00")),
    ("DHAMRA", "PARADIP"): (4, Decimal("15000.00")),
    ("PARADIP", "DHAMRA"): (4, Decimal("15000.00")),
    ("HALDIA", "VISAKHAPATNAM"): (24, Decimal("38000.00")),
    ("VISAKHAPATNAM", "HALDIA"): (24, Decimal("38000.00")),
}

# Default port preferences per SAIL plant
_PLANT_PREFERRED_PORT: Dict[str, str] = {
    "BSP": "VISAKHAPATNAM",
    "DSP": "HALDIA",
    "RSP": "PARADIP",
    "BSL": "HALDIA",
    "ISP": "HALDIA",
}


def _calculate_diversion_cost(from_port: str, to_port: str) -> Decimal:
    """Calculate port shifting, additional bunker fuel, and charter hire costs."""
    clean_from = from_port.strip().upper()
    clean_to = to_port.strip().upper()

    if clean_from == clean_to:
        # Same discharge port (e.g. both plants served by Paradip; merely rake redistribution)
        return Decimal("5000.00")

    key = (clean_from, clean_to)
    if key in _PORT_DIVERSION_COST_TABLE:
        hours, fixed_cost = _PORT_DIVERSION_COST_TABLE[key]
        # Additional bunker + hire: ~$1,000/hour
        variable_cost = (Decimal(str(hours)) * Decimal("1000.00")).quantize(_2DP, rounding=ROUND_HALF_UP)
        return (fixed_cost + variable_cost).quantize(_2DP, rounding=ROUND_HALF_UP)

    # General coastal diversion estimate
    return Decimal("40000.00")


# ---------------------------------------------------------------------------
# Emergency Mitigation Simulator
# ---------------------------------------------------------------------------

def simulate_starvation_mitigation(
    plant_code: str,
    material_category: str = "COKING_COAL",
    target_safe_days: Decimal = TARGET_SAFE_RUNWAY_DAYS,
) -> StarvationMitigationSimulation:
    """
    Computes complete emergency response options for a starving plant:
      1. In-transit vessel diversions from surplus plants
      2. Inter-plant rail rake transfers
      3. Emergency spot chartering parcel sizing and cost
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
            raise StarvationSimulatorError(
                "UNKNOWN_PLANT",
                f"Unknown plant code '{plant_code}'. Valid codes: {list(SAIL_PLANTS_CONFIG.keys())}",
            )
        clean_code = resolved

    clean_mat = material_category.strip().upper()

    # 1. Scan all plants
    all_profiles = scan_multi_plant_starvation(clean_mat, target_safe_days)
    plant_map = {p.plant_code: p for p in all_profiles}
    target_profile = plant_map[clean_code]

    target_pref_port = _PLANT_PREFERRED_PORT.get(clean_code, "PARADIP")
    now_utc = datetime.now(timezone.utc)

    # 2. In-Transit Vessel Diversion Simulation
    from apps.voyages.models import VesselVoyage
    active_statuses = [
        VoyageStatus.SCHEDULED.value,
        VoyageStatus.IN_TRANSIT.value,
        VoyageStatus.ARRIVED.value,
        VoyageStatus.NOR_TENDERED.value,
        VoyageStatus.BERTHED.value,
    ]

    all_voyages = (
        VesselVoyage.objects
        .filter(voyage_status__in=active_statuses)
        .select_related("vessel", "contract")
    )

    diversion_options: List[DiversionOption] = []

    for voy in all_voyages:
        contract = getattr(voy, "contract", None)
        if not contract:
            continue

        c_plant = (contract.charterer_plant or "").strip().upper()
        donor_code = PLANT_NAME_TO_CODE.get(c_plant, c_plant)

        # Do not divert vessels already coming to target plant
        if donor_code == clean_code or donor_code not in plant_map:
            continue

        c_cargo = (contract.cargo_type or "").strip().upper()
        if c_cargo != clean_mat and not (clean_mat == "COKING_COAL" and "COAL" in c_cargo):
            continue

        qty = (voy.quantity_loaded_mt or contract.nominated_quantity_mt or Decimal("0.000")).quantize(_3DP, rounding=ROUND_HALF_UP)
        if qty <= Decimal("0.000"):
            continue

        donor_profile = plant_map[donor_code]
        donor_burn = donor_profile.daily_burn_rate_mt

        # Post diversion donor runway
        donor_rem_stock = max(Decimal("0.000"), donor_profile.current_stock_mt + donor_profile.incoming_tonnage_mt - qty)
        donor_post_runway = (donor_rem_stock / donor_burn).quantize(_2DP, rounding=ROUND_HALF_UP)
        is_donor_safe = donor_post_runway >= Decimal("14.00")

        # Recipient impact
        recip_burn = target_profile.daily_burn_rate_mt
        recip_stock_with_div = target_profile.current_stock_mt + target_profile.incoming_tonnage_mt + qty
        recip_post_runway = (recip_stock_with_div / recip_burn).quantize(_2DP, rounding=ROUND_HALF_UP)
        gained_days = (qty / recip_burn).quantize(_2DP, rounding=ROUND_HALF_UP)

        from_port = voy.discharge_port or "PARADIP"
        div_cost = _calculate_diversion_cost(from_port, target_pref_port)

        # Feasibility scoring (0 - 100)
        score = 0
        if is_donor_safe:
            score += 45
        elif donor_post_runway >= Decimal("10.00"):
            score += 20

        if recip_post_runway >= Decimal("14.00"):
            score += 35
        elif recip_post_runway >= Decimal("7.00"):
            score += 20

        if div_cost <= Decimal("30000.00"):
            score += 20
        elif div_cost <= Decimal("50000.00"):
            score += 10

        if is_donor_safe and score >= 70:
            rec_text = "HIGHLY_FEASIBLE: Divert vessel to avert critical starvation with safe donor buffer."
        elif is_donor_safe:
            rec_text = "FEASIBLE: Divert vessel; donor retains acceptable runway buffer."
        else:
            rec_text = "HIGH_RISK: Diverting this vessel severely degrades donor plant runway."

        diversion_options.append(
            DiversionOption(
                voyage_id=str(voy.voyage_id),
                voyage_reference=voy.voyage_reference,
                vessel_name=voy.vessel.vessel_name if voy.vessel else "Unknown",
                cargo_type=c_cargo,
                quantity_mt=qty,
                current_destination_plant=donor_profile.plant_name,
                current_discharge_port=from_port,
                target_plant=target_profile.plant_name,
                target_discharge_port=target_pref_port,
                donor_current_runway_days=donor_profile.extended_runway_days,
                donor_post_diversion_runway_days=donor_post_runway,
                is_donor_safe=is_donor_safe,
                recipient_post_diversion_runway_days=recip_post_runway,
                runway_days_gained=gained_days,
                estimated_diversion_cost_usd=div_cost,
                feasibility_score=score,
                recommendation=rec_text,
            )
        )

    # Sort diversion options by feasibility score descending
    diversion_options.sort(key=lambda d: d.feasibility_score, reverse=True)

    # 3. Inter-Plant Rail Transfer Simulation
    rail_transfer_options: List[EmergencyInterPlantTransferOption] = []
    deficit = target_profile.deficit_to_safe_stock_mt

    for p in all_profiles:
        if p.plant_code == clean_code:
            continue

        # Check if donor has physical surplus above safe 21 days
        safe_stock = p.target_safe_stock_mt
        surplus_stock = max(Decimal("0.000"), p.current_stock_mt - safe_stock)

        if surplus_stock >= RAKE_CAPACITY_MT:
            # Viable donor for rail transfer
            rakes = min(
                int(surplus_stock // RAKE_CAPACITY_MT),
                max(1, int(deficit // RAKE_CAPACITY_MT) + 1),
                15,  # Max batch size: 15 rakes (~57,000 MT)
            )
            transfer_tonnage = (Decimal(str(rakes)) * RAKE_CAPACITY_MT).quantize(_3DP, rounding=ROUND_HALF_UP)
            rail_freight = (transfer_tonnage * RAIL_FREIGHT_USD_PER_MT).quantize(_2DP, rounding=ROUND_HALF_UP)
            restored_days = (transfer_tonnage / target_profile.daily_burn_rate_mt).quantize(_2DP, rounding=ROUND_HALF_UP)

            # Estimated transit hours based on plant distance
            transit_hours = 24
            if {clean_code, p.plant_code} == {"BSL", "RSP"}:
                transit_hours = 18
            elif {clean_code, p.plant_code} == {"DSP", "ISP"}:
                transit_hours = 8
            elif {clean_code, p.plant_code} == {"BSP", "RSP"}:
                transit_hours = 30

            rail_transfer_options.append(
                EmergencyInterPlantTransferOption(
                    donor_plant_code=p.plant_code,
                    donor_plant_name=p.plant_name,
                    donor_surplus_stock_mt=surplus_stock.quantize(_3DP, rounding=ROUND_HALF_UP),
                    rakes_required=rakes,
                    total_transfer_tonnage_mt=transfer_tonnage,
                    estimated_transit_hours=transit_hours,
                    estimated_rail_freight_usd=rail_freight,
                    recipient_runway_days_restored=restored_days,
                    is_transfer_viable=True,
                )
            )

    rail_transfer_options.sort(key=lambda r: r.total_transfer_tonnage_mt, reverse=True)

    # 4. Emergency Spot Re-chartering Sizing & Costing
    spot_option: Optional[EmergencyRecharterOption] = None

    if deficit > Decimal("0.000"):
        # Select vessel class matching deficit
        if deficit <= Decimal("60000.000"):
            vessel_class = "SUPRAMAX"
            parcel_size = Decimal("55000.000")
            base_rate = Decimal("24.50")
        elif deficit <= Decimal("95000.000"):
            vessel_class = "PANAMAX"
            parcel_size = Decimal("75000.000")
            base_rate = Decimal("21.00")
        else:
            vessel_class = "CAPESIZE"
            parcel_size = Decimal("150000.000")
            base_rate = Decimal("16.50")

        # Emergency surcharge if physical runway < 5 days
        if target_profile.physical_runway_days < Decimal("5.00"):
            rate_usd_mt = (base_rate + Decimal("3.00")).quantize(_2DP, rounding=ROUND_HALF_UP)
        else:
            rate_usd_mt = base_rate.quantize(_2DP, rounding=ROUND_HALF_UP)

        total_spot_freight = (parcel_size * rate_usd_mt).quantize(_2DP, rounding=ROUND_HALF_UP)
        restored = (parcel_size / target_profile.daily_burn_rate_mt).quantize(_2DP, rounding=ROUND_HALF_UP)
        urgency_days = max(1, int(target_profile.physical_runway_days))

        spot_option = EmergencyRecharterOption(
            recommended_vessel_class=vessel_class,
            parcel_size_mt=parcel_size,
            estimated_spot_freight_usd_per_mt=rate_usd_mt,
            total_estimated_freight_usd=total_spot_freight,
            laycan_urgency_days=urgency_days,
            runway_days_restored=restored,
            target_runway_days=target_safe_days,
        )

    # 5. Determine Primary Strategy & Recommendation
    if not target_profile.is_starvation_risk and target_profile.physical_runway_days >= Decimal("14.00"):
        primary_strat = "ADEQUATE_NO_ACTION"
        summary = f"{target_profile.plant_name} maintains healthy stock runway ({target_profile.physical_runway_days}d physical, {target_profile.extended_runway_days}d extended). No emergency actions required."
    elif diversion_options and diversion_options[0].is_donor_safe and diversion_options[0].feasibility_score >= 70:
        primary_strat = "DIVERSION"
        top_div = diversion_options[0]
        summary = (
            f"RECOMMENDED PRIMARY ACTION: Divert vessel {top_div.vessel_name} ({top_div.quantity_mt:,.0f} MT) "
            f"from {top_div.current_destination_plant} to {target_profile.plant_name}. "
            f"Restores runway by +{top_div.runway_days_gained} days (to {top_div.recipient_post_diversion_runway_days}d) "
            f"at an estimated diversion cost of ${top_div.estimated_diversion_cost_usd:,.2f} USD."
        )
    elif rail_transfer_options and rail_transfer_options[0].is_transfer_viable:
        primary_strat = "INTERPLANT_RAIL"
        top_rail = rail_transfer_options[0]
        summary = (
            f"RECOMMENDED PRIMARY ACTION: Emergency Indian Railways inter-plant transfer of {top_rail.rakes_required} BOXN rakes "
            f"({top_rail.total_transfer_tonnage_mt:,.0f} MT) from {top_rail.donor_plant_name} to {target_profile.plant_name}. "
            f"Restores runway by +{top_rail.recipient_runway_days_restored} days within {top_rail.estimated_transit_hours} hours."
        )
    elif spot_option:
        primary_strat = "SPOT_RECHARTER"
        summary = (
            f"RECOMMENDED PRIMARY ACTION: Immediate prompt spot charter of 1x {spot_option.recommended_vessel_class} "
            f"({spot_option.parcel_size_mt:,.0f} MT at ${spot_option.estimated_spot_freight_usd_per_mt}/MT, "
            f"total: ${spot_option.total_estimated_freight_usd:,.2f} USD). Laycan window within {spot_option.laycan_urgency_days} days."
        )
    else:
        primary_strat = "ADEQUATE_NO_ACTION"
        summary = "No immediate mitigation actions needed or available."

    return StarvationMitigationSimulation(
        target_plant_code=clean_code,
        target_plant_name=target_profile.plant_name,
        material_category=clean_mat,
        starvation_profile=target_profile,
        primary_strategy=primary_strat,
        summary_recommendation=summary,
        diversion_options=diversion_options,
        spot_recharter_option=spot_option,
        rail_transfer_options=rail_transfer_options,
        all_plants_matrix=all_profiles,
        simulated_at=now_utc,
    )
