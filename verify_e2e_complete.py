import requests
import json
from pathlib import Path

BACKEND_URL = "http://127.0.0.1:8001"
TEST_APP_URL = "http://127.0.0.1:9001"

print("==================================================")
print("VERIFICATION SUITE: INCIDENT AGENT APPLICATION REGISTRATION")
print("==================================================")

# Step 1: Check Payment Gateway Test API (Server 2)
print("\n[Step 1] Checking External Payment Gateway Test API on port 9001...")
try:
    resp = requests.get(TEST_APP_URL, timeout=3)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    print("SUCCESS: Payment Gateway Test API is running.")
    print("Response:", json.dumps(resp.json(), indent=2))
except Exception as e:
    print(f"FAILED: Could not reach Payment Gateway Test API: {e}")
    exit(1)

# Step 2: Check Incident Agent Backend (Server 1)
print("\n[Step 2] Checking Incident Agent Backend on port 8001...")
try:
    resp = requests.get(f"{BACKEND_URL}/health", timeout=3)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    print("SUCCESS: Incident Agent Backend is running.")
    print("Response:", json.dumps(resp.json(), indent=2))
except Exception as e:
    print(f"FAILED: Could not reach Incident Agent Backend: {e}")
    exit(1)

# Step 3: Check OpenAPI routes in Running Backend
print("\n[Step 3] Checking Registered Routes in Running Backend OpenAPI...")
try:
    resp = requests.get(f"{BACKEND_URL}/openapi.json", timeout=3)
    openapi = resp.json()
    paths = openapi.get("paths", {})
    print("OpenAPI Title:", openapi.get("info", {}).get("title"))
    print("Relevant Routes in Running Backend:")
    for p in ["/api/applications", "/api/applications/register", "/api/incidents/inspect"]:
        if p in paths:
            print(f"  FOUND: {list(paths[p].keys())} {p}")
        else:
            print(f"  MISSING: {p}")
    assert "/api/applications" in paths
    assert "/api/applications/register" in paths
    assert "/api/incidents/inspect" in paths
    print("SUCCESS: All registration and inspection routes exist in running backend!")
except Exception as e:
    print(f"FAILED: OpenAPI check failed: {e}")
    exit(1)

# Step 4: Perform Application Registration
print("\n[Step 4] Registering 'Payment Gateway Test API'...")
payload = {
    "application_id": "payment-test-api",
    "application_name": "Payment Gateway Test API",
    "service_id": "payment-gateway-test",
    "base_url": "http://127.0.0.1:9001",
    "health_endpoint": "/health",
    "log_source": "sample_logs/payment-test-api.log",
    "description": "Local test payment API for incident inspection"
}

resp = requests.post(f"{BACKEND_URL}/api/applications", json=payload, timeout=5)
print(f"POST {BACKEND_URL}/api/applications -> HTTP {resp.status_code}")
print("Response:", json.dumps(resp.json(), indent=2))
assert resp.status_code in [200, 201], f"Expected 200/201, got {resp.status_code}"

# Also test /api/applications/register route
resp_reg = requests.post(f"{BACKEND_URL}/api/applications/register", json=payload, timeout=5)
print(f"POST {BACKEND_URL}/api/applications/register -> HTTP {resp_reg.status_code}")
assert resp_reg.status_code in [200, 201]

# Step 5: Check Persistence
print("\n[Step 5] Checking Backend Persistence...")
# 5a. Via GET /api/applications
resp_list = requests.get(f"{BACKEND_URL}/api/applications", timeout=3)
apps = resp_list.json()
match = [a for a in apps if a.get("application_id") == "payment-test-api"]
assert len(match) == 1, f"Expected 1 entry for payment-test-api, found {len(match)}"
print("Found registered application via GET /api/applications:")
print(json.dumps(match[0], indent=2))

# 5b. On disk in applications_store.json
store_path = Path("applications_store.json")
assert store_path.exists(), "applications_store.json does not exist on disk"
with open(store_path, "r", encoding="utf-8") as f:
    disk_store = json.load(f)
assert any(a.get("application_id") == "payment-test-api" for a in disk_store)
print("SUCCESS: Application configuration is safely persisted to disk in applications_store.json!")

# Step 6: Test Inspection Flow
print("\n[Step 6] Testing Full Inspection Flow...")
print(f"Sending inspection request for 'payment-test-api' to {BACKEND_URL}/api/incidents/inspect...")
inspect_resp = requests.post(f"{BACKEND_URL}/api/incidents/inspect", json={"application_id": "payment-test-api"}, timeout=10)
print(f"HTTP {inspect_resp.status_code}")
data = inspect_resp.json()
print("Inspection Results Summary:")
print(" - Status:", data.get("status"))
print(" - Incident Detected:", data.get("incident_detected"))
print(" - Reason:", data.get("reason"))
print(" - Health Probe:")
print(json.dumps(data.get("evidence", {}).get("health_check"), indent=2))

probe = data.get("evidence", {}).get("health_check", {})
assert probe.get("status") == "PASS", f"Expected PASS, got {probe.get('status')}"
assert probe.get("http_status") == 200, f"Expected 200, got {probe.get('http_status')}"
assert probe.get("endpoint") == "http://127.0.0.1:9001/health", f"Wrong endpoint: {probe.get('endpoint')}"
print("\nALL VERIFICATIONS PASSED SUCCESSFULLY!")
