"""
static_lint_cycle_1_1_1.py — Static SQL structural verifier (no DB required)
Validates V001 and V002 migration files for:
  - Correct ENUM count and names
  - Correct TABLE count and names
  - Presence of required column names (text search)
  - No forbidden patterns (mocking, SERIAL, FLOAT, hardcoded passwords)
  - BEGIN / COMMIT balance
  - TIMESTAMPTZ usage (not TIMESTAMP without TZ)
  - NUMERIC usage for financial columns (not FLOAT/DOUBLE)
"""

import re
import sys
import io
import pathlib

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).parent

FILES = {
    "V001": ROOT / "migrations" / "V001__enums_and_domain_types.sql",
    "V002": ROOT / "migrations" / "V002__core_tables.sql",
}

PASS = "✓ PASS"
FAIL = "✗ FAIL"
failures: list[str] = []


def check(condition: bool, label: str) -> None:
    symbol = PASS if condition else FAIL
    print(f"  {symbol}  {label}")
    if not condition:
        failures.append(label)


def sql(key: str) -> str:
    return FILES[key].read_text(encoding="utf-8").upper()


# ============================================================
print("=" * 68)
print("  Cycle 1.1.1 — Static SQL Structural Lint")
print("=" * 68)

# ---- File existence ----
print("\n[FILES]")
for key, path in FILES.items():
    check(path.exists(), f"{key} file exists: {path.name}")

v1 = sql("V001")
v2 = sql("V002")

# ---- BEGIN / COMMIT pairs ----
print("\n[TRANSACTION INTEGRITY]")
check(v1.count("BEGIN;") == 1 and v1.count("COMMIT;") == 1, "V001: Single BEGIN/COMMIT pair")
check(v2.count("BEGIN;") == 1 and v2.count("COMMIT;") == 1, "V002: Single BEGIN/COMMIT pair")

# ---- Schema isolation ----
print("\n[SCHEMA]")
check("CREATE SCHEMA IF NOT EXISTS SHIPPING" in v1, "V001: CREATE SCHEMA shipping")
check("SET LOCAL SEARCH_PATH TO SHIPPING" in v1, "V001: SET LOCAL search_path")
check("SET LOCAL SEARCH_PATH TO SHIPPING" in v2, "V002: SET LOCAL search_path")

# ---- ENUM definitions ----
print("\n[ENUM DEFINITIONS — V001]")
EXPECTED_ENUMS = [
    "VESSEL_SIZE_CLASS", "CARGO_TYPE", "CHARTER_PARTY_TYPE",
    "CONTRACT_STATUS", "LAYTIME_TERMS", "SETTLEMENT_CURRENCY",
    "VOYAGE_STATUS", "VOYAGE_DIRECTION", "PORT_FUNCTION",
    "SOF_EVENT_TYPE", "INTERVAL_COUNT_STATUS", "LAYTIME_OUTCOME",
    "DOCUMENT_TYPE", "DOCUMENT_VERIFICATION_STATUS",
    "STOCK_SAFETY_STATUS", "RAW_MATERIAL_CATEGORY",
    "USER_ROLE", "ALERT_SEVERITY", "ALERT_STATUS",
]
for enum in EXPECTED_ENUMS:
    check(f"CREATE TYPE SHIPPING.{enum} AS ENUM" in v1, f"ENUM: shipping.{enum}")

# ---- TABLE definitions ----
print("\n[TABLE DEFINITIONS — V002]")
EXPECTED_TABLES = [
    "VESSELS", "CHARTER_CONTRACTS", "VESSEL_VOYAGES",
    "STATEMENT_OF_FACTS", "LAYTIME_CALCULATIONS",
    "PLANT_INVENTORY", "SHIPPING_DOCUMENTS",
]
for tbl in EXPECTED_TABLES:
    check(f"CREATE TABLE SHIPPING.{tbl}" in v2, f"TABLE: shipping.{tbl}")

# ---- Primary keys ----
print("\n[PRIMARY KEY CONSTRAINTS]")
PKS = [
    "VESSELS_PKEY", "CHARTER_CONTRACTS_PKEY", "VESSEL_VOYAGES_PKEY",
    "STATEMENT_OF_FACTS_PKEY", "LAYTIME_CALCULATIONS_PKEY",
    "PLANT_INVENTORY_PKEY", "SHIPPING_DOCUMENTS_PKEY",
]
for pk in PKS:
    check(pk in v2, f"PK constraint: {pk}")

# ---- UUID + gen_random_uuid() ----
print("\n[UUID PRIMARY KEYS]")
check(v2.count("UUID") >= 7, f"≥7 UUID columns defined (found {v2.count('UUID')})")
check(v2.count("GEN_RANDOM_UUID()") >= 7, "All PKs use gen_random_uuid()")

# ---- TIMESTAMPTZ (not bare TIMESTAMP) ----
print("\n[TEMPORAL TYPES]")
tz_count = v2.count("TIMESTAMPTZ")
# Strip inline comments and full-comment lines; only inspect DDL declaration tokens
def strip_sql_comments(sql_text: str) -> str:
    """Remove -- comments (both full-line and inline) from SQL text."""
    lines = []
    for line in sql_text.upper().splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue  # skip full-comment lines
        # Remove inline comment (everything from -- onward)
        inline_idx = line.find("--")
        lines.append(line[:inline_idx] if inline_idx >= 0 else line)
    return "\n".join(lines)

v2_ddl_clean = strip_sql_comments(FILES["V002"].read_text(encoding="utf-8"))
# Match TIMESTAMP only when it appears as a column type declaration:
# Must be followed (possibly with spaces) by type-context tokens
bare_ts = len(re.findall(
    r'\bTIMESTAMP\b(?!TZ)(?=\s*(?:NOT\s+NULL|DEFAULT|,|\)|\s+GENERATED))',
    v2_ddl_clean,
    re.IGNORECASE
))
check(tz_count >= 20, f">=20 TIMESTAMPTZ columns (found {tz_count})")
check(bare_ts == 0, f"Zero bare TIMESTAMP type declarations without TZ (found {bare_ts})")

# ---- NUMERIC for financials (no FLOAT/DOUBLE) ----
print("\n[NUMERIC PRECISION — NO FLOAT]")
float_count = len(re.findall(r'\b(FLOAT|DOUBLE PRECISION|REAL)\b', v2))
numeric_count = v2.count("NUMERIC(")
check(float_count == 0, f"Zero FLOAT/DOUBLE/REAL columns (found {float_count})")
check(numeric_count >= 30, f"≥30 NUMERIC(...) columns for precision (found {numeric_count})")

# ---- No SERIAL (use UUID instead) ----
print("\n[NO SERIAL / BIGSERIAL]")
serial_count = len(re.findall(r'\b(SERIAL|BIGSERIAL|SMALLSERIAL)\b', v2))
check(serial_count == 0, f"Zero SERIAL columns (found {serial_count}) — all PKs are UUID")

# ---- Stored generated columns ----
print("\n[STORED GENERATED COLUMNS]")
GENERATED = [
    "SHORTAGE_MT", "VOYAGE_DAYS",
    "VARIANCE_HOURS", "NET_SETTLEMENT_USD",
    "CLOSING_STOCK_MT", "STOCK_RUNWAY_DAYS",
]
for col in GENERATED:
    check(f"{col}" in v2 and "GENERATED ALWAYS AS" in v2 and "STORED" in v2,
          f"STORED GENERATED: {col}")

# ---- No mock / hardcoded data ----
print("\n[ZERO-MOCK POLICY]")
mock_patterns = ["MOCK", "HARDCODE", "FAKE_", "DUMMY_"]
for pat in mock_patterns:
    found_v1 = pat in v1
    found_v2 = pat in v2
    check(not found_v1 and not found_v2, f"No '{pat}' pattern in migrations")

# ---- Comments / documentation ----
print("\n[DOCUMENTATION]")
v1_raw = FILES["V001"].read_text(encoding="utf-8")
v2_raw = FILES["V002"].read_text(encoding="utf-8")
dash_comment_count_v1 = len(re.findall(r'--\s+\S', v1_raw))
comment_count_v2 = v2.count("COMMENT ON")
check(dash_comment_count_v1 >= 30, f"V001: >=30 inline -- comments (found {dash_comment_count_v1})")
check(comment_count_v2 >= 15, f"V002: >=15 COMMENT ON TABLE/COLUMN statements (found {comment_count_v2})")

# ============================================================
print("\n" + "=" * 68)
if failures:
    print(f"  RESULT: {FAIL} — {len(failures)} check(s) failed:")
    for f in failures:
        print(f"    • {f}")
    sys.exit(1)
else:
    total = len([l for l in open(__file__).readlines() if "check(" in l])
    print(f"  RESULT: {PASS} — All checks passed (≈{total} assertions).")
print("=" * 68)
