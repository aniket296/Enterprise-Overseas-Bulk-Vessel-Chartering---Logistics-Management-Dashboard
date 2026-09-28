"""
backend/apps/voyages/pnl_engine.py
============================================================
Cycle 2.5.1 — Freight Revenue & Voyage P&L Summary Engine

Pure deterministic math engine producing a voyage-level Profit & Loss
summary from first-principles charter-party economics.

Components:
  - Gross freight revenue: freight_rate_usd_per_mt × quantity_loaded_mt
  - Net laytime settlement: demurrage (cost) or despatch (credit)
  - Cargo outturn efficiency: quantity_loaded vs quantity_discharged
  - Net voyage P&L from charterer's perspective (SAIL Ministry of Steel)

All arithmetic uses Python Decimal with fixed-point rounding to 2 decimal
places (USD-standard), consistent with PostgreSQL NUMERIC(14,4) columns.

Design contract:
  - Pure function: no I/O, no side-effects, no network calls.
  - Raises VoyagePnLError (subclass of ValueError) on missing prerequisites.
  - Immutable frozen dataclass result.
  - Zero mocking policy: all tests use real DB entities.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

# Precision constants
_2DP = Decimal("0.01")
_4DP = Decimal("0.0001")
_6DP = Decimal("0.000001")
HOURS_PER_DAY = Decimal("24")


class VoyagePnLError(ValueError):
    """Raised when P&L cannot be computed due to missing or invalid inputs."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"[{code}] {detail}")


@dataclass(frozen=True)
class VoyagePnLSnapshot:
    """
    Immutable voyage Profit & Loss summary from the charterer's perspective.

    All monetary fields are expressed in USD, rounded to 2 decimal places.
    Positive net_voyage_pnl_usd indicates the charterer is in credit (despatch
    received / no demurrage). Negative indicates demurrage cost to charterer.

    Fields
    ------
    voyage_id : str
        UUID of the VesselVoyage.
    voyage_reference : str
        Human-readable voyage reference (e.g. "VOY-2026-001").
    vessel_name : str
        Name of the vessel.
    cargo_type : str
        Cargo classification (e.g. "COKING_COAL").
    load_port : str
        Port of loading.
    discharge_port : str
        Port of discharge.

    # Cargo outturn
    nominated_quantity_mt : Decimal
        Contracted nominated quantity in metric tonnes.
    quantity_loaded_mt : Decimal
        Actual B/L loaded quantity in metric tonnes.
    quantity_discharged_mt : Decimal | None
        Outturn discharge quantity, if available.
    shortage_mt : Decimal | None
        Computed shortage = quantity_loaded − quantity_discharged (floored at 0).
    cargo_outturn_efficiency_pct : Decimal | None
        Discharge outturn as % of loaded quantity (100 × discharged / loaded).

    # Freight revenue
    freight_rate_usd_per_mt : Decimal
        Agreed freight rate per metric tonne.
    gross_freight_revenue_usd : Decimal
        freight_rate × quantity_loaded_mt.

    # Laytime settlement
    laytime_outcome : str | None
        Outcome string from current LaytimeCalculation (DEMURRAGE / DESPATCH / BALANCED).
    gross_demurrage_usd : Decimal
        Gross demurrage payable (cost to charterer). Zero if no demurrage.
    gross_despatch_usd : Decimal
        Gross despatch receivable (credit to charterer). Zero if no despatch.
    net_settlement_usd : Decimal
        Net laytime settlement = demurrage − despatch. Positive = cost, negative = credit.

    # P&L summary
    net_voyage_pnl_usd : Decimal
        gross_freight_revenue_usd − net_settlement_usd
        Positive = charterer receives net benefit; negative = charterer pays net cost.
    freight_efficiency_pct : Decimal
        Loaded quantity as % of nominated (quantity_loaded / nominated × 100).

    # Metadata
    laytime_calc_revision : int | None
        Revision number of the LaytimeCalculation used (None if no calc available).
    is_settlement_final : bool
        True if the laytime outcome is SETTLED or DISPUTED (settled by finance officer).
    calculation_basis : str
        Describes how net_settlement_usd was sourced: "LAYTIME_CALCULATION" or "ZERO_NO_CALC".
    """

    voyage_id: str
    voyage_reference: str
    vessel_name: str
    cargo_type: str
    load_port: str
    discharge_port: str

    # Cargo outturn
    nominated_quantity_mt: Decimal
    quantity_loaded_mt: Decimal
    quantity_discharged_mt: Optional[Decimal]
    shortage_mt: Optional[Decimal]
    cargo_outturn_efficiency_pct: Optional[Decimal]

    # Freight revenue
    freight_rate_usd_per_mt: Decimal
    gross_freight_revenue_usd: Decimal

    # Laytime settlement
    laytime_outcome: Optional[str]
    gross_demurrage_usd: Decimal
    gross_despatch_usd: Decimal
    net_settlement_usd: Decimal

    # P&L summary
    net_voyage_pnl_usd: Decimal
    freight_efficiency_pct: Decimal

    # Metadata
    laytime_calc_revision: Optional[int]
    is_settlement_final: bool
    calculation_basis: str


def compute_voyage_pnl(
    voyage_id: str,
    voyage_reference: str,
    vessel_name: str,
    cargo_type: str,
    load_port: str,
    discharge_port: str,
    nominated_quantity_mt: Decimal,
    freight_rate_usd_per_mt: Decimal,
    quantity_loaded_mt: Decimal,
    quantity_discharged_mt: Optional[Decimal] = None,
    # Optional: from current LaytimeCalculation
    laytime_outcome: Optional[str] = None,
    gross_demurrage_usd: Optional[Decimal] = None,
    gross_despatch_usd: Optional[Decimal] = None,
    net_settlement_usd_from_calc: Optional[Decimal] = None,
    laytime_calc_revision: Optional[int] = None,
    is_settlement_final: bool = False,
) -> VoyagePnLSnapshot:
    """
    Compute a frozen voyage P&L snapshot from charter-party economics.

    Parameters
    ----------
    voyage_id               : str     UUID of voyage.
    voyage_reference        : str     Human-readable voyage ref.
    vessel_name             : str     Vessel name.
    cargo_type              : str     Cargo type code.
    load_port               : str     Load port name.
    discharge_port          : str     Discharge port name.
    nominated_quantity_mt   : Decimal Contracted nomination.
    freight_rate_usd_per_mt : Decimal Agreed freight rate (USD/MT).
    quantity_loaded_mt      : Decimal Actual loaded quantity (MT).
    quantity_discharged_mt  : Decimal Outturn discharged quantity (MT), optional.
    laytime_outcome         : str     Outcome from LaytimeCalculation ("DEMURRAGE", etc.).
    gross_demurrage_usd     : Decimal Gross demurrage from calc, or None.
    gross_despatch_usd      : Decimal Gross despatch from calc, or None.
    net_settlement_usd_from_calc : Decimal Net settlement from calc (positive = demurrage cost).
    laytime_calc_revision   : int     Revision number of the calc used.
    is_settlement_final     : bool    True if outcome is SETTLED/DISPUTED.

    Returns
    -------
    VoyagePnLSnapshot   Immutable frozen P&L summary.

    Raises
    ------
    VoyagePnLError
        MISSING_FREIGHT_RATE   if freight_rate_usd_per_mt is None or ≤ 0.
        MISSING_LOADED_QUANTITY if quantity_loaded_mt is None or ≤ 0.
        NEGATIVE_NOMINATED     if nominated_quantity_mt is ≤ 0.
    """
    # ── Input validation ─────────────────────────────────────────────────────
    if not freight_rate_usd_per_mt or freight_rate_usd_per_mt <= Decimal("0"):
        raise VoyagePnLError(
            "MISSING_FREIGHT_RATE",
            f"freight_rate_usd_per_mt must be positive (got {freight_rate_usd_per_mt!r}).",
        )
    if not quantity_loaded_mt or quantity_loaded_mt <= Decimal("0"):
        raise VoyagePnLError(
            "MISSING_LOADED_QUANTITY",
            f"quantity_loaded_mt must be positive (got {quantity_loaded_mt!r}).",
        )
    if nominated_quantity_mt <= Decimal("0"):
        raise VoyagePnLError(
            "NEGATIVE_NOMINATED",
            f"nominated_quantity_mt must be positive (got {nominated_quantity_mt!r}).",
        )

    # ── Cargo outturn ─────────────────────────────────────────────────────────
    shortage_mt: Optional[Decimal] = None
    cargo_outturn_efficiency_pct: Optional[Decimal] = None

    if quantity_discharged_mt is not None:
        shortage_mt = max(
            quantity_loaded_mt - quantity_discharged_mt, Decimal("0.000")
        ).quantize(_4DP, rounding=ROUND_HALF_UP)
        cargo_outturn_efficiency_pct = (
            (quantity_discharged_mt / quantity_loaded_mt * Decimal("100"))
            .quantize(_2DP, rounding=ROUND_HALF_UP)
        )

    # ── Freight revenue ───────────────────────────────────────────────────────
    gross_freight_revenue_usd = (
        (freight_rate_usd_per_mt * quantity_loaded_mt)
        .quantize(_2DP, rounding=ROUND_HALF_UP)
    )

    # ── Laytime settlement ────────────────────────────────────────────────────
    dem_usd = (gross_demurrage_usd or Decimal("0")).quantize(_2DP, rounding=ROUND_HALF_UP)
    des_usd = (gross_despatch_usd or Decimal("0")).quantize(_2DP, rounding=ROUND_HALF_UP)

    if net_settlement_usd_from_calc is not None:
        net_settlement_usd = net_settlement_usd_from_calc.quantize(_2DP, rounding=ROUND_HALF_UP)
        calculation_basis = "LAYTIME_CALCULATION"
    else:
        # No laytime calculation available yet — default to zero settlement
        net_settlement_usd = Decimal("0.00")
        calculation_basis = "ZERO_NO_CALC"

    # ── P&L summary ───────────────────────────────────────────────────────────
    # From charterer's perspective:
    #   net_voyage_pnl = gross_freight_revenue − net_settlement
    #   (positive net_settlement = demurrage cost → reduces P&L)
    #   (negative net_settlement = despatch credit → improves P&L)
    net_voyage_pnl_usd = (gross_freight_revenue_usd - net_settlement_usd).quantize(
        _2DP, rounding=ROUND_HALF_UP
    )

    # Freight efficiency
    freight_efficiency_pct = (
        (quantity_loaded_mt / nominated_quantity_mt * Decimal("100"))
        .quantize(_2DP, rounding=ROUND_HALF_UP)
    )

    return VoyagePnLSnapshot(
        voyage_id=voyage_id,
        voyage_reference=voyage_reference,
        vessel_name=vessel_name,
        cargo_type=cargo_type,
        load_port=load_port,
        discharge_port=discharge_port,
        nominated_quantity_mt=nominated_quantity_mt.quantize(_4DP, rounding=ROUND_HALF_UP),
        quantity_loaded_mt=quantity_loaded_mt.quantize(_4DP, rounding=ROUND_HALF_UP),
        quantity_discharged_mt=(
            quantity_discharged_mt.quantize(_4DP, rounding=ROUND_HALF_UP)
            if quantity_discharged_mt is not None else None
        ),
        shortage_mt=shortage_mt,
        cargo_outturn_efficiency_pct=cargo_outturn_efficiency_pct,
        freight_rate_usd_per_mt=freight_rate_usd_per_mt.quantize(_2DP, rounding=ROUND_HALF_UP),
        gross_freight_revenue_usd=gross_freight_revenue_usd,
        laytime_outcome=laytime_outcome,
        gross_demurrage_usd=dem_usd,
        gross_despatch_usd=des_usd,
        net_settlement_usd=net_settlement_usd,
        net_voyage_pnl_usd=net_voyage_pnl_usd,
        freight_efficiency_pct=freight_efficiency_pct,
        laytime_calc_revision=laytime_calc_revision,
        is_settlement_final=is_settlement_final,
        calculation_basis=calculation_basis,
    )


def compute_voyage_pnl_from_db(voyage) -> VoyagePnLSnapshot:
    """
    High-level convenience wrapper that extracts all inputs from a
    fully populated VesselVoyage ORM object (with .contract and .vessel
    select_related) and computes the P&L snapshot.

    Parameters
    ----------
    voyage : VesselVoyage ORM instance (must have contract and vessel prefetched).

    Returns
    -------
    VoyagePnLSnapshot
    """
    contract = voyage.contract
    vessel = voyage.vessel

    # Attempt to fetch the current LaytimeCalculation
    current_calc = (
        voyage.laytime_calculations.filter(is_current=True).first()
    )

    laytime_outcome = None
    gross_dem = None
    gross_des = None
    net_settlement = None
    calc_revision = None
    is_final = False

    if current_calc:
        laytime_outcome = current_calc.outcome
        gross_dem = current_calc.gross_demurrage_usd
        gross_des = current_calc.gross_despatch_usd
        net_settlement = current_calc.net_settlement_usd
        calc_revision = current_calc.revision
        is_final = current_calc.outcome in ("SETTLED", "DISPUTED")

    return compute_voyage_pnl(
        voyage_id=str(voyage.voyage_id),
        voyage_reference=voyage.voyage_reference,
        vessel_name=vessel.vessel_name,
        cargo_type=contract.cargo_type,
        load_port=voyage.load_port,
        discharge_port=voyage.discharge_port,
        nominated_quantity_mt=contract.nominated_quantity_mt,
        freight_rate_usd_per_mt=contract.freight_rate_usd_per_mt,
        quantity_loaded_mt=voyage.quantity_loaded_mt,
        quantity_discharged_mt=voyage.quantity_discharged_mt,
        laytime_outcome=laytime_outcome,
        gross_demurrage_usd=gross_dem,
        gross_despatch_usd=gross_des,
        net_settlement_usd_from_calc=net_settlement,
        laytime_calc_revision=calc_revision,
        is_settlement_final=is_final,
    )
