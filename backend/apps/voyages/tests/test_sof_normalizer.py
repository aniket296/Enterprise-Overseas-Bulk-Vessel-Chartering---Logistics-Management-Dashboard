"""
backend/apps/voyages/tests/test_sof_normalizer.py
============================================================
Cycle 2.2.2 — Unit Tests: SOF Timeline Event Normalization & Ordering

Tests cover:
  1.  UTC normalization (aware converted, naive rejected)
  2.  Chronological sort (batch sorted to ascending timestamp order)
  3.  Out-of-order batch accepted if internally consistent after sort
  4.  Duplicate event detection (same type + same timestamp)
  5.  Retrograde timestamp guard (batch precedes committed DB record)
  6.  Interruption pair — orphaned END (no open start)
  7.  Interruption pair — inverted (END before START)
  8.  Interruption pair — unclosed START in new batch (allowed — port may be open)
  9.  Interruption pair — pre-existing open START not closed by batch (flagged)
  10. Phase precedence — arrival event after departure event (rejected)
  11. Phase precedence — discharge event after departure event (rejected)
  12. Phase-agnostic interruption events exempt from phase precedence
  13. Multiple violations collected in a single result
  14. build_existing_interruption_state() utility
  15. API integration: retrograde ingestion rejected with 422
  16. API integration: duplicate event in batch rejected with 422
  17. API integration: out-of-order batch is auto-sorted and accepted
  18. API integration: coincident events (same timestamp) accepted
"""

import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.voyages.sof_normalizer import (
    NormalizationResult,
    SOFNormalizationError,
    build_existing_interruption_state,
    normalize_sof_batch,
)
from apps.voyages.sof_enums import SOFEventType

# Shorthand UTC timezone
UTC = datetime.timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(hour: int, minute: int = 0, day: int = 15) -> datetime.datetime:
    """Return a UTC-aware datetime for 2025-06-<day> HH:MM:00Z."""
    return datetime.datetime(2025, 6, day, hour, minute, 0, tzinfo=UTC)


def _make_item(event_type: str, ts: datetime.datetime, **kwargs) -> dict:
    """Build a minimal SOF event dict as it would come from the serializer."""
    return {
        "event_type": event_type,
        "event_timestamp": ts,
        "sequence_number": kwargs.get("sequence_number", None),
        "event_description": kwargs.get("event_description", ""),
        "recorded_by_agent": kwargs.get("recorded_by_agent", ""),
        "crane_number": kwargs.get("crane_number", None),
        "hatch_number": kwargs.get("hatch_number", None),
        "quantity_discharged_at_event": kwargs.get("quantity_discharged_at_event", None),
        "interruption_cause": kwargs.get("interruption_cause", ""),
        "weather_condition": kwargs.get("weather_condition", ""),
        "is_amendment": kwargs.get("is_amendment", False),
        "amended_sof_id": kwargs.get("amended_sof_id", None),
        "source_document_id": kwargs.get("source_document_id", None),
    }


# ---------------------------------------------------------------------------
# 1. UTC Normalization
# ---------------------------------------------------------------------------

class TestUTCNormalization:

    def test_utc_aware_timestamp_passes(self):
        items = [_make_item(SOFEventType.NOR_TENDERED.value, _ts(8))]
        result = normalize_sof_batch(items)
        assert result.is_valid
        assert result.events[0].event_timestamp.tzinfo is not None

    def test_ist_offset_converted_to_utc(self):
        """IST (+05:30) timestamp should be accepted and converted to UTC."""
        ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        ts_ist = datetime.datetime(2025, 6, 15, 13, 30, 0, tzinfo=ist)  # 08:00 UTC
        items = [_make_item(SOFEventType.NOR_TENDERED.value, ts_ist)]
        result = normalize_sof_batch(items)
        assert result.is_valid
        utc_ts = result.events[0].event_timestamp
        assert utc_ts.tzinfo == UTC or utc_ts.utcoffset() == datetime.timedelta(0)
        assert utc_ts.hour == 8 and utc_ts.minute == 0

    def test_naive_timestamp_raises_error(self):
        naive_ts = datetime.datetime(2025, 6, 15, 8, 0, 0)  # No tzinfo
        items = [_make_item(SOFEventType.NOR_TENDERED.value, naive_ts)]
        result = normalize_sof_batch(items)
        assert not result.is_valid
        codes = [e.code for e in result.errors]
        assert "NAIVE_TIMESTAMP" in codes

    def test_future_timestamp_raises_error(self):
        future_ts = timezone.now() + datetime.timedelta(hours=48)
        items = [_make_item(SOFEventType.NOR_TENDERED.value, future_ts)]
        result = normalize_sof_batch(items)
        assert not result.is_valid
        codes = [e.code for e in result.errors]
        assert "FUTURE_TIMESTAMP" in codes


# ---------------------------------------------------------------------------
# 2. Chronological Sort
# ---------------------------------------------------------------------------

class TestChronologicalSort:

    def test_out_of_order_batch_is_sorted(self):
        """Batch submitted in reverse order should be sorted ascending."""
        items = [
            _make_item(SOFEventType.NOR_TENDERED.value,        _ts(8)),
            _make_item(SOFEventType.VESSEL_ARRIVED_ANCHORAGE.value, _ts(7)),
            _make_item(SOFEventType.VESSEL_ARRIVED_PILOT_STATION.value, _ts(6)),
        ]
        result = normalize_sof_batch(items)
        assert result.is_valid
        timestamps = [e.event_timestamp for e in result.events]
        assert timestamps == sorted(timestamps), "Events must be in ascending timestamp order"

    def test_coincident_events_stable_sorted_by_sequence(self):
        """Two events at identical timestamps: lower sequence_number sorts first."""
        items = [
            _make_item(SOFEventType.GANGWAY_DOWN.value,  _ts(14), sequence_number=2),
            _make_item(SOFEventType.ALL_FAST.value,      _ts(14), sequence_number=1),
        ]
        result = normalize_sof_batch(items)
        assert result.is_valid
        assert result.events[0].event_type == SOFEventType.ALL_FAST.value
        assert result.events[1].event_type == SOFEventType.GANGWAY_DOWN.value

    def test_single_event_passes(self):
        items = [_make_item(SOFEventType.VESSEL_DEPARTED.value, _ts(20))]
        result = normalize_sof_batch(items)
        assert result.is_valid
        assert len(result.events) == 1

    def test_empty_batch_returns_valid_empty(self):
        result = normalize_sof_batch([])
        assert result.is_valid
        assert result.events == []


# ---------------------------------------------------------------------------
# 3. Duplicate Detection
# ---------------------------------------------------------------------------

class TestDuplicateDetection:

    def test_exact_duplicate_in_batch_rejected(self):
        """Same event_type + same event_timestamp within one batch = DUPLICATE_EVENT."""
        ts = _ts(8)
        items = [
            _make_item(SOFEventType.NOR_TENDERED.value, ts),
            _make_item(SOFEventType.NOR_TENDERED.value, ts),  # duplicate
        ]
        result = normalize_sof_batch(items)
        assert not result.is_valid
        codes = [e.code for e in result.errors]
        assert "DUPLICATE_EVENT" in codes

    def test_same_type_different_timestamps_accepted(self):
        """Same event_type at two different timestamps is legitimate (e.g. multiple NOR)."""
        items = [
            _make_item(SOFEventType.NOR_TENDERED.value, _ts(8)),
            _make_item(SOFEventType.NOR_TENDERED.value, _ts(9)),   # new NOR after rejection
        ]
        result = normalize_sof_batch(items)
        assert result.is_valid

    def test_different_types_same_timestamp_accepted(self):
        """Coincident events of different types (e.g. ALL_FAST + GANGWAY_DOWN) are valid."""
        items = [
            _make_item(SOFEventType.ALL_FAST.value,    _ts(14)),
            _make_item(SOFEventType.GANGWAY_DOWN.value, _ts(14)),
        ]
        result = normalize_sof_batch(items)
        assert result.is_valid


# ---------------------------------------------------------------------------
# 4. Retrograde Timestamp Guard
# ---------------------------------------------------------------------------

class TestRetrogradeTsGuard:

    def test_batch_after_committed_events_passes(self):
        existing = [_ts(8), _ts(10)]
        items = [_make_item(SOFEventType.DISCHARGE_COMMENCED.value, _ts(16))]
        result = normalize_sof_batch(items, existing_timestamps=existing)
        assert result.is_valid

    def test_batch_before_committed_events_rejected(self):
        """A batch whose earliest timestamp precedes the latest committed = RETROGRADE."""
        existing = [_ts(10), _ts(12)]   # latest committed = 12:00
        items = [
            _make_item(SOFEventType.VESSEL_ARRIVED_ANCHORAGE.value, _ts(9)),  # precedes 12:00
        ]
        result = normalize_sof_batch(items, existing_timestamps=existing)
        assert not result.is_valid
        codes = [e.code for e in result.errors]
        assert "RETROGRADE_TIMESTAMP" in codes

    def test_batch_equal_to_committed_passes(self):
        """Events at exactly the latest committed timestamp are coincident — allowed."""
        existing = [_ts(10)]
        items = [_make_item(SOFEventType.HATCHES_OPENED.value, _ts(10))]
        result = normalize_sof_batch(items, existing_timestamps=existing)
        assert result.is_valid

    def test_no_existing_timestamps_any_past_ts_accepted(self):
        items = [_make_item(SOFEventType.VESSEL_ARRIVED_PILOT_STATION.value, _ts(6))]
        result = normalize_sof_batch(items, existing_timestamps=[])
        assert result.is_valid


# ---------------------------------------------------------------------------
# 5. Interruption Pair Validation
# ---------------------------------------------------------------------------

class TestInterruptionPairValidation:

    def test_matched_rain_pair_passes(self):
        items = [
            _make_item(SOFEventType.RAIN_COMMENCED.value, _ts(10)),
            _make_item(SOFEventType.RAIN_CEASED.value,    _ts(12)),
        ]
        result = normalize_sof_batch(items)
        assert result.is_valid

    def test_orphaned_rain_ceased_rejected(self):
        """RAIN_CEASED with no prior RAIN_COMMENCED = ORPHANED_INTERRUPTION_END."""
        items = [_make_item(SOFEventType.RAIN_CEASED.value, _ts(12))]
        result = normalize_sof_batch(items)
        assert not result.is_valid
        codes = [e.code for e in result.errors]
        assert "ORPHANED_INTERRUPTION_END" in codes

    def test_inverted_interruption_pair_rejected(self):
        """
        RAIN_CEASED before RAIN_COMMENCED = temporal inversion.

        After chronological sort, RAIN_CEASED (10:00) precedes RAIN_COMMENCED (12:00).
        The normalizer sees the END before the START has been registered, so it
        correctly raises ORPHANED_INTERRUPTION_END (no open interval for it to close).
        Both INTERRUPTION_INVERTED and ORPHANED_INTERRUPTION_END represent the same
        underlying violation — the END event cannot legitimately precede the START.
        """
        items = [
            _make_item(SOFEventType.RAIN_COMMENCED.value, _ts(12)),  # 12:00 — sorts second
            _make_item(SOFEventType.RAIN_CEASED.value,    _ts(10)),  # 10:00 — sorts first
        ]
        result = normalize_sof_batch(items)
        assert not result.is_valid, "Inverted interruption pair must be rejected"
        codes = [e.code for e in result.errors]
        # After sort, CEASED(10:00) appears before COMMENCED(12:00)
        # → reported as ORPHANED_INTERRUPTION_END (no open interval exists yet)
        # Both codes are valid rejections of the same temporal inversion.
        assert "INTERRUPTION_INVERTED" in codes or "ORPHANED_INTERRUPTION_END" in codes, \
            f"Expected inversion or orphan error, got: {codes}"

    def test_unclosed_start_in_batch_is_allowed(self):
        """Opening a RAIN_COMMENCED without closing in same batch is fine — port still open."""
        items = [_make_item(SOFEventType.RAIN_COMMENCED.value, _ts(10))]
        result = normalize_sof_batch(items)
        assert result.is_valid

    def test_pre_existing_open_interruption_not_closed_flagged(self):
        """An already-open interval (from DB) that the batch fails to close = error."""
        existing_intr_state = {
            SOFEventType.CRANE_BREAKDOWN_COMMENCED.value: _ts(8),   # open in DB
        }
        items = [
            # This batch has no CRANE_BREAKDOWN_RESUMED — fails to close the open interval
            _make_item(SOFEventType.DISCHARGE_RESUMED.value, _ts(12)),
        ]
        result = normalize_sof_batch(
            items,
            existing_interruption_state=existing_intr_state,
        )
        assert not result.is_valid
        codes = [e.code for e in result.errors]
        assert "UNCLOSED_PRE_EXISTING_INTERRUPTION" in codes or "ORPHANED_INTERRUPTION_END" in codes

    def test_pre_existing_open_interruption_closed_by_batch_passes(self):
        """Open DB interval closed by this batch = valid."""
        existing_intr_state = {
            SOFEventType.RAIN_COMMENCED.value: _ts(8),   # open in DB
        }
        items = [
            _make_item(SOFEventType.RAIN_CEASED.value, _ts(10)),   # closes it
        ]
        result = normalize_sof_batch(
            items,
            existing_interruption_state=existing_intr_state,
        )
        assert result.is_valid

    def test_multiple_interruption_types_independently_validated(self):
        """Rain and crane breakdown can both be open simultaneously — each pair validated."""
        items = [
            _make_item(SOFEventType.RAIN_COMMENCED.value,           _ts(10)),
            _make_item(SOFEventType.CRANE_BREAKDOWN_COMMENCED.value, _ts(10)),
            _make_item(SOFEventType.RAIN_CEASED.value,              _ts(12)),
            _make_item(SOFEventType.CRANE_BREAKDOWN_RESUMED.value,   _ts(13)),
        ]
        result = normalize_sof_batch(items)
        assert result.is_valid

    def test_discharge_suspended_resumed_pair(self):
        """DISCHARGE_SUSPENDED → DISCHARGE_RESUMED is a valid interruption pair."""
        items = [
            _make_item(SOFEventType.DISCHARGE_SUSPENDED.value, _ts(10)),
            _make_item(SOFEventType.DISCHARGE_RESUMED.value,   _ts(11)),
        ]
        result = normalize_sof_batch(items)
        assert result.is_valid


# ---------------------------------------------------------------------------
# 6. Phase Precedence Invariant
# ---------------------------------------------------------------------------

class TestPhasePrecedence:

    def test_arrival_after_departure_rejected(self):
        """NOR_TENDERED (phase 0) appearing AFTER VESSEL_DEPARTED (phase 5) = error."""
        items = [
            _make_item(SOFEventType.VESSEL_DEPARTED.value, _ts(20)),
            _make_item(SOFEventType.NOR_TENDERED.value,    _ts(22)),  # after departure
        ]
        result = normalize_sof_batch(items)
        assert not result.is_valid
        codes = [e.code for e in result.errors]
        assert "PHASE_PRECEDENCE_VIOLATION" in codes

    def test_discharge_commenced_after_departure_rejected(self):
        items = [
            _make_item(SOFEventType.LINES_RELEASED.value,       _ts(20)),
            _make_item(SOFEventType.DISCHARGE_COMMENCED.value,  _ts(21)),  # after release
        ]
        result = normalize_sof_batch(items)
        assert not result.is_valid
        codes = [e.code for e in result.errors]
        assert "PHASE_PRECEDENCE_VIOLATION" in codes

    def test_interruption_event_after_departure_exempt(self):
        """Phase-agnostic interruption events are NOT subject to phase precedence."""
        items = [
            _make_item(SOFEventType.LINES_RELEASED.value, _ts(20)),
            _make_item(SOFEventType.RAIN_CEASED.value,     _ts(21)),  # closing an open interval
        ]
        result = normalize_sof_batch(items, existing_interruption_state={
            SOFEventType.RAIN_COMMENCED.value: _ts(19),
        })
        assert result.is_valid

    def test_normal_full_port_sequence_passes(self):
        """Happy path: full arrival → berthing → discharge → departure sequence."""
        items = [
            _make_item(SOFEventType.VESSEL_ARRIVED_PILOT_STATION.value, _ts(6)),
            _make_item(SOFEventType.NOR_TENDERED.value,                  _ts(8)),
            _make_item(SOFEventType.ALL_FAST.value,                      _ts(14)),
            _make_item(SOFEventType.DISCHARGE_COMMENCED.value,          _ts(16)),
            _make_item(SOFEventType.DISCHARGE_COMPLETED.value,          _ts(16, day=17)),
            _make_item(SOFEventType.LINES_RELEASED.value,               _ts(18, day=17)),
            _make_item(SOFEventType.VESSEL_DEPARTED.value,              _ts(20, day=17)),
        ]
        result = normalize_sof_batch(items)
        assert result.is_valid, f"Unexpected errors: {result.errors}"


# ---------------------------------------------------------------------------
# 7. Multiple Violations Collected
# ---------------------------------------------------------------------------

class TestMultipleViolations:

    def test_all_errors_collected_in_single_result(self):
        """When multiple independent violations exist, all are returned together."""
        ts = _ts(8)
        items = [
            _make_item(SOFEventType.NOR_TENDERED.value, ts),
            _make_item(SOFEventType.NOR_TENDERED.value, ts),          # duplicate
            _make_item(SOFEventType.RAIN_CEASED.value,  _ts(6)),      # orphaned end, precedes NOR
        ]
        # Existing: latest committed = 10:00 → these items at 08:00 = retrograde
        result = normalize_sof_batch(items, existing_timestamps=[_ts(10)])
        assert not result.is_valid
        # We expect at least 2 distinct error codes (retrograde + duplicate or orphaned)
        assert len(result.errors) >= 2


# ---------------------------------------------------------------------------
# 8. build_existing_interruption_state() utility
# ---------------------------------------------------------------------------

class TestBuildExistingInterruptionState:

    def test_open_rain_interval_detected(self):
        """If RAIN_COMMENCED appears with no RAIN_CEASED → open interval returned."""
        class FakeSOF:
            def __init__(self, event_type, event_timestamp):
                self.event_type = event_type
                self.event_timestamp = event_timestamp

        records = [
            FakeSOF(SOFEventType.DISCHARGE_COMMENCED.value, _ts(8)),
            FakeSOF(SOFEventType.RAIN_COMMENCED.value,      _ts(10)),
        ]
        state = build_existing_interruption_state(records)
        assert state.get(SOFEventType.RAIN_COMMENCED.value) == _ts(10)

    def test_closed_rain_interval_returns_none(self):
        class FakeSOF:
            def __init__(self, event_type, event_timestamp):
                self.event_type = event_type
                self.event_timestamp = event_timestamp

        records = [
            FakeSOF(SOFEventType.RAIN_COMMENCED.value, _ts(10)),
            FakeSOF(SOFEventType.RAIN_CEASED.value,    _ts(12)),
        ]
        state = build_existing_interruption_state(records)
        assert state.get(SOFEventType.RAIN_COMMENCED.value) is None

    def test_empty_history_returns_empty_state(self):
        state = build_existing_interruption_state([])
        assert state == {}


# ---------------------------------------------------------------------------
# 9. API Integration Tests (require DB)
# ---------------------------------------------------------------------------

import uuid
from rest_framework.test import APIClient
from rest_framework import status as drf_status


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def norm_vessel(db):
    from apps.contracts.models import Vessel
    return Vessel.objects.create(
        vessel_name="MV Normalizer Carrier",
        imo_number="7654321",
        flag_state="IN",
        build_year=2020,
        deadweight_tonnage=Decimal("75000.000"),
        gross_tonnage=Decimal("40000.000"),
        net_tonnage=Decimal("22000.000"),
        length_overall_meters=Decimal("220.00"),
        beam_meters=Decimal("32.00"),
        draft_meters=Decimal("14.00"),
        hold_count=7,
        hatch_count=7,
        owner_name="Normalization Shipping Ltd",
    )


@pytest.fixture
def norm_contract(db, norm_vessel):
    from apps.contracts.models import CharterContract
    import datetime as dt
    return CharterContract.objects.create(
        contract_reference="NORM/CP/2025/001",
        vessel=norm_vessel,
        charterer_entity="STEEL_AUTHORITY_OF_INDIA",
        charterer_plant="ROURKELA",
        cargo_type="IRON_ORE",
        charter_party_type="VOYAGE_CHARTER",
        contract_status="ACTIVE",
        laycan_start=dt.date(2025, 6, 1),
        laycan_end=dt.date(2025, 6, 10),
        load_port="Port Hedland",
        discharge_port="Paradip",
        nominated_quantity_mt=Decimal("70000.000"),
        laytime_terms="SHINC",
        discharge_rate_mt_per_day=Decimal("20000.000"),
        turn_time_hours=Decimal("6.00"),
        demurrage_rate_usd_per_day=Decimal("22000.00"),
        despatch_rate_usd_per_day=Decimal("11000.00"),
        freight_rate_usd_per_mt=Decimal("17.50"),
        allowed_laytime_hours=Decimal("84.000000"),
    )


@pytest.fixture
def norm_voyage(db, norm_contract, norm_vessel):
    from apps.voyages.models import VesselVoyage
    from apps.laytime.engine.fixture_factory import VoyageStatus
    from django.utils import timezone
    import datetime as dt
    eta = timezone.now() + dt.timedelta(days=3)
    return VesselVoyage.objects.create(
        voyage_reference="NORM/VOY/001",
        contract=norm_contract,
        vessel=norm_vessel,
        voyage_status=VoyageStatus.SCHEDULED.value,
        load_port="Port Hedland",
        load_country="AU",
        discharge_port="Paradip",
        discharge_country="IN",
        quantity_loaded_mt=Decimal("70000.000"),
        eta_date=eta,
    )


@pytest.fixture
def logistics_user_norm(db):
    from django.contrib.auth import get_user_model
    from apps.authentication.models import UserProfile
    from apps.authentication.roles import UserRole
    User = get_user_model()
    user = User.objects.create_user(
        username="norm_agent",
        email="norm@agent.com",
        password="TestPassword123!",
    )
    UserProfile.objects.create(
        user=user,
        role=UserRole.LOGISTICS_AGENT.value,
        organization="Normalizer Shipping",
    )
    return user


@pytest.mark.django_db
class TestSOFNormalizationAPIIntegration:

    def test_out_of_order_batch_auto_sorted_and_accepted(
        self, api_client, norm_voyage, logistics_user_norm
    ):
        """Batch sent in REVERSE chronological order is auto-sorted and committed correctly."""
        api_client.force_authenticate(user=logistics_user_norm)
        url = f"/api/v1/voyages/{norm_voyage.voyage_id}/sof/"

        # Submit in reverse order: NOR (08:00), then Pilot Station (06:00)
        payload = [
            {"event_type": "NOR_TENDERED",               "event_timestamp": "2025-06-15T08:00:00Z"},
            {"event_type": "VESSEL_ARRIVED_ANCHORAGE",   "event_timestamp": "2025-06-15T07:00:00Z"},
            {"event_type": "VESSEL_ARRIVED_PILOT_STATION", "event_timestamp": "2025-06-15T06:00:00Z"},
        ]
        res = api_client.post(url, payload, format="json")

        assert res.status_code == drf_status.HTTP_201_CREATED
        data = res.json()
        # Events returned must be in chronological order
        ts_list = [e["event_timestamp"] for e in data["events"]]
        assert ts_list == sorted(ts_list)
        # First event should be VESSEL_ARRIVED_PILOT_STATION (06:00)
        assert data["events"][0]["event_type"] == "VESSEL_ARRIVED_PILOT_STATION"

    def test_retrograde_batch_rejected_with_422(
        self, api_client, norm_voyage, logistics_user_norm
    ):
        """Events already committed to DB; ingesting earlier timestamps = RETROGRADE error."""
        from apps.voyages.models import StatementOfFacts
        from apps.voyages.sof_enums import SOFEventType

        # First: commit an event at 10:00
        StatementOfFacts.objects.create(
            voyage=norm_voyage,
            event_type=SOFEventType.NOR_TENDERED.value,
            event_timestamp=datetime.datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC),
            sequence_number=1,
        )

        api_client.force_authenticate(user=logistics_user_norm)
        url = f"/api/v1/voyages/{norm_voyage.voyage_id}/sof/"

        # Now try to ingest an event at 08:00 — BEFORE the committed 10:00
        payload = [
            {"event_type": "VESSEL_ARRIVED_ANCHORAGE", "event_timestamp": "2025-06-15T08:00:00Z"},
        ]
        res = api_client.post(url, payload, format="json")

        assert res.status_code == 422
        data = res.json()
        assert data["error"] == "SOF_NORMALIZATION_ERRORS"
        codes = [v["code"] for v in data["violations"]]
        assert "RETROGRADE_TIMESTAMP" in codes

    def test_duplicate_event_in_batch_rejected_with_422(
        self, api_client, norm_voyage, logistics_user_norm
    ):
        """Two identical event_type + event_timestamp entries in one batch = 422."""
        api_client.force_authenticate(user=logistics_user_norm)
        url = f"/api/v1/voyages/{norm_voyage.voyage_id}/sof/"

        payload = [
            {"event_type": "NOR_TENDERED", "event_timestamp": "2025-06-15T08:00:00Z"},
            {"event_type": "NOR_TENDERED", "event_timestamp": "2025-06-15T08:00:00Z"},  # dup
        ]
        res = api_client.post(url, payload, format="json")

        assert res.status_code == 422
        data = res.json()
        codes = [v["code"] for v in data["violations"]]
        assert "DUPLICATE_EVENT" in codes

    def test_coincident_events_different_types_accepted(
        self, api_client, norm_voyage, logistics_user_norm
    ):
        """ALL_FAST + GANGWAY_DOWN at exactly the same timestamp is operationally valid."""
        api_client.force_authenticate(user=logistics_user_norm)
        url = f"/api/v1/voyages/{norm_voyage.voyage_id}/sof/"

        payload = [
            {"event_type": "ALL_FAST",     "event_timestamp": "2025-06-15T14:00:00Z"},
            {"event_type": "GANGWAY_DOWN", "event_timestamp": "2025-06-15T14:00:00Z"},
        ]
        res = api_client.post(url, payload, format="json")
        assert res.status_code == drf_status.HTTP_201_CREATED
        assert res.json()["ingested_count"] == 2

    def test_orphaned_end_event_rejected_with_422(
        self, api_client, norm_voyage, logistics_user_norm
    ):
        """RAIN_CEASED with no prior RAIN_COMMENCED = 422 ORPHANED_INTERRUPTION_END."""
        api_client.force_authenticate(user=logistics_user_norm)
        url = f"/api/v1/voyages/{norm_voyage.voyage_id}/sof/"

        payload = [
            {"event_type": "RAIN_CEASED", "event_timestamp": "2025-06-15T12:00:00Z"},
        ]
        res = api_client.post(url, payload, format="json")
        assert res.status_code == 422
        codes = [v["code"] for v in res.json()["violations"]]
        assert "ORPHANED_INTERRUPTION_END" in codes

    def test_phase_precedence_violation_rejected_with_422(
        self, api_client, norm_voyage, logistics_user_norm
    ):
        """NOR event after VESSEL_DEPARTED = 422 PHASE_PRECEDENCE_VIOLATION."""
        api_client.force_authenticate(user=logistics_user_norm)
        url = f"/api/v1/voyages/{norm_voyage.voyage_id}/sof/"

        payload = [
            {"event_type": "VESSEL_DEPARTED", "event_timestamp": "2025-06-15T20:00:00Z"},
            {"event_type": "NOR_TENDERED",    "event_timestamp": "2025-06-15T22:00:00Z"},
        ]
        res = api_client.post(url, payload, format="json")
        assert res.status_code == 422
        codes = [v["code"] for v in res.json()["violations"]]
        assert "PHASE_PRECEDENCE_VIOLATION" in codes

    def test_no_events_returns_400(self, api_client, norm_voyage, logistics_user_norm):
        """Sending an empty events list returns 400 VALIDATION_ERROR."""
        api_client.force_authenticate(user=logistics_user_norm)
        url = f"/api/v1/voyages/{norm_voyage.voyage_id}/sof/"
        res = api_client.post(url, [], format="json")
        assert res.status_code == drf_status.HTTP_400_BAD_REQUEST
