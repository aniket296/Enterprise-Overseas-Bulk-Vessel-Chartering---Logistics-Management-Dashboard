"""
backend/apps/voyages/executive_dashboard.py
============================================================
Cycle 2.5.6 — Executive Logistics KPI Summary & Consolidated Dashboard Aggregator Engine

Unifies high-level enterprise operational, financial, raw material inventory,
chartering contract, and document compliance metrics into a single real-time
executive view for the SAIL Ministry of Steel bulk logistics dashboard.

Aggregates:
  1. Fleet Operations & Cargo Movement:
     - Vessels at sea vs in port, active shipments, cargo MT at sea and in port,
       completed voyages, total delivered volume.
  2. Financial Exposure & Laytime Settlement:
     - Gross freight spend, demurrage incurred vs despatch earned, net voyage P&L,
       demurrage-to-freight risk ratio.
  3. Steel Plant Raw Material Security:
     - Health overview across all 5 SAIL plants (BSP, DSP, RSP, BSL, ISP),
       critical/precautionary plant counts, bottleneck materials, Raw Material Security Index.
  4. Contract Fulfillment & Carrier Reliability:
     - Active charter contracts, nominated vs lifted volumes, fulfillment rate %,
       average carrier reliability score (0-100).
  5. Document Vault Compliance & Operational Risk Alerts:
     - Verified vs quarantined documents, tamper-evidence rate, active berth collisions,
       voyage ETA deviation alerts, critical stockout warnings.
  6. Enterprise Composite Health Score:
     - Weighted health index (0-100) and prioritized actionable executive highlights.

All financial and volumetric arithmetic uses Python Decimal with ROUND_HALF_UP.
Air-gapped and deterministic: queries live PostgreSQL models without synthetic mocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Dict, Any

from django.db.models import Sum, Count, Q
from apps.laytime.engine.fixture_factory import VoyageStatus
from apps.contracts.models import CharterContract
from apps.contracts.scorecard_engine import compute_carrier_scorecards
from apps.documents.models import ShippingDocument, DocumentVerificationStatus
from apps.inventory.models import PlantInventory, StockSafetyStatus
from apps.inventory.runway_engine import SAIL_PLANTS_CONFIG, get_plant_health_summary
from .models import VesselVoyage, LaytimeCalculation
from .berth_conflict import sweep_all_active_berth_conflicts

_2DP = Decimal("0.01")
_3DP = Decimal("0.001")


@dataclass(frozen=True)
class PlantSecuritySummary:
    """Concise inventory security status for an individual steel plant."""
    plant_code: str
    plant_name: str
    lowest_runway_days: Optional[Decimal]
    bottleneck_material: Optional[str]
    health_status: str  # HEALTHY, WARNING, CRITICAL


@dataclass(frozen=True)
class FleetLogisticsKPIs:
    """Vessel movements, active fleet status, and in-transit cargo."""
    total_active_voyages: int
    vessels_at_sea: int
    vessels_in_port: int
    total_cargo_at_sea_mt: Decimal
    total_cargo_in_port_mt: Decimal
    total_completed_voyages: int
    total_delivered_tonnage_mt: Decimal


@dataclass(frozen=True)
class FinancialLogisticsKPIs:
    """Freight commitments, laytime settlement financials, and voyage P&L."""
    total_gross_freight_usd: Decimal
    total_demurrage_incurred_usd: Decimal
    total_despatch_earned_usd: Decimal
    net_laytime_impact_usd: Decimal
    net_voyage_pnl_usd: Decimal
    demurrage_to_freight_ratio_pct: Decimal


@dataclass(frozen=True)
class PlantSecurityKPIs:
    """Raw material supply chain resilience across SAIL steel plants."""
    total_plants_evaluated: int
    plants_at_critical_risk: int
    plants_at_precautionary_risk: int
    plants_healthy: int
    raw_material_security_index_pct: Decimal
    plant_summaries: List[PlantSecuritySummary]


@dataclass(frozen=True)
class ContractFulfillmentKPIs:
    """Charter party contract fulfillment and carrier performance."""
    total_active_contracts: int
    total_nominated_volume_mt: Decimal
    total_lifted_volume_mt: Decimal
    volume_fulfillment_rate_pct: Decimal
    average_carrier_reliability_score: Decimal


@dataclass(frozen=True)
class ComplianceAndRiskKPIs:
    """Document integrity, cryptographic verification, and operational alerts."""
    total_vault_documents: int
    verified_documents_count: int
    quarantined_documents_count: int
    vault_compliance_rate_pct: Decimal
    active_berth_conflicts_count: int
    active_eta_deviations_count: int
    active_critical_stockout_alerts_count: int


@dataclass(frozen=True)
class ExecutiveDashboardKPIs:
    """Consolidated enterprise executive logistics summary."""
    composite_enterprise_health_score: Decimal
    overall_status: str  # HEALTHY, ATTENTION_REQUIRED, CRITICAL_ALERT
    fleet_logistics: FleetLogisticsKPIs
    financial_performance: FinancialLogisticsKPIs
    plant_security: PlantSecurityKPIs
    contract_fulfillment: ContractFulfillmentKPIs
    compliance_and_risk: ComplianceAndRiskKPIs
    executive_highlights: List[str]
    generated_at: datetime


def compute_executive_dashboard_summary() -> ExecutiveDashboardKPIs:
    """
    Computes real-time enterprise logistics KPIs across fleet operations,
    freight finance, steel plant inventories, contracts, and compliance vault.
    """
    now_utc = datetime.now(timezone.utc)

    # ──────────────────────────────────────────────────────────────────────
    # 1. Fleet Operations & Cargo Movement
    # ──────────────────────────────────────────────────────────────────────
    active_at_sea_statuses = [VoyageStatus.IN_TRANSIT.value]
    active_in_port_statuses = [
        VoyageStatus.SCHEDULED.value,
        VoyageStatus.ARRIVED.value,
        VoyageStatus.NOR_TENDERED.value,
        VoyageStatus.BERTHED.value,
        VoyageStatus.COMMENCING.value,
        VoyageStatus.DISCHARGING.value,
    ]
    all_active_statuses = active_at_sea_statuses + active_in_port_statuses

    all_voyages = list(VesselVoyage.objects.select_related("vessel", "contract").all())

    at_sea_voyages = [v for v in all_voyages if v.voyage_status in active_at_sea_statuses]
    in_port_voyages = [v for v in all_voyages if v.voyage_status in active_in_port_statuses]
    completed_voyages = [v for v in all_voyages if v.voyage_status == VoyageStatus.COMPLETED.value]

    total_cargo_at_sea = sum(
        (v.quantity_loaded_mt or Decimal("0.000") for v in at_sea_voyages),
        Decimal("0.000"),
    ).quantize(_3DP, rounding=ROUND_HALF_UP)

    total_cargo_in_port = sum(
        (v.quantity_loaded_mt or Decimal("0.000") for v in in_port_voyages),
        Decimal("0.000"),
    ).quantize(_3DP, rounding=ROUND_HALF_UP)

    total_delivered_mt = sum(
        (v.quantity_discharged_mt or v.quantity_loaded_mt or Decimal("0.000") for v in completed_voyages),
        Decimal("0.000"),
    ).quantize(_3DP, rounding=ROUND_HALF_UP)

    fleet_kpis = FleetLogisticsKPIs(
        total_active_voyages=len(at_sea_voyages) + len(in_port_voyages),
        vessels_at_sea=len(at_sea_voyages),
        vessels_in_port=len(in_port_voyages),
        total_cargo_at_sea_mt=total_cargo_at_sea,
        total_cargo_in_port_mt=total_cargo_in_port,
        total_completed_voyages=len(completed_voyages),
        total_delivered_tonnage_mt=total_delivered_mt,
    )

    # ──────────────────────────────────────────────────────────────────────
    # 2. Financial Performance & Laytime Settlement
    # ──────────────────────────────────────────────────────────────────────
    total_gross_freight = Decimal("0.00")
    for v in all_voyages:
        contract = getattr(v, "contract", None)
        rate = getattr(contract, "freight_rate_usd_per_mt", None) if contract else None
        qty = v.quantity_loaded_mt or (getattr(contract, "nominated_quantity_mt", None) if contract else None)
        if rate and qty:
            total_gross_freight += (rate * qty).quantize(_2DP, rounding=ROUND_HALF_UP)

    # Laytime settlements from current calculations
    current_calcs = list(LaytimeCalculation.objects.filter(is_current=True))
    total_demurrage = sum(
        (c.gross_demurrage_usd or Decimal("0.00") for c in current_calcs),
        Decimal("0.00"),
    ).quantize(_2DP, rounding=ROUND_HALF_UP)

    total_despatch = sum(
        (c.gross_despatch_usd or Decimal("0.00") for c in current_calcs),
        Decimal("0.00"),
    ).quantize(_2DP, rounding=ROUND_HALF_UP)

    net_laytime_impact = (total_demurrage - total_despatch).quantize(_2DP, rounding=ROUND_HALF_UP)
    # Net P&L: Gross freight spend minus laytime impact
    net_voyage_pnl = (total_gross_freight - net_laytime_impact).quantize(_2DP, rounding=ROUND_HALF_UP)

    if total_gross_freight > Decimal("0.00"):
        demurrage_ratio_pct = ((total_demurrage / total_gross_freight) * Decimal("100.00")).quantize(_2DP, rounding=ROUND_HALF_UP)
    else:
        demurrage_ratio_pct = Decimal("0.00")

    financial_kpis = FinancialLogisticsKPIs(
        total_gross_freight_usd=total_gross_freight,
        total_demurrage_incurred_usd=total_demurrage,
        total_despatch_earned_usd=total_despatch,
        net_laytime_impact_usd=net_laytime_impact,
        net_voyage_pnl_usd=net_voyage_pnl,
        demurrage_to_freight_ratio_pct=demurrage_ratio_pct,
    )

    # ──────────────────────────────────────────────────────────────────────
    # 3. Steel Plants Raw Material Security
    # ──────────────────────────────────────────────────────────────────────
    plant_summaries: List[PlantSecuritySummary] = []
    crit_count = 0
    prec_count = 0
    healthy_count = 0

    total_runway_sum = Decimal("0.00")
    plants_evaluated = len(SAIL_PLANTS_CONFIG)

    for code, (p_name, _) in SAIL_PLANTS_CONFIG.items():
        summary = get_plant_health_summary(code)
        lowest_runway = summary.lowest_runway_days

        if lowest_runway is None or lowest_runway < Decimal("7.00"):
            crit_count += 1
            h_status = "CRITICAL"
        elif lowest_runway < Decimal("14.00"):
            prec_count += 1
            h_status = "WARNING"
        else:
            healthy_count += 1
            h_status = "HEALTHY"

        if lowest_runway is not None:
            total_runway_sum += min(lowest_runway, Decimal("45.00"))

        plant_summaries.append(
            PlantSecuritySummary(
                plant_code=code,
                plant_name=p_name,
                lowest_runway_days=lowest_runway,
                bottleneck_material=summary.bottleneck_material,
                health_status=h_status,
            )
        )

    # Security index: (healthy_plants / total_plants * 50) + (avg_runway / 45 * 50)
    avg_runway = total_runway_sum / Decimal(str(plants_evaluated))
    security_index = (
        (Decimal(str(healthy_count)) / Decimal(str(plants_evaluated)) * Decimal("50.00")) +
        ((avg_runway / Decimal("45.00")) * Decimal("50.00"))
    ).quantize(_2DP, rounding=ROUND_HALF_UP)
    security_index = min(Decimal("100.00"), max(Decimal("0.00"), security_index))

    plant_kpis = PlantSecurityKPIs(
        total_plants_evaluated=plants_evaluated,
        plants_at_critical_risk=crit_count,
        plants_at_precautionary_risk=prec_count,
        plants_healthy=healthy_count,
        raw_material_security_index_pct=security_index,
        plant_summaries=plant_summaries,
    )

    # ──────────────────────────────────────────────────────────────────────
    # 4. Contract Fulfillment & Carrier Reliability
    # ──────────────────────────────────────────────────────────────────────
    active_contracts = list(CharterContract.objects.filter(contract_status="ACTIVE"))
    total_nominated_vol = sum((c.nominated_quantity_mt or Decimal("0.000") for c in active_contracts), Decimal("0.000"))

    # Lifted volume on active contracts
    active_contract_ids = [c.contract_id for c in active_contracts]
    total_lifted_vol = sum(
        (v.quantity_loaded_mt or Decimal("0.000") for v in all_voyages if v.contract_id in active_contract_ids),
        Decimal("0.000"),
    )

    if total_nominated_vol > Decimal("0.000"):
        vol_fulfillment_pct = ((total_lifted_vol / total_nominated_vol) * Decimal("100.00")).quantize(_2DP, rounding=ROUND_HALF_UP)
    else:
        vol_fulfillment_pct = Decimal("0.00")

    # Carrier scorecards
    carrier_scorecards = compute_carrier_scorecards()
    if carrier_scorecards:
        avg_carrier_score = (
            sum(c.carrier_reliability_score for c in carrier_scorecards) / Decimal(str(len(carrier_scorecards)))
        ).quantize(_2DP, rounding=ROUND_HALF_UP)
    else:
        avg_carrier_score = Decimal("100.00")

    contract_kpis = ContractFulfillmentKPIs(
        total_active_contracts=len(active_contracts),
        total_nominated_volume_mt=total_nominated_vol.quantize(_3DP, rounding=ROUND_HALF_UP),
        total_lifted_volume_mt=total_lifted_vol.quantize(_3DP, rounding=ROUND_HALF_UP),
        volume_fulfillment_rate_pct=vol_fulfillment_pct,
        average_carrier_reliability_score=avg_carrier_score,
    )

    # ──────────────────────────────────────────────────────────────────────
    # 5. Compliance & Operational Risk Alerts
    # ──────────────────────────────────────────────────────────────────────
    all_docs = list(ShippingDocument.objects.all())
    total_docs = len(all_docs)
    verified_docs = sum(1 for d in all_docs if d.verification_status == DocumentVerificationStatus.HASH_VERIFIED.value)
    quarantined_docs = sum(
        1 for d in all_docs if d.verification_status in [
            DocumentVerificationStatus.QUARANTINED.value,
            DocumentVerificationStatus.HASH_MISMATCH.value,
        ]
    )

    if total_docs > 0:
        vault_compliance_pct = ((Decimal(str(verified_docs)) / Decimal(str(total_docs))) * Decimal("100.00")).quantize(_2DP, rounding=ROUND_HALF_UP)
    else:
        vault_compliance_pct = Decimal("100.00")

    # Active berth conflicts
    try:
        conflicts = sweep_all_active_berth_conflicts()
        active_berth_conflicts = len(conflicts)
    except Exception:
        active_berth_conflicts = 0

    # Active ETA deviations (active voyages with elapsed ETA and no ATA)
    active_eta_deviations = 0
    for v in all_voyages:
        if v.voyage_status in all_active_statuses and v.eta_date and not v.ata_date:
            eta_d = v.eta_date if isinstance(v.eta_date, datetime) else datetime.combine(v.eta_date, datetime.min.time(), tzinfo=timezone.utc)
            if eta_d < now_utc:
                active_eta_deviations += 1

    # Active critical stockouts (< 7 days)
    critical_snapshots_count = (
        PlantInventory.objects
        .filter(safety_status=StockSafetyStatus.CRITICAL.value)
        .count()
    )

    compliance_kpis = ComplianceAndRiskKPIs(
        total_vault_documents=total_docs,
        verified_documents_count=verified_docs,
        quarantined_documents_count=quarantined_docs,
        vault_compliance_rate_pct=vault_compliance_pct,
        active_berth_conflicts_count=active_berth_conflicts,
        active_eta_deviations_count=active_eta_deviations,
        active_critical_stockout_alerts_count=critical_snapshots_count,
    )

    # ──────────────────────────────────────────────────────────────────────
    # 6. Composite Enterprise Health Score & Executive Highlights
    # ──────────────────────────────────────────────────────────────────────
    # Weighted composite index:
    #   - Supply Chain Security Index: 35%
    #   - Carrier Reliability Index: 25%
    #   - Financial & Demurrage Control: 20% (100 - demurrage_ratio * 4)
    #   - Vault Compliance Rate: 10%
    #   - Operational Alert Penalty: 10% (starts at 100, -10 per alert)
    financial_score = max(Decimal("0.00"), Decimal("100.00") - (demurrage_ratio_pct * Decimal("4.00")))
    total_alerts = active_berth_conflicts + active_eta_deviations + critical_snapshots_count
    alert_score = max(Decimal("0.00"), Decimal("100.00") - (Decimal(str(total_alerts)) * Decimal("10.00")))

    composite_score = (
        (security_index * Decimal("0.35")) +
        (avg_carrier_score * Decimal("0.25")) +
        (financial_score * Decimal("0.20")) +
        (vault_compliance_pct * Decimal("0.10")) +
        (alert_score * Decimal("0.10"))
    ).quantize(_2DP, rounding=ROUND_HALF_UP)
    composite_score = min(Decimal("100.00"), max(Decimal("0.00"), composite_score))

    if composite_score >= Decimal("80.00") and crit_count == 0:
        overall_status = "HEALTHY"
    elif composite_score >= Decimal("60.00") and crit_count <= 1:
        overall_status = "ATTENTION_REQUIRED"
    else:
        overall_status = "CRITICAL_ALERT"

    # Actionable Highlights
    highlights: List[str] = []
    if crit_count > 0:
        highlights.append(f"CRITICAL: {crit_count} SAIL plant(s) below 7-day starvation threshold.")
    if prec_count > 0:
        highlights.append(f"PRECAUTIONARY: {prec_count} plant(s) operating with 7-14 days raw material runway.")
    if active_berth_conflicts > 0:
        highlights.append(f"BERTH COLLISION: {active_berth_conflicts} discharge berth conflict(s) detected across ports.")
    if active_eta_deviations > 0:
        highlights.append(f"VOYAGE DELAY: {active_eta_deviations} in-transit vessel(s) past ETA requiring arrival intervention.")
    if quarantined_docs > 0:
        highlights.append(f"VAULT WARNING: {quarantined_docs} document(s) quarantined due to cryptographic hash mismatch.")
    if demurrage_ratio_pct > Decimal("5.00"):
        highlights.append(f"DEMURRAGE EXPOSURE: Demurrage risk ratio at {demurrage_ratio_pct}%, exceeding 5% threshold.")
    if not highlights:
        highlights.append(f"All operations nominal. Fleet of {fleet_kpis.total_active_voyages} active vessels tracking on schedule.")

    return ExecutiveDashboardKPIs(
        composite_enterprise_health_score=composite_score,
        overall_status=overall_status,
        fleet_logistics=fleet_kpis,
        financial_performance=financial_kpis,
        plant_security=plant_kpis,
        contract_fulfillment=contract_kpis,
        compliance_and_risk=compliance_kpis,
        executive_highlights=highlights,
        generated_at=now_utc,
    )
