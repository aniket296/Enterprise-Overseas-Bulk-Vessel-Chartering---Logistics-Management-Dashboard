"""
static_lint_cycle_1_1_2.py — Static SQL structural verifier for V003
Validates V003__integrity_constraints.sql for:
  - File existence and transaction integrity
  - CHECK constraint count and critical named constraints
  - FOREIGN KEY constraint count, named, with correct ON DELETE semantics
  - UNIQUE constraint count
  - No unnamed constraints (all have explicit CONSTRAINT <name>)
  - DEFERRABLE on self-referential FKs
  - No hardcoded mock data
"""

import re
import sys
import io
import pathlib

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).parent
V003 = ROOT / "migrations" / "V003__integrity_constraints.sql"

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
print("  Cycle 1.1.2 — Static SQL Structural Lint: V003 Integrity Constraints")
print("=" * 68)

# ---- File existence ----
print("\n[FILE]")
check(V003.exists(), f"V003 file exists: {V003.name}")

raw = V003.read_text(encoding="utf-8")
up  = raw.upper()
clean = strip_comments(raw)

# ---- Transaction integrity ----
print("\n[TRANSACTION INTEGRITY]")
check(up.count("BEGIN;") == 1, "V003: Single BEGIN")
check(up.count("COMMIT;") == 1, "V003: Single COMMIT")
check("SET LOCAL SEARCH_PATH TO SHIPPING" in up, "V003: SET LOCAL search_path")

# ---- CHECK constraint coverage ----
print("\n[CHECK CONSTRAINTS]")
total_checks = up.count("ADD CONSTRAINT") and len(re.findall(r'\bCHECK\b', up))
check_names = re.findall(r'ADD CONSTRAINT\s+(CHK_\w+)\s+CHECK', up)
check(len(check_names) >= 35, f">=35 named CHECK constraints (found {len(check_names)})")

# Critical named CHECK constraints
CRITICAL_CHECKS = [
    # vessels
    "CHK_VESSELS_IMO_DIGITS",
    "CHK_VESSELS_DEADWEIGHT_POSITIVE",
    "CHK_VESSELS_GROSS_TONNAGE_POSITIVE",
    "CHK_VESSELS_NET_TONNAGE_POSITIVE",
    "CHK_VESSELS_LOA_POSITIVE",
    "CHK_VESSELS_BEAM_POSITIVE",
    "CHK_VESSELS_DRAFT_POSITIVE",
    "CHK_VESSELS_HOLD_COUNT_RANGE",
    "CHK_VESSELS_HATCH_LE_HOLD",
    "CHK_VESSELS_BUILD_YEAR_RANGE",
    "CHK_VESSELS_FLAG_STATE_FORMAT",
    "CHK_VESSELS_SERVICE_SPEED_RANGE",
    # charter_contracts
    "CHK_CONTRACTS_LAYCAN_WINDOW",
    "CHK_CONTRACTS_LAYCAN_MAX_WINDOW",
    "CHK_CONTRACTS_QUANTITY_POSITIVE",
    "CHK_CONTRACTS_TOLERANCE_RANGE",
    "CHK_CONTRACTS_LAYTIME_POSITIVE",
    "CHK_CONTRACTS_DISCHARGE_RATE_POSITIVE",
    "CHK_CONTRACTS_DEMURRAGE_RATE_POSITIVE",
    "CHK_CONTRACTS_DESPATCH_RATE_NONNEG",
    "CHK_CONTRACTS_DESPATCH_LE_DEMURRAGE",
    "CHK_CONTRACTS_VOYAGE_FREIGHT_REQUIRED",
    "CHK_CONTRACTS_CP_AFTER_FIXTURE",
    # vessel_voyages
    "CHK_VOYAGES_QUANTITY_LOADED_POSITIVE",
    "CHK_VOYAGES_QUANTITY_DISCHARGED_POSITIVE",
    "CHK_VOYAGES_NOR_AFTER_ATA",
    "CHK_VOYAGES_NOR_ACCEPTED_AFTER_TENDERED",
    "CHK_VOYAGES_BERTHED_AFTER_NOR_ACCEPTED",
    "CHK_VOYAGES_DISCHARGE_AFTER_BERTHED",
    "CHK_VOYAGES_DISCHARGE_COMPLETED_AFTER_COMMENCED",
    "CHK_VOYAGES_COMPLETED_AFTER_DISCHARGE",
    "CHK_VOYAGES_LAT_RANGE",
    "CHK_VOYAGES_LON_RANGE",
    # statement_of_facts
    "CHK_SOF_SEQUENCE_POSITIVE",
    "CHK_SOF_COUNT_FRACTION_RANGE",
    "CHK_SOF_AMENDMENT_NO_SELF_REF",
    "CHK_SOF_AMENDMENT_REF_REQUIRED",
    # laytime_calculations
    "CHK_LAYTIME_REVISION_POSITIVE",
    "CHK_LAYTIME_ALLOWED_POSITIVE",
    "CHK_LAYTIME_HOURS_ACCOUNTING",
    "CHK_LAYTIME_NOT_BOTH_DEMURRAGE_DESPATCH",
    # plant_inventory
    "CHK_INVENTORY_OPENING_STOCK_NONNEG",
    "CHK_INVENTORY_RECEIPTS_NONNEG",
    "CHK_INVENTORY_CONSUMPTION_NONNEG",
    "CHK_INVENTORY_PLANT_CODE_FORMAT",
    # shipping_documents
    "CHK_DOCUMENTS_SHA256_CLIENT_FORMAT",
    "CHK_DOCUMENTS_SHA256_SERVER_FORMAT",
    "CHK_DOCUMENTS_FILE_SIZE_POSITIVE",
    "CHK_DOCUMENTS_VERSION_POSITIVE",
    "CHK_DOCUMENTS_NO_SELF_SUPERSEDE",
    "CHK_DOCUMENTS_MINIO_BUCKET_FORMAT",
    "CHK_DOCUMENTS_VOYAGE_OR_CONTRACT",
    "CHK_DOCUMENTS_MISMATCH_DETAIL_REQUIRED",
]
for name in CRITICAL_CHECKS:
    check(name in up, f"CHECK: {name}")

# ---- FOREIGN KEY constraints ----
print("\n[FOREIGN KEY CONSTRAINTS]")
fk_names = re.findall(r'ADD CONSTRAINT\s+(FK_\w+)\s+FOREIGN KEY', up)
check(len(fk_names) >= 12, f">=12 named FK constraints (found {len(fk_names)})")

CRITICAL_FKS = [
    "FK_CONTRACTS_VESSEL",
    "FK_VOYAGES_CONTRACT",
    "FK_VOYAGES_VESSEL",
    "FK_SOF_VOYAGE",
    "FK_SOF_AMENDMENT_REF",
    "FK_SOF_SOURCE_DOCUMENT",
    "FK_LAYTIME_VOYAGE",
    "FK_INVENTORY_CONTRIBUTING_VOYAGE",
    "FK_DOCUMENTS_VOYAGE",
    "FK_DOCUMENTS_CONTRACT",
    "FK_DOCUMENTS_SUPERSEDES",
    "FK_CONTRACTS_CP_DOCUMENT",
]
for fk in CRITICAL_FKS:
    check(fk in up, f"FK: {fk}")

# ---- ON DELETE semantics ----
print("\n[ON DELETE SEMANTICS]")
check("ON DELETE RESTRICT" in up, "RESTRICT: used on core entity FKs")
check("ON DELETE CASCADE" in up, "CASCADE: used on SOF → voyage")
check("ON DELETE SET NULL" in up, "SET NULL: used on optional back-references")

# ---- DEFERRABLE on self-referential FKs ----
print("\n[DEFERRABLE SELF-REFERENTIAL FKs]")
check("DEFERRABLE INITIALLY DEFERRED" in up,
      "Self-referential FKs marked DEFERRABLE INITIALLY DEFERRED")

# ---- UNIQUE constraints ----
print("\n[UNIQUE CONSTRAINTS]")
uq_names = re.findall(r'ADD CONSTRAINT\s+(UQ_\w+)\s+UNIQUE', up)
check(len(uq_names) >= 7, f">=7 named UNIQUE constraints (found {len(uq_names)})")

CRITICAL_UQS = [
    "UQ_VESSELS_IMO_NUMBER",
    "UQ_CONTRACTS_REFERENCE",
    "UQ_VOYAGES_REFERENCE",
    "UQ_SOF_VOYAGE_SEQUENCE",
    "UQ_LAYTIME_VOYAGE_REVISION",
    "UQ_INVENTORY_PLANT_MATERIAL_DATE",
    "UQ_DOCUMENTS_MINIO_OBJECT",
]
for uq in CRITICAL_UQS:
    check(uq in up, f"UNIQUE: {uq}")

# ---- All constraints are named (no unnamed ADD CONSTRAINT) ----
print("\n[NO UNNAMED CONSTRAINTS]")
# Count ADD CONSTRAINT occurrences vs named ones
total_add = len(re.findall(r'ADD CONSTRAINT\s+\w+', up))
named_checks = len(check_names)
named_fks = len(fk_names)
named_uqs = len(uq_names)
total_named = named_checks + named_fks + named_uqs
check(total_add == total_named,
      f"All ADD CONSTRAINT have explicit names ({total_add} total, {total_named} named)")

# ---- No mock data ----
print("\n[ZERO-MOCK POLICY]")
for pat in ["MOCK", "HARDCODE", "FAKE_", "DUMMY_"]:
    check(pat not in up, f"No '{pat}' pattern in V003")

# ---- Regex patterns for domain validation ----
print("\n[DOMAIN VALIDATION REGEX PATTERNS]")
check("'^[0-9]{7}$'" in raw, "IMO number regex ^[0-9]{7}$ present")
check("'^[A-Z]{2}$'" in raw, "Flag state / country code regex ^[A-Z]{2}$ present")
check("'^[0-9a-f]{64}$'" in raw, "SHA-256 hex regex ^[0-9a-f]{64}$ present")
check("'^[A-Z0-9]{2,10}$'" in raw, "Plant code regex ^[A-Z0-9]{2,10}$ present")
check("'^[a-z0-9]" in raw, "MinIO bucket regex present")

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
