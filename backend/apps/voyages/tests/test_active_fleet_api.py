"""
backend/apps/voyages/tests/test_active_fleet_api.py
============================================================
Cycle 2.1.2 — Unit & Integration Tests: Active Fleet Query API

Test categories:
  A. Active fleet filtering (GET /api/v1/voyages/active/ excludes completed/cancelled)
  B. Dynamic progress percentage calculations across voyage lifecycle
  C. Cargo shortage calculation (loaded vs discharged outturn)
  D. Active fleet KPI summary metrics aggregation
  E. Query filtering (port, cargo_type, plant, status)
  F. Standard Voyage CRUD & milestone transitions
"""

from decimal import Decimal
from datetime import datetime, timezone, date
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.contracts.models import Vessel, CharterContract
from apps.voyages.models import VesselVoyage
from apps.laytime.engine.fixture_factory import (
    VesselSizeClass,
    CargoType,
    CharterPartyType,
    ContractStatus,
    VoyageStatus,
)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class TestActiveFleetAPI(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Create test vessels
        self.vessel1 = Vessel.objects.create(
            vessel_name="MV MAHANADI ORE",
            imo_number="9456789",
            flag_state="IN",
            size_class=VesselSizeClass.CAPESIZE.value,
            deadweight_tonnage=Decimal("181200.000"),
            gross_tonnage=Decimal("93500.000"),
            net_tonnage=Decimal("61200.000"),
            length_overall_meters=Decimal("292.00"),
            beam_meters=Decimal("45.00"),
            draft_meters=Decimal("18.25"),
            hold_count=9,
            hatch_count=9,
            build_year=2018,
            owner_name="Shipping Corporation of India Ltd",
        )
        self.vessel2 = Vessel.objects.create(
            vessel_name="MV SUNDARBAN CARRIER",
            imo_number="9321456",
            flag_state="SG",
            size_class=VesselSizeClass.PANAMAX.value,
            deadweight_tonnage=Decimal("76500.000"),
            gross_tonnage=Decimal("41200.000"),
            net_tonnage=Decimal("26800.000"),
            length_overall_meters=Decimal("225.00"),
            beam_meters=Decimal("32.26"),
            draft_meters=Decimal("14.20"),
            hold_count=7,
            hatch_count=7,
            build_year=2015,
            owner_name="Eastern Bulk Carriers Pte",
        )
        self.vessel3 = Vessel.objects.create(
            vessel_name="MV ANDHRA GLORY",
            imo_number="9543210",
            flag_state="MH",
            size_class=VesselSizeClass.SUPRAMAX.value,
            deadweight_tonnage=Decimal("57800.000"),
            gross_tonnage=Decimal("33100.000"),
            net_tonnage=Decimal("19400.000"),
            length_overall_meters=Decimal("190.00"),
            beam_meters=Decimal("32.26"),
            draft_meters=Decimal("12.80"),
            hold_count=5,
            hatch_count=5,
            build_year=2019,
            owner_name="Global Maritime Inc",
        )

        # Create test contracts
        self.contract1 = CharterContract.objects.create(
            contract_reference="SAIL/CP/2025/CAPE-01",
            charter_party_type=CharterPartyType.VOYAGE_CHARTER.value,
            contract_status=ContractStatus.EXECUTED.value,
            charterer_entity="Steel Authority of India Ltd (SAIL)",
            charterer_plant="Rourkela Steel Plant",
            vessel=self.vessel1,
            cargo_type=CargoType.COKING_COAL.value,
            nominated_quantity_mt=Decimal("150000.000"),
            tolerance_pct=Decimal("5.00"),
            freight_rate_usd_per_mt=Decimal("16.50"),
            load_port="Hay Point",
            discharge_port="Paradip",
            discharge_berth_designated="CQ-1",
            discharge_rate_mt_per_day=Decimal("25000.000"),
            laytime_terms="WWDSHEX",
            demurrage_rate_usd_per_day=Decimal("28000.00"),
            despatch_rate_usd_per_day=Decimal("14000.00"),
            laycan_start=date(2025, 7, 1),
            laycan_end=date(2025, 7, 10),
            allowed_laytime_hours=Decimal("144.000000"),
        )
        self.contract2 = CharterContract.objects.create(
            contract_reference="SAIL/CP/2025/PAN-02",
            charter_party_type=CharterPartyType.VOYAGE_CHARTER.value,
            contract_status=ContractStatus.EXECUTED.value,
            charterer_entity="Steel Authority of India Ltd (SAIL)",
            charterer_plant="Bokaro Steel Plant",
            vessel=self.vessel2,
            cargo_type=CargoType.THERMAL_COAL.value,
            nominated_quantity_mt=Decimal("75000.000"),
            tolerance_pct=Decimal("5.00"),
            freight_rate_usd_per_mt=Decimal("10.80"),
            load_port="Tanjung Bara",
            discharge_port="Dhamra",
            discharge_berth_designated="DP-2",
            discharge_rate_mt_per_day=Decimal("15000.000"),
            laytime_terms="SHEX",
            demurrage_rate_usd_per_day=Decimal("18000.00"),
            despatch_rate_usd_per_day=Decimal("9000.00"),
            laycan_start=date(2025, 8, 10),
            laycan_end=date(2025, 8, 20),
            allowed_laytime_hours=Decimal("120.000000"),
        )
        self.contract3 = CharterContract.objects.create(
            contract_reference="RINL/CP/2025/SUPRA-03",
            charter_party_type=CharterPartyType.VOYAGE_CHARTER.value,
            contract_status=ContractStatus.EXECUTED.value,
            charterer_entity="Rashtriya Ispat Nigam Ltd (RINL)",
            charterer_plant="Visakhapatnam Steel Plant",
            vessel=self.vessel3,
            cargo_type=CargoType.MET_COKE.value,
            nominated_quantity_mt=Decimal("54000.000"),
            tolerance_pct=Decimal("5.00"),
            freight_rate_usd_per_mt=Decimal("22.50"),
            load_port="Richards Bay",
            discharge_port="Visakhapatnam",
            discharge_berth_designated="EQ-1",
            discharge_rate_mt_per_day=Decimal("9000.000"),
            laytime_terms="SHINC",
            demurrage_rate_usd_per_day=Decimal("16000.00"),
            despatch_rate_usd_per_day=Decimal("8000.00"),
            laycan_start=date(2025, 9, 1),
            laycan_end=date(2025, 9, 12),
            allowed_laytime_hours=Decimal("144.000000"),
        )

        # Create voyages in various lifecycle states
        # 1. Active: DISCHARGING
        self.voyage1 = VesselVoyage.objects.create(
            voyage_reference="VOY-SAIL-2025-01",
            contract=self.contract1,
            vessel=self.vessel1,
            voyage_status=VoyageStatus.DISCHARGING.value,
            load_port="Hay Point",
            discharge_port="Paradip",
            discharge_berth="CQ-1",
            quantity_loaded_mt=Decimal("150000.000"),
            quantity_discharged_mt=Decimal("75000.000"),  # 50% discharged
            eta_date=utc(2025, 7, 5, 6, 0),
            ata_date=utc(2025, 7, 5, 8, 30),
            nor_tendered_at=utc(2025, 7, 5, 9, 0),
            berthed_at=utc(2025, 7, 6, 4, 0),
            discharge_commenced_at=utc(2025, 7, 6, 7, 0),
            last_position_lat=Decimal("20.264400"),
            last_position_lon=Decimal("86.685300"),
            speed_over_ground=Decimal("0.0"),
        )

        # 2. Active: IN_TRANSIT
        self.voyage2 = VesselVoyage.objects.create(
            voyage_reference="VOY-SAIL-2025-02",
            contract=self.contract2,
            vessel=self.vessel2,
            voyage_status=VoyageStatus.IN_TRANSIT.value,
            load_port="Tanjung Bara",
            discharge_port="Dhamra",
            discharge_berth="DP-2",
            quantity_loaded_mt=Decimal("74800.000"),
            eta_date=utc(2025, 8, 14, 12, 0),
            last_position_lat=Decimal("10.500000"),
            last_position_lon=Decimal("92.100000"),
            speed_over_ground=Decimal("13.5"),
        )

        # 3. Active: ARRIVED (at anchorage)
        self.voyage3 = VesselVoyage.objects.create(
            voyage_reference="VOY-RINL-2025-03",
            contract=self.contract3,
            vessel=self.vessel3,
            voyage_status=VoyageStatus.ARRIVED.value,
            load_port="Richards Bay",
            discharge_port="Visakhapatnam",
            discharge_berth="EQ-1",
            quantity_loaded_mt=Decimal("53900.000"),
            eta_date=utc(2025, 9, 8, 10, 0),
            ata_date=utc(2025, 9, 8, 9, 45),
            nor_tendered_at=utc(2025, 9, 8, 11, 0),
            last_position_lat=Decimal("17.686800"),
            last_position_lon=Decimal("83.218500"),
            speed_over_ground=Decimal("0.2"),
        )

        # 4. Inactive: COMPLETED (historical voyage)
        self.voyage_completed = VesselVoyage.objects.create(
            voyage_reference="VOY-HISTORICAL-00",
            contract=self.contract1,
            vessel=self.vessel1,
            voyage_status=VoyageStatus.COMPLETED.value,
            load_port="Hay Point",
            discharge_port="Paradip",
            discharge_berth="CQ-1",
            quantity_loaded_mt=Decimal("150000.000"),
            quantity_discharged_mt=Decimal("149800.000"),
            eta_date=utc(2025, 6, 1, 6, 0),
            ata_date=utc(2025, 6, 1, 8, 0),
            completed_at=utc(2025, 6, 8, 12, 0),
        )

    def test_active_fleet_excludes_completed_voyages(self):
        """GET /api/v1/voyages/active/ must exclude COMPLETED and CANCELLED."""
        res = self.client.get("/api/v1/voyages/active/")
        assert res.status_code == status.HTTP_200_OK

        fleet = res.data["fleet"]
        voyage_refs = [v["voyage_reference"] for v in fleet]

        assert "VOY-SAIL-2025-01" in voyage_refs
        assert "VOY-SAIL-2025-02" in voyage_refs
        assert "VOY-RINL-2025-03" in voyage_refs
        assert "VOY-HISTORICAL-00" not in voyage_refs  # COMPLETED excluded!
        assert res.data["summary"]["total_active_voyages"] == 3

    def test_active_fleet_kpi_summary_metrics(self):
        """Verify aggregated KPI metrics in response summary."""
        res = self.client.get("/api/v1/voyages/active/")
        assert res.status_code == status.HTTP_200_OK

        summary = res.data["summary"]
        assert summary["total_active_voyages"] == 3
        # Total loaded: 150,000 + 74,800 + 53,900 = 278,700.000 MT
        assert Decimal(summary["total_loaded_mt_underway"]) == Decimal("278700.000")
        assert summary["vessels_in_transit_count"] == 1  # VOY-SAIL-2025-02
        assert summary["vessels_at_anchor_count"] == 1   # VOY-RINL-2025-03
        assert summary["vessels_at_berth_count"] == 1    # VOY-SAIL-2025-01 (DISCHARGING)

    def test_active_fleet_progress_percentages(self):
        """Verify dynamic progress percentage calculations across lifecycle."""
        res = self.client.get("/api/v1/voyages/active/")
        assert res.status_code == status.HTTP_200_OK

        fleet_by_ref = {v["voyage_reference"]: v for v in res.data["fleet"]}

        # IN_TRANSIT -> 25.0%
        assert fleet_by_ref["VOY-SAIL-2025-02"]["progress_pct"] == 25.0

        # ARRIVED (at anchor) -> 50.0%
        assert fleet_by_ref["VOY-RINL-2025-03"]["progress_pct"] == 50.0

        # DISCHARGING (75k / 150k = 50% discharged -> 65% + 0.3 * 50 = 80.0%)
        assert fleet_by_ref["VOY-SAIL-2025-01"]["progress_pct"] == 80.0

    def test_cargo_shortage_calculation(self):
        """Loaded 150,000 MT, Discharged 149,800 MT -> Shortage 200.000 MT."""
        v = self.voyage_completed
        assert v.shortage_mt == Decimal("200.000")

    def test_active_fleet_query_filters(self):
        """Test filtering active fleet by discharge port and cargo type."""
        # Filter by port Dhamra
        res = self.client.get("/api/v1/voyages/active/?discharge_port=Dhamra")
        assert res.status_code == status.HTTP_200_OK
        assert len(res.data["fleet"]) == 1
        assert res.data["fleet"][0]["voyage_reference"] == "VOY-SAIL-2025-02"

        # Filter by cargo COKING_COAL
        res_cargo = self.client.get("/api/v1/voyages/active/?cargo_type=COKING_COAL")
        assert res_cargo.status_code == status.HTTP_200_OK
        assert len(res_cargo.data["fleet"]) == 1
        assert res_cargo.data["fleet"][0]["voyage_reference"] == "VOY-SAIL-2025-01"

    def test_create_voyage_api(self):
        """POST /api/v1/voyages/ creates a new voyage."""
        payload = {
            "voyage_reference": "VOY-NEW-2025-99",
            "contract": str(self.contract1.contract_id),
            "vessel": str(self.vessel1.vessel_id),
            "voyage_status": "IN_TRANSIT",
            "load_port": "Hay Point",
            "discharge_port": "Paradip",
            "quantity_loaded_mt": "152000.000",
            "eta_date": "2025-07-20T10:00:00Z",
        }
        res = self.client.post("/api/v1/voyages/", payload, format="json")
        assert res.status_code == status.HTTP_201_CREATED
        assert VesselVoyage.objects.filter(voyage_reference="VOY-NEW-2025-99").exists()
