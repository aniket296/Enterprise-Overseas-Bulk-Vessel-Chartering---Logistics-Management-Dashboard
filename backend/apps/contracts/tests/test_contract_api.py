"""
backend/apps/contracts/tests/test_contract_api.py
============================================================
Cycle 2.1.1 — Unit & Integration Tests: Charter Contract & Vessel API

Test categories:
  A. Vessel Registry CRUD & IMO validation
  B. Charter Contract creation and automated laytime calculation
  C. Contract constraints enforcement (laycan window, rates, tonnages)
  D. Query filtering & pagination
  E. Mathematical specifications endpoint (/api/v1/contracts/{id}/specifications/)
  F. Baltic Exchange standard fixture seeding endpoint
"""

import json
from decimal import Decimal
from datetime import date
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.contracts.models import Vessel, CharterContract
from apps.laytime.engine.fixture_factory import (
    VesselSizeClass,
    CargoType,
    CharterPartyType,
    ContractStatus,
)
from apps.laytime.engine.constants import LaytimeTerms


class TestVesselAPI(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_create_vessel_valid(self):
        payload = {
            "vessel_name": "MV PARADIP PIONEER",
            "imo_number": "9123456",
            "flag_state": "IN",
            "size_class": "CAPESIZE",
            "deadweight_tonnage": "180000.000",
            "gross_tonnage": "92000.000",
            "net_tonnage": "60000.000",
            "length_overall_meters": "290.00",
            "beam_meters": "45.00",
            "draft_meters": "18.00",
            "hold_count": 9,
            "hatch_count": 9,
            "build_year": 2021,
            "owner_name": "Shipping Corp of India",
        }
        res = self.client.post("/api/v1/vessels/", payload, format="json")
        assert res.status_code == status.HTTP_201_CREATED
        assert Vessel.objects.filter(imo_number="9123456").exists()

    def test_create_vessel_invalid_imo_rejected(self):
        payload = {
            "vessel_name": "BAD IMO VESSEL",
            "imo_number": "12345",  # 5 digits instead of 7
            "flag_state": "PA",
            "size_class": "PANAMAX",
            "deadweight_tonnage": "75000.000",
            "gross_tonnage": "40000.000",
            "net_tonnage": "25000.000",
            "length_overall_meters": "225.00",
            "beam_meters": "32.00",
            "draft_meters": "14.00",
            "hold_count": 7,
            "hatch_count": 7,
            "build_year": 2019,
            "owner_name": "Test Owner",
        }
        res = self.client.post("/api/v1/vessels/", payload, format="json")
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert "imo_number" in res.data

    def test_lookup_vessel_by_imo(self):
        vessel = Vessel.objects.create(
            vessel_name="MV DHAMRA STAR",
            imo_number="9876543",
            flag_state="SG",
            size_class=VesselSizeClass.PANAMAX.value,
            deadweight_tonnage=Decimal("76000"),
            gross_tonnage=Decimal("41000"),
            net_tonnage=Decimal("26000"),
            length_overall_meters=Decimal("225"),
            beam_meters=Decimal("32.26"),
            draft_meters=Decimal("14.2"),
            hold_count=7,
            hatch_count=7,
            build_year=2020,
            owner_name="Star Shipping",
        )
        res = self.client.get(f"/api/v1/vessels/lookup/{vessel.imo_number}/")
        assert res.status_code == status.HTTP_200_OK
        assert res.data["vessel_name"] == "MV DHAMRA STAR"
        assert res.data["imo_number"] == "9876543"


class TestCharterContractAPI(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.vessel = Vessel.objects.create(
            vessel_name="MV KALINGA EXPEDITION",
            imo_number="9654321",
            flag_state="IN",
            size_class=VesselSizeClass.CAPESIZE.value,
            deadweight_tonnage=Decimal("180000"),
            gross_tonnage=Decimal("93000"),
            net_tonnage=Decimal("61000"),
            length_overall_meters=Decimal("292"),
            beam_meters=Decimal("45"),
            draft_meters=Decimal("18.2"),
            hold_count=9,
            hatch_count=9,
            build_year=2019,
            owner_name="Shipping Corp of India",
        )

    def test_create_contract_auto_derives_allowed_laytime(self):
        """
        Nominated cargo: 150,000 MT.
        Discharge rate: 25,000 MT/day.
        Allowed laytime must be auto-computed: 150,000 / 25,000 * 24 = 144.000000h.
        """
        payload = {
            "contract_reference": "SAIL/CP/2025/TEST-01",
            "charter_party_type": "VOYAGE_CHARTER",
            "contract_status": "ACTIVE",
            "charterer_entity": "Steel Authority of India Ltd (SAIL)",
            "charterer_plant": "Rourkela Steel Plant",
            "vessel": str(self.vessel.vessel_id),
            "cargo_type": "COKING_COAL",
            "nominated_quantity_mt": "150000.000",
            "tolerance_pct": "5.00",
            "freight_rate_usd_per_mt": "16.50",
            "load_port": "Hay Point",
            "discharge_port": "Paradip",
            "discharge_berth_designated": "CQ-1",
            "discharge_rate_mt_per_day": "25000.000",
            "laytime_terms": "WWDSHEX",
            "demurrage_rate_usd_per_day": "28000.00",
            "despatch_rate_usd_per_day": "14000.00",
            "laycan_start": "2025-07-01",
            "laycan_end": "2025-07-10",
            "turn_time_hours": "6.00",
            "once_on_demurrage_always_on": True,
        }
        res = self.client.post("/api/v1/contracts/", payload, format="json")
        assert res.status_code == status.HTTP_201_CREATED
        contract = CharterContract.objects.get(contract_reference="SAIL/CP/2025/TEST-01")
        assert contract.allowed_laytime_hours == Decimal("144.000000")

    def test_contract_laycan_window_exceeding_31_days_rejected(self):
        payload = {
            "contract_reference": "SAIL/CP/2025/BAD-LAYCAN",
            "charter_party_type": "VOYAGE_CHARTER",
            "contract_status": "ACTIVE",
            "charterer_entity": "SAIL",
            "charterer_plant": "Bokaro",
            "vessel": str(self.vessel.vessel_id),
            "cargo_type": "COKING_COAL",
            "nominated_quantity_mt": "75000.000",
            "freight_rate_usd_per_mt": "15.00",
            "load_port": "Hay Point",
            "discharge_port": "Dhamra",
            "discharge_rate_mt_per_day": "15000.000",
            "demurrage_rate_usd_per_day": "15000.00",
            "despatch_rate_usd_per_day": "7500.00",
            "laycan_start": "2025-07-01",
            "laycan_end": "2025-08-15",  # 45 days > 31
        }
        res = self.client.post("/api/v1/contracts/", payload, format="json")
        assert res.status_code == status.HTTP_400_BAD_REQUEST

    def test_contract_despatch_exceeding_demurrage_rejected(self):
        payload = {
            "contract_reference": "SAIL/CP/2025/BAD-RATES",
            "charter_party_type": "VOYAGE_CHARTER",
            "contract_status": "ACTIVE",
            "charterer_entity": "SAIL",
            "charterer_plant": "Bokaro",
            "vessel": str(self.vessel.vessel_id),
            "cargo_type": "COKING_COAL",
            "nominated_quantity_mt": "75000.000",
            "freight_rate_usd_per_mt": "15.00",
            "load_port": "Hay Point",
            "discharge_port": "Dhamra",
            "discharge_rate_mt_per_day": "15000.000",
            "demurrage_rate_usd_per_day": "10000.00",
            "despatch_rate_usd_per_day": "15000.00",  # > demurrage
            "laycan_start": "2025-07-01",
            "laycan_end": "2025-07-10",
        }
        res = self.client.post("/api/v1/contracts/", payload, format="json")
        assert res.status_code == status.HTTP_400_BAD_REQUEST

    def test_contract_specifications_endpoint(self):
        contract = CharterContract.objects.create(
            contract_reference="SAIL/CP/2025/SPEC-TEST",
            charter_party_type=CharterPartyType.VOYAGE_CHARTER.value,
            contract_status=ContractStatus.ACTIVE.value,
            charterer_entity="Steel Authority of India Ltd (SAIL)",
            charterer_plant="Bhilai Steel Plant",
            vessel=self.vessel,
            cargo_type=CargoType.COKING_COAL.value,
            nominated_quantity_mt=Decimal("120000.000"),
            tolerance_pct=Decimal("5.00"),
            freight_rate_usd_per_mt=Decimal("17.00"),
            load_port="Hay Point",
            discharge_port="Visakhapatnam",
            discharge_rate_mt_per_day=Decimal("20000.000"),
            laytime_terms=LaytimeTerms.WWDSHEX.value,
            demurrage_rate_usd_per_day=Decimal("24000.00"),
            despatch_rate_usd_per_day=Decimal("12000.00"),
            laycan_start=date(2025, 8, 1),
            laycan_end=date(2025, 8, 8),
            turn_time_hours=Decimal("6.00"),
        )

        res = self.client.get(f"/api/v1/contracts/{contract.contract_id}/specifications/")
        assert res.status_code == status.HTTP_200_OK
        data = res.data
        assert data["contract_reference"] == "SAIL/CP/2025/SPEC-TEST"
        assert data["vessel_name"] == "MV KALINGA EXPEDITION"
        assert data["vessel_imo"] == "9654321"
        assert Decimal(data["cargo_min_mt"]) == Decimal("114000.000")  # 120k - 5%
        assert Decimal(data["cargo_max_mt"]) == Decimal("126000.000")  # 120k + 5%
        assert data["allowed_laytime_hours"] == "144.000000"  # (120k/20k)*24 = 6 days = 144h
        assert data["allowed_laytime_days"] == "6.000000"
        assert data["hourly_demurrage_rate_usd"] == "1000.00"  # 24k / 24
        assert data["hourly_despatch_rate_usd"] == "500.00"    # 12k / 24
        assert data["laycan_duration_days"] == 8

    def test_seed_baltic_fixtures_endpoint(self):
        res = self.client.post("/api/v1/contracts/seed-baltic-fixtures/")
        assert res.status_code == status.HTTP_201_CREATED
        assert res.data["total_scenarios"] == 3
        assert CharterContract.objects.count() >= 3
        assert Vessel.objects.filter(imo_number="9456789").exists()
        assert Vessel.objects.filter(imo_number="9321456").exists()
        assert Vessel.objects.filter(imo_number="9543210").exists()
