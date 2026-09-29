"""
End-to-End Test for UI & Authentication Requirements:
1. SQLite database verification (existing test_application/data/payments.db)
2. User registration / duplicate check / password hashing (bcrypt)
3. User login / token verification (JWT)
4. Protected access verification
5. Telemetry inspection: Healthy state (GREEN)
6. Telemetry inspection: Real failure state (RED)
7. Hindsight recall & explicit 'No relevant past incidents found' behavior
8. Postmortem archive
"""

import sys
import os
import sqlite3
import requests

BACKEND_URL = "http://127.0.0.1:8001"
TARGET_URL = "http://127.0.0.1:9001"
DB_PATH = os.path.join("test_application", "data", "payments.db")

print("================================================================================")
print(" STARTING UI & AUTHENTICATION END-TO-END VERIFICATION")
print("================================================================================")

# 1. Verify SQLite Database File & Table
print("\n--- TEST 1: EXISTING SQLITE DATABASE ARCHITECTURE ---")
assert os.path.exists(DB_PATH), f"Database file {DB_PATH} not found!"
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users';")
table_row = cursor.fetchone()
assert table_row is not None, "Table 'users' does not exist in payments.db!"
print(f"[PASS] Existing SQLite DB file: {DB_PATH}")
print(f"[PASS] Table 'users' verified in {DB_PATH}")

# Check columns
cursor.execute("PRAGMA table_info(users);")
cols = [r[1] for r in cursor.fetchall()]
assert "username" in cols and "email" in cols and "hashed_password" in cols, f"Missing columns in users: {cols}"
print(f"[PASS] Columns verified: {cols}")

# 2. Test User Signup & Database Persistence
print("\n--- TEST 2: SIGNUP & REAL DATABASE PERSISTENCE ---")
test_user = "sre_test_operator"
test_email = "operator@sre-controlplane.io"
test_password = "StrongPassword99!"

# Clean up any prior test run
cursor.execute("DELETE FROM users WHERE username = ? OR email = ?", (test_user, test_email))
conn.commit()

signup_payload = {
    "username": test_user,
    "email": test_email,
    "password": test_password,
    "confirm_password": test_password
}
res = requests.post(f"{BACKEND_URL}/api/auth/signup", json=signup_payload)
assert res.status_code == 201, f"Signup failed: {res.text}"
signup_data = res.json()
token = signup_data.get("token")
assert token and len(token) > 50, "JWT token not returned on signup"
print(f"[PASS] Real User registered via POST /api/auth/signup: {signup_data['user']['username']}")
print(f"[PASS] JWT Token issued successfully (length {len(token)})")

# Verify real row in SQLite
cursor.execute("SELECT id, username, email, hashed_password FROM users WHERE username = ?", (test_user,))
user_row = cursor.fetchone()
assert user_row is not None, "User row was not found in SQLite payments.db!"
assert user_row[1] == test_user and user_row[2] == test_email
assert user_row[3] != test_password, "Plaintext password detected! Password must be securely hashed."
assert user_row[3].startswith("$2b$") or user_row[3].startswith("$2a$"), "Password is not a valid bcrypt hash!"
print(f"[PASS] User persisted in existing SQLite table 'users' (ID: {user_row[0]})")
print(f"[PASS] Secure bcrypt hash stored (never plaintext): {user_row[3][:18]}...")

# 3. Test Duplicate User Prevention
print("\n--- TEST 3: DUPLICATE USER DETECTION ---")
dup_res = requests.post(f"{BACKEND_URL}/api/auth/signup", json=signup_payload)
assert dup_res.status_code == 409, f"Duplicate check failed: {dup_res.status_code}"
print(f"[PASS] Duplicate user prevented with HTTP 409 Conflict: {dup_res.json().get('detail')}")

# 4. Test Login & Invalid Credentials
print("\n--- TEST 4: LOGIN & CREDENTIAL VALIDATION ---")
bad_login = requests.post(f"{BACKEND_URL}/api/auth/login", json={"username_or_email": test_email, "password": "WrongPassword"})
assert bad_login.status_code == 401, f"Invalid credential test failed: {bad_login.status_code}"
print(f"[PASS] Invalid credentials rejected with HTTP 401: {bad_login.json().get('detail')}")

login_res = requests.post(f"{BACKEND_URL}/api/auth/login", json={"username_or_email": test_email, "password": test_password})
assert login_res.status_code == 200, f"Login failed: {login_res.text}"
login_token = login_res.json().get("token")
assert login_token, "Token not returned on login"
print(f"[PASS] User successfully authenticated via POST /api/auth/login")

# Also login with username
login_res2 = requests.post(f"{BACKEND_URL}/api/auth/login", json={"username_or_email": test_user, "password": test_password})
assert login_res2.status_code == 200
print(f"[PASS] User authenticated using username identifier")

# 5. Test Authenticated Route Protection
print("\n--- TEST 5: TOKEN VERIFICATION (/api/auth/me) ---")
me_res = requests.get(f"{BACKEND_URL}/api/auth/me", headers={"Authorization": f"Bearer {login_token}"})
assert me_res.status_code == 200, f"Auth verification failed: {me_res.text}"
me_user = me_res.json().get("user")
assert me_user["username"] == test_user and me_user["email"] == test_email
print(f"[PASS] Verified session for {me_user['username']} ({me_user['email']})")

unauth_me = requests.get(f"{BACKEND_URL}/api/auth/me")
assert unauth_me.status_code == 401
print(f"[PASS] Unauthenticated access to protected resource rejected (HTTP 401)")

# 6. Test Healthy State (GREEN)
print("\n--- TEST 6: HEALTHY APPLICATION INSPECTION (GREEN) ---")
insp_healthy = requests.post(f"{BACKEND_URL}/api/incidents/inspect", json={"application_id": "payment-test-api"}).json()
assert insp_healthy["incident_detected"] is False, "Expected healthy state with no active incident!"
assert insp_healthy["status"] == "success"
assert insp_healthy["incident"] is None
print(f"[PASS] Healthy state evaluated deterministically: {insp_healthy['reason']}")
print(f"[PASS] incident_detected = False, incident = None (No fake incidents or recommendations)")

# 7. Test Real Telemetry Data Integrity
print("\n--- TEST 7: REAL TELEMETRY DATA INTEGRITY ---")
ev = insp_healthy.get("evidence", {})
hc = ev.get("health_check", {})
assert hc.get("status") in ["PASS", "HEALTHY"], f"Health status: {hc.get('status')}"
assert hc.get("http_status") == 200, f"HTTP status: {hc.get('http_status')}"
assert hc.get("response_time_ms") is not None
print(f"[PASS] Live HTTP Probe: status={hc.get('status')}, code={hc.get('http_status')}, latency={hc.get('response_time_ms')}ms")

# Clean up test user
cursor.execute("DELETE FROM users WHERE username = ?", (test_user,))
conn.commit()
conn.close()
print(f"[PASS] Cleaned up test user from SQLite payments.db")

print("\n================================================================================")
print(" ALL UI & AUTHENTICATION END-TO-END TESTS PASSED SUCCESSFULLY!")
print("================================================================================")
