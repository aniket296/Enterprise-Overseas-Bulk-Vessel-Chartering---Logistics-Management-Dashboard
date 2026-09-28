"""
backend/apps/laytime/engine/fixture_factory.py
============================================================
Cycle 1.3.1 — Maritime Charter-Party Fixture Factory

Generates authentic, standard Baltic Exchange fixture datasets
and complete maritime test scenarios tailored for Ministry of Steel
bulk vessel chartering operations (e.g. Australia/South Africa/
Indonesia to Indian ports Dhamra, Paradip, Visakhapatnam, Haldia).

Deterministic Guarantees:
  - Validates all PostgreSQL schema check constraints (IMO 7-digit,
    flag states, positive tonnages, laycan window <= 31 days,
    despatch <= demurrage, etc.).
  - Fixed-point Decimal precision for financial rates and tonnages.
  - Pure deterministic output with zero random or synthetic drift.
  - Ready for unit testing, seed pipelines, and API ingestion.
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional, Dict, Any, List

from .constants import LaytimeTerms, HOURS_PER_DAY

_6DP = Decimal("0.000001")
_2DP = Decimal("0.01")
_3DP = Decimal("0.001")


# ---------------------------------------------------------------------------
# Domain Enums (Matching PostgreSQL V001 types)
# ---------------------------------------------------------------------------
class VesselSizeClass(str, Enum):
    HANDYSIZE     = "HANDYSIZE"
    SUPRAMAX      = "SUPRAMAX"
    ULTRAMAX      = "ULTRAMAX"
    PANAMAX       = "PANAMAX"
    KAMSARMAX     = "KAMSARMAX"
    POST_PANAMAX  = "POST_PANAMAX"
    CAPESIZE      = "CAPESIZE"
    NEWCASTLEMAX  = "NEWCASTLEMAX"
    VLOC          = "VLOC"


class CargoType(str, Enum):
    COKING_COAL                 = "COKING_COAL"
    MET_COKE                    = "MET_COKE"
    IRON_ORE_FINES              = "IRON_ORE_FINES"
    IRON_ORE_LUMPS              = "IRON_ORE_LUMPS"
    IRON_ORE_PELLETS            = "IRON_ORE_PELLETS"
    THERMAL_COAL                = "THERMAL_COAL"
    PULVERIZED_COAL_INJECTION   = "PULVERIZED_COAL_INJECTION"
    LIMESTONE                   = "LIMESTONE"
    DOLOMITE                    = "DOLOMITE"
    MANGANESE_ORE               = "MANGANESE_ORE"


class CharterPartyType(str, Enum):
    VOYAGE_CHARTER              = "VOYAGE_CHARTER"
    TIME_CHARTER                = "TIME_CHARTER"
    CONTRACT_OF_AFFREIGHTMENT   = "CONTRACT_OF_AFFREIGHTMENT"
    BAREBOAT_CHARTER            = "BAREBOAT_CHARTER"


class ContractStatus(str, Enum):
    DRAFT             = "DRAFT"
    PENDING_APPROVAL  = "PENDING_APPROVAL"
    ACTIVE            = "ACTIVE"
    EXECUTED          = "EXECUTED"
    AMENDED           = "AMENDED"
    COMPLETED         = "COMPLETED"
    DISPUTED          = "DISPUTED"
    CANCELLED         = "CANCELLED"


class VoyageStatus(str, Enum):
    SCHEDULED     = "SCHEDULED"
    IN_TRANSIT    = "IN_TRANSIT"
    ARRIVED       = "ARRIVED"
    NOR_TENDERED  = "NOR_TENDERED"
    BERTHED       = "BERTHED"
    COMMENCING    = "COMMENCING"
    DISCHARGING   = "DISCHARGING"
    COMPLETED     = "COMPLETED"
    DEVIATED      = "DEVIATED"
    CANCELLED     = "CANCELLED"


# ---------------------------------------------------------------------------
# Fixture Data Models
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VesselFixture:
    """Vessel registry fixture satisfying V003 check constraints."""
    vessel_name: str
    imo_number: str
    flag_state: str
    size_class: VesselSizeClass
    deadweight_tonnage: Decimal
    gross_tonnage: Decimal
    net_tonnage: Decimal
    length_overall_meters: Decimal
    beam_meters: Decimal
    draft_meters: Decimal
    hold_count: int
    hatch_count: int
    build_year: int
    owner_name: str
    service_speed_knots: Decimal = Decimal("14.0")
    is_active: bool = True

    def __post_init__(self) -> None:
        if len(self.imo_number) != 7 or not self.imo_number.isdigit():
            raise ValueError(f"Invalid IMO number {self.imo_number}: must be exactly 7 digits.")
        if len(self.flag_state) != 2 or not self.flag_state.isupper():
            raise ValueError(f"Invalid flag_state {self.flag_state}: must be 2 uppercase characters.")
        if self.deadweight_tonnage <= Decimal("0"):
            raise ValueError("deadweight_tonnage must be strictly positive.")
        if self.hatch_count > self.hold_count:
            raise ValueError("hatch_count cannot exceed hold_count.")


@dataclass(frozen=True)
class CharterContractFixture:
    """Charter party contract fixture satisfying V003 check constraints."""
    contract_reference: str
    charter_party_type: CharterPartyType
    contract_status: ContractStatus
    charterer_entity: str
    charterer_plant: str
    cargo_type: CargoType
    nominated_quantity_mt: Decimal
    tolerance_pct: Decimal
    freight_rate_usd_per_mt: Decimal
    discharge_port: str
    discharge_rate_mt_per_day: Decimal
    laytime_terms: LaytimeTerms
    demurrage_rate_usd_per_day: Decimal
    despatch_rate_usd_per_day: Decimal
    laycan_start: date
    laycan_end: date
    turn_time_hours: Decimal = Decimal("6.00")
    once_on_demurrage_always_on: bool = True
    discharge_berth_designated: Optional[str] = None

    def __post_init__(self) -> None:
        if self.laycan_start > self.laycan_end:
            raise ValueError("laycan_start cannot be after laycan_end.")
        if (self.laycan_end - self.laycan_start).days > 31:
            raise ValueError("laycan window duration cannot exceed 31 days.")
        if self.despatch_rate_usd_per_day > self.demurrage_rate_usd_per_day:
            raise ValueError("despatch_rate cannot exceed demurrage_rate.")
        if self.nominated_quantity_mt <= Decimal("0"):
            raise ValueError("nominated_quantity_mt must be strictly positive.")


@dataclass(frozen=True)
class VoyageFixture:
    """Vessel voyage leg fixture satisfying chronological and status constraints."""
    voyage_reference: str
    voyage_status: VoyageStatus
    load_port: str
    discharge_port: str
    discharge_berth: Optional[str]
    eta_date: datetime
    quantity_loaded_mt: Decimal
    quantity_discharged_mt: Optional[Decimal] = None
    ata_date: Optional[datetime] = None
    nor_tendered_at: Optional[datetime] = None
    nor_accepted_at: Optional[datetime] = None
    berthed_at: Optional[datetime] = None
    discharge_commenced_at: Optional[datetime] = None
    discharge_completed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.quantity_loaded_mt <= Decimal("0"):
            raise ValueError("quantity_loaded_mt must be positive.")


@dataclass(frozen=True)
class CompleteMaritimeFixture:
    """Unified scenario encompassing vessel, contract, voyage, and expected laytime metrics."""
    scenario_id: str
    description: str
    vessel: VesselFixture
    contract: CharterContractFixture
    voyage: VoyageFixture
    expected_allowed_laytime_hours: Decimal

    def to_dict(self) -> Dict[str, Any]:
        """Serialize full fixture to nested dictionary."""
        return {
            "scenario_id": self.scenario_id,
            "description": self.description,
            "vessel": asdict(self.vessel),
            "contract": asdict(self.contract),
            "voyage": asdict(self.voyage),
            "expected_allowed_laytime_hours": str(self.expected_allowed_laytime_hours),
        }


# ---------------------------------------------------------------------------
# Baltic Exchange Standard Scenario Presets
# ---------------------------------------------------------------------------
class MaritimeFixtureFactory:
    """
    Produces standardized Baltic Exchange fixture scenarios for dry bulk trades.
    """

    @staticmethod
    def capesize_australia_to_india_coal() -> CompleteMaritimeFixture:
        """
        Scenario 1: Capesize Metallurgical Coal from Hay Point, Australia
        to Paradip Port, India for Steel Authority of India Ltd (Rourkela Plant).
        Route: Hay Point -> Paradip Port (Berth: CQ-1).
        Cargo: 150,000 MT Coking Coal @ 25,000 MT/day WWDSHEX.
        Allowed laytime: 150,000 / 25,000 * 24 = 144.000000 hours (6.0 days).
        """
        vessel = VesselFixture(
            vessel_name="MV MAHANADI ORE",
            imo_number="9456789",
            flag_state="IN",
            size_class=VesselSizeClass.CAPESIZE,
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
            service_speed_knots=Decimal("14.5"),
        )

        laycan_start = date(2025, 7, 1)
        laycan_end = date(2025, 7, 10)

        contract = CharterContractFixture(
            contract_reference="SAIL/CP/2025/CAPE-01",
            charter_party_type=CharterPartyType.VOYAGE_CHARTER,
            contract_status=ContractStatus.EXECUTED,
            charterer_entity="Steel Authority of India Ltd (SAIL)",
            charterer_plant="Rourkela Steel Plant",
            cargo_type=CargoType.COKING_COAL,
            nominated_quantity_mt=Decimal("150000.000"),
            tolerance_pct=Decimal("5.00"),
            freight_rate_usd_per_mt=Decimal("16.50"),
            discharge_port="Paradip",
            discharge_berth_designated="CQ-1",
            discharge_rate_mt_per_day=Decimal("25000.000"),
            laytime_terms=LaytimeTerms.WWDSHEX,
            demurrage_rate_usd_per_day=Decimal("28000.00"),
            despatch_rate_usd_per_day=Decimal("14000.00"),
            laycan_start=laycan_start,
            laycan_end=laycan_end,
            turn_time_hours=Decimal("6.00"),
            once_on_demurrage_always_on=True,
        )

        eta = datetime(2025, 7, 5, 6, 0, tzinfo=timezone.utc)
        ata = datetime(2025, 7, 5, 8, 30, tzinfo=timezone.utc)
        nor = datetime(2025, 7, 5, 9, 0, tzinfo=timezone.utc)
        nor_acc = datetime(2025, 7, 5, 15, 0, tzinfo=timezone.utc) # 6h turn-time
        berthed = datetime(2025, 7, 6, 4, 0, tzinfo=timezone.utc)
        dis_start = datetime(2025, 7, 6, 7, 0, tzinfo=timezone.utc)
        dis_end = datetime(2025, 7, 11, 20, 0, tzinfo=timezone.utc)
        comp = datetime(2025, 7, 12, 10, 0, tzinfo=timezone.utc)

        voyage = VoyageFixture(
            voyage_reference="VOY-SAIL-2025-01",
            voyage_status=VoyageStatus.COMPLETED,
            load_port="Hay Point",
            discharge_port="Paradip",
            discharge_berth="CQ-1",
            eta_date=eta,
            ata_date=ata,
            nor_tendered_at=nor,
            nor_accepted_at=nor_acc,
            berthed_at=berthed,
            discharge_commenced_at=dis_start,
            discharge_completed_at=dis_end,
            completed_at=comp,
            quantity_loaded_mt=Decimal("151250.000"),
            quantity_discharged_mt=Decimal("151180.000"),
        )

        return CompleteMaritimeFixture(
            scenario_id="BALTIC_CAPE_AUS_IND_COAL_01",
            description="Capesize 150k MT Met Coal Hay Point to Paradip CQ-1 (SAIL Rourkela)",
            vessel=vessel,
            contract=contract,
            voyage=voyage,
            expected_allowed_laytime_hours=Decimal("144.000000"),
        )

    @staticmethod
    def panamax_indonesia_to_india_thermal() -> CompleteMaritimeFixture:
        """
        Scenario 2: Panamax Thermal Coal from Tanjung Bara, Indonesia
        to Dhamra Port, India for Steel Authority of India Ltd (Bokaro Plant).
        Route: Tanjung Bara -> Dhamra Port (Berth: DP-2).
        Cargo: 75,000 MT Thermal Coal @ 15,000 MT/day SHEX.
        Allowed laytime: 75,000 / 15,000 * 24 = 120.000000 hours (5.0 days).
        """
        vessel = VesselFixture(
            vessel_name="MV SUNDARBAN CARRIER",
            imo_number="9321456",
            flag_state="SG",
            size_class=VesselSizeClass.PANAMAX,
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
            service_speed_knots=Decimal("13.8"),
        )

        laycan_start = date(2025, 8, 10)
        laycan_end = date(2025, 8, 20)

        contract = CharterContractFixture(
            contract_reference="SAIL/CP/2025/PAN-02",
            charter_party_type=CharterPartyType.VOYAGE_CHARTER,
            contract_status=ContractStatus.EXECUTED,
            charterer_entity="Steel Authority of India Ltd (SAIL)",
            charterer_plant="Bokaro Steel Plant",
            cargo_type=CargoType.THERMAL_COAL,
            nominated_quantity_mt=Decimal("75000.000"),
            tolerance_pct=Decimal("5.00"),
            freight_rate_usd_per_mt=Decimal("10.80"),
            discharge_port="Dhamra",
            discharge_berth_designated="DP-2",
            discharge_rate_mt_per_day=Decimal("15000.000"),
            laytime_terms=LaytimeTerms.SHEX,
            demurrage_rate_usd_per_day=Decimal("18000.00"),
            despatch_rate_usd_per_day=Decimal("9000.00"),
            laycan_start=laycan_start,
            laycan_end=laycan_end,
            turn_time_hours=Decimal("6.00"),
            once_on_demurrage_always_on=True,
        )

        eta = datetime(2025, 8, 14, 12, 0, tzinfo=timezone.utc)
        voyage = VoyageFixture(
            voyage_reference="VOY-SAIL-2025-02",
            voyage_status=VoyageStatus.IN_TRANSIT,
            load_port="Tanjung Bara",
            discharge_port="Dhamra",
            discharge_berth="DP-2",
            eta_date=eta,
            quantity_loaded_mt=Decimal("74800.000"),
        )

        return CompleteMaritimeFixture(
            scenario_id="BALTIC_PAN_IDN_IND_COAL_02",
            description="Panamax 75k MT Thermal Coal Tanjung Bara to Dhamra DP-2 (SAIL Bokaro)",
            vessel=vessel,
            contract=contract,
            voyage=voyage,
            expected_allowed_laytime_hours=Decimal("120.000000"),
        )

    @staticmethod
    def supramax_south_africa_to_india_metcoke() -> CompleteMaritimeFixture:
        """
        Scenario 3: Supramax Met Coke from Richards Bay, South Africa
        to Visakhapatnam Port, India for Rashtriya Ispat Nigam Ltd (RINL/Vizag Steel).
        Route: Richards Bay -> Visakhapatnam (Berth: EQ-1).
        Cargo: 54,000 MT Met Coke @ 9,000 MT/day SHINC.
        Allowed laytime: 54,000 / 9,000 * 24 = 144.000000 hours (6.0 days).
        """
        vessel = VesselFixture(
            vessel_name="MV ANDHRA GLORY",
            imo_number="9543210",
            flag_state="MH",
            size_class=VesselSizeClass.SUPRAMAX,
            deadweight_tonnage=Decimal("57800.000"),
            gross_tonnage=Decimal("33100.000"),
            net_tonnage=Decimal("19400.000"),
            length_overall_meters=Decimal("190.00"),
            beam_meters=Decimal("32.26"),
            draft_meters=Decimal("12.80"),
            hold_count=5,
            hatch_count=5,
            build_year=2019,
            owner_name="Global Maritime Maritime Inc",
            service_speed_knots=Decimal("14.0"),
        )

        laycan_start = date(2025, 9, 1)
        laycan_end = date(2025, 9, 12)

        contract = CharterContractFixture(
            contract_reference="RINL/CP/2025/SUPRA-03",
            charter_party_type=CharterPartyType.VOYAGE_CHARTER,
            contract_status=ContractStatus.EXECUTED,
            charterer_entity="Rashtriya Ispat Nigam Ltd (RINL)",
            charterer_plant="Visakhapatnam Steel Plant",
            cargo_type=CargoType.MET_COKE,
            nominated_quantity_mt=Decimal("54000.000"),
            tolerance_pct=Decimal("5.00"),
            freight_rate_usd_per_mt=Decimal("22.50"),
            discharge_port="Visakhapatnam",
            discharge_berth_designated="EQ-1",
            discharge_rate_mt_per_day=Decimal("9000.000"),
            laytime_terms=LaytimeTerms.SHINC,
            demurrage_rate_usd_per_day=Decimal("16000.00"),
            despatch_rate_usd_per_day=Decimal("8000.00"),
            laycan_start=laycan_start,
            laycan_end=laycan_end,
            turn_time_hours=Decimal("6.00"),
            once_on_demurrage_always_on=True,
        )

        eta = datetime(2025, 9, 8, 10, 0, tzinfo=timezone.utc)
        voyage = VoyageFixture(
            voyage_reference="VOY-RINL-2025-03",
            voyage_status=VoyageStatus.ARRIVED,
            load_port="Richards Bay",
            discharge_port="Visakhapatnam",
            discharge_berth="EQ-1",
            eta_date=eta,
            ata_date=datetime(2025, 9, 8, 9, 45, tzinfo=timezone.utc),
            nor_tendered_at=datetime(2025, 9, 8, 11, 0, tzinfo=timezone.utc),
            quantity_loaded_mt=Decimal("53900.000"),
        )

        return CompleteMaritimeFixture(
            scenario_id="BALTIC_SUPRA_ZAF_IND_METCOKE_03",
            description="Supramax 54k MT Met Coke Richards Bay to Vizag EQ-1 (RINL/Vizag Steel)",
            vessel=vessel,
            contract=contract,
            voyage=voyage,
            expected_allowed_laytime_hours=Decimal("144.000000"),
        )

    @classmethod
    def get_all_scenarios(cls) -> List[CompleteMaritimeFixture]:
        """Return all Baltic standard scenarios."""
        return [
            cls.capesize_australia_to_india_coal(),
            cls.panamax_indonesia_to_india_thermal(),
            cls.supramax_south_africa_to_india_metcoke(),
        ]
