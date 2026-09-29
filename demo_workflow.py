"""
End-to-End Demo Script for Incident Response Execution Engine.
Runs the complete autonomous loop over HTTP:
1. Injects fault: bad_deploy into checkout-service
2. Diagnoses incident via POST /diagnose
3. Approves wrong action: restart_service
4. Executes action -> Telemetry probes evaluate failure -> FAILURE
5. Enters failure recovery loop -> cycle 2 created awaiting approval
6. Approves correct action: rollback_deployment
7. Executes action -> Telemetry probes confirm normal health -> SUCCESS
8. Prints final structured resolution outcome JSON from GET /incidents/{id}/outcome
"""

import os
import sys
import json
import time
import requests

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8001")


def safe_print(*args, **kwargs):
    """Prints text handling Windows console encoding cleanly."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        text = " ".join(str(a) for a in args)
        clean = text.encode("ascii", errors="replace").decode("ascii")
        print(clean, **kwargs)


def print_banner(title: str):
    width = 80
    safe_print("\n" + "=" * width)
    safe_print(f" {title} ".center(width, "="))
    safe_print("=" * width)


def print_step(step_num: int, title: str):
    safe_print(f"\n[STEP {step_num}] {title}")
    safe_print("-" * 60)


def check_backend():
    try:
        r = requests.get(f"{BACKEND_URL}/health", timeout=3)
        if r.status_code == 200:
            return True
    except Exception:
        pass
    return False


def run_demo():
    print_banner("INCIDENT RESPONSE EXECUTION ENGINE - END-TO-END DEMO")
    safe_print(f"Backend Target: {BACKEND_URL}")

    # Check if backend is running; if not, use TestClient so demo can run standalone as well
    use_test_client = not check_backend()
    if use_test_client:
        safe_print("Notice: No live HTTP server detected on port 8001.")
        safe_print("Using in-process TestClient to execute end-to-end demo flow...")
        from fastapi.testclient import TestClient
        from main import app
        http = TestClient(app)
    else:
        safe_print("Live backend API connected on http://127.0.0.1:8001!")
        http = requests.Session()

    # Step 1: Reset & Inject Fault
    print_step(1, "Resetting Infrastructure & Injecting 'bad_deploy' Fault")
    http.post(f"{BACKEND_URL}/sim/reset" if not use_test_client else "/sim/reset")
    inj_resp = http.post(
        f"{BACKEND_URL}/sim/inject" if not use_test_client else "/sim/inject",
        json={"scenario": "bad_deploy"}
    )
    safe_print(f"Fault Injection Response: {inj_resp.json()}")

    # Check initial degraded metrics
    svc_metrics = http.get(
        f"{BACKEND_URL}/sim/checkout-service/metrics" if not use_test_client else "/sim/checkout-service/metrics"
    ).json()
    safe_print(f"Checkout Service Initial State: version={svc_metrics['version']}, error_rate={svc_metrics['error_rate']}, status={svc_metrics['status']}")

    # Step 2: Diagnose Incident
    print_step(2, "Calling POST /diagnose with Alert")
    alert_text = (
        "TypeError: Cannot read property 'currency' of undefined at CheckoutOrderService.process "
        "on checkout-service v2.4.1 impacting 12% of checkout flows."
    )
    diag_resp = http.post(
        f"{BACKEND_URL}/diagnose" if not use_test_client else "/diagnose",
        json={"alert": alert_text}
    )
    diag_data = diag_resp.json()
    incident_id = diag_data.get("incident_id")
    safe_print(f"Incident Created: ID = {incident_id}")
    safe_print(f"Memory Source Used: {diag_data.get('memory_source')}")
    safe_print(f"Recommended Action: {diag_data.get('recommended_action')}")

    # Step 3: Approve Wrong Action (restart_service)
    print_step(3, "Approving Non-Effective Action: restart_service")
    app_resp = http.post(
        f"{BACKEND_URL}/incidents/{incident_id}/approve" if not use_test_client else f"/incidents/{incident_id}/approve",
        json={
            "approver": "Junior SRE",
            "note": "Let's try restarting the pod first",
            "action": {
                "type": "restart_service",
                "target_service": "checkout-service",
                "params": {}
            }
        }
    )
    safe_print(f"Approval State: {app_resp.json().get('approval', {}).get('state')}")
    safe_print(f"Approved Action: {app_resp.json().get('recommended_action', {}).get('type')}")

    # Step 4: Execute Wrong Action & Run Automated Verification
    print_step(4, "Executing restart_service & Collecting Telemetry Verification")
    safe_print("Executing action and running 5 genuine health probes spaced 0.3s apart...")
    exec_resp = http.post(
        f"{BACKEND_URL}/incidents/{incident_id}/execute" if not use_test_client else f"/incidents/{incident_id}/execute"
    )
    exec_data = exec_resp.json()
    ver1 = exec_data.get("verification", {})
    safe_print(f"Execution 1 Status: {exec_data.get('execution', {}).get('status')}")
    safe_print(f"Verification 1 Status: {ver1.get('status')}")
    safe_print(f"Improvement Summary: {ver1.get('improvement_summary')}")
    safe_print(f"Reasons: {ver1.get('reasons')}")

    # Step 5: Check Failure Recovery Loop
    print_step(5, "Verifying Failure Recovery Loop State")
    inc_state1 = http.get(
        f"{BACKEND_URL}/incidents/{incident_id}" if not use_test_client else f"/incidents/{incident_id}"
    ).json()
    safe_print(f"Incident Status: {inc_state1.get('status')}")
    safe_print(f"Current Cycle: #{inc_state1.get('cycle')}")
    safe_print(f"Approval Reset To: {inc_state1.get('approval', {}).get('state')}")
    safe_print(f"Failed Attempts Recorded: {len(inc_state1.get('failed_attempts', []))}")
    next_action_type = (inc_state1.get("recommended_action") or {}).get("type", "manual_selection_required")
    safe_print(f"Next Action Recommended by Recovery Loop: {next_action_type}")

    # Step 6: Approve Correct Action (rollback_deployment)
    print_step(6, "Approving Effective Fix: rollback_deployment")
    app_resp2 = http.post(
        f"{BACKEND_URL}/incidents/{incident_id}/approve" if not use_test_client else f"/incidents/{incident_id}/approve",
        json={
            "approver": "Principal SRE",
            "note": "Reverting deployment regression v2.4.1 to v2.4.0",
            "action": {
                "type": "rollback_deployment",
                "target_service": "checkout-service",
                "params": {}
            }
        }
    )
    safe_print(f"Approval State: {app_resp2.json().get('approval', {}).get('state')}")

    # Step 7: Execute Correct Action & Verify Recovery
    print_step(7, "Executing rollback_deployment & Verifying Recovery")
    safe_print("Executing action and running telemetry verification probes...")
    exec_resp2 = http.post(
        f"{BACKEND_URL}/incidents/{incident_id}/execute" if not use_test_client else f"/incidents/{incident_id}/execute"
    )
    exec_data2 = exec_resp2.json()
    ver2 = exec_data2.get("verification", {})
    safe_print(f"Execution 2 Status: {exec_data2.get('execution', {}).get('status')}")
    safe_print(f"Verification 2 Status: {ver2.get('status')}")
    safe_print(f"Improvement Summary: {ver2.get('improvement_summary')}")
    safe_print("Criteria Checklist:")
    for crit in ver2.get("criteria", []):
        mark = "PASS" if crit.get("passed") else "FAIL"
        safe_print(f"  [{mark}] {crit.get('id')}: {crit.get('description')} (observed: {crit.get('observed')})")

    # Step 8: Fetch and Print Final Structured Resolution Outcome
    print_step(8, "Fetching Structured Resolution Outcome (GET /incidents/{id}/outcome)")
    outcome_resp = http.get(
        f"{BACKEND_URL}/incidents/{incident_id}/outcome" if not use_test_client else f"/incidents/{incident_id}/outcome"
    )
    outcome_data = outcome_resp.json()

    print_banner("INCIDENT RESOLUTION OUTCOME JSON")
    safe_print(json.dumps(outcome_data, indent=2, default=str))

    print_banner("DEMO COMPLETED SUCCESSFULLY")


if __name__ == "__main__":
    run_demo()
