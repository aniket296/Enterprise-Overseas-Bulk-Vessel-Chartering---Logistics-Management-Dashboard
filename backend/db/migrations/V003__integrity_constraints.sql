-- =============================================================================
-- Migration: V003__integrity_constraints.sql
-- Project:   Overseas Bulk Vessel Chartering & Logistics Management Dashboard
-- Cycle:     1.1.2 — Database Integrity Constraints
-- Author:    Antigravity / SIH26006 Engineering Team
-- DB:        PostgreSQL 16
-- Depends:   V002__core_tables.sql
-- Description:
--   Adds all referential integrity and domain-validity constraints to the
--   seven core tables.  Organised into four sections:
--
--   A. CHECK constraints  — domain invariants on individual columns/rows
--   B. FOREIGN KEY constraints — relational integrity with explicit
--      CASCADE / RESTRICT / SET NULL semantics
--   C. UNIQUE constraints — business-key uniqueness (not covered by PKs)
--   D. NOT NULL / DEFAULT back-fills — any late-binding defaults that
--      require the FK graph to exist first
--
--   Every constraint is named explicitly (never unnamed) to enable precise
--   error messages and targeted future migrations.
-- =============================================================================

BEGIN;

SET LOCAL search_path TO shipping, public;

-- ===========================================================================
-- SECTION A: CHECK CONSTRAINTS
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- A.1  vessels
-- ---------------------------------------------------------------------------

-- IMO number: exactly 7 decimal digits (IMO standard)
ALTER TABLE shipping.vessels
    ADD CONSTRAINT chk_vessels_imo_digits
        CHECK (imo_number ~ '^[0-9]{7}$');

-- All physical dimensions must be strictly positive
ALTER TABLE shipping.vessels
    ADD CONSTRAINT chk_vessels_deadweight_positive
        CHECK (deadweight_tonnes > 0);

ALTER TABLE shipping.vessels
    ADD CONSTRAINT chk_vessels_gross_tonnage_positive
        CHECK (gross_tonnage > 0);

ALTER TABLE shipping.vessels
    ADD CONSTRAINT chk_vessels_net_tonnage_positive
        CHECK (net_tonnage > 0);

ALTER TABLE shipping.vessels
    ADD CONSTRAINT chk_vessels_loa_positive
        CHECK (loa_metres > 0);

ALTER TABLE shipping.vessels
    ADD CONSTRAINT chk_vessels_beam_positive
        CHECK (beam_metres > 0);

ALTER TABLE shipping.vessels
    ADD CONSTRAINT chk_vessels_draft_positive
        CHECK (draft_laden_metres > 0);

-- Hold count must be between 1 and 12 (realistic for bulk carriers)
ALTER TABLE shipping.vessels
    ADD CONSTRAINT chk_vessels_hold_count_range
        CHECK (hold_count BETWEEN 1 AND 12);

-- Hatch count must match or be less than hold count
ALTER TABLE shipping.vessels
    ADD CONSTRAINT chk_vessels_hatch_le_hold
        CHECK (hatch_count <= hold_count AND hatch_count >= 1);

-- Build year: no vessel before 1900, none after current year + 3 (under construction)
ALTER TABLE shipping.vessels
    ADD CONSTRAINT chk_vessels_build_year_range
        CHECK (build_year BETWEEN 1900 AND EXTRACT(YEAR FROM NOW())::SMALLINT + 3);

-- ISO 3166-1 alpha-2 flag state: exactly 2 uppercase letters
ALTER TABLE shipping.vessels
    ADD CONSTRAINT chk_vessels_flag_state_format
        CHECK (flag_state ~ '^[A-Z]{2}$');

-- GT must be less than or equal to DWT for typical bulk carriers
-- (GT is volumetric; DWT is mass—for bulk carriers GT < DWT is typical)
ALTER TABLE shipping.vessels
    ADD CONSTRAINT chk_vessels_gt_le_dwt
        CHECK (gross_tonnage <= deadweight_tonnes * 1.5); -- generous headroom for outliers

-- Service speed: realistic range for bulk carriers (5–25 knots)
ALTER TABLE shipping.vessels
    ADD CONSTRAINT chk_vessels_service_speed_range
        CHECK (service_speed_knots IS NULL OR service_speed_knots BETWEEN 5 AND 25);

-- ---------------------------------------------------------------------------
-- A.2  charter_contracts
-- ---------------------------------------------------------------------------

-- Laycan window: start must be on or before end
ALTER TABLE shipping.charter_contracts
    ADD CONSTRAINT chk_contracts_laycan_window
        CHECK (laycan_start <= laycan_end);

-- Laycan window cannot exceed 31 days (industry norm)
ALTER TABLE shipping.charter_contracts
    ADD CONSTRAINT chk_contracts_laycan_max_window
        CHECK ((laycan_end - laycan_start) <= 31);

-- Cargo quantity must be positive
ALTER TABLE shipping.charter_contracts
    ADD CONSTRAINT chk_contracts_quantity_positive
        CHECK (nominated_quantity_mt > 0);

-- Tolerance percentage: 0–15% (industry max is ±10%, 15% is generous upper bound)
ALTER TABLE shipping.charter_contracts
    ADD CONSTRAINT chk_contracts_tolerance_range
        CHECK (tolerance_pct BETWEEN 0 AND 15);

-- Allowed laytime must be positive
ALTER TABLE shipping.charter_contracts
    ADD CONSTRAINT chk_contracts_laytime_positive
        CHECK (allowed_laytime_hours > 0);

-- Discharge rate must be positive (MT per day)
ALTER TABLE shipping.charter_contracts
    ADD CONSTRAINT chk_contracts_discharge_rate_positive
        CHECK (discharge_rate_mt_per_day > 0);

-- Demurrage rate must be positive (cannot be zero — a zero-demurrage contract
-- is recorded as a very large laytime allowance instead)
ALTER TABLE shipping.charter_contracts
    ADD CONSTRAINT chk_contracts_demurrage_rate_positive
        CHECK (demurrage_rate_usd_per_day > 0);

-- Despatch rate must be non-negative (some contracts have zero despatch)
ALTER TABLE shipping.charter_contracts
    ADD CONSTRAINT chk_contracts_despatch_rate_nonneg
        CHECK (despatch_rate_usd_per_day >= 0);

-- Despatch rate cannot exceed demurrage rate (universal industry norm)
ALTER TABLE shipping.charter_contracts
    ADD CONSTRAINT chk_contracts_despatch_le_demurrage
        CHECK (despatch_rate_usd_per_day <= demurrage_rate_usd_per_day);

-- Freight rate is mandatory for voyage charters
ALTER TABLE shipping.charter_contracts
    ADD CONSTRAINT chk_contracts_voyage_freight_required
        CHECK (
            charter_party_type != 'VOYAGE_CHARTER'
            OR freight_rate_usd_per_mt IS NOT NULL
        );

-- CP date must not precede fixture date (CP is signed after recap)
ALTER TABLE shipping.charter_contracts
    ADD CONSTRAINT chk_contracts_cp_after_fixture
        CHECK (
            cp_date IS NULL
            OR fixture_date IS NULL
            OR cp_date >= fixture_date
        );

-- Discharge country default enforcement: must be 2-letter ISO code
ALTER TABLE shipping.charter_contracts
    ADD CONSTRAINT chk_contracts_discharge_country_format
        CHECK (discharge_country ~ '^[A-Z]{2}$');

ALTER TABLE shipping.charter_contracts
    ADD CONSTRAINT chk_contracts_load_country_format
        CHECK (load_country ~ '^[A-Z]{2}$');

-- ---------------------------------------------------------------------------
-- A.3  vessel_voyages
-- ---------------------------------------------------------------------------

-- Cargo quantity must be positive when set
ALTER TABLE shipping.vessel_voyages
    ADD CONSTRAINT chk_voyages_quantity_loaded_positive
        CHECK (quantity_loaded_mt IS NULL OR quantity_loaded_mt > 0);

ALTER TABLE shipping.vessel_voyages
    ADD CONSTRAINT chk_voyages_quantity_discharged_positive
        CHECK (quantity_discharged_mt IS NULL OR quantity_discharged_mt > 0);

-- BL quantity must be positive when set
ALTER TABLE shipping.vessel_voyages
    ADD CONSTRAINT chk_voyages_bl_quantity_positive
        CHECK (bl_quantity_mt IS NULL OR bl_quantity_mt > 0);

-- ATA cannot precede ETA by more than 7 days (data sanity)
ALTER TABLE shipping.vessel_voyages
    ADD CONSTRAINT chk_voyages_ata_eta_proximity
        CHECK (
            ata_date IS NULL
            OR eta_date IS NULL
            OR ata_date >= eta_date - INTERVAL '7 days'
        );

-- NOR tender must be at or after arrival
ALTER TABLE shipping.vessel_voyages
    ADD CONSTRAINT chk_voyages_nor_after_ata
        CHECK (
            nor_tendered_at IS NULL
            OR ata_date IS NULL
            OR nor_tendered_at >= ata_date
        );

-- NOR accepted must be at or after NOR tendered
ALTER TABLE shipping.vessel_voyages
    ADD CONSTRAINT chk_voyages_nor_accepted_after_tendered
        CHECK (
            nor_accepted_at IS NULL
            OR nor_tendered_at IS NULL
            OR nor_accepted_at >= nor_tendered_at
        );

-- Berthed must be at or after NOR accepted
ALTER TABLE shipping.vessel_voyages
    ADD CONSTRAINT chk_voyages_berthed_after_nor_accepted
        CHECK (
            berthed_at IS NULL
            OR nor_accepted_at IS NULL
            OR berthed_at >= nor_accepted_at
        );

-- Discharge commenced must be at or after berthed
ALTER TABLE shipping.vessel_voyages
    ADD CONSTRAINT chk_voyages_discharge_after_berthed
        CHECK (
            discharge_commenced_at IS NULL
            OR berthed_at IS NULL
            OR discharge_commenced_at >= berthed_at
        );

-- Discharge completed must be at or after commenced
ALTER TABLE shipping.vessel_voyages
    ADD CONSTRAINT chk_voyages_discharge_completed_after_commenced
        CHECK (
            discharge_completed_at IS NULL
            OR discharge_commenced_at IS NULL
            OR discharge_completed_at >= discharge_commenced_at
        );

-- Voyage completed must be at or after discharge completed
ALTER TABLE shipping.vessel_voyages
    ADD CONSTRAINT chk_voyages_completed_after_discharge
        CHECK (
            completed_at IS NULL
            OR discharge_completed_at IS NULL
            OR completed_at >= discharge_completed_at
        );

-- Latitude: −90 to +90
ALTER TABLE shipping.vessel_voyages
    ADD CONSTRAINT chk_voyages_lat_range
        CHECK (
            last_position_lat IS NULL
            OR last_position_lat BETWEEN -90 AND 90
        );

-- Longitude: −180 to +180
ALTER TABLE shipping.vessel_voyages
    ADD CONSTRAINT chk_voyages_lon_range
        CHECK (
            last_position_lon IS NULL
            OR last_position_lon BETWEEN -180 AND 180
        );

-- Speed over ground: 0–35 knots (max realistic for any commercial vessel)
ALTER TABLE shipping.vessel_voyages
    ADD CONSTRAINT chk_voyages_sog_range
        CHECK (
            speed_over_ground IS NULL
            OR speed_over_ground BETWEEN 0 AND 35
        );

-- ATL (actual time of loading) must be before ATA (arrival at discharge port)
ALTER TABLE shipping.vessel_voyages
    ADD CONSTRAINT chk_voyages_atl_before_ata
        CHECK (
            atl_date IS NULL
            OR ata_date IS NULL
            OR atl_date <= ata_date
        );

-- ---------------------------------------------------------------------------
-- A.4  statement_of_facts
-- ---------------------------------------------------------------------------

-- Sequence number must be strictly positive
ALTER TABLE shipping.statement_of_facts
    ADD CONSTRAINT chk_sof_sequence_positive
        CHECK (sequence_number > 0);

-- Count fraction: 0.00 – 1.00 (inclusive)
ALTER TABLE shipping.statement_of_facts
    ADD CONSTRAINT chk_sof_count_fraction_range
        CHECK (
            count_fraction IS NULL
            OR count_fraction BETWEEN 0.00 AND 1.00
        );

-- Counted hours must be non-negative
ALTER TABLE shipping.statement_of_facts
    ADD CONSTRAINT chk_sof_counted_hours_nonneg
        CHECK (counted_hours IS NULL OR counted_hours >= 0);

-- Crane/hatch numbers must be positive when provided
ALTER TABLE shipping.statement_of_facts
    ADD CONSTRAINT chk_sof_crane_number_positive
        CHECK (crane_number IS NULL OR crane_number > 0);

ALTER TABLE shipping.statement_of_facts
    ADD CONSTRAINT chk_sof_hatch_number_positive
        CHECK (hatch_number IS NULL OR hatch_number > 0);

-- Quantity discharged at event must be non-negative
ALTER TABLE shipping.statement_of_facts
    ADD CONSTRAINT chk_sof_quantity_nonneg
        CHECK (
            quantity_discharged_at_event IS NULL
            OR quantity_discharged_at_event >= 0
        );

-- Amendment rows must reference a different SOF row (no self-reference)
ALTER TABLE shipping.statement_of_facts
    ADD CONSTRAINT chk_sof_amendment_no_self_ref
        CHECK (
            amended_sof_id IS NULL
            OR amended_sof_id != sof_id
        );

-- If is_amendment = TRUE, amended_sof_id must be provided
ALTER TABLE shipping.statement_of_facts
    ADD CONSTRAINT chk_sof_amendment_ref_required
        CHECK (
            NOT is_amendment
            OR amended_sof_id IS NOT NULL
        );

-- ---------------------------------------------------------------------------
-- A.5  laytime_calculations
-- ---------------------------------------------------------------------------

-- Revision must be positive
ALTER TABLE shipping.laytime_calculations
    ADD CONSTRAINT chk_laytime_revision_positive
        CHECK (revision > 0);

-- Allowed laytime must be positive
ALTER TABLE shipping.laytime_calculations
    ADD CONSTRAINT chk_laytime_allowed_positive
        CHECK (allowed_laytime_hours > 0);

-- Total counted hours must be non-negative
ALTER TABLE shipping.laytime_calculations
    ADD CONSTRAINT chk_laytime_counted_nonneg
        CHECK (total_counted_hours IS NULL OR total_counted_hours >= 0);

-- Total port hours must be non-negative
ALTER TABLE shipping.laytime_calculations
    ADD CONSTRAINT chk_laytime_port_hours_nonneg
        CHECK (total_port_hours IS NULL OR total_port_hours >= 0);

-- Excepted hours must be non-negative
ALTER TABLE shipping.laytime_calculations
    ADD CONSTRAINT chk_laytime_excepted_nonneg
        CHECK (total_excepted_hours IS NULL OR total_excepted_hours >= 0);

-- Disputed hours must be non-negative
ALTER TABLE shipping.laytime_calculations
    ADD CONSTRAINT chk_laytime_disputed_nonneg
        CHECK (total_disputed_hours >= 0);

-- Port hours ≥ counted hours + excepted hours (counted + excepted ≤ total)
ALTER TABLE shipping.laytime_calculations
    ADD CONSTRAINT chk_laytime_hours_accounting
        CHECK (
            total_port_hours IS NULL
            OR total_counted_hours IS NULL
            OR total_excepted_hours IS NULL
            OR (total_counted_hours + total_excepted_hours) <= total_port_hours + 0.001
            -- 0.001-hour epsilon tolerates floating arithmetic in interim states
        );

-- Financial rates must be positive
ALTER TABLE shipping.laytime_calculations
    ADD CONSTRAINT chk_laytime_demurrage_rate_positive
        CHECK (
            demurrage_rate_usd_per_day IS NULL
            OR demurrage_rate_usd_per_day > 0
        );

ALTER TABLE shipping.laytime_calculations
    ADD CONSTRAINT chk_laytime_despatch_rate_nonneg
        CHECK (
            despatch_rate_usd_per_day IS NULL
            OR despatch_rate_usd_per_day >= 0
        );

-- Gross demurrage / despatch cannot both be non-zero (only one is ever payable)
ALTER TABLE shipping.laytime_calculations
    ADD CONSTRAINT chk_laytime_not_both_demurrage_despatch
        CHECK (
            gross_demurrage_usd IS NULL
            OR gross_despatch_usd IS NULL
            OR gross_demurrage_usd = 0
            OR gross_despatch_usd = 0
        );

-- SOF event count must be positive when set
ALTER TABLE shipping.laytime_calculations
    ADD CONSTRAINT chk_laytime_sof_event_count_positive
        CHECK (sof_event_count IS NULL OR sof_event_count > 0);

-- Last SOF event must be at or after first
ALTER TABLE shipping.laytime_calculations
    ADD CONSTRAINT chk_laytime_sof_event_order
        CHECK (
            first_sof_event_at IS NULL
            OR last_sof_event_at IS NULL
            OR last_sof_event_at >= first_sof_event_at
        );

-- ---------------------------------------------------------------------------
-- A.6  plant_inventory
-- ---------------------------------------------------------------------------

-- All stock quantities must be non-negative
ALTER TABLE shipping.plant_inventory
    ADD CONSTRAINT chk_inventory_opening_stock_nonneg
        CHECK (opening_stock_mt >= 0);

ALTER TABLE shipping.plant_inventory
    ADD CONSTRAINT chk_inventory_receipts_nonneg
        CHECK (receipts_mt >= 0);

ALTER TABLE shipping.plant_inventory
    ADD CONSTRAINT chk_inventory_consumption_nonneg
        CHECK (consumption_mt >= 0);

-- Daily consumption rate must be positive when set
ALTER TABLE shipping.plant_inventory
    ADD CONSTRAINT chk_inventory_consumption_rate_positive
        CHECK (
            daily_consumption_rate_mt IS NULL
            OR daily_consumption_rate_mt > 0
        );

-- Projected receipts must be non-negative
ALTER TABLE shipping.plant_inventory
    ADD CONSTRAINT chk_inventory_projected_receipts_nonneg
        CHECK (projected_receipts_mt >= 0);

-- Plant code: 2–10 uppercase alphanumeric characters
ALTER TABLE shipping.plant_inventory
    ADD CONSTRAINT chk_inventory_plant_code_format
        CHECK (plant_code ~ '^[A-Z0-9]{2,10}$');

-- ---------------------------------------------------------------------------
-- A.7  shipping_documents
-- ---------------------------------------------------------------------------

-- SHA-256 hash: exactly 64 lowercase hex characters
ALTER TABLE shipping.shipping_documents
    ADD CONSTRAINT chk_documents_sha256_client_format
        CHECK (
            sha256_hash_client IS NULL
            OR sha256_hash_client ~ '^[0-9a-f]{64}$'
        );

ALTER TABLE shipping.shipping_documents
    ADD CONSTRAINT chk_documents_sha256_server_format
        CHECK (
            sha256_hash_server IS NULL
            OR sha256_hash_server ~ '^[0-9a-f]{64}$'
        );

-- File size must be positive when provided
ALTER TABLE shipping.shipping_documents
    ADD CONSTRAINT chk_documents_file_size_positive
        CHECK (file_size_bytes IS NULL OR file_size_bytes > 0);

-- Version number must be positive
ALTER TABLE shipping.shipping_documents
    ADD CONSTRAINT chk_documents_version_positive
        CHECK (version_number >= 1);

-- Self-reference: a document cannot supersede itself
ALTER TABLE shipping.shipping_documents
    ADD CONSTRAINT chk_documents_no_self_supersede
        CHECK (
            supersedes_document_id IS NULL
            OR supersedes_document_id != document_id
        );

-- MinIO bucket name: 3–63 lowercase alphanumeric / hyphen (S3 rules)
ALTER TABLE shipping.shipping_documents
    ADD CONSTRAINT chk_documents_minio_bucket_format
        CHECK (minio_bucket ~ '^[a-z0-9][a-z0-9\-]{1,61}[a-z0-9]$');

-- MinIO object key must be non-empty
ALTER TABLE shipping.shipping_documents
    ADD CONSTRAINT chk_documents_minio_key_nonempty
        CHECK (length(trim(minio_object_key)) > 0);

-- At least one of voyage_id or contract_id must be set
ALTER TABLE shipping.shipping_documents
    ADD CONSTRAINT chk_documents_voyage_or_contract
        CHECK (
            voyage_id IS NOT NULL
            OR contract_id IS NOT NULL
        );

-- If HASH_MISMATCH status is set, the mismatch detail must be populated
ALTER TABLE shipping.shipping_documents
    ADD CONSTRAINT chk_documents_mismatch_detail_required
        CHECK (
            verification_status != 'HASH_MISMATCH'
            OR verification_mismatch_detail IS NOT NULL
        );

-- Approved timestamp requires an approver
ALTER TABLE shipping.shipping_documents
    ADD CONSTRAINT chk_documents_approved_by_required
        CHECK (
            approved_at IS NULL
            OR approved_by IS NOT NULL
        );

-- ===========================================================================
-- SECTION B: FOREIGN KEY CONSTRAINTS
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- B.1  charter_contracts → vessels
-- RESTRICT on DELETE: cannot delete a vessel that has active contracts.
-- CASCADE on UPDATE: if vessel_id PK changes (edge case), propagate.
-- ---------------------------------------------------------------------------
ALTER TABLE shipping.charter_contracts
    ADD CONSTRAINT fk_contracts_vessel
        FOREIGN KEY (vessel_id)
        REFERENCES shipping.vessels (vessel_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE;

-- ---------------------------------------------------------------------------
-- B.2  vessel_voyages → charter_contracts
-- RESTRICT on DELETE: cannot delete a contract that has voyages.
-- CASCADE on UPDATE: propagate contract_id changes.
-- ---------------------------------------------------------------------------
ALTER TABLE shipping.vessel_voyages
    ADD CONSTRAINT fk_voyages_contract
        FOREIGN KEY (contract_id)
        REFERENCES shipping.charter_contracts (contract_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE;

-- ---------------------------------------------------------------------------
-- B.3  vessel_voyages → vessels (denormalised copy)
-- RESTRICT on DELETE: vessel must exist.
-- CASCADE on UPDATE: keep in sync.
-- ---------------------------------------------------------------------------
ALTER TABLE shipping.vessel_voyages
    ADD CONSTRAINT fk_voyages_vessel
        FOREIGN KEY (vessel_id)
        REFERENCES shipping.vessels (vessel_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE;

-- ---------------------------------------------------------------------------
-- B.4  statement_of_facts → vessel_voyages
-- CASCADE on DELETE: if a voyage is deleted (rare, admin action), purge SOF log.
-- CASCADE on UPDATE: propagate voyage_id changes.
-- ---------------------------------------------------------------------------
ALTER TABLE shipping.statement_of_facts
    ADD CONSTRAINT fk_sof_voyage
        FOREIGN KEY (voyage_id)
        REFERENCES shipping.vessel_voyages (voyage_id)
        ON DELETE CASCADE
        ON UPDATE CASCADE;

-- ---------------------------------------------------------------------------
-- B.5  statement_of_facts → statement_of_facts (self-referential amendment)
-- SET NULL on DELETE: if the original SOF line is purged, clear the back-ref.
-- CASCADE on UPDATE: propagate sof_id.
-- ---------------------------------------------------------------------------
ALTER TABLE shipping.statement_of_facts
    ADD CONSTRAINT fk_sof_amendment_ref
        FOREIGN KEY (amended_sof_id)
        REFERENCES shipping.statement_of_facts (sof_id)
        ON DELETE SET NULL
        ON UPDATE CASCADE
        DEFERRABLE INITIALLY DEFERRED;
        -- DEFERRABLE: allows inserting the pair in the same transaction
        -- before the referenced row exists (e.g., bulk inserts).

-- ---------------------------------------------------------------------------
-- B.6  statement_of_facts → shipping_documents (source document)
-- SET NULL on DELETE: losing a document reference is survivable.
-- CASCADE on UPDATE.
-- ---------------------------------------------------------------------------
ALTER TABLE shipping.statement_of_facts
    ADD CONSTRAINT fk_sof_source_document
        FOREIGN KEY (source_document_id)
        REFERENCES shipping.shipping_documents (document_id)
        ON DELETE SET NULL
        ON UPDATE CASCADE;

-- ---------------------------------------------------------------------------
-- B.7  laytime_calculations → vessel_voyages
-- RESTRICT on DELETE: cannot delete voyage while calculation exists.
-- CASCADE on UPDATE.
-- ---------------------------------------------------------------------------
ALTER TABLE shipping.laytime_calculations
    ADD CONSTRAINT fk_laytime_voyage
        FOREIGN KEY (voyage_id)
        REFERENCES shipping.vessel_voyages (voyage_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE;

-- ---------------------------------------------------------------------------
-- B.8  plant_inventory → vessel_voyages (optional contributing voyage)
-- SET NULL on DELETE: stock record survives if voyage is removed.
-- CASCADE on UPDATE.
-- ---------------------------------------------------------------------------
ALTER TABLE shipping.plant_inventory
    ADD CONSTRAINT fk_inventory_contributing_voyage
        FOREIGN KEY (contributing_voyage_id)
        REFERENCES shipping.vessel_voyages (voyage_id)
        ON DELETE SET NULL
        ON UPDATE CASCADE;

-- ---------------------------------------------------------------------------
-- B.9  shipping_documents → vessel_voyages (optional)
-- SET NULL on DELETE: document can exist without a voyage reference.
-- CASCADE on UPDATE.
-- ---------------------------------------------------------------------------
ALTER TABLE shipping.shipping_documents
    ADD CONSTRAINT fk_documents_voyage
        FOREIGN KEY (voyage_id)
        REFERENCES shipping.vessel_voyages (voyage_id)
        ON DELETE SET NULL
        ON UPDATE CASCADE;

-- ---------------------------------------------------------------------------
-- B.10  shipping_documents → charter_contracts (optional)
-- SET NULL on DELETE: document can exist without a contract reference.
-- CASCADE on UPDATE.
-- ---------------------------------------------------------------------------
ALTER TABLE shipping.shipping_documents
    ADD CONSTRAINT fk_documents_contract
        FOREIGN KEY (contract_id)
        REFERENCES shipping.charter_contracts (contract_id)
        ON DELETE SET NULL
        ON UPDATE CASCADE;

-- ---------------------------------------------------------------------------
-- B.11  shipping_documents → shipping_documents (self-referential versioning)
-- SET NULL on DELETE: if superseded document is purged, clear reference.
-- CASCADE on UPDATE.
-- ---------------------------------------------------------------------------
ALTER TABLE shipping.shipping_documents
    ADD CONSTRAINT fk_documents_supersedes
        FOREIGN KEY (supersedes_document_id)
        REFERENCES shipping.shipping_documents (document_id)
        ON DELETE SET NULL
        ON UPDATE CASCADE
        DEFERRABLE INITIALLY DEFERRED;

-- ---------------------------------------------------------------------------
-- B.12  charter_contracts → shipping_documents (CP document back-reference)
-- Note: This is a post-creation reference (document uploaded after contract
-- is created), so SET NULL on DELETE is appropriate.
-- ---------------------------------------------------------------------------
ALTER TABLE shipping.charter_contracts
    ADD CONSTRAINT fk_contracts_cp_document
        FOREIGN KEY (cp_document_id)
        REFERENCES shipping.shipping_documents (document_id)
        ON DELETE SET NULL
        ON UPDATE CASCADE;

-- ===========================================================================
-- SECTION C: UNIQUE CONSTRAINTS (business-key uniqueness)
-- ===========================================================================

-- IMO number: globally unique across the vessel registry
ALTER TABLE shipping.vessels
    ADD CONSTRAINT uq_vessels_imo_number
        UNIQUE (imo_number);

-- Contract reference: unique within the system
ALTER TABLE shipping.charter_contracts
    ADD CONSTRAINT uq_contracts_reference
        UNIQUE (contract_reference);

-- Voyage reference: unique within the system
ALTER TABLE shipping.vessel_voyages
    ADD CONSTRAINT uq_voyages_reference
        UNIQUE (voyage_reference);

-- SOF events: within a single voyage, sequence_number must be unique
-- (ensures no duplicate sequence positions and supports ordering)
ALTER TABLE shipping.statement_of_facts
    ADD CONSTRAINT uq_sof_voyage_sequence
        UNIQUE (voyage_id, sequence_number);

-- Laytime calc: only one CURRENT revision per voyage (partial unique index
-- is more efficient; this plain constraint acts as a DB-level safety net)
-- Note: The authoritative partial unique index is in V004 (indexing cycle).
-- This constraint catches any non-indexed path:
ALTER TABLE shipping.laytime_calculations
    ADD CONSTRAINT uq_laytime_voyage_revision
        UNIQUE (voyage_id, revision);

-- Plant inventory: one record per (plant, material, date)
ALTER TABLE shipping.plant_inventory
    ADD CONSTRAINT uq_inventory_plant_material_date
        UNIQUE (plant_code, material_category, stock_date);

-- MinIO object key: globally unique within a bucket (no duplicate uploads)
ALTER TABLE shipping.shipping_documents
    ADD CONSTRAINT uq_documents_minio_object
        UNIQUE (minio_bucket, minio_object_key);

COMMIT;
