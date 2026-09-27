-- =============================================================================
-- Migration: V005__triggers_and_audit.sql
-- Project:   Overseas Bulk Vessel Chartering & Logistics Management Dashboard
-- Cycle:     1.1.4 — Procedural Triggers & Timestamp Automation
-- Author:    Antigravity / SIH26006 Engineering Team
-- DB:        PostgreSQL 16
-- Depends:   V004__indexes_and_temporal_ranges.sql
-- Description:
--   Installs all PL/pgSQL trigger functions and their associated triggers.
--   Organised into five sections:
--
--   A. Timestamp automation
--      - fn_set_created_at  : Sets created_at on INSERT (immutable thereafter)
--      - fn_set_updated_at  : Refreshes updated_at on every UPDATE
--
--   B. SOF sequence auto-assignment
--      - fn_sof_auto_sequence: If sequence_number IS NULL on INSERT, assigns
--        MAX(sequence_number)+1 within the voyage (advisory lock prevents gaps)
--
--   C. Laytime is_current flip
--      - fn_laytime_flip_current: When a new laytime_calculations row is
--        INSERTed with is_current=TRUE, atomically demotes the previous
--        current revision to is_current=FALSE for the same voyage.
--
--   D. Audit log table + trigger
--      - shipping.audit_log  : Append-only audit table (never updated/deleted)
--      - fn_audit_log        : Captures old/new JSONB for all core tables on
--        INSERT, UPDATE, DELETE. Uses session variables to record the Django
--        auth user ID who performed the operation.
--
--   E. Vessel voyage status guard
--      - fn_voyage_status_guard: Enforces the valid state machine transitions
--        for voyage_status (prevents illegal backward transitions at DB level)
-- =============================================================================

BEGIN;

SET LOCAL search_path TO shipping, public;

-- ===========================================================================
-- SECTION A: Timestamp automation
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- A.1  created_at immutability guard + initial set
--      Fires BEFORE INSERT to guarantee created_at is set to NOW() and
--      BEFORE UPDATE to prevent any modification of created_at.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION shipping.fn_set_timestamps()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- Always override created_at; no caller should set it manually.
        NEW.created_at := NOW();
        NEW.updated_at := NOW();

    ELSIF TG_OP = 'UPDATE' THEN
        -- Enforce created_at immutability: silently restore original value.
        -- This prevents accidental or malicious backdating.
        IF NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            NEW.created_at := OLD.created_at;
        END IF;
        -- Always refresh updated_at to current wall-clock time.
        NEW.updated_at := NOW();
    END IF;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION shipping.fn_set_timestamps() IS
    'Trigger function: sets created_at on INSERT (immutable), refreshes updated_at on every UPDATE. '
    'Apply to all tables that carry created_at / updated_at columns.';

-- Attach to every core table --

CREATE TRIGGER trg_vessels_timestamps
    BEFORE INSERT OR UPDATE ON shipping.vessels
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_set_timestamps();

CREATE TRIGGER trg_charter_contracts_timestamps
    BEFORE INSERT OR UPDATE ON shipping.charter_contracts
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_set_timestamps();

CREATE TRIGGER trg_vessel_voyages_timestamps
    BEFORE INSERT OR UPDATE ON shipping.vessel_voyages
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_set_timestamps();

CREATE TRIGGER trg_laytime_calculations_timestamps
    BEFORE INSERT OR UPDATE ON shipping.laytime_calculations
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_set_timestamps();

CREATE TRIGGER trg_plant_inventory_timestamps
    BEFORE INSERT OR UPDATE ON shipping.plant_inventory
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_set_timestamps();

CREATE TRIGGER trg_shipping_documents_timestamps
    BEFORE INSERT OR UPDATE ON shipping.shipping_documents
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_set_timestamps();

-- statement_of_facts uses submitted_at instead of created_at/updated_at;
-- apply a narrower trigger that only guards submitted_at:
CREATE OR REPLACE FUNCTION shipping.fn_set_sof_submitted_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.submitted_at := NOW();
    ELSIF TG_OP = 'UPDATE' THEN
        -- submitted_at on a SOF row is immutable (SOF lines are immutable records).
        -- Amendments must be new rows; updates to existing SOF rows are forbidden
        -- except by SYSTEM_ADMIN via direct DB access with explicit override.
        IF NEW.submitted_at IS DISTINCT FROM OLD.submitted_at THEN
            RAISE EXCEPTION
                'shipping.statement_of_facts.submitted_at is immutable. '
                'Create an amendment row (is_amendment=TRUE) instead of updating.'
                USING ERRCODE = 'check_violation',
                      HINT    = 'Insert a new SOF row with is_amendment=TRUE referencing the original sof_id.';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION shipping.fn_set_sof_submitted_at() IS
    'Enforces submitted_at immutability on SOF rows. Updates to submitted_at raise an exception; '
    'amendments must be separate INSERT rows with is_amendment=TRUE.';

CREATE TRIGGER trg_sof_submitted_at
    BEFORE INSERT OR UPDATE ON shipping.statement_of_facts
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_set_sof_submitted_at();

-- ===========================================================================
-- SECTION B: SOF sequence auto-assignment
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- B.1  Auto-assign sequence_number if not provided by caller.
--      Uses pg_advisory_xact_lock to serialise concurrent inserts
--      for the same voyage, preventing sequence gaps or duplicates.
--      The lock key is derived from a hash of the voyage_id UUID.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION shipping.fn_sof_auto_sequence()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_next_seq  INTEGER;
    v_lock_key  BIGINT;
BEGIN
    -- Only act when sequence_number is not explicitly provided.
    IF NEW.sequence_number IS NOT NULL THEN
        RETURN NEW;
    END IF;

    -- Derive a stable advisory lock key from the voyage UUID.
    -- hashtext() returns an int4; cast to int8 for advisory lock.
    v_lock_key := hashtext(NEW.voyage_id::TEXT)::BIGINT;

    -- Acquire an exclusive transaction-level advisory lock for this voyage.
    -- This serialises concurrent SOF inserts for the same voyage.
    PERFORM pg_advisory_xact_lock(v_lock_key);

    -- Compute the next sequence number within the voyage.
    SELECT COALESCE(MAX(sequence_number), 0) + 1
    INTO   v_next_seq
    FROM   shipping.statement_of_facts
    WHERE  voyage_id = NEW.voyage_id
      AND  NOT is_amendment;  -- Amendments share the sequence of their parent

    NEW.sequence_number := v_next_seq;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION shipping.fn_sof_auto_sequence() IS
    'Auto-assigns sequence_number on SOF INSERT if not provided. '
    'Uses pg_advisory_xact_lock(hashtext(voyage_id)) to prevent race conditions '
    'between concurrent port agent submissions for the same voyage.';

CREATE TRIGGER trg_sof_auto_sequence
    BEFORE INSERT ON shipping.statement_of_facts
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_sof_auto_sequence();

-- ===========================================================================
-- SECTION C: Laytime is_current flip
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- C.1  When a new laytime_calculations row is inserted with is_current=TRUE,
--      atomically demote the previous current revision for the same voyage.
--      This works in concert with uidx_laytime_one_current_per_voyage
--      (from V004) — the demotion must happen BEFORE the unique index
--      is checked, so it fires as a BEFORE trigger.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION shipping.fn_laytime_flip_current()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    -- Only act when the incoming row asserts is_current = TRUE.
    IF NEW.is_current IS NOT TRUE THEN
        RETURN NEW;
    END IF;

    -- Demote any existing current revision for this voyage.
    UPDATE shipping.laytime_calculations
    SET    is_current  = FALSE,
           updated_at  = NOW()
    WHERE  voyage_id   = NEW.voyage_id
      AND  is_current  = TRUE
      AND  calc_id    != NEW.calc_id;   -- Exclude the row being inserted (NEW.calc_id
                                        -- is NULL on INSERT, so != NULL is always TRUE —
                                        -- safe, because no existing row has NULL calc_id)

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION shipping.fn_laytime_flip_current() IS
    'Atomically demotes the previous is_current=TRUE laytime revision when a new revision '
    'is inserted. Works in tandem with uidx_laytime_one_current_per_voyage partial unique index.';

CREATE TRIGGER trg_laytime_flip_current
    BEFORE INSERT ON shipping.laytime_calculations
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_laytime_flip_current();

-- ===========================================================================
-- SECTION D: Audit log table + trigger
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- D.1  Append-only audit log table.
--      Never receives UPDATE or DELETE — enforced by trigger below.
--      Stores old/new row states as JSONB for complete change history.
-- ---------------------------------------------------------------------------
CREATE TABLE shipping.audit_log (
    audit_id        UUID            NOT NULL DEFAULT gen_random_uuid(),
    -- What changed
    schema_name     VARCHAR(63)     NOT NULL DEFAULT 'shipping',
    table_name      VARCHAR(63)     NOT NULL,
    operation       CHAR(1)         NOT NULL,   -- 'I'=INSERT, 'U'=UPDATE, 'D'=DELETE
    record_id       TEXT            NOT NULL,   -- PK value of the changed row (cast to TEXT)
    -- Row state snapshots
    old_data        JSONB,                      -- NULL on INSERT
    new_data        JSONB,                      -- NULL on DELETE
    changed_fields  TEXT[],                     -- Column names that differed (UPDATE only)
    -- Session context
    app_user_id     TEXT,                       -- Django auth user ID (set via app.current_user_id)
    app_user_role   TEXT,                       -- User role at time of change
    client_ip       INET,                       -- Remote IP (set via app.client_ip)
    -- Timing
    changed_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    -- Append-only enforcement
    CONSTRAINT audit_log_pkey PRIMARY KEY (audit_id)
);

COMMENT ON TABLE  shipping.audit_log IS
    'Append-only audit trail for all DML operations on core shipping tables. '
    'Rows are never updated or deleted. Immutability enforced by trg_audit_log_immutable.';
COMMENT ON COLUMN shipping.audit_log.operation IS
    'I = INSERT, U = UPDATE, D = DELETE.';
COMMENT ON COLUMN shipping.audit_log.changed_fields IS
    'Array of column names whose value changed in an UPDATE operation.';
COMMENT ON COLUMN shipping.audit_log.app_user_id IS
    'Django auth user ID, injected via SET LOCAL app.current_user_id in API middleware.';
COMMENT ON COLUMN shipping.audit_log.client_ip IS
    'Client IP injected via SET LOCAL app.client_ip in API middleware for traceability.';

-- Index: chronological audit queries (most queries ask "what changed recently")
CREATE INDEX idx_audit_log_table_changed
    ON shipping.audit_log (table_name, changed_at DESC);

CREATE INDEX idx_audit_log_record
    ON shipping.audit_log (table_name, record_id, changed_at DESC);

CREATE INDEX idx_audit_log_user
    ON shipping.audit_log (app_user_id, changed_at DESC)
    WHERE app_user_id IS NOT NULL;

-- BRIN for wide date-range audit analytics
CREATE INDEX idx_audit_log_changed_at_brin
    ON shipping.audit_log
    USING BRIN (changed_at)
    WITH (pages_per_range = 32);

-- ---------------------------------------------------------------------------
-- D.2  Immutability guard on audit_log itself
--      Prevents anyone (including superusers via ORM) from modifying
--      or deleting audit records.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION shipping.fn_audit_log_immutable()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'Audit log rows are immutable. UPDATE and DELETE are forbidden on shipping.audit_log.'
        USING ERRCODE = 'insufficient_privilege',
              HINT    = 'Audit records may only be archived via a separate secure export process.';
END;
$$;

COMMENT ON FUNCTION shipping.fn_audit_log_immutable() IS
    'Prevents UPDATE and DELETE on audit_log, enforcing append-only semantics.';

CREATE TRIGGER trg_audit_log_immutable
    BEFORE UPDATE OR DELETE ON shipping.audit_log
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_audit_log_immutable();

-- ---------------------------------------------------------------------------
-- D.3  Core audit capture trigger function
--      Reads session variables set by Django middleware:
--        SET LOCAL app.current_user_id = '<uuid>';
--        SET LOCAL app.current_user_role = '<role>';
--        SET LOCAL app.client_ip = '<ip>';
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION shipping.fn_audit_log()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER   -- runs as owner, not caller, so always has INSERT rights on audit_log
AS $$
DECLARE
    v_old_data      JSONB;
    v_new_data      JSONB;
    v_changed_cols  TEXT[] := ARRAY[]::TEXT[];
    v_record_id     TEXT;
    v_operation     CHAR(1);
    v_col           TEXT;
    v_user_id       TEXT;
    v_user_role     TEXT;
    v_client_ip     INET;
BEGIN
    -- Read session-level variables injected by Django middleware.
    -- current_setting returns '' if not set; treat empty as NULL.
    v_user_id   := NULLIF(current_setting('app.current_user_id',   TRUE), '');
    v_user_role := NULLIF(current_setting('app.current_user_role', TRUE), '');
    v_client_ip := NULLIF(current_setting('app.client_ip',         TRUE), '')::INET;

    IF TG_OP = 'INSERT' THEN
        v_operation := 'I';
        v_new_data  := to_jsonb(NEW);
        v_old_data  := NULL;
        -- Extract PK: each table's PK column name ends in '_id'
        v_record_id := (v_new_data ->> (TG_TABLE_NAME::TEXT || '_id'));
        -- Fallback for audit_log itself (should never happen due to immutability trigger)
        IF v_record_id IS NULL THEN
            v_record_id := v_new_data ->> 'audit_id';
        END IF;

    ELSIF TG_OP = 'UPDATE' THEN
        v_operation := 'U';
        v_new_data  := to_jsonb(NEW);
        v_old_data  := to_jsonb(OLD);
        v_record_id := (v_old_data ->> (TG_TABLE_NAME::TEXT || '_id'));

        -- Compute changed fields by comparing keys
        FOR v_col IN
            SELECT key
            FROM   jsonb_each_text(v_new_data) AS n(key, val)
            WHERE  v_new_data ->> key IS DISTINCT FROM v_old_data ->> key
        LOOP
            v_changed_cols := v_changed_cols || v_col;
        END LOOP;

        -- Skip audit if only updated_at changed (prevents infinite loops
        -- from the timestamp trigger firing on its own audit write)
        IF v_changed_cols = ARRAY['updated_at'] THEN
            RETURN NULL;
        END IF;

    ELSIF TG_OP = 'DELETE' THEN
        v_operation := 'D';
        v_old_data  := to_jsonb(OLD);
        v_new_data  := NULL;
        v_record_id := (v_old_data ->> (TG_TABLE_NAME::TEXT || '_id'));
        v_changed_cols := NULL;
    END IF;

    INSERT INTO shipping.audit_log (
        table_name,
        operation,
        record_id,
        old_data,
        new_data,
        changed_fields,
        app_user_id,
        app_user_role,
        client_ip,
        changed_at
    ) VALUES (
        TG_TABLE_NAME,
        v_operation,
        v_record_id,
        v_old_data,
        v_new_data,
        NULLIF(v_changed_cols, ARRAY[]::TEXT[]),
        v_user_id,
        v_user_role,
        v_client_ip,
        NOW()
    );

    RETURN NULL;   -- AFTER trigger; return value is ignored
END;
$$;

COMMENT ON FUNCTION shipping.fn_audit_log() IS
    'AFTER trigger: captures INSERT/UPDATE/DELETE on core tables into shipping.audit_log. '
    'Reads app.current_user_id, app.current_user_role, app.client_ip session variables '
    'set by Django request middleware. Skips audit when only updated_at changes.';

-- Attach audit trigger to all core tables (AFTER, statement-level not feasible
-- because we need OLD/NEW row data — must be FOR EACH ROW)

CREATE TRIGGER trg_vessels_audit
    AFTER INSERT OR UPDATE OR DELETE ON shipping.vessels
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_audit_log();

CREATE TRIGGER trg_charter_contracts_audit
    AFTER INSERT OR UPDATE OR DELETE ON shipping.charter_contracts
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_audit_log();

CREATE TRIGGER trg_vessel_voyages_audit
    AFTER INSERT OR UPDATE OR DELETE ON shipping.vessel_voyages
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_audit_log();

CREATE TRIGGER trg_statement_of_facts_audit
    AFTER INSERT OR UPDATE OR DELETE ON shipping.statement_of_facts
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_audit_log();

CREATE TRIGGER trg_laytime_calculations_audit
    AFTER INSERT OR UPDATE OR DELETE ON shipping.laytime_calculations
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_audit_log();

CREATE TRIGGER trg_plant_inventory_audit
    AFTER INSERT OR UPDATE OR DELETE ON shipping.plant_inventory
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_audit_log();

CREATE TRIGGER trg_shipping_documents_audit
    AFTER INSERT OR UPDATE OR DELETE ON shipping.shipping_documents
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_audit_log();

-- ===========================================================================
-- SECTION E: Voyage status state machine guard
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- E.1  Legal state machine transitions for voyage_status.
--      Prevents backward or invalid transitions at the database level,
--      independent of application logic.
--
--      Valid transitions:
--        SCHEDULED   → IN_TRANSIT, CANCELLED
--        IN_TRANSIT  → ARRIVED, DEVIATED, CANCELLED
--        ARRIVED     → NOR_TENDERED, DEVIATED, CANCELLED
--        NOR_TENDERED→ BERTHED, ARRIVED (NOR rejected → re-anchor)
--        BERTHED     → COMMENCING
--        COMMENCING  → DISCHARGING
--        DISCHARGING → COMPLETED, DEVIATED
--        DEVIATED    → IN_TRANSIT, ARRIVED, CANCELLED
--        COMPLETED   → (terminal — no further transitions)
--        CANCELLED   → (terminal — no further transitions)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION shipping.fn_voyage_status_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    valid_transitions JSONB := '{
        "SCHEDULED"    : ["IN_TRANSIT", "CANCELLED"],
        "IN_TRANSIT"   : ["ARRIVED", "DEVIATED", "CANCELLED"],
        "ARRIVED"      : ["NOR_TENDERED", "DEVIATED", "CANCELLED"],
        "NOR_TENDERED" : ["BERTHED", "ARRIVED"],
        "BERTHED"      : ["COMMENCING"],
        "COMMENCING"   : ["DISCHARGING"],
        "DISCHARGING"  : ["COMPLETED", "DEVIATED"],
        "DEVIATED"     : ["IN_TRANSIT", "ARRIVED", "CANCELLED"],
        "COMPLETED"    : [],
        "CANCELLED"    : []
    }'::JSONB;
    allowed JSONB;
BEGIN
    -- Only enforce on status changes
    IF NEW.voyage_status = OLD.voyage_status THEN
        RETURN NEW;
    END IF;

    allowed := valid_transitions -> OLD.voyage_status::TEXT;

    -- If the old status has no entry, it's an unrecognised state — allow
    -- (forward-compatible with future ENUM additions)
    IF allowed IS NULL THEN
        RETURN NEW;
    END IF;

    IF NOT (allowed @> to_jsonb(NEW.voyage_status::TEXT)) THEN
        RAISE EXCEPTION
            'Invalid voyage status transition: % → % for voyage_id=%. '
            'Allowed next states: %',
            OLD.voyage_status, NEW.voyage_status, NEW.voyage_id, allowed
            USING ERRCODE = 'check_violation',
                  HINT    = 'See shipping.fn_voyage_status_guard for the valid state machine.';
    END IF;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION shipping.fn_voyage_status_guard() IS
    'Enforces the vessel voyage status state machine at DB level. '
    'Rejects invalid backward/illegal transitions. '
    'Valid transitions encoded as JSONB lookup table within the function.';

CREATE TRIGGER trg_voyage_status_guard
    BEFORE UPDATE OF voyage_status ON shipping.vessel_voyages
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_voyage_status_guard();

-- ===========================================================================
-- SECTION F: Document verification status guard
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- F.1  Prevents illegal verification status transitions on shipping_documents.
--      E.g., a QUARANTINED document cannot become HASH_VERIFIED directly.
--
--      Valid transitions:
--        PENDING_UPLOAD   → UPLOAD_COMPLETE, DELETED
--        UPLOAD_COMPLETE  → HASH_VERIFIED, HASH_MISMATCH, DELETED
--        HASH_VERIFIED    → APPROVED, SUPERSEDED, DELETED
--        HASH_MISMATCH    → QUARANTINED, DELETED
--        QUARANTINED      → DELETED                 (admin resolves)
--        APPROVED         → SUPERSEDED, DELETED
--        SUPERSEDED       → DELETED
--        DELETED          → (terminal)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION shipping.fn_document_status_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    valid_transitions JSONB := '{
        "PENDING_UPLOAD"  : ["UPLOAD_COMPLETE", "DELETED"],
        "UPLOAD_COMPLETE" : ["HASH_VERIFIED", "HASH_MISMATCH", "DELETED"],
        "HASH_VERIFIED"   : ["APPROVED", "SUPERSEDED", "DELETED"],
        "HASH_MISMATCH"   : ["QUARANTINED", "DELETED"],
        "QUARANTINED"     : ["DELETED"],
        "APPROVED"        : ["SUPERSEDED", "DELETED"],
        "SUPERSEDED"      : ["DELETED"],
        "DELETED"         : []
    }'::JSONB;
    allowed JSONB;
BEGIN
    IF NEW.verification_status = OLD.verification_status THEN
        RETURN NEW;
    END IF;

    allowed := valid_transitions -> OLD.verification_status::TEXT;

    IF allowed IS NULL THEN
        RETURN NEW;
    END IF;

    IF NOT (allowed @> to_jsonb(NEW.verification_status::TEXT)) THEN
        RAISE EXCEPTION
            'Invalid document verification status transition: % → % for document_id=%. '
            'Allowed next states: %',
            OLD.verification_status, NEW.verification_status, NEW.document_id, allowed
            USING ERRCODE = 'check_violation',
                  HINT    = 'See shipping.fn_document_status_guard for the valid state machine.';
    END IF;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION shipping.fn_document_status_guard() IS
    'Enforces document verification_status state machine transitions. '
    'Prevents quarantined or mismatched documents from being silently re-approved.';

CREATE TRIGGER trg_document_status_guard
    BEFORE UPDATE OF verification_status ON shipping.shipping_documents
    FOR EACH ROW EXECUTE FUNCTION shipping.fn_document_status_guard();

COMMIT;
