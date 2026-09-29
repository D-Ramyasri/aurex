"""
Incident lifecycle store persisting to data/incidents.json.
Guarded by threading.Lock for thread safety across API and background operations.
"""

import os
import json
import hashlib
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from enum import Enum

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)
INCIDENTS_FILE = os.path.join(DATA_DIR, "incidents.json")

_incidents_lock = threading.Lock()
_incidents_store: Dict[str, Dict[str, Any]] = {}


class IncidentStatus(str, Enum):
    DIAGNOSED = "DIAGNOSED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    FAILED_RETRYING = "FAILED_RETRYING"
    ESCALATED = "ESCALATED"
    REJECTED = "REJECTED"


class ApprovalState(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


def compute_action_fingerprint(action_type: str, target_service: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Computes a deterministic sha256 fingerprint for an action."""
    params_dict = params or {}
    # Canonical JSON string of sorted params
    params_json = json.dumps(params_dict, sort_keys=True, separators=(",", ":"))
    raw_str = f"{action_type.strip()}:{target_service.strip()}:{params_json}"
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()


def _load_incidents_from_disk():
    global _incidents_store
    if os.path.exists(INCIDENTS_FILE):
        try:
            with open(INCIDENTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    _incidents_store = data
                elif isinstance(data, list):
                    _incidents_store = {inc["id"]: inc for inc in data if "id" in inc}
                for rec in _incidents_store.values():
                    if "user_id" not in rec or rec["user_id"] is None:
                        rec["user_id"] = 2
        except Exception as e:
            # If corrupted, start empty
            _incidents_store = {}


def _save_incidents_to_disk():
    try:
        with open(INCIDENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(_incidents_store, f, indent=2, default=str)
    except Exception as e:
        pass


# Initial load
_load_incidents_from_disk()


def create_incident(
    alert: str,
    extracted_details: Dict[str, Any],
    diagnosis: str,
    reflection: str,
    raw_recalled_memories: List[Dict[str, Any]],
    recommended_action: Optional[Dict[str, Any]] = None,
    memory_source: str = "local_fallback",
    user_id: Optional[int] = None,
    **kwargs: Any
) -> Dict[str, Any]:
    """Creates a canonical lifecycle record that preserves the legacy approval workflow schema."""
    incident_id = kwargs.get("incident_id") or kwargs.get("id")
    if incident_id is None or str(incident_id).strip() == "":
        raise ValueError(
            "Business incident_id is required. Use the canonical incident generator or pass the existing ID explicitly."
        )
    incident_id = str(incident_id)
    now_iso = datetime.now(timezone.utc).isoformat()
    target_user_id = user_id or kwargs.get("user_id") or 2

    action_fp = None
    if recommended_action and isinstance(recommended_action, dict):
        action_fp = compute_action_fingerprint(
            recommended_action.get("type", ""),
            recommended_action.get("target_service", ""),
            recommended_action.get("params", {})
        )

    record = {
        "id": incident_id,
        "incident_id": incident_id,
        "user_id": int(target_user_id),
        "correlation_id": kwargs.get("correlation_id") or incident_id,
        "source_type": kwargs.get("source_type", kwargs.get("source", "application_inspection")),
        "data_source": kwargs.get("data_source", "real_telemetry"),
        "execution_target_type": kwargs.get("execution_target_type", "real_service"),
        "created_at": now_iso,
        "updated_at": now_iso,
        "detected_at": kwargs.get("detected_at") or now_iso,
        "first_seen_at": kwargs.get("first_seen_at") or now_iso,
        "alert": alert,
        "alert_text": alert,
        "summary": kwargs.get("summary") or alert,
        "severity": kwargs.get("severity") or extracted_details.get("severity") or "medium",
        "classification": kwargs.get("classification") or kwargs.get("incident_type") or "application",
        "service_id": kwargs.get("service_id") or extracted_details.get("service") or kwargs.get("service"),
        "service_alias": kwargs.get("service_alias") or kwargs.get("service") or extracted_details.get("service"),
        "application_id": kwargs.get("application_id"),
        "application_name": kwargs.get("application_name"),
        "symptoms": kwargs.get("symptoms") or [alert],
        "errors": kwargs.get("errors") or [],
        "impact": kwargs.get("impact") or "",
        "extracted_details": extracted_details or {},
        "diagnosis": diagnosis or "",
        "reflection": reflection or "",
        "raw_recalled_memories": raw_recalled_memories or [],
        "hindsight_query": kwargs.get("hindsight_query"),
        "hindsight_results": kwargs.get("hindsight_results") or raw_recalled_memories or [],
        "matched_memory": kwargs.get("matched_memory"),
        "memory_source": memory_source,
        "memory_status": kwargs.get("memory_status") or ("matched" if kwargs.get("matched_memory") else memory_source),
        "recommendation": kwargs.get("recommendation") or (recommended_action.get("rationale") if isinstance(recommended_action, dict) else None),
        "recommended_action": recommended_action,
        "approved_action": kwargs.get("approved_action"),
        "action_fingerprint": action_fp,
        "approval": {
            "state": ApprovalState.PENDING.value,
            "approved_fingerprint": None,
            "decided_by": None,
            "decided_at": None,
            "note": None
        },
        "approval_state": ApprovalState.PENDING.value,
        "approval_decider": None,
        "approval_note": None,
        "cycle": 1,
        "executions": [],
        "execution_history": [],
        "verifications": [],
        "verification_history": [],
        "failed_attempts": [],
        "retry_count": 0,
        "status": IncidentStatus.AWAITING_APPROVAL.value,
        "status_reason": kwargs.get("status_reason") or "Recommendation generated",
        "outcome": None,
        "final_status": None,
        "incident_id_ref": kwargs.get("incident_id_ref"),
        "source": kwargs.get("source", "manual_alert"),
        "evidence": kwargs.get("evidence"),
        "timeline": kwargs.get("timeline") or [],
        "root_cause": kwargs.get("root_cause"),
        "failure_summary": kwargs.get("failure_summary"),
        "rationale": kwargs.get("rationale") or reflection,
        "confidence": kwargs.get("confidence"),
        "rca": kwargs.get("rca"),
        "resolution_plan": kwargs.get("resolution_plan"),
        "reflection_patterns": kwargs.get("reflection_patterns"),
        "recalled_memories": kwargs.get("recalled_memories"),
        "recommended_action_plan": kwargs.get("recommended_action_plan"),
    }

    with _incidents_lock:
        _incidents_store[incident_id] = record
        _save_incidents_to_disk()

    return record


def get_incident(incident_id: str, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Retrieves an incident by its canonical ID scoped to the authenticated user."""
    with _incidents_lock:
        incident = _incidents_store.get(incident_id)
        if incident is None:
            for record in _incidents_store.values():
                if record.get("incident_id") == incident_id or record.get("id") == incident_id:
                    incident = record
                    break
        if incident is not None:
            inc_user = incident.get("user_id")
            if inc_user is None:
                inc_user = 2
            if user_id is not None and int(inc_user) != int(user_id):
                return None
            return dict(incident)
        return None


def list_incidents(user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Lists all incidents ordered by creation time descending, scoped to user_id."""
    with _incidents_lock:
        incidents = list(_incidents_store.values())
        if user_id is not None:
            incidents = [
                inc for inc in incidents
                if int(inc.get("user_id", 2) or 2) == int(user_id)
            ]
        incidents.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return [dict(inc) for inc in incidents]


def update_incident(incident_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Applies updates to an incident and persists to disk."""
    with _incidents_lock:
        if incident_id in _incidents_store:
            active = _incidents_store[incident_id]
        else:
            active = None
            for key, record in _incidents_store.items():
                if record.get("incident_id") == incident_id or record.get("id") == incident_id:
                    active = record
                    incident_id = key
                    break
        if not active:
            return None
        active.update(updates)
        active.setdefault("id", active.get("incident_id") or incident_id)
        active.setdefault("incident_id", active.get("id") or incident_id)
        if active.get("approval") is not None:
            active["approval_state"] = active["approval"].get("state")
        active["updated_at"] = datetime.now(timezone.utc).isoformat()
        _incidents_store[incident_id] = active
        _save_incidents_to_disk()
        return dict(active)


def get_active_incident_for_app(application_id: str, service_id: Optional[str] = None, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Retrieves the most recent genuinely active/unresolved incident for a specific application and user."""
    active_statuses = {
        IncidentStatus.DIAGNOSED.value,
        IncidentStatus.AWAITING_APPROVAL.value,
        IncidentStatus.APPROVED.value,
        IncidentStatus.EXECUTING.value,
        IncidentStatus.VERIFYING.value,
        IncidentStatus.FAILED_RETRYING.value,
    }
    with _incidents_lock:
        incidents = list(_incidents_store.values())
        incidents.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        for inc in incidents:
            if user_id is not None:
                inc_user = inc.get("user_id", 2) or 2
                if int(inc_user) != int(user_id):
                    continue
            inc_app = inc.get("application_id")
            inc_svc = inc.get("service_id") or inc.get("service")
            if (inc_app == application_id) or (service_id and inc_svc == service_id):
                if inc.get("status") in active_statuses:
                    return dict(inc)
    return None


def auto_resolve_stale_incidents(
    application_id: str, service_id: Optional[str] = None, reason: str = "Live telemetry confirmed healthy status", user_id: Optional[int] = None
) -> List[str]:
    """
    Marks prior pending approval incidents as auto-resolved when the live target service
    has returned to a verified healthy state before any remediation was executed.
    Preserves full audit history in the database while clearing stale approval gates.
    """
    unresolved_statuses = {
        IncidentStatus.DIAGNOSED.value,
        IncidentStatus.AWAITING_APPROVAL.value,
        IncidentStatus.APPROVED.value,
        IncidentStatus.EXECUTING.value,
        IncidentStatus.VERIFYING.value,
        IncidentStatus.FAILED_RETRYING.value,
    }
    stale_ids = []
    now_iso = datetime.now(timezone.utc).isoformat()
    with _incidents_lock:
        for k, inc in _incidents_store.items():
            if user_id is not None:
                inc_user = inc.get("user_id", 2) or 2
                if int(inc_user) != int(user_id):
                    continue
            inc_app = inc.get("application_id")
            inc_svc = inc.get("service_id") or inc.get("service")
            if (inc_app == application_id) or (service_id and inc_svc == service_id):
                # If it's in any unresolved state and service is now healthy, it's stale
                if inc.get("status") in unresolved_statuses:
                    inc["status"] = IncidentStatus.RESOLVED.value
                    inc["status_reason"] = f"Auto-resolved: {reason}"
                    inc["updated_at"] = now_iso
                    appr = inc.get("approval") or {}
                    if appr.get("state") in (ApprovalState.PENDING.value, None):
                        inc["approval"] = {
                            "state": "SUPERSEDED_HEALTHY",
                            "approved_fingerprint": None,
                            "decided_by": "system-auto-health-check",
                            "decided_at": now_iso,
                            "note": reason
                        }
                        inc["approval_state"] = "SUPERSEDED_HEALTHY"
                    inc["outcome"] = {
                        "final_status": "RESOLVED",
                        "resolved_at": now_iso,
                        "reason": reason,
                        "resolved_by": "live_telemetry_verification"
                    }
                    stale_ids.append(inc.get("incident_id") or k)
        if stale_ids:
            _save_incidents_to_disk()
    return stale_ids


def clear_all_incidents():
    """Testing helper to reset incident store."""
    global _incidents_store
    with _incidents_lock:
        _incidents_store = {}
        _save_incidents_to_disk()

