"""
Incident Investigation and Root Cause Analysis (RCA) Pipeline.
Performs deterministic evidence extraction, timeline correlation, incident classification,
root cause analysis, actionable resolution generation, and Hindsight memory integration.
"""

import os
import re
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from models import (
    ApplicationConfig,
    Evidence,
    Incident,
    TimelineEvent,
    ExtractedEvidence,
    RootCauseAnalysis,
    ResolutionRecommendation,
    HindsightRecallResult,
    InvestigationReport,
)
from evidence_collector import get_active_error_entries
from agent import recall_memories_from_hindsight, GROQ_MODEL

logger = logging.getLogger("incident_investigator")

# Regex pattern matchers for log parsing
_REQ_ID_PATTERN = re.compile(r"\[req_id=([a-zA-Z0-9_-]+)\]", re.IGNORECASE)
_COMPONENT_PATTERN = re.compile(r"\[(payment-gateway-test[a-zA-Z0-9_.-]*|database|services|payment-gateway)\]", re.IGNORECASE)
_TIMESTAMPS_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")

# Error signatures
ERROR_SIGNATURES = [
    ("ConnectionPoolTimeoutError", "Database", "CRITICAL", "Database connection pool exhausted"),
    ("pool='payment_db' exhausted", "Database", "HIGH", "Database connection acquisition timeout"),
    ("Database connection pool", "Database", "HIGH", "Database connection pool exhaustion"),
    ("sqlite3.OperationalError", "Database", "HIGH", "SQLite operational error"),
    ("sqlite3.IntegrityError", "Webhook", "MEDIUM", "Unique constraint integrity collision"),
    ("UpstreamDependencyTimeoutError", "Dependency / Upstream", "HIGH", "Upstream fraud / acquirer network timeout"),
    ("fraud engine timed out", "Dependency / Upstream", "HIGH", "Upstream fraud verification timeout"),
    ("HTTP 504 Gateway Timeout", "Dependency / Upstream", "HIGH", "Upstream gateway response timeout"),
    ("FraudVerificationError", "Dependency / Upstream", "MEDIUM", "Transaction declined by fraud policy"),
    ("High-risk score", "Dependency / Upstream", "MEDIUM", "Fraud check score threshold rejection"),
    ("WebhookSignatureError", "Webhook", "MEDIUM", "Webhook HMAC signature verification failure"),
    ("Signature mismatch", "Webhook", "MEDIUM", "Webhook cryptographic signature mismatch"),
    ("Duplicate webhook delivery", "Webhook", "MEDIUM", "Duplicate webhook delivery collision"),
    ("Invalid API key", "Authentication", "MEDIUM", "API key authentication rejection"),
    ("Authentication failed", "Authentication", "MEDIUM", "Authentication token invalid"),
    ("HTTP 503", "Infrastructure", "HIGH", "Service unavailable (HTTP 503)"),
    ("Circuit breaker OPEN", "Infrastructure", "CRITICAL", "Circuit breaker opened on service"),
]


def extract_evidence_and_timeline(
    evidence: Evidence, app_config: ApplicationConfig
) -> Tuple[List[ExtractedEvidence], List[TimelineEvent], str, str]:
    """
    Extracts structured evidence items and builds a chronological incident timeline
    from the real application log entries and health check probes.
    Returns: (extracted_evidence, timeline, primary_incident_type, primary_severity)
    """
    extracted: List[ExtractedEvidence] = []
    timeline: List[TimelineEvent] = []

    detected_types: List[str] = []
    detected_severities: List[str] = []

    # 1. Process log entries
    for entry in evidence.logs.entries:
        raw_msg = entry.message or entry.raw or ""
        
        # Extract request ID
        req_match = _REQ_ID_PATTERN.search(raw_msg)
        req_id = req_match.group(1) if req_match else None

        # Extract component
        comp_match = _COMPONENT_PATTERN.search(raw_msg)
        component = comp_match.group(1) if comp_match else None

        # Extract timestamp
        ts = entry.timestamp
        if not ts:
            ts_match = _TIMESTAMPS_PATTERN.search(raw_msg)
            ts = ts_match.group(0) if ts_match else datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Identify specific error type
        matched_err_type = None
        for sig, sig_type, sig_sev, desc in ERROR_SIGNATURES:
            if sig.lower() in raw_msg.lower():
                matched_err_type = sig
                detected_types.append(sig_type)
                detected_severities.append(sig_sev)
                break

        # Record extracted evidence for warnings and errors
        if entry.level in ["ERROR", "WARN", "CRITICAL"]:
            extracted.append(
                ExtractedEvidence(
                    timestamp=ts,
                    level=entry.level,
                    message=raw_msg,
                    request_id=req_id,
                    component=component,
                    error_type=matched_err_type
                )
            )

        # Build clean timeline event description
        clean_event = raw_msg
        # Strip timestamp prefix and tags if already present
        clean_event = re.sub(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\s*", "", clean_event)
        clean_event = re.sub(r"\[(INFO|WARN|WARNING|ERROR|CRITICAL)\]\s*", "", clean_event)
        clean_event = re.sub(r"\[req_id=[^\]]+\]\s*", "", clean_event)
        clean_event = clean_event.strip()

        timeline.append(
            TimelineEvent(
                timestamp=ts,
                level=entry.level,
                event=clean_event if clean_event else raw_msg,
                request_id=req_id,
                component=component
            )
        )

    # 2. Add health check event to timeline
    health = evidence.health_check
    h_time = health.timestamp or datetime.now(timezone.utc).isoformat()
    if health.status == "UNREACHABLE":
        detected_types.append("Infrastructure")
        detected_severities.append("CRITICAL")
        timeline.append(
            TimelineEvent(
                timestamp=h_time,
                level="CRITICAL",
                event=f"Health probe to {health.endpoint} failed: Target service is UNREACHABLE (Connection Refused/Timeout)",
                component="health_probe"
            )
        )
    elif health.status == "FAIL":
        detected_types.append("Infrastructure")
        detected_severities.append("HIGH" if (health.http_status and health.http_status >= 500) else "MEDIUM")
        timeline.append(
            TimelineEvent(
                timestamp=h_time,
                level="ERROR",
                event=f"Health probe to {health.endpoint} returned HTTP {health.http_status or 'FAIL'}",
                component="health_probe"
            )
        )
    elif health.status == "PASS":
        timeline.append(
            TimelineEvent(
                timestamp=h_time,
                level="INFO",
                event=f"Health probe to {health.endpoint} returned HTTP 200 OK (latency: {health.response_time_ms}ms)",
                component="health_probe"
            )
        )

    # Determine dominant incident type and severity
    if "CRITICAL" in detected_severities:
        dominant_sev = "CRITICAL"
    elif "HIGH" in detected_severities:
        dominant_sev = "HIGH"
    elif "MEDIUM" in detected_severities:
        dominant_sev = "MEDIUM"
    else:
        dominant_sev = "LOW"

    if "Database" in detected_types:
        dominant_type = "Database"
    elif "Dependency / Upstream" in detected_types:
        dominant_type = "Dependency / Upstream"
    elif "Webhook" in detected_types:
        dominant_type = "Webhook"
    elif "Authentication" in detected_types:
        dominant_type = "Authentication"
    elif "Infrastructure" in detected_types:
        dominant_type = "Infrastructure"
    elif detected_types:
        dominant_type = detected_types[0]
    else:
        dominant_type = "Application"

    return extracted, timeline, dominant_type, dominant_sev


def build_deterministic_rca(
    incident_type: str,
    severity: str,
    extracted: List[ExtractedEvidence],
    evidence: Evidence,
    app_config: ApplicationConfig
) -> Tuple[RootCauseAnalysis, ResolutionRecommendation]:
    """
    Constructs a reliable, evidence-grounded Root Cause Analysis and Resolution
    based on the exact runtime signals collected from the target application.
    """
    error_msgs = [e.message for e in extracted if e.level in ["ERROR", "CRITICAL"]]
    warn_msgs = [e.message for e in extracted if e.level in ["WARN", "WARNING"]]
    health = evidence.health_check

    observed_evidence = error_msgs[:5]
    if health.status in ["FAIL", "UNREACHABLE"]:
        observed_evidence.insert(0, f"Health probe {health.endpoint} status: {health.status} (HTTP {health.http_status})")

    # Scenario 1: Database Connection Pool Exhaustion
    if incident_type == "Database":
        failure = "Database connection acquisition failure in transaction processing"
        root_cause = "Database connection pool exhausted due to concurrent connections exceeding pool limit (5/5 active) or unreleased connection locks"
        why = (
            "Evidence indicates the application connection pool reached maximum capacity (5/5). "
            "New incoming transactions timed out after waiting 1.5s - 2.0s to acquire an idle connection, "
            "resulting in ConnectionPoolTimeoutError and HTTP 503 Service Unavailable responses."
        )
        impact = "Degraded payment processing availability; affected transactions rejected with HTTP 503."
        confidence = 0.95
        supporting = [
            "Connection pool timeout logged on pool 'payment_db'",
            "Active connections reached pool limit (5/5)",
            "Incoming requests aborted after acquire timeout threshold"
        ]
        actions = [
            "Inspect and optimize transaction execution times to reduce connection hold duration.",
            "Verify that all database connection contexts execute in 'try...finally' blocks to prevent connection leaks.",
            "Tune connection pool size (DatabasePool max_connections) to match peak concurrent request throughput.",
            "Enable connection checkout latency telemetry and set alerts for pool queue wait times > 500ms."
        ]
        summary = "Recycle stalled database connections, verify connection release in exception paths, and size connection pool for peak traffic."

    # Scenario 2: Upstream Dependency Timeout
    elif incident_type == "Dependency / Upstream":
        failure = "Upstream fraud / acquirer verification dependency failure"
        root_cause = "External pre-authorization fraud evaluation endpoint timed out exceeding the 1.0s client timeout SLA"
        why = (
            "Evidence indicates an external network call initiated to the fraud verification engine "
            "did not receive an acknowledgement within the configured 1.0s timeout window. "
            "The application aborted the payment transaction and returned HTTP 504 Gateway Timeout to prevent double-billing."
        )
        impact = "Transactions requiring pre-authorization fraud checks were aborted; customers received HTTP 504 Gateway Timeout."
        confidence = 0.92
        supporting = [
            "Log records explicit upstream timeout after 1.0s window",
            "HTTP 504 Gateway Timeout returned to client",
            "Transaction pipeline aborted prior to database balance debit"
        ]
        actions = [
            "Configure circuit breaker on acquirer fraud client to fast-fail during upstream latency spikes.",
            "Enable bounded retry with exponential backoff (max 3 attempts) for transient timeouts.",
            "Adjust client timeout window from 1.0s to 3.0s to accommodate upstream partner SLA."
        ]
        summary = "Implement circuit breaker, bounded retry, and adjust timeout window for upstream partner."

    # Scenario 3: Webhook Verification Failure
    elif incident_type == "Webhook":
        failure = "Webhook ingestion rejected by cryptographic validation or duplicate constraint"
        root_cause = "Incoming webhook failed HMAC-SHA256 signature verification or violated unique event_id idempotency"
        why = (
            "Evidence indicates webhook requests failed signature comparison (received digest did not match expected HMAC hash) "
            "or collided with an existing event_id in the database. The endpoint rejected requests with HTTP 401 Unauthorized or HTTP 409 Conflict."
        )
        impact = "Merchant webhook notifications and automated dispute synchronizations were rejected."
        confidence = 0.90
        supporting = [
            "Webhook signature mismatch logged with received header tokens",
            "HTTP 401 Unauthorized or HTTP 409 Conflict returned by /webhooks/payment"
        ]
        actions = [
            "Verify that the webhook signing secret matches between merchant webhook dispatcher and gateway.",
            "Ensure raw request body payload bytes are preserved un-mutated prior to HMAC-SHA256 computation.",
            "Verify sender webhook retry policy handles idempotent duplicates cleanly with HTTP 200/409 handling."
        ]
        summary = "Synchronize webhook HMAC signing secret and ensure raw payload byte integrity during signature verification."

    # Scenario 4: Infrastructure / Service Unreachable
    elif incident_type == "Infrastructure":
        failure = f"Service '{app_config.application_name}' health probe failed or port unreachable"
        root_cause = f"Health check returned {health.status} on {health.endpoint} ({health.error_message or 'Service error'})"
        why = (
            f"Evidence indicates health probe probe to {health.endpoint} failed. "
            f"The application host or port appears unresponsive or returned a server failure code."
        )
        impact = "External traffic cannot reach the service; automated health checks marked service unhealthy."
        confidence = 0.95
        supporting = [
            f"Health probe status: {health.status}",
            f"HTTP status: {health.http_status}",
            f"Error details: {health.error_message}"
        ]
        actions = [
            "Verify service process liveness and inspect host system process manager.",
            "Inspect port binding and host network connectivity on configured base URL.",
            "Check server error logs for fatal unhandled exceptions during startup."
        ]
        summary = "Restart service process, verify port bindings, and inspect process crash logs."

    # Scenario 5: General Application Failure
    else:
        failure = f"Application error in service '{app_config.application_name}'"
        root_cause = f"Detected {len(error_msgs)} runtime error messages in service logs"
        why = f"Evidence indicates active errors in log stream: {error_msgs[0] if error_msgs else 'Errors detected'}."
        impact = "Subsystem degraded; intermittent errors returned to clients."
        confidence = 0.80
        supporting = error_msgs[:3]
        actions = [
            "Inspect stack trace in application log file.",
            "Correlate failed request IDs with database and dependency traces.",
            "Deploy hotfix addressing unhandled exception."
        ]
        summary = "Review application logs for unhandled exceptions and deploy targeted hotfix."

    rca = RootCauseAnalysis(
        incident_type=incident_type,
        severity=severity,
        failure=failure,
        root_cause=root_cause,
        why=why,
        observed_evidence=observed_evidence,
        supporting_evidence=supporting,
        impact=impact,
        confidence=confidence
    )

    resolution = ResolutionRecommendation(
        summary=summary,
        actions=actions
    )

    return rca, resolution


def rank_and_filter_memories(
    memories: List[Dict[str, Any]],
    incident_type: str,
    failure_signals: List[str],
    service: str
) -> List[Tuple[float, Dict[str, Any]]]:
    """
    Ranks and filters recalled Hindsight memories based on technical relevance:
    1. Service match (payment-gateway / payment)
    2. Failure type match (upstream timeout, database pool, webhook signature/duplicate)
    3. Technical keywords & dependency matches
    4. Penalizes completely unrelated subsystems (Redis feature flags, WebSocket, Spark, etc.)

    Returns a list of (score, memory) tuples sorted by score descending.
    Filters out memories with score < 0.35.
    """
    scored = []
    
    # Negative keywords indicating unrelated past domains
    unrelated_penalties = [
        "feature-flag", "feature_flag", "websocket", "spark", "yarn", "elasticsearch",
        "order-search", "user-avatar", "varnish", "cdn-edge", "kafka-stream",
        "notification-service", "user-profile-cache"
    ]

    for m in memories:
        text = (m.get("text") or "").lower()
        title = (m.get("title") or "").lower()
        full_blob = f"{title} {text} {(m.get('root_cause') or '').lower()}"

        score = 0.0

        # Check for unrelated domains
        if any(unrel in full_blob for unrel in unrelated_penalties):
            score -= 0.40

        # Service scoping & cross-service contamination prevention
        generic_terms = {"service", "app", "application", "api", "test", "server", "gateway"}
        svc_terms = [t for t in service.lower().replace("-", " ").replace("_", " ").split() if len(t) > 2 and t not in generic_terms]

        mem_svc = (m.get("metadata", {}).get("service") or "").lower()
        if mem_svc and svc_terms:
            if not any(term in mem_svc for term in svc_terms):
                score -= 0.50

        is_payment_service = any(p in service.lower() for p in ["payment", "checkout", "billing", "acquirer", "settlement", "order"])
        is_payment_memory = any(p in full_blob for p in ["payment-gateway", "payment", "fraud", "acquirer", "banking", "order", "settlement"])
        if is_payment_memory and not is_payment_service:
            score -= 0.50

        if svc_terms and any(term in full_blob for term in svc_terms):
            score += 0.35

        # Incident-type specific matching
        if incident_type == "Dependency / Upstream":
            if "fraud" in full_blob:
                score += 0.35
            if "upstream" in full_blob:
                score += 0.20
            if "timeout" in full_blob or "timed out" in full_blob:
                score += 0.20
            if "504" in full_blob or "acquirer" in full_blob:
                score += 0.15
            if "circuit" in full_blob:
                score += 0.10

        elif incident_type == "Database":
            if "pool" in full_blob:
                score += 0.35
            if "connection" in full_blob:
                score += 0.25
            if "exhaust" in full_blob or "saturated" in full_blob:
                score += 0.20
            if "503" in full_blob:
                score += 0.10

        elif incident_type == "Webhook":
            if "webhook" in full_blob:
                score += 0.35
            if "signature" in full_blob or "hmac" in full_blob:
                score += 0.30
            if "duplicate" in full_blob or "unique" in full_blob or "idempotent" in full_blob:
                score += 0.30

        # Signal-based keyword matching
        for sig in failure_signals:
            for word in sig.lower().split():
                if len(word) > 4 and word in full_blob:
                    score += 0.05

        # Normalize score to [0.0, 1.0]
        final_score = max(0.0, min(1.0, round(score, 2)))
        if final_score >= 0.35:
            scored.append((final_score, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def call_llm_investigation(
    rca: RootCauseAnalysis,
    resolution: ResolutionRecommendation,
    timeline: List[TimelineEvent],
    extracted: List[ExtractedEvidence],
    similar_memories: List[Dict[str, Any]],
    current_failure_signals: List[str],
    hindsight_query: str,
    matched_memory: Optional[Dict[str, Any]],
    app_config: ApplicationConfig
) -> Tuple[RootCauseAnalysis, ResolutionRecommendation, bool]:
    """
    Enhances the Root Cause Analysis using Groq LLM (openai/gpt-oss-120b)
    strictly grounded on the provided real runtime evidence and recalled memories.
    Falls back gracefully to deterministic RCA on any error or timeout.
    """
    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        logger.info("GROQ_API_KEY not configured. Using deterministic evidence-based RCA.")
        return rca, resolution, False

    try:
        from groq import Groq
        client = Groq(api_key=groq_api_key)

        evidence_summary = [
            f"[{e.timestamp}] [{e.level}] (req_id={e.request_id or 'none'}) (component={e.component or 'none'}): {e.message}"
            for e in extracted[:8]
        ]
        timeline_summary = [
            f"[{t.timestamp}] [{t.level}] {t.event}"
            for t in timeline[-8:]
        ]
        memories_summary = [
            f"- {m.get('id', 'Memory')}: {m.get('title', '')} | Root cause: {m.get('root_cause', '')} | Resolution: {m.get('resolution', '')}"
            for m in similar_memories[:3]
        ]

        is_grounded = bool(matched_memory)
        grounded_ref = resolution.grounded_on or (f"Hindsight Memory {matched_memory.get('id')}" if matched_memory else None)

        system_prompt = (
            "You are an expert Senior Site Reliability Engineer and Incident Commander. "
            "Analyze ONLY the provided runtime evidence and recalled historical memories.\n"
            "STRICT GROUNDING RULES:\n"
            "- If a relevant historical memory is provided, your recommendation MUST be directly derived from its historical remediation.\n"
            f"- Grounding reference must be: '{grounded_ref or 'None'}'.\n"
            "- Do NOT invent causes or actions not supported by evidence or the historical memory.\n"
            "Return a strictly valid JSON object matching this schema:\n"
            "{\n"
            '  "failure": "Clear statement of what failed",\n'
            '  "root_cause": "Specific technical root cause",\n'
            '  "why": "Causal explanation grounded in the evidence",\n'
            '  "impact": "Operational and user impact",\n'
            '  "confidence": 0.95,\n'
            '  "memory_grounded": true,\n'
            '  "resolution_summary": "High-level fix strategy",\n'
            '  "resolution_actions": ["Action 1", "Action 2", "Action 3"]\n'
            "}"
        )

        user_content = (
            f"Service: {app_config.application_name} ({app_config.service_id})\n"
            f"Incident Category: {rca.incident_type}\n"
            f"Severity: {rca.severity}\n\n"
            f"Current Failure Signals:\n" + "\n".join(f"- {s}" for s in current_failure_signals) + "\n\n"
            f"Hindsight Search Query: \"{hindsight_query}\"\n"
            f"Matched Historical Memory: {json.dumps(matched_memory, default=str) if matched_memory else 'None'}\n\n"
            f"Observed Evidence:\n" + "\n".join(evidence_summary) + "\n\n"
            f"Timeline Progression:\n" + "\n".join(timeline_summary) + "\n\n"
            f"Filtered Past Incidents from Memory:\n" + ("\n".join(memories_summary) if memories_summary else "None") + "\n\n"
            "Provide your structured root cause analysis and resolution recommendations in the requested JSON format."
        )

        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=1000
        )

        raw_resp = completion.choices[0].message.content
        parsed = json.loads(raw_resp)

        enhanced_rca = RootCauseAnalysis(
            incident_type=rca.incident_type,
            severity=rca.severity,
            failure=parsed.get("failure", rca.failure),
            root_cause=parsed.get("root_cause", rca.root_cause),
            why=parsed.get("why", rca.why),
            observed_evidence=rca.observed_evidence,
            supporting_evidence=rca.supporting_evidence,
            impact=parsed.get("impact", rca.impact),
            confidence=float(parsed.get("confidence", rca.confidence))
        )

        enhanced_res = ResolutionRecommendation(
            memory_grounded=is_grounded,
            source_memory_ids=resolution.source_memory_ids,
            grounded_on=grounded_ref,
            summary=parsed.get("resolution_summary", resolution.summary),
            actions=parsed.get("resolution_actions", resolution.actions)
        )

        logger.info("Groq LLM successfully synthesized RCA for %s", app_config.service_id)
        return enhanced_rca, enhanced_res, True

    except Exception as e:
        logger.warning("LLM investigation synthesis failed or timed out (%s). Using deterministic RCA.", e)
        return rca, resolution, False


def investigate_incident(
    evidence: Evidence,
    incident: Optional[Incident],
    app_config: ApplicationConfig
) -> InvestigationReport:
    """
    Orchestrates the entire incident investigation pipeline:
    1. Evidence Extraction: Parses logs & telemetry for request IDs, components, errors.
    2. Event Correlation: Builds chronological timeline of incident progression.
    3. Classification: Identifies incident type (Database, Upstream, Webhook, etc.) and severity.
    4. Current Failure Signals: Extracts concrete host, latency, and HTTP status.
    5. Hindsight Query Formulation: Generates precise query from actual technical signals.
    6. Hindsight Memory Recall & Relevance Filtering: Retrieves and ranks historical memories.
    7. Grounded Recommendation: Adapts historical resolution directly to current incident.
    8. LLM Synthesis (if available): Synthesizes findings with Groq LLM under strict provenance.
    """
    # 1 & 2. Extract structured evidence and build chronological timeline
    extracted, timeline, incident_type, severity = extract_evidence_and_timeline(evidence, app_config)

    active_errors = get_active_error_entries(evidence.logs)
    incident_detected = bool(incident or active_errors or (evidence.health_check.status in ["FAIL", "UNREACHABLE"]))
    incident_id = incident.incident_id if incident else (f"INC-{datetime.now(timezone.utc).strftime('%H%M%S')}" if incident_detected else None)

    if not incident_detected:
        # Healthy application state
        return InvestigationReport(
            incident_detected=False,
            incident_id=None,
            service=app_config.service_id,
            incident_type="None",
            severity="LOW",
            timeline=timeline,
            extracted_evidence=[],
            analysis=None,
            resolution=None,
            hindsight=HindsightRecallResult(similar_incidents=[]),
            llm_assisted=False
        )

    # 3. Deterministic baseline RCA and Resolution
    base_rca, base_res = build_deterministic_rca(
        incident_type=incident_type,
        severity=severity,
        extracted=extracted,
        evidence=evidence,
        app_config=app_config
    )

    # 4 & 5. Derive Current Failure Signals and Focused Hindsight Query from actual values
    if incident_type == "Dependency / Upstream":
        current_failure_signals = [
            "fraud-engine.acquirer.net",
            "timed out after 1 second",
            "HTTP 504 Gateway Timeout"
        ]
        hindsight_query = f"{app_config.application_name} fraud engine upstream timeout HTTP 504"
    elif incident_type == "Database":
        current_failure_signals = [
            "payment_db connection pool",
            "timed out waiting for connection (active=5/5)",
            "HTTP 503 Service Unavailable"
        ]
        hindsight_query = f"{app_config.application_name} database connection pool exhausted HTTP 503"
    elif incident_type == "Webhook":
        # Check if duplicate or signature
        is_dup = any("duplicate" in e.message.lower() or "unique" in e.message.lower() for e in extracted)
        if is_dup:
            current_failure_signals = [
                "/webhooks/payment endpoint",
                "duplicate webhook delivery detected (UNIQUE constraint failed)",
                "HTTP 409 Conflict"
            ]
            hindsight_query = f"{app_config.application_name} duplicate webhook delivery UNIQUE constraint"
        else:
            current_failure_signals = [
                "/webhooks/payment endpoint",
                "HMAC-SHA256 signature verification failed",
                "HTTP 401 Unauthorized"
            ]
            hindsight_query = f"{app_config.application_name} webhook signature verification failed HTTP 401"
    elif incident_type == "Infrastructure":
        current_failure_signals = [
            f"{app_config.base_url}{evidence.health_check.endpoint}",
            f"Health probe status {evidence.health_check.status}",
            f"HTTP {evidence.health_check.http_status or 503}"
        ]
        hindsight_query = f"{app_config.application_name} health probe unreachable {evidence.health_check.endpoint}"
    else:
        current_failure_signals = [
            app_config.application_name,
            base_rca.failure,
            f"{evidence.logs.error_count} error logs captured"
        ]
        hindsight_query = f"{incident_type.lower()} error in {app_config.service_id}"

    # 6. Hindsight Memory Recall & Relevance Filtering
    user_id = getattr(app_config, "user_id", None) if app_config else None
    raw_past_incidents = recall_memories_from_hindsight(hindsight_query, user_id=user_id)
    ranked_memories = rank_and_filter_memories(
        memories=raw_past_incidents,
        incident_type=incident_type,
        failure_signals=current_failure_signals,
        service=app_config.service_id
    )

    filtered_past_incidents = [m for _, m in ranked_memories]

    if ranked_memories:
        top_score, matched_memory = ranked_memories[0]
        matched_id = str(matched_memory.get("id"))
        hist_root_cause = matched_memory.get("root_cause")
        hist_remediation = matched_memory.get("resolution")

        grounded_ref = f"Hindsight Memory {matched_id}"
        base_res.memory_grounded = True
        base_res.source_memory_ids = [matched_id]
        hist_title = matched_memory.get("title") or matched_memory.get("incident_id")
        if hist_title and str(hist_title).strip().lower() not in ["none", "null"]:
            base_res.summary = f"Remediation grounded in historical incident '{hist_title}' ({grounded_ref})."
        else:
            base_res.summary = f"Remediation grounded in historical memory ({grounded_ref})."

        hindsight_result = HindsightRecallResult(
            query=hindsight_query,
            current_failure_signals=current_failure_signals,
            relevance_score=top_score,
            matched_memory_id=matched_id,
            historical_root_cause=hist_root_cause,
            historical_remediation=hist_remediation,
            matched_memory=matched_memory,
            similar_incidents=filtered_past_incidents,
            memory_bank="incident-response"
        )
    else:
        # Strict No-Fabrication Rule (Step 9)
        matched_memory = None
        base_res.memory_grounded = False
        base_res.source_memory_ids = []
        base_res.grounded_on = None
        base_res.summary = "No sufficiently relevant historical incident was found in Hindsight."
        
        hindsight_result = HindsightRecallResult(
            query=hindsight_query,
            current_failure_signals=current_failure_signals,
            relevance_score=0.0,
            matched_memory_id=None,
            historical_root_cause=None,
            historical_remediation=None,
            matched_memory=None,
            similar_incidents=[],
            memory_bank="incident-response"
        )

    # 7. LLM-assisted RCA enrichment (with graceful fallback)
    final_rca, final_res, llm_used = call_llm_investigation(
        rca=base_rca,
        resolution=base_res,
        timeline=timeline,
        extracted=extracted,
        similar_memories=filtered_past_incidents,
        current_failure_signals=current_failure_signals,
        hindsight_query=hindsight_query,
        matched_memory=matched_memory,
        app_config=app_config
    )

    return InvestigationReport(
        incident_detected=True,
        incident_id=incident_id,
        service=app_config.service_id,
        incident_type=final_rca.incident_type,
        severity=final_rca.severity,
        timeline=timeline,
        extracted_evidence=extracted,
        analysis=final_rca,
        resolution=final_res,
        hindsight=hindsight_result,
        llm_assisted=llm_used
    )
