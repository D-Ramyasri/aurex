"""
Live Multi-Port End-to-End Verification Script for Feature 1.
Spins up a live test target server on port 9002 and executes real TCP network probes.
Verifies all requirements:
- Test 1: Application registration
- Test 2: Different application configuration (Port 9002 vs Port 8001 isolation)
- Test 3: Target application running (HTTP 200 -> Healthy)
- Test 4: Target application not running (Connection Refused / UNREACHABLE != HTTP 404)
- Test 5: Target application returns HTTP 404 (Reachable but 404)
- Test 6: Target application returns HTTP 503 (Incident Created with high severity)
- Test 7: Per-application log source isolation
- Test 8: Backend route consistency
"""

import time
import threading
import uvicorn
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from main import app as agent_api
from models import ApplicationConfig
from app_registry import register_application, get_application, list_applications

# State container for dynamic target server behavior
class MockServerState:
    status_code: int = 200
    response_body: dict = {"status": "healthy", "service": "order-service", "version": "2.4.0"}

server_state = MockServerState()

# Live Target Microservice on Port 9002
target_app = FastAPI(title="Target Order Microservice")

@target_app.get("/health")
def dynamic_health_endpoint(response: Response):
    response.status_code = server_state.status_code
    if server_state.status_code == 200:
        return {"status": "healthy", "service": "order-service", "active_connections": 14}
    elif server_state.status_code == 404:
        return {"detail": "Endpoint not found"}
    elif server_state.status_code == 503:
        return {"status": "unhealthy", "error": "Database connection pool exhausted", "active_workers": 0}
    else:
        return {"status": "error", "code": server_state.status_code}


def run_target_server():
    config = uvicorn.Config(target_app, host="127.0.0.1", port=9002, log_level="error")
    server = uvicorn.Server(config)
    server.run()


def main():
    print("=" * 85)
    print(" FEATURE 1: DYNAMIC APPLICATION REGISTRATION & INGESTION VERIFICATION ")
    print("=" * 85)

    # Start live server on port 9002 in background thread
    server_thread = threading.Thread(target=run_target_server, daemon=True)
    server_thread.start()
    time.sleep(1.0)  # Wait for socket bind

    client = TestClient(agent_api)

    # -------------------------------------------------------------------------
    # TEST 1: Application Registration
    # -------------------------------------------------------------------------
    print("\n[Test 1] Registering Monitored Application via POST /api/applications ...")
    order_app_payload = {
        "application_id": "order-api",
        "application_name": "Order Fulfillment API",
        "service_id": "order-service",
        "base_url": "http://127.0.0.1:9002",
        "health_endpoint": "/health",
        "log_source": "sample_logs/order-api.log",
        "description": "Order state and payment dispatch service"
    }
    reg_resp = client.post("/api/applications", json=order_app_payload)
    assert reg_resp.status_code in [200, 201], f"Registration failed: {reg_resp.text}"
    print(f"-> Application Registered: '{reg_resp.json()['application_name']}' ({reg_resp.json()['application_id']})")
    print(f"   Base URL: {reg_resp.json()['base_url']}")

    # -------------------------------------------------------------------------
    # TEST 2 & 3: Port Isolation & Live Running Healthy Inspection (Port 9002)
    # -------------------------------------------------------------------------
    print("\n[Test 2 & 3] Inspecting Order API on Port 9002 (Live Server Running HTTP 200) ...")
    server_state.status_code = 200
    inspect_order = client.post("/api/incidents/inspect", json={"application_id": "order-api"})
    assert inspect_order.status_code == 200
    data_order = inspect_order.json()

    print(f"-> Target Endpoint Probed: {data_order['evidence']['health_check']['endpoint']}")
    print(f"-> Health Check Status:    {data_order['evidence']['health_check']['status']} (HTTP {data_order['evidence']['health_check']['http_status']}, Latency: {data_order['evidence']['health_check']['response_time_ms']}ms)")
    print(f"-> Incident Detected:      {data_order['incident_detected']}")
    print(f"-> Detection Reason:       {data_order['reason']}")
    print(f"-> Log Source Inspected:   {data_order['evidence']['logs']['source']} (Errors: {data_order['evidence']['logs']['error_count']})")
    
    assert data_order["evidence"]["health_check"]["endpoint"] == "http://127.0.0.1:9002/health", "Must call port 9002, NOT 8001"
    assert data_order["evidence"]["health_check"]["status"] == "PASS"
    assert data_order["evidence"]["health_check"]["http_status"] == 200
    assert data_order["incident_detected"] is False
    assert data_order["incident"] is None

    # -------------------------------------------------------------------------
    # TEST 4: Target Application Not Running (Port 9999 Offline) -> UNREACHABLE
    # -------------------------------------------------------------------------
    print("\n[Test 4] Inspecting Offline Application on Port 9999 ('unreachable-service') ...")
    inspect_offline = client.post("/api/incidents/inspect", json={"application_id": "unreachable-service"})
    assert inspect_offline.status_code == 200
    data_offline = inspect_offline.json()

    print(f"-> Target Endpoint Probed: {data_offline['evidence']['health_check']['endpoint']}")
    print(f"-> Health Check Status:    {data_offline['evidence']['health_check']['status']}")
    print(f"-> HTTP Status Code:       {data_offline['evidence']['health_check']['http_status']} (None because connection failed)")
    print(f"-> Connection Error:       {data_offline['evidence']['health_check']['error_message']}")
    print(f"-> Incident Detected:      {data_offline['incident_detected']}")
    print(f"-> Incident Severity:      {data_offline['incident']['severity']}")
    
    assert data_offline["evidence"]["health_check"]["status"] == "UNREACHABLE", "Must be UNREACHABLE"
    assert data_offline["evidence"]["health_check"]["http_status"] is None, "Must NOT be HTTP 404"
    assert data_offline["incident_detected"] is True
    assert data_offline["incident"]["severity"] == "critical"

    # -------------------------------------------------------------------------
    # TEST 5: Target Application Returns HTTP 404 (Reachable but Route Missing)
    # -------------------------------------------------------------------------
    print("\n[Test 5] Simulating Target Application Returning HTTP 404 on Port 9002 ...")
    server_state.status_code = 404
    inspect_404 = client.post("/api/incidents/inspect", json={"application_id": "order-api"})
    assert inspect_404.status_code == 200
    data_404 = inspect_404.json()

    print(f"-> Health Check Status:    {data_404['evidence']['health_check']['status']}")
    print(f"-> HTTP Status Code:       {data_404['evidence']['health_check']['http_status']}")
    print(f"-> Incident Detected:      {data_404['incident_detected']}")
    print(f"-> Detection Reason:       {data_404['reason']}")
    print(f"-> Incident Severity:      {data_404['incident']['severity']}")

    assert data_404["evidence"]["health_check"]["status"] == "FAIL"
    assert data_404["evidence"]["health_check"]["http_status"] == 404, "Must capture HTTP 404"
    assert data_404["incident_detected"] is True
    assert "404" in data_404["reason"]

    # -------------------------------------------------------------------------
    # TEST 6: Target Application Returns HTTP 503 (Incident Created)
    # -------------------------------------------------------------------------
    print("\n[Test 6] Simulating Target Application Returning HTTP 503 on Port 9002 ...")
    server_state.status_code = 503
    inspect_503 = client.post("/api/incidents/inspect", json={"application_id": "order-api"})
    assert inspect_503.status_code == 200
    data_503 = inspect_503.json()

    print(f"-> Health Check Status:    {data_503['evidence']['health_check']['status']}")
    print(f"-> HTTP Status Code:       {data_503['evidence']['health_check']['http_status']}")
    print(f"-> Incident ID:            {data_503['incident']['incident_id']}")
    print(f"-> Incident Severity:      {data_503['incident']['severity']}")
    print(f"-> Description:            {data_503['incident']['description']}")
    print(f"-> Symptoms ({len(data_503['incident']['symptoms'])}):")
    for s in data_503['incident']['symptoms']:
        print(f"     - {s}")
    print(f"-> Errors ({len(data_503['incident']['errors'])}):")
    for e in data_503['incident']['errors']:
        print(f"     - {e}")

    assert data_503["evidence"]["health_check"]["http_status"] == 503
    assert data_503["incident_detected"] is True
    assert data_503["incident"]["severity"] == "high"
    assert data_503["incident"]["incident_id"].startswith("INC-")

    # -------------------------------------------------------------------------
    # TEST 7: Per-Application Log Source Isolation
    # -------------------------------------------------------------------------
    print("\n[Test 7] Verifying Per-Application Log Source Isolation ...")
    app_payment = get_application("payment-api")
    app_order = get_application("order-api")
    assert app_payment.log_source == "sample_logs/payment-api.log"
    assert app_order.log_source == "sample_logs/order-api.log"
    print(f"-> Payment API log source: {app_payment.log_source}")
    print(f"-> Order API log source:   {app_order.log_source}")

    # -------------------------------------------------------------------------
    # TEST 8: Backend Route Validation
    # -------------------------------------------------------------------------
    print("\n[Test 8] Verifying Backend Route Contracts ...")
    resp_apps = client.get("/api/applications")
    assert resp_apps.status_code == 200
    assert len(resp_apps.json()) >= 2

    resp_single = client.get("/api/applications/order-api")
    assert resp_single.status_code == 200
    assert resp_single.json()["application_id"] == "order-api"

    resp_missing = client.get("/api/applications/non-existent-app-999")
    assert resp_missing.status_code == 404

    print("-> All API routes (/api/applications, /api/applications/{id}, /api/incidents/inspect) verified!")

    print("\n" + "=" * 85)
    print(" ALL 8 ARCHITECTURAL REQUIREMENTS & TESTS VERIFIED SUCCESSFULLY! ")
    print("=" * 85)


if __name__ == "__main__":
    main()
