"""
Simulated Infrastructure Environment for Incident Response Agent.
Maintains state for microservices, models fault injections, and applies allowlisted actions.
All endpoints are plain `def` (synchronous) so internal HTTP requests don't deadlock FastAPI's event loop.
"""

import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

# Ensure data directory exists
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

sim_router = APIRouter(prefix="/sim", tags=["Simulation Environment"])

_sim_lock = threading.Lock()

SERVICES = [
    "payment-gateway",
    "auth-service",
    "database-cluster",
    "api-gateway",
    "checkout-service",
]

def _get_healthy_service_state(name: str) -> Dict[str, Any]:
    now_iso = datetime.now(timezone.utc).isoformat()
    version = "v2.4.0" if name == "checkout-service" else "v1.0.0"
    prev_version = "v2.3.9" if name == "checkout-service" else "v0.9.9"
    return {
        "name": name,
        "status": "running",  # "running" | "degraded" | "down"
        "replicas": 2,
        "version": version,
        "previous_version": prev_version,
        "config": {},
        "error_rate": 0.001,
        "p95_latency_ms": 120,
        "active_fault": None,
        "logs": [
            {
                "ts": now_iso,
                "level": "INFO",
                "message": f"{name} initialized and operating at healthy baseline."
            }
        ]
    }

# Module-level state dictionary
_services_state: Dict[str, Dict[str, Any]] = {
    name: _get_healthy_service_state(name) for name in SERVICES
}

def _append_log(service_dict: Dict[str, Any], level: str, message: str):
    now_iso = datetime.now(timezone.utc).isoformat()
    service_dict["logs"].append({
        "ts": now_iso,
        "level": level,
        "message": message
    })
    # Keep only the last 200 logs
    if len(service_dict["logs"]) > 200:
        service_dict["logs"] = service_dict["logs"][-200:]


# --- Pydantic Action Models ---

class ActionPayload(BaseModel):
    type: str = Field(..., description="Allowlisted action type")
    params: Dict[str, Any] = Field(default_factory=dict, description="Action parameters")

    @field_validator("type")
    @classmethod
    def validate_action_type(cls, v: str) -> str:
        allowed = {
            "restart_service",
            "scale_replicas",
            "set_config",
            "flush_cache",
            "rollback_deployment"
        }
        if v not in allowed:
            raise ValueError(f"Action '{v}' is not allowlisted. Must be one of: {sorted(allowed)}")
        return v


class InjectPayload(BaseModel):
    scenario: str = Field(..., description="Fault scenario name")


# --- Allowlisted Action Validation Helpers ---

def _validate_action_params(action_type: str, params: Dict[str, Any]):
    if action_type == "restart_service":
        return
    elif action_type == "scale_replicas":
        replicas = params.get("replicas")
        if replicas is None or not isinstance(replicas, int) or not (1 <= replicas <= 20):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="scale_replicas requires 'replicas' integer between 1 and 20"
            )
    elif action_type == "set_config":
        key = params.get("key")
        value = params.get("value")
        if not key or not isinstance(key, str) or value is None or not isinstance(value, (str, int, float, bool)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="set_config requires 'key' (str) and 'value' (str|int|bool)"
            )
    elif action_type == "flush_cache":
        return
    elif action_type == "rollback_deployment":
        return


# --- Endpoints ---

@sim_router.get("/services")
def get_services():
    """Returns state of all simulated services."""
    with _sim_lock:
        # Return deep copy of states
        return {name: dict(state) for name, state in _services_state.items()}


@sim_router.get("/{service}/health")
def get_service_health(service: str):
    """
    HTTP 200 {"status":"healthy"} when error_rate < 0.05 and status != down,
    else HTTP 503 {"status":"unhealthy"}.
    """
    with _sim_lock:
        if service not in _services_state:
            raise HTTPException(status_code=404, detail=f"Service '{service}' not found")
        svc = _services_state[service]
        is_healthy = (svc["error_rate"] < 0.05) and (svc["status"] != "down")
        if is_healthy:
            return {"status": "healthy"}
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "unhealthy"})


@sim_router.get("/{service}/metrics")
def get_service_metrics(service: str):
    """Returns {error_rate, p95_latency_ms, replicas, version, status, config}."""
    with _sim_lock:
        if service not in _services_state:
            raise HTTPException(status_code=404, detail=f"Service '{service}' not found")
        svc = _services_state[service]
        return {
            "error_rate": svc["error_rate"],
            "p95_latency_ms": svc["p95_latency_ms"],
            "replicas": svc["replicas"],
            "version": svc["version"],
            "status": svc["status"],
            "config": dict(svc["config"])
        }


@sim_router.get("/{service}/logs")
def get_service_logs(service: str, since: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=200)):
    """Returns service logs filtered by since and limit."""
    with _sim_lock:
        if service not in _services_state:
            raise HTTPException(status_code=404, detail=f"Service '{service}' not found")
        logs = _services_state[service]["logs"]
        if since:
            filtered = [log for log in logs if log["ts"] >= since]
        else:
            filtered = logs
        return filtered[-limit:]


@sim_router.post("/reset")
def reset_simulation():
    """Restores all services to healthy baseline."""
    with _sim_lock:
        for name in SERVICES:
            _services_state[name] = _get_healthy_service_state(name)
        return {"status": "reset", "message": "All simulated services restored to healthy baseline."}


@sim_router.post("/inject")
def inject_fault(payload: InjectPayload):
    """
    Injects a fault scenario into the target service:
    1. db_pool_exhaustion (database-cluster)
    2. redis_oom (auth-service)
    3. payment_timeout (payment-gateway)
    4. stale_dns_502 (api-gateway)
    5. bad_deploy (checkout-service)
    """
    scenario = payload.scenario
    with _sim_lock:
        if scenario == "db_pool_exhaustion":
            svc = _services_state["database-cluster"]
            svc["status"] = "degraded"
            svc["error_rate"] = 0.35
            svc["p95_latency_ms"] = 4000
            svc["active_fault"] = "db_pool_exhaustion"
            _append_log(svc, "ERROR", "FATAL: remaining connection slots are reserved for non-replication superuser connections (500/500 in use)")
            _append_log(svc, "ERROR", "Connection pool exhausted; 142 client queries queued in backlog waiting for connection slot")
            return {"status": "injected", "scenario": scenario, "target_service": "database-cluster"}

        elif scenario == "redis_oom":
            svc = _services_state["auth-service"]
            svc["status"] = "degraded"
            svc["error_rate"] = 0.30
            svc["p95_latency_ms"] = 350
            svc["active_fault"] = "redis_oom"
            _append_log(svc, "ERROR", "OOM command not allowed when used memory > 'maxmemory' (Redis session cache full)")
            _append_log(svc, "ERROR", "JWT revocation check failed due to Redis memory limit exhaustion; returning HTTP 500")
            return {"status": "injected", "scenario": scenario, "target_service": "auth-service"}

        elif scenario == "payment_timeout":
            svc = _services_state["payment-gateway"]
            svc["status"] = "degraded"
            svc["error_rate"] = 0.25
            svc["p95_latency_ms"] = 8000
            svc["active_fault"] = "payment_timeout"
            _append_log(svc, "ERROR", "Upstream webhook processing exceeded 2000ms threshold; HTTP 504 Gateway Timeout")
            _append_log(svc, "ERROR", "Worker threads blocked on synchronous Stripe webhook dispatch; queue depth 85")
            return {"status": "injected", "scenario": scenario, "target_service": "payment-gateway"}

        elif scenario == "stale_dns_502":
            svc = _services_state["api-gateway"]
            svc["status"] = "degraded"
            svc["error_rate"] = 0.40
            svc["p95_latency_ms"] = 550
            svc["active_fault"] = "stale_dns_502"
            _append_log(svc, "ERROR", "Bad Gateway: Upstream IP 10.0.4.12 unreachable after service migration (stale DNS cache)")
            _append_log(svc, "ERROR", "HTTP 502 Bad Gateway rate surged to 40% across routed endpoints")
            return {"status": "injected", "scenario": scenario, "target_service": "api-gateway"}

        elif scenario == "bad_deploy":
            svc = _services_state["checkout-service"]
            svc["previous_version"] = "v2.4.0"
            svc["version"] = "v2.4.1"
            svc["status"] = "degraded"
            svc["error_rate"] = 0.12
            svc["p95_latency_ms"] = 280
            svc["active_fault"] = "bad_deploy"
            _append_log(svc, "ERROR", "TypeError: Cannot read property 'currency' of undefined at CheckoutOrderService.process")
            _append_log(svc, "ERROR", "Null pointer exception introduced in release v2.4.1 impacting 12% of checkout flows")
            return {"status": "injected", "scenario": scenario, "target_service": "checkout-service"}

        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown scenario '{scenario}'. Allowed: db_pool_exhaustion, redis_oom, payment_timeout, stale_dns_502, bad_deploy"
            )


@sim_router.post("/{service}/actions")
def apply_service_action(service: str, payload: ActionPayload):
    """
    Applies an allowlisted action to the target service:
    - restart_service {}
    - scale_replicas {replicas: int 1-20}
    - set_config {key: str, value: str|int}
    - flush_cache {}
    - rollback_deployment {version: str}

    Decides FULL FIX, PARTIAL, or NO CHANGE based on fault scenario rules.
    """
    with _sim_lock:
        if service not in _services_state:
            raise HTTPException(status_code=404, detail=f"Service '{service}' not found")

        action_type = payload.type
        params = payload.params or {}
        _validate_action_params(action_type, params)

        svc = _services_state[service]
        active_fault = svc.get("active_fault")
        action_desc = f"{action_type}({params})"
        _append_log(svc, "INFO", f"Applied action: {action_desc}")

        full_fix = False
        partial_fix = False

        # --- Rule Evaluation ---
        if active_fault == "db_pool_exhaustion" and service == "database-cluster":
            if action_type == "set_config":
                key = str(params.get("key", ""))
                val = params.get("value")
                svc["config"][key] = val
                if key == "idle_in_transaction_session_timeout":
                    try:
                        if int(val) <= 30000:
                            full_fix = True
                    except (ValueError, TypeError):
                        pass
                elif key == "max_connections":
                    try:
                        if int(val) >= 800:
                            full_fix = True
                    except (ValueError, TypeError):
                        pass
            elif action_type == "restart_service":
                partial_fix = True
                svc["error_rate"] = 0.02
                svc["p95_latency_ms"] = 300
                _append_log(svc, "WARN", "Service restarted; connections reset but idle transaction leak persists.")

        elif active_fault == "redis_oom" and service == "auth-service":
            if action_type == "set_config":
                key = str(params.get("key", ""))
                val = str(params.get("value", ""))
                svc["config"][key] = val
                if key == "eviction_policy" and val == "allkeys-lru":
                    full_fix = True
            elif action_type == "flush_cache":
                full_fix = True
            elif action_type == "restart_service":
                partial_fix = True
                svc["error_rate"] = 0.03
                svc["p95_latency_ms"] = 180
                _append_log(svc, "WARN", "Service restarted; temporary memory relief but eviction policy unchanged.")

        elif active_fault == "payment_timeout" and service == "payment-gateway":
            if action_type == "set_config":
                key = str(params.get("key", ""))
                val = params.get("value")
                svc["config"][key] = val
                if key == "timeout_ms":
                    try:
                        if int(val) >= 8000:
                            full_fix = True
                    except (ValueError, TypeError):
                        pass
                elif key == "async_webhooks":
                    if str(val).lower() in ("true", "1"):
                        full_fix = True
            elif action_type == "scale_replicas":
                replicas = params.get("replicas", 0)
                try:
                    svc["replicas"] = int(replicas)
                    if int(replicas) >= 4:
                        partial_fix = True
                        svc["error_rate"] = 0.02
                        svc["p95_latency_ms"] = 600
                        _append_log(svc, "WARN", f"Scaled replicas to {replicas}; throughput improved but timeout root cause remains.")
                except (ValueError, TypeError):
                    pass

        elif active_fault == "stale_dns_502" and service == "api-gateway":
            if action_type == "set_config":
                key = str(params.get("key", ""))
                val = params.get("value")
                svc["config"][key] = val
                if key == "dns_ttl_seconds":
                    try:
                        if int(val) <= 10:
                            full_fix = True
                    except (ValueError, TypeError):
                        pass
            elif action_type == "restart_service":
                full_fix = True

        elif active_fault == "bad_deploy" and service == "checkout-service":
            if action_type == "rollback_deployment":
                # Swap version with previous_version
                curr = svc["version"]
                prev = svc["previous_version"]
                target_ver = params.get("version")
                if target_ver and target_ver == prev:
                    svc["version"] = prev
                    svc["previous_version"] = curr
                else:
                    svc["version"] = prev
                    svc["previous_version"] = curr
                full_fix = True
            elif action_type in ("restart_service", "scale_replicas"):
                # NO CHANGE
                if action_type == "scale_replicas":
                    svc["replicas"] = int(params.get("replicas", svc["replicas"]))
                _append_log(svc, "WARN", f"Action {action_type} executed but application bug in {svc['version']} persists.")

        else:
            # Action on healthy service or unhandled action
            if action_type == "restart_service":
                pass
            elif action_type == "scale_replicas":
                svc["replicas"] = int(params.get("replicas", svc["replicas"]))
            elif action_type == "set_config":
                svc["config"][str(params.get("key"))] = params.get("value")
            elif action_type == "rollback_deployment":
                curr = svc["version"]
                prev = svc["previous_version"]
                svc["version"] = prev
                svc["previous_version"] = curr

        if full_fix:
            svc["error_rate"] = 0.001
            svc["p95_latency_ms"] = 120
            svc["status"] = "running"
            svc["active_fault"] = None
            _append_log(svc, "INFO", f"Service recovered: all telemetry normalized and active fault cleared.")
            result_outcome = "FULL_FIX"
        elif partial_fix:
            result_outcome = "PARTIAL_FIX"
        else:
            result_outcome = "NO_CHANGE"

        return {
            "status": "success",
            "action": action_type,
            "target_service": service,
            "params": params,
            "result_outcome": result_outcome,
            "service_state": {
                "status": svc["status"],
                "error_rate": svc["error_rate"],
                "p95_latency_ms": svc["p95_latency_ms"],
                "replicas": svc["replicas"],
                "version": svc["version"],
                "active_fault": svc["active_fault"]
            }
        }
