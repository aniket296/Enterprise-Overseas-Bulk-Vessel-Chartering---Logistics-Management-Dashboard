"""
backend/apps/contracts/scorecard_engine.py
============================================================
Cycle 2.5.3 — Charter Contract Fulfillment & Carrier Reliability Scorecard Engine

Pure deterministic mathematical and analytical engine for evaluating:
  1. Contract-Level Commitment Fulfillment:
     - Nominated quantity vs actual lifted & delivered tonnage
     - Operational tolerance band compliance (-5% to +5% or contract tolerance)
     - Remaining commitment tonnage under charter party
     - Financial laytime audit (demurrage incurred vs despatch earned)
     - Laycan adherence rate (% of voyages arriving on or before laycan_end)
  2. Carrier / Shipowner Reliability Scorecard:
     - Aggregated volume and voyage counts per vessel owner / carrier
     - Demurrage incidence rate (% of voyages triggering demurrage)
     - Laycan punctuality rating (% of voyages compliant with laycan window)
     - Transit outturn shortage percentage
     - Composite Carrier Reliability Score (0 to 100 index)

Arithmetic Guarantees:
  - Strict Python Decimal arithmetic with ROUND_HALF_UP (zero float drift).
  - Pure deterministic output with zero random synthetic drift.
  - Air-gapped: evaluates real Django ORM model entities.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, List, Dict, Any
import datetime
from django.utils import timezone

_2DP = Decimal("0.01")
_3DP = Decimal("0.001")
_4DP = Decimal("0.0001")


@dataclass(frozen=True)
class ContractPerformanceSnapshot:
    """Snapshot of fulfillment and financial performance for a single charter contract."""
    contract_id: str
    contract_reference: str
    vessel_name: str
    owner_name: str
    cargo_type: str
    charterer_plant: str

    # Contractual Commitments
    nominated_quantity_mt: Decimal
    tolerance_pct: Decimal
    min_commitment_mt: Decimal
    max_commitment_mt: Decimal

    # Operational Execution
    total_voyages_count: int
    completed_voyages_count: int
    actual_lifted_mt: Decimal
    actual_delivered_mt: Decimal
    total_shortage_mt: Decimal
    fulfillment_pct: Decimal
    fulfillment_status: str  # "COMPLIANT", "UNDER_FULFILLED", "OVER_FULFILLED", "PENDING"
    remaining_commitment_mt: Decimal

    # Financial Summary
    freight_rate_usd_per_mt: Decimal
    total_freight_paid_usd: Decimal
    total_demurrage_paid_usd: Decimal
    total_despatch_earned_usd: Decimal
    net_laytime_impact_usd: Decimal

    # Operational Quality
    laycan_adherence_rate_pct: Decimal
    as_of: datetime.datetime


@dataclass(frozen=True)
class CarrierScorecardSnapshot:
    """Aggregated performance scorecard for a commercial carrier / vessel owner."""
    owner_name: str
    total_fixtures: int
    total_voyages: int
    completed_voyages: int
    total_carried_mt: Decimal
    total_freight_usd: Decimal
    total_demurrage_usd: Decimal
    total_despatch_usd: Decimal
    net_laytime_usd: Decimal
    demurrage_incidence_pct: Decimal
    laycan_adherence_pct: Decimal
    average_shortage_pct: Decimal
    carrier_reliability_score: Decimal  # Composite score: 0 to 100


def compute_contract_performance(contract) -> ContractPerformanceSnapshot:
    """
    Compute fulfillment and operational execution scorecard for a single CharterContract.

    Parameters
    ----------
    contract : CharterContract ORM instance (with prefetched voyages).

    Returns
    -------
    ContractPerformanceSnapshot
    """
    from apps.laytime.engine.fixture_factory import VoyageStatus

    now = timezone.now()
    vessel = contract.vessel
    vessel_name = vessel.vessel_name if vessel else "UNKNOWN"
    owner_name = vessel.owner_name if vessel else "UNKNOWN"

    nom_qty = contract.nominated_quantity_mt or Decimal("0.000")
    tol_pct = contract.tolerance_pct or Decimal("5.00")

    tol_factor = tol_pct / Decimal("100.00")
    min_commitment = (nom_qty * (Decimal("1.00") - tol_factor)).quantize(_3DP, rounding=ROUND_HALF_UP)
    max_commitment = (nom_qty * (Decimal("1.00") + tol_factor)).quantize(_3DP, rounding=ROUND_HALF_UP)

    voyages = list(contract.voyages.prefetch_related("laytime_calculations").all())
    total_voyages = len(voyages)
    completed_voyages = 0

    actual_lifted = Decimal("0.000")
    actual_delivered = Decimal("0.000")
    total_shortage = Decimal("0.000")

    total_demurrage = Decimal("0.00")
    total_despatch = Decimal("0.00")
    total_net_laytime = Decimal("0.00")

    laycan_on_time_count = 0
    laycan_eval_count = 0

    for v in voyages:
        if v.voyage_status == VoyageStatus.COMPLETED.value:
            completed_voyages += 1

        loaded = v.quantity_loaded_mt or Decimal("0.000")
        actual_lifted += loaded

        if v.quantity_discharged_mt is not None:
            discharged = v.quantity_discharged_mt
            actual_delivered += discharged
            shortage = max(loaded - discharged, Decimal("0.000"))
            total_shortage += shortage

        # Laycan adherence evaluation
        # Compliant if ata_date <= laycan_end (end of day UTC) or arrived on/before laycan_end
        if contract.laycan_end and (v.ata_date or v.eta_date):
            arrival_dt = v.ata_date or v.eta_date
            arrival_date = arrival_dt.date() if isinstance(arrival_dt, datetime.datetime) else arrival_dt
            laycan_eval_count += 1
            if arrival_date <= contract.laycan_end:
                laycan_on_time_count += 1

        # Laytime settlement
        for calc in v.laytime_calculations.all():
            if calc.is_current:
                dem = (calc.gross_demurrage_usd or Decimal("0.00")).quantize(_2DP, rounding=ROUND_HALF_UP)
                des = (calc.gross_despatch_usd or Decimal("0.00")).quantize(_2DP, rounding=ROUND_HALF_UP)
                net = (calc.net_settlement_usd or Decimal("0.00")).quantize(_2DP, rounding=ROUND_HALF_UP)
                total_demurrage += dem
                total_despatch += des
                total_net_laytime += net
                break

    # Fulfillment metrics
    if nom_qty > Decimal("0.000"):
        fulfillment_pct = ((actual_lifted / nom_qty) * Decimal("100.00")).quantize(_2DP, rounding=ROUND_HALF_UP)
    else:
        fulfillment_pct = Decimal("0.00")

    if actual_lifted == Decimal("0.000"):
        fulfillment_status = "PENDING"
    elif actual_lifted < min_commitment:
        fulfillment_status = "UNDER_FULFILLED"
    elif actual_lifted > max_commitment:
        fulfillment_status = "OVER_FULFILLED"
    else:
        fulfillment_status = "COMPLIANT"

    remaining_commitment = max(min_commitment - actual_lifted, Decimal("0.000")).quantize(_3DP, rounding=ROUND_HALF_UP)

    freight_rate = contract.freight_rate_usd_per_mt or Decimal("0.00")
    total_freight_paid = (actual_lifted * freight_rate).quantize(_2DP, rounding=ROUND_HALF_UP)

    if laycan_eval_count > 0:
        laycan_adherence_pct = (
            (Decimal(str(laycan_on_time_count)) / Decimal(str(laycan_eval_count)) * Decimal("100.00"))
            .quantize(_2DP, rounding=ROUND_HALF_UP)
        )
    else:
        laycan_adherence_pct = Decimal("100.00")

    return ContractPerformanceSnapshot(
        contract_id=str(contract.contract_id),
        contract_reference=contract.contract_reference,
        vessel_name=vessel_name,
        owner_name=owner_name,
        cargo_type=contract.cargo_type,
        charterer_plant=contract.charterer_plant,
        nominated_quantity_mt=nom_qty.quantize(_3DP, rounding=ROUND_HALF_UP),
        tolerance_pct=tol_pct.quantize(_2DP, rounding=ROUND_HALF_UP),
        min_commitment_mt=min_commitment,
        max_commitment_mt=max_commitment,
        total_voyages_count=total_voyages,
        completed_voyages_count=completed_voyages,
        actual_lifted_mt=actual_lifted.quantize(_3DP, rounding=ROUND_HALF_UP),
        actual_delivered_mt=actual_delivered.quantize(_3DP, rounding=ROUND_HALF_UP),
        total_shortage_mt=total_shortage.quantize(_3DP, rounding=ROUND_HALF_UP),
        fulfillment_pct=fulfillment_pct,
        fulfillment_status=fulfillment_status,
        remaining_commitment_mt=remaining_commitment,
        freight_rate_usd_per_mt=freight_rate.quantize(_2DP, rounding=ROUND_HALF_UP),
        total_freight_paid_usd=total_freight_paid,
        total_demurrage_paid_usd=total_demurrage.quantize(_2DP, rounding=ROUND_HALF_UP),
        total_despatch_earned_usd=total_despatch.quantize(_2DP, rounding=ROUND_HALF_UP),
        net_laytime_impact_usd=total_net_laytime.quantize(_2DP, rounding=ROUND_HALF_UP),
        laycan_adherence_rate_pct=laycan_adherence_pct,
        as_of=now,
    )


def compute_carrier_scorecards(vessel_qs=None, voyage_qs=None) -> List[CarrierScorecardSnapshot]:
    """
    Compute reliability scorecards aggregated by vessel owner / commercial carrier.

    Parameters
    ----------
    vessel_qs : Optional QuerySet of Vessel. If None, queries all active vessels.
    voyage_qs : Optional QuerySet of VesselVoyage. If None, queries all voyages.

    Returns
    -------
    List[CarrierScorecardSnapshot] sorted by carrier_reliability_score DESC.
    """
    from apps.contracts.models import Vessel
    from apps.voyages.models import VesselVoyage
    from apps.laytime.engine.fixture_factory import VoyageStatus

    if vessel_qs is None:
        vessel_qs = Vessel.objects.all()
    if voyage_qs is None:
        voyage_qs = VesselVoyage.objects.all()

    # Pre-fetch voyages with contracts and calculations
    voyages = list(
        voyage_qs.select_related("vessel", "contract")
        .prefetch_related("laytime_calculations")
    )

    # Carrier mapping: owner_name -> data dict
    carriers: Dict[str, Dict[str, Any]] = {}

    for v in voyages:
        owner = v.vessel.owner_name if (v.vessel and v.vessel.owner_name) else "UNKNOWN_CARRIER"

        if owner not in carriers:
            carriers[owner] = {
                "owner_name": owner,
                "fixture_ids": set(),
                "voyage_count": 0,
                "completed_count": 0,
                "total_carried_mt": Decimal("0.000"),
                "total_freight_usd": Decimal("0.00"),
                "total_demurrage_usd": Decimal("0.00"),
                "total_despatch_usd": Decimal("0.00"),
                "net_laytime_usd": Decimal("0.00"),
                "demurrage_voyage_count": 0,
                "laycan_eval_count": 0,
                "laycan_on_time_count": 0,
                "shortage_sum_pct": Decimal("0.00"),
                "shortage_eval_count": 0,
            }

        c = carriers[owner]
        c["voyage_count"] += 1
        if v.contract_id:
            c["fixture_ids"].add(v.contract_id)

        if v.voyage_status == VoyageStatus.COMPLETED.value:
            c["completed_count"] += 1

        loaded = v.quantity_loaded_mt or Decimal("0.000")
        c["total_carried_mt"] += loaded

        contract = v.contract
        rate = contract.freight_rate_usd_per_mt if contract else Decimal("0.00")
        c["total_freight_usd"] += (loaded * rate).quantize(_2DP, rounding=ROUND_HALF_UP)

        # Laytime settlement
        dem_val = Decimal("0.00")
        des_val = Decimal("0.00")
        net_val = Decimal("0.00")

        for calc in v.laytime_calculations.all():
            if calc.is_current:
                dem_val = (calc.gross_demurrage_usd or Decimal("0.00")).quantize(_2DP, rounding=ROUND_HALF_UP)
                des_val = (calc.gross_despatch_usd or Decimal("0.00")).quantize(_2DP, rounding=ROUND_HALF_UP)
                net_val = (calc.net_settlement_usd or Decimal("0.00")).quantize(_2DP, rounding=ROUND_HALF_UP)
                break

        c["total_demurrage_usd"] += dem_val
        c["total_despatch_usd"] += des_val
        c["net_laytime_usd"] += net_val
        if dem_val > Decimal("0.00"):
            c["demurrage_voyage_count"] += 1

        # Laycan compliance
        if contract and contract.laycan_end and (v.ata_date or v.eta_date):
            arrival_dt = v.ata_date or v.eta_date
            arrival_date = arrival_dt.date() if isinstance(arrival_dt, datetime.datetime) else arrival_dt
            c["laycan_eval_count"] += 1
            if arrival_date <= contract.laycan_end:
                c["laycan_on_time_count"] += 1

        # Shortage %
        if loaded > Decimal("0.000") and v.quantity_discharged_mt is not None:
            discharged = v.quantity_discharged_mt
            shortage = max(loaded - discharged, Decimal("0.000"))
            shortage_pct = (shortage / loaded) * Decimal("100.00")
            c["shortage_sum_pct"] += shortage_pct
            c["shortage_eval_count"] += 1

    snapshots: List[CarrierScorecardSnapshot] = []

    for owner_name, data in carriers.items():
        voy_count = data["voyage_count"]

        # 1. Demurrage incidence rate (% of voyages triggering demurrage)
        dem_incidence = (
            (Decimal(str(data["demurrage_voyage_count"])) / Decimal(str(voy_count)) * Decimal("100.00"))
            .quantize(_2DP, rounding=ROUND_HALF_UP)
            if voy_count > 0 else Decimal("0.00")
        )

        # 2. Laycan adherence rate (% on-time)
        if data["laycan_eval_count"] > 0:
            laycan_pct = (
                (Decimal(str(data["laycan_on_time_count"])) / Decimal(str(data["laycan_eval_count"])) * Decimal("100.00"))
                .quantize(_2DP, rounding=ROUND_HALF_UP)
            )
        else:
            laycan_pct = Decimal("100.00")

        # 3. Average shortage %
        if data["shortage_eval_count"] > 0:
            avg_shortage_pct = (
                (data["shortage_sum_pct"] / Decimal(str(data["shortage_eval_count"])))
                .quantize(_2DP, rounding=ROUND_HALF_UP)
            )
        else:
            avg_shortage_pct = Decimal("0.00")

        # 4. Composite Carrier Reliability Rating Score (0 to 100):
        #    - Laycan adherence: 40 points (laycan_pct * 0.40)
        #    - Demurrage avoidance: 35 points ((100 - dem_incidence) * 0.35)
        #    - Outturn accuracy: 25 points (max(100 - avg_shortage * 10, 0) * 0.25)
        score_laycan = laycan_pct * Decimal("0.40")
        score_demurrage = (Decimal("100.00") - dem_incidence) * Decimal("0.35")
        shortage_penalty = min(avg_shortage_pct * Decimal("20.00"), Decimal("100.00"))
        score_outturn = (Decimal("100.00") - shortage_penalty) * Decimal("0.25")

        raw_score = score_laycan + score_demurrage + score_outturn
        composite_score = min(max(raw_score, Decimal("0.00")), Decimal("100.00")).quantize(_2DP, rounding=ROUND_HALF_UP)

        snapshots.append(
            CarrierScorecardSnapshot(
                owner_name=owner_name,
                total_fixtures=len(data["fixture_ids"]),
                total_voyages=voy_count,
                completed_voyages=data["completed_count"],
                total_carried_mt=data["total_carried_mt"].quantize(_3DP, rounding=ROUND_HALF_UP),
                total_freight_usd=data["total_freight_usd"].quantize(_2DP, rounding=ROUND_HALF_UP),
                total_demurrage_usd=data["total_demurrage_usd"].quantize(_2DP, rounding=ROUND_HALF_UP),
                total_despatch_usd=data["total_despatch_usd"].quantize(_2DP, rounding=ROUND_HALF_UP),
                net_laytime_usd=data["net_laytime_usd"].quantize(_2DP, rounding=ROUND_HALF_UP),
                demurrage_incidence_pct=dem_incidence,
                laycan_adherence_pct=laycan_pct,
                average_shortage_pct=avg_shortage_pct,
                carrier_reliability_score=composite_score,
            )
        )

    # Sort descending by composite score, then by carried tonnage
    snapshots.sort(key=lambda s: (s.carrier_reliability_score, s.total_carried_mt), reverse=True)
    return snapshots
