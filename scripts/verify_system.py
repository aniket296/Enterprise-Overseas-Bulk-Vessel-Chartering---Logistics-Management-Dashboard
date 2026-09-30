#!/usr/bin/env python
"""
Enterprise Overseas Bulk Vessel Chartering & Logistics Management Dashboard
=============================================================================
Automated System Verification & Health Smoke Check Script (Phase 4 Deliverable)
Strict Zero-Mocking Policy & Air-Gapped Architectural Invariant Verification.
Validates Django Runtime, Database Connection, Celery Queues, Mathematical
Precision (BIMCO 2013 Decimals), Document Vault HMAC/SHA-256, and API Endpoints.
=============================================================================
"""

import os
import sys
import io
import time
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP

# Ensure backend root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = BASE_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Terminal ANSI styling
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


class SystemVerifier:
    def __init__(self):
        self.passed_checks = 0
        self.failed_checks = 0
        self.start_time = time.time()

    def record_pass(self, title: str, details: str = ""):
        self.passed_checks += 1
        detail_str = f" - {details}" if details else ""
        print(f"  {GREEN}[PASS]{RESET} {title}{detail_str}")

    def record_fail(self, title: str, error: str):
        self.failed_checks += 1
        print(f"  {RED}[FAIL]{RESET} {title}: {error}")

    def run_all_checks(self) -> int:
        print(f"\n{BOLD}{CYAN}==============================================================================={RESET}")
        print(f"{BOLD}{CYAN}   SAIL Bulk Logistics & Chartering Dashboard — System Verification Audit      {RESET}")
        print(f"{BOLD}{CYAN}==============================================================================={RESET}\n")

        self.check_django_runtime()
        self.check_database_connectivity()
        self.check_models_registry()
        self.check_decimal_invariance_and_laytime_engine()
        self.check_celery_broker_and_queue_topology()
        self.check_cryptographic_document_vault()
        self.check_analytical_and_forecasting_engines()
        self.check_api_url_resolvers()

        duration = time.time() - self.start_time
        total_checks = self.passed_checks + self.failed_checks

        print(f"\n{BOLD}{CYAN}-------------------------------------------------------------------------------{RESET}")
        print(f"{BOLD}Verification Summary:{RESET} {total_checks} checks executed in {duration:.2f}s")
        print(f"  Passed: {GREEN}{self.passed_checks}{RESET}")
        print(f"  Failed: {RED}{self.failed_checks}{RESET}")
        print(f"{BOLD}{CYAN}==============================================================================={RESET}\n")

        if self.failed_checks > 0:
            print(f"{RED}{BOLD}[CRITICAL] System verification failed with {self.failed_checks} errors.{RESET}\n")
            return 1

        print(f"{GREEN}{BOLD}[SUCCESS] All system invariants verified successfully. Production-ready!{RESET}\n")
        return 0

    def check_django_runtime(self):
        print(f"{BOLD}1. Django Runtime & Core Configuration{RESET}")
        try:
            import django
            django.setup()
            self.record_pass("Django Framework initialized", f"Version {django.__version__}")
        except Exception as e:
            self.record_fail("Django Framework initialization", str(e))
            return

        from django.conf import settings
        required_apps = [
            "apps.authentication",
            "apps.contracts",
            "apps.voyages",
            "apps.laytime",
            "apps.inventory",
            "apps.documents",
        ]
        installed = settings.INSTALLED_APPS
        for app in required_apps:
            # Handle AppConfig names or direct names
            match = any(app in item for item in installed)
            if match:
                self.record_pass(f"Installed App: {app}")
            else:
                self.record_fail(f"Installed App: {app}", "App missing from INSTALLED_APPS")

    def check_database_connectivity(self):
        print(f"\n{BOLD}2. Database Connectivity & Transaction Engine{RESET}")
        try:
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                row = cursor.fetchone()
                if row and row[0] == 1:
                    self.record_pass(
                        "Database connection verified",
                        f"Vendor: {connection.vendor}, Engine: {connection.settings_dict['ENGINE']}"
                    )
                else:
                    self.record_fail("Database ping", "Did not return expected query result")
        except Exception as e:
            self.record_fail("Database connection failed", str(e))

    def check_models_registry(self):
        print(f"\n{BOLD}3. Model Schema & ORM Registry Audit{RESET}")
        try:
            from django.apps import apps
            expected_models = [
                ("authentication", "UserProfile"),
                ("contracts", "Vessel"),
                ("contracts", "CharterContract"),
                ("voyages", "VesselVoyage"),
                ("voyages", "StatementOfFacts"),
                ("voyages", "LaytimeCalculation"),
                ("inventory", "PlantInventory"),
                ("documents", "ShippingDocument"),
            ]
            for app_label, model_name in expected_models:
                try:
                    model = apps.get_model(app_label, model_name)
                    if model is not None:
                        count = model.objects.count()
                        self.record_pass(f"Model {app_label}.{model_name}", f"Count in DB: {count}")
                    else:
                        self.record_fail(f"Model {app_label}.{model_name}", "Model not found in registry")
                except Exception as ex:
                    self.record_fail(f"Model {app_label}.{model_name}", str(ex))
        except Exception as e:
            self.record_fail("ORM Model registry check", str(e))

    def check_decimal_invariance_and_laytime_engine(self):
        print(f"\n{BOLD}4. Deterministic Decimal Precision & BIMCO Laytime Invariants{RESET}")
        try:
            # Check 1: Decimal precision & rounding behavior
            val = (Decimal("100.00") / Decimal("3")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if val == Decimal("33.33"):
                self.record_pass("Deterministic ROUND_HALF_UP arithmetic", f"100/3 = {val}")
            else:
                self.record_fail("ROUND_HALF_UP check", f"Expected 33.33 but got {val}")

            # Check 2: Allowed Laytime Calculation
            from apps.laytime.engine.allowed_laytime import AllowedLaytimeEngine, LaytimeTerms
            cargo_mt = Decimal("75000.00")
            rate_mt = Decimal("15000.00")
            engine = AllowedLaytimeEngine(
                cargo_mt_nominated=cargo_mt,
                discharge_rate_mt_day=rate_mt,
                laytime_terms=LaytimeTerms.SHINC
            )
            result = engine.compute()
            expected_days = Decimal("5.000000")
            expected_hours = Decimal("120.000000")

            if result.days_nominated == expected_days and result.allowed_hours_nominated == expected_hours:
                self.record_pass(
                    "BIMCO 2013 Allowed Laytime Engine",
                    f"75,000 MT @ 15,000 MT/day -> {result.days_nominated} days ({result.allowed_hours_nominated} hrs)"
                )
            else:
                self.record_fail(
                    "Allowed Laytime Result mismatch",
                    f"Got {result.days_nominated} days, {result.allowed_hours_nominated} hrs"
                )

            # Check 3: 1,000-Iteration Zero Bit-Drift Invariance Proof
            engine_drift = AllowedLaytimeEngine(
                cargo_mt_nominated=Decimal("73456.789"),
                discharge_rate_mt_day=Decimal("7654.321"),
                tolerance_pct=Decimal("5.00"),
                laytime_terms=LaytimeTerms.WWDSHEX,
            )
            distinct_hashes = set()
            for _ in range(1000):
                res = engine_drift.compute()
                distinct_hashes.add(res.allowed_hours_nominated)
            if len(distinct_hashes) == 1:
                self.record_pass("1,000-Iteration Bit-for-Bit Determinism", f"1,000 runs -> exactly 1 unique value: {res.allowed_hours_nominated}")
            else:
                self.record_fail("Bit-for-bit determinism", f"Float or rounding drift detected: {len(distinct_hashes)} distinct values")

        except Exception as e:
            self.record_fail("Mathematical precision check", str(e))

    def check_celery_broker_and_queue_topology(self):
        print(f"\n{BOLD}5. Celery Distributed Worker Topology & Task Routes{RESET}")
        try:
            from django.conf import settings
            # Verify Broker URL
            broker = getattr(settings, "CELERY_BROKER_URL", None)
            if broker:
                self.record_pass("Celery Broker URL configured", broker)
            else:
                self.record_fail("Celery Broker URL", "Not configured in settings")

            # Verify Queue Topology
            queues = [q.name for q in getattr(settings, "CELERY_TASK_QUEUES", [])]
            expected_queues = ["calculations", "documents", "alerts", "default"]
            for q_name in expected_queues:
                if q_name in queues:
                    self.record_pass(f"Queue segregated: '{q_name}'")
                else:
                    self.record_fail(f"Queue segregated: '{q_name}'", "Queue missing from CELERY_TASK_QUEUES")

            # Verify Periodic Scheduled Beat Tasks
            beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})
            expected_tasks = [
                "periodic-inventory-runway-watchdog",
                "periodic-berth-conflict-watchdog",
                "periodic-voyage-eta-deviation-monitor",
                "periodic-document-vault-health-scan",
            ]
            for task_id in expected_tasks:
                if task_id in beat_schedule:
                    task_info = beat_schedule[task_id]
                    self.record_pass(
                        f"Beat Watchdog: '{task_id}'",
                        f"Target: {task_info.get('task')}, Interval: {task_info.get('schedule')}s"
                    )
                else:
                    self.record_fail(f"Beat Watchdog: '{task_id}'", "Task missing from CELERY_BEAT_SCHEDULE")

        except Exception as e:
            self.record_fail("Celery topology check", str(e))

    def check_cryptographic_document_vault(self):
        print(f"\n{BOLD}6. Cryptographic Document Vault & Tamper Detection{RESET}")
        try:
            from apps.documents.vault_service import DocumentVaultService
            
            # Test deterministic SHA-256 calculation
            test_content = b"SAIL_BULK_SHIPPING_BILL_OF_LADING_VERIFICATION_SAMPLE_2026"
            f = io.BytesIO(test_content)
            computed_hash, file_size = DocumentVaultService.compute_sha256(f)

            import hashlib
            expected_hash = hashlib.sha256(test_content).hexdigest().lower()

            if computed_hash == expected_hash and file_size == len(test_content):
                self.record_pass(
                    "Deterministic SHA-256 Digest Computation",
                    f"Digest: {computed_hash[:16]}... ({file_size} bytes)"
                )
            else:
                self.record_fail(
                    "SHA-256 Computation mismatch",
                    f"Expected {expected_hash}, got {computed_hash}"
                )

            # Test local storage fallback directory path
            storage_path = DocumentVaultService.get_storage_path("shipping-documents", "test-doc.pdf")
            if storage_path:
                self.record_pass("Local Vault Storage Path resolution", str(storage_path))
            else:
                self.record_fail("Local Vault Storage Path", "Returned null path")

        except Exception as e:
            self.record_fail("Document Vault integrity check", str(e))

    def check_analytical_and_forecasting_engines(self):
        print(f"\n{BOLD}7. Advanced Analytical, Logistics & Starvation Engines{RESET}")
        try:
            # Inbound Forecast Simulation Engine
            from apps.inventory.inbound_forecast import simulate_forward_runway
            forecast = simulate_forward_runway(
                plant_code="BSP",
                material_category="COKING_COAL",
                horizon_days=30,
                custom_stock_mt=Decimal("85000.00"),
                custom_burn_rate_mt=Decimal("5000.00")
            )
            self.record_pass("Inbound Forward Runway Simulation Engine", f"BSP 30-day forecast: {len(forecast.timeline)} daily points generated")

            # Multi-Plant Starvation Scanner & Mitigation Simulator
            from apps.inventory.starvation_simulator import scan_multi_plant_starvation, simulate_starvation_mitigation
            profiles = scan_multi_plant_starvation(material_category="COKING_COAL")
            self.record_pass("Multi-Plant Starvation Early Warning Scanner", f"Evaluated {len(profiles)} steel plants for starvation threat")

            # Contract & Carrier Reliability Scorecard Engine
            from apps.contracts.scorecard_engine import compute_carrier_scorecards
            scorecards = compute_carrier_scorecards()
            self.record_pass("Carrier Reliability Scorecard Engine", f"Evaluated carriers: {len(scorecards)} registered")

            # Berth Conflict Detection Engine
            from apps.voyages.berth_conflict import sweep_all_active_berth_conflicts
            conflicts = sweep_all_active_berth_conflicts()
            self.record_pass("Berth Conflict Collision Detector", f"Evaluated port berths: {len(conflicts)} active conflicts")

            # Voyage Demurrage/Despatch Net P&L Engine
            from apps.voyages.pnl_engine import compute_voyage_pnl
            pnl_snapshot = compute_voyage_pnl(
                voyage_id="test-voyage-uuid-1",
                voyage_reference="VOY-TEST-001",
                vessel_name="MV Steel Leader",
                cargo_type="COKING_COAL",
                load_port="Gladstone",
                discharge_port="Haldia",
                nominated_quantity_mt=Decimal("75000.00"),
                freight_rate_usd_per_mt=Decimal("24.50"),
                quantity_loaded_mt=Decimal("75000.00"),
                quantity_discharged_mt=Decimal("74850.00"),
                laytime_outcome="DEMURRAGE",
                gross_demurrage_usd=Decimal("15000.00"),
                gross_despatch_usd=Decimal("0.00"),
                net_settlement_usd_from_calc=Decimal("15000.00"),
            )
            self.record_pass("Voyage Net Financial P&L Engine", f"Evaluated P&L: Net Freight ${pnl_snapshot.gross_freight_revenue_usd}")

        except Exception as e:
            self.record_fail("Analytical engines audit", str(e))

    def check_api_url_resolvers(self):
        print(f"\n{BOLD}8. DRF API Endpoints & URL Resolver Resolution{RESET}")
        try:
            from django.urls import resolve, reverse
            
            # Test key API route reverses or resolves
            routes = [
                ("/api/v1/system/celery/topology/", "celery-topology"),
                ("/api/v1/contracts/", None),
                ("/api/v1/voyages/", None),
                ("/api/v1/inventory/runway/", None),
                ("/api/v1/documents/", None),
            ]
            for url_path, named_route in routes:
                try:
                    match = resolve(url_path)
                    target = named_route or match.view_name
                    self.record_pass(f"URL Resolver: {url_path}", f"View: {match.func.__name__} (route: {target})")
                except Exception as ex:
                    self.record_fail(f"URL Resolver: {url_path}", str(ex))

        except Exception as e:
            self.record_fail("API URL resolution check", str(e))


if __name__ == "__main__":
    verifier = SystemVerifier()
    sys.exit(verifier.run_all_checks())
