"""
Live End-to-End Verification Script for Application Registration, Persistence & Live Inspection
against the running Payment Gateway Test API on http://127.0.0.1:9001.
"""

import time
import requests
from pathlib import Path
from fastapi.testclient import TestClient

from main import app as agent_api

WORKSPACE_ROOT = Path(__file__).resolve().parent
LOG_FILE = WORKSPACE_ROOT / "sample_logs" / "payment-test-api.log"


def main():
    print("=" * 85)
    print(" VERIFYING INCIDENT AGENT REGISTRATION ROUTING & END-TO-END FLOW ")
    print("=" * 85)

    client = TestClient(agent_api)

    # -------------------------------------------------------------------------
    # STEP 1 & 2: Register Application Configuration
    # -------------------------------------------------------------------------
    print("\n[Step 1 & 2] Testing Application Registration (POST /api/applications) ...")
    payload = {
        "application_id": "payment-test-api",
        "application_name": "Payment Gateway Test API",
        "service_id": "payment-gateway-test",
        "base_url": "http://127.0.0.1:9001",
        "health_endpoint": "/health",
        "log_source": "sample_logs/payment-test-api.log",
        "description": "Local test payment API for incident inspection"
    }

    # Test POST /api/applications
    resp = client.post("/api/applications", json=payload)
    print(f"-> POST /api/applications Response Code: {resp.status_code}")
    print(f"-> Response Body: {resp.json()}")

    assert resp.status_code in [200, 201], f"Expected 201 Created, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data.get("success") is True, "Expected success: True"
    assert data["application"]["application_id"] == "payment-test-api"
    assert data["application"]["base_url"] == "http://127.0.0.1:9001"
    assert data["application"]["log_source"] == "sample_logs/payment-test-api.log"

    # Also test registration alias endpoint POST /api/applications/register
    resp_alias = client.post("/api/applications/register", json=payload)
    assert resp_alias.status_code in [200, 201]
    assert resp_alias.json().get("success") is True
    print("-> Both /api/applications and /api/applications/register return 201 SUCCESS (No 404)!")

    # -------------------------------------------------------------------------
    # STEP 3: Verify Persistence and Retrieval from Backend
    # -------------------------------------------------------------------------
    print("\n[Step 3] Verifying Backend Persistence & Listing (GET /api/applications) ...")
    list_resp = client.get("/api/applications")
    assert list_resp.status_code == 200
    apps = list_resp.json()
    matching_apps = [a for a in apps if a["application_id"] == "payment-test-api"]
    assert len(matching_apps) == 1, "Expected exactly 1 registered payment-test-api entry"
    saved_app = matching_apps[0]
    print(f"-> Loaded '{saved_app['application_name']}' from backend registry.")
    print(f"   Base URL:     {saved_app['base_url']}")
    print(f"   Health Probe: {saved_app['health_endpoint']}")
    print(f"   Log Source:   {saved_app['log_source']}")

    # -------------------------------------------------------------------------
    # STEP 4: Live Inspection - Clean Healthy State (Port 9001)
    # -------------------------------------------------------------------------
    print("\n[Step 4] Ensuring Healthy State on Port 9001 & Inspecting ...")
    
    # Initialize clean log file for fresh demonstration
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("")

    # Recover service on port 9001
    rec_call = requests.post("http://127.0.0.1:9001/simulate/recover", timeout=3)
    assert rec_call.status_code == 200
    time.sleep(0.2)

    inspect_resp = client.post("/api/incidents/inspect", json={"application_id": "payment-test-api"})
    assert inspect_resp.status_code == 200
    ins_data = inspect_resp.json()

    print(f"-> Health Check:      {ins_data['evidence']['health_check']['status']} (HTTP {ins_data['evidence']['health_check']['http_status']}, Latency: {ins_data['evidence']['health_check']['response_time_ms']}ms)")
    print(f"-> Incident Detected: {ins_data['incident_detected']}")
    print(f"-> Detection Reason:  {ins_data['reason']}")
    print(f"-> Log File Read:     {ins_data['evidence']['logs']['source']} (Errors: {ins_data['evidence']['logs']['error_count']})")
    assert ins_data["incident_detected"] is False
    assert ins_data["incident"] is None
    assert ins_data["evidence"]["health_check"]["status"] == "PASS"
    print("-> HEALTHY STATE INSPECTION PASSED: No incident detected.")

    # -------------------------------------------------------------------------
    # STEP 5: Live Inspection - Failure State (Port 9001 returns 503)
    # -------------------------------------------------------------------------
    print("\n[Step 5] Triggering Outage on Port 9001 via POST /simulate/failure ...")
    fail_call = requests.post(
        "http://127.0.0.1:9001/simulate/failure",
        json={"reason": "Stripe webhook worker connection pool saturated (50/50 threads)."},
        timeout=3
    )
    assert fail_call.status_code == 200
    time.sleep(0.2)

    inspect_fail = client.post("/api/incidents/inspect", json={"application_id": "payment-test-api"})
    assert inspect_fail.status_code == 200
    fail_data = inspect_fail.json()

    print(f"-> Health Check:      {fail_data['evidence']['health_check']['status']} (HTTP {fail_data['evidence']['health_check']['http_status']}, Latency: {fail_data['evidence']['health_check']['response_time_ms']}ms)")
    print(f"-> Incident Detected: {fail_data['incident_detected']}")
    print(f"-> Incident ID:       {fail_data['incident']['incident_id']}")
    print(f"-> Severity:          {fail_data['incident']['severity']}")
    print(f"-> Description:       {fail_data['incident']['description']}")
    print(f"-> Symptoms ({len(fail_data['incident']['symptoms'])}):")
    for s in fail_data['incident']['symptoms']:
        print(f"     - {s}")
    print(f"-> Captured Errors ({len(fail_data['incident']['errors'])}):")
    for e in fail_data['incident']['errors']:
        print(f"     - {e}")
    assert fail_data["incident_detected"] is True
    assert fail_data["incident"] is not None
    assert fail_data["incident"]["severity"] == "high"
    assert fail_data["evidence"]["health_check"]["http_status"] == 503
    print("-> FAILURE STATE INSPECTION PASSED: High-severity incident generated.")

    # -------------------------------------------------------------------------
    # STEP 6: Live Inspection - Recovery State (Port 9001 restored to 200)
    # -------------------------------------------------------------------------
    print("\n[Step 6] Restoring Service on Port 9001 via POST /simulate/recover ...")
    rec_call = requests.post("http://127.0.0.1:9001/simulate/recover", timeout=3)
    assert rec_call.status_code == 200
    time.sleep(0.2)

    # Re-check live health directly
    h_check = requests.get("http://127.0.0.1:9001/health", timeout=3)
    assert h_check.status_code == 200
    print(f"-> Live Service Direct Probe: HTTP {h_check.status_code} ({h_check.json()['status']})")

    inspect_rec = client.post("/api/incidents/inspect", json={"application_id": "payment-test-api"})
    assert inspect_rec.status_code == 200
    rec_data = inspect_rec.json()

    print(f"-> Health Check:      {rec_data['evidence']['health_check']['status']} (HTTP {rec_data['evidence']['health_check']['http_status']})")
    assert rec_data["evidence"]["health_check"]["status"] == "PASS"
    print("-> RECOVERY STATE INSPECTION PASSED: Health probe back to PASS.")

    print("\n" + "=" * 85)
    print(" ALL REGISTRATION, PERSISTENCE & LIVE INSPECTION CHECKS PASSED! ")
    print("=" * 85)


if __name__ == "__main__":
    main()
