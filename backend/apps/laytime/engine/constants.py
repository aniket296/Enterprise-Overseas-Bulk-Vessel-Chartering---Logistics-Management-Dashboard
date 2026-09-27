"""
backend/apps/laytime/engine/constants.py
============================================================
Immutable domain constants for the laytime calculation engine.

All numeric constants are expressed as Python ``Decimal`` strings
(never float literals) to prevent IEEE-754 representation drift.

References:
  - BIMCO Laytime Definitions 2013
  - Baltic Exchange standard clauses
  - Voylayrules 1993
"""

from decimal import Decimal
from enum import Enum

# ----------------------------------------------------------------
# Hours constants (Decimal, not float)
# ----------------------------------------------------------------
HOURS_PER_DAY:   Decimal = Decimal("24")
HOURS_PER_WEEK:  Decimal = Decimal("168")   # 7 × 24

# Standard SHEX window: Saturday 17:00 → Monday 08:00 in many ports
# (port-specific; these are Baltic Exchange defaults)
SHEX_SATURDAY_CUTOFF_HOUR: int = 17   # 17:00 Saturday — laytime stops counting
SHEX_MONDAY_RESUME_HOUR:   int = 8    # 08:00 Monday   — laytime resumes

# Standard NOR turn-time buffers (fractional hours)
NOR_TURNTIME_DEFAULT_HOURS:  Decimal = Decimal("6")    # 6h after NOR tendered
NOR_FREEPRATIQUE_HOURS:      Decimal = Decimal("1")    # Typical free pratique delay
NOR_OFFICE_HOURS_START:      int = 8                   # 08:00
NOR_OFFICE_HOURS_END:        int = 17                  # 17:00 (for WIPON/WIBON clauses)

# Minimum practical cargo quantity (prevents division by zero in rate calc)
MIN_CARGO_QTY_MT: Decimal = Decimal("0.001")

# Maximum realistic discharge rate (sanity guard: 100,000 MT/day)
MAX_DISCHARGE_RATE_MT_DAY: Decimal = Decimal("100000")

# Maximum realistic allowed laytime (sanity guard: 365 days = 8760 hours)
MAX_ALLOWED_LAYTIME_HOURS: Decimal = Decimal("8760")


# ----------------------------------------------------------------
# Laytime term enumeration
# Mirrors shipping.laytime_terms PostgreSQL ENUM from V001.
# ----------------------------------------------------------------
class LaytimeTerms(str, Enum):
    """
    Charter party laytime terms that govern which periods of time
    count against the allowed laytime allowance.

    Values match the PostgreSQL ENUM shipping.laytime_terms exactly.
    """
    SHINC       = "SHINC"       # Sundays and Holidays Included
    SHEX        = "SHEX"        # Sundays and Holidays Excluded (if used, counts)
    SHEX_EIU    = "SHEX_EIU"   # Sundays and Holidays Excluded Even If Used
    FHINC       = "FHINC"       # Fridays and Holidays Included (Middle East)
    FHEX        = "FHEX"        # Fridays and Holidays Excluded
    WWD         = "WWD"         # Weather Working Days
    WWDSHEX     = "WWDSHEX"    # WWD combined with SHEX
    CQD         = "CQD"         # Customary Quick Despatch (no fixed laytime)
    AS_FAST_AS_CAN = "AS_FAST_AS_CAN"  # No fixed numerical allowance


# Terms where no fixed numerical allowed-laytime is contractually specified
INDEFINITE_LAYTIME_TERMS: frozenset[LaytimeTerms] = frozenset({
    LaytimeTerms.CQD,
    LaytimeTerms.AS_FAST_AS_CAN,
})

# Terms that exclude Sundays and public holidays from counting
SUNDAY_HOLIDAY_EXCLUDING_TERMS: frozenset[LaytimeTerms] = frozenset({
    LaytimeTerms.SHEX,
    LaytimeTerms.SHEX_EIU,
    LaytimeTerms.WWDSHEX,
})

# Terms that exclude Friday (used in Gulf/Middle East ports)
FRIDAY_EXCLUDING_TERMS: frozenset[LaytimeTerms] = frozenset({
    LaytimeTerms.FHEX,
})

# Terms where weather interruptions are excluded from laytime count
WEATHER_WORKING_TERMS: frozenset[LaytimeTerms] = frozenset({
    LaytimeTerms.WWD,
    LaytimeTerms.WWDSHEX,
})

# "Even If Used" terms: rest days excluded even if vessel actually works
EIU_TERMS: frozenset[LaytimeTerms] = frozenset({
    LaytimeTerms.SHEX_EIU,
})
