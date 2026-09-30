#!/usr/bin/env python
"""
Enterprise Overseas Bulk Vessel Chartering & Logistics Management Dashboard
=============================================================================
Authoritative Demonstration Data Seeder (SIH26006 / SAIL Ministry of Steel)
Idempotent script populating realistic maritime operational entities:
- 5 Commercial Vessels (Capesize, Panamax, Supramax)
- 5 Charter Contracts (CoAs and Spot Fixtures with SAIL plants)
- 5 Active Voyages with real-time geospatial coordinates in the Indian Ocean
- 5 Plant Inventory Baselines (BSP, DSP, RSP, BSL, ISP) with burn rates
- Authoritative Statement of Facts (SOF) & Laytime Calculation records
=============================================================================
"""

import os
import sys
from pathlib import Path
from decimal import Decimal
from datetime import timedelta

# Ensure backend root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = BASE_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from django.utils import timezone
from apps.contracts.models import Vessel, CharterContract
from apps.laytime.engine.constants import LaytimeTerms
from apps.laytime.engine.fixture_factory import (
    VesselSizeClass,
    CargoType,
    CharterPartyType,
    ContractStatus,
    VoyageStatus,
)
from apps.voyages.models import VesselVoyage, StatementOfFacts, LaytimeCalculation
from apps.voyages.sof_enums import SOFEventType, IntervalCountStatus
from apps.laytime.engine.settlement_engine import LaytimeOutcome
from apps.inventory.models import PlantInventory, RawMaterialCategory, StockSafetyStatus

GREEN = "\033[92m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def seed_all():
    print(f"\n{BOLD}{CYAN}==============================================================================={RESET}")
    print(f"{BOLD}{CYAN}   Seeding Operational Demonstration Data for SAIL Bulk Dashboard (SIH26006)  {RESET}")
    print(f"{BOLD}{CYAN}==============================================================================={RESET}\n")

    now = timezone.now()
    today = now.date()

    # 1. Vessels
    print(f"{BOLD}1. Seeding Commercial Bulk Carrier Vessels...{RESET}")
    vessels_data = [
        {
            "vessel_name": "MV CHANDRAGUPTA",
            "imo_number": "9452310",
            "flag_state": "IN",
            "size_class": VesselSizeClass.CAPESIZE.value,
            "deadweight_tonnage": Decimal("180000.000"),
            "gross_tonnage": Decimal("95000.000"),
            "net_tonnage": Decimal("62000.000"),
            "length_overall_meters": Decimal("292.00"),
            "beam_meters": Decimal("45.00"),
            "draft_meters": Decimal("18.20"),
            "hold_count": 9,
            "hatch_count": 9,
            "build_year": 2018,
            "owner_name": "Shipping Corporation of India",
            "service_speed_knots": Decimal("14.0"),
        },
        {
            "vessel_name": "MV BENGAL GLORY",
            "imo_number": "9623456",
            "flag_state": "PA",
            "size_class": VesselSizeClass.PANAMAX.value,
            "deadweight_tonnage": Decimal("82000.000"),
            "gross_tonnage": Decimal("44000.000"),
            "net_tonnage": Decimal("27000.000"),
            "length_overall_meters": Decimal("229.00"),
            "beam_meters": Decimal("32.26"),
            "draft_meters": Decimal("14.50"),
            "hold_count": 7,
            "hatch_count": 7,
            "build_year": 2020,
            "owner_name": "Oldendorff Carriers",
            "service_speed_knots": Decimal("13.5"),
        },
        {
            "vessel_name": "MV KALINGA PIONEER",
            "imo_number": "9734567",
            "flag_state": "LR",
            "size_class": VesselSizeClass.PANAMAX.value,
            "deadweight_tonnage": Decimal("76000.000"),
            "gross_tonnage": Decimal("41000.000"),
            "net_tonnage": Decimal("25000.000"),
            "length_overall_meters": Decimal("225.00"),
            "beam_meters": Decimal("32.20"),
            "draft_meters": Decimal("14.20"),
            "hold_count": 7,
            "hatch_count": 7,
            "build_year": 2019,
            "owner_name": "Star Bulk Carriers",
            "service_speed_knots": Decimal("14.2"),
        },
        {
            "vessel_name": "MV DECCAN TRADER",
            "imo_number": "9845678",
            "flag_state": "SG",
            "size_class": VesselSizeClass.SUPRAMAX.value,
            "deadweight_tonnage": Decimal("58000.000"),
            "gross_tonnage": Decimal("32000.000"),
            "net_tonnage": Decimal("19000.000"),
            "length_overall_meters": Decimal("190.00"),
            "beam_meters": Decimal("32.26"),
            "draft_meters": Decimal("12.80"),
            "hold_count": 5,
            "hatch_count": 5,
            "build_year": 2021,
            "owner_name": "Pacific Basin Shipping",
            "service_speed_knots": Decimal("14.5"),
        },
        {
            "vessel_name": "MV STEEL ENTERPRISE",
            "imo_number": "9956789",
            "flag_state": "IN",
            "size_class": VesselSizeClass.PANAMAX.value,
            "deadweight_tonnage": Decimal("81500.000"),
            "gross_tonnage": Decimal("43500.000"),
            "net_tonnage": Decimal("26800.000"),
            "length_overall_meters": Decimal("229.00"),
            "beam_meters": Decimal("32.26"),
            "draft_meters": Decimal("14.45"),
            "hold_count": 7,
            "hatch_count": 7,
            "build_year": 2022,
            "owner_name": "Great Eastern Shipping",
            "service_speed_knots": Decimal("14.0"),
        },
    ]

    vessels_map = {}
    for v_data in vessels_data:
        vessel, created = Vessel.objects.update_or_create(
            imo_number=v_data["imo_number"],
            defaults=v_data,
        )
        vessels_map[vessel.vessel_name] = vessel
        action = "Created" if created else "Updated"
        print(f"  {GREEN}[{action}]{RESET} {vessel.vessel_name} ({vessel.size_class}, IMO {vessel.imo_number})")

    # 2. Charter Contracts
    print(f"\n{BOLD}2. Seeding Charter Contracts & Fixture Parameters...{RESET}")
    contracts_data = [
        {
            "contract_reference": "SAIL/COA/2026/01",
            "charter_party_type": CharterPartyType.CONTRACT_OF_AFFREIGHTMENT.value,
            "contract_status": ContractStatus.ACTIVE.value,
            "charterer_entity": "Steel Authority of India Limited",
            "charterer_plant": "BSP",
            "vessel": vessels_map["MV CHANDRAGUPTA"],
            "cargo_type": CargoType.COKING_COAL.value,
            "nominated_quantity_mt": Decimal("165000.000"),
            "tolerance_pct": Decimal("5.00"),
            "freight_rate_usd_per_mt": Decimal("18.50"),
            "load_port": "Gladstone",
            "discharge_port": "Paradip",
            "discharge_rate_mt_per_day": Decimal("20000.000"),
            "demurrage_rate_usd_per_day": Decimal("24000.00"),
            "despatch_rate_usd_per_day": Decimal("12000.00"),
            "laytime_terms": LaytimeTerms.SHINC.value,
            "laycan_start": today - timedelta(days=15),
            "laycan_end": today - timedelta(days=10),
        },
        {
            "contract_reference": "SAIL/SPOT/2026/08",
            "charter_party_type": CharterPartyType.VOYAGE_CHARTER.value,
            "contract_status": ContractStatus.ACTIVE.value,
            "charterer_entity": "Steel Authority of India Limited",
            "charterer_plant": "DSP",
            "vessel": vessels_map["MV BENGAL GLORY"],
            "cargo_type": CargoType.COKING_COAL.value,
            "nominated_quantity_mt": Decimal("75000.000"),
            "tolerance_pct": Decimal("5.00"),
            "freight_rate_usd_per_mt": Decimal("23.40"),
            "load_port": "Hay Point",
            "discharge_port": "Haldia",
            "discharge_rate_mt_per_day": Decimal("15000.000"),
            "demurrage_rate_usd_per_day": Decimal("18500.00"),
            "despatch_rate_usd_per_day": Decimal("9250.00"),
            "laytime_terms": LaytimeTerms.WWDSHEX.value,
            "laycan_start": today - timedelta(days=12),
            "laycan_end": today - timedelta(days=8),
        },
        {
            "contract_reference": "SAIL/SPOT/2026/09",
            "charter_party_type": CharterPartyType.VOYAGE_CHARTER.value,
            "contract_status": ContractStatus.ACTIVE.value,
            "charterer_entity": "Steel Authority of India Limited",
            "charterer_plant": "RSP",
            "vessel": vessels_map["MV KALINGA PIONEER"],
            "cargo_type": CargoType.COKING_COAL.value,
            "nominated_quantity_mt": Decimal("72000.000"),
            "tolerance_pct": Decimal("5.00"),
            "freight_rate_usd_per_mt": Decimal("22.80"),
            "load_port": "Newcastle",
            "discharge_port": "Visakhapatnam",
            "discharge_rate_mt_per_day": Decimal("14000.000"),
            "demurrage_rate_usd_per_day": Decimal("17500.00"),
            "despatch_rate_usd_per_day": Decimal("8750.00"),
            "laytime_terms": LaytimeTerms.SHINC.value,
            "laycan_start": today - timedelta(days=20),
            "laycan_end": today - timedelta(days=16),
        },
        {
            "contract_reference": "SAIL/COA/2026/02",
            "charter_party_type": CharterPartyType.CONTRACT_OF_AFFREIGHTMENT.value,
            "contract_status": ContractStatus.ACTIVE.value,
            "charterer_entity": "Steel Authority of India Limited",
            "charterer_plant": "BSL",
            "vessel": vessels_map["MV DECCAN TRADER"],
            "cargo_type": CargoType.PULVERIZED_COAL_INJECTION.value,
            "nominated_quantity_mt": Decimal("55000.000"),
            "tolerance_pct": Decimal("5.00"),
            "freight_rate_usd_per_mt": Decimal("21.50"),
            "load_port": "Abbot Point",
            "discharge_port": "Dhamra",
            "discharge_rate_mt_per_day": Decimal("12500.000"),
            "demurrage_rate_usd_per_day": Decimal("16000.00"),
            "despatch_rate_usd_per_day": Decimal("8000.00"),
            "laytime_terms": LaytimeTerms.SHINC.value,
            "laycan_start": today - timedelta(days=10),
            "laycan_end": today - timedelta(days=5),
        },
        {
            "contract_reference": "SAIL/SPOT/2026/10",
            "charter_party_type": CharterPartyType.VOYAGE_CHARTER.value,
            "contract_status": ContractStatus.ACTIVE.value,
            "charterer_entity": "Steel Authority of India Limited",
            "charterer_plant": "ISP",
            "vessel": vessels_map["MV STEEL ENTERPRISE"],
            "cargo_type": CargoType.COKING_COAL.value,
            "nominated_quantity_mt": Decimal("78000.000"),
            "tolerance_pct": Decimal("5.00"),
            "freight_rate_usd_per_mt": Decimal("24.10"),
            "load_port": "Richards Bay",
            "discharge_port": "Haldia",
            "discharge_rate_mt_per_day": Decimal("16000.000"),
            "demurrage_rate_usd_per_day": Decimal("19000.00"),
            "despatch_rate_usd_per_day": Decimal("9500.00"),
            "laytime_terms": LaytimeTerms.SHINC.value,
            "laycan_start": today - timedelta(days=5),
            "laycan_end": today,
        },
    ]

    contracts_map = {}
    for c_data in contracts_data:
        contract, created = CharterContract.objects.update_or_create(
            contract_reference=c_data["contract_reference"],
            defaults=c_data,
        )
        contracts_map[contract.contract_reference] = contract
        action = "Created" if created else "Updated"
        print(f"  {GREEN}[{action}]{RESET} Contract {contract.contract_reference} -> {contract.charterer_plant} ({contract.cargo_type})")

    # 3. Vessel Voyages
    print(f"\n{BOLD}3. Seeding Live Voyages with Telemetry & Port Timelines...{RESET}")
    voyages_data = [
        {
            "voyage_reference": "VOY-2026-001",
            "contract": contracts_map["SAIL/COA/2026/01"],
            "vessel": vessels_map["MV CHANDRAGUPTA"],
            "voyage_status": VoyageStatus.IN_TRANSIT.value,
            "load_port": "Gladstone",
            "load_country": "AU",
            "quantity_loaded_mt": Decimal("165000.000"),
            "discharge_port": "Paradip",
            "discharge_country": "IN",
            "discharge_berth": "CB-1",
            "etl_date": today - timedelta(days=12),
            "atl_date": now - timedelta(days=12),
            "eta_date": now + timedelta(days=4),
            "last_position_lat": Decimal("8.500000"),
            "last_position_lon": Decimal("85.200000"),
            "last_position_updated": now - timedelta(minutes=15),
            "speed_over_ground": Decimal("13.5"),
            "bl_number": "BL-GLAD-2026-001",
            "bl_date": today - timedelta(days=12),
            "bl_quantity_mt": Decimal("165000.000"),
        },
        {
            "voyage_reference": "VOY-2026-002",
            "contract": contracts_map["SAIL/SPOT/2026/08"],
            "vessel": vessels_map["MV BENGAL GLORY"],
            "voyage_status": VoyageStatus.NOR_TENDERED.value,
            "load_port": "Hay Point",
            "load_country": "AU",
            "quantity_loaded_mt": Decimal("75000.000"),
            "discharge_port": "Haldia",
            "discharge_country": "IN",
            "discharge_berth": "Berth 4B",
            "etl_date": today - timedelta(days=15),
            "atl_date": now - timedelta(days=15),
            "eta_date": now - timedelta(hours=8),
            "ata_date": now - timedelta(hours=8),
            "nor_tendered_at": now - timedelta(hours=6),
            "last_position_lat": Decimal("19.200000"),
            "last_position_lon": Decimal("88.100000"),
            "last_position_updated": now - timedelta(minutes=10),
            "speed_over_ground": Decimal("0.0"),
            "bl_number": "BL-HAYP-2026-088",
            "bl_date": today - timedelta(days=15),
            "bl_quantity_mt": Decimal("75000.000"),
        },
        {
            "voyage_reference": "VOY-2026-003",
            "contract": contracts_map["SAIL/SPOT/2026/09"],
            "vessel": vessels_map["MV KALINGA PIONEER"],
            "voyage_status": VoyageStatus.DISCHARGING.value,
            "load_port": "Newcastle",
            "load_country": "AU",
            "quantity_loaded_mt": Decimal("72000.000"),
            "quantity_discharged_mt": Decimal("48000.000"),
            "discharge_port": "Visakhapatnam",
            "discharge_country": "IN",
            "discharge_berth": "EQ-1",
            "etl_date": today - timedelta(days=22),
            "atl_date": now - timedelta(days=22),
            "eta_date": now - timedelta(days=3),
            "ata_date": now - timedelta(days=3),
            "nor_tendered_at": now - timedelta(days=3),
            "nor_accepted_at": now - timedelta(days=2, hours=18),
            "berthed_at": now - timedelta(days=2, hours=12),
            "discharge_commenced_at": now - timedelta(days=2, hours=8),
            "last_position_lat": Decimal("17.680000"),
            "last_position_lon": Decimal("83.290000"),
            "last_position_updated": now - timedelta(minutes=5),
            "speed_over_ground": Decimal("0.0"),
            "bl_number": "BL-NEWC-2026-104",
            "bl_date": today - timedelta(days=22),
            "bl_quantity_mt": Decimal("72000.000"),
        },
        {
            "voyage_reference": "VOY-2026-004",
            "contract": contracts_map["SAIL/COA/2026/02"],
            "vessel": vessels_map["MV DECCAN TRADER"],
            "voyage_status": VoyageStatus.ARRIVED.value,
            "load_port": "Abbot Point",
            "load_country": "AU",
            "quantity_loaded_mt": Decimal("55000.000"),
            "discharge_port": "Dhamra",
            "discharge_country": "IN",
            "discharge_berth": "Berth 2",
            "etl_date": today - timedelta(days=14),
            "atl_date": now - timedelta(days=14),
            "eta_date": now - timedelta(hours=18),
            "ata_date": now - timedelta(hours=18),
            "last_position_lat": Decimal("20.800000"),
            "last_position_lon": Decimal("87.050000"),
            "last_position_updated": now - timedelta(minutes=20),
            "speed_over_ground": Decimal("0.2"),
            "bl_number": "BL-ABBT-2026-052",
            "bl_date": today - timedelta(days=14),
            "bl_quantity_mt": Decimal("55000.000"),
        },
        {
            "voyage_reference": "VOY-2026-005",
            "contract": contracts_map["SAIL/SPOT/2026/10"],
            "vessel": vessels_map["MV STEEL ENTERPRISE"],
            "voyage_status": VoyageStatus.IN_TRANSIT.value,
            "load_port": "Richards Bay",
            "load_country": "ZA",
            "quantity_loaded_mt": Decimal("78000.000"),
            "discharge_port": "Haldia",
            "discharge_country": "IN",
            "discharge_berth": "Berth 4A",
            "etl_date": today - timedelta(days=6),
            "atl_date": now - timedelta(days=6),
            "eta_date": now + timedelta(days=7),
            "last_position_lat": Decimal("3.200000"),
            "last_position_lon": Decimal("78.400000"),
            "last_position_updated": now - timedelta(minutes=30),
            "speed_over_ground": Decimal("14.1"),
            "bl_number": "BL-RBAY-2026-319",
            "bl_date": today - timedelta(days=6),
            "bl_quantity_mt": Decimal("78000.000"),
        },
    ]

    voyages_map = {}
    for v_data in voyages_data:
        voyage, created = VesselVoyage.objects.update_or_create(
            voyage_reference=v_data["voyage_reference"],
            defaults=v_data,
        )
        voyages_map[voyage.voyage_reference] = voyage
        action = "Created" if created else "Updated"
        print(f"  {GREEN}[{action}]{RESET} Voyage {voyage.voyage_reference} -> {voyage.discharge_port} ({voyage.voyage_status})")

    # 4. Plant Inventory Records
    print(f"\n{BOLD}4. Seeding SAIL Plant Stockyard Inventory Baselines...{RESET}")
    plants_data = [
        {
            "plant_code": "BSP",
            "plant_name": "Bhilai Steel Plant",
            "plant_location": "Bhilai, Chhattisgarh",
            "opening_stock_mt": Decimal("88000.000"),
            "receipts_mt": Decimal("2200.000"),
            "consumption_mt": Decimal("5200.000"),
            "closing_stock_mt": Decimal("85000.000"),
            "daily_consumption_rate_mt": Decimal("5200.000"),
            "projected_receipts_mt": Decimal("165000.000"),
            "stock_runway_days": Decimal("16.35"),
            "safety_status": StockSafetyStatus.WATCH.value,
        },
        {
            "plant_code": "DSP",
            "plant_name": "Durgapur Steel Plant",
            "plant_location": "Durgapur, West Bengal",
            "opening_stock_mt": Decimal("32100.000"),
            "receipts_mt": Decimal("0.000"),
            "consumption_mt": Decimal("4100.000"),
            "closing_stock_mt": Decimal("28000.000"),
            "daily_consumption_rate_mt": Decimal("4100.000"),
            "projected_receipts_mt": Decimal("75000.000"),
            "stock_runway_days": Decimal("6.83"),
            "safety_status": StockSafetyStatus.CRITICAL.value,
        },
        {
            "plant_code": "RSP",
            "plant_name": "Rourkela Steel Plant",
            "plant_location": "Rourkela, Odisha",
            "opening_stock_mt": Decimal("114800.000"),
            "receipts_mt": Decimal("0.000"),
            "consumption_mt": Decimal("4800.000"),
            "closing_stock_mt": Decimal("110000.000"),
            "daily_consumption_rate_mt": Decimal("4800.000"),
            "projected_receipts_mt": Decimal("72000.000"),
            "stock_runway_days": Decimal("22.92"),
            "safety_status": StockSafetyStatus.ADEQUATE.value,
        },
        {
            "plant_code": "BSL",
            "plant_name": "Bokaro Steel Plant",
            "plant_location": "Bokaro, Jharkhand",
            "opening_stock_mt": Decimal("67500.000"),
            "receipts_mt": Decimal("0.000"),
            "consumption_mt": Decimal("5500.000"),
            "closing_stock_mt": Decimal("62000.000"),
            "daily_consumption_rate_mt": Decimal("5500.000"),
            "projected_receipts_mt": Decimal("55000.000"),
            "stock_runway_days": Decimal("11.27"),
            "safety_status": StockSafetyStatus.PRECAUTIONARY.value,
        },
        {
            "plant_code": "ISP",
            "plant_name": "IISCO Steel Plant",
            "plant_location": "Burnpur, West Bengal",
            "opening_stock_mt": Decimal("48200.000"),
            "receipts_mt": Decimal("0.000"),
            "consumption_mt": Decimal("3200.000"),
            "closing_stock_mt": Decimal("45000.000"),
            "daily_consumption_rate_mt": Decimal("3200.000"),
            "projected_receipts_mt": Decimal("78000.000"),
            "stock_runway_days": Decimal("14.06"),
            "safety_status": StockSafetyStatus.WATCH.value,
        },
    ]

    for p_data in plants_data:
        p_obj, created = PlantInventory.objects.update_or_create(
            plant_code=p_data["plant_code"],
            stock_date=today,
            material_category=RawMaterialCategory.COKING_COAL.value,
            defaults=p_data,
        )
        action = "Created" if created else "Updated"
        print(f"  {GREEN}[{action}]{RESET} Plant {p_obj.plant_code}: {p_obj.closing_stock_mt} MT ({p_obj.stock_runway_days}d -> {p_obj.safety_status})")

    # 5. Statement of Facts (SOF) & Laytime Calculation for VOY-2026-003
    print(f"\n{BOLD}5. Seeding Authoritative SOF Events & Laytime Settlement...{RESET}")
    voy_3 = voyages_map["VOY-2026-003"]
    StatementOfFacts.objects.filter(voyage=voy_3).delete()

    sof_events = [
        (SOFEventType.NOR_TENDERED.value, now - timedelta(days=3), "Notice of Readiness tendered via email/VHF to port control"),
        (SOFEventType.NOR_ACCEPTED.value, now - timedelta(days=2, hours=18), "NOR accepted by Charterer Port Agent"),
        (SOFEventType.PILOT_BOARDED.value, now - timedelta(days=2, hours=14), "Inward pilot boarded vessel at outer anchorage"),
        (SOFEventType.ALL_FAST.value, now - timedelta(days=2, hours=12), "All fast alongside Vizag Port Berth EQ-1"),
        (SOFEventType.DISCHARGE_COMMENCED.value, now - timedelta(days=2, hours=8), "Discharge commenced using shore gantry cranes #1 & #2"),
        (SOFEventType.RAIN_COMMENCED.value, now - timedelta(days=1, hours=14), "Heavy tropical rain squall; hatches closed by master"),
        (SOFEventType.RAIN_CEASED.value, now - timedelta(days=1, hours=10), "Rain ceased; hatches reopened; discharging resumed"),
    ]

    for idx, (etype, ts, desc) in enumerate(sof_events, start=1):
        StatementOfFacts.objects.create(
            voyage=voy_3,
            sequence_number=idx,
            event_type=etype,
            event_timestamp=ts,
            event_description=desc,
            recorded_by_agent="Vizag Maritime Agencies Pvt Ltd",
        )

    # Allowed Laytime = (72,000 MT / 14,000 MT/day) * 24 = 123.428571 hours
    # Counted = 46.00 hours. Status: PENDING / DESPATCH
    allowed_hrs = Decimal("123.428571")
    counted_hrs = Decimal("46.000000")
    excepted_hrs = Decimal("4.000000")   # Rain interruption
    variance_hrs = allowed_hrs - counted_hrs

    LaytimeCalculation.objects.filter(voyage=voy_3).delete()
    lay_calc = LaytimeCalculation.objects.create(
        voyage=voy_3,
        revision=1,
        is_current=True,
        outcome=LaytimeOutcome.DESPATCH.value,
        calculated_at=now,
        allowed_laytime_hours=allowed_hrs,
        total_port_hours=Decimal("56.000000"),
        total_counted_hours=counted_hrs,
        total_excepted_hours=excepted_hrs,
        total_disputed_hours=Decimal("0.000000"),
        variance_hours=variance_hrs,
        demurrage_rate_usd_per_day=Decimal("17500.00"),
        despatch_rate_usd_per_day=Decimal("8750.00"),
        gross_demurrage_usd=Decimal("0.00"),
        gross_despatch_usd=((Decimal("8750.00") / Decimal("24")) * variance_hrs).quantize(Decimal("0.0001")),
        net_settlement_usd=(-((Decimal("8750.00") / Decimal("24")) * variance_hrs)).quantize(Decimal("0.0001")),
        sof_event_count=len(sof_events),
        first_sof_event_at=sof_events[0][1],
        last_sof_event_at=sof_events[-1][1],
    )
    print(f"  {GREEN}[Created]{RESET} LaytimeCalculation #{lay_calc.revision} for {voy_3.voyage_reference}: Outcome = {lay_calc.outcome}")

    print(f"\n{BOLD}{CYAN}==============================================================================={RESET}")
    print(f"{GREEN}{BOLD}[SUCCESS] Operational demo database seeded successfully! Ready for evaluation.{RESET}\n")


if __name__ == "__main__":
    seed_all()
