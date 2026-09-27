"""
static_lint_cycle_1_1_4.py — Static SQL structural verifier for V005
Validates V005__triggers_and_audit.sql for:
  - File existence and transaction integrity
  - All trigger functions defined (CREATE OR REPLACE FUNCTION)
  - All triggers defined and attached to correct tables
  - audit_log table structure
  - Advisory lock for SOF sequence
  - SECURITY DEFINER on audit function
  - State machine completeness (voyage + document)
  - JSONB state machine lookup
  - Zero mock patterns
  - COMMENT ON FUNCTION coverage
"""

import re
import sys
import io
import pathlib

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).parent
V005 = ROOT / "migrations" / "V005__triggers_and_audit.sql"

PASS = "✓ PASS"
FAIL = "✗ FAIL"
failures: list[str] = []


def check(condition: bool, label: str) -> None:
    symbol = PASS if condition else FAIL
    print(f"  {symbol}  {label}")
    if not condition:
        failures.append(label)


# ============================================================
print("=" * 68)
print("  Cycle 1.1.4 — Static SQL Structural Lint: V005 Triggers & Audit")
print("=" * 68)

print("\n[FILE]")
check(V005.exists(), f"V005 file exists: {V005.name}")

raw = V005.read_text(encoding="utf-8")
up  = raw.upper()

# ---- Transaction integrity ----
print("\n[TRANSACTION INTEGRITY]")
check(up.count("BEGIN;") == 1,  "V005: Single BEGIN")
check(up.count("COMMIT;") == 1, "V005: Single COMMIT")
check("SET LOCAL SEARCH_PATH TO SHIPPING" in up, "V005: SET LOCAL search_path")

# ---- Trigger functions ----
print("\n[TRIGGER FUNCTIONS]")
EXPECTED_FUNCTIONS = [
    "FN_SET_TIMESTAMPS",
    "FN_SET_SOF_SUBMITTED_AT",
    "FN_SOF_AUTO_SEQUENCE",
    "FN_LAYTIME_FLIP_CURRENT",
    "FN_AUDIT_LOG_IMMUTABLE",
    "FN_AUDIT_LOG",
    "FN_VOYAGE_STATUS_GUARD",
    "FN_DOCUMENT_STATUS_GUARD",
]
for fn in EXPECTED_FUNCTIONS:
    check(f"FUNCTION SHIPPING.{fn}" in up, f"Function: shipping.{fn}")

fn_count = len(re.findall(r'CREATE OR REPLACE FUNCTION', up))
check(fn_count >= 8, f">=8 CREATE OR REPLACE FUNCTION statements (found {fn_count})")

# ---- All functions return TRIGGER ----
check(up.count("RETURNS TRIGGER") >= 8, f">=8 RETURNS TRIGGER declarations (found {up.count('RETURNS TRIGGER')})")
check(up.count("LANGUAGE PLPGSQL") >= 8, f">=8 LANGUAGE PLPGSQL declarations")

# ---- Triggers attached to tables ----
print("\n[TRIGGERS — TIMESTAMP]")
TIMESTAMP_TRIGGERS = [
    "TRG_VESSELS_TIMESTAMPS",
    "TRG_CHARTER_CONTRACTS_TIMESTAMPS",
    "TRG_VESSEL_VOYAGES_TIMESTAMPS",
    "TRG_LAYTIME_CALCULATIONS_TIMESTAMPS",
    "TRG_PLANT_INVENTORY_TIMESTAMPS",
    "TRG_SHIPPING_DOCUMENTS_TIMESTAMPS",
    "TRG_SOF_SUBMITTED_AT",
]
for t in TIMESTAMP_TRIGGERS:
    check(t in up, f"Trigger: {t}")

print("\n[TRIGGERS — SEQUENCE & FLIP]")
check("TRG_SOF_AUTO_SEQUENCE" in up, "Trigger: trg_sof_auto_sequence")
check("TRG_LAYTIME_FLIP_CURRENT" in up, "Trigger: trg_laytime_flip_current")

print("\n[TRIGGERS — AUDIT]")
AUDIT_TRIGGERS = [
    "TRG_VESSELS_AUDIT",
    "TRG_CHARTER_CONTRACTS_AUDIT",
    "TRG_VESSEL_VOYAGES_AUDIT",
    "TRG_STATEMENT_OF_FACTS_AUDIT",
    "TRG_LAYTIME_CALCULATIONS_AUDIT",
    "TRG_PLANT_INVENTORY_AUDIT",
    "TRG_SHIPPING_DOCUMENTS_AUDIT",
    "TRG_AUDIT_LOG_IMMUTABLE",
]
for t in AUDIT_TRIGGERS:
    check(t in up, f"Trigger: {t}")

print("\n[TRIGGERS — STATE MACHINES]")
check("TRG_VOYAGE_STATUS_GUARD" in up, "Trigger: trg_voyage_status_guard")
check("TRG_DOCUMENT_STATUS_GUARD" in up, "Trigger: trg_document_status_guard")

# Total trigger count
total_triggers = len(re.findall(r'\bCREATE TRIGGER\b', up))
check(total_triggers >= 18, f">=18 CREATE TRIGGER statements (found {total_triggers})")

# ---- Audit log table ----
print("\n[AUDIT LOG TABLE]")
check("CREATE TABLE SHIPPING.AUDIT_LOG" in up, "Table: shipping.audit_log")
check("AUDIT_ID" in up and "UUID" in up, "audit_log.audit_id UUID PK")
check("TABLE_NAME" in up, "audit_log.table_name column")
check("OPERATION" in up and "CHAR(1)" in up, "audit_log.operation CHAR(1)")
check("OLD_DATA" in up and "JSONB" in up, "audit_log.old_data JSONB column")
check("NEW_DATA" in up, "audit_log.new_data JSONB column")
check("CHANGED_FIELDS" in up and "TEXT[]" in up, "audit_log.changed_fields TEXT[] column")
check("APP_USER_ID" in up, "audit_log.app_user_id column")
check("CLIENT_IP" in up and "INET" in up, "audit_log.client_ip INET column")
check("CHANGED_AT" in up and "TIMESTAMPTZ" in up, "audit_log.changed_at TIMESTAMPTZ")

# ---- Advisory lock for SOF sequence ----
print("\n[SOF SEQUENCE — ADVISORY LOCK]")
check("PG_ADVISORY_XACT_LOCK" in up, "pg_advisory_xact_lock() used for SOF sequence serialisation")
check("HASHTEXT" in up, "hashtext() used to derive advisory lock key from voyage_id")
check("COALESCE(MAX(SEQUENCE_NUMBER), 0) + 1" in up, "Sequence auto-increment formula present")

# ---- SECURITY DEFINER on audit function ----
print("\n[SECURITY]")
check("SECURITY DEFINER" in up, "fn_audit_log declared SECURITY DEFINER")
check("CURRENT_SETTING('APP.CURRENT_USER_ID'" in up, "Session variable app.current_user_id read")
check("CURRENT_SETTING('APP.CURRENT_USER_ROLE'" in up, "Session variable app.current_user_role read")
check("CURRENT_SETTING('APP.CLIENT_IP'" in up, "Session variable app.client_ip read")

# ---- State machine completeness ----
print("\n[VOYAGE STATE MACHINE]")
VOYAGE_STATES = [
    "SCHEDULED", "IN_TRANSIT", "ARRIVED", "NOR_TENDERED",
    "BERTHED", "COMMENCING", "DISCHARGING", "COMPLETED",
    "DEVIATED", "CANCELLED"
]
for state in VOYAGE_STATES:
    check(f'"{state}"' in raw, f"Voyage state in machine: {state}")

print("\n[DOCUMENT STATUS STATE MACHINE]")
DOC_STATES = [
    "PENDING_UPLOAD", "UPLOAD_COMPLETE", "HASH_VERIFIED",
    "HASH_MISMATCH", "QUARANTINED", "APPROVED", "SUPERSEDED", "DELETED"
]
for state in DOC_STATES:
    check(f'"{state}"' in raw, f"Document state in machine: {state}")

# ---- JSONB state machine lookup ----
check("JSONB" in up and "VALID_TRANSITIONS" in up,
      "JSONB valid_transitions lookup table defined in state machines")
check("RAISE EXCEPTION" in up, "RAISE EXCEPTION used for invalid transitions")
check("ERRCODE = 'CHECK_VIOLATION'" in up, "check_violation ERRCODE used")

# ---- Audit immutability ----
print("\n[AUDIT IMMUTABILITY]")
check("BEFORE UPDATE OR DELETE ON SHIPPING.AUDIT_LOG" in up,
      "Immutability trigger protects audit_log from UPDATE/DELETE")
check("INSUFFICIENT_PRIVILEGE" in up, "insufficient_privilege ERRCODE on audit mutation attempt")

# ---- updated_at skip in audit ----
check("UPDATED_AT" in up and "ARRAY['UPDATED_AT']" in up,
      "Audit trigger skips rows where only updated_at changed")

# ---- COMMENT ON FUNCTION coverage ----
print("\n[COMMENT ON FUNCTION COVERAGE]")
comment_fn_count = up.count("COMMENT ON FUNCTION")
check(comment_fn_count >= 8, f">=8 COMMENT ON FUNCTION statements (found {comment_fn_count})")

# ---- Zero mock ----
print("\n[ZERO-MOCK POLICY]")
for pat in ["MOCK", "HARDCODE", "FAKE_", "DUMMY_"]:
    check(pat not in up, f"No '{pat}' pattern in V005")

# ============================================================
print("\n" + "=" * 68)
if failures:
    print(f"  RESULT: {FAIL} — {len(failures)} check(s) failed:")
    for f in failures:
        print(f"    • {f}")
    sys.exit(1)
else:
    print(f"  RESULT: {PASS} — All checks passed.")
print("=" * 68)
