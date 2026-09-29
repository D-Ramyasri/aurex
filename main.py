"""
FastAPI Backend for Incident Response Memory Agent.
Provides REST endpoints:
- GET /api/applications: List all registered microservices.
- GET /api/applications/{id}: Retrieve specific microservice config.
- POST /api/applications: Register a new microservice configuration.
- DELETE /api/applications/{id}: Remove a microservice configuration.
- POST /api/incidents/inspect: Deterministically inspects an application's configured signals.
- POST /diagnose: Analyzes alert, retrieves past incidents from Hindsight, synthesizes diagnosis via Groq.
- POST /resolve: Saves newly resolved incident into Hindsight memory.
- GET /health: Health check and status.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, status, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent import (
    diagnose_incident,
    resolve_incident,
    propose_action,
    extract_incident_details,
    recall_memories_from_hindsight,
    reflect_on_incident,
    call_groq_llm,
    ALLOWED_ACTIONS,
    ALLOWED_SERVICES,
    get_allowed_services,
    BANK_ID,
    GROQ_MODEL
)
from app_registry import (
    list_applications,
    get_application,
    register_application,
    delete_application,
)
from evidence_collector import collect_evidence
from incident_detector import detect_incident
from incident_investigator import investigate_incident
from canonical_incident import to_canonical_incident
from models import (
    ApplicationConfig,
    InspectRequest,
    InspectResponse,
    RegisterApplicationResponse,
)
from incidents import (
    create_incident,
    get_incident,
    list_incidents,
    update_incident,
    compute_action_fingerprint,
    IncidentStatus,
    ApprovalState,
    get_active_incident_for_app,
    auto_resolve_stale_incidents
)
from executor import execute_incident, build_incident_outcome

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("incident_api")

app = FastAPI(
    title="Incident Response Memory Agent API",
    description="Backend powered by Hindsight semantic memory and Groq LLM (gpt-oss-120b) to diagnose production incidents.",
    version="1.0.0"
)

# CORS middleware allowing all origins for local demo purposes
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from auth import auth_router, decode_access_token
app.include_router(auth_router)


def get_request_user_id(authorization: Optional[str] = Header(None)) -> Optional[int]:
    """
    Extracts authenticated user_id from Authorization: Bearer <token> header.
    If header is present:
      - decodes JWT and returns user_id (int).
      - if token is invalid or expired, raises 401.
    If header is omitted (e.g. internal test scripts, health checks):
      - returns 2 (Ramya) as fallback to preserve existing demo pipeline compatibility.
    """
    if not authorization:
        return 2
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected 'Bearer <token>'."
        )
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token."
        )
    try:
        return int(payload["sub"])
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user subject in token."
        )

if os.getenv("ENABLE_SIMULATOR", "1").strip().lower() in {"1", "true", "yes", "on"}:
    from sim_env import sim_router

    app.include_router(sim_router)


class DiagnoseRequest(BaseModel):
    alert: str = Field(..., description="Incoming error log or alert description to diagnose", min_length=1)
    incident_id: Optional[str] = Field(None, description="Existing canonical business incident ID to update; omit to create a new one")


class ResolveRequest(BaseModel):
    alert: str = Field(..., description="Original alert or symptom description", min_length=1)
    root_cause: str = Field(..., description="Diagnosed root cause", min_length=1)
    fix: str = Field(..., description="Remediation or fix applied", min_length=1)
    service: Optional[str] = Field("general", description="Affected microservice name")
    resolution_time_minutes: Optional[int] = Field(15, description="Time taken to resolve in minutes")


@app.get("/")
@app.get("/health")
def health_check():
    """Health check endpoint confirming API service status."""
    return {
        "status": "healthy",
        "service": "Incident Response Memory Agent",
        "memory_bank": BANK_ID,
        "llm_model": GROQ_MODEL,
        "endpoints": {
            "applications": "GET /api/applications",
            "register_application": "POST /api/applications",
            "inspect": "POST /api/incidents/inspect",
            "diagnose": "POST /diagnose",
            "resolve": "POST /resolve",
            "docs": "/docs"
        }
    }


@app.get("/api/applications", response_model=List[ApplicationConfig])
def get_applications_endpoint(authorization: Optional[str] = Header(None)):
    """
    Returns the list of registered applications/services for the authenticated user.
    """
    user_id = get_request_user_id(authorization)
    return list_applications(user_id=user_id)


@app.get("/api/applications/{application_id}", response_model=ApplicationConfig)
def get_single_application_endpoint(application_id: str, authorization: Optional[str] = Header(None)):
    """
    Returns configuration for a specific application.
    """
    user_id = get_request_user_id(authorization)
    app_config = get_application(application_id, user_id=user_id)
    if not app_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application with ID '{application_id}' is not registered."
        )
    return app_config


@app.post("/api/applications", response_model=RegisterApplicationResponse, status_code=status.HTTP_201_CREATED)
@app.post("/api/applications/register", response_model=RegisterApplicationResponse, status_code=status.HTTP_201_CREATED)
def register_application_endpoint(config: ApplicationConfig, authorization: Optional[str] = Header(None)):
    """
    Registers or updates an application configuration and persists it to storage.
    Accepts requests at both POST /api/applications and POST /api/applications/register.
    """
    user_id = get_request_user_id(authorization)
    logger.info("Registering/Updating application: %s (%s) for user_id=%s", config.application_name, config.application_id, user_id)
    saved = register_application(config, user_id=user_id)
    return RegisterApplicationResponse(
        success=True,
        message=f"Application '{saved.application_name}' registered successfully",
        application=saved
    )


@app.delete("/api/applications/{application_id}")
def delete_application_endpoint(application_id: str, authorization: Optional[str] = Header(None)):
    """
    Deletes an application from the registry and store.
    """
    user_id = get_request_user_id(authorization)
    success = delete_application(application_id, user_id=user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application with ID '{application_id}' not found."
        )
    return {"status": "success", "message": f"Application '{application_id}' deleted."}


@app.post("/api/incidents/inspect", response_model=InspectResponse)
def inspect_application_endpoint(payload: InspectRequest, authorization: Optional[str] = Header(None)):
    """
    Inspects a registered application using ITS OWN specific configuration:
    1. Validates and loads the specific application configuration by ID.
    2. Probes ITS configured HTTP health endpoint (base_url + health_endpoint).
    3. Reads and parses recent logs from the application's HTTP log endpoint.
    4. Aggregates signals into a structured Evidence object.
    5. Deterministically evaluates signals to detect incident conditions.
    6. Constructs a structured Incident object if unhealthy or returns healthy status.
    """
    user_id = get_request_user_id(authorization)
    if payload.user_id is not None:
        user_id = payload.user_id
    logger.info("Received inspection request for application: %s (user_id=%s)", payload.application_id, user_id)
    app_config = get_application(payload.application_id, user_id=user_id)
    if not app_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{payload.application_id}' is not registered."
        )

    # Collect real signals for THIS specific application
    evidence = collect_evidence(app_config)

    # Deterministic incident detection
    incident_detected, reason, incident = detect_incident(evidence, app_config)

    # CASE 1: HEALTHY APPLICATION STATE (No incident detected)
    if not incident_detected:
        # Auto-resolve any prior pending stale incidents for this service
        auto_resolve_stale_incidents(app_config.application_id, app_config.service_id, user_id=user_id)

        # Build nominal investigation report without creating incident, RCA, recommendation, or approval gate
        investigation = investigate_incident(evidence, incident=None, app_config=app_config)

        res_status = "error" if (evidence.health_check.status in ["UNREACHABLE", "ERROR"]) else "success"

        return InspectResponse(
            status=res_status,
            application_id=app_config.application_id,
            application_name=app_config.application_name,
            incident_detected=False,
            reason=reason,
            incident=None,
            evidence=evidence,
            investigation=investigation,
            timeline=investigation.timeline,
            analysis=None,
            resolution=None,
            hindsight=None
        )

    # CASE 2: REAL INCIDENT DETECTED
    # Core Investigation & Root Cause Analysis Pipeline
    investigation = investigate_incident(evidence, incident, app_config)

    canonical = to_canonical_incident(
        incident,
        evidence=evidence,
        investigation=investigation,
        source_type="application_inspection",
        data_source="real_telemetry",
        execution_target_type="real_service",
        memory_source="hindsight" if investigation.hindsight and investigation.hindsight.matched_memory else "local_fallback",
    )
    canonical_incident_id = canonical.incident_id
    if incident and getattr(incident, "incident_id", None):
        incident.incident_id = canonical_incident_id
        if hasattr(incident, "service"):
            incident.service = app_config.service_id

    persist_record = create_incident(
        alert=incident.description if incident else app_config.application_name,
        extracted_details={"service": app_config.service_id, "severity": incident.severity if incident else "medium"},
        diagnosis=investigation.analysis.failure if investigation and investigation.analysis else "investigation complete",
        reflection=investigation.reflection or "",
        raw_recalled_memories=(investigation.hindsight.similar_incidents if investigation and investigation.hindsight else []),
        recommended_action=(
            propose_action(
                incident.description if incident else app_config.application_name,
                {"service": app_config.service_id, "severity": incident.severity if incident else "medium"},
                investigation.hindsight.similar_incidents if investigation and investigation.hindsight else [],
                investigation.reflection or "",
                application_config=app_config
            )
        ) or {
            "type": "restart_service",
            "target_service": app_config.service_id,
            "params": {},
            "rationale": investigation.resolution.summary if investigation and investigation.resolution else "Allowlisted remediation"
        },
        memory_source="hindsight" if investigation and investigation.hindsight and investigation.hindsight.matched_memory else "local_fallback",
        incident_id=canonical_incident_id,
        application_id=app_config.application_id,
        application_name=app_config.application_name,
        service_id=app_config.service_id,
        service_alias=app_config.service_id,
        summary=incident.description if incident else app_config.application_name,
        severity=incident.severity if incident else "medium",
        classification=incident.incident_type if incident else "application",
        symptoms=incident.symptoms if incident else [],
        errors=incident.errors if incident else [],
        impact=incident.impact if incident else "",
        evidence=evidence.model_dump() if hasattr(evidence, "model_dump") else evidence,
        timeline=[event.model_dump(exclude_none=True) if hasattr(event, "model_dump") else event for event in investigation.timeline],
        root_cause=investigation.analysis.root_cause if investigation and investigation.analysis else None,
        failure_summary=investigation.analysis.failure if investigation and investigation.analysis else None,
        rationale=investigation.analysis.why if investigation and investigation.analysis else None,
        confidence=float(investigation.analysis.confidence) if investigation and investigation.analysis else 0.0,
        hindsight_query=investigation.hindsight.query if investigation and investigation.hindsight else None,
        hindsight_results=(investigation.hindsight.similar_incidents if investigation and investigation.hindsight else []),
        matched_memory=(investigation.hindsight.matched_memory if investigation and investigation.hindsight else None),
        recommendation=(investigation.resolution.summary if investigation and investigation.resolution else None),
        source_type="application_inspection",
        status_reason="Detected and investigated",
        user_id=user_id,
    )
    persist_record["incident_id"] = canonical_incident_id
    persist_record["id"] = canonical_incident_id
    update_incident(canonical_incident_id, persist_record)

    res_status = "error" if (evidence.health_check.status in ["UNREACHABLE", "ERROR"]) else "warning"

    return InspectResponse(
        status=res_status,
        application_id=app_config.application_id,
        application_name=app_config.application_name,
        incident_detected=True,
        reason=reason,
        incident=incident,
        evidence=evidence,
        investigation=investigation,
        timeline=investigation.timeline,
        analysis=investigation.analysis,
        resolution=investigation.resolution,
        hindsight=investigation.hindsight
    )


@app.get("/api/incidents/active/{application_id}")
def get_active_incident_endpoint(application_id: str, authorization: Optional[str] = Header(None)):
    """Retrieves current genuinely active/unresolved incident for a registered application and user."""
    user_id = get_request_user_id(authorization)
    app_config = get_application(application_id, user_id=user_id)
    svc_id = app_config.service_id if app_config else None
    active_inc = get_active_incident_for_app(application_id, service_id=svc_id, user_id=user_id)
    return {"application_id": application_id, "active_incident": active_inc}



@app.post("/diagnose")
def diagnose_endpoint(payload: DiagnoseRequest):
    """
    Diagnoses an incident:
    1. Extracts structured details (service, severity, symptom_type, summary) via Groq
    2. Recalls similar past incidents from Hindsight memory bank
    3. Fetches reflection on past patterns via Hindsight reflect()
    4. Builds LLM prompt with context and reflection
    5. Returns diagnosis, extracted_details, reflection, and raw recalled memories
    """
    logger.info("Received /diagnose request for alert: %s", payload.alert[:100])
    try:
        result = diagnose_incident(alert_text=payload.alert, incident_id=payload.incident_id)
        return result
    except Exception as e:
        logger.exception("Unexpected error in /diagnose: %s", e)
        return {
            "status": "error",
            "message": f"Diagnosis failed: {str(e)}",
            "alert": payload.alert,
            "extracted_details": {
                "service": "unknown",
                "severity": "medium",
                "symptom_type": "unknown",
                "summary": payload.alert[:100]
            },
            "reflection": "Reflection unavailable — proceeding with recall-based reasoning only.",
            "diagnosis": "Unable to complete diagnosis due to backend processing error.",
            "raw_recalled_memories": [],
            "incident_id": None,
            "recommended_action": None,
            "memory_source": "local_fallback"
        }


@app.post("/resolve")
def resolve_endpoint(payload: ResolveRequest, authorization: Optional[str] = Header(None)):
    """
    Records an incident resolution into Hindsight memory bank.
    Enables future similar alerts to recall this fix.
    """
    user_id = get_request_user_id(authorization)
    logger.info("Received /resolve request for service '%s' (user_id=%s)", payload.service, user_id)
    try:
        result = resolve_incident(
            alert_text=payload.alert,
            root_cause=payload.root_cause,
            fix=payload.fix,
            service=payload.service or "general",
            resolution_time_minutes=payload.resolution_time_minutes or 15,
            user_id=user_id
        )
        return result
    except Exception as e:
        logger.exception("Unexpected error in /resolve: %s", e)
        return {
            "status": "error",
            "message": f"Resolution retention failed: {str(e)}",
            "bank_id": BANK_ID
        }


# --- Phase 3: Incident Lifecycle & Approval Endpoints ---

class ApproveRequest(BaseModel):
    approver: str = Field(..., description="Name or identifier of the approving engineer", min_length=1)
    note: Optional[str] = Field("", description="Optional approval notes")
    action: Optional[Dict[str, Any]] = Field(None, description="Optional action override: {type, target_service, params}")


class RejectRequest(BaseModel):
    approver: str = Field(..., description="Name or identifier of the rejecting engineer", min_length=1)
    note: Optional[str] = Field("", description="Reason for rejection")


class RediagnoseRequest(BaseModel):
    correction: str = Field(..., description="Correction or additional domain context from engineer", min_length=1)


@app.get("/incidents")
def list_incidents_endpoint(authorization: Optional[str] = Header(None)):
    """Lists all incidents from the lifecycle store scoped to authenticated user."""
    user_id = get_request_user_id(authorization)
    return list_incidents(user_id=user_id)


@app.get("/incidents/{incident_id}")
def get_incident_endpoint(incident_id: str, authorization: Optional[str] = Header(None)):
    """Retrieves a single incident by ID scoped to authenticated user."""
    user_id = get_request_user_id(authorization)
    inc = get_incident(incident_id, user_id=user_id)
    if not inc:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")
    return inc


@app.post("/incidents/{incident_id}/approve")
def approve_endpoint(incident_id: str, payload: ApproveRequest, authorization: Optional[str] = Header(None)):
    """
    Approves an incident action.
    Enforces state machine: only PENDING -> APPROVED allowed.
    Binds approval to the exact action_fingerprint.
    """
    user_id = get_request_user_id(authorization)
    inc = get_incident(incident_id, user_id=user_id)
    if not inc:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")

    current_state = inc.get("approval", {}).get("state", ApprovalState.PENDING.value)
    if current_state != ApprovalState.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot approve incident in state '{current_state}'. A decided approval cannot change."
        )

    # If action override provided, validate and update
    if payload.action:
        act = payload.action
        act_type = act.get("type")
        target_svc = act.get("target_service")
        params = act.get("params") or {}

        if act_type not in ALLOWED_ACTIONS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid action type '{act_type}'. Must be one of: {sorted(ALLOWED_ACTIONS)}"
            )
        allowed_svcs = get_allowed_services()
        if target_svc not in allowed_svcs:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid target service '{target_svc}'. Must be one of: {sorted(allowed_svcs)}"
            )

        new_action = {
            "type": act_type,
            "target_service": target_svc,
            "params": params,
            "rationale": act.get("rationale") or f"Manual action override by {payload.approver}"
        }
        action_fp = compute_action_fingerprint(act_type, target_svc, params)
        inc["recommended_action"] = new_action
        inc["action_fingerprint"] = action_fp
    else:
        if not inc.get("recommended_action"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No recommended action on incident to approve. Please provide an action override."
            )
        action_fp = inc.get("action_fingerprint")
        if not action_fp:
            rec = inc["recommended_action"]
            action_fp = compute_action_fingerprint(
                rec.get("type", ""),
                rec.get("target_service", ""),
                rec.get("params", {})
            )
            inc["action_fingerprint"] = action_fp

    now_iso = datetime.now(timezone.utc).isoformat()
    inc["approval"] = {
        "state": ApprovalState.APPROVED.value,
        "approved_fingerprint": action_fp,
        "decided_by": payload.approver,
        "decided_at": now_iso,
        "note": payload.note or ""
    }
    inc["status"] = IncidentStatus.APPROVED.value
    updated = update_incident(incident_id, inc)
    return updated


@app.post("/incidents/{incident_id}/reject")
def reject_endpoint(incident_id: str, payload: RejectRequest, authorization: Optional[str] = Header(None)):
    """
    Rejects an incident action.
    Enforces state machine: only PENDING -> REJECTED allowed.
    """
    user_id = get_request_user_id(authorization)
    inc = get_incident(incident_id, user_id=user_id)
    if not inc:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")

    current_state = inc.get("approval", {}).get("state", ApprovalState.PENDING.value)
    if current_state != ApprovalState.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot reject incident in state '{current_state}'. A decided approval cannot change."
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    inc["approval"] = {
        "state": ApprovalState.REJECTED.value,
        "approved_fingerprint": None,
        "decided_by": payload.approver,
        "decided_at": now_iso,
        "note": payload.note or ""
    }
    inc["status"] = IncidentStatus.REJECTED.value
    updated = update_incident(incident_id, inc)
    return updated


@app.post("/incidents/{incident_id}/rediagnose")
def rediagnose_endpoint(incident_id: str, payload: RediagnoseRequest, authorization: Optional[str] = Header(None)):
    """
    Rediagnoses an incident after human rejection.
    Only allowed after REJECTED; starts a new cycle with the correction appended to alert context;
    Approval resets to PENDING.
    """
    user_id = get_request_user_id(authorization)
    inc = get_incident(incident_id, user_id=user_id)
    if not inc:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")

    current_state = inc.get("approval", {}).get("state")
    if current_state != ApprovalState.REJECTED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Rediagnosis is only allowed after approval state is REJECTED (current: '{current_state}')."
        )

    correction = payload.correction.strip()
    augmented_alert = f"{inc['alert']}\n\n[ENGINEER CORRECTION & CONTEXT]:\n{correction}"

    # Extract details, recall memories, reflect, call LLM
    extracted_details = extract_incident_details(augmented_alert)
    recalled_memories, memory_source = recall_memories_from_hindsight(augmented_alert, return_source=True, user_id=user_id)
    reflection_text = reflect_on_incident(augmented_alert)

    if recalled_memories:
        memory_snippets = [f"[Memory #{idx}] {m.get('text', '')}" for idx, m in enumerate(recalled_memories, 1)]
        memories_text = "\n\n".join(memory_snippets)
    else:
        memories_text = "No similar past incidents found in memory."

    diag_prompt = (
        f"INCOMING PRODUCTION ALERT / ERROR LOG (WITH ENGINEER CORRECTION):\n"
        f"\"\"\"\n{augmented_alert}\n\"\"\"\n\n"
        f"EXTRACTED INCIDENT METADATA:\n"
        f"Service: {extracted_details.get('service', 'unknown')} | Severity: {extracted_details.get('severity', 'medium')}\n\n"
        f"RECALLED PAST INCIDENTS:\n{memories_text}\n\n"
        f"REFLECTION:\n{reflection_text}\n\n"
        f"TASK:\n"
        f"1. In light of the engineer's explicit correction, revise the likely root cause.\n"
        f"2. Suggest an immediate technical fix / remediation.\n"
        f"3. Reference any past incident parallels."
    )

    try:
        revised_diagnosis = call_groq_llm(diag_prompt)
    except Exception as e:
        revised_diagnosis = f"Error generating revised diagnosis: {e}"

    new_action = propose_action(
        alert_text=augmented_alert,
        extracted_details=extracted_details,
        recalled_memories=recalled_memories,
        reflection=reflection_text,
        previous_failures=inc.get("failed_attempts")
    )

    new_fp = None
    if new_action:
        new_fp = compute_action_fingerprint(
            new_action.get("type", ""),
            new_action.get("target_service", ""),
            new_action.get("params", {})
        )

    cycle = inc.get("cycle", 1) + 1
    updates = {
        "alert": augmented_alert,
        "extracted_details": extracted_details,
        "diagnosis": revised_diagnosis,
        "reflection": reflection_text,
        "raw_recalled_memories": recalled_memories,
        "memory_source": memory_source,
        "recommended_action": new_action,
        "action_fingerprint": new_fp,
        "cycle": cycle,
        "status": IncidentStatus.AWAITING_APPROVAL.value,
        "approval": {
            "state": ApprovalState.PENDING.value,
            "approved_fingerprint": None,
            "decided_by": None,
            "decided_at": None,
            "note": None
        }
    }
    updated = update_incident(incident_id, updates)
    return updated


# --- Phase 4 & Phase 7: Execution & Outcome Endpoints ---

@app.post("/incidents/{incident_id}/execute")
def execute_endpoint(incident_id: str, authorization: Optional[str] = Header(None)):
    """
    Executes the approved remediation action for the incident.
    Captures before evidence, applies action to simulation,
    records execution result, runs automatic verification against C1-C5,
    and updates incident state / recovery loop.
    """
    user_id = get_request_user_id(authorization)
    inc = get_incident(incident_id, user_id=user_id)
    if not inc:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")
    return execute_incident(incident_id, user_id=user_id)


@app.get("/incidents/{incident_id}/outcome")
def get_outcome_endpoint(incident_id: str, authorization: Optional[str] = Header(None)):
    """
    Returns structured resolution outcome for the incident:
    {
      incident_id, alert, final_status, total_attempts,
      actions_attempted, execution_results, verification_results,
      before_after, time_to_resolution_seconds, memory_updated
    }
    """
    user_id = get_request_user_id(authorization)
    inc = get_incident(incident_id, user_id=user_id)
    if not inc:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")
    if inc.get("outcome"):
        return inc["outcome"]
    return build_incident_outcome(inc)


if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Incident Response API on port 8001...")
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
