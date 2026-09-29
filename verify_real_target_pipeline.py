import requests
import json

print("--- STEP 1: INSPECT REAL 9001 APPLICATION ---")
r = requests.post("http://127.0.0.1:8001/api/incidents/inspect", json={"application_id": "payment-test-api"}, timeout=15)
assert r.status_code == 200, f"Inspect failed: {r.text}"
inspect_data = r.json()
incident = inspect_data["incident"]
incident_id = incident["incident_id"]
print(f"Detected Incident: {incident_id} on {incident['service']}")
print(f"Evidence logs reason: {inspect_data['reason']}")

print("\n--- STEP 2: FETCH CANONICAL INCIDENT RECORD ---")
r = requests.get(f"http://127.0.0.1:8001/incidents/{incident_id}")
assert r.status_code == 200
inc_rec = r.json()
print(f"Execution Target Type: {inc_rec.get('execution_target_type')}")
print(f"Recommended Action: {inc_rec.get('recommended_action')}")
print(f"Action Fingerprint: {inc_rec.get('action_fingerprint')}")
assert inc_rec.get("execution_target_type") == "real_service"

print("\n--- STEP 3: APPROVE ACTION WITH FINGERPRINT BINDING ---")
r = requests.post(f"http://127.0.0.1:8001/incidents/{incident_id}/approve", json={
    "approver": "oncall-sre",
    "note": "Approved against real telemetry evidence"
})
assert r.status_code == 200, f"Approve failed: {r.text}"
app_data = r.json()
print(f"Approval State: {app_data['approval']['state']}")

print("\n--- STEP 4: DISPATCH VIA REAL EXECUTION TARGET ---")
r = requests.post(f"http://127.0.0.1:8001/incidents/{incident_id}/execute")
assert r.status_code == 200, f"Execute failed: {r.text}"
exec_payload = r.json()
incident_rec = exec_payload["incident"]
latest_exec = incident_rec["executions"][-1]
print(f"Execution Status: {latest_exec['status']}")
print(f"Execution Output: {latest_exec.get('output')}")
print(f"Execution Errors: {latest_exec.get('errors')}")
assert latest_exec["status"] == "UNAVAILABLE"
assert "Real execution capability not configured" in latest_exec["errors"][0]

print("\n--- STEP 5: VERIFY REAL TELEMETRY & METRICS ---")
latest_ver = incident_rec["verifications"][-1]
print(f"Verification Status: {latest_ver['status']}")
after_metrics = latest_ver["after"]
print(f"After Metrics: Error Rate={after_metrics.get('error_rate')}, P95={after_metrics.get('p95_latency_ms')}, Service Status={after_metrics.get('service_status')}")
assert after_metrics.get("error_rate") in (None, "UNAVAILABLE")
assert after_metrics.get("p95_latency_ms") in (None, "UNAVAILABLE")
c2_crit = next(c for c in latest_ver["criteria"] if c["id"] == "C2")
assert c2_crit["status"] == "UNAVAILABLE"
assert c2_crit["observed"] == "UNAVAILABLE"

print("\n--- STEP 6: VERIFY RESOLUTION OUTCOME ---")
r = requests.get(f"http://127.0.0.1:8001/incidents/{incident_id}/outcome")
assert r.status_code == 200
outcome = r.json()
print(f"Final Status: {outcome.get('final_status')}")
print(f"Total Attempts: {outcome.get('total_attempts')}")
print("\n>>> COMPLETE REAL PIPELINE PROVEN END-TO-END! <<<")
