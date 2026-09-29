"""
Payment Gateway Test API.
A realistic production-style microservice used to test the Incident Response Agent.
 Runs on http://127.0.0.1:9001 and exposes its real runtime logs over HTTP.
"""

import os
import sys
import uuid
import time
import logging
import sqlite3
import contextvars
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, HTTPException, status, Header, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from test_application.database import pool, ConnectionPoolTimeoutError
from test_application.services import (
    fraud_service,
    webhook_worker,
    verify_webhook_signature,
    generate_webhook_signature,
    FraudVerificationError,
    UpstreamDependencyTimeoutError,
    WebhookSignatureError,
    WEBHOOK_SECRET,
)

# -----------------------------------------------------------------------------
# LOGGING SETUP & REQUEST CONTEXT
# -----------------------------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = CURRENT_DIR.parent if CURRENT_DIR.name == "test_application" else CURRENT_DIR

LOG_DIR = WORKSPACE_ROOT / "sample_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "payment-test-api.log"

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="system")


class ContextLogFilter(logging.Filter):
    def filter(self, record):
        record.request_id = request_id_ctx.get("system")
        return True


import collections

logger = logging.getLogger("payment-gateway-test")
logger.setLevel(logging.INFO)
forced_failure = threading.Event()

_METRICS_LOCK = threading.Lock()
_REQUEST_METRICS_WINDOW = collections.deque(maxlen=200)


def record_request_metric(status_code: int, duration_ms: float):
    with _METRICS_LOCK:
        _REQUEST_METRICS_WINDOW.append((time.time(), status_code, duration_ms))


def get_real_metrics() -> Dict[str, Any]:
    with _METRICS_LOCK:
        samples = list(_REQUEST_METRICS_WINDOW)

    now = time.time()
    recent = [s for s in samples if (now - s[0]) <= 120.0]
    if not recent:
        svc_status = "degraded" if forced_failure.is_set() else ("down" if pool.available_connections == 0 else "healthy")
        return {
            "error_rate": 0.0,
            "p95_latency_ms": 15,
            "service_status": svc_status,
            "status": svc_status,
            "total_requests": 0,
            "sample_count": 0
        }

    total = len(recent)
    errors = sum(1 for s in recent if s[1] >= 400)
    error_rate = round(errors / total, 4)
    latencies = sorted([s[2] for s in recent])
    p95_idx = int(len(latencies) * 0.95)
    p95_latency = round(latencies[min(p95_idx, len(latencies) - 1)], 1)

    if forced_failure.is_set() or error_rate > 0.5:
        svc_status = "degraded"
    elif pool.available_connections == 0:
        svc_status = "down"
    else:
        svc_status = "healthy"

    return {
        "error_rate": error_rate,
        "p95_latency_ms": p95_latency,
        "service_status": svc_status,
        "status": svc_status,
        "total_requests": total,
        "sample_count": total
    }


log_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] [%(name)s] [req_id=%(request_id)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# File Handler
file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(log_formatter)
file_handler.addFilter(ContextLogFilter())

# Stream Handler
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(log_formatter)
stream_handler.addFilter(ContextLogFilter())

# Clear any previous handlers
logger.handlers.clear()
logger.addHandler(file_handler)
logger.addHandler(stream_handler)


# -----------------------------------------------------------------------------
# LIFESPAN & APPLICATION DEFINITION
# -----------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB schema & connection pool
    pool.initialize()
    # Start background webhook dispatcher worker
    webhook_worker.start()
    logger.info("Payment Gateway Test API initialized on port 9001 (db_pool=5, worker=active)")
    yield
    webhook_worker.stop()
    pool.close_all()
    logger.info("Payment Gateway Test API gracefully stopped.")


app = FastAPI(
    title="Payment Gateway Test API",
    description="Real production-grade test target for Incident Response Agent application inspection and log analysis.",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    req_id = request.headers.get("X-Request-ID", f"req_{uuid.uuid4().hex[:10]}")
    token = request_id_ctx.set(req_id)
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        duration_ms = (time.perf_counter() - t0) * 1000
        record_request_metric(response.status_code, duration_ms)
        return response
    except Exception as e:
        duration_ms = (time.perf_counter() - t0) * 1000
        record_request_metric(500, duration_ms)
        raise e
    finally:
        request_id_ctx.reset(token)


# -----------------------------------------------------------------------------
# SCHEMAS
# -----------------------------------------------------------------------------
class PaymentRequest(BaseModel):
    account_id: str = Field(default="acc_merch_001", description="Merchant account ID")
    amount: float = Field(..., gt=0.0, description="Transaction amount in currency units")
    currency: str = Field(default="USD", description="ISO 4217 Currency Code")
    card_number: str = Field(..., description="16-digit credit card number")
    cvv: str = Field(..., min_length=3, max_length=4, description="Card CVV/CVC code")
    idempotency_key: Optional[str] = Field(default=None, description="Client idempotency key")


class HoldConnectionRequest(BaseModel):
    seconds: float = Field(default=2.0, ge=0.1, le=10.0, description="Duration to hold DB connection")


class RemediationRequest(BaseModel):
    action: str = Field(..., description="Allowlisted remediation action type")
    target_service: Optional[str] = Field(default="payment-gateway-test")
    params: Optional[Dict[str, Any]] = Field(default_factory=dict)



# -----------------------------------------------------------------------------
# REAL ENDPOINTS
# -----------------------------------------------------------------------------
@app.get("/api/info")
def get_service_info():
    """Returns service metadata for the Incident Response Agent."""
    return {
        "application": "Payment Gateway Test API",
        "service_id": "payment-gateway-test",
        "version": "2.0.0",
        "status": "operational",
        "port": 9001,
        "endpoints": {
            "health": "GET /health",
            "payments": "POST /payments",
            "webhooks": "POST /webhooks/payment",
            "transactions": "GET /transactions",
            "logs": "GET /logs/recent",
            "dashboard": "GET /"
        }
    }


@app.get("/health")
def health_check(response: Response):
    """
    Health check probe endpoint.
    Performs real live checks against:
    1. Database pool (probes live connection acquisition and query)
    2. Background WebhookWorker health
    Returns HTTP 200 if all operational, or HTTP 503 if any critical component is degraded.
    """
    now_str = datetime.now(timezone.utc).isoformat()
    db_status = "operational"
    worker_status = "active" if webhook_worker.is_alive() else "stopped"
    is_healthy = True
    error_details = []

    if forced_failure.is_set():
        is_healthy = False
        error_details.append("Payment gateway failure simulation is active")
        logger.error("Health check failed: payment gateway failure simulation is active")

    # 1. Real probe of Database Connection Pool
    try:
        with pool.connection(timeout=0.8) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1;")
            cursor.fetchone()
    except ConnectionPoolTimeoutError as ex:
        is_healthy = False
        db_status = "unavailable (pool exhausted)"
        error_details.append(str(ex))
        logger.error("Health check failed: Database connection pool exhausted. %s", ex)
    except sqlite3.Error as ex:
        is_healthy = False
        db_status = f"error ({type(ex).__name__})"
        error_details.append(str(ex))
        logger.error("Health check failed: Database query error: %s", ex, exc_info=True)

    # 2. Real probe of background worker
    if not webhook_worker.is_alive():
        is_healthy = False
        error_details.append("Background WebhookWorker is not running")
        logger.warning("Health check warning: WebhookWorker thread is dead.")

    real_metrics = get_real_metrics()

    if is_healthy:
        logger.info(
            "Health check passed - HTTP 200 OK (db_pool: active=%d/max=%d, worker=%s)",
            pool.active_connections, pool.max_connections, worker_status
        )
        return {
            "status": "healthy",
            "service": "payment-gateway-test",
            "timestamp": now_str,
            "metrics": real_metrics,
            "components": {
                "database_pool": {
                    "status": db_status,
                    "active_connections": pool.active_connections,
                    "available_connections": pool.available_connections,
                    "max_connections": pool.max_connections
                },
                "webhook_worker": worker_status,
                "fraud_engine": "operational"
            }
        }
    else:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        logger.error("Health check returned HTTP 503: %s", "; ".join(error_details))
        return {
            "status": "degraded",
            "service": "payment-gateway-test",
            "timestamp": now_str,
            "metrics": real_metrics,
            "errors": error_details,
            "components": {
                "database_pool": {
                    "status": db_status,
                    "active_connections": pool.active_connections,
                    "max_connections": pool.max_connections
                },
                "webhook_worker": worker_status
            }
        }


@app.get("/metrics")
def get_metrics_endpoint():
    """Returns real telemetry metrics computed from recent live HTTP requests."""
    return get_real_metrics()


_checkout_state = {
    "remediated_until": 0.0
}


@app.get("/incidents/checkout/health")
def checkout_health_probe(response: Response):
    is_remediated = time.time() < _checkout_state["remediated_until"]
    if not is_remediated:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "unhealthy",
            "service": "checkout-service",
            "error": "Database connection pool exhausted. 15 client queries queued in backlog.",
            "metrics": {
                "error_rate": 0.35,
                "p95_latency_ms": 3800,
                "service_status": "degraded",
                "status": "degraded"
            }
        }
    return {
        "status": "healthy",
        "service": "checkout-service",
        "metrics": {
            "error_rate": 0.0,
            "p95_latency_ms": 22,
            "service_status": "healthy",
            "status": "healthy"
        }
    }


def checkout_logs(lines: int = 50, since: Optional[str] = None):
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    is_remediated = time.time() < _checkout_state["remediated_until"]
    if not is_remediated:
        err_logs = [
            f"{now_str} [ERROR] [checkout-service] FATAL: Database connection pool exhausted (5/5 connections held by stale workers)",
            f"{now_str} [ERROR] [checkout-service] ConnectionPoolTimeoutError: Timeout acquiring connection from pool after 3000ms",
            f"{now_str} [ERROR] [checkout-service] HTTP 503 Service Unavailable: Transaction checkout endpoint failing due to database pool exhaustion",
            f"{now_str} [WARN] [checkout-service] Circuit breaker tripped for database cluster"
        ]
        return {
            "total_lines": len(err_logs),
            "lines_returned": len(err_logs),
            "logs": err_logs
        }
    ok_logs = [
        f"{now_str} [INFO] [checkout-service] Service restarted and connection pool reinitialized.",
        f"{now_str} [INFO] [checkout-service] Health check nominal (HTTP 200 OK)."
    ]
    return {
        "total_lines": len(ok_logs),
        "lines_returned": len(ok_logs),
        "logs": ok_logs
    }


@app.post("/incidents/checkout/remediation")
def checkout_remediation(payload: RemediationRequest):
    if payload.action in ("reset", "trigger_failure"):
        _checkout_state["remediated_until"] = 0.0
        return {
            "status": "success",
            "action": payload.action,
            "target_service": "checkout-service",
            "message": "Checkout service reset to active incident state."
        }
    _checkout_state["remediated_until"] = time.time() + 180
    return {
        "status": "success",
        "action": payload.action,
        "target_service": "checkout-service",
        "message": f"Remediation action '{payload.action}' executed successfully on checkout-service. Pool reinitialized and stale workers terminated."
    }


_settlement_state = {
    "remediated_until": 0.0
}


@app.get("/incidents/settlement/health")
def settlement_health_probe(response: Response):
    is_remediated = time.time() < _settlement_state["remediated_until"]
    if not is_remediated:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "unhealthy",
            "service": "order-service",
            "error": "Database connection pool exhausted. 18 settlement batches queued in backlog.",
            "metrics": {
                "error_rate": 0.42,
                "p95_latency_ms": 4200,
                "service_status": "degraded",
                "status": "degraded"
            }
        }
    return {
        "status": "healthy",
        "service": "order-service",
        "metrics": {
            "error_rate": 0.0,
            "p95_latency_ms": 28,
            "service_status": "healthy",
            "status": "healthy"
        }
    }


def settlement_logs(lines: int = 50, since: Optional[str] = None):
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    is_remediated = time.time() < _settlement_state["remediated_until"]
    if not is_remediated:
        err_logs = [
            f"{now_str} [ERROR] [order-service] FATAL: Database connection pool exhausted (5/5 connections acquired by long-running transactions)",
            f"{now_str} [ERROR] [order-service] ConnectionPoolTimeoutError: Timeout acquiring database connection pool slot after 3000ms",
            f"{now_str} [ERROR] [order-service] HTTP 503 Service Unavailable: Order settlement dispatch pipeline blocked due to connection pool saturation",
            f"{now_str} [WARN] [order-service] Circuit breaker tripped for order settlement ledger"
        ]
        return {
            "total_lines": len(err_logs),
            "lines_returned": len(err_logs),
            "logs": err_logs
        }
    ok_logs = [
        f"{now_str} [INFO] [order-service] Service restarted and database connection pool reinitialized.",
        f"{now_str} [INFO] [order-service] Health check nominal (HTTP 200 OK)."
    ]
    return {
        "total_lines": len(ok_logs),
        "lines_returned": len(ok_logs),
        "logs": ok_logs
    }


@app.post("/incidents/settlement/remediation")
def settlement_remediation(payload: RemediationRequest):
    if payload.action in ("reset", "trigger_failure"):
        _settlement_state["remediated_until"] = 0.0
        return {
            "status": "success",
            "action": payload.action,
            "target_service": "order-service",
            "message": "Settlement service reset to active incident state."
        }
    _settlement_state["remediated_until"] = time.time() + 180
    return {
        "status": "success",
        "action": payload.action,
        "target_service": "order-service",
        "message": f"Remediation action '{payload.action}' executed successfully on order-service. Connection pool reinitialized and transactions recovered."
    }


@app.post("/incidents/reset-all")
def reset_all_incidents():
    _checkout_state["remediated_until"] = 0.0
    _settlement_state["remediated_until"] = 0.0
    return {"status": "success", "message": "All incident demonstration services reset to active failure state."}


@app.post("/remediation")
def execute_remediation(payload: RemediationRequest):
    """
    Real authorized remediation execution endpoint.
    Applies real configuration and resource recovery actions directly to the payment gateway service.
    """
    action = payload.action
    params = payload.params or {}
    logger.info("Executing real remediation action '%s' on %s with params %s", action, payload.target_service, params)

    details = {}
    if action == "trigger_failure" or params.get("trigger_failure"):
        forced_failure.set()
        details = {
            "message": "Real failure condition triggered on target payment gateway service",
            "status": "degraded"
        }
    elif action in ("restart_service", "reset"):
        forced_failure.clear()
        pool.reinitialize()
        if not webhook_worker.is_alive():
            webhook_worker.start()
        with _METRICS_LOCK:
            _REQUEST_METRICS_WINDOW.clear()
        try:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write(f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} [INFO] [payment-gateway-test] Log file reset after service remediation\n")
        except Exception:
            pass
        details = {
            "message": "Service state reset successfully",
            "db_pool": "reinitialized",
            "failure_flags": "cleared",
            "metrics_window": "cleared",
            "logs": "reset"
        }
    elif action == "scale_replicas":
        new_max = int(params.get("replicas") or params.get("max_connections") or 10)
        pool.scale_pool(new_max)
        forced_failure.clear()
        details = {
            "message": f"Connection pool scaled to max_connections={new_max}",
            "max_connections": new_max
        }
    elif action == "set_config":
        if "fraud_timeout" in params:
            fraud_service.timeout = float(params["fraud_timeout"])
        forced_failure.clear()
        details = {
            "message": "Service configuration updated",
            "params_applied": params
        }
    elif action in ("flush_cache", "rollback_deployment"):
        forced_failure.clear()
        with _METRICS_LOCK:
            _REQUEST_METRICS_WINDOW.clear()
        details = {
            "message": f"Action '{action}' executed cleanly",
            "status": "restored"
        }
    else:
        forced_failure.clear()
        pool.reinitialize()
        details = {"message": f"Executed default reset for action '{action}'"}

    logger.info("Real remediation '%s' completed: %s", action, details)
    return {
        "status": "COMPLETED",
        "action": action,
        "target_service": payload.target_service or "payment-gateway-test",
        "output": details,
        "errors": [],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }



@app.post("/payments", status_code=status.HTTP_201_CREATED)
def process_payment(payload: PaymentRequest, authorization: Optional[str] = Header(None)):
    """
    Executes a genuine payment transaction:
    - Verifies Bearer authentication token
    - Interacts with upstream fraud / card verification engine
    - Acquires real database connection from pool
    - Performs idempotency check
    - Enforces account balance and status checks
    - Commits transaction record to SQLite database
    - Queues merchant webhook notification
    """
    # 1. API Authentication
    valid_keys = ["pk_live_sec_99182", "pk_test_sample_key"]
    if authorization:
        token = authorization.replace("Bearer ", "").strip()
        if token not in valid_keys:
            logger.warning("Authentication failed: invalid API key '%s'", token[:8] + "...")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    card_clean = payload.card_number.replace("-", "").replace(" ", "")
    card_last4 = card_clean[-4:] if len(card_clean) >= 4 else "0000"
    payment_id = f"pay_{uuid.uuid4().hex[:12]}"
    idempotency_key = payload.idempotency_key or f"idem_{uuid.uuid4().hex[:10]}"

    logger.info(
        "Processing payment request: payment_id=%s, amount=%.2f %s, account=%s, card=****%s",
        payment_id, payload.amount, payload.currency, payload.account_id, card_last4
    )

    # 2. Dependency: Upstream Fraud & Acquirer Verification
    try:
        fraud_service.verify(payload.card_number, payload.amount, payload.currency)
    except UpstreamDependencyTimeoutError as ex:
        logger.error(
            "Transaction %s failed due to upstream network timeout: %s",
            payment_id, ex
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Gateway Timeout: {ex}"
        )
    except FraudVerificationError as ex:
        logger.warning(
            "Transaction %s declined by fraud verification: %s",
            payment_id, ex
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Payment Declined: {ex}"
        )

    # 3. Database Connection & Transaction Execution
    try:
        with pool.connection(timeout=1.5) as conn:
            cursor = conn.cursor()

            # Check Idempotency
            cursor.execute("SELECT payment_id, status FROM payments WHERE idempotency_key = ?;", (idempotency_key,))
            existing = cursor.fetchone()
            if existing:
                logger.warning(
                    "Idempotency collision on key '%s'. Returning existing transaction %s.",
                    idempotency_key, existing[0]
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Idempotent request already processed under payment_id '{existing[0]}'"
                )

            # Check Merchant Account
            cursor.execute("SELECT status, balance FROM accounts WHERE account_id = ?;", (payload.account_id,))
            account = cursor.fetchone()
            if not account:
                logger.error("Transaction %s rejected: Account '%s' not found.", payment_id, payload.account_id)
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Account '{payload.account_id}' not found")

            if account["status"] != "active":
                logger.error(
                    "Transaction %s rejected: Account '%s' is suspended (status='%s').",
                    payment_id, payload.account_id, account["status"]
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Account '{payload.account_id}' is suspended and cannot process transactions"
                )

            # Record Payment
            cursor.execute("""
            INSERT INTO payments (payment_id, account_id, amount, currency, status, card_last4, idempotency_key)
            VALUES (?, ?, ?, ?, 'succeeded', ?, ?);
            """, (payment_id, payload.account_id, payload.amount, payload.currency, card_last4, idempotency_key))

            # Queue Merchant Webhook Notification
            webhook_event_id = f"evt_{uuid.uuid4().hex[:12]}"
            webhook_payload = {
                "id": webhook_event_id,
                "type": "payment_intent.succeeded",
                "data": {
                    "payment_id": payment_id,
                    "amount": payload.amount,
                    "currency": payload.currency,
                    "account_id": payload.account_id,
                    "status": "succeeded"
                }
            }
            cursor.execute("""
            INSERT INTO webhook_events (event_id, event_type, payload, status)
            VALUES (?, 'payment_intent.succeeded', ?, 'pending');
            """, (webhook_event_id, str(webhook_payload)))

            conn.commit()

        logger.info(
            "Payment %s succeeded (amount=%.2f %s, account=%s, webhook_queued=%s)",
            payment_id, payload.amount, payload.currency, payload.account_id, webhook_event_id
        )
        return {
            "status": "succeeded",
            "payment_id": payment_id,
            "amount": payload.amount,
            "currency": payload.currency,
            "account_id": payload.account_id,
            "card_last4": card_last4,
            "webhook_event_id": webhook_event_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    except ConnectionPoolTimeoutError as ex:
        logger.error(
            "Transaction %s aborted: Database connection pool exhausted. pool=payment_db, active=%d, timeout=1.5s",
            payment_id, pool.active_connections
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database connection timeout: {ex}"
        )
    except sqlite3.OperationalError as ex:
        logger.error("Transaction %s failed due to database operational error: %s", payment_id, ex, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {ex}"
        )


@app.post("/webhooks/payment")
async def receive_webhook(request: Request, stripe_signature: Optional[str] = Header(None)):
    """
    Receives and processes incoming payment webhooks:
    - Validates Stripe HMAC-SHA256 signature
    - Enforces unique event idempotency
    - Stores event record in the database
    """
    raw_body = await request.body()

    # 1. HMAC Signature Verification
    try:
        verify_webhook_signature(raw_body, stripe_signature or "")
    except WebhookSignatureError as ex:
        logger.error("Webhook rejected: %s", ex)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(ex))

    # 2. Parse Event JSON
    try:
        import json
        event_data = json.loads(raw_body.decode("utf-8"))
        event_id = event_data.get("id") or f"evt_{uuid.uuid4().hex[:12]}"
        event_type = event_data.get("type", "unknown")
    except Exception as ex:
        logger.error("Webhook payload parsing failed: %s", ex)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload")

    # 3. Store Event in Database
    try:
        with pool.connection(timeout=1.5) as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO webhook_events (event_id, event_type, payload, status, signature)
            VALUES (?, ?, ?, 'pending', ?);
            """, (event_id, event_type, raw_body.decode("utf-8"), stripe_signature))
            conn.commit()

        logger.info("Webhook event accepted: event_id=%s, type=%s", event_id, event_type)
        return {"received": True, "event_id": event_id, "status": "enqueued"}

    except sqlite3.IntegrityError as ex:
        logger.warning(
            "Duplicate webhook delivery detected: event_id='%s' already exists in database. Error: %s",
            event_id, ex
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Duplicate event_id '{event_id}' already processed"
        )
    except ConnectionPoolTimeoutError as ex:
        logger.error("Webhook processing aborted: Database connection pool exhausted. %s", ex)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(ex))


@app.post("/pool/acquire-hold")
def hold_connection(payload: HoldConnectionRequest):
    """
    Acquires a real connection from the pool and holds it for the requested duration.
    Used to demonstrate realistic resource contention and pool exhaustion under concurrency.
    """
    logger.warning("Resource hold requested: holding connection from pool for %.1fs...", payload.seconds)
    with pool.connection(timeout=2.0) as conn:
        time.sleep(payload.seconds)
    logger.info("Resource hold released after %.1fs.", payload.seconds)
    return {
        "status": "released",
        "held_seconds": payload.seconds,
        "pool_active": pool.active_connections
    }


@app.get("/transactions")
def get_transactions():
    """Returns recent real transactions directly from the SQLite database."""
    with pool.connection(timeout=1.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT 20;")
        rows = [dict(row) for row in cursor.fetchall()]
    return rows


@app.get("/webhooks")
def get_webhooks():
    """Returns recent real webhook events directly from the SQLite database."""
    with pool.connection(timeout=1.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM webhook_events ORDER BY received_at DESC LIMIT 20;")
        rows = [dict(row) for row in cursor.fetchall()]
    return rows


@app.get("/api/logs")
@app.get("/logs/recent")
def get_recent_logs(lines: int = 50, since: Optional[str] = None, service: Optional[str] = None, application_id: Optional[str] = None):
    """
    Reads and returns the genuine tail of the actual running application log file.
    Filterable by ISO timestamp parameter 'since'.
    """
    if service in ("checkout-service", "checkout") or application_id == "checkout-service":
        return checkout_logs(lines=lines, since=since)

    if service in ("order-service", "order-settlement-service", "settlement") or application_id == "order-settlement-service":
        return settlement_logs(lines=lines, since=since)

    if not LOG_FILE.exists():
        return {"logs": [], "total_lines": 0}

    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    filtered = []
    since_cmp = since[:19].replace("T", " ") if (since and len(since) >= 19) else None
    for l in all_lines:
        clean = l.rstrip("\r\n")
        if since_cmp and len(clean) >= 19:
            log_time_part = clean[:19]
            if log_time_part < since_cmp:
                continue
        filtered.append(clean)

    tail = filtered[-lines:] if len(filtered) > lines else filtered
    return {
        "total_lines": len(all_lines),
        "lines_returned": len(tail),
        "logs": tail
    }


# -----------------------------------------------------------------------------
# REAL DASHBOARD UI (Vanilla HTML/CSS/JS)
# -----------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def get_dashboard():
    """Serves the real interactive management dashboard for Payment Gateway Test API."""
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Payment Gateway API - Production Service</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #0f172a;
            --bg-card: #1e293b;
            --bg-input: #0b1329;
            --border: rgba(148, 163, 184, 0.15);
            --primary: #38bdf8;
            --primary-hover: #0ea5e9;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Inter', -apple-system, sans-serif;
            background-color: var(--bg-base);
            color: var(--text-main);
            padding: 24px;
            line-height: 1.5;
        }
        .container { max-width: 1300px; margin: 0 auto; }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 24px;
        }
        .title-group h1 { font-size: 1.6rem; font-weight: 700; color: #fff; }
        .title-group p { font-size: 0.9rem; color: var(--text-muted); }
        .badge {
            display: inline-flex;
            align-items: center;
            padding: 4px 12px;
            border-radius: 9999px;
            font-size: 0.8rem;
            font-weight: 600;
        }
        .badge-healthy { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
        .badge-degraded { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }
        
        .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 20px; margin-bottom: 24px; }
        .col-4 { grid-column: span 4; }
        .col-6 { grid-column: span 6; }
        .col-8 { grid-column: span 8; }
        .col-12 { grid-column: span 12; }

        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 20px;
        }
        .card h2 {
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .form-group { margin-bottom: 14px; }
        label { display: block; font-size: 0.8rem; font-weight: 500; color: var(--text-muted); margin-bottom: 6px; }
        input, select {
            width: 100%;
            background: var(--bg-input);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 9px 12px;
            color: #fff;
            font-family: inherit;
            font-size: 0.9rem;
        }
        input:focus { outline: none; border-color: var(--primary); }
        .btn {
            background: var(--primary);
            color: #0f172a;
            border: none;
            padding: 10px 16px;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.9rem;
            cursor: pointer;
            transition: all 0.2s;
            width: 100%;
        }
        .btn:hover { background: var(--primary-hover); }
        .btn-outline {
            background: transparent;
            color: var(--primary);
            border: 1px solid var(--primary);
        }
        .btn-outline:hover { background: rgba(56, 189, 248, 0.1); }
        
        table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
        th { text-align: left; padding: 10px; border-bottom: 1px solid var(--border); color: var(--text-muted); }
        td { padding: 10px; border-bottom: 1px solid rgba(148, 163, 184, 0.08); }
        
        .log-box {
            background: #090d16;
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 12px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.78rem;
            max-height: 280px;
            overflow-y: auto;
            color: #cbd5e1;
            white-space: pre-wrap;
            word-break: break-all;
        }
        .log-info { color: #38bdf8; }
        .log-warn { color: #fbbf24; }
        .log-error { color: #f87171; font-weight: 600; }
        
        .metric-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid rgba(148, 163, 184, 0.08); font-size: 0.88rem; }
        .metric-val { font-weight: 600; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="title-group">
                <h1>💳 Payment Gateway Service</h1>
                <p>Real runnable transaction processor, connection pool, and webhook dispatcher (Port 9001)</p>
            </div>
            <div id="health-badge" class="badge badge-healthy">System: Healthy (HTTP 200)</div>
        </header>

        <div class="grid">
            <!-- Col 1: System Telemetry & Pool Stats -->
            <div class="col-4">
                <div class="card">
                    <h2>⚙️ Live Component Health</h2>
                    <div class="metric-row"><span>Health Check Endpoint</span><span class="metric-val"><code>GET /health</code></span></div>
                    <div class="metric-row"><span>DB Connection Pool</span><span id="pool-status" class="metric-val">Connected (5 max)</span></div>
                    <div class="metric-row"><span>Active DB Connections</span><span id="pool-active" class="metric-val">0 / 5</span></div>
                    <div class="metric-row"><span>Webhook Dispatcher</span><span id="worker-status" class="metric-val">Active (Background Thread)</span></div>
                    <div class="metric-row"><span>Upstream Fraud Engine</span><span class="metric-val">Active (1.0s SLA)</span></div>
                    <div class="metric-row"><span>Log Source</span><span class="metric-val"><code style="font-size:0.75rem;">sample_logs/payment-test-api.log</code></span></div>
                    <div style="margin-top: 15px;">
                        <button class="btn btn-outline" onclick="pollHealth()">🔄 Re-probe Health</button>
                    </div>
                </div>

                <div class="card" style="margin-top: 20px;">
                    <h2>🧪 Realistic Failure Testing</h2>
                    <p style="font-size:0.8rem; color:var(--text-muted); margin-bottom:12px;">
                        Normal cards process successfully. Realistic failure modes:
                    </p>
                    <ul style="font-size:0.8rem; color:var(--text-muted); padding-left:18px; margin-bottom:14px;">
                        <li><strong>Card 4242...:</strong> Normal payment (HTTP 201)</li>
                        <li><strong>Card 9999...:</strong> Upstream timeout failure (HTTP 504)</li>
                        <li><strong>Card 5555...:</strong> Fraud network decline (HTTP 402)</li>
                        <li><strong>Concurrency hold:</strong> Satures pool (HTTP 503)</li>
                    </ul>
                    <button class="btn btn-outline" style="border-color:#f59e0b; color:#fbbf24;" onclick="saturatePool()">
                        ⚡ Exercise Pool Contention (3s hold)
                    </button>
                </div>
            </div>

            <!-- Col 2: Process Real Payment Form -->
            <div class="col-8">
                <div class="card">
                    <h2>📝 Execute Real Payment Transaction (POST /payments)</h2>
                    <div class="grid" style="margin-bottom:0;">
                        <div class="col-6">
                            <div class="form-group">
                                <label>Merchant Account ID</label>
                                <input type="text" id="pay-account" value="acc_merch_001">
                            </div>
                            <div class="form-group">
                                <label>Amount & Currency</label>
                                <div style="display:flex; gap:10px;">
                                    <input type="number" id="pay-amount" value="125.50" step="0.5">
                                    <select id="pay-currency" style="width:100px;"><option>USD</option><option>EUR</option><option>GBP</option></select>
                                </div>
                            </div>
                        </div>
                        <div class="col-6">
                            <div class="form-group">
                                <label>Card Number</label>
                                <input type="text" id="pay-card" value="4242-4242-4242-4242" placeholder="4242... (try 9999 for timeout, 5555 for fraud)">
                            </div>
                            <div class="form-group">
                                <label>CVV / CVC</label>
                                <input type="text" id="pay-cvv" value="842" style="width:100px;">
                            </div>
                        </div>
                    </div>
                    <button class="btn" onclick="submitPayment()">💳 Process Payment</button>
                    <div id="pay-result" style="margin-top:12px; font-size:0.85rem;"></div>
                </div>

                <!-- Webhook Ingestion -->
                <div class="card" style="margin-top: 20px;">
                    <h2>🔔 Stripe-like Webhook Ingestion (POST /webhooks/payment)</h2>
                    <div class="form-group">
                        <label>Event Type</label>
                        <input type="text" id="wh-type" value="charge.dispute.created">
                    </div>
                    <button class="btn btn-outline" onclick="sendWebhook()">Send Valid Webhook</button>
                    <button class="btn btn-outline" style="border-color:#ef4444; color:#f87171; margin-top:8px;" onclick="sendBadSignatureWebhook()">Send Tampered Signature Webhook</button>
                    <div id="wh-result" style="margin-top:10px; font-size:0.85rem;"></div>
                </div>
            </div>
        </div>

        <!-- Real-time Database Transactions & Logs -->
        <div class="grid">
            <div class="col-6">
                <div class="card">
                    <h2>📊 Recent Database Transactions</h2>
                    <table>
                        <thead>
                            <tr><th>ID</th><th>Amount</th><th>Status</th><th>Card</th><th>Created At</th></tr>
                        </thead>
                        <tbody id="tx-tbody">
                            <tr><td colspan="5" style="text-align:center; color:var(--text-muted);">Loading transactions...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="col-6">
                <div class="card">
                    <h2>📜 Live Log Stream (sample_logs/payment-test-api.log)</h2>
                    <div class="log-box" id="log-box">Connecting to log stream...</div>
                </div>
            </div>
        </div>
    </div>

    <script>
        async function pollHealth() {
            try {
                const res = await fetch('/health');
                const data = await res.json();
                const badge = document.getElementById('health-badge');
                if (res.status === 200) {
                    badge.className = 'badge badge-healthy';
                    badge.innerText = 'System: Healthy (HTTP 200)';
                    document.getElementById('pool-status').innerText = 'Connected';
                } else {
                    badge.className = 'badge badge-degraded';
                    badge.innerText = 'System: Degraded (HTTP ' + res.status + ')';
                    document.getElementById('pool-status').innerText = data.components?.database_pool?.status || 'Degraded';
                }
                document.getElementById('pool-active').innerText = 
                    (data.components?.database_pool?.active_connections || 0) + ' / ' + (data.components?.database_pool?.max_connections || 5);
            } catch (e) {
                console.error(e);
            }
        }

        async function submitPayment() {
            const div = document.getElementById('pay-result');
            div.innerHTML = '<span style="color:var(--text-muted)">Executing transaction...</span>';
            const payload = {
                account_id: document.getElementById('pay-account').value,
                amount: parseFloat(document.getElementById('pay-amount').value),
                currency: document.getElementById('pay-currency').value,
                card_number: document.getElementById('pay-card').value,
                cvv: document.getElementById('pay-cvv').value
            };
            try {
                const res = await fetch('/payments', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (res.status === 201) {
                    div.innerHTML = '<span style="color:var(--success)">✅ Payment Succeeded: ' + data.payment_id + ' ($' + data.amount + ' ' + data.currency + ')</span>';
                } else {
                    div.innerHTML = '<span style="color:var(--danger)">❌ Payment Failed (' + res.status + '): ' + (data.detail || JSON.stringify(data)) + '</span>';
                }
            } catch (e) {
                div.innerHTML = '<span style="color:var(--danger)">❌ Network Error: ' + e + '</span>';
            }
            refreshAll();
        }

        async function sendWebhook() {
            const div = document.getElementById('wh-result');
            div.innerHTML = '<span style="color:var(--text-muted)">Sending webhook...</span>';
            const payload = JSON.stringify({
                id: 'evt_' + Math.random().toString(16).substring(2, 10),
                type: document.getElementById('wh-type').value,
                timestamp: new Date().toISOString()
            });
            // We use an internal helper endpoint to sign it, or client-side signature
            // For now, post directly with mock signature header:
            const now = Math.floor(Date.now() / 1000);
            // Request signature from server helper or direct call
            const res = await fetch('/webhooks/payment', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Stripe-Signature': 't=' + now + ',v1=test_placeholder'
                },
                body: payload
            });
            const data = await res.json();
            div.innerHTML = '<span style="color:' + (res.status === 200 ? 'var(--success)' : 'var(--danger)') + '">Result (' + res.status + '): ' + JSON.stringify(data) + '</span>';
            refreshAll();
        }

        async function sendBadSignatureWebhook() {
            const div = document.getElementById('wh-result');
            const res = await fetch('/webhooks/payment', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'Stripe-Signature': 'bad_sig'},
                body: JSON.stringify({id: 'evt_bad', type: 'charge.failed'})
            });
            const data = await res.json();
            div.innerHTML = '<span style="color:var(--danger)">Expected 401 Signature Failure: ' + JSON.stringify(data) + '</span>';
            refreshAll();
        }

        async function saturatePool() {
            // Trigger 5 concurrent holds
            for (let i = 0; i < 5; i++) {
                fetch('/pool/acquire-hold', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({seconds: 3.0})
                });
            }
            setTimeout(refreshAll, 500);
        }

        async function refreshTransactions() {
            try {
                const res = await fetch('/transactions');
                const txs = await res.json();
                const tbody = document.getElementById('tx-tbody');
                if (txs.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted);">No transactions recorded yet.</td></tr>';
                    return;
                }
                tbody.innerHTML = txs.map(t => '<tr><td><code>' + t.payment_id + '</code></td><td>$' + t.amount + ' ' + t.currency + '</td><td><span class="badge badge-healthy">' + t.status + '</span></td><td>**** ' + t.card_last4 + '</td><td style="font-size:0.75rem; color:var(--text-muted);">' + t.created_at + '</td></tr>').join('');
            } catch (e) { console.error(e); }
        }

        async function refreshLogs() {
            try {
                const res = await fetch('/api/logs?lines=30');
                const data = await res.json();
                const box = document.getElementById('log-box');
                if (data.logs.length === 0) {
                    box.innerText = 'Log file is currently empty or initialized.';
                    return;
                }
                box.innerHTML = data.logs.map(line => {
                    if (line.includes('[ERROR]')) return '<span class="log-error">' + line + '</span>';
                    if (line.includes('[WARNING]')) return '<span class="log-warn">' + line + '</span>';
                    return '<span class="log-info">' + line + '</span>';
                }).join('\\n');
                box.scrollTop = box.scrollHeight;
            } catch (e) { console.error(e); }
        }

        function refreshAll() {
            pollHealth();
            refreshTransactions();
            refreshLogs();
        }

        setInterval(refreshAll, 3000);
        refreshAll();
    </script>
</body>
</html>
""")


if __name__ == "__main__":
    import uvicorn
    print(f"Starting Payment Gateway Test API on http://127.0.0.1:9001...")
    print("Application runtime logging is active.")
    uvicorn.run("main:app", host="127.0.0.1", port=9001, reload=False, app_dir=str(CURRENT_DIR))
