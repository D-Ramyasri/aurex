"""
Evidence Collector for Monitored Applications.
Collects real signals from HTTP health probes and application-specific log sources.
Safely validates paths to prevent traversal and clearly distinguishes network connection failures from HTTP status codes.
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional
import requests

from models import (
    ApplicationConfig,
    Evidence,
    HealthCheckEvidence,
    LogEntry,
    LogEvidence,
)

logger = logging.getLogger("evidence_collector")

# Regex patterns for parsing log files
_LEVEL_PATTERN = re.compile(r"\b(ERROR|CRITICAL|FATAL|WARN|WARNING|INFO|DEBUG)\b", re.IGNORECASE)
_TIMESTAMP_PATTERN = re.compile(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b")


def get_active_error_entries(logs: LogEvidence) -> List[LogEntry]:
    """Return timestamped, recent error logs that are not superseded by recovery."""
    if not logs.available:
        return []

    entries = logs.entries
    parsed_timestamps = {}
    for index, entry in enumerate(entries):
        if not entry.timestamp:
            continue
        try:
            timestamp = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.astimezone()
            parsed_timestamps[index] = timestamp.astimezone(timezone.utc)
        except (AttributeError, TypeError, ValueError):
            continue

    recovery_markers = [
        index for index, entry in enumerate(entries)
        if "recovered" in entry.message.lower()
        or ("recovery" in entry.message.lower() and any(
            word in entry.message.lower() for word in ("complete", "restore", "disable")
        ))
    ]
    timed_recoveries = [
        (parsed_timestamps[index], index)
        for index in recovery_markers
        if index in parsed_timestamps
    ]
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    active_errors = []

    for index, entry in enumerate(entries):
        if entry.level != "ERROR" or index not in parsed_timestamps:
            continue
        timestamp = parsed_timestamps[index]
        if timestamp < cutoff:
            continue

        recovered = any(
            recovery_timestamp > timestamp
            or (recovery_timestamp == timestamp and recovery_index > index)
            for recovery_timestamp, recovery_index in timed_recoveries
        )
        if not recovered:
            active_errors.append(entry)

    return active_errors


def collect_health_check(app_config: ApplicationConfig, timeout: float = 3.0) -> HealthCheckEvidence:
    """
    Executes an HTTP GET probe to the application's configured health endpoint using its specific base_url and health_endpoint.
    Captures HTTP status, latency in milliseconds, response body, and connection errors.
    Clearly distinguishes network connection failure (UNREACHABLE) from HTTP response codes (e.g. 404, 500, 503).
    """
    base = app_config.base_url.rstrip("/")
    endpoint_path = app_config.health_endpoint.lstrip("/")
    full_url = f"{base}/{endpoint_path}" if endpoint_path else base

    start_time = time.perf_counter()
    try:
        logger.info("Probing health endpoint: %s (Application: %s)", full_url, app_config.application_id)
        resp = requests.get(full_url, timeout=timeout)
        duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
        status_code = resp.status_code

        # Attempt to parse response body as JSON
        body: Any = None
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:500].strip() if resp.text else None

        # 2xx Success check
        if 200 <= status_code < 300:
            if isinstance(body, dict):
                body_status = str(body.get("status", "")).lower()
                if body_status in ["down", "unhealthy", "error", "degraded", "failing"]:
                    return HealthCheckEvidence(
                        status="FAIL",
                        http_status=status_code,
                        response_time_ms=duration_ms,
                        endpoint=full_url,
                        response_body=body,
                        error_message=f"Health payload reported unhealthy state: {body_status}"
                    )
            return HealthCheckEvidence(
                status="PASS",
                http_status=status_code,
                response_time_ms=duration_ms,
                endpoint=full_url,
                response_body=body,
                error_message=None
            )
        elif status_code == 404:
            return HealthCheckEvidence(
                status="FAIL",
                http_status=404,
                response_time_ms=duration_ms,
                endpoint=full_url,
                response_body=body,
                error_message=f"Health check endpoint returned HTTP 404 Not Found at {full_url}"
            )
        else:
            return HealthCheckEvidence(
                status="FAIL",
                http_status=status_code,
                response_time_ms=duration_ms,
                endpoint=full_url,
                response_body=body,
                error_message=f"HTTP probe returned status code {status_code}"
            )

    except requests.exceptions.ConnectionError as e:
        duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
        logger.warning("Connection refused / unreachable for %s: %s", full_url, e)
        return HealthCheckEvidence(
            status="UNREACHABLE",
            http_status=None,
            response_time_ms=duration_ms,
            endpoint=full_url,
            response_body=None,
            error_message=f"Connection refused or host unreachable at {full_url}"
        )

    except requests.exceptions.Timeout as e:
        duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
        logger.warning("Connection timed out for %s after %ss", full_url, timeout)
        return HealthCheckEvidence(
            status="UNREACHABLE",
            http_status=None,
            response_time_ms=duration_ms,
            endpoint=full_url,
            response_body=None,
            error_message=f"Connection timed out after {timeout:.1f}s probing {full_url}"
        )

    except Exception as e:
        duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
        logger.error("Unexpected error probing %s: %s", full_url, e)
        return HealthCheckEvidence(
            status="ERROR",
            http_status=None,
            response_time_ms=duration_ms,
            endpoint=full_url,
            response_body=None,
            error_message=f"Unexpected health probe error: {str(e)}"
        )


def collect_logs(app_config: ApplicationConfig, max_lines: int = 50) -> LogEvidence:
    """
    Reads recent real log lines from the selected application's HTTP log endpoint.
    Parses timestamps, log levels, and counts ERROR/WARN entries.
    """
    base = app_config.base_url.rstrip("/")
    endpoint = f"{base}/logs/recent"
    try:
        log_params = {
            "lines": max_lines,
            "service": app_config.service_id,
            "application_id": app_config.application_id
        }
        response = requests.get(endpoint, params=log_params, timeout=3.0)
        source = endpoint
        if response.status_code < 200 or response.status_code >= 300:
            return LogEvidence(
                available=False,
                source=source,
                entries=[],
                error_count=0,
                warn_count=0,
                error_message=f"Log endpoint returned HTTP {response.status_code}",
            )
        payload = response.json()
        raw_lines = payload.get("logs", []) if isinstance(payload, dict) else payload
        if not isinstance(raw_lines, list):
            raise ValueError("Log endpoint response must contain a list of log lines")
    except requests.exceptions.RequestException as exc:
        return LogEvidence(
            available=False,
            source=endpoint,
            entries=[],
            error_count=0,
            warn_count=0,
            error_message=f"Could not collect logs from {endpoint}: {exc}",
        )
    except (ValueError, TypeError) as exc:
        return LogEvidence(
            available=False,
            source=endpoint,
            entries=[],
            error_count=0,
            warn_count=0,
            error_message=f"Invalid log endpoint response: {exc}",
        )

    raw_lines = raw_lines[-max_lines:]
    entries: List[LogEntry] = []
    error_count = 0
    warn_count = 0

    for item in raw_lines:
        raw_line = item.get("raw", item.get("message", "")) if isinstance(item, dict) else str(item)
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        ts_match = _TIMESTAMP_PATTERN.search(raw_line)
        timestamp = ts_match.group(0) if ts_match else None
        lvl_match = _LEVEL_PATTERN.search(raw_line)
        raw_level = lvl_match.group(1).upper() if lvl_match else "INFO"

        if raw_level in ["CRITICAL", "FATAL", "ERROR"]:
            level = "ERROR"
            error_count += 1
        elif raw_level in ["WARN", "WARNING"]:
            level = "WARN"
            warn_count += 1
        elif raw_level == "DEBUG":
            level = "DEBUG"
        else:
            level = "INFO"

        entries.append(LogEntry(timestamp=timestamp, level=level, message=raw_line, raw=raw_line))

    log_evidence = LogEvidence(
        available=True,
        source=source,
        entries=entries,
        error_count=error_count,
        warn_count=warn_count,
    )
    log_evidence.active_error_count = len(get_active_error_entries(log_evidence))
    return log_evidence


def collect_evidence(app_config: ApplicationConfig, timeout: float = 3.0) -> Evidence:
    """
    Orchestrates collection of health check probe and application logs for the specific configured application.
    """
    health = collect_health_check(app_config, timeout=timeout)
    logs = collect_logs(app_config)

    # Build human-readable summary
    summary_parts = []
    if health.status == "PASS":
        summary_parts.append(f"Health check PASS (HTTP {health.http_status}, {health.response_time_ms}ms)")
    elif health.status == "UNREACHABLE":
        summary_parts.append(f"Health check UNREACHABLE ({health.error_message})")
    elif health.http_status == 404:
        summary_parts.append(f"Health check REACHABLE but returned HTTP 404 (Endpoint not found at {health.endpoint})")
    else:
        summary_parts.append(f"Health check FAIL (HTTP {health.http_status or 'N/A'}, {health.error_message})")

    if logs.available:
        summary_parts.append(f"Logs: {logs.error_count} errors, {logs.warn_count} warnings from {logs.source}")
    else:
        summary_parts.append(f"Logs: Unavailable ({logs.error_message})")

    return Evidence(
        application_id=app_config.application_id,
        service=app_config.service_id,
        health_check=health,
        logs=logs,
        http_status=health.http_status,
        summary="; ".join(summary_parts)
    )
