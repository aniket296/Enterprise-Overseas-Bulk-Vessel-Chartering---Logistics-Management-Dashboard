"""
backend/apps/voyages/berth_conflict.py
============================================================
Cycle 2.3.1 — Berth Conflict Detection Engine

Provides pure deterministic temporal interval intersection logic and
berth occupancy scheduling validation for active bulk vessel voyages.

Domain Concept
--------------
A bulk vessel occupies a discharge berth during a continuous time window:
  - Actual window:   [berthed_at, discharge_completed_at)
  - Estimated window: [eta_date, eta_date + estimated_discharge_hours)

Two vessels assigned to the same (discharge_port, discharge_berth) are in
conflict if their occupancy windows overlap:
  max(start_1, start_2) < min(end_1, end_2)

This engine powers:
  1. Pre-allocation validation (POST /voyages/berth-conflicts/):
     Testing a proposed berthing window before assigning a berth to prevent
     double-booking at the port.
  2. Fleet-wide conflict sweep (GET /voyages/berth-conflicts/):
     Identifying all active berth collisions in the current operational schedule.
  3. Voyage-specific conflict audit (GET /voyages/{id}/berth-conflicts/):
     Verifying whether a specific voyage is in conflict with any peer vessel.

Air-Gap & Performance
---------------------
- Zero external services, zero mocks, pure Python & Django ORM math.
- In PostgreSQL 16: aligns with GiST tstzrange index (idx_voyages_berth_occupancy_gist)
  from V004 for O(log n) conflict lookups.
- In SQLite / unit-test environments: uses exact ISO-8601 temporal range arithmetic.
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Tuple

from django.db.models import Q
from apps.laytime.engine.fixture_factory import VoyageStatus

_HOURS_PER_DAY = Decimal("24")
_SECONDS_PER_HOUR = Decimal("3600")
_2DP = Decimal("0.01")
_TERMINAL_STATES = {VoyageStatus.COMPLETED.value, "CANCELLED"}


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BerthOccupancyWindow:
    """Effective or proposed temporal window a vessel occupies a berth."""
    voyage_id: Optional[str]
    voyage_reference: Optional[str]
    vessel_name: Optional[str]
    discharge_port: str
    discharge_berth: str
    start_time: datetime
    end_time: datetime
    is_actual: bool            # True if based on berthed_at, False if estimated from ETA
    duration_hours: Decimal

    def __post_init__(self) -> None:
        if self.start_time >= self.end_time:
            raise ValueError(f"Invalid window: start {self.start_time} >= end {self.end_time}")


@dataclass(frozen=True)
class BerthConflictDetail:
    """Detail of a single pairwise conflict between two voyages."""
    voyage_id_a: Optional[str]
    voyage_ref_a: Optional[str]
    voyage_id_b: str
    voyage_ref_b: str
    vessel_name_b: str
    discharge_port: str
    discharge_berth: str
    overlap_start: datetime
    overlap_end: datetime
    overlap_hours: Decimal
    severity: str              # "CRITICAL" if actual overlap, "WARNING" if estimated


@dataclass(frozen=True)
class BerthConflictReport:
    """Consolidated conflict audit result for a port/berth or proposed window."""
    discharge_port: str
    discharge_berth: str
    has_conflict: bool
    conflict_count: int
    conflicts: Tuple[BerthConflictDetail, ...]
    earliest_available_at: Optional[datetime]
    evaluated_at: datetime


# ---------------------------------------------------------------------------
# Core Temporal Mathematics
# ---------------------------------------------------------------------------

def calculate_temporal_overlap(
    start_a: datetime,
    end_a: datetime,
    start_b: datetime,
    end_b: datetime,
) -> Optional[Tuple[datetime, datetime, Decimal]]:
    """
    Computes strict half-open interval overlap [start, end) between window A and B.

    Returns:
        (overlap_start, overlap_end, overlap_hours) if overlapping, else None.
    """
    latest_start = max(start_a, start_b)
    earliest_end = min(end_a, end_b)

    if latest_start < earliest_end:
        overlap_seconds = Decimal(str((earliest_end - latest_start).total_seconds()))
        overlap_hours = (overlap_seconds / _SECONDS_PER_HOUR).quantize(_2DP, rounding=ROUND_HALF_UP)
        return latest_start, earliest_end, overlap_hours

    return None


def resolve_voyage_occupancy_window(voyage) -> Optional[BerthOccupancyWindow]:
    """
    Deduces the authoritative or estimated berth occupancy window for a voyage.

    Returns None if:
      - voyage has no discharge_berth assigned, or
      - voyage is in a terminal state (COMPLETED / CANCELLED).
    """
    if not voyage.discharge_berth or voyage.voyage_status in _TERMINAL_STATES:
        return None

    is_actual = False
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    # 1. Determine Start Time
    if voyage.berthed_at:
        start_time = voyage.berthed_at
        is_actual = True
    elif voyage.ata_date:
        start_time = voyage.ata_date
    elif voyage.eta_date:
        start_time = voyage.eta_date

    if not start_time:
        return None

    # Ensure timezone awareness
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)

    # 2. Determine End Time
    if voyage.discharge_completed_at:
        end_time = voyage.discharge_completed_at
    else:
        # Estimate duration:
        # Priority A: contract allowed laytime hours
        # Priority B: cargo_mt / discharge_rate * 24
        # Fallback C: default 48 hours
        est_hours = Decimal("48.00")
        contract = getattr(voyage, "contract", None)
        if contract:
            if contract.allowed_laytime_hours and contract.allowed_laytime_hours > Decimal("0"):
                est_hours = contract.allowed_laytime_hours
            elif contract.nominated_quantity_mt and contract.discharge_rate_mt_per_day and contract.discharge_rate_mt_per_day > Decimal("0"):
                est_hours = (contract.nominated_quantity_mt / contract.discharge_rate_mt_per_day) * _HOURS_PER_DAY

        duration_sec = int((est_hours * _SECONDS_PER_HOUR).to_integral_value(ROUND_HALF_UP))
        end_time = start_time + timedelta(seconds=max(duration_sec, 3600))

    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=timezone.utc)

    if start_time >= end_time:
        end_time = start_time + timedelta(hours=1)

    duration_hours = (
        Decimal(str((end_time - start_time).total_seconds())) / _SECONDS_PER_HOUR
    ).quantize(_2DP, rounding=ROUND_HALF_UP)

    return BerthOccupancyWindow(
        voyage_id=str(voyage.voyage_id),
        voyage_reference=voyage.voyage_reference,
        vessel_name=voyage.vessel.vessel_name if voyage.vessel else None,
        discharge_port=voyage.discharge_port,
        discharge_berth=voyage.discharge_berth,
        start_time=start_time,
        end_time=end_time,
        is_actual=is_actual,
        duration_hours=duration_hours,
    )


# ---------------------------------------------------------------------------
# Conflict Detection Engine Functions
# ---------------------------------------------------------------------------

def detect_berth_conflicts_for_window(
    discharge_port: str,
    discharge_berth: str,
    start_time: datetime,
    end_time: datetime,
    exclude_voyage_id: Optional[str] = None,
    proposed_voyage_ref: Optional[str] = None,
) -> BerthConflictReport:
    """
    Evaluates whether a proposed berthing window [start_time, end_time) at
    (discharge_port, discharge_berth) conflicts with any active vessel.
    """
    from apps.voyages.models import VesselVoyage

    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=timezone.utc)

    now = datetime.now(tz=timezone.utc)

    # Candidate active voyages at the same port and berth
    qs = (
        VesselVoyage.objects
        .filter(discharge_port__iexact=discharge_port.strip())
        .filter(discharge_berth__iexact=discharge_berth.strip())
        .exclude(voyage_status__in=_TERMINAL_STATES)
        .select_related("vessel", "contract")
    )

    if exclude_voyage_id:
        qs = qs.exclude(voyage_id=exclude_voyage_id)

    conflicts: List[BerthConflictDetail] = []
    latest_busy_end: Optional[datetime] = None

    for candidate in qs:
        cand_win = resolve_voyage_occupancy_window(candidate)
        if not cand_win:
            continue

        overlap = calculate_temporal_overlap(
            start_time, end_time, cand_win.start_time, cand_win.end_time
        )
        if overlap:
            ov_start, ov_end, ov_hours = overlap
            severity = "CRITICAL" if cand_win.is_actual else "WARNING"
            conflicts.append(
                BerthConflictDetail(
                    voyage_id_a=exclude_voyage_id,
                    voyage_ref_a=proposed_voyage_ref,
                    voyage_id_b=cand_win.voyage_id,
                    voyage_ref_b=cand_win.voyage_reference,
                    vessel_name_b=cand_win.vessel_name or "Unknown",
                    discharge_port=discharge_port,
                    discharge_berth=discharge_berth,
                    overlap_start=ov_start,
                    overlap_end=ov_end,
                    overlap_hours=ov_hours,
                    severity=severity,
                )
            )

        if cand_win.end_time > (latest_busy_end or now):
            latest_busy_end = cand_win.end_time

    # Earliest available: if conflicts exist, berth is clear after the latest conflicting occupant finishes
    earliest_available = None
    if conflicts:
        conflicting_ends = [
            c.overlap_end for c in conflicts
        ]
        # Also find actual end of conflicting vessels
        cand_ends = [
            resolve_voyage_occupancy_window(candidate).end_time
            for candidate in qs
            if str(candidate.voyage_id) in {c.voyage_id_b for c in conflicts}
        ]
        earliest_available = max(cand_ends) if cand_ends else max(conflicting_ends)

    return BerthConflictReport(
        discharge_port=discharge_port,
        discharge_berth=discharge_berth,
        has_conflict=len(conflicts) > 0,
        conflict_count=len(conflicts),
        conflicts=tuple(conflicts),
        earliest_available_at=earliest_available,
        evaluated_at=now,
    )


def detect_conflicts_for_voyage(voyage) -> BerthConflictReport:
    """
    Evaluates whether a specific voyage is currently in conflict with any peer vessel.
    """
    window = resolve_voyage_occupancy_window(voyage)
    now = datetime.now(tz=timezone.utc)

    if not window:
        return BerthConflictReport(
            discharge_port=voyage.discharge_port,
            discharge_berth=voyage.discharge_berth or "UNASSIGNED",
            has_conflict=False,
            conflict_count=0,
            conflicts=(),
            earliest_available_at=None,
            evaluated_at=now,
        )

    return detect_berth_conflicts_for_window(
        discharge_port=window.discharge_port,
        discharge_berth=window.discharge_berth,
        start_time=window.start_time,
        end_time=window.end_time,
        exclude_voyage_id=str(voyage.voyage_id),
        proposed_voyage_ref=voyage.voyage_reference,
    )


def sweep_all_active_berth_conflicts(
    discharge_port: Optional[str] = None,
    discharge_berth: Optional[str] = None,
) -> List[BerthConflictDetail]:
    """
    Scans all active voyages in the system (or filtered by port/berth) and identifies
    all pairwise berth overlap collisions.
    """
    from apps.voyages.models import VesselVoyage

    qs = (
        VesselVoyage.objects
        .exclude(voyage_status__in=_TERMINAL_STATES)
        .exclude(discharge_berth__isnull=True)
        .exclude(discharge_berth="")
        .select_related("vessel", "contract")
        .order_by("discharge_port", "discharge_berth")
    )

    if discharge_port:
        qs = qs.filter(discharge_port__iexact=discharge_port.strip())
    if discharge_berth:
        qs = qs.filter(discharge_berth__iexact=discharge_berth.strip())

    windows: List[BerthOccupancyWindow] = []
    for voyage in qs:
        win = resolve_voyage_occupancy_window(voyage)
        if win:
            windows.append(win)

    # Group by (port.lower(), berth.lower())
    groups: dict[Tuple[str, str], List[BerthOccupancyWindow]] = {}
    for win in windows:
        key = (win.discharge_port.strip().lower(), win.discharge_berth.strip().lower())
        groups.setdefault(key, []).append(win)

    all_conflicts: List[BerthConflictDetail] = []

    # Pairwise comparison within each berth group
    for (port, berth), win_list in groups.items():
        n = len(win_list)
        for i in range(n):
            for j in range(i + 1, n):
                w_a = win_list[i]
                w_b = win_list[j]
                overlap = calculate_temporal_overlap(
                    w_a.start_time, w_a.end_time, w_b.start_time, w_b.end_time
                )
                if overlap:
                    ov_start, ov_end, ov_hours = overlap
                    severity = "CRITICAL" if (w_a.is_actual or w_b.is_actual) else "WARNING"
                    all_conflicts.append(
                        BerthConflictDetail(
                            voyage_id_a=w_a.voyage_id,
                            voyage_ref_a=w_a.voyage_reference,
                            voyage_id_b=w_b.voyage_id,
                            voyage_ref_b=w_b.voyage_reference,
                            vessel_name_b=w_b.vessel_name or "Unknown",
                            discharge_port=w_a.discharge_port,
                            discharge_berth=w_a.discharge_berth,
                            overlap_start=ov_start,
                            overlap_end=ov_end,
                            overlap_hours=ov_hours,
                            severity=severity,
                        )
                    )

    return all_conflicts
