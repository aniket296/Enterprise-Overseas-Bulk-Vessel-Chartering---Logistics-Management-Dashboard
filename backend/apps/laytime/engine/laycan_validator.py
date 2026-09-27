"""
backend/apps/laytime/engine/laycan_validator.py
============================================================
Cycle 1.2.1 — Notice of Readiness (NOR) & Laycan Window Validator

Validates NOR tender timestamps against contractual laycan windows
and computes the effective laytime commencement time, applying:

  1. Turn-time buffer (fixed hours after NOR tender)
  2. Office-hours clause (WIPON / WIBON / WIBON+OT)
  3. Whether the NOR is within the laycan window

NOR Acceptance Rules (Baltic Exchange standard)
-----------------------------------------------
When NOR is tendered:
  A. If within laycan → accepted; laytime commences after turn-time.
  B. If tendered BEFORE laycan start:
       NOR is held and treated as re-tendered at laycan_start.
       Laytime commences after turn-time from laycan_start.
  C. If tendered AFTER laycan end:
       Vessel is "off-hire" / owners may claim damages; this module
       flags the event as LATE but still computes commencement for
       operational purposes (legal resolution is out of scope).

Turn-Time Commencement (Cycle 1.2.2 responsibility)
---------------------------------------------------
This module computes the *earliest possible* laytime commencement
timestamp.  Actual commencement depends on WIPON/WIBON clauses
handled in Cycle 1.2.2.  Here we compute the turn-time endpoint.

All datetimes must be timezone-aware (UTC).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from .constants import (
    HOURS_PER_DAY,
    NOR_TURNTIME_DEFAULT_HOURS,
    NOR_OFFICE_HOURS_START,
    NOR_OFFICE_HOURS_END,
)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class NORStatus(str, Enum):
    """Result of NOR window validation against the laycan."""
    WITHIN_LAYCAN   = "WITHIN_LAYCAN"    # NOR tendered within [laycan_start, laycan_end]
    BEFORE_LAYCAN   = "BEFORE_LAYCAN"    # NOR tendered before laycan_start (held)
    AFTER_LAYCAN    = "AFTER_LAYCAN"     # NOR tendered after laycan_end (late)
    NOT_YET_TENDERED = "NOT_YET_TENDERED"  # NOR timestamp not yet provided


class OfficeHoursClause(str, Enum):
    """
    Clause governing how office hours affect laytime commencement after NOR.

    WIPON  — Whether In Port Or Not (laytime commences immediately on NOR)
    WIBON  — Whether In Berth Or Not (laytime commences on NOR even at anchor)
    WIFPON — Whether In Free Pratique Or Not
    WCCON  — Whether Customs Cleared Or Not
    NONE   — No special clause; laytime commences on berth + hatches ready
    """
    WIPON  = "WIPON"
    WIBON  = "WIBON"
    WIFPON = "WIFPON"
    WCCON  = "WCCON"
    NONE   = "NONE"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class NORValidationResult:
    """
    Immutable result of NOR laycan validation and turn-time computation.
    """
    # --- Inputs echoed ---
    nor_tendered_at:          Optional[datetime]
    laycan_start:             date
    laycan_end:               date
    turn_time_hours:          Decimal
    office_hours_clause:      OfficeHoursClause

    # --- Status ---
    nor_status:               NORStatus

    # --- Effective NOR (may differ from tendered for BEFORE_LAYCAN) ---
    effective_nor_at:         Optional[datetime]
    """The datetime from which the turn-time buffer is measured."""

    # --- Commencement window ---
    laytime_commences_at:     Optional[datetime]
    """
    The earliest datetime from which laytime starts counting.
    = effective_nor_at + turn_time_hours
    (Cycles 1.2.2+ refine this for SHEX/WWD/office-hours clauses.)
    """

    # --- Flags ---
    is_within_office_hours:   Optional[bool]
    """True if NOR was tendered during port office hours (08:00–17:00)."""

    is_weekend_nor:           Optional[bool]
    """True if NOR was tendered on Saturday or Sunday."""

    days_early:               Optional[int]
    """How many calendar days before laycan_start NOR was tendered (BEFORE_LAYCAN)."""

    days_late:                Optional[int]
    """How many calendar days after laycan_end NOR was tendered (AFTER_LAYCAN)."""

    # --- Audit ---
    validation_notes:         list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"NORValidation[{self.nor_status.value}]: "
            f"tendered={self.nor_tendered_at}, "
            f"effective={self.effective_nor_at}, "
            f"commences={self.laytime_commences_at}"
        )


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------
class LaycanValidator:
    """
    Validates NOR tender timestamps against contractual laycan windows
    and computes the effective laytime commencement time.

    Parameters
    ----------
    laycan_start : date
        Contractual laycan opening date (inclusive).
    laycan_end : date
        Contractual laycan closing date (inclusive).
    turn_time_hours : Decimal | str
        Contractual turn-time buffer after NOR tender before laytime
        commences.  Defaults to 6 hours (Baltic Exchange standard).
    office_hours_clause : OfficeHoursClause
        Special NOR clause affecting commencement timing.
    nor_tendered_at : datetime | None
        Actual NOR tender timestamp (UTC, timezone-aware).
        Pass ``None`` to query laycan status without a specific NOR.
    """

    def __init__(
        self,
        laycan_start:        date,
        laycan_end:          date,
        turn_time_hours:     Decimal | str = NOR_TURNTIME_DEFAULT_HOURS,
        office_hours_clause: OfficeHoursClause = OfficeHoursClause.NONE,
        nor_tendered_at:     Optional[datetime] = None,
    ) -> None:
        if laycan_start > laycan_end:
            raise ValueError(
                f"laycan_start ({laycan_start}) must be <= laycan_end ({laycan_end})"
            )
        if nor_tendered_at is not None and nor_tendered_at.tzinfo is None:
            raise ValueError(
                "nor_tendered_at must be timezone-aware (UTC). "
                "Use datetime.now(tz=timezone.utc) or attach tzinfo."
            )

        self._laycan_start   = laycan_start
        self._laycan_end     = laycan_end
        self._turn_time_h    = Decimal(str(turn_time_hours))
        self._clause         = OfficeHoursClause(office_hours_clause)
        self._nor_at         = nor_tendered_at

        if self._turn_time_h < Decimal("0"):
            raise ValueError(f"turn_time_hours must be non-negative; got {self._turn_time_h}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _is_office_hours(dt: datetime) -> bool:
        """True if datetime falls within port office hours (08:00–17:00 local)."""
        return NOR_OFFICE_HOURS_START <= dt.hour < NOR_OFFICE_HOURS_END

    @staticmethod
    def _is_weekend(dt: datetime) -> bool:
        """True if datetime is on Saturday (5) or Sunday (6)."""
        return dt.weekday() >= 5

    def _apply_turn_time(self, base: datetime) -> datetime:
        """Add turn_time_hours (fractional) to a base datetime."""
        total_seconds = float(self._turn_time_h) * 3600
        return base + timedelta(seconds=total_seconds)

    def _classify_nor(self, nor_date: date) -> NORStatus:
        """Classify NOR date relative to laycan window."""
        if nor_date < self._laycan_start:
            return NORStatus.BEFORE_LAYCAN
        if nor_date > self._laycan_end:
            return NORStatus.AFTER_LAYCAN
        return NORStatus.WITHIN_LAYCAN

    def _laycan_start_utc_midnight(self) -> datetime:
        """Return laycan_start as midnight UTC datetime."""
        return datetime(
            self._laycan_start.year,
            self._laycan_start.month,
            self._laycan_start.day,
            0, 0, 0,
            tzinfo=timezone.utc,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def validate(self) -> NORValidationResult:
        """
        Execute the NOR laycan validation.

        Returns
        -------
        NORValidationResult
            Fully auditable result with NOR status, effective NOR time,
            laytime commencement time, and all flag fields.
        """
        notes: list[str] = []

        # ---- Case 1: NOR not yet tendered ----
        if self._nor_at is None:
            return NORValidationResult(
                nor_tendered_at       = None,
                laycan_start          = self._laycan_start,
                laycan_end            = self._laycan_end,
                turn_time_hours       = self._turn_time_h,
                office_hours_clause   = self._clause,
                nor_status            = NORStatus.NOT_YET_TENDERED,
                effective_nor_at      = None,
                laytime_commences_at  = None,
                is_within_office_hours= None,
                is_weekend_nor        = None,
                days_early            = None,
                days_late             = None,
                validation_notes      = ["NOR not yet tendered."],
            )

        nor_date   = self._nor_at.date()
        nor_status = self._classify_nor(nor_date)

        in_office  = self._is_office_hours(self._nor_at)
        is_weekend = self._is_weekend(self._nor_at)
        days_early: Optional[int] = None
        days_late:  Optional[int] = None

        # ---- Determine effective NOR ----
        if nor_status == NORStatus.WITHIN_LAYCAN:
            effective_nor = self._nor_at
            notes.append(
                f"NOR tendered {self._nor_at.isoformat()} is within laycan "
                f"[{self._laycan_start} – {self._laycan_end}]. Accepted."
            )

        elif nor_status == NORStatus.BEFORE_LAYCAN:
            days_early = (self._laycan_start - nor_date).days
            # NOR is held; effective NOR is midnight on laycan_start
            effective_nor = self._laycan_start_utc_midnight()
            notes.append(
                f"NOR tendered {days_early} day(s) BEFORE laycan_start "
                f"({self._laycan_start}). NOR held — effective NOR = "
                f"{effective_nor.isoformat()} (midnight UTC on laycan_start)."
            )

        else:  # AFTER_LAYCAN
            days_late   = (nor_date - self._laycan_end).days
            effective_nor = self._nor_at  # Use actual tender time; owner's claim arises
            notes.append(
                f"NOR tendered {days_late} day(s) AFTER laycan_end "
                f"({self._laycan_end}). Vessel is LATE. "
                f"Charterer may have cancellation rights. "
                f"Laytime commencement computed operationally from actual NOR."
            )

        # ---- Compute laytime commencement ----
        # Base: effective_nor + turn_time
        laytime_commences = self._apply_turn_time(effective_nor)

        # Office-hours clause adjustments (WIPON overrides — no delay)
        if self._clause == OfficeHoursClause.WIPON:
            # WIPON: laytime commences immediately on NOR — no turn-time
            laytime_commences = effective_nor
            notes.append(
                "WIPON clause: laytime commences immediately on NOR tender "
                "(turn-time waived)."
            )
        elif self._clause == OfficeHoursClause.WIBON:
            # WIBON: same effect as WIPON for anchored vessel
            notes.append(
                "WIBON clause: laytime commences on NOR tender even if "
                "vessel is at anchorage (not yet berthed)."
            )
        elif self._clause in (OfficeHoursClause.WIFPON, OfficeHoursClause.WCCON):
            notes.append(
                f"{self._clause.value} clause: laytime conditional on "
                f"free pratique / customs clearance. "
                f"Turn-time applied from NOR; Cycle 1.2.2 will refine."
            )

        # Weekend NOR note (relevant for SHEX contracts — handled in 1.2.2+)
        if is_weekend:
            notes.append(
                "NOR tendered on weekend. Under SHEX terms, turn-time "
                "may not run until next working day (Cycle 1.2.2)."
            )

        # Out-of-office-hours note
        if not in_office:
            notes.append(
                f"NOR tendered outside office hours "
                f"({NOR_OFFICE_HOURS_START}:00–{NOR_OFFICE_HOURS_END}:00). "
                f"Under some contracts, turn-time starts at next opening of business."
            )

        return NORValidationResult(
            nor_tendered_at       = self._nor_at,
            laycan_start          = self._laycan_start,
            laycan_end            = self._laycan_end,
            turn_time_hours       = self._turn_time_h,
            office_hours_clause   = self._clause,
            nor_status            = nor_status,
            effective_nor_at      = effective_nor,
            laytime_commences_at  = laytime_commences,
            is_within_office_hours= in_office,
            is_weekend_nor        = is_weekend,
            days_early            = days_early,
            days_late             = days_late,
            validation_notes      = notes,
        )

    # ------------------------------------------------------------------
    # Utility: laycan window duration
    # ------------------------------------------------------------------
    @property
    def laycan_duration_days(self) -> int:
        """Number of calendar days in the laycan window (inclusive)."""
        return (self._laycan_end - self._laycan_start).days + 1

    @property
    def laycan_midpoint(self) -> date:
        """Midpoint date of the laycan window (useful for scheduling)."""
        mid_delta = (self._laycan_end - self._laycan_start).days // 2
        return self._laycan_start + timedelta(days=mid_delta)
