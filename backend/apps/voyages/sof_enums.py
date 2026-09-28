"""
backend/apps/voyages/sof_enums.py
============================================================
Statement of Facts (SOF) Event Types and Laytime Counting Statuses.
Strictly maps to PostgreSQL 'shipping.sof_event_type' and
'shipping.interval_count_status' (V001 DDL).
"""

from typing import List, Set
from django.db import models


class SOFEventType(models.TextChoices):
    """
    30 standard maritime Statement of Facts port event types.
    """
    # 1. Arrival / Notice sequence
    VESSEL_ARRIVED_PILOT_STATION = "VESSEL_ARRIVED_PILOT_STATION", "Vessel Arrived Pilot Station"
    VESSEL_ARRIVED_ANCHORAGE = "VESSEL_ARRIVED_ANCHORAGE", "Vessel Arrived Anchorage"
    NOR_TENDERED = "NOR_TENDERED", "Notice of Readiness Tendered"
    NOR_ACCEPTED = "NOR_ACCEPTED", "Notice of Readiness Accepted"
    NOR_REJECTED = "NOR_REJECTED", "Notice of Readiness Rejected"
    VESSEL_SHIFTED_ANCHORAGE = "VESSEL_SHIFTED_ANCHORAGE", "Vessel Shifted Anchorage"

    # 2. Berthing sequence
    PILOT_BOARDED = "PILOT_BOARDED", "Pilot Boarded"
    VESSEL_COMMENCED_BERTHING = "VESSEL_COMMENCED_BERTHING", "Vessel Commenced Berthing"
    FIRST_LINE_ASHORE = "FIRST_LINE_ASHORE", "First Line Ashore"
    ALL_FAST = "ALL_FAST", "All Fast"
    GANGWAY_DOWN = "GANGWAY_DOWN", "Gangway Down"

    # 3. Pre-discharge operations
    CUSTOMS_BOARDING = "CUSTOMS_BOARDING", "Customs Boarding"
    CUSTOMS_CLEARED = "CUSTOMS_CLEARED", "Customs Cleared"
    DRAFT_SURVEY_COMMENCED = "DRAFT_SURVEY_COMMENCED", "Draft Survey Commenced"
    DRAFT_SURVEY_COMPLETED = "DRAFT_SURVEY_COMPLETED", "Draft Survey Completed"
    HATCHES_OPENED = "HATCHES_OPENED", "Hatches Opened"

    # 4. Discharge operations
    DISCHARGE_COMMENCED = "DISCHARGE_COMMENCED", "Discharge Commenced"
    DISCHARGE_SUSPENDED = "DISCHARGE_SUSPENDED", "Discharge Suspended"
    DISCHARGE_RESUMED = "DISCHARGE_RESUMED", "Discharge Resumed"
    DISCHARGE_COMPLETED = "DISCHARGE_COMPLETED", "Discharge Completed"

    # 5. Interruption events
    RAIN_COMMENCED = "RAIN_COMMENCED", "Rain Commenced"
    RAIN_CEASED = "RAIN_CEASED", "Rain Ceased"
    WIND_FORCE_EXCEEDED_LIMIT = "WIND_FORCE_EXCEEDED_LIMIT", "Wind Force Exceeded Limit"
    WIND_FORCE_WITHIN_LIMIT = "WIND_FORCE_WITHIN_LIMIT", "Wind Force Within Limit"
    CRANE_BREAKDOWN_COMMENCED = "CRANE_BREAKDOWN_COMMENCED", "Crane Breakdown Commenced"
    CRANE_BREAKDOWN_RESUMED = "CRANE_BREAKDOWN_RESUMED", "Crane Breakdown Resumed"
    POWER_FAILURE_COMMENCED = "POWER_FAILURE_COMMENCED", "Power Failure Commenced"
    POWER_FAILURE_RESUMED = "POWER_FAILURE_RESUMED", "Power Failure Resumed"
    LABOUR_DISPUTE_COMMENCED = "LABOUR_DISPUTE_COMMENCED", "Labour Dispute Commenced"
    LABOUR_DISPUTE_ENDED = "LABOUR_DISPUTE_ENDED", "Labour Dispute Ended"
    BERTH_CONGESTION_DELAY_START = "BERTH_CONGESTION_DELAY_START", "Berth Congestion Delay Start"
    BERTH_CONGESTION_DELAY_END = "BERTH_CONGESTION_DELAY_END", "Berth Congestion Delay End"

    # 6. Departure sequence
    HATCHES_CLOSED = "HATCHES_CLOSED", "Hatches Closed"
    DRAFT_SURVEY_FINAL = "DRAFT_SURVEY_FINAL", "Draft Survey Final"
    LINES_RELEASED = "LINES_RELEASED", "Lines Released"
    VESSEL_UNMOORED = "VESSEL_UNMOORED", "Vessel Unmoored"
    PILOT_DISEMBARKED = "PILOT_DISEMBARKED", "Pilot Disembarked"
    VESSEL_DEPARTED = "VESSEL_DEPARTED", "Vessel Departed"

    @classmethod
    def all_types(cls) -> List[str]:
        return [choice[0] for choice in cls.choices]


class IntervalCountStatus(models.TextChoices):
    """
    Laytime interval counting classification.
    """
    FULLY_COUNTED = "FULLY_COUNTED", "Fully Counted (100%)"
    PARTIALLY_COUNTED = "PARTIALLY_COUNTED", "Partially Counted"
    EXCLUDED = "EXCLUDED", "Excluded"
    DISPUTED = "DISPUTED", "Disputed"


# Interruption Event Types Grouping
INTERRUPTION_START_EVENTS: Set[str] = {
    SOFEventType.RAIN_COMMENCED.value,
    SOFEventType.WIND_FORCE_EXCEEDED_LIMIT.value,
    SOFEventType.CRANE_BREAKDOWN_COMMENCED.value,
    SOFEventType.POWER_FAILURE_COMMENCED.value,
    SOFEventType.LABOUR_DISPUTE_COMMENCED.value,
    SOFEventType.BERTH_CONGESTION_DELAY_START.value,
    SOFEventType.DISCHARGE_SUSPENDED.value,
}

INTERRUPTION_END_EVENTS: Set[str] = {
    SOFEventType.RAIN_CEASED.value,
    SOFEventType.WIND_FORCE_WITHIN_LIMIT.value,
    SOFEventType.CRANE_BREAKDOWN_RESUMED.value,
    SOFEventType.POWER_FAILURE_RESUMED.value,
    SOFEventType.LABOUR_DISPUTE_ENDED.value,
    SOFEventType.BERTH_CONGESTION_DELAY_END.value,
    SOFEventType.DISCHARGE_RESUMED.value,
}

ALL_INTERRUPTION_EVENTS: Set[str] = INTERRUPTION_START_EVENTS | INTERRUPTION_END_EVENTS
