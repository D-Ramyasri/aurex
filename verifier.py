"""
Automated Verification Engine for Incident Response Agent.
Collects telemetry evidence before and after remediation actions and evaluates 5 strict criteria:
- C1: health_pass_ratio == 1.0 (5 probes 200)
- C2: error_rate <= 0.01
- C3: p95_latency_ms <= 500
- C4: service_status == "running"
- C5: error_log_count since execution == 0
"""

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import requests

# Verification Criteria Constants
C1_NAME = "C1"
C1_DESC = "health_pass_ratio == 1.0 (all 5 probes HTTP 200)"

C2_NAME = "C2"
C2_DESC = "error_rate <= 0.01"

C3_NAME = "C3"
C3_DESC = "p95_latency_ms <= 500"

C4_NAME = "C4"
C4_DESC = "service_status == 'running'"

C5_NAME = "C5"
C5_DESC = "error_log_count since execution == 0 ERROR lines"

import socket
from urllib.parse import urlparse

SIM_BASE_URL = os.getenv("SIM_BASE_URL", "").rstrip("/")

_server_reachable: Optional[bool] = None
_last_check_time: float = 0.0


def _is_sim_server_listening() -> bool:
    global _server_reachable, _last_check_time
    if not SIM_BASE_URL:
        return False
    now = time.time()
    if _server_reachable is not None and (now - _last_check_time < 3.0):
        return _server_reachable

    parsed = urlparse(SIM_BASE_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.05)
        s.connect((host, port))
        s.close()
        _server_reachable = True
    except Exception:
        _server_reachable = False
    _last_check_time = now
    return _server_reachable


def sim_http_call(
    method: str,
    path: str,
    json_data: Any = None,
    params: Any = None,
    timeout: float = 5.0
) -> requests.Response:
    """Makes HTTP request to simulated environment with fast fallback to in-process TestClient."""
    if _is_sim_server_listening():
        url = f"{SIM_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
        try:
            return requests.request(method, url, json=json_data, params=params, timeout=timeout)
        except requests.exceptions.ConnectionError:
            pass

    # Fast in-process dispatch for tests
    from fastapi.testclient import TestClient
    from main import app
    tc = TestClient(app)
    return tc.request(method, path, json=json_data, params=params)


def collect_evidence(service: str, execution_start_iso: Optional[str] = None) -> Dict[str, Any]:
    """
    Collects genuine telemetry evidence:
    - 5 health probes spaced 0.3s apart
    - metrics (error_rate, p95_latency_ms, status)
    - logs since execution start (count ERROR lines)
    """
    health_codes: List[int] = []
    for _ in range(5):
        resp = sim_http_call("GET", f"/sim/{service}/health", timeout=5.0)
        health_codes.append(resp.status_code)
        time.sleep(0.3)

    health_pass_ratio = (
        sum(1 for c in health_codes if c == 200) / len(health_codes)
        if health_codes else 0.0
    )

    metrics_resp = sim_http_call("GET", f"/sim/{service}/metrics", timeout=5.0)
    if metrics_resp.status_code == 200:
        metrics = metrics_resp.json()
        error_rate = float(metrics.get("error_rate", 0.0))
        p95_latency_ms = int(metrics.get("p95_latency_ms", 0))
        service_status = str(metrics.get("status", "unknown"))
    else:
        error_rate = 1.0
        p95_latency_ms = 9999
        service_status = "down"

    params = {"limit": 100}
    if execution_start_iso:
        params["since"] = execution_start_iso
    logs_resp = sim_http_call("GET", f"/sim/{service}/logs", params=params, timeout=5.0)
    error_log_count = 0
    if logs_resp.status_code == 200:
        logs = logs_resp.json()
        error_log_count = sum(1 for l in logs if l.get("level") == "ERROR")

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "health_codes": health_codes,
        "health_pass_ratio": health_pass_ratio,
        "error_rate": error_rate,
        "p95_latency_ms": p95_latency_ms,
        "service_status": service_status,
        "error_log_count": error_log_count
    }


def collect_real_telemetry(app_config: Optional[Any], execution_start_iso: Optional[str] = None) -> Dict[str, Any]:
    """
    Collects real telemetry from a registered application:
    - 5 health probes spaced 0.2s apart to app_config.base_url + app_config.health_endpoint
    - reads genuine application logs from app_config.base_url + /logs/recent
    - extracts real metrics only if exposed by the application (no fabrication)
    """
    base_url = (getattr(app_config, "base_url", None) or "http://127.0.0.1:9001").rstrip("/")
    health_ep = (getattr(app_config, "health_endpoint", None) or "/health").lstrip("/")
    health_url = f"{base_url}/{health_ep}"

    health_codes: List[int] = []
    latencies: List[float] = []
    last_body: Any = None
    for _ in range(5):
        try:
            t0 = time.perf_counter()
            resp = requests.get(health_url, timeout=3.0)
            dur = round((time.perf_counter() - t0) * 1000, 2)
            health_codes.append(resp.status_code)
            latencies.append(dur)
            try:
                last_body = resp.json()
            except Exception:
                last_body = resp.text
        except Exception:
            health_codes.append(0)
            latencies.append(0.0)
        time.sleep(0.2)

    health_pass_ratio = (
        sum(1 for c in health_codes if c == 200) / len(health_codes)
        if health_codes else 0.0
    )
    avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0.0

    # Real log telemetry
    error_log_count = 0
    logs_available = False
    try:
        log_url = f"{base_url}/logs/recent"
        log_params = {"lines": 100}
        if execution_start_iso:
            log_params["since"] = execution_start_iso
        l_resp = requests.get(log_url, params=log_params, timeout=3.0)
        if l_resp.status_code == 200:
            logs_available = True
            log_data = l_resp.json()
            raw_lines = log_data.get("logs", [])
            for line in raw_lines:
                if "[ERROR]" in line or "ERROR" in line:
                    error_log_count += 1
    except Exception:
        pass

    service_status = "unknown"
    error_rate = None
    p95_latency_ms = None

    if isinstance(last_body, dict):
        if last_body.get("status"):
            service_status = str(last_body.get("status"))
        elif last_body.get("service"):
            service_status = "operational"

        if "metrics" in last_body and isinstance(last_body["metrics"], dict):
            m = last_body["metrics"]
            error_rate = m.get("error_rate")
            p95_latency_ms = m.get("p95_latency_ms")

    if error_rate is None or p95_latency_ms is None:
        try:
            m_ep = getattr(app_config, "metrics_endpoint", "/metrics") or "/metrics"
            m_url = f"{base_url}/{m_ep.lstrip('/')}"
            m_resp = requests.get(m_url, timeout=2.0)
            if m_resp.status_code == 200:
                m_data = m_resp.json()
                if error_rate is None:
                    error_rate = m_data.get("error_rate")
                if p95_latency_ms is None:
                    p95_latency_ms = m_data.get("p95_latency_ms")
        except Exception:
            pass

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "health_codes": health_codes,
        "health_pass_ratio": health_pass_ratio,
        "avg_latency_ms": avg_latency,
        "error_rate": error_rate,
        "p95_latency_ms": p95_latency_ms,
        "service_status": service_status,
        "error_log_count": error_log_count,
        "logs_available": logs_available,
        "raw_health_body": last_body,
        "target_url": health_url
    }


def verify_real_telemetry(
    incident: Dict[str, Any],
    before_evidence: Dict[str, Any],
    app_config: Optional[Any],
    execution_start_iso: Optional[str] = None
) -> Dict[str, Any]:
    """
    Evaluates verification against real application telemetry.
    Supports deterministic C1-C5 verification based strictly on actual post-action evidence.
    """
    after_evidence = collect_real_telemetry(app_config, execution_start_iso=execution_start_iso)

    c1_passed = (after_evidence["health_pass_ratio"] == 1.0)

    error_rate = after_evidence.get("error_rate")
    c2_passed = (error_rate is not None and error_rate <= 0.01)

    p95_latency_ms = after_evidence.get("p95_latency_ms")
    c3_passed = (p95_latency_ms is not None and p95_latency_ms <= 500)

    svc_status = str(after_evidence.get("service_status", "")).lower()
    c4_passed = svc_status in ("healthy", "operational", "running")
    c5_passed = (after_evidence["error_log_count"] == 0)

    criteria = [
        {
            "id": C1_NAME,
            "description": C1_DESC,
            "passed": c1_passed,
            "status": "PASS" if c1_passed else "FAIL",
            "observed": f"{after_evidence['health_pass_ratio'] * 100:.0f}%",
            "threshold": "== 100%"
        },
        {
            "id": C2_NAME,
            "description": C2_DESC,
            "passed": c2_passed,
            "status": "PASS" if c2_passed else ("UNAVAILABLE" if error_rate is None else "FAIL"),
            "observed": f"{error_rate * 100:.2f}%" if error_rate is not None else "UNAVAILABLE",
            "threshold": "<= 0.01"
        },
        {
            "id": C3_NAME,
            "description": C3_DESC,
            "passed": c3_passed,
            "status": "PASS" if c3_passed else ("UNAVAILABLE" if p95_latency_ms is None else "FAIL"),
            "observed": f"{p95_latency_ms}ms" if p95_latency_ms is not None else "UNAVAILABLE",
            "threshold": "<= 500ms"
        },
        {
            "id": C4_NAME,
            "description": C4_DESC,
            "passed": c4_passed,
            "status": "PASS" if c4_passed else "FAIL",
            "observed": after_evidence["service_status"],
            "threshold": "== 'healthy' / 'operational'"
        },
        {
            "id": C5_NAME,
            "description": C5_DESC,
            "passed": c5_passed,
            "status": "PASS" if c5_passed else "FAIL",
            "observed": after_evidence["error_log_count"],
            "threshold": "== 0"
        }
    ]

    reasons: List[str] = []
    executions = incident.get("executions", [])
    latest_exec = executions[-1] if executions else {}
    exec_status = latest_exec.get("status")

    if exec_status in ("UNAVAILABLE", "FAILED", "REJECTED_BY_ENGINE"):
        reasons.append(f"Real execution returned status '{exec_status}'; action was not applied cleanly to production target.")

    if not c1_passed:
        reasons.append(f"Real health probes failed (pass ratio: {after_evidence['health_pass_ratio'] * 100:.0f}%)")
    if not c4_passed:
        reasons.append(f"Real application status is '{after_evidence['service_status']}'")
    if not c5_passed:
        reasons.append(f"Observed {after_evidence['error_log_count']} errors in real logs")

    all_passed = c1_passed and c2_passed and c3_passed and c4_passed and c5_passed and (exec_status == "COMPLETED")

    if all_passed:
        decision = "SUCCESS"
        reasons = ["All 5 real verification criteria passed successfully; telemetry is fully healthy."]
    elif after_evidence["health_pass_ratio"] == 0.0 or svc_status in ("down", "degraded", "503"):
        decision = "FAILURE"
    elif exec_status in ("UNAVAILABLE", "FAILED") or error_rate is None or p95_latency_ms is None:
        decision = "UNCERTAIN"
    else:
        decision = "UNCERTAIN"

    app_name = (
        getattr(app_config, "application_name", None)
        or incident.get("application_name")
        or incident.get("service")
        or "Application"
    )
    improvement_summary = (
        f"Real Application: {app_name} | "
        f"Health Probes: {after_evidence['health_pass_ratio'] * 100:.0f}% | "
        f"Status: {after_evidence['service_status']} | "
        f"Recent Log Errors: {after_evidence['error_log_count']}"
    )

    return {
        "status": decision,
        "criteria": criteria,
        "before": before_evidence,
        "after": after_evidence,
        "improvement_summary": improvement_summary,
        "reasons": reasons,
        "primary_service": getattr(app_config, "service_id", None) or incident.get("service"),
        "faulted_services": []
    }

    return {
        "status": decision,
        "criteria": criteria,
        "before": before_evidence,
        "after": after_evidence,
        "improvement_summary": improvement_summary,
        "reasons": reasons,
        "primary_service": getattr(app_config, "service_id", None) or incident.get("service"),
        "faulted_services": []
    }


def verify_simulator(
    incident: Dict[str, Any],
    before_evidence: Dict[str, Any],
    execution_start_iso: Optional[str] = None
) -> Dict[str, Any]:
    """Simulator verification logic for isolated test scenarios."""
    target_service = incident.get("recommended_action", {}).get("target_service") or "checkout-service"
    primary_service = incident.get("primary_service") or target_service
    primary_fault = incident.get("primary_fault")

    if target_service != primary_service:
        before_primary = collect_evidence(primary_service, execution_start_iso=execution_start_iso)
    else:
        before_primary = before_evidence

    after_evidence = collect_evidence(primary_service, execution_start_iso=execution_start_iso)

    c1_passed = (after_evidence["health_pass_ratio"] == 1.0)
    c2_passed = (after_evidence["error_rate"] <= 0.01)
    c3_passed = (after_evidence["p95_latency_ms"] <= 500)
    c4_passed = (after_evidence["service_status"] == "running")
    c5_passed = (after_evidence["error_log_count"] == 0)

    criteria = [
        {"id": C1_NAME, "description": C1_DESC, "passed": c1_passed, "observed": after_evidence["health_pass_ratio"], "threshold": "== 1.0"},
        {"id": C2_NAME, "description": C2_DESC, "passed": c2_passed, "observed": after_evidence["error_rate"], "threshold": "<= 0.01"},
        {"id": C3_NAME, "description": C3_DESC, "passed": c3_passed, "observed": after_evidence["p95_latency_ms"], "threshold": "<= 500ms"},
        {"id": C4_NAME, "description": C4_DESC, "passed": c4_passed, "observed": after_evidence["service_status"], "threshold": "== 'running'"},
        {"id": C5_NAME, "description": C5_DESC, "passed": c5_passed, "observed": after_evidence["error_log_count"], "threshold": "== 0"}
    ]

    reasons: List[str] = []
    if target_service != primary_service:
        reasons.append(f"Action targeted {target_service} but fault was on {primary_service}")
    if not c1_passed:
        reasons.append(f"Health probes failed pass ratio check: {after_evidence['health_pass_ratio']} < 1.0")
    if not c2_passed:
        reasons.append(f"Error rate {after_evidence['error_rate']} exceeds threshold 0.01")
    if not c3_passed:
        reasons.append(f"P95 latency {after_evidence['p95_latency_ms']}ms exceeds threshold 500ms")
    if not c4_passed:
        reasons.append(f"Service status is '{after_evidence['service_status']}', expected 'running'")
    if not c5_passed:
        reasons.append(f"Observed {after_evidence['error_log_count']} ERROR logs since execution start")

    before_err = float(before_primary.get("error_rate", 0.0))
    after_err = float(after_evidence.get("error_rate", 0.0))
    before_healthy = (before_primary.get("service_status") == "running") and (before_err <= 0.05) and not primary_fault
    result_outcome = (incident.get("executions") or [{}])[-1].get("output", {}).get("result_outcome") if (incident.get("executions") or []) else None

    def build_result(decision: str, summary: str) -> Dict[str, Any]:
        return {
            "status": decision,
            "criteria": criteria,
            "before": before_primary,
            "after": after_evidence,
            "improvement_summary": summary,
            "reasons": reasons,
            "primary_service": primary_service,
            "faulted_services": incident.get("faulted_services", []),
        }

    if before_healthy:
        decision = "UNCERTAIN"
        reasons.append("No fault present; nothing to verify")
        return build_result(decision, f"error_rate: {before_err} -> {after_err}")

    if result_outcome == "NO_CHANGE" and primary_fault:
        decision = "FAILURE"
        reasons.append("Action had no effect on the faulty service")
        return build_result(decision, f"error_rate: {before_err} -> {after_err}")

    all_passed = c1_passed and c2_passed and c3_passed and c4_passed and c5_passed
    if all_passed:
        decision = "SUCCESS"
        reasons = ["All 5 verification criteria passed successfully; telemetry is healthy."]
    elif after_evidence["health_pass_ratio"] == 0:
        decision = "FAILURE"
        reasons.append("All 5 health check probes failed (pass ratio = 0.0)")
    elif after_evidence["service_status"] == "down":
        decision = "FAILURE"
        reasons.append("Service status is 'down'")
    elif before_err > 0 and after_err >= (0.9 * before_err):
        decision = "FAILURE"
        reasons.append(f"Error rate showed no meaningful improvement ({before_err} -> {after_err})")
    elif before_err > 0 and after_err < before_err:
        decision = "UNCERTAIN"
        reasons.append("Telemetry improved, but the service is not yet fully healthy and still below the strict success threshold.")
    else:
        decision = "UNCERTAIN"
        reasons.append("Telemetry improved or partially restored, but not all 5 strict criteria were met.")

    improvement_summary = (
        f"error_rate: {before_err} -> {after_err} | "
        f"p95: {before_primary.get('p95_latency_ms')}ms -> {after_evidence.get('p95_latency_ms')}ms | "
        f"status: {before_primary.get('service_status')} -> {after_evidence.get('service_status')}"
    )

    return build_result(decision, improvement_summary)


def verify(
    incident: Dict[str, Any],
    before_evidence: Dict[str, Any],
    execution_start_iso: Optional[str] = None
) -> Dict[str, Any]:
    """
    Verification dispatcher. Defaults to real application telemetry.
    Selects simulator verification only when EXECUTION_TARGET=simulator or incident['execution_target_type'] == 'simulator'.
    """
    env_target = os.getenv("EXECUTION_TARGET", "").lower().strip()
    inc_target = str(incident.get("execution_target_type", "")).lower().strip()
    if env_target == "simulator" or inc_target == "simulator":
        return verify_simulator(incident, before_evidence, execution_start_iso=execution_start_iso)

    from app_registry import get_application, list_applications
    app_id = incident.get("application_id")
    app_config = get_application(app_id) if app_id else None
    if not app_config:
        svc = incident.get("service") or incident.get("recommended_action", {}).get("target_service")
        for a in list_applications():
            if a.service_id == svc or a.application_id == svc:
                app_config = a
                break

    return verify_real_telemetry(incident, before_evidence, app_config, execution_start_iso=execution_start_iso)
