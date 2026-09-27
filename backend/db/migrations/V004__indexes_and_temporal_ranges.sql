-- =============================================================================
-- Migration: V004__indexes_and_temporal_ranges.sql
-- Project:   Overseas Bulk Vessel Chartering & Logistics Management Dashboard
-- Cycle:     1.1.3 — Advanced Indexing & Temporal Ranges
-- Author:    Antigravity / SIH26006 Engineering Team
-- DB:        PostgreSQL 16
-- Depends:   V003__integrity_constraints.sql
-- Description:
--   Constructs the full index layer for production query performance.
--   Organised into six sections:
--
--   A. vessels            — IMO lookups, fleet registry scans
--   B. charter_contracts  — status filtering, vessel/date queries
--   C. vessel_voyages     — active fleet dashboard, port/ETA queries,
--                           tstzrange berth conflict detection
--   D. statement_of_facts — chronological event retrieval per voyage
--   E. laytime_calculations — current-revision lookup, outcome queries
--   F. plant_inventory    — plant+material stock queries, runway alerts
--   G. shipping_documents — document vault lookup, SHA-256 uniqueness,
--                           verification status queues
--
--   Index naming convention:
--     idx_<table>_<columns>              — B-tree (default)
--     idx_<table>_<column>_gin           — GIN (full-text / array)
--     idx_<table>_<column>_gist          — GiST (ranges, geometry)
--     uidx_<table>_<columns>             — Partial UNIQUE index
--     idx_<table>_<column>_brin          — BRIN (large append-only tables)
--
--   tstzrange strategy for berth conflict detection:
--     The `berth_occupancy` range [berthed_at, discharge_completed_at) is
--     stored as a computed tstzrange expression index. Conflict detection
--     (Cycle 2.3.1) uses the && (overlap) operator against this index for
--     sub-millisecond berth scheduling queries.
-- =============================================================================

BEGIN;

SET LOCAL search_path TO shipping, public;

-- ===========================================================================
-- SECTION A: vessels
-- ===========================================================================

-- A.1  IMO number lookup (exact match — the primary vessel lookup key)
--      Already covered by UQ_VESSELS_IMO_NUMBER, but add explicit
--      name for clarity in EXPLAIN plans.
--      NOTE: The unique constraint creates an implicit index; we add a
--      separate named partial index for active vessels to support the
--      common "find active vessel by IMO" path used in API validation.
CREATE INDEX idx_vessels_imo_active
    ON shipping.vessels (imo_number)
    WHERE is_active = TRUE;

COMMENT ON INDEX shipping.idx_vessels_imo_active IS
    'Partial index: active-vessel IMO lookup. Used by charter contract creation to validate IMO.';

-- A.2  Vessel name text search (case-insensitive prefix scan)
CREATE INDEX idx_vessels_name_lower
    ON shipping.vessels (lower(vessel_name));

COMMENT ON INDEX shipping.idx_vessels_name_lower IS
    'Case-insensitive vessel name lookup for search-as-you-type UI.';

-- A.3  Size class + flag state — fleet registry filter panel
CREATE INDEX idx_vessels_size_class_flag
    ON shipping.vessels (size_class, flag_state)
    WHERE is_active = TRUE;

COMMENT ON INDEX shipping.idx_vessels_size_class_flag IS
    'Fleet registry filter: size class and flag state for active vessels.';

-- A.4  Owner entity — broker / owner management views
CREATE INDEX idx_vessels_owner_name_lower
    ON shipping.vessels (lower(owner_name));

COMMENT ON INDEX shipping.idx_vessels_owner_name_lower IS
    'Owner name lookup for vessel ownership management panel.';

-- A.5  Build year — age-based fleet analytics
CREATE INDEX idx_vessels_build_year
    ON shipping.vessels (build_year DESC);

COMMENT ON INDEX shipping.idx_vessels_build_year IS
    'Fleet age analytics: descending build year for youngest-first ordering.';

-- ===========================================================================
-- SECTION B: charter_contracts
-- ===========================================================================

-- B.1  Contract status + vessel — primary active contracts dashboard query
--      "Show all ACTIVE contracts for a given vessel"
CREATE INDEX idx_contracts_status_vessel
    ON shipping.charter_contracts (contract_status, vessel_id);

COMMENT ON INDEX shipping.idx_contracts_status_vessel IS
    'Active contracts dashboard: filter by status, then join to vessel.';

-- B.2  Active contracts by laycan window — procurement calendar
--      "Show contracts with laycan overlapping a given date range"
CREATE INDEX idx_contracts_laycan
    ON shipping.charter_contracts (laycan_start, laycan_end)
    WHERE contract_status NOT IN ('CANCELLED', 'COMPLETED');

COMMENT ON INDEX shipping.idx_contracts_laycan IS
    'Procurement calendar: open contracts by laycan window.';

-- B.3  Charterer entity — multi-plant SAIL procurement dashboard
CREATE INDEX idx_contracts_charterer_plant
    ON shipping.charter_contracts (charterer_entity, charterer_plant)
    WHERE contract_status NOT IN ('CANCELLED', 'COMPLETED');

COMMENT ON INDEX shipping.idx_contracts_charterer_plant IS
    'Multi-plant procurement view: contracts grouped by charterer and plant.';

-- B.4  Cargo type — commodity-specific contract analysis
CREATE INDEX idx_contracts_cargo_type_status
    ON shipping.charter_contracts (cargo_type, contract_status);

COMMENT ON INDEX shipping.idx_contracts_cargo_type_status IS
    'Commodity lens: all contracts for a given cargo type, by status.';

-- B.5  Created at — audit log chronological ordering
CREATE INDEX idx_contracts_created_at
    ON shipping.charter_contracts (created_at DESC);

COMMENT ON INDEX shipping.idx_contracts_created_at IS
    'Chronological contract listing for recent-contracts panel.';

-- B.6  Discharge port — port-centric analytics
CREATE INDEX idx_contracts_discharge_port
    ON shipping.charter_contracts (discharge_port, contract_status);

COMMENT ON INDEX shipping.idx_contracts_discharge_port IS
    'Port-centric view: all contracts routing to a given discharge port.';

-- ===========================================================================
-- SECTION C: vessel_voyages
-- ===========================================================================

-- C.1  Voyage status — active fleet dashboard (primary hot path)
--      "Get all non-terminal voyages" — runs on every dashboard refresh
CREATE INDEX idx_voyages_status
    ON shipping.vessel_voyages (voyage_status)
    WHERE voyage_status NOT IN ('COMPLETED', 'CANCELLED');

COMMENT ON INDEX shipping.idx_voyages_status IS
    'Hot path: active fleet dashboard. Partial index excludes terminal voyages.';

-- C.2  Contract + status — all voyages under a contract
CREATE INDEX idx_voyages_contract_status
    ON shipping.vessel_voyages (contract_id, voyage_status);

COMMENT ON INDEX shipping.idx_voyages_contract_status IS
    'Contract detail: all voyages under a charter contract, by status.';

-- C.3  Vessel + status — vessel voyage history
CREATE INDEX idx_voyages_vessel_status
    ON shipping.vessel_voyages (vessel_id, voyage_status);

COMMENT ON INDEX shipping.idx_voyages_vessel_status IS
    'Vessel history: all voyages for a given vessel, by status.';

-- C.4  ETA window — ETA deviation watchdog (Celery Beat, Cycle 4.3.2)
--      Scans for voyages where ETA is within the next N hours
CREATE INDEX idx_voyages_eta_active
    ON shipping.vessel_voyages (eta_date)
    WHERE voyage_status NOT IN ('COMPLETED', 'CANCELLED', 'SCHEDULED');

COMMENT ON INDEX shipping.idx_voyages_eta_active IS
    'ETA watchdog: active voyages ordered by ETA for deviation alerting.';

-- C.5  Discharge port + status — port operations console
CREATE INDEX idx_voyages_discharge_port_status
    ON shipping.vessel_voyages (discharge_port, voyage_status);

COMMENT ON INDEX shipping.idx_voyages_discharge_port_status IS
    'Port operations: all active voyages at a given discharge port.';

-- C.6  NOR tendered timestamp — laytime calculation trigger lookup
--      The engine needs to find voyages where NOR has been tendered
--      but laytime has not yet been calculated.
CREATE INDEX idx_voyages_nor_tendered
    ON shipping.vessel_voyages (nor_tendered_at)
    WHERE nor_tendered_at IS NOT NULL
      AND voyage_status NOT IN ('COMPLETED', 'CANCELLED');

COMMENT ON INDEX shipping.idx_voyages_nor_tendered IS
    'Laytime engine trigger: voyages with NOR tendered awaiting calculation.';

-- C.7  Discharge completed — settlement queue
--      Voyages ready for demurrage / despatch settlement
CREATE INDEX idx_voyages_discharge_completed
    ON shipping.vessel_voyages (discharge_completed_at DESC)
    WHERE discharge_completed_at IS NOT NULL
      AND voyage_status NOT IN ('CANCELLED');

COMMENT ON INDEX shipping.idx_voyages_discharge_completed IS
    'Settlement queue: recently completed discharges ordered by completion time.';

-- C.8  ── TEMPORAL RANGE INDEX: BERTH CONFLICT DETECTION ──────────────────
--
--      PostgreSQL 16 expression index on a tstzrange formed from
--      [berthed_at, discharge_completed_at) for each voyage.
--
--      The GiST index enables the && (overlap) operator to run in O(log n)
--      rather than O(n) full-table scans. This is the performance backbone
--      of Cycle 2.3.1 (Berth Conflict Detection Engine).
--
--      Query pattern used by the conflict engine:
--        SELECT voyage_id
--        FROM   shipping.vessel_voyages
--        WHERE  tstzrange(berthed_at, discharge_completed_at, '[)')
--               && tstzrange($proposed_start, $proposed_end, '[)')
--          AND  discharge_berth = $berth_name
--          AND  voyage_status NOT IN ('COMPLETED', 'CANCELLED');
--
--      Partial condition: only index voyages with both endpoints set
--      AND that are in an active berthing state.

CREATE INDEX idx_voyages_berth_occupancy_gist
    ON shipping.vessel_voyages
    USING GIST (
        tstzrange(berthed_at, discharge_completed_at, '[)'),
        discharge_berth
    )
    WHERE berthed_at IS NOT NULL
      AND voyage_status NOT IN ('COMPLETED', 'CANCELLED');

COMMENT ON INDEX shipping.idx_voyages_berth_occupancy_gist IS
    'GiST range index for berth conflict detection. Supports tstzrange && overlap queries '
    'in O(log n). Used by Cycle 2.3.1 berth allocation engine. Partial: only active berthed voyages.';

-- C.9  Berth + discharge port — berth scheduling timeline (Gantt chart)
--      Composite index for Gantt chart data retrieval
CREATE INDEX idx_voyages_berth_port_eta
    ON shipping.vessel_voyages (discharge_port, discharge_berth, eta_date)
    WHERE voyage_status NOT IN ('CANCELLED');

COMMENT ON INDEX shipping.idx_voyages_berth_port_eta IS
    'Gantt chart data retrieval: voyages by port, berth and ETA for timeline rendering.';

-- C.10  Last position updated — real-time vessel tracking
CREATE INDEX idx_voyages_position_updated
    ON shipping.vessel_voyages (last_position_updated DESC)
    WHERE last_position_updated IS NOT NULL
      AND voyage_status NOT IN ('COMPLETED', 'CANCELLED');

COMMENT ON INDEX shipping.idx_voyages_position_updated IS
    'Real-time tracking: most recently updated vessel positions.';

-- C.11  Cargo type via contract join — commodity-level fleet view
--       Supporting JOIN-heavy query: voyages + cargo type for dashboard filter
CREATE INDEX idx_voyages_contract_vessel
    ON shipping.vessel_voyages (contract_id, vessel_id);

COMMENT ON INDEX shipping.idx_voyages_contract_vessel IS
    'Join acceleration: voyage to contract and vessel FK lookups.';

-- C.12  ATA date — arrival analytics and port dwell time reporting
CREATE INDEX idx_voyages_ata_date
    ON shipping.vessel_voyages (ata_date DESC)
    WHERE ata_date IS NOT NULL;

COMMENT ON INDEX shipping.idx_voyages_ata_date IS
    'Arrival analytics: descending ATA for recent arrivals dashboard panel.';

-- ===========================================================================
-- SECTION D: statement_of_facts
-- ===========================================================================

-- D.1  Voyage + sequence — the primary SOF retrieval path
--      "Get all SOF events for voyage X in chronological order"
--      (Also enforced by UQ_SOF_VOYAGE_SEQUENCE, which creates an implicit
--       index, but naming it explicitly aids EXPLAIN plan readability.)
CREATE INDEX idx_sof_voyage_seq
    ON shipping.statement_of_facts (voyage_id, sequence_number ASC);

COMMENT ON INDEX shipping.idx_sof_voyage_seq IS
    'Primary SOF retrieval: all events for a voyage in sequence order. Core laytime engine scan.';

-- D.2  Voyage + event type — event-specific lookups
--      "Find NOR_TENDERED event for voyage X"
CREATE INDEX idx_sof_voyage_event_type
    ON shipping.statement_of_facts (voyage_id, event_type);

COMMENT ON INDEX shipping.idx_sof_voyage_event_type IS
    'Event-type lookup: find specific event types (NOR, DISCHARGE_COMMENCED) for a voyage.';

-- D.3  Event timestamp — chronological ordering across all voyages (admin view)
CREATE INDEX idx_sof_event_timestamp
    ON shipping.statement_of_facts (event_timestamp DESC);

COMMENT ON INDEX shipping.idx_sof_event_timestamp IS
    'Global SOF chronological view for cross-voyage operational audit.';

-- D.4  Interruption events — weather/breakdown interval analysis
--      Partial index: only interruption-type events for the WWD evaluator
CREATE INDEX idx_sof_interruptions
    ON shipping.statement_of_facts (voyage_id, event_timestamp)
    WHERE event_type IN (
        'RAIN_COMMENCED', 'RAIN_CEASED',
        'WIND_FORCE_EXCEEDED_LIMIT', 'WIND_FORCE_WITHIN_LIMIT',
        'CRANE_BREAKDOWN_COMMENCED', 'CRANE_BREAKDOWN_RESUMED',
        'POWER_FAILURE_COMMENCED', 'POWER_FAILURE_RESUMED',
        'LABOUR_DISPUTE_COMMENCED', 'LABOUR_DISPUTE_ENDED'
    );

COMMENT ON INDEX shipping.idx_sof_interruptions IS
    'Interruption event partial index: feeds the WWD/SHEX interval evaluator in Cycle 1.2.4.';

-- D.5  Count status — laytime interval audit queries
CREATE INDEX idx_sof_count_status
    ON shipping.statement_of_facts (voyage_id, count_status)
    WHERE count_status IS NOT NULL;

COMMENT ON INDEX shipping.idx_sof_count_status IS
    'Laytime audit ledger: filter SOF intervals by counting status (FULLY_COUNTED, EXCLUDED, etc.).';

-- D.6  Amendments — amendment chain traversal
CREATE INDEX idx_sof_amendments
    ON shipping.statement_of_facts (amended_sof_id)
    WHERE is_amendment = TRUE;

COMMENT ON INDEX shipping.idx_sof_amendments IS
    'Amendment chain: find all SOF corrections referencing a given original row.';

-- D.7  Submitted at — recent submissions panel
CREATE INDEX idx_sof_submitted_at
    ON shipping.statement_of_facts (submitted_at DESC);

COMMENT ON INDEX shipping.idx_sof_submitted_at IS
    'Recent SOF submissions: descending for port agent activity feed.';

-- ===========================================================================
-- SECTION E: laytime_calculations
-- ===========================================================================

-- E.1  ── PARTIAL UNIQUE INDEX: ONE CURRENT REVISION PER VOYAGE ───────────
--
--      Ensures that at most one laytime_calculations row per voyage has
--      is_current = TRUE. This is the authoritative enforcement mechanism
--      for the "current calculation" invariant — the plain UNIQUE constraint
--      in V003 covers (voyage_id, revision) but not the is_current flag.
--
--      This partial unique index is the canonical DB-level guard that makes
--      the Celery calculation engine safe for concurrent re-submissions.

CREATE UNIQUE INDEX uidx_laytime_one_current_per_voyage
    ON shipping.laytime_calculations (voyage_id)
    WHERE is_current = TRUE;

COMMENT ON INDEX shipping.uidx_laytime_one_current_per_voyage IS
    'CRITICAL: Enforces exactly one is_current=TRUE calculation row per voyage. '
    'Celery worker must set is_current=FALSE on previous revision before inserting new one.';

-- E.2  Voyage + revision — lookup specific historical revision
CREATE INDEX idx_laytime_voyage_revision
    ON shipping.laytime_calculations (voyage_id, revision DESC);

COMMENT ON INDEX shipping.idx_laytime_voyage_revision IS
    'Historical revision retrieval: find specific calculation revision for a voyage.';

-- E.3  Outcome + calculated_at — settlement queue and demurrage analytics
CREATE INDEX idx_laytime_outcome_calculated
    ON shipping.laytime_calculations (outcome, calculated_at DESC)
    WHERE is_current = TRUE;

COMMENT ON INDEX shipping.idx_laytime_outcome_calculated IS
    'Settlement queue: current calculations by outcome (DEMURRAGE/DESPATCH), newest first.';

-- E.4  Pending calculations — Celery worker polling
--      "Find all voyages with PENDING or IN_PROGRESS calculations"
CREATE INDEX idx_laytime_pending
    ON shipping.laytime_calculations (outcome, voyage_id)
    WHERE outcome IN ('PENDING', 'IN_PROGRESS')
      AND is_current = TRUE;

COMMENT ON INDEX shipping.idx_laytime_pending IS
    'Celery worker poll: pending/in-progress calculations awaiting engine execution.';

-- E.5  Net settlement — financial reporting (largest demurrage claims first)
CREATE INDEX idx_laytime_net_settlement
    ON shipping.laytime_calculations (net_settlement_usd DESC NULLS LAST)
    WHERE is_current = TRUE AND outcome IN ('DEMURRAGE', 'DESPATCH', 'SETTLED');

COMMENT ON INDEX shipping.idx_laytime_net_settlement IS
    'Financial reporting: rank settled voyages by net settlement amount.';

-- ===========================================================================
-- SECTION F: plant_inventory
-- ===========================================================================

-- F.1  Plant + material + stock date — primary inventory lookup
--      (Also covered by UQ_INVENTORY_PLANT_MATERIAL_DATE, named separately)
CREATE INDEX idx_inventory_plant_material_date
    ON shipping.plant_inventory (plant_code, material_category, stock_date DESC);

COMMENT ON INDEX shipping.idx_inventory_plant_material_date IS
    'Primary inventory query: latest stock level for a plant and material.';

-- F.2  Safety status — critical stock alert watchdog (Cycle 4.3.1)
--      "Find all CRITICAL or PRECAUTIONARY stock records"
CREATE INDEX idx_inventory_safety_status
    ON shipping.plant_inventory (safety_status, stock_date DESC)
    WHERE safety_status IN ('CRITICAL', 'PRECAUTIONARY', 'WATCH');

COMMENT ON INDEX shipping.idx_inventory_safety_status IS
    'Alert watchdog partial index: critical and precautionary stock levels for Celery alerting.';

-- F.3  Stock runway — low-runway sorting for dashboard gauge
CREATE INDEX idx_inventory_runway_asc
    ON shipping.plant_inventory (stock_runway_days ASC NULLS LAST)
    WHERE safety_status != 'SURPLUS';

COMMENT ON INDEX shipping.idx_inventory_runway_asc IS
    'Stock runway gauge: materials sorted by ascending runway days for triage view.';

-- F.4  Plant code — plant-level aggregation
CREATE INDEX idx_inventory_plant_date
    ON shipping.plant_inventory (plant_code, stock_date DESC);

COMMENT ON INDEX shipping.idx_inventory_plant_date IS
    'Plant-level stock aggregation: all materials for a plant on a given date.';

-- F.5  Contributing voyage — reverse lookup: which inventory rows does a voyage feed?
CREATE INDEX idx_inventory_contributing_voyage
    ON shipping.plant_inventory (contributing_voyage_id)
    WHERE contributing_voyage_id IS NOT NULL;

COMMENT ON INDEX shipping.idx_inventory_contributing_voyage IS
    'Reverse lookup: inventory records associated with an incoming vessel voyage.';

-- ===========================================================================
-- SECTION G: shipping_documents
-- ===========================================================================

-- G.1  Voyage + document type — document vault retrieval for a voyage
CREATE INDEX idx_documents_voyage_type
    ON shipping.shipping_documents (voyage_id, document_type)
    WHERE voyage_id IS NOT NULL;

COMMENT ON INDEX shipping.idx_documents_voyage_type IS
    'Document vault: all documents of a given type for a voyage.';

-- G.2  Contract + document type — contract document library
CREATE INDEX idx_documents_contract_type
    ON shipping.shipping_documents (contract_id, document_type)
    WHERE contract_id IS NOT NULL;

COMMENT ON INDEX shipping.idx_documents_contract_type IS
    'Contract document library: all documents of a given type for a contract.';

-- G.3  Verification status — integrity verification queue
--      "Find all documents awaiting hash verification"
CREATE INDEX idx_documents_verification_status
    ON shipping.shipping_documents (verification_status, uploaded_at ASC)
    WHERE verification_status IN ('UPLOAD_COMPLETE', 'PENDING_UPLOAD');

COMMENT ON INDEX shipping.idx_documents_verification_status IS
    'Verification queue: documents awaiting Celery SHA-256 digest verification, oldest first.';

-- G.4  ── PARTIAL UNIQUE INDEX: SHA-256 DEDUPLICATION ────────────────────
--
--      Prevents duplicate uploads of the same file content. Once a document
--      has been hash-verified, no other document in the same bucket may share
--      the server-computed SHA-256 digest.
--
--      Partial condition: only on verified documents (HASH_VERIFIED, APPROVED)
--      to avoid blocking parallel pending uploads of the same file before
--      verification completes.

CREATE UNIQUE INDEX uidx_documents_sha256_verified
    ON shipping.shipping_documents (minio_bucket, sha256_hash_server)
    WHERE sha256_hash_server IS NOT NULL
      AND verification_status IN ('HASH_VERIFIED', 'APPROVED');

COMMENT ON INDEX shipping.uidx_documents_sha256_verified IS
    'DEDUPLICATION: Prevents storing two verified files with identical SHA-256 digest in the same bucket.';

-- G.5  Hash mismatch — security quarantine queue
CREATE INDEX idx_documents_hash_mismatch
    ON shipping.shipping_documents (uploaded_at DESC)
    WHERE verification_status = 'HASH_MISMATCH';

COMMENT ON INDEX shipping.idx_documents_hash_mismatch IS
    'Security quarantine queue: hash-mismatched documents for admin review.';

-- G.6  Document type + date — document date-range filtering
CREATE INDEX idx_documents_type_date
    ON shipping.shipping_documents (document_type, document_date DESC)
    WHERE document_date IS NOT NULL;

COMMENT ON INDEX shipping.idx_documents_type_date IS
    'Date-range document filter: find documents of a given type within a date window.';

-- G.7  Supersedes chain — version history traversal
CREATE INDEX idx_documents_supersedes
    ON shipping.shipping_documents (supersedes_document_id)
    WHERE supersedes_document_id IS NOT NULL;

COMMENT ON INDEX shipping.idx_documents_supersedes IS
    'Version history: find documents that supersede a given document ID.';

-- G.8  Uploaded by + uploaded at — per-user upload audit trail
CREATE INDEX idx_documents_uploaded_by
    ON shipping.shipping_documents (uploaded_by, uploaded_at DESC)
    WHERE uploaded_by IS NOT NULL;

COMMENT ON INDEX shipping.idx_documents_uploaded_by IS
    'User audit trail: all documents uploaded by a specific user, newest first.';

-- G.9  Confidential documents — access control filter
CREATE INDEX idx_documents_confidential
    ON shipping.shipping_documents (uploaded_at DESC)
    WHERE is_confidential = TRUE;

COMMENT ON INDEX shipping.idx_documents_confidential IS
    'Confidential document index: supports access-controlled document listing.';

-- G.10  BRIN index on uploaded_at — efficient range scans on large document tables
--       BRIN is ideal for append-only columns with strong physical correlation.
--       Complements the B-tree indexes for large date-range aggregation queries.
CREATE INDEX idx_documents_uploaded_at_brin
    ON shipping.shipping_documents
    USING BRIN (uploaded_at)
    WITH (pages_per_range = 32);

COMMENT ON INDEX shipping.idx_documents_uploaded_at_brin IS
    'BRIN index: efficient date-range scans on uploaded_at for large document tables. '
    'Complements B-tree for analytics queries spanning months of uploads.';

-- ===========================================================================
-- SECTION H: Cross-cutting — updated_at maintenance
--   All tables carry updated_at. Add BRIN indexes to support efficient
--   incremental sync queries (e.g., "changed since last poll").
-- ===========================================================================

CREATE INDEX idx_vessels_updated_at_brin
    ON shipping.vessels
    USING BRIN (updated_at)
    WITH (pages_per_range = 32);

CREATE INDEX idx_contracts_updated_at_brin
    ON shipping.charter_contracts
    USING BRIN (updated_at)
    WITH (pages_per_range = 32);

CREATE INDEX idx_voyages_updated_at_brin
    ON shipping.vessel_voyages
    USING BRIN (updated_at)
    WITH (pages_per_range = 32);

CREATE INDEX idx_laytime_updated_at_brin
    ON shipping.laytime_calculations
    USING BRIN (updated_at)
    WITH (pages_per_range = 32);

CREATE INDEX idx_inventory_updated_at_brin
    ON shipping.plant_inventory
    USING BRIN (updated_at)
    WITH (pages_per_range = 32);

COMMENT ON INDEX shipping.idx_vessels_updated_at_brin    IS 'BRIN: incremental sync on vessels.updated_at.';
COMMENT ON INDEX shipping.idx_contracts_updated_at_brin  IS 'BRIN: incremental sync on charter_contracts.updated_at.';
COMMENT ON INDEX shipping.idx_voyages_updated_at_brin    IS 'BRIN: incremental sync on vessel_voyages.updated_at.';
COMMENT ON INDEX shipping.idx_laytime_updated_at_brin    IS 'BRIN: incremental sync on laytime_calculations.updated_at.';
COMMENT ON INDEX shipping.idx_inventory_updated_at_brin  IS 'BRIN: incremental sync on plant_inventory.updated_at.';

COMMIT;
