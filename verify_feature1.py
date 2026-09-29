"""
End-to-End Verification Script for Feature 1: Incident Ingestion.
Spins up a real live test microservice on port 8890 with live /health (200 OK) and /unhealthy (503) routes.
Then tests:
1. Application Registry discovery (/api/applications)
2. Real Healthy Application inspection (HTTP 200 via network probe)
3. Real Unhealthy Application inspection (HTTP 503 via network probe + log file parsing)
4. Real Unreachable Application inspection (Port 9999 connection failure)
5. 404 for un-registered application
"""

import time
import threading
import uvicorn
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from main import app as agent_api
from models import ApplicationConfig
from app_registry import register_application

# 1. Create a live mock target microservice
target_service_app = FastAPI(title="Target Microservice")

@target_service_app.get("/health")
def healthy_health():
    return {"status": "healthy", "service": "order-service", "version": "1.2.0"}

@target_service_app.get("/unhealthy")
def unhealthy_health(response: Response):
    response.status_code = 503
    return {"status": "unhealthy", "error": "Database connection pool exhausted", "pool_size": 0}


def run_target_server():
    config = uvicorn.Config(target_service_app, host="127.0.0.1", port=8890, log_level="error")
    server = uvicorn.Server(config)
    server.run()


def main():
    print("=" * 80)
    print(" STARTING REAL LIVE MICROSERVICE ON http://127.0.0.1:8890 ")
    print("=" * 80)

    server_thread = threading.Thread(target=run_target_server, daemon=True)
    server_thread.start()
    time.sleep(1.0)  # Allow live server to bind socket

    client = TestClient(agent_api)

    # Register live test apps pointing to the running mock service
    live_healthy_app = ApplicationConfig(
        application_id="live-order-service",
        name="Order Fulfillment Service",
        service="order-service",
        base_url="http://127.0.0.1:8890",
        health_endpoint="/health",
        log_source="sample_logs/healthy-service.log",
        description="Live order service returning HTTP 200"
    )
    register_application(live_healthy_app)

    live_unhealthy_app = ApplicationConfig(
        application_id="live-payment-api",
        name="Payment Gateway API",
        service="payment-gateway",
        base_url="http://127.0.0.1:8890",
        health_endpoint="/unhealthy",
        log_source="sample_logs/payment-api.log",
        description="Live payment service returning HTTP 503 and error logs"
    )
    register_application(live_unhealthy_app)

    # -------------------------------------------------------------------------
    # STEP 1: Application Registry Discovery
    # -------------------------------------------------------------------------
    print("\n[Step 1] Querying GET /api/applications ...")
    resp = client.get("/api/applications")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    apps = resp.json()
    print(f"-> Successfully retrieved {len(apps)} registered applications:")
    for a in apps:
        print(f"   * [{a['application_id']}] {a['name']} ({a['service']}) -> {a['base_url']}{a['health_endpoint']}")

    # -------------------------------------------------------------------------
    # STEP 2: Live Healthy Application Inspection
    # -------------------------------------------------------------------------
    print("\n[Step 2] Probing Live Healthy Application ('live-order-service') ...")
    resp_healthy = client.post("/api/incidents/inspect", json={"application_id": "live-order-service"})
    assert resp_healthy.status_code == 200
    data_h = resp_healthy.json()
    print(f"-> Status:            {data_h['status']}")
    print(f"-> Incident Detected: {data_h['incident_detected']}")
    print(f"-> Reason:            {data_h['reason']}")
    print(f"-> Health Probe:      {data_h['evidence']['health_check']['status']} (HTTP {data_h['evidence']['health_check']['http_status']}, Latency: {data_h['evidence']['health_check']['response_time_ms']}ms)")
    print(f"-> Logs:              Available={data_h['evidence']['logs']['available']}, Errors={data_h['evidence']['logs']['error_count']}, Warnings={data_h['evidence']['logs']['warn_count']}")
    assert data_h["incident_detected"] is False, "Healthy service must NOT trigger an incident"
    assert data_h["incident"] is None, "Incident object must be None for healthy service"
    assert data_h["evidence"]["health_check"]["status"] == "PASS"
    assert data_h["evidence"]["health_check"]["http_status"] == 200

    # -------------------------------------------------------------------------
    # STEP 3: Live Unhealthy Application Inspection (HTTP 503 + Log Errors)
    # -------------------------------------------------------------------------
    print("\n[Step 3] Probing Live Unhealthy Application ('live-payment-api') ...")
    resp_unhealthy = client.post("/api/incidents/inspect", json={"application_id": "live-payment-api"})
    assert resp_unhealthy.status_code == 200
    data_u = resp_unhealthy.json()
    print(f"-> Status:            {data_u['status']}")
    print(f"-> Incident Detected: {data_u['incident_detected']}")
    print(f"-> Reason:            {data_u['reason']}")
    print(f"-> Health Probe:      {data_u['evidence']['health_check']['status']} (HTTP {data_u['evidence']['health_check']['http_status']}, Latency: {data_u['evidence']['health_check']['response_time_ms']}ms)")
    print(f"-> Incident ID:       {data_u['incident']['incident_id']}")
    print(f"-> Service:           {data_u['incident']['service']}")
    print(f"-> Severity:          {data_u['incident']['severity']}")
    print(f"-> Description:       {data_u['incident']['description']}")
    print(f"-> Symptoms ({len(data_u['incident']['symptoms'])}):")
    for s in data_u['incident']['symptoms']:
        print(f"     - {s}")
    print(f"-> Captured Errors ({len(data_u['incident']['errors'])}):")
    for e in data_u['incident']['errors']:
        print(f"     - {e}")
    assert data_u["incident_detected"] is True, "Unhealthy service MUST trigger an incident"
    assert data_u["incident"] is not None, "Incident object must be populated"
    assert data_u["incident"]["incident_id"].startswith("INC-"), "Incident ID must be dynamic"
    assert data_u["incident"]["severity"] == "high", "HTTP 503 should be high severity"
    assert data_u["evidence"]["health_check"]["http_status"] == 503

    # -------------------------------------------------------------------------
    # STEP 4: Unreachable Application Inspection
    # -------------------------------------------------------------------------
    print("\n[Step 4] Probing Unreachable Application ('unreachable-service' -> port 9999) ...")
    resp_down = client.post("/api/incidents/inspect", json={"application_id": "unreachable-service"})
    assert resp_down.status_code == 200
    data_d = resp_down.json()
    print(f"-> Status:            {data_d['status']}")
    print(f"-> Incident Detected: {data_d['incident_detected']}")
    print(f"-> Reason:            {data_d['reason']}")
    print(f"-> Health Probe:      {data_d['evidence']['health_check']['status']}")
    print(f"-> Incident Severity: {data_d['incident']['severity']}")
    assert data_d["incident_detected"] is True
    assert data_d["evidence"]["health_check"]["status"] == "UNREACHABLE"
    assert data_d["incident"]["severity"] == "critical"

    # -------------------------------------------------------------------------
    # STEP 5: Non-registered Application Handling
    # -------------------------------------------------------------------------
    print("\n[Step 5] Probing Non-registered Application ('unknown-xyz') ...")
    resp_404 = client.post("/api/incidents/inspect", json={"application_id": "unknown-xyz"})
    assert resp_404.status_code == 404
    print(f"-> Safely returned 404: {resp_404.json()['detail']}")

    print("\n" + "=" * 80)
    print(" ALL 5 END-TO-END RUNTIME VERIFICATION STEPS PASSED PERFECTLY! ")
    print("=" * 80)


if __name__ == "__main__":
    main()
