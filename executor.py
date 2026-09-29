"""
Execution Engine for Incident Response Agent.
Enforces backend approval checks, captures telemetry evidence, applies allowlisted actions
against the simulated environment, triggers automatic verification, and drives the failure recovery loop.
"""

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from fastapi import HTTPException, status

import agent
from agent import ALLOWED_ACTIONS, ALLOWED_SERVICES
from incidents import (
    ApprovalState,
    IncidentStatus,
    compute_action_fingerprint,
    get_incident,
    update_incident
)
from abc import ABC, abstractmethod
import requests

from verifier import collect_evidence, collect_real_telemetry, sim_http_call, verify


class ExecutionTarget(ABC):
    @abstractmethod
    def execute(
        self,
        action: Dict[str, Any],
        incident: Dict[str, Any],
        app_config: Optional[Any] = None
    ) -> Dict[str, Any]:
        """Execute an action against the target and return result metadata."""
        pass


class RealExecutionTarget(ExecutionTarget):
    """
    Real execution target for production operations.
    Communicates only with real registered application endpoints.
    Fails closed when an action has no configured authorized real execution capability.
    """
    def execute(
        self,
        action: Dict[str, Any],
        incident: Dict[str, Any],
        app_config: Optional[Any] = None
    ) -> Dict[str, Any]:
        t0 = time.time()
        action_type = action.get("type")
        target_service = action.get("target_service")
        params = action.get("params") or {}

        if action_type not in ALLOWED_ACTIONS:
            return {
                "status": "REJECTED_BY_ENGINE",
                "errors": [f"Action '{action_type}' is not allowlisted: {sorted(ALLOWED_ACTIONS)}"],
                "output": {},
                "duration_ms": 0
            }

        if not app_config:
            from app_registry import get_application, list_applications
            app_id = incident.get("application_id")
            if app_id:
                app_config = get_application(app_id)
            if not app_config:
                svc = target_service or incident.get("service")
                for a in list_applications():
                    if a.service_id == svc or a.application_id == svc:
                        app_config = a
                        break

        remediation_url = getattr(app_config, "remediation_endpoint", None) or "/remediation"
        base_url = getattr(app_config, "base_url", None)
        if base_url:
            full_url = f"{base_url.rstrip('/')}/{remediation_url.lstrip('/')}"
            try:
                resp = requests.post(full_url, json={"action": action_type, "params": params, "target_service": target_service}, timeout=10.0)
                dur = int((time.time() - t0) * 1000)
                if resp.status_code == 200:
                    resp_data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"text": resp.text}
                    return {
                        "status": "COMPLETED",
                        "output": resp_data,
                        "errors": [],
                        "duration_ms": dur
                    }
                else:
                    return {
                        "status": "FAILED",
                        "output": {},
                        "errors": [f"Remediation endpoint returned HTTP {resp.status_code}: {resp.text}"],
                        "duration_ms": dur
                    }
            except Exception as e:
                return {
                    "status": "FAILED",
                    "output": {},
                    "errors": [f"Failed to communicate with real remediation endpoint: {str(e)}"],
                    "duration_ms": int((time.time() - t0) * 1000)
                }

        # Fail closed: No genuine remediation API configured on test application
        app_name = (
            getattr(app_config, "application_name", None)
            or incident.get("application_name")
            or target_service
        )
        dur = int((time.time() - t0) * 1000)
        return {
            "status": "UNAVAILABLE",
            "output": {
                "message": "Real execution capability not configured",
                "application": app_name,
                "action": action_type,
                "target_service": target_service
            },
            "errors": [
                f"Action unavailable for this application: Real execution capability not configured"
            ],
            "duration_ms": dur
        }


class SimulatorExecutionTarget(ExecutionTarget):
    """
    Test-only execution target for automated integration testing in tests/test_workflow.py.
    Never selected in user-facing production runtime.
    """
    def execute(
        self,
        action: Dict[str, Any],
        incident: Dict[str, Any],
        app_config: Optional[Any] = None
    ) -> Dict[str, Any]:
        action_type = action.get("type")
        target_service = action.get("target_service")
        params = action.get("params") or {}
        t0 = time.time()
        errors = []
        output = {}

        if action_type not in ALLOWED_ACTIONS:
            return {
                "status": "REJECTED_BY_ENGINE",
                "errors": [f"Action '{action_type}' is not allowlisted: {sorted(ALLOWED_ACTIONS)}"],
                "output": {},
                "duration_ms": 0
            }
        if target_service not in ALLOWED_SERVICES:
            return {
                "status": "REJECTED_BY_ENGINE",
                "errors": [f"Target service '{target_service}' is not allowlisted: {sorted(ALLOWED_SERVICES)}"],
                "output": {},
                "duration_ms": 0
            }

        try:
            resp = sim_http_call(
                "POST",
                f"/sim/{target_service}/actions",
                json_data={"type": action_type, "params": params},
                timeout=5.0
            )
            duration_ms = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                output = resp.json()
                status_val = "COMPLETED"
            elif resp.status_code == 422:
                status_val = "REJECTED_BY_ENGINE"
                errors.append(f"Simulation rejected action parameters: {resp.text}")
            else:
                status_val = "FAILED"
                errors.append(f"Simulation returned HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            duration_ms = int((time.time() - t0) * 1000)
            status_val = "FAILED"
            errors.append(f"Failed to communicate with simulated service: {str(e)}")

        return {
            "status": status_val,
            "output": output,
            "errors": errors,
            "duration_ms": duration_ms
        }


def get_execution_target(incident: Dict[str, Any]) -> ExecutionTarget:
    """
    Returns the execution target.
    Default user-facing runtime is ALWAYS RealExecutionTarget.
    SimulatorExecutionTarget is selected ONLY when explicitly configured
    via EXECUTION_TARGET=simulator or incident['execution_target_type'] == 'simulator'.
    """
    env_target = os.getenv("EXECUTION_TARGET", "").lower().strip()
    inc_target = str(incident.get("execution_target_type", "")).lower().strip()
    if env_target == "simulator" or inc_target == "simulator":
        return SimulatorExecutionTarget()
    return RealExecutionTarget()

    if target_type == "simulator":
        return SimulatorExecutionTarget()
    return RealExecutionTarget()


def build_incident_outcome(incident: Dict[str, Any]) -> Dict[str, Any]:
    """Builds structured resolution outcome for the incident."""
    created_at_str = incident.get("created_at")
    try:
        created_at_dt = datetime.fromisoformat(created_at_str)
        now_dt = datetime.now(timezone.utc)
        ttr = round((now_dt - created_at_dt).total_seconds(), 2)
    except Exception:
        ttr = 0.0

    actions_attempted = []
    for idx, ex in enumerate(incident.get("executions", [])):
        ver = incident.get("verifications", [])[idx] if idx < len(incident.get("verifications", [])) else {}
        actions_attempted.append({
            "cycle": idx + 1,
            "action": ex.get("action_type"),
            "target": ex.get("target_service"),
            "params": ex.get("params"),
            "approved_by": ex.get("approved_by"),
            "execution_status": ex.get("status"),
            "verification_status": ver.get("status", "UNKNOWN")
        })

    before_evidence = (
        incident["verifications"][0].get("before")
        if incident.get("verifications")
        else None
    )
    after_evidence = (
        incident["verifications"][-1].get("after")
        if incident.get("verifications")
        else None
    )

    final_status = incident.get("status", "IN_PROGRESS")
    if final_status not in (IncidentStatus.RESOLVED.value, IncidentStatus.ESCALATED.value):
        final_status = "IN_PROGRESS"

    return {
        "incident_id": incident.get("id"),
        "alert": incident.get("alert"),
        "final_status": final_status,
        "total_attempts": len(incident.get("executions", [])),
        "actions_attempted": actions_attempted,
        "execution_results": incident.get("executions", []),
        "verification_results": incident.get("verifications", []),
        "before_after": {
            "before": before_evidence,
            "after": after_evidence
        },
        "time_to_resolution_seconds": ttr,
        "memory_updated": bool((incident.get("outcome") or {}).get("memory_updated", False))
    }


def execute_incident(incident_id: str, user_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Executes approved action for an incident with defense-in-depth approval checks.
    Selects RealExecutionTarget by default, captures telemetry evidence,
    dispatches execution, runs automatic verification, and manages recovery loop.
    """
    incident = get_incident(incident_id, user_id=user_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")

    approval = incident.get("approval", {})
    action = incident.get("recommended_action") or {}
    action_type = action.get("type")
    target_service = action.get("target_service")
    params = action.get("params") or {}
    action_fp = incident.get("action_fingerprint")

    # Defense-in-depth checks
    if approval.get("state") != ApprovalState.APPROVED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot execute incident in approval state '{approval.get('state')}'. Approval must be APPROVED."
        )

    if approval.get("approved_fingerprint") != action_fp:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approval fingerprint does not match the current action fingerprint. Action was changed."
        )

    if incident.get("status") != IncidentStatus.APPROVED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Incident status is '{incident.get('status')}'. This approval has already been executed."
        )

    # Transition to EXECUTING
    incident["status"] = IncidentStatus.EXECUTING.value
    update_incident(incident_id, incident)

    target = get_execution_target(incident)
    started_at = datetime.now(timezone.utc).isoformat()

    # Resolve application configuration from registry
    from app_registry import get_application, list_applications
    inc_user_id = incident.get("user_id", user_id)
    app_id = incident.get("application_id")
    app_config = get_application(app_id, user_id=inc_user_id) if app_id else None
    if not app_config:
        svc = target_service or incident.get("service")
        for a in list_applications(user_id=inc_user_id):
            if a.service_id == svc or a.application_id == svc:
                app_config = a
                break

    if isinstance(target, SimulatorExecutionTarget):
        # Simulator path for automated tests
        try:
            services_resp = sim_http_call("GET", "/sim/services", timeout=5.0)
            sim_services = services_resp.json() if services_resp.status_code == 200 else {}
        except Exception:
            sim_services = {}

        primary_service = target_service
        primary_fault = None
        if isinstance(sim_services, dict) and sim_services:
            scored_services = []
            for service_name, state in sim_services.items():
                if not isinstance(state, dict):
                    continue
                score = float(state.get("error_rate", 0.0))
                if state.get("active_fault"):
                    score += 1.0
                if state.get("status") in {"degraded", "down"}:
                    score += 0.25
                scored_services.append((service_name, score, state))
            if scored_services:
                candidate_service, candidate_score, candidate_state = max(scored_services, key=lambda item: item[1])
                if candidate_state.get("active_fault") or candidate_state.get("status") in {"degraded", "down"} or candidate_score > 0.001:
                    primary_service = candidate_service
                    primary_fault = candidate_state.get("active_fault")

        incident["primary_service"] = primary_service
        incident["primary_fault"] = primary_fault
        incident["faulted_services"] = [
            name for name, state in (sim_services or {}).items()
            if isinstance(state, dict) and (state.get("active_fault") or state.get("status") in {"degraded", "down"})
        ]
        update_incident(incident_id, incident)
        before_evidence = collect_evidence(primary_service)
    else:
        # Real Execution Target: Capture real before telemetry
        before_evidence = collect_real_telemetry(app_config)

    # Execute action via target
    exec_res = target.execute(action, incident, app_config)
    ended_at = datetime.now(timezone.utc).isoformat()

    execution_result = {
        "execution_id": str(uuid.uuid4()),
        "action_type": action_type,
        "target_service": target_service,
        "params": params,
        "approved_by": approval.get("decided_by", "unknown"),
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": exec_res.get("duration_ms", 0),
        "output": exec_res.get("output", {}),
        "errors": exec_res.get("errors", []),
        "status": exec_res.get("status", "FAILED")
    }
    incident["executions"].append(execution_result)

    # Transition to VERIFYING and run automatic verification
    incident["status"] = IncidentStatus.VERIFYING.value
    update_incident(incident_id, incident)

    verification_result = verify(incident, before_evidence, execution_start_iso=started_at)
    incident["verifications"].append(verification_result)

    # Handle Decision: SUCCESS, FAILURE, or UNCERTAIN
    ver_status = verification_result["status"]

    if ver_status == "SUCCESS":
        incident["status"] = IncidentStatus.RESOLVED.value
        outcome = build_incident_outcome(incident)
        incident["outcome"] = outcome
        mem_ok = agent.retain_outcome(incident)
        incident["outcome"]["memory_updated"] = mem_ok
    else:
        # Failure or Uncertain: DO NOT mark RESOLVED
        cycle = incident.get("cycle", 1)
        failed_attempt = {
            "cycle": cycle,
            "action": action,
            "before": before_evidence,
            "after": verification_result.get("after"),
            "criteria_failed": [
                c["id"] for c in verification_result.get("criteria", []) if not c.get("passed")
            ],
            "reasons": verification_result.get("reasons", [])
        }
        incident["failed_attempts"].append(failed_attempt)

        if len(incident.get("executions", [])) >= 3:
            incident["status"] = IncidentStatus.ESCALATED.value
            outcome = build_incident_outcome(incident)
            incident["outcome"] = outcome
            mem_ok = agent.retain_outcome(incident)
            incident["outcome"]["memory_updated"] = mem_ok
        else:
            incident["status"] = IncidentStatus.FAILED_RETRYING.value
            incident["cycle"] = cycle + 1
            incident["approval"] = {
                "state": ApprovalState.PENDING.value,
                "approved_fingerprint": None,
                "decided_by": None,
                "decided_at": None,
                "note": None
            }
            new_action = agent.propose_action(
                incident.get("alert", ""),
                incident.get("extracted_details", {}),
                incident.get("raw_recalled_memories", []),
                incident.get("reflection", ""),
                incident.get("failed_attempts")
            )
            incident["recommended_action"] = new_action
            if new_action:
                incident["action_fingerprint"] = compute_action_fingerprint(
                    new_action.get("type", ""),
                    new_action.get("target_service", ""),
                    new_action.get("params", {})
                )

    updated = update_incident(incident_id, incident)
    return {
        "execution": execution_result,
        "verification": verification_result,
        "incident": updated
    }
