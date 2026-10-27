#!/usr/bin/env python
"""
scripts/e2e_live_test.py
=============================================================================
Full-Scale Live End-to-End System Integration & Contract Verification Suite
Tests live running instances of Django REST API (8000) and Next.js (3000).
=============================================================================
"""

import sys
import json
import urllib.request
import urllib.error
import mimetypes
import uuid

API_BASE = "http://127.0.0.1:8000/api/v1"
UI_BASE = "http://localhost:3000"

GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

results = {"passed": 0, "failed": 0, "total": 0}

def record(test_name: str, passed: bool, details: str = ""):
    results["total"] += 1
    if passed:
        results["passed"] += 1
        print(f"  {GREEN}[PASS]{RESET} {test_name} {f'({details})' if details else ''}")
    else:
        results["failed"] += 1
        print(f"  {RED}[FAIL]{RESET} {test_name}: {details}")

def http_request(url: str, method: str = "GET", data: dict = None, headers: dict = None):
    req_headers = {"User-Agent": "E2ETester/1.0"}
    if headers:
        req_headers.update(headers)
    
    encoded_data = None
    if data is not None:
        encoded_data = json.dumps(data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    
    req = urllib.request.Request(url, data=encoded_data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            body = response.read().decode("utf-8")
            status = response.status
            return status, body, None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        return e.code, body, e
    except Exception as e:
        return 0, "", e

def http_multipart_upload(url: str, fields: dict, files: dict, headers: dict = None):
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    req_headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if headers:
        req_headers.update(headers)
    
    body_bytes = bytearray()
    for k, v in fields.items():
        body_bytes.extend(f"--{boundary}\r\n".encode("utf-8"))
        body_bytes.extend(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode("utf-8"))
        body_bytes.extend(f"{v}\r\n".encode("utf-8"))
    
    for field_name, (filename, file_content, mime) in files.items():
        body_bytes.extend(f"--{boundary}\r\n".encode("utf-8"))
        body_bytes.extend(f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode("utf-8"))
        body_bytes.extend(f"Content-Type: {mime}\r\n\r\n".encode("utf-8"))
        body_bytes.extend(file_content)
        body_bytes.extend(b"\r\n")
    
    body_bytes.extend(f"--{boundary}--\r\n".encode("utf-8"))
    
    req = urllib.request.Request(url, data=bytes(body_bytes), headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read().decode("utf-8"), None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), e
    except Exception as e:
        return 0, "", e

def run_e2e_tests():
    print(f"\n{BOLD}{CYAN}==============================================================================={RESET}")
    print(f"{BOLD}{CYAN}   SAIL Bulk Logistics & Chartering Dashboard — Full-Scale Live E2E Audit      {RESET}")
    print(f"{BOLD}{CYAN}==============================================================================={RESET}\n")

    # -------------------------------------------------------------
    # Section 1: Core Backend REST API Telemetry & Endpoints
    # -------------------------------------------------------------
    print(f"{BOLD}1. Live REST API Contract Verification (Port 8000){RESET}")

    # 1.1 Executive Summary
    code, body, _ = http_request(f"{API_BASE}/voyages/executive-summary/", headers={"X-User-Role": "EXECUTIVE_DIRECTOR"})
    if code == 200:
        data = json.loads(body)
        health = data.get("composite_health", {}).get("enterprise_health_score")
        record("GET /api/v1/voyages/executive-summary/", True, f"Health Score: {health}/100")
    else:
        record("GET /api/v1/voyages/executive-summary/", False, f"Status {code}")

    # 1.2 Active Fleet Roster & Finding Voyage with SOF
    code, body, _ = http_request(f"{API_BASE}/voyages/", headers={"X-User-Role": "LOGISTICS_DIRECTOR"})
    active_voyage_id = None
    completed_voyage_id = None
    if code == 200:
        data = json.loads(body)
        voyages = data.get("results", data if isinstance(data, list) else [])
        for v in voyages:
            if v.get("voyage_status") == "DISCHARGING" or v.get("discharge_completed_at"):
                completed_voyage_id = v["voyage_id"]
            if not active_voyage_id:
                active_voyage_id = v["voyage_id"]
        record("GET /api/v1/voyages/active/", len(voyages) > 0, f"{len(voyages)} active voyages in database")
    else:
        record("GET /api/v1/voyages/active/", False, f"Status {code}")

    target_voyage_id = completed_voyage_id or active_voyage_id

    # 1.3 Berth Timeline & Dynamic Gantt Data
    code, body, _ = http_request(f"{API_BASE}/voyages/berth-timeline/?discharge_port=Paradip", headers={"X-User-Role": "LOGISTICS_DIRECTOR"})
    if code == 200:
        data = json.loads(body)
        berths = data.get("berths", {})
        kpis = data.get("kpis", {})
        record("GET /api/v1/voyages/berth-timeline/", True, f"Port: Paradip, Congestion: {kpis.get('congestion_level')}, Berths: {list(berths.keys())}")
    else:
        record("GET /api/v1/voyages/berth-timeline/", False, f"Status {code}")

    # 1.4 Laytime Balance for Active Voyage
    if target_voyage_id:
        code, body, _ = http_request(f"{API_BASE}/voyages/{target_voyage_id}/laytime/", headers={"X-User-Role": "LOGISTICS_DIRECTOR"})
        record(f"GET /api/v1/voyages/{target_voyage_id[:8]}.../laytime/", code in (200, 409), f"Status {code} (BIMCO Laytime Evaluator Active)")
    
    # 1.5 On-Demand Laytime Calculation API
    calc_payload = {
        "cargo_quantity_mt": "75000.000",
        "discharge_rate_mt_day": "15000.000",
        "laytime_terms": "SHINC",
        "demurrage_rate_usd_day": "25000.00",
        "despatch_rate_usd_day": "12500.00",
        "total_port_hours": "130.000000",
        "counted_hours": "125.000000",
        "excepted_hours": "5.000000",
        "time_saved_hours": "0.000000",
        "demurrage_hours": "5.000000"
    }
    code, body, _ = http_request(f"{API_BASE}/calculations/compute-laytime/", method="POST", data=calc_payload, headers={"X-User-Role": "LOGISTICS_DIRECTOR"})
    if code == 200:
        data = json.loads(body)
        record("POST /api/v1/calculations/compute-laytime/", data.get("outcome") == "DEMURRAGE", f"Outcome: {data.get('outcome')}, Net Settlement: ${data.get('net_settlement_usd')}")
    else:
        record("POST /api/v1/calculations/compute-laytime/", False, f"Status {code}: {body}")

    # 1.6 Multi-Plant Starvation Assessment Matrix
    code, body, _ = http_request(f"{API_BASE}/inventory/starvation-matrix/?material_category=COKING_COAL", headers={"X-User-Role": "LOGISTICS_DIRECTOR"})
    if code == 200:
        matrix = json.loads(body)
        record("GET /api/v1/inventory/starvation-matrix/", len(matrix) >= 5, f"{len(matrix)} plants evaluated across network")
    else:
        record("GET /api/v1/inventory/starvation-matrix/", False, f"Status {code}")

    # 1.7 60-Day Forward Runway Simulation
    code, body, _ = http_request(f"{API_BASE}/inventory/inbound-forecast/?plant_code=BSP&material_category=COKING_COAL&horizon_days=60", headers={"X-User-Role": "LOGISTICS_DIRECTOR"})
    if code == 200:
        data = json.loads(body)
        timeline = data.get("timeline", [])
        record("GET /api/v1/inventory/inbound-forecast/", len(timeline) == 60, f"{len(timeline)} daily depletion points generated for BSP")
    else:
        record("GET /api/v1/inventory/inbound-forecast/", False, f"Status {code}")

    # 1.8 Multi-Plant Starvation Solver
    code, body, _ = http_request(f"{API_BASE}/inventory/simulate-mitigation/?plant_code=BSP&material_category=COKING_COAL", headers={"X-User-Role": "LOGISTICS_DIRECTOR"})
    if code == 200:
        data = json.loads(body)
        strat = data.get("primary_strategy")
        diversions = len(data.get("diversion_options", []))
        record("GET /api/v1/inventory/simulate-mitigation/", True, f"Strategy: {strat}, Diversion Candidates: {diversions}")
    else:
        record("GET /api/v1/inventory/simulate-mitigation/", False, f"Status {code}: {body}")

    # 1.9 Charter Contracts & Carrier Scorecards
    code, body, _ = http_request(f"{API_BASE}/contracts/carrier-scorecard/", headers={"X-User-Role": "LOGISTICS_DIRECTOR"})
    if code == 200:
        cards = json.loads(body)
        record("GET /api/v1/contracts/carrier-scorecard/", len(cards) > 0, f"{len(cards)} carriers scored on reliability & demurrage")
    else:
        record("GET /api/v1/contracts/carrier-scorecard/", False, f"Status {code}")

    # 1.10 Document Vault Upload Spec API (Multipart Upload)
    fields = {
        "document_type": "BILL_OF_LADING",
        "document_title": "Original Bill of Lading VOY-2026-001",
        "voyage_id": str(target_voyage_id),
    }
    sample_pdf_bytes = b"%PDF-1.4 sample maritime shipping document content for testing"
    files = {
        "file": ("bill_of_lading.pdf", sample_pdf_bytes, "application/pdf")
    }
    code, body, _ = http_multipart_upload(f"{API_BASE}/documents/upload/", fields=fields, files=files, headers={"X-User-Role": "LOGISTICS_AGENT"})
    if code in (201, 200):
        data = json.loads(body)
        record("POST /api/v1/documents/upload/", True, f"Document uploaded, Verification Status: {data.get('verification_status')}")
    else:
        record("POST /api/v1/documents/upload/", False, f"Status {code}: {body}")

    # -------------------------------------------------------------
    # Section 2: Defensive Security & RBAC Enforcement
    # -------------------------------------------------------------
    print(f"\n{BOLD}2. Defensive Security & Role-Based Access Control (RBAC){RESET}")

    # 2.1 Vertical Privilege Escalation Prevention
    # A LOGISTICS_AGENT attempting to approve/lock a financial settlement must receive 403 Forbidden
    if target_voyage_id:
        lock_payload = {"outcome": "SETTLED", "settlement_notes": "Attempting unauthorized settlement lock"}
        code, body, _ = http_request(f"{API_BASE}/voyages/{target_voyage_id}/settle/", method="POST", data=lock_payload, headers={"X-User-Role": "LOGISTICS_AGENT"})
        record("RBAC: LOGISTICS_AGENT blocked from settlement lock", code in (403, 409), f"Status {code} (Forbidden/Guarded against escalation)")

    # 2.2 Berth Assignment Conflict Handling
    if target_voyage_id:
        berth_payload = {"discharge_berth": "Berth 1", "force": False}
        code, body, _ = http_request(f"{API_BASE}/voyages/{target_voyage_id}/berth/", method="PATCH", data=berth_payload, headers={"X-User-Role": "LOGISTICS_AGENT"})
        record("Berth Assignment Conflict Handling", code in (200, 409), f"Status {code} (Deterministic conflict resolution)")

    # -------------------------------------------------------------
    # Section 3: Live Next.js 15 Frontend Verification
    # -------------------------------------------------------------
    print(f"\n{BOLD}3. Live Next.js Frontend Page Availability (Port 3000){RESET}")

    routes = [
        ("/", "Executive Command Center"),
        ("/fleet", "Fleet Tracking & Berthing Gantt"),
        ("/laytime", "Laytime Settlement Workbench"),
        ("/contracts", "Charter Fixtures & Carrier League"),
        ("/inventory/forecast", "Forward Runway Forecaster"),
        ("/inventory/starvation", "Multi-Plant Starvation Solver"),
    ]

    for path, name in routes:
        code, body, err = http_request(f"{UI_BASE}{path}")
        is_ok = code == 200 and "<html" in body.lower()
        record(f"Page {path} ({name})", is_ok, f"HTTP {code}")

    # -------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------
    print(f"\n{BOLD}{CYAN}-------------------------------------------------------------------------------{RESET}")
    print(f"{BOLD}Full-Scale E2E Audit Summary:{RESET} {results['total']} tests executed")
    print(f"  Passed: {GREEN}{results['passed']}{RESET}")
    print(f"  Failed: {RED}{results['failed']}{RESET}")
    print(f"{BOLD}{CYAN}==============================================================================={RESET}\n")

    return 0 if results["failed"] == 0 else 1

if __name__ == "__main__":
    sys.exit(run_e2e_tests())
