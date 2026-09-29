"""
Domain models for Application Configuration, Telemetry Evidence, and Structured Incidents.
Feature 1: Incident Ingestion & Inspection.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, model_validator

from canonical_incident import generate_incident_id


class ApplicationConfig(BaseModel):
    """Configuration for a registered service / application."""
    application_id: str = Field(..., description="Unique slug for the application (e.g. 'payment-api')")
    application_name: str = Field(..., description="Human-readable application name (e.g. 'Payment Gateway API')")
    service_id: str = Field(..., description="Service identifier used across incident response (e.g. 'payment-gateway')")
    base_url: str = Field(..., description="Base HTTP URL of the service (e.g. 'http://127.0.0.1:8001')")
    health_endpoint: str = Field(default="/health", description="Path to the health check endpoint")
    remediation_endpoint: Optional[str] = Field(default="/remediation", description="Path to the remediation execution endpoint")
    metrics_endpoint: Optional[str] = Field(default="/metrics", description="Path to the real metrics endpoint")
    log_collection_method: Literal["http"] = Field(default="http", description="Application-level HTTP log collection")
    description: Optional[str] = Field(default="", description="Brief description of the service's role")
    user_id: Optional[int] = Field(default=None, description="Owner user ID for tenant isolation")

    @model_validator(mode="before")
    @classmethod
    def populate_aliases(cls, values: Any) -> Any:
        if isinstance(values, dict):
            # Support both name/application_name and service/service_id seamlessly
            if "name" in values and "application_name" not in values:
                values["application_name"] = values["name"]
            elif "application_name" in values and "name" not in values:
                values["name"] = values["application_name"]
                
            if "service" in values and "service_id" not in values:
                values["service_id"] = values["service"]
            elif "service_id" in values and "service" not in values:
                values["service"] = values["service_id"]
        return values

    @property
    def name(self) -> str:
        return self.application_name

    @property
    def service(self) -> str:
        return self.service_id


class HealthCheckEvidence(BaseModel):
    """Structured evidence collected from an HTTP health probe."""
    status: Literal["PASS", "FAIL", "UNREACHABLE", "ERROR"] = Field(..., description="Health check outcome")
    http_status: Optional[int] = Field(default=None, description="HTTP response status code (e.g. 200, 404, 503, or None if unreachable)")
    response_time_ms: float = Field(default=0.0, description="Response latency in milliseconds")
    endpoint: str = Field(..., description="Full URL probe endpoint")
    response_body: Optional[Any] = Field(default=None, description="Parsed response payload or raw text")
    error_message: Optional[str] = Field(default=None, description="Error detail if the connection failed")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class LogEntry(BaseModel):
    """Individual parsed log line with metadata."""
    timestamp: Optional[str] = Field(default=None, description="Extracted log timestamp")
    level: Literal["ERROR", "WARN", "INFO", "DEBUG", "UNKNOWN"] = Field(default="INFO")
    message: str = Field(..., description="Log message text")
    raw: str = Field(..., description="Original raw log line")


class LogEvidence(BaseModel):
    """Structured evidence collected from application logs."""
    available: bool = Field(..., description="Whether the log source was accessible")
    source: Optional[str] = Field(default=None, description="Path or location of the log source")
    entries: List[LogEntry] = Field(default_factory=list, description="Recent parsed log entries")
    error_count: int = Field(default=0, description="Count of ERROR/CRITICAL/FATAL entries")
    warn_count: int = Field(default=0, description="Count of WARN entries")
    active_error_count: Optional[int] = Field(default=None, description="Recent error entries not superseded by recovery")
    error_message: Optional[str] = Field(default=None, description="Error message if log reading failed")


class Evidence(BaseModel):
    """Aggregated machine-readable evidence collected during application inspection."""
    application_id: str
    service: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    health_check: HealthCheckEvidence
    logs: LogEvidence
    http_status: Optional[int] = None
    summary: str = Field(default="", description="Human-readable summary of collected evidence")
    recent_metrics: Optional[Dict[str, Any]] = Field(default=None, description="Derived recent metric summary from log entries and/or telemetry")
    service_status: Optional[str] = Field(default=None, description="Current runtime service status if available")
    deployment: Optional[Dict[str, Any]] = Field(default=None, description="Deployment metadata such as version, last_deployed_at, last_change")
    metrics: Optional[Dict[str, Any]] = Field(default=None, description="Additional telemetry metrics")


class Incident(BaseModel):
    """Structured incident record generated when an incident condition is detected."""
    incident_id: str = Field(default_factory=generate_incident_id)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    application_id: str
    service: str
    severity: Literal["low", "medium", "high", "critical"]
    description: str
    symptoms: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    impact: str = Field(default="", description="User and business impact of the incident")
    affected_components: List[str] = Field(default_factory=list, description="Components impacted by the incident")
    incident_type: str = Field(default="application", description="Incident category")
    source: str = Field(default="application_inspection")
    status: str = Field(default="open")
    evidence: Optional[Evidence] = None


class TimelineEvent(BaseModel):
    """An event in the chronological incident timeline."""
    timestamp: str = Field(..., description="Timestamp of the timeline event")
    level: Optional[str] = Field(default="INFO", description="Log level or event severity")
    event: str = Field(..., description="Description of the event in the sequence")
    request_id: Optional[str] = Field(default=None, description="Correlated request ID")
    component: Optional[str] = Field(default=None, description="Affected subsystem or component")


class ExtractedEvidence(BaseModel):
    """Specific piece of evidence extracted from application telemetry or logs."""
    timestamp: Optional[str] = None
    level: str
    message: str
    request_id: Optional[str] = None
    component: Optional[str] = None
    error_type: Optional[str] = None


class Hypothesis(BaseModel):
    """Alternative hypotheses considered during RCA."""
    statement: str = Field(..., description="Hypothesis statement")
    likelihood: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence value for the hypothesis")
    supporting_evidence: List[str] = Field(default_factory=list, description="Evidence supporting this hypothesis")
    against_evidence: List[str] = Field(default_factory=list, description="Evidence that weakens this hypothesis")


class RootCauseAnalysis(BaseModel):
    """Structured Root Cause Analysis (RCA) findings."""
    incident_type: str = Field(..., description="Classification category (e.g. Database, Dependency / Upstream, Webhook, Auth)")
    severity: str = Field(..., description="Severity level (LOW, MEDIUM, HIGH, CRITICAL)")
    failure: str = Field(..., description="Clear statement of what failed")
    root_cause: str = Field(..., description="Identified root cause")
    why: str = Field(..., description="Causal explanation supported by the evidence")
    observed_evidence: List[str] = Field(default_factory=list, description="Specific log lines / signals observed")
    supporting_evidence: List[str] = Field(default_factory=list, description="Specific supporting factors")
    impact: str = Field(..., description="Operational and user impact")
    confidence: float = Field(default=0.9, ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    hypotheses: List[Hypothesis] = Field(default_factory=list, description="Alternative hypotheses considered")


class ResolutionPlan(BaseModel):
    """Structured remediation plan for a recommended action."""
    action_type: str = Field(..., description="Allowlisted action type")
    target_service: str = Field(..., description="Simulated service target")
    params: Dict[str, Any] = Field(default_factory=dict, description="Action-specific parameters")
    reason: str = Field(default="", description="Why this action is appropriate")
    expected_result: str = Field(default="", description="Expected outcome")
    risk: str = Field(default="medium", description="low/medium/high")
    note: Optional[str] = Field(default=None, description="Risk note")
    prerequisites: List[str] = Field(default_factory=list, description="Preconditions required")
    rollback_plan: List[str] = Field(default_factory=list, description="Rollback plan if needed")


class ResolutionRecommendation(BaseModel):
    """Actionable remediation and resolution steps."""
    memory_grounded: bool = Field(default=False, description="Whether recommendation is grounded in an actual relevant Hindsight memory")
    source_memory_ids: List[str] = Field(default_factory=list, description="IDs of Hindsight memories used as remediation source")
    grounded_on: Optional[str] = Field(default=None, description="Grounding reference e.g. 'Hindsight Memory 86f5ef93-...'")
    summary: str = Field(..., description="Summary of the recommended remediation")
    actions: List[str] = Field(default_factory=list, description="Ordered actionable remediation steps")
    plan: Optional[ResolutionPlan] = Field(default=None, description="Structured resolution plan for approval workflow")


class HindsightRecallResult(BaseModel):
    """Past incidents retrieved from Hindsight memory bank."""
    query: Optional[str] = Field(default=None, description="Exact Hindsight search query executed")
    current_failure_signals: List[str] = Field(default_factory=list, description="Concrete current runtime failure signals")
    relevance_score: float = Field(default=0.0, description="Relevance score of top matched memory")
    matched_memory_id: Optional[str] = Field(default=None, description="Actual Hindsight memory ID")
    historical_root_cause: Optional[str] = Field(default=None, description="Historical root cause from recalled memory")
    historical_remediation: Optional[str] = Field(default=None, description="Historical remediation from recalled memory")
    matched_memory: Optional[Dict[str, Any]] = Field(default=None, description="Primary matched historical memory")
    similar_incidents: List[Dict[str, Any]] = Field(default_factory=list, description="Past incidents retrieved from Hindsight memory")
    memory_bank: str = Field(default="incident-response")


class InvestigationReport(BaseModel):
    """Comprehensive incident investigation, RCA, and resolution report."""
    incident_detected: bool
    incident_id: Optional[str] = None
    service: str
    incident_type: str
    severity: str
    timeline: List[TimelineEvent] = Field(default_factory=list)
    extracted_evidence: List[ExtractedEvidence] = Field(default_factory=list)
    analysis: Optional[RootCauseAnalysis] = None
    resolution: Optional[ResolutionRecommendation] = None
    hindsight: Optional[HindsightRecallResult] = None
    reflection: str = Field(default="", description="Engineer-facing reflection on the incident")
    patterns: List[str] = Field(default_factory=list, description="Patterns observed from prior actions")
    llm_assisted: bool = Field(default=False)


class InspectRequest(BaseModel):
    """Request payload to inspect a registered application."""
    application_id: str = Field(..., description="ID of the registered application to inspect")
    user_id: Optional[int] = Field(default=None, description="Optional user ID of the requesting operator")


class InspectResponse(BaseModel):
    """Response payload returned by the inspection endpoint."""
    status: Literal["success", "warning", "error"]
    application_id: str
    application_name: str
    incident_detected: bool
    reason: str
    incident: Optional[Incident] = None
    evidence: Evidence
    # Extended Investigation Fields
    investigation: Optional[InvestigationReport] = None
    timeline: List[TimelineEvent] = Field(default_factory=list)
    analysis: Optional[RootCauseAnalysis] = None
    resolution: Optional[ResolutionRecommendation] = None
    hindsight: Optional[HindsightRecallResult] = None


class RegisterApplicationResponse(BaseModel):
    """Response returned upon successful application registration."""
    success: bool = Field(default=True, description="Whether registration was successful")
    message: str = Field(default="Application registered successfully", description="Status message")
    application: ApplicationConfig = Field(..., description="Registered application configuration")

