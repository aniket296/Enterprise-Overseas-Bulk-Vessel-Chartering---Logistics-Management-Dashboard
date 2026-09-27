-- =============================================================================
-- Migration: V001__enums_and_domain_types.sql
-- Project:   Overseas Bulk Vessel Chartering & Logistics Management Dashboard
-- Cycle:     1.1.1 — Database DDL & Enum Definition
-- Author:    Antigravity / SIH26006 Engineering Team
-- DB:        PostgreSQL 16
-- Description:
--   Defines all custom ENUM types and DOMAIN types used across the entire
--   schema. Enums are immutable vocabulary; new values must go through a
--   schema-reviewed migration. All types live in the `shipping` schema to
--   avoid polluting the public namespace.
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- Schema isolation
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS shipping;

-- Set search path for this migration session only (does not persist globally)
SET LOCAL search_path TO shipping, public;

-- ---------------------------------------------------------------------------
-- 1. VESSEL & FLEET ENUMS
-- ---------------------------------------------------------------------------

-- Vessel size class per DWT (Deadweight Tonnage) categorisation
CREATE TYPE shipping.vessel_size_class AS ENUM (
    'HANDYSIZE',       -- 10,000 – 35,000 DWT
    'HANDYMAX',        -- 35,000 – 50,000 DWT
    'SUPRAMAX',        -- 50,000 – 60,000 DWT
    'ULTRAMAX',        -- 60,000 – 65,000 DWT
    'PANAMAX',         -- 65,000 – 80,000 DWT
    'KAMSARMAX',       -- 80,000 – 85,000 DWT
    'CAPESIZE',        -- 100,000 – 180,000 DWT
    'VERY_LARGE_ORE_CARRIER' -- > 180,000 DWT (VLOC / Newcastlemax)
);

-- Cargo commodity type for bulk carriers
CREATE TYPE shipping.cargo_type AS ENUM (
    'COKING_COAL',          -- Metallurgical / coking coal (primary SAIL need)
    'THERMAL_COAL',         -- Steam coal
    'IRON_ORE',             -- Iron ore fines and lump
    'IRON_ORE_PELLETS',     -- Pre-processed pellets
    'LIMESTONE',            -- Fluxing agent
    'DOLOMITE',             -- Secondary fluxing agent
    'MANGANESE_ORE',        -- For ferro-alloy production
    'CHROME_ORE',           -- For specialty steels
    'NICKEL_ORE',           -- For stainless steel
    'PIG_IRON',             -- Semi-finished iron
    'SCRAP_METAL',          -- Recycled steel input
    'GENERAL_BULK'          -- Catch-all for miscellaneous bulk
);

-- ---------------------------------------------------------------------------
-- 2. CHARTER CONTRACT ENUMS
-- ---------------------------------------------------------------------------

-- Charter party type per Baltic Exchange standard
CREATE TYPE shipping.charter_party_type AS ENUM (
    'VOYAGE_CHARTER',       -- Freight on specific cargo / voyage
    'TIME_CHARTER',         -- Vessel hired for period, owner pays bunkers
    'BAREBOAT_CHARTER',     -- Full vessel control to charterer
    'CONTRACT_OF_AFFREIGHTMENT' -- Series of voyages under one contract (COA)
);

-- Contract lifecycle state machine
CREATE TYPE shipping.contract_status AS ENUM (
    'DRAFT',            -- Internal draft, not yet sent
    'NEGOTIATING',      -- Under commercial negotiation
    'FIXTURE_CONFIRMED',-- Recap confirmed, commercially binding
    'CP_SIGNED',        -- Charter party formally executed
    'ACTIVE',           -- Voyage(s) in progress under this contract
    'COMPLETED',        -- All voyages completed, financials settled
    'CANCELLED',        -- Cancelled before commencement
    'DISPUTED'          -- Under legal / arbitration dispute
);

-- Laytime terms — determines which periods count against allowed laytime
CREATE TYPE shipping.laytime_terms AS ENUM (
    'SHINC',    -- Sundays and Holidays Included
    'SHEX',     -- Sundays and Holidays Excluded
    'SHEX_EIU', -- Sundays and Holidays Excluded Even If Used
    'FHINC',    -- Fridays and Holidays Included (Middle East)
    'FHEX',     -- Fridays and Holidays Excluded
    'WWD',      -- Weather Working Days (weather halts excluded)
    'WWDSHEX',  -- WWD combined with SHEX
    'CQD',      -- Customary Quick Despatch (no fixed laytime)
    'AS_FAST_AS_CAN' -- Vessel discharges as fast as physically possible
);

-- Demurrage / despatch payment currency
CREATE TYPE shipping.settlement_currency AS ENUM (
    'USD',  -- US Dollar (dominant charter market currency)
    'EUR',  -- Euro
    'GBP',  -- British Pound
    'INR',  -- Indian Rupee (domestic settlements)
    'JPY',  -- Japanese Yen
    'CNY'   -- Chinese Yuan
);

-- ---------------------------------------------------------------------------
-- 3. VOYAGE LIFECYCLE ENUMS
-- ---------------------------------------------------------------------------

-- Operational state machine for a single vessel voyage leg
CREATE TYPE shipping.voyage_status AS ENUM (
    'SCHEDULED',        -- Voyage planned, vessel not yet departed
    'IN_TRANSIT',       -- Vessel underway to load/discharge port
    'ARRIVED',          -- Vessel arrived, awaiting berth (at anchor or roads)
    'NOR_TENDERED',     -- Notice of Readiness officially tendered
    'BERTHED',          -- Vessel secured to berth
    'COMMENCING',       -- First line of discharge commenced
    'DISCHARGING',      -- Active cargo discharge in progress
    'COMPLETED',        -- Discharge complete, vessel released
    'DEVIATED',         -- Vessel diverted (weather, port congestion)
    'CANCELLED'         -- Voyage cancelled after scheduling
);

-- Direction of a voyage leg
CREATE TYPE shipping.voyage_direction AS ENUM (
    'LADEN',    -- Carrying cargo (loaded leg)
    'BALLAST'   -- Empty, positioning to next load port
);

-- Port function in the voyage itinerary
CREATE TYPE shipping.port_function AS ENUM (
    'LOAD',         -- Loading port
    'DISCHARGE',    -- Discharge port
    'BUNKER',       -- Fuelling stop
    'DEVIATION',    -- Emergency or weather diversion
    'TRANSSHIPMENT' -- Cargo transferred to another vessel
);

-- ---------------------------------------------------------------------------
-- 4. STATEMENT OF FACTS (SOF) ENUMS
-- ---------------------------------------------------------------------------

-- SOF event type — each event marks a timestamp milestone in the port log
CREATE TYPE shipping.sof_event_type AS ENUM (
    -- Arrival / Notice sequence
    'VESSEL_ARRIVED_PILOT_STATION',
    'VESSEL_ARRIVED_ANCHORAGE',
    'NOR_TENDERED',
    'NOR_ACCEPTED',
    'NOR_REJECTED',
    'VESSEL_SHIFTED_ANCHORAGE',

    -- Berthing sequence
    'PILOT_BOARDED',
    'VESSEL_COMMENCED_BERTHING',
    'FIRST_LINE_ASHORE',
    'ALL_FAST',
    'GANGWAY_DOWN',

    -- Pre-discharge operations
    'CUSTOMS_BOARDING',
    'CUSTOMS_CLEARED',
    'DRAFT_SURVEY_COMMENCED',
    'DRAFT_SURVEY_COMPLETED',
    'HATCHES_OPENED',

    -- Discharge operations
    'DISCHARGE_COMMENCED',
    'DISCHARGE_SUSPENDED',
    'DISCHARGE_RESUMED',
    'DISCHARGE_COMPLETED',

    -- Interruption events
    'RAIN_COMMENCED',
    'RAIN_CEASED',
    'WIND_FORCE_EXCEEDED_LIMIT',
    'WIND_FORCE_WITHIN_LIMIT',
    'CRANE_BREAKDOWN_COMMENCED',
    'CRANE_BREAKDOWN_RESUMED',
    'POWER_FAILURE_COMMENCED',
    'POWER_FAILURE_RESUMED',
    'LABOUR_DISPUTE_COMMENCED',
    'LABOUR_DISPUTE_ENDED',
    'BERTH_CONGESTION_DELAY_START',
    'BERTH_CONGESTION_DELAY_END',

    -- Departure sequence
    'HATCHES_CLOSED',
    'DRAFT_SURVEY_FINAL',
    'LINES_RELEASED',
    'VESSEL_UNMOORED',
    'PILOT_DISEMBARKED',
    'VESSEL_DEPARTED'
);

-- Classification of a SOF time interval for laytime counting
CREATE TYPE shipping.interval_count_status AS ENUM (
    'FULLY_COUNTED',        -- Counts 100% against allowed laytime
    'PARTIALLY_COUNTED',    -- Counts at specified fraction (e.g., 0.5 for rain)
    'EXCLUDED',             -- Does not count (SHEX, weather under WWD, etc.)
    'DISPUTED'              -- Under dispute between owners and charterers
);

-- ---------------------------------------------------------------------------
-- 5. LAYTIME CALCULATION ENUMS
-- ---------------------------------------------------------------------------

-- Overall laytime outcome after full calculation
CREATE TYPE shipping.laytime_outcome AS ENUM (
    'PENDING',      -- Calculation not yet triggered / complete
    'IN_PROGRESS',  -- Celery worker actively computing
    'DESPATCH',     -- Vessel completed before allowed laytime (charterer earns despatch)
    'DEMURRAGE',    -- Vessel exceeded allowed laytime (owner earns demurrage)
    'EVEN_KEEL',    -- Allowed laytime exactly consumed (no payment either way)
    'DISPUTED',     -- Figures contested, pending agent / broker review
    'SETTLED'       -- Financials agreed and payment issued
);

-- ---------------------------------------------------------------------------
-- 6. DOCUMENT MANAGEMENT ENUMS
-- ---------------------------------------------------------------------------

-- Shipping document classification
CREATE TYPE shipping.document_type AS ENUM (
    'CHARTER_PARTY',            -- The main charter party contract
    'FIXTURE_RECAP',            -- Pre-CP fixture confirmation
    'BILL_OF_LADING',           -- B/L issued at load port
    'NOTICE_OF_READINESS',      -- Formal NOR document
    'STATEMENT_OF_FACTS',       -- Port agent's SOF report
    'LAYTIME_STATEMENT',        -- Laytime calculation sheet
    'DEMURRAGE_CLAIM',          -- Owner's demurrage invoice
    'DESPATCH_CREDIT_NOTE',     -- Charterer's despatch credit
    'CARGO_MANIFEST',           -- Full cargo manifest
    'DRAFT_SURVEY_REPORT',      -- Pre/post discharge draft survey
    'CUSTOMS_ENTRY',            -- Import/export customs documentation
    'LETTER_OF_INDEMNITY',      -- LOI issued against missing BL
    'INSURANCE_CERTIFICATE',    -- Marine cargo insurance cert
    'WEIGHT_CERTIFICATE',       -- Shore scale / weighbridge cert
    'QUALITY_CERTIFICATE',      -- Cargo quality / assay report
    'PHYTOSANITARY_CERTIFICATE',-- Plant product clearance
    'OTHER'                     -- Miscellaneous
);

-- Document integrity verification state
CREATE TYPE shipping.document_verification_status AS ENUM (
    'PENDING_UPLOAD',       -- Metadata record created, upload not yet complete
    'UPLOAD_COMPLETE',      -- File received in MinIO, pending hash verification
    'HASH_VERIFIED',        -- SHA-256 digest confirmed intact
    'HASH_MISMATCH',        -- Digest mismatch — file quarantined
    'QUARANTINED',          -- Isolated pending security review
    'APPROVED',             -- Reviewed and approved by authorised officer
    'SUPERSEDED',           -- Replaced by a newer version of the document
    'DELETED'               -- Soft-deleted (retains audit record)
);

-- ---------------------------------------------------------------------------
-- 7. PLANT INVENTORY ENUMS
-- ---------------------------------------------------------------------------

-- Stock safety status based on runway calculation
CREATE TYPE shipping.stock_safety_status AS ENUM (
    'CRITICAL',         -- Runway < 7 days (emergency replenishment)
    'PRECAUTIONARY',    -- Runway 7–14 days (expedite next vessel)
    'WATCH',            -- Runway 14–21 days (monitor closely)
    'ADEQUATE',         -- Runway 21–45 days (normal operations)
    'SURPLUS'           -- Runway > 45 days (consider deferring next cargo)
);

-- Raw material category at the integrated steel plant
CREATE TYPE shipping.raw_material_category AS ENUM (
    'COKING_COAL',
    'PCI_COAL',         -- Pulverised Coal Injection coal
    'IRON_ORE_FINES',
    'IRON_ORE_LUMP',
    'IRON_ORE_PELLETS',
    'SINTER_FEED',
    'LIMESTONE',
    'DOLOMITE',
    'MANGANESE_ORE',
    'QUARTZITE',        -- Fluxing quartzite
    'COKE'              -- Coke produced at coke ovens
);

-- ---------------------------------------------------------------------------
-- 8. SYSTEM / AUDIT ENUMS
-- ---------------------------------------------------------------------------

-- User role for RBAC (maps to Django groups)
CREATE TYPE shipping.user_role AS ENUM (
    'SYSTEM_ADMIN',         -- Full system access
    'PROCUREMENT_OFFICER',  -- Manages contracts and commercial terms
    'LOGISTICS_AGENT',      -- Port operations, SOF entry, berth management
    'CHARTER_BROKER',       -- External broker with read + proposal access
    'FINANCE_OFFICER',      -- Demurrage / despatch settlement sign-off
    'PLANT_MANAGER',        -- Inventory and stock runway visibility
    'AUDITOR',              -- Read-only access to all records and audit logs
    'READONLY'              -- Strictly read-only, no write operations
);

-- Alert severity tier
CREATE TYPE shipping.alert_severity AS ENUM (
    'INFO',
    'WARNING',
    'CRITICAL',
    'EMERGENCY'
);

-- Alert lifecycle state
CREATE TYPE shipping.alert_status AS ENUM (
    'ACTIVE',
    'ACKNOWLEDGED',
    'RESOLVED',
    'SUPPRESSED'
);

COMMIT;
