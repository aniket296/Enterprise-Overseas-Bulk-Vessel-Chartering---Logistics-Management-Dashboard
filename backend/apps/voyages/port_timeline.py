"""
backend/apps/voyages/port_timeline.py
============================================================
Cycle 2.3.2 — Berth Allocation & Port Congestion Timeline Engine

Provides Gantt-ready berth occupancy schedules, port congestion metrics,
anchorage queue analysis, and conflict-guarded berth assignment for bulk ports.

Capabilities
------------
1. Berth Occupancy Schedule (Gantt-Ready):
   Groups voyages by berth at a discharge port, calculating continuous
   occupancy windows, overlap conflict flags, and schedule status.
2. Anchorage Queue & Congestion KPIs:
   Computes current queue depth, total waiting cargo tonnage, average
   waiting/anchorage hours, and qualitative congestion level (LOW/MODERATE/HIGH).
3. Conflict-Guarded Berth Allocation:
   Assigns or modifies a vessel's berth, enforcing conflict validation
   unless explicitly overridden with force=True.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Tuple

from apps.laytime.engine.fixture_factory import VoyageStatus
from .berth_conflict import (
    calculate_temporal_overlap,
    detect_berth_conflicts_for_window,
    resolve_voyage_occupancy_window,
    BerthConflictReport,
    _TERMINAL_STATES,
)

_2DP = Decimal("0.01")
_SECONDS_PER_HOUR = Decimal("3600")


# ---------------------------------------------------------------------------
# Value Objects / Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BerthScheduleItem:
    """A single vessel occupancy block on a berth timeline (Gantt-ready)."""
    voyage_id: str
    voyage_reference: str
    vessel_name: str
    vessel_imo: str
    cargo_type: str
    quantity_mt: Decimal
    voyage_status: str
    is_actual: bool
    start_time: datetime
    end_time: datetime
    duration_hours: Decimal
    has_conflict: bool
    conflicts_with: Tuple[str, ...]   # List of conflicting voyage references


@dataclass(frozen=True)
class AnchorageQueueItem:
    """A vessel waiting at anchorage or approaching port without a clear berth."""
    voyage_id: str
    voyage_reference: str
    vessel_name: str
    vessel_imo: str
    cargo_type: str
    quantity_mt: Decimal
    voyage_status: str
    eta_date: datetime
    ata_date: Optional[datetime]
    nor_tendered_at: Optional[datetime]
    assigned_berth: Optional[str]
    waiting_hours: Optional[Decimal]


@dataclass(frozen=True)
class PortCongestionKPIs:
    """Key operational metrics quantifying congestion at a bulk discharge port."""
    discharge_port: str
    total_active_vessels: int
    vessels_berthed_count: int
    vessels_at_anchorage_count: int
    vessels_unassigned_berth_count: int
    total_cargo_berthed_mt: Decimal
    total_cargo_waiting_mt: Decimal
    average_anchorage_wait_hours: Optional[Decimal]
    congestion_level: str       # "LOW", "MODERATE", "HIGH"
    as_of: datetime


@dataclass(frozen=True)
class PortBerthTimelineReport:
    """Comprehensive port schedule combining berth Gantt timelines, anchorage queue, and KPIs."""
    discharge_port: str
    kpis: PortCongestionKPIs
    berths: Dict[str, Tuple[BerthScheduleItem, ...]]
    anchorage_queue: Tuple[AnchorageQueueItem, ...]
    total_conflicts_detected: int


class BerthAllocationError(Exception):
    """Raised when assigning a berth fails conflict validation without force override."""
    def __init__(self, conflict_report: BerthConflictReport) -> None:
        self.conflict_report = conflict_report
        super().__init__(
            f"Cannot assign berth {conflict_report.discharge_berth} at {conflict_report.discharge_port}: "
            f"{conflict_report.conflict_count} conflicting vessel(s) detected."
        )


# ---------------------------------------------------------------------------
# Engine Functions
# ---------------------------------------------------------------------------

def get_port_congestion_kpis(discharge_port: str, as_of: Optional[datetime] = None) -> PortCongestionKPIs:
    """
    Computes real-time congestion indicators for a specific discharge port.
    """
    from apps.voyages.models import VesselVoyage

    now = as_of or datetime.now(tz=timezone.utc)
    port_name = discharge_port.strip()

    active_voyages = (
        VesselVoyage.objects
        .filter(discharge_port__iexact=port_name)
        .exclude(voyage_status__in=_TERMINAL_STATES)
        .select_related("vessel", "contract")
    )

    berthed_count = 0
    anchorage_count = 0
    unassigned_count = 0
    cargo_berthed = Decimal("0.000")
    cargo_waiting = Decimal("0.000")
    wait_durations: List[Decimal] = []

    for v in active_voyages:
        qty = v.quantity_loaded_mt or Decimal("0.000")
        is_berthed = v.berthed_at is not None and v.discharge_completed_at is None

        if is_berthed:
            berthed_count += 1
            cargo_berthed += qty
        else:
            # Check if at anchorage / waiting
            if v.ata_date is not None or v.nor_tendered_at is not None or v.voyage_status in (
                VoyageStatus.ARRIVED.value, VoyageStatus.NOR_TENDERED.value
            ):
                anchorage_count += 1
                cargo_waiting += qty

        if not v.discharge_berth:
            unassigned_count += 1

        # Calculate historical or active wait time
        ref_start = v.nor_tendered_at or v.ata_date
        if ref_start and v.berthed_at:
            wait_sec = Decimal(str((v.berthed_at - ref_start).total_seconds()))
            wait_hours = (wait_sec / _SECONDS_PER_HOUR).quantize(_2DP, rounding=ROUND_HALF_UP)
            if wait_hours >= Decimal("0.00"):
                wait_durations.append(wait_hours)

    avg_wait = None
    if wait_durations:
        avg_wait = (sum(wait_durations) / Decimal(str(len(wait_durations)))).quantize(_2DP, rounding=ROUND_HALF_UP)

    # Congestion Level logic
    if anchorage_count >= 3 or (avg_wait is not None and avg_wait >= Decimal("48.00")):
        level = "HIGH"
    elif anchorage_count >= 1 or (avg_wait is not None and avg_wait >= Decimal("24.00")):
        level = "MODERATE"
    else:
        level = "LOW"

    return PortCongestionKPIs(
        discharge_port=port_name,
        total_active_vessels=active_voyages.count(),
        vessels_berthed_count=berthed_count,
        vessels_at_anchorage_count=anchorage_count,
        vessels_unassigned_berth_count=unassigned_count,
        total_cargo_berthed_mt=cargo_berthed,
        total_cargo_waiting_mt=cargo_waiting,
        average_anchorage_wait_hours=avg_wait,
        congestion_level=level,
        as_of=now,
    )


def get_port_berth_timeline(
    discharge_port: str,
    as_of: Optional[datetime] = None,
) -> PortBerthTimelineReport:
    """
    Produces a complete Gantt-ready berth schedule and anchorage queue for discharge_port.
    """
    from apps.voyages.models import VesselVoyage

    now = as_of or datetime.now(tz=timezone.utc)
    port_name = discharge_port.strip()

    kpis = get_port_congestion_kpis(port_name, as_of=now)

    active_voyages = (
        VesselVoyage.objects
        .filter(discharge_port__iexact=port_name)
        .exclude(voyage_status__in=_TERMINAL_STATES)
        .select_related("vessel", "contract")
        .order_by("eta_date")
    )

    berth_groups: Dict[str, List[BerthScheduleItem]] = {}
    anchorage_queue: List[AnchorageQueueItem] = []
    total_conflicts = 0

    # 1. Bucket voyages into berths vs anchorage
    temp_berth_windows: Dict[str, List[Tuple[VesselVoyage, any]]] = {}

    for voyage in active_voyages:
        # Check if voyage has an assigned berth
        if voyage.discharge_berth:
            berth_name = voyage.discharge_berth.strip()
            win = resolve_voyage_occupancy_window(voyage)
            if win:
                temp_berth_windows.setdefault(berth_name, []).append((voyage, win))
        else:
            # Anchorage / Unassigned queue item
            wait_hours = None
            ref_start = voyage.nor_tendered_at or voyage.ata_date
            if ref_start:
                ref_tz = ref_start if ref_start.tzinfo else ref_start.replace(tzinfo=timezone.utc)
                diff = Decimal(str((now - ref_tz).total_seconds()))
                wait_hours = max(Decimal("0.00"), (diff / _SECONDS_PER_HOUR).quantize(_2DP, rounding=ROUND_HALF_UP))

            anchorage_queue.append(
                AnchorageQueueItem(
                    voyage_id=str(voyage.voyage_id),
                    voyage_reference=voyage.voyage_reference,
                    vessel_name=voyage.vessel.vessel_name if voyage.vessel else "Unknown",
                    vessel_imo=voyage.vessel.imo_number if voyage.vessel else "",
                    cargo_type=voyage.contract.cargo_type if voyage.contract else "UNKNOWN",
                    quantity_mt=voyage.quantity_loaded_mt or Decimal("0.000"),
                    voyage_status=voyage.voyage_status,
                    eta_date=voyage.eta_date,
                    ata_date=voyage.ata_date,
                    nor_tendered_at=voyage.nor_tendered_at,
                    assigned_berth=voyage.discharge_berth,
                    waiting_hours=wait_hours,
                )
            )

    # 2. Process each berth group and detect pairwise overlaps
    for berth_name, pairs in temp_berth_windows.items():
        n = len(pairs)
        conflict_map: Dict[str, List[str]] = {str(v.voyage_id): [] for v, _ in pairs}

        for i in range(n):
            v_a, w_a = pairs[i]
            for j in range(i + 1, n):
                v_b, w_b = pairs[j]
                if calculate_temporal_overlap(w_a.start_time, w_a.end_time, w_b.start_time, w_b.end_time):
                    conflict_map[str(v_a.voyage_id)].append(v_b.voyage_reference)
                    conflict_map[str(v_b.voyage_id)].append(v_a.voyage_reference)
                    total_conflicts += 1

        schedule_items = []
        for voyage, win in pairs:
            c_refs = tuple(sorted(conflict_map[str(voyage.voyage_id)]))
            schedule_items.append(
                BerthScheduleItem(
                    voyage_id=str(voyage.voyage_id),
                    voyage_reference=voyage.voyage_reference,
                    vessel_name=voyage.vessel.vessel_name if voyage.vessel else "Unknown",
                    vessel_imo=voyage.vessel.imo_number if voyage.vessel else "",
                    cargo_type=voyage.contract.cargo_type if voyage.contract else "UNKNOWN",
                    quantity_mt=voyage.quantity_loaded_mt or Decimal("0.000"),
                    voyage_status=voyage.voyage_status,
                    is_actual=win.is_actual,
                    start_time=win.start_time,
                    end_time=win.end_time,
                    duration_hours=win.duration_hours,
                    has_conflict=len(c_refs) > 0,
                    conflicts_with=c_refs,
                )
            )
        schedule_items.sort(key=lambda item: item.start_time)
        berth_groups[berth_name] = tuple(schedule_items)

    return PortBerthTimelineReport(
        discharge_port=port_name,
        kpis=kpis,
        berths=berth_groups,
        anchorage_queue=tuple(anchorage_queue),
        total_conflicts_detected=total_conflicts,
    )


def assign_berth_to_voyage(
    voyage,
    berth_name: Optional[str],
    force: bool = False,
) -> Tuple[any, Optional[BerthConflictReport]]:
    """
    Assigns or updates a vessel's discharge_berth, running conflict detection
    unless force=True is provided.

    Returns:
        (updated_voyage, conflict_report_if_any)

    Raises:
        BerthAllocationError if conflict is found and force=False.
    """
    clean_berth = berth_name.strip() if berth_name else None

    # Clearing berth
    if not clean_berth:
        voyage.discharge_berth = None
        voyage.save(update_fields=["discharge_berth", "updated_at"])
        return voyage, None

    # Estimate occupancy window for the proposed berth
    start_time = voyage.berthed_at or voyage.ata_date or voyage.eta_date
    if not start_time:
        start_time = datetime.now(tz=timezone.utc)

    # Estimate end time
    if voyage.discharge_completed_at:
        end_time = voyage.discharge_completed_at
    else:
        est_hours = Decimal("48.00")
        if voyage.contract:
            if voyage.contract.allowed_laytime_hours and voyage.contract.allowed_laytime_hours > Decimal("0"):
                est_hours = voyage.contract.allowed_laytime_hours
            elif voyage.contract.nominated_quantity_mt and voyage.contract.discharge_rate_mt_per_day:
                est_hours = (voyage.contract.nominated_quantity_mt / voyage.contract.discharge_rate_mt_per_day) * Decimal("24")
        duration_sec = int((est_hours * _SECONDS_PER_HOUR).to_integral_value(ROUND_HALF_UP))
        from datetime import timedelta
        end_time = start_time + timedelta(seconds=max(duration_sec, 3600))

    conflict_report = detect_berth_conflicts_for_window(
        discharge_port=voyage.discharge_port,
        discharge_berth=clean_berth,
        start_time=start_time,
        end_time=end_time,
        exclude_voyage_id=str(voyage.voyage_id),
        proposed_voyage_ref=voyage.voyage_reference,
    )

    if conflict_report.has_conflict and not force:
        raise BerthAllocationError(conflict_report)

    voyage.discharge_berth = clean_berth
    voyage.save(update_fields=["discharge_berth", "updated_at"])
    return voyage, conflict_report
