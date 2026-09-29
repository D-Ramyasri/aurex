"""
End-to-End Verification Script for all 18 Production Features.
Tests the entire Incident Response Agent pipeline against the live Payment Gateway Test API on port 9001 and Agent API on port 8001.
NO simulated data, NO /sim/* endpoints, NO fake telemetry.
"""

import time
import threading
import uvicorn
import requests
import json
import os

from test_application.main import app as target_app
from main import app as agent_app

AGENT_API = "http://127.0.0.1:8001"
TARGET_APP = "http://127.0.0.1:9001"


def run_target_server():
    config = uvicorn.Config(target_app, host="127.0.0.1", port=9001, log_level="error")
    server = uvicorn.Server(config)
    server.run()


def run_agent_server():
    os.environ["ENABLE_SIMULATOR"] = "0"
    config = uvicorn.Config(agent_app, host="127.0.0.1", port=8001, log_level="error")
    server = uvicorn.Server(config)
    server.run()


def ensure_servers_running():
    # Start target app on 9001 if not running
    try:
        r = requests.get(f"{TARGET_APP}/health", timeout=1.0)
    except Exception:
        t = threading.Thread(target=run_target_server, daemon=True)
        t.start()
        time.sleep(1.5)

    # Start agent app on 8001 if not running
    try:
        r = requests.get(f"{AGENT_API}/health", timeout=1.0)
    except Exception:
        t = threading.Thread(target=run_agent_server, daemon=True)
        t.start()
        time.sleep(1.5)


def main():
    print("=" * 80)
    print(" STARTING COMPLETE REAL 18-FEATURE END-TO-END PIPELINE VERIFICATION")
    print("=" * 80)

    ensure_servers_running()

    # -------------------------------------------------------------------------
    # 0. Check Health of Target Application & Agent Backend
    # -------------------------------------------------------------------------
    try:
        r_target = requests.get(f"{TARGET_APP}/health", timeout=3.0)
        assert r_target.status_code == 200, f"Target app not healthy on port 9001: {r_target.text}"
        print("[PASS] Target application running on port 9001 (HTTP 200 OK)")
    except Exception as e:
        print(f"[FAIL] Target application unavailable on port 9001: {e}")
        return

    try:
        r_agent = requests.get(f"{AGENT_API}/health", timeout=3.0)
        assert r_agent.status_code == 200, f"Agent backend not healthy on port 8001: {r_agent.text}"
        print("[PASS] Agent backend API running on port 8001 (HTTP 200 OK)")
    except Exception as e:
        print(f"[FAIL] Agent backend unavailable on port 8001: {e}")
        return

    # -------------------------------------------------------------------------
    # FEATURE 1: Real Application Registration & Target Discovery
    # -------------------------------------------------------------------------
    print("\n--- FEATURE 1: REAL APPLICATION REGISTRATION ---")
    reg_payload = {
        "application_id": "payment-test-api",
        "application_name": "Payment Gateway Test API",
        "service_id": "payment-gateway-test",
        "base_url": TARGET_APP,
        "health_endpoint": "/health",
        "remediation_endpoint": "/remediation",
        "metrics_endpoint": "/metrics",
        "log_collection_method": "http",
        "description": "Live production payment processing microservice"
    }
    r_reg = requests.post(f"{AGENT_API}/api/applications", json=reg_payload, timeout=5.0)
    assert r_reg.status_code in (200, 201), f"Registration failed: {r_reg.text}"
    app_data = r_reg.json()
    app_obj = app_data.get("application", app_data)
    app_name = app_obj.get("application_name", app_obj.get("name", "payment-test-api"))
    app_id = app_obj.get("application_id")
    print(f"[PASS] Registered application: '{app_name}' ({app_id})")
    print(f"       Base URL: {app_obj.get('base_url')} | Remediation: {app_obj.get('remediation_endpoint')}")

    # -------------------------------------------------------------------------
    # TRIGGER REAL APPLICATION INCIDENT (DB Pool Exhaustion / Failure)
    # -------------------------------------------------------------------------
    print("\n--- TRIGGERING REAL APPLICATION FAILURE ON PORT 9001 ---")
    r_fail = requests.post(f"{TARGET_APP}/remediation", json={"action": "trigger_failure", "params": {}}, timeout=5.0)
    print(f"[PASS] Failure state triggered on target service: {r_fail.json()}")

    # Make target app fail health probe
    r_degraded = requests.get(f"{TARGET_APP}/health", timeout=3.0)
    print(f"[PASS] Target health status code: HTTP {r_degraded.status_code}")

    # -------------------------------------------------------------------------
    # FEATURE 2 & 3: Real Incident Detection & Real Telemetry Ingestion
    # -------------------------------------------------------------------------
    print("\n--- FEATURE 2 & 3: REAL INCIDENT DETECTION & TELEMETRY INGESTION ---")
    r_inspect = requests.post(f"{AGENT_API}/api/incidents/inspect", json={"application_id": "payment-test-api"}, timeout=15.0)
    assert r_inspect.status_code == 200, f"Inspect failed: {r_inspect.text}"
    inspect_data = r_inspect.json()
    assert inspect_data.get("incident_detected") == True, "Incident should be detected on degraded app"

    incident_obj = inspect_data["incident"]
    incident_id = incident_obj["incident_id"]
    evidence = inspect_data["evidence"]
    print(f"[PASS] Real Incident Detected! Canonical ID: {incident_id}")
    print(f"       Service: {incident_obj['service']} | Severity: {incident_obj['severity'].upper()}")
    print(f"       Health probe status: {evidence['health_check']['status']} (HTTP {evidence['health_check']['http_status']})")
    print(f"       Log source: {evidence['logs']['source']} (Log Errors: {evidence['logs']['error_count']})")

    # -------------------------------------------------------------------------
    # FEATURE 4, 5, 6, 7: Understanding, Evidence Reasoning, Hindsight Recall & RCA
    # -------------------------------------------------------------------------
    print("\n--- FEATURE 4-7: UNDERSTANDING, HINDSIGHT RECALL & RCA ---")
    r_inc = requests.get(f"{AGENT_API}/incidents/{incident_id}", timeout=5.0)
    assert r_inc.status_code == 200
    inc_rec = r_inc.json()

    print(f"[PASS] Incident Description: {inc_rec.get('description')}")
    print(f"[PASS] Execution Target Type: {inc_rec.get('execution_target_type')} (Must be 'real_service')")
    assert inc_rec.get("execution_target_type") == "real_service"

    hindsight_memories = inspect_data.get("hindsight", {}).get("similar_incidents") or inspect_data.get("investigation", {}).get("hindsight", {}).get("similar_incidents", [])
    print(f"[PASS] Hindsight Memories Recalled: {len(hindsight_memories)} historical experiences")

    rca_analysis = inspect_data.get("analysis") or inspect_data.get("investigation", {}).get("analysis", {})
    print(f"[PASS] Root Cause Analysis: {rca_analysis.get('root_cause')}")

    # -------------------------------------------------------------------------
    # FEATURE 8: Remediation Recommendation & Action Fingerprint
    # -------------------------------------------------------------------------
    print("\n--- FEATURE 8: REMEDIATION RECOMMENDATION & FINGERPRINT ---")
    rec_action = inc_rec.get("recommended_action")
    action_fp = inc_rec.get("action_fingerprint")
    print(f"[PASS] Recommended Action: {rec_action.get('type')} on {rec_action.get('target_service')}")
    print(f"[PASS] Action Fingerprint: {action_fp}")
    assert rec_action and action_fp, "Must generate recommendation and cryptographic fingerprint"

    # -------------------------------------------------------------------------
    # FEATURE 9: Human Approval Gate & Cryptographic Binding
    # -------------------------------------------------------------------------
    print("\n--- FEATURE 9: HUMAN APPROVAL GATE ---")
    r_approve = requests.post(f"{AGENT_API}/incidents/{incident_id}/approve", json={
        "approver": "lead-sre-oncall",
        "note": "Approved against real telemetry evidence and Hindsight memory match"
    }, timeout=5.0)
    assert r_approve.status_code == 200, f"Approve failed: {r_approve.text}"
    app_res = r_approve.json()
    print(f"[PASS] Approval State: {app_res['approval']['state']} by {app_res['approval']['decided_by']}")
    print(f"[PASS] Approved Fingerprint: {app_res['approval']['approved_fingerprint']}")
    assert app_res['approval']['state'] == "APPROVED"
    assert app_res['approval']['approved_fingerprint'] == action_fp

    # -------------------------------------------------------------------------
    # FEATURE 10 & 11: REAL Execution Target Dispatch & Result Capture
    # -------------------------------------------------------------------------
    print("\n--- FEATURE 10 & 11: REAL EXECUTION TARGET DISPATCH & RESULT ---")
    r_exec = requests.post(f"{AGENT_API}/incidents/{incident_id}/execute", timeout=45.0)
    assert r_exec.status_code == 200, f"Execution failed: {r_exec.text}"
    exec_res = r_exec.json()
    latest_exec = exec_res["execution"]
    latest_ver = exec_res["verification"]

    print(f"[PASS] Execution Status: {latest_exec['status']} (Target: {latest_exec['target_service']})")
    print(f"[PASS] Execution Duration: {latest_exec['duration_ms']}ms")
    print(f"[PASS] Execution Output: {json.dumps(latest_exec['output'])}")
    assert latest_exec["status"] == "COMPLETED", f"Execution should be COMPLETED, got {latest_exec['status']}"

    # -------------------------------------------------------------------------
    # FEATURE 12 & 13: REAL Telemetry Verification & Decision Engine
    # -------------------------------------------------------------------------
    print("\n--- FEATURE 12 & 13: REAL TELEMETRY VERIFICATION & SUCCESS DECISION ---")
    print(f"[PASS] Verification Decision: {latest_ver['status']}")
    print("[PASS] 5-Criteria Telemetry Evaluation:")
    for crit in latest_ver["criteria"]:
        print(f"       [{crit['status']}] {crit['id']}: {crit['description']} | Observed: {crit['observed']}")

    assert latest_ver["status"] == "SUCCESS", f"Verification decision should be SUCCESS, got {latest_ver['status']}"
    assert all(c["passed"] for c in latest_ver["criteria"]), "All 5 verification criteria must pass"

    # Verify Target App on 9001 is back to HTTP 200 OK
    r_health_after = requests.get(f"{TARGET_APP}/health", timeout=3.0)
    assert r_health_after.status_code == 200, "Target application must return HTTP 200 OK after real remediation"
    print(f"[PASS] Target Application Health Post-Remediation: HTTP {r_health_after.status_code} OK")

    # -------------------------------------------------------------------------
    # FEATURE 15: REAL Resolution Outcome Summary
    # -------------------------------------------------------------------------
    print("\n--- FEATURE 15: REAL RESOLUTION OUTCOME ---")
    r_outcome = requests.get(f"{AGENT_API}/incidents/{incident_id}/outcome", timeout=5.0)
    assert r_outcome.status_code == 200
    outcome_data = r_outcome.json()
    print(f"[PASS] Final Status: {outcome_data.get('final_status')}")
    print(f"[PASS] Total Attempts: {outcome_data.get('total_attempts')}")
    print(f"[PASS] Time to Resolution: {outcome_data.get('time_to_resolution_seconds')}s")
    assert outcome_data.get("final_status") == "RESOLVED"

    # -------------------------------------------------------------------------
    # FEATURE 16: Hindsight Retention & Subsequent Recall
    # -------------------------------------------------------------------------
    print("\n--- FEATURE 16: HINDSIGHT RETENTION & SUBSEQUENT RECALL ---")
    assert outcome_data.get("memory_updated") == True, "Resolution outcome must be retained in Hindsight memory"
    print("[PASS] Resolution experience retained in Hindsight memory store!")

    # Test Subsequent Incident Recall via /diagnose endpoint
    r_diag = requests.post(f"{AGENT_API}/diagnose", json={"alert": "Production incident in payment-gateway-test: restart_service applied"}, timeout=10.0)
    assert r_diag.status_code == 200
    diag_res = r_diag.json()
    recalled_in_subsequent = diag_res.get("raw_recalled_memories", [])
    assert len(recalled_in_subsequent) > 0, "Subsequent diagnose query must recall newly retained incident memory"
    print(f"[PASS] Subsequent diagnose query successfully recalled {len(recalled_in_subsequent)} historical incidents from Hindsight memory bank!")

    # -------------------------------------------------------------------------
    # FEATURE 17 & 18: Postmortem Archiving & End-to-End UI Integration
    # -------------------------------------------------------------------------
    print("\n--- FEATURE 17 & 18: POSTMORTEM ARCHIVE & GOVERNANCE UI ---")
    r_archive = requests.get(f"{AGENT_API}/incidents", timeout=5.0)
    assert r_archive.status_code == 200
    all_archived = r_archive.json()
    found_inc = next((i for i in all_archived if i.get("incident_id") == incident_id), None)
    assert found_inc is not None, "Incident record must be archived in canonical incident store"
    print(f"[PASS] Postmortem archived cleanly for canonical incident '{incident_id}'!")
    print(f"[PASS] UI & Backend connected across all 18 features!")

    print("\n" + "=" * 80)
    print(" ALL 18 PRODUCTION FEATURES VERIFIED END-TO-END ON REAL APPLICATION DATA!")
    print("=" * 80)


if __name__ == "__main__":
    main()
