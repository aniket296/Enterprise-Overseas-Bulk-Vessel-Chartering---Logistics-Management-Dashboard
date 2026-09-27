-- =============================================================================
-- Migration: V002__core_tables.sql
-- Project:   Overseas Bulk Vessel Chartering & Logistics Management Dashboard
-- Cycle:     1.1.1 — Database DDL & Enum Definition (core tables)
-- Author:    Antigravity / SIH26006 Engineering Team
-- DB:        PostgreSQL 16
-- Depends:   V001__enums_and_domain_types.sql
-- Description:
--   Creates the six primary domain tables that form the backbone of the
--   platform:
--     1. vessels               — IMO-registered vessel master registry
--     2. charter_contracts     — Commercial charter party agreements
--     3. vessel_voyages        — Individual voyage legs under contracts
--     4. statement_of_facts    — Port event log entries (raw SOF lines)
--     5. laytime_calculations  — Computed laytime / demurrage records
--     6. plant_inventory       — Steel plant raw material stock levels
--     7. shipping_documents    — Document vault with SHA-256 integrity
--
--   Foreign-key constraints, check constraints, and full indexing are
--   handled in subsequent migrations (V003, V004).  This migration ONLY
--   defines column-level NOT NULL / DEFAULT / basic type safety.
-- =============================================================================

BEGIN;

SET LOCAL search_path TO shipping, public;

-- ===========================================================================
-- TABLE 1: vessels
-- Master registry of all IMO-registered bulk carrier vessels tracked by
-- the platform.  A vessel record is system-wide and shared across voyages.
-- ===========================================================================
CREATE TABLE shipping.vessels (
    -- Surrogate primary key
    vessel_id           UUID            NOT NULL DEFAULT gen_random_uuid(),

    -- IMO number: 7 digits, globally unique, immutable once assigned
    -- Format enforced via CHECK in V003
    imo_number          CHAR(7)         NOT NULL,

    -- Commercial names (a vessel may be renamed; historical names in audit log)
    vessel_name         VARCHAR(120)    NOT NULL,
    former_name         VARCHAR(120),

    -- Classification
    size_class          shipping.vessel_size_class  NOT NULL,
    flag_state          CHAR(2)         NOT NULL,   -- ISO 3166-1 alpha-2
    classification_society  VARCHAR(60),             -- e.g. 'LLOYD'S REGISTER'

    -- Dimensions & capacity (NUMERIC for arithmetic precision)
    deadweight_tonnes   NUMERIC(10, 2)  NOT NULL,   -- DWT in metric tonnes
    gross_tonnage       NUMERIC(10, 2)  NOT NULL,   -- GT for port dues
    net_tonnage         NUMERIC(10, 2)  NOT NULL,   -- NT for canal dues
    loa_metres          NUMERIC(7, 3)   NOT NULL,   -- Length Overall
    beam_metres         NUMERIC(6, 3)   NOT NULL,   -- Breadth moulded
    draft_laden_metres  NUMERIC(5, 3)   NOT NULL,   -- Max laden draft
    hold_count          SMALLINT        NOT NULL,   -- Number of cargo holds
    hatch_count         SMALLINT        NOT NULL,   -- Number of hatches

    -- Build & ownership
    build_year          SMALLINT        NOT NULL,
    shipyard            VARCHAR(120),
    owner_name          VARCHAR(120)    NOT NULL,
    owner_country       CHAR(2),                    -- ISO 3166-1 alpha-2
    manager_name        VARCHAR(120),
    operator_name       VARCHAR(120),

    -- Propulsion
    engine_type         VARCHAR(60),
    service_speed_knots NUMERIC(4, 1),              -- Design service speed

    -- Audit
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,

    CONSTRAINT vessels_pkey PRIMARY KEY (vessel_id)
);

COMMENT ON TABLE  shipping.vessels IS
    'IMO-registered bulk carrier master registry. One row per physical vessel.';
COMMENT ON COLUMN shipping.vessels.imo_number IS
    'IMO ship identification number — 7 digits, globally unique, immutable.';
COMMENT ON COLUMN shipping.vessels.deadweight_tonnes IS
    'Deadweight Tonnage in metric tonnes; primary vessel capacity metric for bulk charters.';

-- ===========================================================================
-- TABLE 2: charter_contracts
-- Represents a single charter party agreement between a charterer (SAIL/RINL)
-- and a vessel owner / operator.  One contract may cover multiple voyages
-- (Contract of Affreightment) or a single voyage.
-- ===========================================================================
CREATE TABLE shipping.charter_contracts (
    -- Surrogate primary key
    contract_id             UUID            NOT NULL DEFAULT gen_random_uuid(),

    -- Human-readable reference number (e.g. SAIL/CHR/2025-26/0042)
    contract_reference      VARCHAR(60)     NOT NULL,

    -- Parties
    charterer_entity        VARCHAR(120)    NOT NULL,   -- e.g. 'SAIL Corporate'
    charterer_plant         VARCHAR(120),               -- e.g. 'BSP Bhilai'
    owner_entity            VARCHAR(120)    NOT NULL,
    broker_entity           VARCHAR(120),               -- Intermediary broker
    vessel_id               UUID            NOT NULL,   -- FK -> vessels

    -- Commercial terms
    charter_party_type      shipping.charter_party_type     NOT NULL,
    contract_status         shipping.contract_status        NOT NULL DEFAULT 'DRAFT',

    -- Load / discharge geography
    load_port               VARCHAR(120)    NOT NULL,
    load_country            CHAR(2)         NOT NULL,
    discharge_port          VARCHAR(120)    NOT NULL,
    discharge_country       CHAR(2)         NOT NULL DEFAULT 'IN', -- India default

    -- Cargo specification
    cargo_type              shipping.cargo_type     NOT NULL,
    nominated_quantity_mt   NUMERIC(10, 3)  NOT NULL,   -- Metric tonnes
    tolerance_pct           NUMERIC(4, 2)   NOT NULL DEFAULT 5.00, -- ± %
    cargo_description       TEXT,

    -- Laytime terms
    laytime_terms           shipping.laytime_terms      NOT NULL,
    allowed_laytime_hours   NUMERIC(8, 4)   NOT NULL,   -- Contractual allowance
    discharge_rate_mt_per_day NUMERIC(10, 3) NOT NULL,  -- MT per WWD
    demurrage_rate_usd_per_day NUMERIC(12, 4) NOT NULL, -- USD per day
    despatch_rate_usd_per_day  NUMERIC(12, 4) NOT NULL, -- Typically 50% of demurrage
    once_on_demurrage_always_on BOOLEAN      NOT NULL DEFAULT TRUE,

    -- Financial
    freight_rate_usd_per_mt NUMERIC(10, 4),            -- Only for voyage charters
    total_freight_usd       NUMERIC(14, 4),
    settlement_currency     shipping.settlement_currency NOT NULL DEFAULT 'USD',

    -- Key dates
    laycan_start            DATE            NOT NULL,   -- Earliest vessel arrival
    laycan_end              DATE            NOT NULL,   -- Latest vessel arrival
    cp_date                 DATE,                       -- Charter party execution date
    fixture_date            DATE,                       -- Recap / fixture confirmation date

    -- Contractual documents
    cp_document_id          UUID,                       -- FK -> shipping_documents (set post-creation)
    special_clauses         TEXT,                       -- Rider clauses in plain text

    -- Audit
    created_by              UUID,                       -- FK -> auth_user (Django)
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT charter_contracts_pkey PRIMARY KEY (contract_id)
);

COMMENT ON TABLE  shipping.charter_contracts IS
    'Charter party agreements for overseas bulk vessel procurement. Central commercial record.';
COMMENT ON COLUMN shipping.charter_contracts.allowed_laytime_hours IS
    'Contractual allowed laytime in fractional hours. Computed as nominated_quantity_mt / discharge_rate_mt_per_day * 24.';
COMMENT ON COLUMN shipping.charter_contracts.once_on_demurrage_always_on IS
    'If TRUE, once demurrage commences it runs continuously regardless of subsequent interruptions.';
COMMENT ON COLUMN shipping.charter_contracts.tolerance_pct IS
    'Cargo quantity tolerance percentage (±). Actual loaded quantity may vary within this band.';

-- ===========================================================================
-- TABLE 3: vessel_voyages
-- A single voyage leg executed under a charter contract.  COA contracts
-- produce multiple voyage rows; voyage charters produce exactly one.
-- ===========================================================================
CREATE TABLE shipping.vessel_voyages (
    -- Surrogate primary key
    voyage_id               UUID            NOT NULL DEFAULT gen_random_uuid(),

    -- Voyage reference (e.g. SAIL/VOY/2025-26/0042-01)
    voyage_reference        VARCHAR(80)     NOT NULL,

    -- Links
    contract_id             UUID            NOT NULL,   -- FK -> charter_contracts
    vessel_id               UUID            NOT NULL,   -- FK -> vessels (denormalised for query speed)

    -- Voyage classification
    voyage_status           shipping.voyage_status      NOT NULL DEFAULT 'SCHEDULED',
    voyage_direction        shipping.voyage_direction   NOT NULL DEFAULT 'LADEN',

    -- Load port details
    load_port               VARCHAR(120)    NOT NULL,
    load_country            CHAR(2)         NOT NULL,
    load_berth              VARCHAR(60),
    load_terminal           VARCHAR(120),
    etl_date                DATE,                       -- Estimated Time of Loading
    atl_date                TIMESTAMPTZ,                -- Actual Time of Loading (UTC)
    quantity_loaded_mt      NUMERIC(10, 3),             -- BL quantity

    -- Discharge port details
    discharge_port          VARCHAR(120)    NOT NULL,
    discharge_country       CHAR(2)         NOT NULL DEFAULT 'IN',
    discharge_berth         VARCHAR(60),
    discharge_terminal      VARCHAR(120),

    -- Key voyage timestamps (all UTC)
    eta_date                TIMESTAMPTZ,                -- Latest ETA at discharge port
    ata_date                TIMESTAMPTZ,                -- Actual Time of Arrival
    nor_tendered_at         TIMESTAMPTZ,                -- NOR tender timestamp
    nor_accepted_at         TIMESTAMPTZ,                -- NOR accepted / laytime commences
    berthed_at              TIMESTAMPTZ,                -- All-fast timestamp
    discharge_commenced_at  TIMESTAMPTZ,                -- First grab / conveyor start
    discharge_completed_at  TIMESTAMPTZ,                -- Holds empty, hatches closed
    completed_at            TIMESTAMPTZ,                -- Vessel departed, voyage closed

    -- Cargo outturn
    quantity_discharged_mt  NUMERIC(10, 3),             -- Shore scale / draft survey
    shortage_mt             NUMERIC(10, 3) GENERATED ALWAYS AS
        (CASE
            WHEN quantity_loaded_mt IS NOT NULL AND quantity_discharged_mt IS NOT NULL
            THEN GREATEST(quantity_loaded_mt - quantity_discharged_mt, 0)
            ELSE NULL
         END) STORED,

    -- Operational metadata
    voyage_days             INTEGER GENERATED ALWAYS AS
        (CASE
            WHEN ata_date IS NOT NULL AND completed_at IS NOT NULL
            THEN EXTRACT(DAY FROM (completed_at - ata_date))::INTEGER
            ELSE NULL
         END) STORED,

    -- Bill of Lading reference
    bl_number               VARCHAR(60),
    bl_date                 DATE,
    bl_quantity_mt          NUMERIC(10, 3),

    -- Vessel position (last known, updated by Celery watcher)
    last_position_lat       NUMERIC(9, 6),
    last_position_lon       NUMERIC(9, 6),
    last_position_updated   TIMESTAMPTZ,
    speed_over_ground       NUMERIC(4, 1),  -- knots

    -- Audit
    created_by              UUID,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT vessel_voyages_pkey PRIMARY KEY (voyage_id)
);

COMMENT ON TABLE  shipping.vessel_voyages IS
    'Individual voyage legs executed under a charter contract. State machine progresses from SCHEDULED to COMPLETED.';
COMMENT ON COLUMN shipping.vessel_voyages.shortage_mt IS
    'Computed shortage: loaded quantity minus discharged quantity, floored at 0.';
COMMENT ON COLUMN shipping.vessel_voyages.nor_accepted_at IS
    'The precise moment laytime begins counting. Determined by NOR_TENDERED timestamp plus contractual turn-time/free-pratique buffer.';

-- ===========================================================================
-- TABLE 4: statement_of_facts
-- Immutable port event log (SOF lines) recorded by the port agent.
-- Each row is a single timestamp-stamped event.  The sequence of events
-- drives the laytime calculation engine in Phase 1.2.
-- ===========================================================================
CREATE TABLE shipping.statement_of_facts (
    -- Surrogate primary key
    sof_id              UUID            NOT NULL DEFAULT gen_random_uuid(),

    -- Link to voyage
    voyage_id           UUID            NOT NULL,   -- FK -> vessel_voyages

    -- Event definition
    event_type          shipping.sof_event_type     NOT NULL,
    event_timestamp     TIMESTAMPTZ     NOT NULL,   -- Authoritative UTC timestamp

    -- Sequence ordering within the voyage (must be monotonically increasing)
    sequence_number     INTEGER         NOT NULL,

    -- Contextual metadata
    event_description   TEXT,                       -- Free-text agent notes
    recorded_by_agent   VARCHAR(120),               -- Port agent name / company
    crane_number        SMALLINT,                   -- Relevant crane ID (for crane events)
    hatch_number        SMALLINT,                   -- Relevant hatch ID
    quantity_discharged_at_event NUMERIC(10, 3),    -- Cumulative MT at time of event

    -- Interruption metadata (populated for interruption-type events)
    interruption_cause  VARCHAR(120),               -- e.g. 'Monsoon squall', 'Grab failure'
    weather_condition   VARCHAR(60),                -- e.g. 'BF5', 'Heavy rain'

    -- Laytime counting (computed by Cycle 1.2.x engine, stored here for audit)
    count_status        shipping.interval_count_status,  -- Set by calculation engine
    count_fraction      NUMERIC(3, 2),              -- 0.00–1.00 (e.g. 0.50 for rain-SHINC)
    counted_hours       NUMERIC(10, 6),             -- Hours this interval contributes

    -- Immutability: once submitted by port agent, SOF lines must not be silently edited.
    -- Corrections must insert a new row with is_amendment = TRUE referencing amended_sof_id.
    is_amendment        BOOLEAN         NOT NULL DEFAULT FALSE,
    amended_sof_id      UUID,                       -- FK -> statement_of_facts (self-ref)

    -- Document reference (original SOF PDF in document vault)
    source_document_id  UUID,                       -- FK -> shipping_documents

    -- Audit
    submitted_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    submitted_by        UUID,                       -- FK -> auth_user

    CONSTRAINT statement_of_facts_pkey PRIMARY KEY (sof_id)
);

COMMENT ON TABLE  shipping.statement_of_facts IS
    'Immutable chronological port event log. Each row = one SOF line item from the port agent. Drives laytime calculation.';
COMMENT ON COLUMN shipping.statement_of_facts.sequence_number IS
    'Monotonically increasing within a voyage. Used to detect and reject out-of-order submissions.';
COMMENT ON COLUMN shipping.statement_of_facts.count_fraction IS
    'Fraction of interval that counts as laytime (0.00=excluded, 1.00=fully counted, 0.50=half-time).';
COMMENT ON COLUMN shipping.statement_of_facts.is_amendment IS
    'If TRUE, this row supersedes the SOF line referenced by amended_sof_id.';

-- ===========================================================================
-- TABLE 5: laytime_calculations
-- One row per voyage; stores the fully computed laytime result after the
-- Celery engine processes all SOF events.  Supports iterative recomputation
-- as new SOF events are appended.
-- ===========================================================================
CREATE TABLE shipping.laytime_calculations (
    -- Surrogate primary key
    calc_id                 UUID            NOT NULL DEFAULT gen_random_uuid(),

    -- Link (one-to-one with voyage in steady state, but may have multiple
    -- revisions; the latest is_current = TRUE row is authoritative)
    voyage_id               UUID            NOT NULL,   -- FK -> vessel_voyages
    revision                INTEGER         NOT NULL DEFAULT 1,
    is_current              BOOLEAN         NOT NULL DEFAULT TRUE,

    -- Calculation state
    outcome                 shipping.laytime_outcome    NOT NULL DEFAULT 'PENDING',
    calculated_at           TIMESTAMPTZ,
    celery_task_id          VARCHAR(64),                -- Celery task UUID for traceability

    -- Laytime accounting (all durations in fractional hours, NUMERIC for precision)
    allowed_laytime_hours   NUMERIC(12, 6)  NOT NULL,   -- From contract
    total_port_hours        NUMERIC(12, 6),             -- Actual time at port (NOR accepted → discharge completed)
    total_counted_hours     NUMERIC(12, 6), -- After applying SHEX / WWD / interruption exclusions
    total_excepted_hours    NUMERIC(12, 6), -- Total excluded interruption hours
    total_disputed_hours    NUMERIC(12, 6) DEFAULT 0,

    -- Demurrage / despatch variance
    variance_hours          NUMERIC(12, 6) GENERATED ALWAYS AS
        (CASE
            WHEN total_counted_hours IS NOT NULL
            THEN total_counted_hours - allowed_laytime_hours
            ELSE NULL
         END) STORED,
    -- Positive = demurrage; negative = despatch

    -- Financial settlement
    demurrage_rate_usd_per_day  NUMERIC(12, 4), -- Copied from contract at calc time
    despatch_rate_usd_per_day   NUMERIC(12, 4),
    gross_demurrage_usd         NUMERIC(14, 4), -- Payable by charterer
    gross_despatch_usd          NUMERIC(14, 4), -- Payable by owner
    net_settlement_usd          NUMERIC(14, 4) GENERATED ALWAYS AS
        (CASE
            WHEN gross_demurrage_usd IS NOT NULL AND gross_despatch_usd IS NOT NULL
            THEN gross_demurrage_usd - gross_despatch_usd
            ELSE NULL
         END) STORED,
    -- Positive = charterer owes owner; negative = owner owes charterer

    -- Audit trail of SOF events used in this calculation
    sof_event_count         INTEGER,
    first_sof_event_at      TIMESTAMPTZ,
    last_sof_event_at       TIMESTAMPTZ,

    -- Settlement
    settled_at              TIMESTAMPTZ,
    settled_by              UUID,
    settlement_notes        TEXT,

    -- Audit
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT laytime_calculations_pkey PRIMARY KEY (calc_id)
);

COMMENT ON TABLE  shipping.laytime_calculations IS
    'Authoritative laytime computation result per voyage revision. Celery engine populates after SOF ingestion.';
COMMENT ON COLUMN shipping.laytime_calculations.variance_hours IS
    'Positive = demurrage (excess laytime); negative = despatch (saved laytime). Stored generated column.';
COMMENT ON COLUMN shipping.laytime_calculations.net_settlement_usd IS
    'Net monetary settlement: positive = charterer owes, negative = owner owes. Stored generated column.';
COMMENT ON COLUMN shipping.laytime_calculations.is_current IS
    'Only one revision per voyage should be TRUE at any time (enforced via partial unique index).';

-- ===========================================================================
-- TABLE 6: plant_inventory
-- Daily stock level snapshots at each integrated steel plant location.
-- One row per (plant, material_category, snapshot_date).
-- ===========================================================================
CREATE TABLE shipping.plant_inventory (
    -- Surrogate primary key
    inventory_id            UUID            NOT NULL DEFAULT gen_random_uuid(),

    -- Plant identification
    plant_code              VARCHAR(20)     NOT NULL,   -- e.g. 'BSP', 'DSP', 'RSP', 'BSL', 'ISP'
    plant_name              VARCHAR(120)    NOT NULL,
    plant_location          VARCHAR(120),

    -- Material
    material_category       shipping.raw_material_category  NOT NULL,
    material_description    VARCHAR(120),               -- Specific grade / origin

    -- Stock quantities (all in metric tonnes)
    stock_date              DATE            NOT NULL,
    opening_stock_mt        NUMERIC(12, 3)  NOT NULL DEFAULT 0,
    receipts_mt             NUMERIC(12, 3)  NOT NULL DEFAULT 0,   -- Discharge from vessel today
    consumption_mt          NUMERIC(12, 3)  NOT NULL DEFAULT 0,   -- Blast furnace burn today
    closing_stock_mt        NUMERIC(12, 3) GENERATED ALWAYS AS
        (opening_stock_mt + receipts_mt - consumption_mt) STORED,

    -- Demand forecast
    daily_consumption_rate_mt   NUMERIC(10, 3),         -- Rolling 30-day average burn rate
    projected_receipts_mt       NUMERIC(12, 3) DEFAULT 0, -- Expected from vessels in transit

    -- Runway calculation (computed, stored for alert watchdogs)
    stock_runway_days       NUMERIC(8, 2) GENERATED ALWAYS AS
        (CASE
            WHEN daily_consumption_rate_mt IS NOT NULL
                 AND daily_consumption_rate_mt > 0
            THEN (opening_stock_mt + receipts_mt - consumption_mt + projected_receipts_mt)
                 / daily_consumption_rate_mt
            ELSE NULL
         END) STORED,

    -- Safety classification
    safety_status           shipping.stock_safety_status    NOT NULL DEFAULT 'ADEQUATE',

    -- Incoming vessels contributing to projected_receipts
    contributing_voyage_id  UUID,                       -- FK -> vessel_voyages (optional, for drill-down)

    -- Audit
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    recorded_by             UUID,                       -- FK -> auth_user

    CONSTRAINT plant_inventory_pkey PRIMARY KEY (inventory_id)
);

COMMENT ON TABLE  shipping.plant_inventory IS
    'Daily raw material stock snapshot per steel plant and material category. Drives runway calculations.';
COMMENT ON COLUMN shipping.plant_inventory.closing_stock_mt IS
    'Closing stock = opening + receipts - consumption. Stored generated column.';
COMMENT ON COLUMN shipping.plant_inventory.stock_runway_days IS
    'Days until stock-out at current burn rate, including projected receipts. Stored generated column.';
COMMENT ON COLUMN shipping.plant_inventory.plant_code IS
    'SAIL plant codes: BSP=Bhilai, DSP=Durgapur, RSP=Rourkela, BSL=Bokaro, ISP=IISCO (Burnpur).';

-- ===========================================================================
-- TABLE 7: shipping_documents
-- Document vault: tracks every shipping document uploaded to MinIO.
-- SHA-256 digest provides tamper-evident integrity.
-- ===========================================================================
CREATE TABLE shipping.shipping_documents (
    -- Surrogate primary key
    document_id             UUID            NOT NULL DEFAULT gen_random_uuid(),

    -- Document classification
    document_type           shipping.document_type          NOT NULL,
    document_title          VARCHAR(255)    NOT NULL,
    document_description    TEXT,

    -- Voyage / contract association (at least one must be non-null)
    voyage_id               UUID,                       -- FK -> vessel_voyages
    contract_id             UUID,                       -- FK -> charter_contracts

    -- File metadata
    original_filename       VARCHAR(255)    NOT NULL,
    file_extension          VARCHAR(20),
    file_size_bytes         BIGINT,
    mime_type               VARCHAR(120),

    -- MinIO object store coordinates
    minio_bucket            VARCHAR(63)     NOT NULL,   -- S3 bucket name rules
    minio_object_key        VARCHAR(1024)   NOT NULL,   -- Full object key / path
    minio_version_id        VARCHAR(64),                -- Object versioning ID

    -- Integrity
    sha256_hash_client      CHAR(64),                   -- Browser-computed (pre-upload)
    sha256_hash_server      CHAR(64),                   -- Server-verified (post-upload)
    verification_status     shipping.document_verification_status   NOT NULL DEFAULT 'PENDING_UPLOAD',
    verified_at             TIMESTAMPTZ,
    verification_mismatch_detail TEXT,                  -- If HASH_MISMATCH, why

    -- Dates
    document_date           DATE,                       -- Date on the face of the document
    issue_date              DATE,
    expiry_date             DATE,

    -- Versioning (documents can be superseded but never deleted from MinIO)
    version_number          INTEGER         NOT NULL DEFAULT 1,
    supersedes_document_id  UUID,                       -- FK -> shipping_documents (self-ref)

    -- Access control
    is_confidential         BOOLEAN         NOT NULL DEFAULT FALSE,
    accessible_roles        shipping.user_role[]         DEFAULT ARRAY[]::shipping.user_role[],

    -- Audit
    uploaded_by             UUID,                       -- FK -> auth_user
    uploaded_at             TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    approved_by             UUID,                       -- FK -> auth_user
    approved_at             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT shipping_documents_pkey PRIMARY KEY (document_id)
);

COMMENT ON TABLE  shipping.shipping_documents IS
    'Tamper-evident document vault backed by MinIO. SHA-256 digest chain provides cryptographic integrity.';
COMMENT ON COLUMN shipping.shipping_documents.sha256_hash_client IS
    'SHA-256 hex digest computed in the browser via Web Crypto API before upload begins.';
COMMENT ON COLUMN shipping.shipping_documents.sha256_hash_server IS
    'SHA-256 hex digest computed by Celery worker after the file is written to MinIO. Must match client hash.';
COMMENT ON COLUMN shipping.shipping_documents.minio_object_key IS
    'Full MinIO object key. Convention: {document_type}/{year}/{month}/{document_id}/{original_filename}';

COMMIT;
