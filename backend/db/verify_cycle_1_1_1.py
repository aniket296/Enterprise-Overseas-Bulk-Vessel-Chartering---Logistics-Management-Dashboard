"""
verify_cycle_1_1_1.py
Cycle 1.1.1 — Verification Script
Connects to a running PostgreSQL 16 instance (via env vars or .env) and
validates that all enums and tables from V001 / V002 exist with the
expected structure.

Usage (inside Docker or with local psycopg2):
    python backend/db/verify_cycle_1_1_1.py

Environment variables:
    DB_HOST     (default: localhost)
    DB_PORT     (default: 5432)
    DB_NAME     (default: shipping_dev)
    DB_USER     (default: shipping_user)
    DB_PASSWORD (required)
"""

import os
import sys
import textwrap

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
DSN = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "dbname": os.getenv("DB_NAME", "shipping_dev"),
    "user": os.getenv("DB_USER", "shipping_user"),
    "password": os.getenv("DB_PASSWORD", ""),
}

# ---------------------------------------------------------------------------
# Expected artifacts
# ---------------------------------------------------------------------------
EXPECTED_ENUMS = [
    "vessel_size_class",
    "cargo_type",
    "charter_party_type",
    "contract_status",
    "laytime_terms",
    "settlement_currency",
    "voyage_status",
    "voyage_direction",
    "port_function",
    "sof_event_type",
    "interval_count_status",
    "laytime_outcome",
    "document_type",
    "document_verification_status",
    "stock_safety_status",
    "raw_material_category",
    "user_role",
    "alert_severity",
    "alert_status",
]

EXPECTED_TABLES = {
    "vessels": [
        "vessel_id", "imo_number", "vessel_name", "size_class",
        "flag_state", "deadweight_tonnes", "gross_tonnage", "net_tonnage",
        "loa_metres", "beam_metres", "draft_laden_metres",
        "hold_count", "hatch_count", "build_year",
        "owner_name", "created_at", "updated_at", "is_active",
    ],
    "charter_contracts": [
        "contract_id", "contract_reference", "charterer_entity",
        "owner_entity", "vessel_id", "charter_party_type",
        "contract_status", "cargo_type", "nominated_quantity_mt",
        "laytime_terms", "allowed_laytime_hours",
        "discharge_rate_mt_per_day", "demurrage_rate_usd_per_day",
        "despatch_rate_usd_per_day", "once_on_demurrage_always_on",
        "laycan_start", "laycan_end", "created_at", "updated_at",
    ],
    "vessel_voyages": [
        "voyage_id", "voyage_reference", "contract_id", "vessel_id",
        "voyage_status", "voyage_direction",
        "load_port", "discharge_port",
        "nor_tendered_at", "nor_accepted_at",
        "discharge_commenced_at", "discharge_completed_at",
        "quantity_loaded_mt", "quantity_discharged_mt",
        "shortage_mt",   # generated
        "voyage_days",   # generated
        "created_at", "updated_at",
    ],
    "statement_of_facts": [
        "sof_id", "voyage_id", "event_type", "event_timestamp",
        "sequence_number", "count_status", "count_fraction",
        "counted_hours", "is_amendment", "amended_sof_id",
        "submitted_at",
    ],
    "laytime_calculations": [
        "calc_id", "voyage_id", "revision", "is_current",
        "outcome", "allowed_laytime_hours", "total_counted_hours",
        "variance_hours",      # generated
        "net_settlement_usd",  # generated
        "created_at", "updated_at",
    ],
    "plant_inventory": [
        "inventory_id", "plant_code", "plant_name",
        "material_category", "stock_date",
        "opening_stock_mt", "receipts_mt", "consumption_mt",
        "closing_stock_mt",         # generated
        "stock_runway_days",        # generated
        "safety_status", "created_at", "updated_at",
    ],
    "shipping_documents": [
        "document_id", "document_type", "document_title",
        "voyage_id", "contract_id",
        "original_filename", "minio_bucket", "minio_object_key",
        "sha256_hash_client", "sha256_hash_server",
        "verification_status", "uploaded_at", "created_at", "updated_at",
    ],
}

# ---------------------------------------------------------------------------
# Verification logic
# ---------------------------------------------------------------------------
PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"
failures: list[str] = []


def check(condition: bool, label: str) -> None:
    if condition:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}")
        failures.append(label)


def run_verification(cur: "RealDictCursor") -> None:
    # ---- 1. Schema existence ------------------------------------------------
    cur.execute(
        "SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'shipping';"
    )
    check(cur.fetchone() is not None, "Schema 'shipping' exists")

    # ---- 2. ENUM existence --------------------------------------------------
    print("\n[ENUMS]")
    cur.execute(
        """
        SELECT typname
        FROM   pg_type t
        JOIN   pg_namespace n ON n.oid = t.typnamespace
        WHERE  n.nspname = 'shipping'
          AND  t.typtype  = 'e';
        """
    )
    existing_enums = {row["typname"] for row in cur.fetchall()}
    for enum_name in EXPECTED_ENUMS:
        check(enum_name in existing_enums, f"ENUM shipping.{enum_name}")

    # ---- 3. TABLE existence & column presence -------------------------------
    print("\n[TABLES & COLUMNS]")
    for table_name, expected_columns in EXPECTED_TABLES.items():
        cur.execute(
            """
            SELECT column_name
            FROM   information_schema.columns
            WHERE  table_schema = 'shipping'
              AND  table_name   = %s;
            """,
            (table_name,),
        )
        existing_cols = {row["column_name"] for row in cur.fetchall()}
        check(bool(existing_cols), f"Table shipping.{table_name} exists")
        for col in expected_columns:
            check(col in existing_cols, f"  shipping.{table_name}.{col}")

    # ---- 4. Generated columns are STORED ------------------------------------
    print("\n[GENERATED COLUMNS — STORED]")
    generated_cols = [
        ("vessel_voyages",        "shortage_mt"),
        ("vessel_voyages",        "voyage_days"),
        ("laytime_calculations",  "variance_hours"),
        ("laytime_calculations",  "net_settlement_usd"),
        ("plant_inventory",       "closing_stock_mt"),
        ("plant_inventory",       "stock_runway_days"),
    ]
    cur.execute(
        """
        SELECT table_name, column_name
        FROM   information_schema.columns
        WHERE  table_schema  = 'shipping'
          AND  is_generated  = 'ALWAYS';
        """
    )
    stored_gen = {(r["table_name"], r["column_name"]) for r in cur.fetchall()}
    for tbl, col in generated_cols:
        check((tbl, col) in stored_gen, f"STORED GENERATED: {tbl}.{col}")

    # ---- 5. Primary key names -----------------------------------------------
    print("\n[PRIMARY KEYS]")
    pk_checks = [
        ("vessels",               "vessels_pkey"),
        ("charter_contracts",     "charter_contracts_pkey"),
        ("vessel_voyages",        "vessel_voyages_pkey"),
        ("statement_of_facts",    "statement_of_facts_pkey"),
        ("laytime_calculations",  "laytime_calculations_pkey"),
        ("plant_inventory",       "plant_inventory_pkey"),
        ("shipping_documents",    "shipping_documents_pkey"),
    ]
    cur.execute(
        """
        SELECT tc.table_name, tc.constraint_name
        FROM   information_schema.table_constraints tc
        WHERE  tc.table_schema    = 'shipping'
          AND  tc.constraint_type = 'PRIMARY KEY';
        """
    )
    pks = {(r["table_name"], r["constraint_name"]) for r in cur.fetchall()}
    for tbl, pk_name in pk_checks:
        check((tbl, pk_name) in pks, f"PK: {tbl} → {pk_name}")

    # ---- 6. NUMERIC precision for financial columns -------------------------
    print("\n[NUMERIC PRECISION — FINANCIAL COLUMNS]")
    financial_cols = [
        ("charter_contracts",    "nominated_quantity_mt",          10, 3),
        ("charter_contracts",    "demurrage_rate_usd_per_day",     12, 4),
        ("charter_contracts",    "despatch_rate_usd_per_day",      12, 4),
        ("laytime_calculations", "allowed_laytime_hours",          12, 6),
        ("laytime_calculations", "gross_demurrage_usd",            14, 4),
    ]
    for tbl, col, expected_precision, expected_scale in financial_cols:
        cur.execute(
            """
            SELECT numeric_precision, numeric_scale
            FROM   information_schema.columns
            WHERE  table_schema = 'shipping'
              AND  table_name   = %s
              AND  column_name  = %s;
            """,
            (tbl, col),
        )
        row = cur.fetchone()
        if row:
            ok = (
                row["numeric_precision"] == expected_precision
                and row["numeric_scale"] == expected_scale
            )
            check(
                ok,
                f"NUMERIC({expected_precision},{expected_scale}): {tbl}.{col} "
                f"[got ({row['numeric_precision']},{row['numeric_scale']})]",
            )
        else:
            check(False, f"Column not found: {tbl}.{col}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    print("=" * 70)
    print("  Cycle 1.1.1 — Schema Verification: Enums & Core Tables")
    print("=" * 70)
    print(f"  Connecting to {DSN['host']}:{DSN['port']}/{DSN['dbname']} …")

    try:
        conn = psycopg2.connect(**DSN)
        conn.autocommit = True
    except psycopg2.OperationalError as exc:
        print(f"\n{FAIL} Cannot connect to PostgreSQL: {exc}")
        print(
            textwrap.dedent(
                """
                  TIP: Ensure the database is running. For Docker:
                    docker compose up -d db
                  Then set environment variables:
                    DB_HOST=localhost DB_PORT=5432 DB_NAME=shipping_dev \\
                    DB_USER=shipping_user DB_PASSWORD=<password> \\
                    python backend/db/verify_cycle_1_1_1.py
                """
            )
        )
        sys.exit(1)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        run_verification(cur)

    conn.close()

    print("\n" + "=" * 70)
    if failures:
        print(f"  RESULT: {FAIL} — {len(failures)} check(s) failed:")
        for f in failures:
            print(f"    • {f}")
        sys.exit(1)
    else:
        print(f"  RESULT: {PASS} — All checks passed.")
    print("=" * 70)


if __name__ == "__main__":
    main()
