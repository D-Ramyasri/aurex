import os
import time
import requests
import pytest
from fastapi.testclient import TestClient

from main import app

TARGET_URL = "http://127.0.0.1:9001"
BACKEND_URL = "http://127.0.0.1:8001"


def test_live_investigation():
    """
    Live Incident Investigation & RCA Verification with real target application on 9001.
    """
    # 1. Verify target application is running on port 9001
    try:
        r_health = requests.get(f"{TARGET_URL}/health", timeout=2.0)
        assert r_health.status_code == 200, f"Expected 200 from {TARGET_URL}/health, got {r_health.status_code}"
    except Exception as e:
        pytest.skip(f"Live target application on {TARGET_URL} is not reachable: {e}")

    # 2. Clear log file for a clean fresh incident
    log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sample_logs", "payment-test-api.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("")

    # 3. Generate a real runtime failure on Target Application
    payload = {
        "account_id": "acc_merch_001",
        "amount": 420.00,
        "currency": "USD",
        "card_number": "9999-0000-0000-8812",
        "cvv": "999"
    }
    r_pay = requests.post(f"{TARGET_URL}/payments", json=payload, timeout=5.0)
    assert r_pay.status_code == 504, f"Expected 504 gateway timeout, got {r_pay.status_code}"

    # 4. Verify real log was written by Target Application
    time.sleep(0.2)
    with open(log_path, "r", encoding="utf-8") as f:
        log_content = f.read()

    assert "Dependency failure" in log_content or "upstream network timeout" in log_content or "timed out" in log_content

    # 5. Invoke Incident Response Agent inspection endpoint
    data = None
    try:
        r_inspect = requests.post(
            f"{BACKEND_URL}/api/incidents/inspect",
            json={"application_id": "payment-test-api"},
            timeout=25.0
        )
        if r_inspect.status_code == 200:
            data = r_inspect.json()
    except Exception:
        pass

    if data is None:
        # Fallback to in-process dispatch if backend 8001 is not running as separate server
        client = TestClient(app)
        r_inspect = client.post("/api/incidents/inspect", json={"application_id": "payment-test-api"})
        assert r_inspect.status_code == 200
        data = r_inspect.json()

    # 6. Verify Investigation and RCA results
    assert data.get("incident_detected") is True
    incident = data.get("incident", {})
    assert incident.get("incident_id", "").startswith("INC-")

    analysis = data.get("analysis")
    assert analysis is not None
    assert analysis.get("incident_type") is not None
    assert str(analysis.get("severity", "")).lower() in ["low", "medium", "high", "critical"]
    assert analysis.get("root_cause") is not None
    assert analysis.get("why") is not None

    timeline = data.get("timeline")
    assert timeline is not None
    assert len(timeline) > 0

    resolution = data.get("resolution")
    assert resolution is not None
    assert len(resolution.get("actions", [])) > 0

    hindsight = data.get("hindsight")
    assert hindsight is not None
