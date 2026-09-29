import requests
import json
import time
import sys

# Ensure UTF-8 output encoding for Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TARGET_URL = "http://127.0.0.1:9001"
BACKEND_URL = "http://127.0.0.1:8001"
for cand in ["http://127.0.0.1:8001"]:
    try:
        if "Incident Response Memory Agent" in requests.get(f"{cand}/health", timeout=0.8).text:
            BACKEND_URL = cand
            break
    except Exception:
        pass

print("==================================================")
print("LIVE INCIDENT INVESTIGATION & RCA VERIFICATION")
print("==================================================")

# 1. Clear log file to test a clean fresh incident
log_path = "sample_logs/payment-test-api.log"
with open(log_path, "w", encoding="utf-8") as f:
    f.write("")
print(f"[Step 1] Cleared {log_path} for fresh live test.")

# 2. Generate a real runtime failure on Target Application
print("\n[Step 2] Triggering REAL runtime failure on http://127.0.0.1:9001/payments...")
print("Executing payment with card 9999-0000-0000-8812 (triggers real 1.0s upstream timeout)...")

payload = {
    "account_id": "acc_merch_001",
    "amount": 420.00,
    "currency": "USD",
    "card_number": "9999-0000-0000-8812",
    "cvv": "999"
}

t0 = time.time()
r_pay = requests.post(f"{TARGET_URL}/payments", json=payload, timeout=5)
elapsed = time.time() - t0
print(f"Target Response: HTTP {r_pay.status_code} in {elapsed:.2f}s")
print("Response body:", r_pay.json())
assert r_pay.status_code == 504, f"Expected 504, got {r_pay.status_code}"

# 3. Verify real log was written by Target Application
print("\n[Step 3] Verifying runtime logs in sample_logs/payment-test-api.log...")
with open(log_path, "r", encoding="utf-8") as f:
    log_content = f.read()

print("Log content on disk:")
for line in log_content.strip().split("\n"):
    print("  ", line)

assert "Dependency failure" in log_content, "Expected dependency failure log"
assert "timed out after 1.0s" in log_content, "Expected timeout log"

# 4. Invoke Incident Response Agent inspection endpoint
print("\n[Step 4] Calling Incident Agent Inspection: POST http://127.0.0.1:8001/api/incidents/inspect...")
r_inspect = requests.post(
    f"{BACKEND_URL}/api/incidents/inspect",
    json={"application_id": "payment-test-api"},
    timeout=20
)
print(f"Agent Response: HTTP {r_inspect.status_code}")
data = r_inspect.json()

# 5. Verify Investigation and RCA results
print("\n[Step 5] Checking Incident Investigation Results:")
print("Incident Detected:", data.get("incident_detected"))
print("Incident ID:", data.get("incident", {}).get("incident_id"))

analysis = data.get("analysis")
assert analysis is not None, "Expected analysis in response"
print("\n--- ROOT CAUSE ANALYSIS ---")
print("Incident Type:", analysis.get("incident_type"))
print("Severity:", analysis.get("severity"))
print("Failure:", analysis.get("failure"))
print("Root Cause:", analysis.get("root_cause"))
print("Why:", analysis.get("why"))
print("Impact:", analysis.get("impact"))
print("Confidence:", analysis.get("confidence"))

timeline = data.get("timeline")
print("\n--- INCIDENT TIMELINE ---")
for t in timeline:
    print(f"  {t.get('timestamp')} [{t.get('level')}] (req_id={t.get('request_id')}): {t.get('event')}")

resolution = data.get("resolution")
assert resolution is not None, "Expected resolution in response"
print("\n--- RECOMMENDED RESOLUTION ---")
print("Strategy:", resolution.get("summary"))
for i, act in enumerate(resolution.get("actions", []), 1):
    print(f"  {i}. {act}")

hindsight = data.get("hindsight")
print("\n--- HINDSIGHT MEMORY ---")
past_incs = hindsight.get("similar_incidents", []) if hindsight else []
print(f"Recalled {len(past_incs)} similar past incidents.")
for p in past_incs[:2]:
    print(f"  - {p.get('text', '')[:120]}...")

print("\nSUCCESS: Complete investigation and RCA pipeline verified with real live incident!")
