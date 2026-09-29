from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def generate_incident_id() -> str:
    """Generate the canonical incident identifier used across Pipeline A and B."""
    return f"INC-{uuid.uuid4().hex[:8].upper()}"


class CanonicalIncident(BaseModel):
    """One canonical incident representation used across detection, investigation, approval, execution, and memory retention."""

    incident_id: str = Field(default_factory=generate_incident_id)
    correlation_id: str = Field(default="")
    source_type: str = Field(default="application_inspection")
    data_source: str = Field(default="real_telemetry")
    execution_target_type: str = Field(default="real_service")
    application_id: Optional[str] = None
    application_name: Optional[str] = None
    service_id: Optional[str] = None
    service_alias: Optional[str] = None
    status: str = Field(default="DETECTED")
    status_reason: Optional[str] = None
    detected_at: Optional[str] = None
    first_seen_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    severity: str = Field(default="medium")
    classification: Optional[str] = None
    alert_text: Optional[str] = None
    summary: Optional[str] = None
    symptoms: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    impact: str = ""
    evidence: Dict[str, Any] = Field(default_factory=dict)
    timeline: List[Dict[str, Any]] = Field(default_factory=list)
    root_cause: Optional[str] = None
    failure_summary: Optional[str] = None
    rationale: Optional[str] = None
    confidence: float = 0.0
    hindsight_query: Optional[str] = None
    hindsight_results: List[Dict[str, Any]] = Field(default_factory=list)
    matched_memory: Optional[Dict[str, Any]] = None
    memory_source: Optional[str] = None
    memory_status: str = Field(default="pending")
    recommendation: Optional[str] = None
    approved_action: Optional[Dict[str, Any]] = None
    approval_state: str = Field(default="PENDING")
    approval_decider: Optional[str] = None
    approval_note: Optional[str] = None
    action_fingerprint: Optional[str] = None
    execution: Optional[Dict[str, Any]] = None
    execution_history: List[Dict[str, Any]] = Field(default_factory=list)
    verification: Optional[Dict[str, Any]] = None
    verification_history: List[Dict[str, Any]] = Field(default_factory=list)
    retry_count: int = 0
    failed_attempts: List[Dict[str, Any]] = Field(default_factory=list)
    outcome: Optional[Dict[str, Any]] = None
    final_status: Optional[str] = None
    memory_retention: Optional[Dict[str, Any]] = None


def _to_plain_dict(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return [_to_plain_dict(item) for item in value]
    return value


def to_canonical_incident(
    incident: Any,
    evidence: Optional[Any] = None,
    investigation: Optional[Any] = None,
    hindsight: Optional[Any] = None,
    *,
    source_type: str = "application_inspection",
    data_source: str = "real_telemetry",
    execution_target_type: str = "real_service",
    memory_source: Optional[str] = None,
) -> CanonicalIncident:
    """Map a Pipeline A Incident + InvestigationReport into a canonical incident record."""

    incident_id = getattr(incident, "incident_id", None) or getattr(incident, "id", None) or generate_incident_id()
    now = datetime.now(timezone.utc).isoformat()
    analysis = getattr(investigation, "analysis", None) if investigation else None
    resolution = getattr(investigation, "resolution", None) if investigation else None
    hindsight_result = getattr(investigation, "hindsight", None) if investigation else None
    if hindsight is not None:
        hindsight_result = hindsight

    recommendation_text = getattr(resolution, "summary", None) if resolution else None
    if not recommendation_text and isinstance(incident, dict):
        recommendation_text = incident.get("recommendation")

    canonical = CanonicalIncident(
        incident_id=incident_id,
        correlation_id=incident_id,
        source_type=source_type,
        data_source=data_source,
        execution_target_type=execution_target_type,
        application_id=getattr(incident, "application_id", None) or (incident.get("application_id") if isinstance(incident, dict) else None),
        application_name=getattr(incident, "application_name", None),
        service_id=getattr(incident, "service", None) or (incident.get("service") if isinstance(incident, dict) else None),
        service_alias=getattr(incident, "service", None) or (incident.get("service") if isinstance(incident, dict) else None),
        status="DETECTED",
        status_reason="Detected via application inspection",
        detected_at=now,
        first_seen_at=now,
        created_at=now,
        updated_at=now,
        severity=str(getattr(incident, "severity", "medium") or "medium"),
        classification=getattr(incident, "incident_type", None) or (incident.get("incident_type") if isinstance(incident, dict) else None),
        alert_text=getattr(incident, "description", None) or (incident.get("description") if isinstance(incident, dict) else None),
        summary=getattr(incident, "description", None) or (incident.get("description") if isinstance(incident, dict) else None),
        symptoms=getattr(incident, "symptoms", []) or (incident.get("symptoms", []) if isinstance(incident, dict) else []),
        errors=getattr(incident, "errors", []) or (incident.get("errors", []) if isinstance(incident, dict) else []),
        impact=getattr(incident, "impact", "") or (incident.get("impact", "") if isinstance(incident, dict) else ""),
        evidence=_to_plain_dict(evidence) or {},
        timeline=_to_plain_dict(getattr(investigation, "timeline", []) if investigation else []) or [],
        root_cause=getattr(analysis, "root_cause", None) if analysis else None,
        failure_summary=getattr(analysis, "failure", None) if analysis else None,
        rationale=getattr(analysis, "why", None) if analysis else None,
        confidence=float(getattr(analysis, "confidence", 0.0) or 0.0) if analysis else 0.0,
        hindsight_query=getattr(hindsight_result, "query", None) if hindsight_result else None,
        hindsight_results=_to_plain_dict(getattr(hindsight_result, "similar_incidents", []) if hindsight_result else []) or [],
        matched_memory=_to_plain_dict(getattr(hindsight_result, "matched_memory", None) if hindsight_result else None),
        memory_source=memory_source or ("hindsight" if hindsight_result else "local_fallback"),
        memory_status="matched" if getattr(hindsight_result, "matched_memory", None) else ("available" if hasattr(hindsight_result, "similar_incidents") else "pending"),
        recommendation=recommendation_text,
        approval_state="PENDING",
    )

    if resolution and getattr(resolution, "actions", None):
        canonical.approved_action = {
            "type": "recommended_action",
            "target_service": getattr(incident, "service", None) or (incident.get("service") if isinstance(incident, dict) else None),
            "params": {},
            "rationale": recommendation_text,
        }

    return canonical
