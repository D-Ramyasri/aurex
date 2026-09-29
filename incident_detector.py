"""
Deterministic Incident Detector for Monitored Applications.
Evaluates collected telemetry and log evidence to detect and structure incidents without LLM dependency.
Accurately differentiates connection failures (UNREACHABLE) from HTTP 404, HTTP 500, and HTTP 503 errors.
"""

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from canonical_incident import generate_incident_id
from evidence_collector import get_active_error_entries
from models import ApplicationConfig, Evidence, Incident


def detect_incident(evidence: Evidence, app_config: ApplicationConfig) -> Tuple[bool, str, Optional[Incident]]:
    """
    Evaluates collected application evidence against deterministic rules.
    Returns: (incident_detected: bool, reason: str, incident: Optional[Incident])
    """
    symptoms: List[str] = []
    errors: List[str] = []
    severity: str = "medium"
    reason: str = ""
    description: str = ""
    incident_detected = False

    health = evidence.health_check
    logs = evidence.logs
    active_error_entries = get_active_error_entries(logs)
    telemetry = evidence.recent_metrics or evidence.metrics or {}
    telemetry_status = str(
        evidence.service_status or telemetry.get("status", "")
    ).lower()
    try:
        telemetry_error_rate = float(telemetry.get("error_rate", 0.0))
    except (TypeError, ValueError):
        telemetry_error_rate = 0.0

    # Rule 1: Application is completely unreachable or timed out (Critical)
    if health.status == "UNREACHABLE":
        incident_detected = True
        severity = "critical"
        reason = f"Application unreachable: {health.error_message}"
        symptoms.append(f"Health check probe {health.endpoint} is completely unreachable (Connection Refused or Timed Out)")
        if health.error_message:
            errors.append(health.error_message)
        description = (
            f"Production service '{app_config.application_name}' ({app_config.service_id}) is unreachable at {health.endpoint}. "
            f"Health probe failed with connection error."
        )

    # Rule 2: Application health probe returned HTTP 404 Not Found (Medium)
    elif health.http_status == 404:
        incident_detected = True
        severity = "medium"
        reason = f"Health check probe returned HTTP 404 (Endpoint not found at {health.endpoint})"
        symptoms.append(f"Service is reachable but health check endpoint returned HTTP 404 Not Found ({health.endpoint})")
        if health.error_message:
            errors.append(health.error_message)
        description = (
            f"Service '{app_config.application_name}' is reachable on its host, but the configured health endpoint "
            f"'{health.endpoint}' returned HTTP 404 Not Found."
        )

    # Rule 3: Application health probe returned HTTP 5xx or general failure (High / Medium)
    elif health.status == "FAIL":
        incident_detected = True
        if health.http_status and health.http_status >= 500:
            severity = "high"
            reason = f"Health endpoint returned HTTP {health.http_status}"
            symptoms.append(f"HTTP {health.http_status} server error on health probe {health.endpoint}")
        else:
            severity = "medium"
            reason = f"Health check failed (HTTP {health.http_status or 'N/A'})"
            symptoms.append(f"Health probe failed with HTTP {health.http_status or 'error'} on {health.endpoint}")

        if health.error_message:
            errors.append(health.error_message)
        if health.response_body:
            errors.append(f"Health response payload: {health.response_body}")

        description = (
            f"Service '{app_config.application_name}' reported unhealthy state on endpoint {health.endpoint}. "
            f"Probe returned status code {health.http_status}."
        )

    # Rule 4: Current unhealthy runtime telemetry
    unhealthy_statuses = {"degraded", "down", "unhealthy", "failed", "error", "stopped"}
    if telemetry_status in unhealthy_statuses or telemetry_error_rate > 0.01:
        incident_detected = True
        symptoms.append(
            f"Current telemetry reports status '{telemetry_status or 'unknown'}' "
            f"and error rate {telemetry_error_rate:.3f}"
        )
        if not description:
            severity = "high" if telemetry_status in {"down", "unhealthy"} or telemetry_error_rate >= 0.1 else "medium"
            reason = f"Current service telemetry is unhealthy (status: {telemetry_status or 'unknown'}, error rate: {telemetry_error_rate:.3f})"
            description = (
                f"Service '{app_config.application_name}' has current unhealthy telemetry: "
                f"status '{telemetry_status or 'unknown'}', error rate {telemetry_error_rate:.3f}."
            )

    # Rule 5: Current error logs detected in log stream
    if active_error_entries:
        incident_detected = True
        active_error_count = len(active_error_entries)
        symptoms.append(f"{active_error_count} recent ERROR level entries detected in service logs ({logs.source})")
        
        # Extract messages of ERROR entries (up to 5)
        for entry in active_error_entries[:5]:
            errors.append(entry.message)

        if not description:
            # If health check passed or was not the primary trigger
            severity = "high" if active_error_count >= 5 else "medium"
            reason = f"{active_error_count} recent ERROR logs detected in {logs.source}"
            description = (
                f"Service '{app_config.application_name}' produced {active_error_count} recent error log entries "
                f"indicating active internal failures."
            )
        else:
            # Append log errors to existing description
            description += f" Additionally, {active_error_count} recent error log entries were captured."

    # If no incident conditions are met
    if not incident_detected:
        reason = "Application health check passed and no active error logs detected."
        return False, reason, None

    # Construct structured Incident
    incident = Incident(
        incident_id=generate_incident_id(),
        timestamp=datetime.now(timezone.utc).isoformat(),
        application_id=app_config.application_id,
        service=app_config.service_id,
        severity=severity,
        description=description,
        symptoms=symptoms,
        errors=errors,
        source="application_inspection",
        evidence=evidence
    )

    return True, reason, incident
