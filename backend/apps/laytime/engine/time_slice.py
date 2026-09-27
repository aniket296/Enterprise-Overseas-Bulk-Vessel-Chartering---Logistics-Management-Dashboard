"""
backend/apps/laytime/engine/time_slice.py
============================================================
Cycle 1.2.3 — Time-Slice Interval Discretization Engine

Chronologically segments raw Statement of Facts (SOF) port log events
and contractual milestones into a sequence of contiguous, non-overlapping,
uniform half-open time slices:

    [T_0, T_1), [T_1, T_2), [T_2, T_3), ..., [T_{n-1}, T_n)

Mathematical & Operational Guarantees
-------------------------------------
1. Strict Contiguity:
   Slice[i].end_time == Slice[i+1].start_time for all i.
   No gaps, no overlaps.

2. Deterministic Invariant:
   sum(slice.duration_seconds for slice in slices) == (T_n - T_0).total_seconds()
   sum(slice.duration_hours for slice in slices) == total_hours (within 6dp rounding epsilon)

3. Exact Milestone Partitioning:
   `laytime_commences_at` and `discharge_completed_at` are injected into
   the partition boundaries. Therefore, NO slice ever straddles the
   commencement or completion boundaries. Slices are deterministically
   pre-commencement, active-laytime, or post-completion.

4. Constant State Uniformity:
   Every slice carries invariant state throughout its entire duration:
   weather, machinery/crane breakdowns, labor disputes, berthing state,
   and active discharging operations.
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional, Sequence

# ---------------------------------------------------------------------------
# Precision context (shared across laytime engine)
# ---------------------------------------------------------------------------
_ENGINE_CONTEXT = decimal.Context(
    prec=28,
    rounding=ROUND_HALF_UP,
    traps=[decimal.DivisionByZero, decimal.InvalidOperation, decimal.Overflow],
)
_6DP = Decimal("0.000001")
_SECONDS_PER_HOUR = Decimal("3600")


# ---------------------------------------------------------------------------
# Enums mirroring V001 DB schema
# ---------------------------------------------------------------------------
class SOFEventType(str, Enum):
    # Arrival / Notice sequence
    VESSEL_ARRIVED_PILOT_STATION  = "VESSEL_ARRIVED_PILOT_STATION"
    VESSEL_ARRIVED_ANCHORAGE      = "VESSEL_ARRIVED_ANCHORAGE"
    NOR_TENDERED                  = "NOR_TENDERED"
    NOR_ACCEPTED                  = "NOR_ACCEPTED"
    NOR_REJECTED                  = "NOR_REJECTED"
    VESSEL_SHIFTED_ANCHORAGE      = "VESSEL_SHIFTED_ANCHORAGE"

    # Berthing sequence
    PILOT_BOARDED                 = "PILOT_BOARDED"
    VESSEL_COMMENCED_BERTHING     = "VESSEL_COMMENCED_BERTHING"
    FIRST_LINE_ASHORE             = "FIRST_LINE_ASHORE"
    ALL_FAST                      = "ALL_FAST"
    GANGWAY_DOWN                  = "GANGWAY_DOWN"

    # Pre-discharge operations
    CUSTOMS_BOARDING              = "CUSTOMS_BOARDING"
    CUSTOMS_CLEARED               = "CUSTOMS_CLEARED"
    DRAFT_SURVEY_COMMENCED        = "DRAFT_SURVEY_COMMENCED"
    DRAFT_SURVEY_COMPLETED        = "DRAFT_SURVEY_COMPLETED"
    HATCHES_OPENED                = "HATCHES_OPENED"

    # Discharge operations
    DISCHARGE_COMMENCED           = "DISCHARGE_COMMENCED"
    DISCHARGE_SUSPENDED           = "DISCHARGE_SUSPENDED"
    DISCHARGE_RESUMED             = "DISCHARGE_RESUMED"
    DISCHARGE_COMPLETED           = "DISCHARGE_COMPLETED"

    # Interruption events
    RAIN_COMMENCED                = "RAIN_COMMENCED"
    RAIN_CEASED                   = "RAIN_CEASED"
    WIND_FORCE_EXCEEDED_LIMIT     = "WIND_FORCE_EXCEEDED_LIMIT"
    WIND_FORCE_WITHIN_LIMIT       = "WIND_FORCE_WITHIN_LIMIT"
    CRANE_BREAKDOWN_COMMENCED     = "CRANE_BREAKDOWN_COMMENCED"
    CRANE_BREAKDOWN_RESUMED       = "CRANE_BREAKDOWN_RESUMED"
    POWER_FAILURE_COMMENCED       = "POWER_FAILURE_COMMENCED"
    POWER_FAILURE_RESUMED         = "POWER_FAILURE_RESUMED"
    LABOUR_DISPUTE_COMMENCED      = "LABOUR_DISPUTE_COMMENCED"
    LABOUR_DISPUTE_ENDED          = "LABOUR_DISPUTE_ENDED"
    BERTH_CONGESTION_DELAY_START  = "BERTH_CONGESTION_DELAY_START"
    BERTH_CONGESTION_DELAY_END    = "BERTH_CONGESTION_DELAY_END"

    # Departure sequence
    HATCHES_CLOSED                = "HATCHES_CLOSED"
    DRAFT_SURVEY_FINAL            = "DRAFT_SURVEY_FINAL"
    LINES_RELEASED                = "LINES_RELEASED"
    VESSEL_UNMOORED               = "VESSEL_UNMOORED"
    PILOT_DISEMBARKED             = "PILOT_DISEMBARKED"
    VESSEL_DEPARTED               = "VESSEL_DEPARTED"


class IntervalCountStatus(str, Enum):
    FULLY_COUNTED     = "FULLY_COUNTED"
    PARTIALLY_COUNTED = "PARTIALLY_COUNTED"
    EXCLUDED          = "EXCLUDED"
    DISPUTED          = "DISPUTED"


# ---------------------------------------------------------------------------
# Domain Models
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SOFEvent:
    """A single logged event from Statement of Facts."""
    timestamp: datetime
    event_type: SOFEventType
    sequence_number: Optional[int] = None
    description: str = ""
    cranes_working: Optional[int] = None
    cranes_total: Optional[int] = None
    weather_condition: Optional[str] = None
    count_status_override: Optional[IntervalCountStatus] = None
    count_fraction_override: Optional[Decimal] = None
    is_amendment: bool = False
    amended_sof_id: Optional[str] = None
    sof_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError(f"SOFEvent timestamp {self.timestamp} must be timezone-aware (UTC).")


@dataclass(frozen=True)
class OperationalState:
    """The uniform state of port operations active during a time slice."""
    is_berthed: bool = False
    is_discharging: bool = False
    is_rain: bool = False
    is_wind_excessive: bool = False
    is_crane_breakdown: bool = False
    is_power_failure: bool = False
    is_labour_dispute: bool = False
    is_berth_congestion: bool = False
    cranes_working: Optional[int] = None
    cranes_total: Optional[int] = None

    @property
    def has_weather_interruption(self) -> bool:
        return self.is_rain or self.is_wind_excessive

    @property
    def has_mechanical_interruption(self) -> bool:
        return self.is_crane_breakdown or self.is_power_failure

    @property
    def has_operational_interruption(self) -> bool:
        return (
            self.has_weather_interruption
            or self.has_mechanical_interruption
            or self.is_labour_dispute
            or self.is_berth_congestion
        )

    def active_interruption_reasons(self) -> tuple[str, ...]:
        reasons = []
        if self.is_rain:
            reasons.append("Rain / Precipitation")
        if self.is_wind_excessive:
            reasons.append("Excessive Wind Force")
        if self.is_crane_breakdown:
            reasons.append("Crane Breakdown")
        if self.is_power_failure:
            reasons.append("Port Power Failure")
        if self.is_labour_dispute:
            reasons.append("Labour Dispute / Strike")
        if self.is_berth_congestion:
            reasons.append("Berth Congestion Delay")
        return tuple(reasons)


@dataclass(frozen=True)
class TimeSlice:
    """A discrete, uniform time interval [start_time, end_time)."""
    index: int
    start_time: datetime
    end_time: datetime
    duration_seconds: int
    duration_hours: Decimal
    state: OperationalState
    events_at_start: tuple[SOFEvent, ...]
    is_pre_commencement: bool
    is_post_completion: bool

    def __post_init__(self) -> None:
        if self.start_time >= self.end_time:
            raise ValueError(f"Invalid slice interval: start {self.start_time} >= end {self.end_time}")

    @property
    def is_active_laytime_window(self) -> bool:
        """True if this slice falls strictly within [laytime_commences_at, discharge_completed_at)."""
        return not self.is_pre_commencement and not self.is_post_completion

    def __str__(self) -> str:
        tag = "PRE" if self.is_pre_commencement else ("POST" if self.is_post_completion else "ACTIVE")
        reasons = ", ".join(self.state.active_interruption_reasons()) or "Normal Operation"
        return (
            f"Slice #{self.index} [{tag}] "
            f"{self.start_time.isoformat()} -> {self.end_time.isoformat()} "
            f"({self.duration_hours}h): {reasons}"
        )


@dataclass(frozen=True)
class DiscretizationResult:
    """Immutable result of time-slice discretization."""
    slices: tuple[TimeSlice, ...]
    total_port_seconds: int
    total_port_hours: Decimal
    active_laytime_seconds: int
    active_laytime_hours: Decimal
    pre_commencement_seconds: int
    pre_commencement_hours: Decimal
    post_completion_seconds: int
    post_completion_hours: Decimal
    timeline_start: datetime
    timeline_end: datetime


# ---------------------------------------------------------------------------
# Discretization Engine
# ---------------------------------------------------------------------------
class TimeSliceDiscretizationEngine:
    """
    Transforms SOF events and contractual milestones into contiguous TimeSlices.
    """

    def __init__(
        self,
        events: Sequence[SOFEvent],
        laytime_commences_at: Optional[datetime] = None,
        discharge_completed_at: Optional[datetime] = None,
        timeline_start_override: Optional[datetime] = None,
        timeline_end_override: Optional[datetime] = None,
    ) -> None:
        if laytime_commences_at is not None and laytime_commences_at.tzinfo is None:
            raise ValueError("laytime_commences_at must be timezone-aware (UTC).")
        if discharge_completed_at is not None and discharge_completed_at.tzinfo is None:
            raise ValueError("discharge_completed_at must be timezone-aware (UTC).")
        if (
            laytime_commences_at is not None
            and discharge_completed_at is not None
            and laytime_commences_at > discharge_completed_at
        ):
            raise ValueError(
                f"laytime_commences_at ({laytime_commences_at}) cannot be after "
                f"discharge_completed_at ({discharge_completed_at})."
            )

        self._raw_events = list(events)
        self._commences_at = laytime_commences_at
        self._completed_at = discharge_completed_at
        self._start_override = timeline_start_override
        self._end_override = timeline_end_override

    def discretize(self) -> DiscretizationResult:
        """
        Execute discretization and generate the time slice ledger.
        """
        # 1. Clean & Sort Events
        events_by_id = {}
        for ev in self._raw_events:
            # If an amendment references a previous SOF ID, replace or record
            events_by_id[ev.sof_id or f"seq_{ev.sequence_number}_{ev.timestamp.isoformat()}"] = ev

        cleaned_events = sorted(
            self._raw_events,
            key=lambda e: (e.timestamp, e.sequence_number or 0)
        )

        # 2. Gather All Cut Points
        cut_points_set = set()
        for ev in cleaned_events:
            cut_points_set.add(ev.timestamp)

        if self._commences_at is not None:
            cut_points_set.add(self._commences_at)

        if self._completed_at is not None:
            cut_points_set.add(self._completed_at)

        if self._start_override is not None:
            cut_points_set.add(self._start_override)

        if self._end_override is not None:
            cut_points_set.add(self._end_override)

        if len(cut_points_set) < 2:
            raise ValueError("At least two distinct chronological points are required to form time slices.")

        sorted_cuts = sorted(cut_points_set)

        # Apply overrides to start/end boundaries if requested
        t_start = self._start_override or sorted_cuts[0]
        t_end = self._end_override or sorted_cuts[-1]

        if t_start >= t_end:
            raise ValueError(f"Timeline start ({t_start}) must be before timeline end ({t_end}).")

        # Keep only cut points within [t_start, t_end]
        valid_cuts = [t for t in sorted_cuts if t_start <= t <= t_end]
        if valid_cuts[0] != t_start:
            valid_cuts.insert(0, t_start)
        if valid_cuts[-1] != t_end:
            valid_cuts.append(t_end)

        # Group events by their exact timestamp
        events_by_ts: dict[datetime, list[SOFEvent]] = {}
        for ev in cleaned_events:
            events_by_ts.setdefault(ev.timestamp, []).append(ev)

        # 3. Step forward and maintain operational state
        # Initial mutable state tracker
        curr_berthed = False
        curr_discharging = False
        curr_rain = False
        curr_wind = False
        curr_crane_breakdown = False
        curr_power_failure = False
        curr_labour_dispute = False
        curr_berth_congestion = False
        curr_cranes_working: Optional[int] = None
        curr_cranes_total: Optional[int] = None

        slices: list[TimeSlice] = []

        # Before timeline start, advance state for any events occurring before t_start
        for ev in cleaned_events:
            if ev.timestamp < t_start:
                (
                    curr_berthed,
                    curr_discharging,
                    curr_rain,
                    curr_wind,
                    curr_crane_breakdown,
                    curr_power_failure,
                    curr_labour_dispute,
                    curr_berth_congestion,
                    curr_cranes_working,
                    curr_cranes_total,
                ) = self._apply_event_to_state(
                    ev,
                    curr_berthed,
                    curr_discharging,
                    curr_rain,
                    curr_wind,
                    curr_crane_breakdown,
                    curr_power_failure,
                    curr_labour_dispute,
                    curr_berth_congestion,
                    curr_cranes_working,
                    curr_cranes_total,
                )

        for i in range(len(valid_cuts) - 1):
            t_curr = valid_cuts[i]
            t_next = valid_cuts[i + 1]

            # Apply any events occurring at t_curr
            evs_at_t = events_by_ts.get(t_curr, [])
            for ev in evs_at_t:
                (
                    curr_berthed,
                    curr_discharging,
                    curr_rain,
                    curr_wind,
                    curr_crane_breakdown,
                    curr_power_failure,
                    curr_labour_dispute,
                    curr_berth_congestion,
                    curr_cranes_working,
                    curr_cranes_total,
                ) = self._apply_event_to_state(
                    ev,
                    curr_berthed,
                    curr_discharging,
                    curr_rain,
                    curr_wind,
                    curr_crane_breakdown,
                    curr_power_failure,
                    curr_labour_dispute,
                    curr_berth_congestion,
                    curr_cranes_working,
                    curr_cranes_total,
                )

            # Build state snapshot for [t_curr, t_next)
            slice_state = OperationalState(
                is_berthed=curr_berthed,
                is_discharging=curr_discharging,
                is_rain=curr_rain,
                is_wind_excessive=curr_wind,
                is_crane_breakdown=curr_crane_breakdown,
                is_power_failure=curr_power_failure,
                is_labour_dispute=curr_labour_dispute,
                is_berth_congestion=curr_berth_congestion,
                cranes_working=curr_cranes_working,
                cranes_total=curr_cranes_total,
            )

            dur_sec = int((t_next - t_curr).total_seconds())
            with decimal.localcontext(_ENGINE_CONTEXT):
                dur_hours = (Decimal(str(dur_sec)) / _SECONDS_PER_HOUR).quantize(_6DP, rounding=ROUND_HALF_UP)

            # Determine pre / active / post flag
            is_pre = False
            if self._commences_at is not None and t_next <= self._commences_at:
                is_pre = True

            is_post = False
            if self._completed_at is not None and t_curr >= self._completed_at:
                is_post = True

            slice_obj = TimeSlice(
                index=i,
                start_time=t_curr,
                end_time=t_next,
                duration_seconds=dur_sec,
                duration_hours=dur_hours,
                state=slice_state,
                events_at_start=tuple(evs_at_t),
                is_pre_commencement=is_pre,
                is_post_completion=is_post,
            )
            slices.append(slice_obj)

        # Totals accounting
        total_sec = sum(s.duration_seconds for s in slices)
        with decimal.localcontext(_ENGINE_CONTEXT):
            total_hours = (Decimal(str(total_sec)) / _SECONDS_PER_HOUR).quantize(_6DP, rounding=ROUND_HALF_UP)

        active_sec = sum(s.duration_seconds for s in slices if s.is_active_laytime_window)
        with decimal.localcontext(_ENGINE_CONTEXT):
            active_hours = (Decimal(str(active_sec)) / _SECONDS_PER_HOUR).quantize(_6DP, rounding=ROUND_HALF_UP)

        pre_sec = sum(s.duration_seconds for s in slices if s.is_pre_commencement)
        with decimal.localcontext(_ENGINE_CONTEXT):
            pre_hours = (Decimal(str(pre_sec)) / _SECONDS_PER_HOUR).quantize(_6DP, rounding=ROUND_HALF_UP)

        post_sec = sum(s.duration_seconds for s in slices if s.is_post_completion)
        with decimal.localcontext(_ENGINE_CONTEXT):
            post_hours = (Decimal(str(post_sec)) / _SECONDS_PER_HOUR).quantize(_6DP, rounding=ROUND_HALF_UP)

        return DiscretizationResult(
            slices=tuple(slices),
            total_port_seconds=total_sec,
            total_port_hours=total_hours,
            active_laytime_seconds=active_sec,
            active_laytime_hours=active_hours,
            pre_commencement_seconds=pre_sec,
            pre_commencement_hours=pre_hours,
            post_completion_seconds=post_sec,
            post_completion_hours=post_hours,
            timeline_start=t_start,
            timeline_end=t_end,
        )

    @staticmethod
    def _apply_event_to_state(
        ev: SOFEvent,
        berthed: bool,
        discharging: bool,
        rain: bool,
        wind: bool,
        crane_down: bool,
        power_down: bool,
        labour: bool,
        congestion: bool,
        cranes_work: Optional[int],
        cranes_tot: Optional[int],
    ):
        """Update active operational conditions upon encountering an event."""
        # Berthing
        if ev.event_type in (SOFEventType.ALL_FAST, SOFEventType.FIRST_LINE_ASHORE):
            berthed = True
        elif ev.event_type in (SOFEventType.LINES_RELEASED, SOFEventType.VESSEL_UNMOORED, SOFEventType.VESSEL_DEPARTED):
            berthed = False

        # Discharging
        if ev.event_type in (SOFEventType.DISCHARGE_COMMENCED, SOFEventType.DISCHARGE_RESUMED):
            discharging = True
        elif ev.event_type in (SOFEventType.DISCHARGE_SUSPENDED, SOFEventType.DISCHARGE_COMPLETED):
            discharging = False

        # Weather - Rain
        if ev.event_type == SOFEventType.RAIN_COMMENCED:
            rain = True
        elif ev.event_type == SOFEventType.RAIN_CEASED:
            rain = False

        # Weather - Wind
        if ev.event_type == SOFEventType.WIND_FORCE_EXCEEDED_LIMIT:
            wind = True
        elif ev.event_type == SOFEventType.WIND_FORCE_WITHIN_LIMIT:
            wind = False

        # Crane Breakdown
        if ev.event_type == SOFEventType.CRANE_BREAKDOWN_COMMENCED:
            crane_down = True
        elif ev.event_type == SOFEventType.CRANE_BREAKDOWN_RESUMED:
            crane_down = False

        # Power Failure
        if ev.event_type == SOFEventType.POWER_FAILURE_COMMENCED:
            power_down = True
        elif ev.event_type == SOFEventType.POWER_FAILURE_RESUMED:
            power_down = False

        # Labour
        if ev.event_type == SOFEventType.LABOUR_DISPUTE_COMMENCED:
            labour = True
        elif ev.event_type == SOFEventType.LABOUR_DISPUTE_ENDED:
            labour = False

        # Berth Congestion
        if ev.event_type == SOFEventType.BERTH_CONGESTION_DELAY_START:
            congestion = True
        elif ev.event_type == SOFEventType.BERTH_CONGESTION_DELAY_END:
            congestion = False

        # Crane metrics
        if ev.cranes_working is not None:
            cranes_work = ev.cranes_working
        if ev.cranes_total is not None:
            cranes_tot = ev.cranes_total

        return (
            berthed,
            discharging,
            rain,
            wind,
            crane_down,
            power_down,
            labour,
            congestion,
            cranes_work,
            cranes_tot,
        )
