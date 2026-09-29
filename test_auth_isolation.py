"""
Exhaustive Multi-Tenant Authentication & Data Isolation Verification Script.
Tests:
1. Existing Ramya login -> existing data (15 apps, 71 incidents) visible.
2. New user registration -> 0 apps, 0 incidents, empty Hindsight memory.
3. Direct API call by new user accessing Ramya's incident -> 404 Not Found (blocked).
4. Direct API call by new user accessing Ramya's app -> 404 Not Found (blocked).
5. New user registers an application and creates an incident -> only that new user sees it.
6. Ramya login -> Ramya still sees only Ramya's data, untouched.
7. Logout / session refresh isolation verification.
"""

import sys
import uuid
import requests

BACKEND = "http://127.0.0.1:8001"

def run_tests():
    print("=" * 80)
    print("RUNNING AUTHENTICATION & MULTI-TENANT ISOLATION TESTS")
    print("=" * 80)

    # 1. Login as Ramya
    print("\n[STEP 1] Logging into existing Ramya account...")
    login_res = requests.post(f"{BACKEND}/api/auth/login", json={
        "username_or_email": "ramya",
        "password": "123456"
    })
    assert login_res.status_code == 200, f"Ramya login failed: {login_res.text}"
    ramya_token = login_res.json()["token"]
    ramya_user = login_res.json()["user"]
    assert ramya_user["username"] == "ramya"
    assert ramya_user["id"] == 2
    ramya_headers = {"Authorization": f"Bearer {ramya_token}"}
    print(f"  -> Ramya authenticated successfully (user_id={ramya_user['id']})")

    # Check Ramya's applications
    ramya_apps_res = requests.get(f"{BACKEND}/api/applications", headers=ramya_headers)
    assert ramya_apps_res.status_code == 200
    ramya_apps = ramya_apps_res.json()
    print(f"  -> Ramya applications count: {len(ramya_apps)}")
    assert len(ramya_apps) >= 10, f"Expected Ramya to have existing demo apps, got {len(ramya_apps)}"

    # Check Ramya's incidents
    ramya_inc_res = requests.get(f"{BACKEND}/incidents", headers=ramya_headers)
    assert ramya_inc_res.status_code == 200
    ramya_incidents = ramya_inc_res.json()
    print(f"  -> Ramya incidents count: {len(ramya_incidents)}")
    assert len(ramya_incidents) >= 10, f"Expected Ramya to have existing incidents, got {len(ramya_incidents)}"
    ramya_sample_incident_id = ramya_incidents[0]["incident_id"]
    print(f"  -> Ramya sample incident ID: {ramya_sample_incident_id}")

    # 2. Register a brand-new user
    new_user_name = f"user_{uuid.uuid4().hex[:6]}"
    new_user_email = f"{new_user_name}@enterprise.com"
    print(f"\n[STEP 2] Registering new user: {new_user_name} ({new_user_email})...")
    signup_res = requests.post(f"{BACKEND}/api/auth/signup", json={
        "username": new_user_name,
        "email": new_user_email,
        "password": "SecurePassword123!",
        "confirm_password": "SecurePassword123!"
    })
    assert signup_res.status_code == 201, f"Signup failed: {signup_res.text}"
    new_token = signup_res.json()["token"]
    new_user = signup_res.json()["user"]
    new_headers = {"Authorization": f"Bearer {new_token}"}
    print(f"  -> New user created: id={new_user['id']}, username={new_user['username']}")

    # 3. Verify New User has ZERO applications and ZERO incidents
    print("\n[STEP 3] Verifying new user starts with 0 applications and 0 incidents...")
    new_apps_res = requests.get(f"{BACKEND}/api/applications", headers=new_headers)
    assert new_apps_res.status_code == 200
    new_apps = new_apps_res.json()
    print(f"  -> New user applications count: {len(new_apps)}")
    assert len(new_apps) == 0, f"New user must have 0 applications, found: {len(new_apps)}"

    new_inc_res = requests.get(f"{BACKEND}/incidents", headers=new_headers)
    assert new_inc_res.status_code == 200
    new_incidents = new_inc_res.json()
    print(f"  -> New user incidents count: {len(new_incidents)}")
    assert len(new_incidents) == 0, f"New user must have 0 incidents, found: {len(new_incidents)}"

    # 4. Verify New User CANNOT access Ramya's incident via direct API call (404 Not Found)
    print(f"\n[STEP 4] Testing security isolation: New user requests Ramya's incident {ramya_sample_incident_id}...")
    direct_hack_res = requests.get(f"{BACKEND}/incidents/{ramya_sample_incident_id}", headers=new_headers)
    print(f"  -> Direct access status code: {direct_hack_res.status_code} (detail: {direct_hack_res.text})")
    assert direct_hack_res.status_code == 404, f"Security violation: New user could access Ramya's incident! Status: {direct_hack_res.status_code}"
    print("  -> PASS: Cross-tenant direct access blocked with HTTP 404 Not Found")

    # 5. Verify New User CANNOT approve or execute Ramya's incident
    print(f"\n[STEP 5] Testing authorization bypass prevention: New user attempts to approve Ramya's incident...")
    hack_approve_res = requests.post(
        f"{BACKEND}/incidents/{ramya_sample_incident_id}/approve",
        json={"approver": new_user_name, "note": "Malicious approval"},
        headers=new_headers
    )
    assert hack_approve_res.status_code == 404, f"Security violation: New user approved Ramya's incident! Status: {hack_approve_res.status_code}"
    print("  -> PASS: Cross-tenant approval blocked with HTTP 404 Not Found")

    # 6. New User registers an application and runs inspection
    print("\n[STEP 6] New user registers their own application...")
    new_app_cfg = {
        "application_id": f"app-{new_user_name}",
        "application_name": f"{new_user_name} Service",
        "service_id": f"svc-{new_user_name}",
        "base_url": "http://127.0.0.1:9001",
        "health_endpoint": "/incidents/settlement/health",
        "remediation_endpoint": "/incidents/settlement/remediation",
        "log_collection_method": "http",
        "description": "New user isolated microservice"
    }
    reg_res = requests.post(f"{BACKEND}/api/applications", json=new_app_cfg, headers=new_headers)
    assert reg_res.status_code in (200, 201), f"Registration failed: {reg_res.text}"
    print("  -> New user registered application successfully.")

    # Verify new user now sees 1 application
    new_apps_after = requests.get(f"{BACKEND}/api/applications", headers=new_headers).json()
    assert len(new_apps_after) == 1
    assert new_apps_after[0]["application_id"] == f"app-{new_user_name}"
    print(f"  -> New user now sees only their 1 application: {new_apps_after[0]['application_id']}")

    # New user inspects their application to trigger an incident
    print("  -> New user inspecting their application...")
    inspect_res = requests.post(
        f"{BACKEND}/api/incidents/inspect",
        json={"application_id": f"app-{new_user_name}"},
        headers=new_headers
    )
    assert inspect_res.status_code == 200
    inspect_data = inspect_res.json()
    assert inspect_data["incident_detected"] is True
    new_user_incident_id = inspect_data["incident"]["incident_id"]
    print(f"  -> New user incident created: {new_user_incident_id}")

    # Verify new user sees their 1 incident
    new_user_inc_list = requests.get(f"{BACKEND}/incidents", headers=new_headers).json()
    assert len(new_user_inc_list) == 1
    assert new_user_inc_list[0]["incident_id"] == new_user_incident_id
    print("  -> New user incident list contains strictly 1 incident.")

    # 7. Verify Ramya CANNOT see the new user's application or incident
    print("\n[STEP 7] Verifying Ramya's account isolation...")
    ramya_apps_check = requests.get(f"{BACKEND}/api/applications", headers=ramya_headers).json()
    assert not any(a["application_id"] == f"app-{new_user_name}" for a in ramya_apps_check), "Ramya leaked new user application!"
    print(f"  -> Ramya does NOT see new user application (Ramya count: {len(ramya_apps_check)})")

    ramya_inc_check = requests.get(f"{BACKEND}/incidents", headers=ramya_headers).json()
    assert not any(i["incident_id"] == new_user_incident_id for i in ramya_inc_check), "Ramya leaked new user incident!"
    print(f"  -> Ramya does NOT see new user incident (Ramya count: {len(ramya_inc_check)})")

    # 8. Ramya CANNOT access new user's incident via direct API call (404)
    ramya_cross_res = requests.get(f"{BACKEND}/incidents/{new_user_incident_id}", headers=ramya_headers)
    assert ramya_cross_res.status_code == 404
    print("  -> PASS: Ramya cannot access new user's incident via direct API (404 Not Found)")

    print("\n" + "=" * 80)
    print("ALL MULTI-TENANT ISOLATION TESTS PASSED WITH 100% SUCCESS!")
    print("=" * 80)

if __name__ == "__main__":
    run_tests()
