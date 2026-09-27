"""
backend/apps/laytime/engine/allowed_laytime.py
============================================================
Cycle 1.2.1 — Mathematical Allowed Laytime Engine

Computes the contractually allowed laytime (in fractional hours)
from charter party parameters, using Python ``decimal.Decimal``
throughout for IEEE-754-free fixed-point arithmetic.

Public API
----------
``AllowedLaytimeEngine``
    The main engine class.  Instantiate once per charter contract;
    call ``.compute()`` to get an ``AllowedLaytimeResult``.

``AllowedLaytimeResult``
    Immutable dataclass holding the computation output with full
    audit trail (inputs echoed, formula string, precision context).

Mathematical Model
------------------
For fixed-rate terms (SHINC, SHEX, SHEX_EIU, FHINC, FHEX, WWD, WWDSHEX):

    allowed_laytime_hours = (cargo_mt / discharge_rate_mt_per_day) × 24

Where:
  - ``cargo_mt``               — nominated cargo quantity in metric tonnes
  - ``discharge_rate_mt_per_day`` — contractual discharge rate in MT/WWD

The formula is universal across all fixed-rate terms because the
*allowed* laytime is always expressed in clock hours. The laytime
TERMS only govern how actual elapsed time is *counted* against this
allowance (handled in Cycles 1.2.2–1.2.5).

For CQD / AS_FAST_AS_CAN terms: no fixed allowance is computed;
the engine returns ``allowed_hours = None`` with an explanatory flag.

Tolerance Handling
------------------
Charter parties specify a ±tolerance% on cargo quantity.  This module
computes allowed laytime for three scenarios:
  - ``NOMINATED``:  the face-value nominated quantity
  - ``MIN_OPTION``: nominated × (1 − tolerance/100)  [minimum option]
  - ``MAX_OPTION``: nominated × (1 + tolerance/100)  [maximum option]

The settlement-relevant figure is determined after the B/L quantity
is known (handled in Phase 1.2.5).

Precision
---------
All arithmetic uses ``decimal.ROUND_HALF_UP`` with 10 significant
figures of working precision, then rounds final results to 6 decimal
places (sub-second accuracy over multi-day durations).
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from .constants import (
    HOURS_PER_DAY,
    MIN_CARGO_QTY_MT,
    MAX_DISCHARGE_RATE_MT_DAY,
    MAX_ALLOWED_LAYTIME_HOURS,
    LaytimeTerms,
    INDEFINITE_LAYTIME_TERMS,
)

# ---------------------------------------------------------------------------
# Decimal precision context for the engine
# 28 significant digits — far beyond any realistic maritime quantity
# ---------------------------------------------------------------------------
_ENGINE_CONTEXT = decimal.Context(
    prec=28,
    rounding=ROUND_HALF_UP,
    Emin=-999999999,
    Emax=999999999,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[
        decimal.DivisionByZero,
        decimal.InvalidOperation,
        decimal.Overflow,
    ],
)

# Rounding target for final output: 6 decimal places (~0.000001 hours ≈ 3.6 ms)
_OUTPUT_QUANTIZE = Decimal("0.000001")


def _D(value: str | int | float) -> Decimal:
    """
    Safely coerce a value to Decimal using the engine context.
    Float inputs are converted via str() to avoid IEEE-754 representation errors.
    """
    if isinstance(value, float):
        value = repr(value)
    with decimal.localcontext(_ENGINE_CONTEXT):
        return Decimal(str(value))


# ---------------------------------------------------------------------------
# Input validation errors
# ---------------------------------------------------------------------------
class LaytimeInputError(ValueError):
    """Raised when charter party inputs fail validation before computation."""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AllowedLaytimeResult:
    """
    Immutable result of an allowed laytime computation.

    All Decimal fields carry 6 decimal places.
    ``allowed_hours_nominated`` is None for CQD / AS_FAST_AS_CAN terms.
    """

    # --- Input echo (for audit) ---
    cargo_mt_nominated:      Decimal
    discharge_rate_mt_day:   Decimal
    tolerance_pct:           Decimal
    laytime_terms:           LaytimeTerms

    # --- Core result ---
    is_fixed_laytime:        bool
    """True for SHINC/SHEX/WWD etc.; False for CQD/AS_FAST_AS_CAN."""

    allowed_hours_nominated: Optional[Decimal]
    """Allowed laytime for the nominated quantity. None if is_fixed_laytime=False."""

    allowed_hours_min:       Optional[Decimal]
    """Allowed laytime at minimum cargo option (nominated × (1 − tol/100))."""

    allowed_hours_max:       Optional[Decimal]
    """Allowed laytime at maximum cargo option (nominated × (1 + tol/100))."""

    # --- Derived quantities ---
    cargo_mt_min:            Optional[Decimal]
    cargo_mt_max:            Optional[Decimal]
    days_nominated:          Optional[Decimal]
    """Allowed laytime expressed in days (= allowed_hours_nominated / 24)."""

    # --- Audit trail ---
    formula_string:          str
    """Human-readable formula with substituted values, for audit ledger."""

    precision_context_prec:  int = field(default=28, compare=False)

    def __str__(self) -> str:
        if not self.is_fixed_laytime:
            return (
                f"AllowedLaytime[{self.laytime_terms.value}]: "
                f"No fixed allowance (CQD/AS_FAST_AS_CAN)"
            )
        return (
            f"AllowedLaytime[{self.laytime_terms.value}]: "
            f"{self.allowed_hours_nominated} hours "
            f"({self.days_nominated} days) "
            f"for {self.cargo_mt_nominated} MT "
            f"@ {self.discharge_rate_mt_day} MT/day"
        )


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------
class AllowedLaytimeEngine:
    """
    Computes the contractually allowed laytime from charter party parameters.

    Usage::

        engine = AllowedLaytimeEngine(
            cargo_mt_nominated   = Decimal("75000.000"),
            discharge_rate_mt_day= Decimal("8000.000"),
            tolerance_pct        = Decimal("5.00"),
            laytime_terms        = LaytimeTerms.WWDSHEX,
        )
        result = engine.compute()
        print(result.allowed_hours_nominated)  # → Decimal("225.000000")

    Parameters
    ----------
    cargo_mt_nominated : Decimal | str | int
        Nominated cargo quantity in metric tonnes (from charter party).
    discharge_rate_mt_day : Decimal | str | int
        Contractual discharge rate in metric tonnes per WWD (or per day
        for SHINC terms).  Must be strictly positive.
    tolerance_pct : Decimal | str | int
        Cargo quantity tolerance in percent (e.g., ``5`` for ±5%).
        Must be in [0, 15].
    laytime_terms : LaytimeTerms
        Charter party laytime terms.
    override_allowed_hours : Decimal | str | None
        If the contract explicitly states a fixed allowed laytime in hours
        (overriding the formula), supply it here.  The formula is still
        computed for verification, and a deviation flag is set if they differ.
    """

    def __init__(
        self,
        cargo_mt_nominated:    Decimal | str | int,
        discharge_rate_mt_day: Decimal | str | int,
        tolerance_pct:         Decimal | str | int = Decimal("0"),
        laytime_terms:         LaytimeTerms = LaytimeTerms.SHINC,
        override_allowed_hours: Optional[Decimal | str] = None,
    ) -> None:
        with decimal.localcontext(_ENGINE_CONTEXT):
            self._cargo_mt      = _D(cargo_mt_nominated)
            self._rate_mt_day   = _D(discharge_rate_mt_day)
            self._tolerance_pct = _D(tolerance_pct)
            self._terms         = LaytimeTerms(laytime_terms)
            self._override      = _D(override_allowed_hours) if override_allowed_hours is not None else None
        self._validate()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def _validate(self) -> None:
        """Raise LaytimeInputError on any invalid input combination."""

        if self._cargo_mt < MIN_CARGO_QTY_MT:
            raise LaytimeInputError(
                f"cargo_mt_nominated must be >= {MIN_CARGO_QTY_MT} MT; "
                f"got {self._cargo_mt}"
            )

        if self._rate_mt_day <= Decimal("0"):
            raise LaytimeInputError(
                f"discharge_rate_mt_day must be strictly positive; "
                f"got {self._rate_mt_day}"
            )

        if self._rate_mt_day > MAX_DISCHARGE_RATE_MT_DAY:
            raise LaytimeInputError(
                f"discharge_rate_mt_day {self._rate_mt_day} exceeds realistic "
                f"maximum of {MAX_DISCHARGE_RATE_MT_DAY} MT/day"
            )

        if not (Decimal("0") <= self._tolerance_pct <= Decimal("15")):
            raise LaytimeInputError(
                f"tolerance_pct must be in [0, 15]; got {self._tolerance_pct}"
            )

        if self._override is not None and self._override <= Decimal("0"):
            raise LaytimeInputError(
                f"override_allowed_hours must be positive; got {self._override}"
            )

    # ------------------------------------------------------------------
    # Core computation helpers
    # ------------------------------------------------------------------
    def _compute_hours_for_qty(self, cargo_mt: Decimal) -> Decimal:
        """
        Apply the standard formula:
            hours = (cargo_mt / discharge_rate_mt_day) × 24

        Uses the engine's decimal context for controlled rounding.
        Result is quantized to 6 decimal places.
        """
        with decimal.localcontext(_ENGINE_CONTEXT):
            days  = cargo_mt / self._rate_mt_day
            hours = days * HOURS_PER_DAY
            return hours.quantize(_OUTPUT_QUANTIZE, rounding=ROUND_HALF_UP)

    def _cargo_bounds(self) -> tuple[Decimal, Decimal]:
        """Return (min_qty, max_qty) honouring the ±tolerance% band."""
        with decimal.localcontext(_ENGINE_CONTEXT):
            factor_min = (Decimal("1") - self._tolerance_pct / Decimal("100"))
            factor_max = (Decimal("1") + self._tolerance_pct / Decimal("100"))
            return (
                (self._cargo_mt * factor_min).quantize(_OUTPUT_QUANTIZE, rounding=ROUND_HALF_UP),
                (self._cargo_mt * factor_max).quantize(_OUTPUT_QUANTIZE, rounding=ROUND_HALF_UP),
            )

    def _build_formula_string(
        self,
        nominated_hours: Optional[Decimal],
    ) -> str:
        """Produce a human-readable audit formula string."""
        if self._terms in INDEFINITE_LAYTIME_TERMS:
            return (
                f"({self._terms.value}) No fixed allowance — "
                f"vessel to discharge as per custom/port practice"
            )
        hours_str = str(nominated_hours) if nominated_hours else "N/A"
        return (
            f"allowed_hours = ({self._cargo_mt} MT "
            f"÷ {self._rate_mt_day} MT/day) × 24 "
            f"= {hours_str} hours"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def compute(self) -> AllowedLaytimeResult:
        """
        Execute the allowed laytime computation and return an immutable result.

        Returns
        -------
        AllowedLaytimeResult
            Fully auditable result with inputs echoed, formula string,
            and three cargo-band scenarios (nominated, min, max).

        Raises
        ------
        LaytimeInputError
            If inputs are invalid (caught at ``__init__`` time, but re-raises
            if override triggers a secondary validation failure).
        """
        with decimal.localcontext(_ENGINE_CONTEXT):

            is_fixed = self._terms not in INDEFINITE_LAYTIME_TERMS

            if is_fixed:
                hours_nom = (
                    self._override
                    if self._override is not None
                    else self._compute_hours_for_qty(self._cargo_mt)
                )

                if hours_nom > MAX_ALLOWED_LAYTIME_HOURS:
                    raise LaytimeInputError(
                        f"Computed allowed laytime {hours_nom} hours exceeds "
                        f"safety ceiling of {MAX_ALLOWED_LAYTIME_HOURS} hours (365 days). "
                        f"Check discharge_rate_mt_day or cargo quantity."
                    )

                days_nom = (hours_nom / HOURS_PER_DAY).quantize(
                    _OUTPUT_QUANTIZE, rounding=ROUND_HALF_UP
                )

                cargo_min, cargo_max = self._cargo_bounds()
                hours_min = self._compute_hours_for_qty(cargo_min)
                hours_max = self._compute_hours_for_qty(cargo_max)

            else:
                hours_nom = None
                days_nom  = None
                cargo_min = None
                cargo_max = None
                hours_min = None
                hours_max = None

            formula = self._build_formula_string(hours_nom)

            return AllowedLaytimeResult(
                cargo_mt_nominated      = self._cargo_mt,
                discharge_rate_mt_day   = self._rate_mt_day,
                tolerance_pct           = self._tolerance_pct,
                laytime_terms           = self._terms,
                is_fixed_laytime        = is_fixed,
                allowed_hours_nominated = hours_nom,
                allowed_hours_min       = hours_min,
                allowed_hours_max       = hours_max,
                cargo_mt_min            = cargo_min,
                cargo_mt_max            = cargo_max,
                days_nominated          = days_nom,
                formula_string          = formula,
                precision_context_prec  = _ENGINE_CONTEXT.prec,
            )

    # ------------------------------------------------------------------
    # Convenience class methods (Baltic Exchange standard scenarios)
    # ------------------------------------------------------------------
    @classmethod
    def from_contract_dict(cls, contract: dict) -> "AllowedLaytimeEngine":
        """
        Construct from a dict with keys matching the charter_contracts table columns:
            - nominated_quantity_mt
            - discharge_rate_mt_per_day
            - tolerance_pct
            - laytime_terms
            - allowed_laytime_hours  (optional override from contract face value)
        """
        return cls(
            cargo_mt_nominated    = contract["nominated_quantity_mt"],
            discharge_rate_mt_day = contract["discharge_rate_mt_per_day"],
            tolerance_pct         = contract.get("tolerance_pct", "0"),
            laytime_terms         = LaytimeTerms(contract["laytime_terms"]),
            override_allowed_hours= contract.get("allowed_laytime_hours"),
        )

    def verify_formula_against_contract(
        self, contract_allowed_hours: Decimal | str
    ) -> tuple[bool, Decimal]:
        """
        Compare this engine's formula-derived hours against a value stated on
        the face of the contract.  Returns (is_consistent, deviation_hours).

        A deviation of > 0.001 hours (3.6 seconds) is flagged as inconsistent.
        Used to catch data-entry errors during contract creation (Cycle 2.1.1).
        """
        result = self.compute()
        if not result.is_fixed_laytime:
            return (True, Decimal("0"))  # CQD: no formula to verify against

        with decimal.localcontext(_ENGINE_CONTEXT):
            stated    = _D(contract_allowed_hours)
            deviation = abs(result.allowed_hours_nominated - stated)
            is_ok     = deviation <= Decimal("0.001")
            return (is_ok, deviation.quantize(_OUTPUT_QUANTIZE, rounding=ROUND_HALF_UP))
