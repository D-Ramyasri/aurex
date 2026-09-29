"""
Integration and Unit Tests for Incident Response Execution Engine.
Covers all 9 required test scenarios using FastAPI TestClient with stubs for LLM/Hindsight.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient

from main import app
import agent
from sim_env import SERVICES
from incidents import (
    create_incident,
    get_incident,
    update_incident,
    clear_all_incidents,
    compute_action_fingerprint,
    ApprovalState,
    IncidentStatus
)
from canonical_incident import generate_incident_id


@pytest.fixture(autouse=True)
def setup_teardown_env(monkeypatch):
    """Resets sim environment and incident store before every test, stubs Groq and Hindsight."""
    client = TestClient(app)
    client.post("/sim/reset")
    clear_all_incidents()
    monkeypatch.setenv("EXECUTION_TARGET", "simulator")

    # Stub LLM & Hindsight calls so external network is not required
    monkeypatch.setattr(agent, "call_groq_llm", lambda prompt: "Stubbed Diagnosis: Root cause analysis.")
    monkeypatch.setattr(agent, "extract_incident_details", lambda text: {
        "service": "checkout-service" if "checkout" in text.lower() else "database-cluster",
        "severity": "high",
        "symptom_type": "error",
        "summary": text[:60]
    })
    monkeypatch.setattr(agent, "reflect_on_incident", lambda text: "Stubbed Reflection: Past patterns show rollback or config fix.")
    monkeypatch.setattr(agent, "get_hindsight_client", lambda: None)
    monkeypatch.setattr(agent, "retain_outcome", lambda inc: True)

    yield client


def test_1_execute_before_approval_409_sim_unchanged(setup_teardown_env):
    """1. execute before approval -> 409 and sim state unchanged."""
    client = setup_teardown_env
    client.post("/sim/inject", json={"scenario": "db_pool_exhaustion"})
    metrics_before = client.get("/sim/database-cluster/metrics").json()

    inc = create_incident(
        incident_id=generate_incident_id(),
        alert="Database pool exhausted",
        extracted_details={"service": "database-cluster"},
        diagnosis="Exhaustion",
        reflection="Reflect",
        raw_recalled_memories=[],
        recommended_action={"type": "restart_service", "target_service": "database-cluster", "params": {}, "rationale": "test"}
    )
    inc_id = inc["id"]

    # Execute before approval
    resp = client.post(f"/incidents/{inc_id}/execute")
    assert resp.status_code == 409
    assert "Approval must be APPROVED" in resp.text

    # Verify sim state remains completely unchanged
    metrics_after = client.get("/sim/database-cluster/metrics").json()
    assert metrics_before == metrics_after


def test_2_reject_then_execute_409(setup_teardown_env):
    """2. reject then execute -> 409."""
    client = setup_teardown_env
    inc = create_incident(
        incident_id=generate_incident_id(),
        alert="Test reject alert",
        extracted_details={"service": "auth-service"},
        diagnosis="Auth error",
        reflection="Reflect",
        raw_recalled_memories=[],
        recommended_action={"type": "flush_cache", "target_service": "auth-service", "params": {}, "rationale": "flush"}
    )
    inc_id = inc["id"]

    # Reject incident
    r_rej = client.post(f"/incidents/{inc_id}/reject", json={"approver": "Senior SRE", "note": "Wrong action"})
    assert r_rej.status_code == 200
    assert r_rej.json()["approval"]["state"] == "REJECTED"

    # Execute rejected incident -> 409
    r_exec = client.post(f"/incidents/{inc_id}/execute")
    assert r_exec.status_code == 409
    assert "Approval must be APPROVED" in r_exec.text


def test_3_approve_then_approve_or_reject_again_409_immutable(setup_teardown_env):
    """3. approve then approve/reject again -> 409 (immutable)."""
    client = setup_teardown_env
    inc = create_incident(
        incident_id=generate_incident_id(),
        alert="Test immutable approval",
        extracted_details={"service": "payment-gateway"},
        diagnosis="Diag",
        reflection="Reflect",
        raw_recalled_memories=[],
        recommended_action={"type": "restart_service", "target_service": "payment-gateway", "params": {}, "rationale": "restart"}
    )
    inc_id = inc["id"]

    # First approval -> 200
    r_app = client.post(f"/incidents/{inc_id}/approve", json={"approver": "Engineer 1", "note": "OK"})
    assert r_app.status_code == 200
    assert r_app.json()["approval"]["state"] == "APPROVED"

    # Second approval -> 409
    r_app2 = client.post(f"/incidents/{inc_id}/approve", json={"approver": "Engineer 2", "note": "Try override"})
    assert r_app2.status_code == 409
    assert "A decided approval cannot change" in r_app2.text

    # Rejection after approval -> 409
    r_rej = client.post(f"/incidents/{inc_id}/reject", json={"approver": "Engineer 2", "note": "Reject"})
    assert r_rej.status_code == 409
    assert "A decided approval cannot change" in r_rej.text


def test_4_action_override_fingerprint_mismatch_409(setup_teardown_env):
    """4. approve with action override changes fingerprint; executing a different action than approved -> 409."""
    client = setup_teardown_env
    inc = create_incident(
        incident_id=generate_incident_id(),
        alert="Test override alert",
        extracted_details={"service": "payment-gateway"},
        diagnosis="Diag",
        reflection="Reflect",
        raw_recalled_memories=[],
        recommended_action={"type": "restart_service", "target_service": "payment-gateway", "params": {}, "rationale": "rec"}
    )
    inc_id = inc["id"]

    # Approve with override: scale_replicas
    override_action = {"type": "scale_replicas", "target_service": "payment-gateway", "params": {"replicas": 4}}
    r_app = client.post(f"/incidents/{inc_id}/approve", json={
        "approver": "Lead SRE",
        "note": "Scale instead of restart",
        "action": override_action
    })
    assert r_app.status_code == 200
    app_data = r_app.json()
    approved_fp = app_data["approval"]["approved_fingerprint"]
    expected_override_fp = compute_action_fingerprint("scale_replicas", "payment-gateway", {"replicas": 4})
    assert approved_fp == expected_override_fp

    # Simulate tampered or changed action after approval
    fresh_inc = get_incident(inc_id)
    fresh_inc["recommended_action"] = {"type": "flush_cache", "target_service": "payment-gateway", "params": {}}
    fresh_inc["action_fingerprint"] = compute_action_fingerprint("flush_cache", "payment-gateway", {})
    update_incident(inc_id, fresh_inc)

    # Executing now must fail with 409 due to fingerprint mismatch
    r_exec = client.post(f"/incidents/{inc_id}/execute")
    assert r_exec.status_code == 409
    assert "fingerprint" in r_exec.text.lower()


def test_5_bad_deploy_rollback_deployment_success(setup_teardown_env):
    """5. bad_deploy + rollback_deployment -> SUCCESS with real before/after difference."""
    client = setup_teardown_env
    client.post("/sim/inject", json={"scenario": "bad_deploy"})

    # Check degraded before state
    svc_before = client.get("/sim/checkout-service/metrics").json()
    assert svc_before["error_rate"] == 0.12
    assert svc_before["version"] == "v2.4.1"

    inc = create_incident(
        incident_id=generate_incident_id(),
        alert="Bad deploy on checkout-service",
        extracted_details={"service": "checkout-service"},
        diagnosis="Null pointer in v2.4.1",
        reflection="Rollback to v2.4.0",
        raw_recalled_memories=[],
        recommended_action={"type": "rollback_deployment", "target_service": "checkout-service", "params": {}, "rationale": "revert bad deploy"}
    )
    inc_id = inc["id"]

    client.post(f"/incidents/{inc_id}/approve", json={"approver": "SRE", "note": "Rollback approved"})
    r_exec = client.post(f"/incidents/{inc_id}/execute")
    assert r_exec.status_code == 200
    res = r_exec.json()

    assert res["execution"]["status"] == "COMPLETED"
    assert res["verification"]["status"] == "SUCCESS"

    # Verify real before/after differences
    before = res["verification"]["before"]
    after = res["verification"]["after"]
    assert before["error_rate"] == 0.12
    assert after["error_rate"] == 0.001
    assert after["service_status"] == "running"
    assert after["health_pass_ratio"] == 1.0
    assert not any("fault was on" in reason for reason in res["verification"]["reasons"])

    # Incident status should be RESOLVED
    inc_final = client.get(f"/incidents/{inc_id}").json()
    assert inc_final["status"] == "RESOLVED"
    assert inc_final["outcome"]["final_status"] == "RESOLVED"


def test_payment_timeout_action_on_checkout_fails_for_wrong_service(setup_teardown_env):
    client = setup_teardown_env
    client.post("/sim/inject", json={"scenario": "payment_timeout"})
    inc = create_incident(
        incident_id=generate_incident_id(),
        alert="Payment timeout while action targets checkout",
        extracted_details={"service": "checkout-service"},
        diagnosis="Payment gateway timeout",
        reflection="Reflect",
        raw_recalled_memories=[],
        recommended_action={"type": "restart_service", "target_service": "checkout-service", "params": {}, "rationale": "test wrong target"}
    )

    client.post(f"/incidents/{inc['id']}/approve", json={"approver": "SRE", "note": "Test"})
    response = client.post(f"/incidents/{inc['id']}/execute")

    assert response.status_code == 200
    verification = response.json()["verification"]
    assert verification["status"] == "FAILURE"
    assert verification["primary_service"] == "payment-gateway"
    assert any("fault was on payment-gateway" in reason for reason in verification["reasons"])


def test_no_fault_execution_is_uncertain(setup_teardown_env):
    client = setup_teardown_env
    inc = create_incident(
        incident_id=generate_incident_id(),
        alert="No active fault present",
        extracted_details={"service": "checkout-service"},
        diagnosis="No fault detected",
        reflection="Reflect",
        raw_recalled_memories=[],
        recommended_action={"type": "restart_service", "target_service": "checkout-service", "params": {}, "rationale": "test no fault"}
    )

    client.post(f"/incidents/{inc['id']}/approve", json={"approver": "SRE", "note": "Test"})
    response = client.post(f"/incidents/{inc['id']}/execute")

    assert response.status_code == 200
    verification = response.json()["verification"]
    assert verification["status"] == "UNCERTAIN"
    assert verification["primary_service"] == "checkout-service"
    assert "No fault present; nothing to verify" in verification["reasons"]


def test_6_db_pool_exhaustion_restart_uncertain(setup_teardown_env):
    """6. db_pool_exhaustion + restart_service -> UNCERTAIN (partial recovery)."""
    client = setup_teardown_env
    client.post("/sim/inject", json={"scenario": "db_pool_exhaustion"})

    inc = create_incident(
        incident_id=generate_incident_id(),
        alert="PostgreSQL connections maxed out",
        extracted_details={"service": "database-cluster"},
        diagnosis="Exhaustion",
        reflection="Restart clears connections temporarily",
        raw_recalled_memories=[],
        recommended_action={"type": "restart_service", "target_service": "database-cluster", "params": {}, "rationale": "partial relief"}
    )
    inc_id = inc["id"]

    client.post(f"/incidents/{inc_id}/approve", json={"approver": "SRE", "note": "Restart"})
    r_exec = client.post(f"/incidents/{inc_id}/execute")
    assert r_exec.status_code == 200
    res = r_exec.json()

    assert res["verification"]["status"] == "UNCERTAIN"
    # Criteria C2 (error_rate <= 0.01) must fail since error_rate is 0.02
    c2 = next(c for c in res["verification"]["criteria"] if c["id"] == "C2")
    assert c2["passed"] is False
    assert c2["observed"] == 0.02


def test_7_bad_deploy_restart_failure_and_retry(setup_teardown_env, monkeypatch):
    """7. bad_deploy + restart_service -> FAILURE, new cycle created, approval PENDING, previous failure passed to propose_action."""
    client = setup_teardown_env
    client.post("/sim/inject", json={"scenario": "bad_deploy"})

    captured_failures = []

    def mock_propose_action(alert_text, extracted_details, recalled_memories, reflection, previous_failures=None):
        if previous_failures:
            captured_failures.append(list(previous_failures))
            return {
                "type": "rollback_deployment",
                "target_service": "checkout-service",
                "params": {},
                "rationale": "Retrying with rollback after restart failed"
            }
        return {
            "type": "restart_service",
            "target_service": "checkout-service",
            "params": {},
            "rationale": "try restart"
        }

    monkeypatch.setattr(agent, "propose_action", mock_propose_action)

    inc = create_incident(
        incident_id=generate_incident_id(),
        alert="Bad deploy on checkout-service",
        extracted_details={"service": "checkout-service"},
        diagnosis="Bug",
        reflection="Reflect",
        raw_recalled_memories=[],
        recommended_action={"type": "restart_service", "target_service": "checkout-service", "params": {}, "rationale": "try restart"}
    )
    inc_id = inc["id"]

    client.post(f"/incidents/{inc_id}/approve", json={"approver": "SRE", "note": "restart"})
    r_exec = client.post(f"/incidents/{inc_id}/execute")
    assert r_exec.status_code == 200
    res = r_exec.json()

    # Must be FAILURE because error_rate is unchanged on checkout-service (0.12 >= 0.9 * 0.12)
    assert res["verification"]["status"] == "FAILURE"

    inc_after = client.get(f"/incidents/{inc_id}").json()
    assert inc_after["status"] == "FAILED_RETRYING"
    assert inc_after["cycle"] == 2
    assert inc_after["approval"]["state"] == "PENDING"
    assert len(inc_after["failed_attempts"]) == 1
    assert inc_after["failed_attempts"][0]["action"]["type"] == "restart_service"

    # Confirm previous failure was passed to propose_action
    assert len(captured_failures) == 1
    assert captured_failures[0][0]["action"]["type"] == "restart_service"
    # New recommendation awaiting approval
    assert inc_after["recommended_action"]["type"] == "rollback_deployment"


def test_8_three_failed_attempts_escalated(setup_teardown_env, monkeypatch):
    """8. three failed attempts -> ESCALATED."""
    client = setup_teardown_env
    client.post("/sim/inject", json={"scenario": "bad_deploy"})

    # Propose restart every time to force 3 consecutive failures
    monkeypatch.setattr(agent, "propose_action", lambda *args, **kwargs: {
        "type": "restart_service",
        "target_service": "checkout-service",
        "params": {},
        "rationale": "restart"
    })

    inc = create_incident(
        incident_id=generate_incident_id(),
        alert="Bad deploy causing persistent failures",
        extracted_details={"service": "checkout-service"},
        diagnosis="Bug",
        reflection="Reflect",
        raw_recalled_memories=[],
        recommended_action={"type": "restart_service", "target_service": "checkout-service", "params": {}, "rationale": "restart"}
    )
    inc_id = inc["id"]

    # Attempt 1: approve & execute -> FAILURE
    client.post(f"/incidents/{inc_id}/approve", json={"approver": "SRE1", "note": "try 1"})
    client.post(f"/incidents/{inc_id}/execute")
    inc1 = client.get(f"/incidents/{inc_id}").json()
    assert inc1["status"] == "FAILED_RETRYING"
    assert inc1["cycle"] == 2

    # Attempt 2: approve & execute -> FAILURE
    client.post(f"/incidents/{inc_id}/approve", json={"approver": "SRE2", "note": "try 2"})
    client.post(f"/incidents/{inc_id}/execute")
    inc2 = client.get(f"/incidents/{inc_id}").json()
    assert inc2["status"] == "FAILED_RETRYING"
    assert inc2["cycle"] == 3

    # Attempt 3: approve & execute -> ESCALATED
    client.post(f"/incidents/{inc_id}/approve", json={"approver": "SRE3", "note": "try 3"})
    client.post(f"/incidents/{inc_id}/execute")
    inc3 = client.get(f"/incidents/{inc_id}").json()

    assert inc3["status"] == "ESCALATED"
    assert len(inc3["executions"]) == 3
    assert len(inc3["failed_attempts"]) == 3

    # Check outcome endpoint
    r_out = client.get(f"/incidents/{inc_id}/outcome")
    assert r_out.status_code == 200
    outcome = r_out.json()
    assert outcome["final_status"] == "ESCALATED"
    assert outcome["total_attempts"] == 3


def test_9_invalid_action_type_rejected_by_engine(setup_teardown_env):
    """9. invalid action type -> rejected with recorded REJECTED_BY_ENGINE."""
    client = setup_teardown_env

    inc = create_incident(
        incident_id=generate_incident_id(),
        alert="Test invalid action alert",
        extracted_details={"service": "checkout-service"},
        diagnosis="Diag",
        reflection="Reflect",
        raw_recalled_memories=[],
        recommended_action={"type": "format_c_drive", "target_service": "checkout-service", "params": {}, "rationale": "malicious"}
    )
    inc_id = inc["id"]

    # Manually approve the action in the record to test the engine's defense-in-depth gate
    fp = compute_action_fingerprint("format_c_drive", "checkout-service", {})
    inc["action_fingerprint"] = fp
    inc["approval"] = {
        "state": ApprovalState.APPROVED.value,
        "approved_fingerprint": fp,
        "decided_by": "Rogue Approver",
        "decided_at": "2026-09-28T00:00:00Z",
        "note": "Forced"
    }
    inc["status"] = IncidentStatus.APPROVED.value
    update_incident(inc_id, inc)

    # Execute should be rejected by the execution engine without crashing
    r_exec = client.post(f"/incidents/{inc_id}/execute")
    assert r_exec.status_code == 200
    res = r_exec.json()
    assert res["execution"]["status"] == "REJECTED_BY_ENGINE"
    assert len(res["execution"]["errors"]) > 0
    assert "not allowlisted" in res["execution"]["errors"][0]
