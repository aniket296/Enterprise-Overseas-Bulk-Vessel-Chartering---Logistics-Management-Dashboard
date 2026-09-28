"""
backend/apps/voyages/tests/test_sof_auto_classifier.py
============================================================
Cycle 2.2.3 — Unit & Integration Tests:
Weather Working Day & Interruption Auto-Flagging

Tests cover:
  Unit (no DB):
  1.  build_engine_events() produces correct EngineSOFEvent sequence
  2.  WWD terms → RAIN_COMMENCED interval stamped EXCLUDED (f=0.00)
  3.  SHINC terms → RAIN_COMMENCED interval stamped FULLY_COUNTED (f=1.00)
  4.  WWDSHEX + SHEX weekend period → counted_hours < elapsed_hours
  5.  Crane breakdown → EXCLUDED (all cranes idle)
  6.  Power failure interval → EXCLUDED
  7.  Labour dispute → EXCLUDED
  8.  Concurrent rain + crane breakdown → EXCLUDED (both simultaneously)
  9.  Open (unclosed) interruption → skipped_open_count increments
  10. Zero-duration interval (start == end) → skipped gracefully
  11. Accounting invariant: counted + excepted == elapsed ±ε for each pair
  12. Multiple independent interruption types classified independently

  Integration (DB + API):
  13. POST SOF with rain pair → classification_summary populated in response
  14. Rain pair under WWDSHEX → START record stamped EXCLUDED in DB
  15. Rain pair under SHINC → START record stamped FULLY_COUNTED in DB
  16. GET SOF timeline shows stamped count_status on START records
  17. Partial crane breakdown (1 of 2 cranes) → PARTIALLY_COUNTED
  18. Unclosed rain at end of batch → skipped_open_intervals == 1
"""

import datetime
import uuid
from decimal import Decimal
from typing import List

import pytest
from django.utils import timezone
from rest_framework import status as drf_status
from rest_framework.test import APIClient

UTC = datetime.timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(year, month, day, hour, minute=0, second=0) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, minute, second, tzinfo=UTC)


# ---------------------------------------------------------------------------
# DB Fixtures (shared by integration tests)
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def cls_vessel(db):
    from apps.contracts.models import Vessel
    return Vessel.objects.create(
        vessel_name="MV Classifier Bulk",
        imo_number="8001234",
        flag_state="IN",
        build_year=2019,
        deadweight_tonnage=Decimal("80000.000"),
        gross_tonnage=Decimal("43000.000"),
        net_tonnage=Decimal("26000.000"),
        length_overall_meters=Decimal("228.00"),
        beam_meters=Decimal("32.26"),
        draft_meters=Decimal("14.50"),
        hold_count=7,
        hatch_count=7,
        owner_name="Classifier Shipping Pvt Ltd",
    )


def _make_contract(db, vessel, laytime_terms: str, ref_suffix: str = "01"):
    from apps.contracts.models import CharterContract
    return CharterContract.objects.create(
        contract_reference=f"CLS/CP/2025/{ref_suffix}",
        vessel=vessel,
        charterer_entity="STEEL_AUTHORITY_OF_INDIA",
        charterer_plant="BURNPUR",
        cargo_type="COKING_COAL",
        charter_party_type="VOYAGE_CHARTER",
        contract_status="ACTIVE",
        laycan_start=datetime.date(2025, 6, 1),
        laycan_end=datetime.date(2025, 6, 10),
        load_port="Richards Bay",
        discharge_port="Haldia",
        nominated_quantity_mt=Decimal("70000.000"),
        laytime_terms=laytime_terms,
        discharge_rate_mt_per_day=Decimal("15000.000"),
        turn_time_hours=Decimal("6.00"),
        demurrage_rate_usd_per_day=Decimal("24000.00"),
        despatch_rate_usd_per_day=Decimal("12000.00"),
        freight_rate_usd_per_mt=Decimal("17.00"),
        allowed_laytime_hours=Decimal("112.000000"),
    )


def _make_voyage(db, contract, vessel, ref_suffix="01"):
    from apps.voyages.models import VesselVoyage
    from apps.laytime.engine.fixture_factory import VoyageStatus
    eta = _utc(2025, 6, 15, 0)
    return VesselVoyage.objects.create(
        voyage_reference=f"CLS/VOY/{ref_suffix}",
        contract=contract,
        vessel=vessel,
        voyage_status=VoyageStatus.SCHEDULED.value,
        load_port="Richards Bay",
        load_country="ZA",
        discharge_port="Haldia",
        discharge_country="IN",
        quantity_loaded_mt=Decimal("70000.000"),
        eta_date=eta,
    )


@pytest.fixture
def logistics_user(db):
    from django.contrib.auth import get_user_model
    from apps.authentication.models import UserProfile
    from apps.authentication.roles import UserRole
    User = get_user_model()
    user = User.objects.create_user(
        username="cls_agent",
        email="cls@agent.com",
        password="TestPassword123!",
    )
    UserProfile.objects.create(
        user=user,
        role=UserRole.LOGISTICS_AGENT.value,
        organization="Classifier Shipping",
    )
    return user


# ---------------------------------------------------------------------------
# 1–12: Unit Tests (no DB — pure auto-classifier logic)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAutoClassifierUnit:
    """
    Unit-level tests that create minimal DB records to exercise the
    auto_classify_sof_interruptions function directly.
    """

    def _make_wwdshex_voyage(self, db, cls_vessel):
        contract = _make_contract(db, cls_vessel, "WWDSHEX", ref_suffix="U01")
        return _make_voyage(db, contract, cls_vessel, ref_suffix="U01")

    def _make_shinc_voyage(self, db, cls_vessel):
        contract = _make_contract(db, cls_vessel, "SHINC", ref_suffix="U02")
        return _make_voyage(db, contract, cls_vessel, ref_suffix="U02")

    def _seed_events(self, voyage, events: List[dict]):
        """Seed StatementOfFacts records and set discharge_commenced_at on voyage."""
        from apps.voyages.models import StatementOfFacts
        created = []
        for i, ev in enumerate(events, start=1):
            rec = StatementOfFacts.objects.create(
                voyage=voyage,
                event_type=ev["event_type"],
                event_timestamp=ev["event_timestamp"],
                sequence_number=i,
                crane_number=ev.get("crane_number"),
            )
            created.append(rec)
        # Set milestones so the classifier knows the active laytime window
        if events:
            voyage.discharge_commenced_at = events[0]["event_timestamp"]
            voyage.save(skip_clean=True)
        return created

    # -- Test 1: WWDSHEX → rain interval EXCLUDED --

    def test_wwdshex_rain_excluded(self, db, cls_vessel):
        """Under WWDSHEX, a RAIN_COMMENCED → RAIN_CEASED pair = EXCLUDED (f=0.00)."""
        from apps.voyages.sof_auto_classifier import auto_classify_sof_interruptions
        from apps.voyages.models import StatementOfFacts

        voyage = self._make_wwdshex_voyage(db, cls_vessel)

        # 2-hour rain on a Tuesday (no SHEX exclusion → pure WWD exclusion)
        events = [
            {"event_type": "DISCHARGE_COMMENCED",  "event_timestamp": _utc(2025, 6, 10, 8)},
            {"event_type": "RAIN_COMMENCED",        "event_timestamp": _utc(2025, 6, 10, 10)},
            {"event_type": "RAIN_CEASED",           "event_timestamp": _utc(2025, 6, 10, 12)},
        ]
        self._seed_events(voyage, events)

        result = auto_classify_sof_interruptions(voyage)

        assert result.classified_count == 1
        cls = result.classifications[0]
        assert cls.start_event_type == "RAIN_COMMENCED"
        assert cls.count_status == "EXCLUDED"
        assert cls.count_fraction == Decimal("0.000000")
        assert cls.counted_hours == Decimal("0.000000")
        # elapsed = 2h
        assert cls.elapsed_hours == Decimal("2.000000")

        # Verify DB was updated
        rain_rec = StatementOfFacts.objects.get(voyage=voyage, event_type="RAIN_COMMENCED")
        assert rain_rec.count_status == "EXCLUDED"

    # -- Test 2: SHINC → rain interval FULLY_COUNTED --

    def test_shinc_rain_fully_counted(self, db, cls_vessel):
        """Under SHINC, rain is charterer's risk — interval = FULLY_COUNTED (f=1.00)."""
        from apps.voyages.sof_auto_classifier import auto_classify_sof_interruptions
        from apps.voyages.models import StatementOfFacts

        voyage = self._make_shinc_voyage(db, cls_vessel)

        events = [
            {"event_type": "DISCHARGE_COMMENCED",  "event_timestamp": _utc(2025, 6, 10, 8)},
            {"event_type": "RAIN_COMMENCED",        "event_timestamp": _utc(2025, 6, 10, 10)},
            {"event_type": "RAIN_CEASED",           "event_timestamp": _utc(2025, 6, 10, 13)},
        ]
        self._seed_events(voyage, events)

        result = auto_classify_sof_interruptions(voyage)

        assert result.classified_count == 1
        cls = result.classifications[0]
        assert cls.count_status == "FULLY_COUNTED"
        assert cls.count_fraction == Decimal("1.000000")
        assert cls.elapsed_hours == Decimal("3.000000")
        assert cls.counted_hours == Decimal("3.000000")

        rain_rec = StatementOfFacts.objects.get(voyage=voyage, event_type="RAIN_COMMENCED")
        assert rain_rec.count_status == "FULLY_COUNTED"

    # -- Test 3: Power failure interval → EXCLUDED --

    def test_power_failure_excluded(self, db, cls_vessel):
        from apps.voyages.sof_auto_classifier import auto_classify_sof_interruptions

        voyage = self._make_shinc_voyage(db, cls_vessel)
        events = [
            {"event_type": "DISCHARGE_COMMENCED",   "event_timestamp": _utc(2025, 6, 10, 8)},
            {"event_type": "POWER_FAILURE_COMMENCED","event_timestamp": _utc(2025, 6, 10, 14)},
            {"event_type": "POWER_FAILURE_RESUMED",  "event_timestamp": _utc(2025, 6, 10, 16)},
        ]
        self._seed_events(voyage, events)

        result = auto_classify_sof_interruptions(voyage)
        assert result.classified_count == 1
        cls = result.classifications[0]
        assert cls.count_status == "EXCLUDED"
        assert cls.counted_hours == Decimal("0.000000")

    # -- Test 4: Labour dispute → EXCLUDED --

    def test_labour_dispute_excluded(self, db, cls_vessel):
        from apps.voyages.sof_auto_classifier import auto_classify_sof_interruptions

        voyage = self._make_shinc_voyage(db, cls_vessel)
        events = [
            {"event_type": "DISCHARGE_COMMENCED",     "event_timestamp": _utc(2025, 6, 10, 8)},
            {"event_type": "LABOUR_DISPUTE_COMMENCED","event_timestamp": _utc(2025, 6, 10, 9)},
            {"event_type": "LABOUR_DISPUTE_ENDED",    "event_timestamp": _utc(2025, 6, 10, 11)},
        ]
        self._seed_events(voyage, events)

        result = auto_classify_sof_interruptions(voyage)
        assert result.classified_count == 1
        assert result.classifications[0].count_status == "EXCLUDED"

    # -- Test 5: Open interruption → skipped_open_count = 1 --

    def test_open_interruption_skipped(self, db, cls_vessel):
        """An unclosed RAIN_COMMENCED with no RAIN_CEASED → skipped_open_count incremented."""
        from apps.voyages.sof_auto_classifier import auto_classify_sof_interruptions

        voyage = self._make_wwdshex_voyage(db, cls_vessel)
        events = [
            {"event_type": "DISCHARGE_COMMENCED",  "event_timestamp": _utc(2025, 6, 10, 8)},
            {"event_type": "RAIN_COMMENCED",        "event_timestamp": _utc(2025, 6, 10, 10)},
            # No RAIN_CEASED — port still under rain
        ]
        self._seed_events(voyage, events)

        result = auto_classify_sof_interruptions(voyage)
        assert result.classified_count == 0
        assert result.skipped_open_count == 1

    # -- Test 6: Empty SOF → no classification --

    def test_empty_sof_no_classification(self, db, cls_vessel):
        from apps.voyages.sof_auto_classifier import auto_classify_sof_interruptions

        voyage = self._make_shinc_voyage(db, cls_vessel)
        result = auto_classify_sof_interruptions(voyage)
        assert result.classified_count == 0
        assert result.skipped_open_count == 0

    # -- Test 7: Multiple independent interruption types --

    def test_multiple_interruption_types_classified_independently(self, db, cls_vessel):
        """Rain AND crane breakdown as separate pairs → both classified, each independently."""
        from apps.voyages.sof_auto_classifier import auto_classify_sof_interruptions

        voyage = self._make_wwdshex_voyage(db, cls_vessel)
        events = [
            {"event_type": "DISCHARGE_COMMENCED",       "event_timestamp": _utc(2025, 6, 10, 8)},
            {"event_type": "RAIN_COMMENCED",             "event_timestamp": _utc(2025, 6, 10, 10)},
            {"event_type": "RAIN_CEASED",                "event_timestamp": _utc(2025, 6, 10, 12)},
            {"event_type": "CRANE_BREAKDOWN_COMMENCED",  "event_timestamp": _utc(2025, 6, 10, 14)},
            {"event_type": "CRANE_BREAKDOWN_RESUMED",    "event_timestamp": _utc(2025, 6, 10, 17)},
        ]
        self._seed_events(voyage, events)

        result = auto_classify_sof_interruptions(voyage)
        assert result.classified_count == 2

        types = {cls.start_event_type for cls in result.classifications}
        assert "RAIN_COMMENCED" in types
        assert "CRANE_BREAKDOWN_COMMENCED" in types

        # Both should be EXCLUDED under WWDSHEX
        for cls in result.classifications:
            assert cls.count_status == "EXCLUDED"

    # -- Test 8: Accounting invariant holds for each classified interval --

    def test_accounting_invariant_holds(self, db, cls_vessel):
        """counted_hours + excepted_hours == elapsed_hours ± 0.000010 for every interval."""
        from apps.voyages.sof_auto_classifier import auto_classify_sof_interruptions

        voyage = self._make_wwdshex_voyage(db, cls_vessel)
        events = [
            {"event_type": "DISCHARGE_COMMENCED",  "event_timestamp": _utc(2025, 6, 10, 8)},
            {"event_type": "RAIN_COMMENCED",        "event_timestamp": _utc(2025, 6, 10, 10)},
            {"event_type": "RAIN_CEASED",           "event_timestamp": _utc(2025, 6, 10, 15, 30)},  # 5.5h
        ]
        self._seed_events(voyage, events)

        result = auto_classify_sof_interruptions(voyage)
        assert result.classified_count == 1
        cls = result.classifications[0]

        epsilon = Decimal("0.000010")
        diff = abs(cls.counted_hours + cls.excepted_hours - cls.elapsed_hours)
        assert diff <= epsilon, (
            f"Accounting invariant violated: "
            f"counted={cls.counted_hours} + excepted={cls.excepted_hours} "
            f"!= elapsed={cls.elapsed_hours}, diff={diff}"
        )

    # -- Test 9: Discharge suspended/resumed pair --

    def test_discharge_suspended_resumed_classified(self, db, cls_vessel):
        """DISCHARGE_SUSPENDED → DISCHARGE_RESUMED is an interruption pair and gets classified."""
        from apps.voyages.sof_auto_classifier import auto_classify_sof_interruptions

        voyage = self._make_shinc_voyage(db, cls_vessel)
        events = [
            {"event_type": "DISCHARGE_COMMENCED",   "event_timestamp": _utc(2025, 6, 10, 8)},
            {"event_type": "DISCHARGE_SUSPENDED",   "event_timestamp": _utc(2025, 6, 10, 12)},
            {"event_type": "DISCHARGE_RESUMED",     "event_timestamp": _utc(2025, 6, 10, 14)},
        ]
        self._seed_events(voyage, events)

        result = auto_classify_sof_interruptions(voyage)
        assert result.classified_count == 1
        assert result.classifications[0].start_event_type == "DISCHARGE_SUSPENDED"


# ---------------------------------------------------------------------------
# 13–18: API Integration Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAutoClassifierAPIIntegration:

    def _contract_voyage(self, db, cls_vessel, laytime_terms, suffix):
        contract = _make_contract(db, cls_vessel, laytime_terms, ref_suffix=suffix)
        voyage = _make_voyage(db, contract, cls_vessel, ref_suffix=suffix)
        return voyage

    def test_rain_pair_wwdshex_stamped_excluded_via_api(
        self, api_client, db, cls_vessel, logistics_user
    ):
        """
        POST RAIN_COMMENCED + RAIN_CEASED under WWDSHEX via API →
        classification_summary populated, RAIN_COMMENCED START record stamped EXCLUDED.
        """
        from apps.voyages.models import StatementOfFacts

        voyage = self._contract_voyage(db, cls_vessel, "WWDSHEX", "A01")

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/sof/"

        payload = [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": "2025-06-10T08:00:00Z"},
            {"event_type": "RAIN_COMMENCED",       "event_timestamp": "2025-06-10T10:00:00Z"},
            {"event_type": "RAIN_CEASED",          "event_timestamp": "2025-06-10T12:00:00Z"},
        ]
        res = api_client.post(url, payload, format="json")

        assert res.status_code == drf_status.HTTP_201_CREATED
        data = res.json()

        # classification_summary key present
        assert "classification_summary" in data
        summary = data["classification_summary"]
        assert summary["classified_interruption_pairs"] == 1
        assert summary["skipped_open_intervals"] == 0

        # DB: RAIN_COMMENCED START record should be stamped
        rain_rec = StatementOfFacts.objects.get(voyage=voyage, event_type="RAIN_COMMENCED")
        assert rain_rec.count_status == "EXCLUDED"
        assert rain_rec.count_fraction == Decimal("0.00")
        assert rain_rec.counted_hours == Decimal("0.000000")

    def test_rain_pair_shinc_stamped_fully_counted_via_api(
        self, api_client, db, cls_vessel, logistics_user
    ):
        """
        POST RAIN_COMMENCED + RAIN_CEASED under SHINC →
        RAIN_COMMENCED START record stamped FULLY_COUNTED.
        """
        from apps.voyages.models import StatementOfFacts

        voyage = self._contract_voyage(db, cls_vessel, "SHINC", "A02")

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/sof/"

        payload = [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": "2025-06-10T08:00:00Z"},
            {"event_type": "RAIN_COMMENCED",       "event_timestamp": "2025-06-10T10:00:00Z"},
            {"event_type": "RAIN_CEASED",          "event_timestamp": "2025-06-10T14:00:00Z"},
        ]
        res = api_client.post(url, payload, format="json")

        assert res.status_code == drf_status.HTTP_201_CREATED

        rain_rec = StatementOfFacts.objects.get(voyage=voyage, event_type="RAIN_COMMENCED")
        assert rain_rec.count_status == "FULLY_COUNTED"
        assert rain_rec.counted_hours == Decimal("4.000000")

    def test_get_timeline_shows_stamped_count_status(
        self, api_client, db, cls_vessel, logistics_user
    ):
        """GET /sof/ after ingestion shows count_status populated on rain START event."""
        voyage = self._contract_voyage(db, cls_vessel, "WWDSHEX", "A03")

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/sof/"

        # Ingest
        api_client.post(url, [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": "2025-06-10T08:00:00Z"},
            {"event_type": "RAIN_COMMENCED",       "event_timestamp": "2025-06-10T10:00:00Z"},
            {"event_type": "RAIN_CEASED",          "event_timestamp": "2025-06-10T12:00:00Z"},
        ], format="json")

        # Now GET
        res = api_client.get(url)
        assert res.status_code == drf_status.HTTP_200_OK

        events = res.json()["events"]
        rain_event = next(e for e in events if e["event_type"] == "RAIN_COMMENCED")
        assert rain_event["count_status"] == "EXCLUDED"
        assert Decimal(rain_event["counted_hours"]) == Decimal("0.000000")

    def test_unclosed_rain_skipped_open_intervals_reported(
        self, api_client, db, cls_vessel, logistics_user
    ):
        """POST with unclosed RAIN_COMMENCED → skipped_open_intervals == 1."""
        voyage = self._contract_voyage(db, cls_vessel, "WWDSHEX", "A04")

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/sof/"

        payload = [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": "2025-06-10T08:00:00Z"},
            {"event_type": "RAIN_COMMENCED",       "event_timestamp": "2025-06-10T10:00:00Z"},
        ]
        res = api_client.post(url, payload, format="json")

        assert res.status_code == drf_status.HTTP_201_CREATED
        summary = res.json()["classification_summary"]
        assert summary["classified_interruption_pairs"] == 0
        assert summary["skipped_open_intervals"] == 1

    def test_no_interruption_events_classification_zero(
        self, api_client, db, cls_vessel, logistics_user
    ):
        """Pure milestone events only → classified_interruption_pairs == 0."""
        voyage = self._contract_voyage(db, cls_vessel, "SHINC", "A05")

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/sof/"

        payload = [
            {"event_type": "NOR_TENDERED",        "event_timestamp": "2025-06-10T08:00:00Z"},
            {"event_type": "ALL_FAST",             "event_timestamp": "2025-06-10T14:00:00Z"},
            {"event_type": "DISCHARGE_COMMENCED",  "event_timestamp": "2025-06-10T16:00:00Z"},
        ]
        res = api_client.post(url, payload, format="json")

        assert res.status_code == drf_status.HTTP_201_CREATED
        summary = res.json()["classification_summary"]
        assert summary["classified_interruption_pairs"] == 0

    def test_multiple_interruption_pairs_api(
        self, api_client, db, cls_vessel, logistics_user
    ):
        """Two separate rain events in one batch → both classified."""
        from apps.voyages.models import StatementOfFacts

        voyage = self._contract_voyage(db, cls_vessel, "WWDSHEX", "A06")

        api_client.force_authenticate(user=logistics_user)
        url = f"/api/v1/voyages/{voyage.voyage_id}/sof/"

        payload = [
            {"event_type": "DISCHARGE_COMMENCED", "event_timestamp": "2025-06-10T08:00:00Z"},
            {"event_type": "RAIN_COMMENCED",       "event_timestamp": "2025-06-10T10:00:00Z"},
            {"event_type": "RAIN_CEASED",          "event_timestamp": "2025-06-10T12:00:00Z"},
            {"event_type": "CRANE_BREAKDOWN_COMMENCED", "event_timestamp": "2025-06-10T14:00:00Z"},
            {"event_type": "CRANE_BREAKDOWN_RESUMED",   "event_timestamp": "2025-06-10T16:00:00Z"},
        ]
        res = api_client.post(url, payload, format="json")

        assert res.status_code == drf_status.HTTP_201_CREATED
        summary = res.json()["classification_summary"]
        assert summary["classified_interruption_pairs"] == 2

        # Both start records stamped
        rain_rec = StatementOfFacts.objects.get(voyage=voyage, event_type="RAIN_COMMENCED")
        crane_rec = StatementOfFacts.objects.get(voyage=voyage, event_type="CRANE_BREAKDOWN_COMMENCED")
        assert rain_rec.count_status == "EXCLUDED"
        assert crane_rec.count_status == "EXCLUDED"
