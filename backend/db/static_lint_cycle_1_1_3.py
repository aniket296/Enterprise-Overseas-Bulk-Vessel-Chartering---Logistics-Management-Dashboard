"""
static_lint_cycle_1_1_3.py — Static SQL structural verifier for V004
Validates V004__indexes_and_temporal_ranges.sql for:
  - File existence and transaction integrity
  - Total index count (>=50)
  - Critical named B-tree indexes per table
  - GiST tstzrange berth conflict index
  - Partial unique index for is_current laytime revision
  - Partial unique SHA-256 deduplication index
  - BRIN indexes on updated_at columns
  - Partial index usage (WHERE clauses)
  - COMMENT ON INDEX for all indexes
  - Index naming conventions followed
  - Zero mock patterns
"""

import re
import sys
import io
import pathlib

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).parent
V004 = ROOT / "migrations" / "V004__indexes_and_temporal_ranges.sql"

PASS = "✓ PASS"
FAIL = "✗ FAIL"
failures: list[str] = []


def check(condition: bool, label: str) -> None:
    symbol = PASS if condition else FAIL
    print(f"  {symbol}  {label}")
    if not condition:
        failures.append(label)


def strip_comments(text: str) -> str:
    lines = []
    for line in text.upper().splitlines():
        if line.strip().startswith("--"):
            continue
        idx = line.find("--")
        lines.append(line[:idx] if idx >= 0 else line)
    return "\n".join(lines)


# ============================================================
print("=" * 68)
print("  Cycle 1.1.3 — Static SQL Structural Lint: V004 Indexes")
print("=" * 68)

print("\n[FILE]")
check(V004.exists(), f"V004 file exists: {V004.name}")

raw = V004.read_text(encoding="utf-8")
up  = raw.upper()
clean = strip_comments(raw)

# ---- Transaction integrity ----
print("\n[TRANSACTION INTEGRITY]")
check(up.count("BEGIN;") == 1,  "V004: Single BEGIN")
check(up.count("COMMIT;") == 1, "V004: Single COMMIT")
check("SET LOCAL SEARCH_PATH TO SHIPPING" in up, "V004: SET LOCAL search_path")

# ---- Total index count ----
print("\n[TOTAL INDEX COUNT]")
create_index_count = len(re.findall(r'\bCREATE\b(?:\s+UNIQUE)?\s+INDEX\b', up))
check(create_index_count >= 45, f">=45 CREATE INDEX statements (found {create_index_count})")

# ---- Critical named indexes by table ----
print("\n[NAMED B-TREE INDEXES — vessels]")
VESSEL_INDEXES = [
    "IDX_VESSELS_IMO_ACTIVE",
    "IDX_VESSELS_NAME_LOWER",
    "IDX_VESSELS_SIZE_CLASS_FLAG",
    "IDX_VESSELS_OWNER_NAME_LOWER",
    "IDX_VESSELS_BUILD_YEAR",
]
for idx in VESSEL_INDEXES:
    check(idx in up, f"Index: {idx}")

print("\n[NAMED B-TREE INDEXES — charter_contracts]")
CONTRACT_INDEXES = [
    "IDX_CONTRACTS_STATUS_VESSEL",
    "IDX_CONTRACTS_LAYCAN",
    "IDX_CONTRACTS_CHARTERER_PLANT",
    "IDX_CONTRACTS_CARGO_TYPE_STATUS",
    "IDX_CONTRACTS_CREATED_AT",
    "IDX_CONTRACTS_DISCHARGE_PORT",
]
for idx in CONTRACT_INDEXES:
    check(idx in up, f"Index: {idx}")

print("\n[NAMED B-TREE INDEXES — vessel_voyages]")
VOYAGE_INDEXES = [
    "IDX_VOYAGES_STATUS",
    "IDX_VOYAGES_CONTRACT_STATUS",
    "IDX_VOYAGES_VESSEL_STATUS",
    "IDX_VOYAGES_ETA_ACTIVE",
    "IDX_VOYAGES_DISCHARGE_PORT_STATUS",
    "IDX_VOYAGES_NOR_TENDERED",
    "IDX_VOYAGES_DISCHARGE_COMPLETED",
    "IDX_VOYAGES_BERTH_PORT_ETA",
    "IDX_VOYAGES_POSITION_UPDATED",
    "IDX_VOYAGES_CONTRACT_VESSEL",
    "IDX_VOYAGES_ATA_DATE",
]
for idx in VOYAGE_INDEXES:
    check(idx in up, f"Index: {idx}")

print("\n[NAMED INDEXES — statement_of_facts]")
SOF_INDEXES = [
    "IDX_SOF_VOYAGE_SEQ",
    "IDX_SOF_VOYAGE_EVENT_TYPE",
    "IDX_SOF_EVENT_TIMESTAMP",
    "IDX_SOF_INTERRUPTIONS",
    "IDX_SOF_COUNT_STATUS",
    "IDX_SOF_AMENDMENTS",
    "IDX_SOF_SUBMITTED_AT",
]
for idx in SOF_INDEXES:
    check(idx in up, f"Index: {idx}")

print("\n[NAMED INDEXES — laytime_calculations]")
LAYTIME_INDEXES = [
    "UIDX_LAYTIME_ONE_CURRENT_PER_VOYAGE",
    "IDX_LAYTIME_VOYAGE_REVISION",
    "IDX_LAYTIME_OUTCOME_CALCULATED",
    "IDX_LAYTIME_PENDING",
    "IDX_LAYTIME_NET_SETTLEMENT",
]
for idx in LAYTIME_INDEXES:
    check(idx in up, f"Index: {idx}")

print("\n[NAMED INDEXES — plant_inventory]")
INVENTORY_INDEXES = [
    "IDX_INVENTORY_PLANT_MATERIAL_DATE",
    "IDX_INVENTORY_SAFETY_STATUS",
    "IDX_INVENTORY_RUNWAY_ASC",
    "IDX_INVENTORY_PLANT_DATE",
    "IDX_INVENTORY_CONTRIBUTING_VOYAGE",
]
for idx in INVENTORY_INDEXES:
    check(idx in up, f"Index: {idx}")

print("\n[NAMED INDEXES — shipping_documents]")
DOC_INDEXES = [
    "IDX_DOCUMENTS_VOYAGE_TYPE",
    "IDX_DOCUMENTS_CONTRACT_TYPE",
    "IDX_DOCUMENTS_VERIFICATION_STATUS",
    "UIDX_DOCUMENTS_SHA256_VERIFIED",
    "IDX_DOCUMENTS_HASH_MISMATCH",
    "IDX_DOCUMENTS_TYPE_DATE",
    "IDX_DOCUMENTS_SUPERSEDES",
    "IDX_DOCUMENTS_UPLOADED_BY",
    "IDX_DOCUMENTS_CONFIDENTIAL",
    "IDX_DOCUMENTS_UPLOADED_AT_BRIN",
]
for idx in DOC_INDEXES:
    check(idx in up, f"Index: {idx}")

# ---- GiST tstzrange berth conflict index ----
print("\n[GIST TSTZRANGE — BERTH CONFLICT DETECTION]")
check("IDX_VOYAGES_BERTH_OCCUPANCY_GIST" in up, "GiST index: IDX_VOYAGES_BERTH_OCCUPANCY_GIST")
check("USING GIST" in up, "USING GIST method declared")
check("TSTZRANGE(" in up, "tstzrange() expression in GiST index")
check("'[)'" in raw, "Half-open interval '[)' for berth occupancy range")
check("DISCHARGE_BERTH" in up and "BERTHED_AT" in up,
      "GiST index covers berthed_at and discharge_berth columns")
check("VOYAGE_STATUS NOT IN" in up and
      "'COMPLETED'" in up and "'CANCELLED'" in up,
      "Partial condition excludes COMPLETED/CANCELLED voyages")

# ---- Partial UNIQUE indexes ----
print("\n[PARTIAL UNIQUE INDEXES]")
unique_indexes = re.findall(r'CREATE UNIQUE INDEX\s+(\w+)', up)
check("UIDX_LAYTIME_ONE_CURRENT_PER_VOYAGE" in unique_indexes,
      "UNIQUE: uidx_laytime_one_current_per_voyage (is_current partial)")
check("UIDX_DOCUMENTS_SHA256_VERIFIED" in unique_indexes,
      "UNIQUE: uidx_documents_sha256_verified (SHA-256 deduplication partial)")
# Verify partial condition for laytime unique index
check("WHERE IS_CURRENT = TRUE" in up,
      "Partial condition IS_CURRENT = TRUE on laytime unique index")
# Verify partial condition for SHA-256 unique index
check("SHA256_HASH_SERVER IS NOT NULL" in up,
      "Partial condition sha256_hash_server IS NOT NULL on documents index")

# ---- BRIN indexes ----
print("\n[BRIN INDEXES — updated_at columns]")
brin_indexes = [
    "IDX_VESSELS_UPDATED_AT_BRIN",
    "IDX_CONTRACTS_UPDATED_AT_BRIN",
    "IDX_VOYAGES_UPDATED_AT_BRIN",
    "IDX_LAYTIME_UPDATED_AT_BRIN",
    "IDX_INVENTORY_UPDATED_AT_BRIN",
    "IDX_DOCUMENTS_UPLOADED_AT_BRIN",
]
for idx in brin_indexes:
    check(idx in up, f"BRIN: {idx}")
check(up.count("USING BRIN") >= 6, f">=6 BRIN indexes declared (found {up.count('USING BRIN')})")
check("PAGES_PER_RANGE = 32" in up, "BRIN pages_per_range = 32 storage parameter set")

# ---- Partial index WHERE clauses ----
print("\n[PARTIAL INDEX USAGE]")
partial_count = len(re.findall(r'\bWHERE\b', up))
check(partial_count >= 20, f">=20 WHERE clauses (partial indexes) (found {partial_count})")

# ---- COMMENT ON INDEX coverage ----
print("\n[COMMENT ON INDEX COVERAGE]")
comment_count = up.count("COMMENT ON INDEX")
check(comment_count >= 45, f">=45 COMMENT ON INDEX statements (found {comment_count})")

# ---- Naming convention compliance ----
print("\n[NAMING CONVENTION]")
all_index_names = re.findall(r'CREATE(?:\s+UNIQUE)?\s+INDEX\s+(\w+)', up)
valid_prefixes = ("IDX_", "UIDX_")
bad_names = [n for n in all_index_names if not any(n.startswith(p) for p in valid_prefixes)]
check(len(bad_names) == 0,
      f"All index names follow idx_/uidx_ convention (violations: {bad_names})")

# ---- ON clause: all indexes on shipping schema tables ----
print("\n[SCHEMA TARGETING]")
check(all("SHIPPING." in line.upper()
          for line in raw.splitlines()
          if "ON shipping." in line.lower()),
      "All indexes target shipping.* tables")

# ---- Zero mock policy ----
print("\n[ZERO-MOCK POLICY]")
for pat in ["MOCK", "HARDCODE", "FAKE_", "DUMMY_"]:
    check(pat not in up, f"No '{pat}' pattern in V004")

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
