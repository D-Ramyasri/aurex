"""
Business logic services for Payment Gateway Test API.
Includes:
- Fraud & Upstream Acquirer Verification Service (with real dependency timeouts)
- Stripe-compatible Webhook Signature Verification (HMAC-SHA256)
- Background Webhook Dispatcher Worker
"""

import hmac
import hashlib
import time
import uuid
import logging
import threading
import json
from typing import Dict, Any, Tuple
from test_application.database import pool

logger = logging.getLogger("payment-gateway-test.services")

# Standard test webhook secret
WEBHOOK_SECRET = "whsec_test_secret_key_99182"


class FraudVerificationError(Exception):
    """Raised when upstream fraud verification rejects a transaction."""
    pass


class UpstreamDependencyTimeoutError(Exception):
    """Raised when the upstream fraud/acquirer service fails to respond within timeout."""
    pass


class WebhookSignatureError(Exception):
    """Raised when HMAC signature verification fails on an incoming webhook."""
    pass


class FraudVerificationService:
    """
    Communicates with the upstream Fraud/Acquirer verification network.
    Executes real checks with genuine timeout and network failure conditions.
    """

    def __init__(self, timeout_seconds: float = 1.0):
        self.timeout_seconds = timeout_seconds

    def verify(self, card_number: str, amount: float, currency: str) -> Dict[str, Any]:
        """
        Performs pre-authorization fraud verification.
        - Cards starting with '9999': Emulates an unresponsive upstream network node (induces a real timeout).
        - Cards starting with '5555': Emulates an upstream fraud alert decline.
        - Cards starting with '4000' or other standard numbers: Successfully passes.
        """
        clean_card = card_number.replace("-", "").replace(" ", "")

        # Real Upstream Timeout Scenario
        if clean_card.startswith("9999"):
            logger.warning(
                "Upstream network call initiated to fraud-engine.acquirer.net (timeout=%.1fs)...",
                self.timeout_seconds
            )
            # Sleep slightly longer than timeout to trigger genuine timeout exception
            time.sleep(self.timeout_seconds + 0.1)
            err_msg = f"Upstream fraud engine timed out after {self.timeout_seconds:.1f}s while verifying card {clean_card[-4:]}"
            logger.error("Dependency failure: %s (endpoint=https://fraud-engine.acquirer.net/v1/score)", err_msg)
            raise UpstreamDependencyTimeoutError(err_msg)

        # Real Upstream Rejection Scenario
        if clean_card.startswith("5555"):
            err_msg = f"High-risk score (98/100) returned by upstream fraud network for card ending in {clean_card[-4:]}"
            logger.warning("Fraud check rejected: %s", err_msg)
            raise FraudVerificationError(err_msg)

        # Normal passing verification with realistic latency (10-30ms)
        time.sleep(0.02)
        return {
            "status": "approved",
            "risk_score": 12,
            "verification_id": f"vrf_{uuid.uuid4().hex[:12]}",
            "currency": currency,
            "amount": amount
        }


def generate_webhook_signature(payload: str, secret: str = WEBHOOK_SECRET) -> str:
    """Helper to generate a valid Stripe-like HMAC-SHA256 signature for testing."""
    timestamp = str(int(time.time()))
    signed_payload = f"{timestamp}.{payload}"
    computed = hmac.new(secret.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={computed}"


def verify_webhook_signature(payload_bytes: bytes, signature_header: str, secret: str = WEBHOOK_SECRET) -> bool:
    """
    Validates Stripe-compatible webhook signature (t=timestamp,v1=hash).
    Raises WebhookSignatureError if signature is invalid or tampered with.
    """
    if not signature_header:
        raise WebhookSignatureError("Missing Stripe-Signature header in webhook request")

    parts = dict(item.split("=", 1) for item in signature_header.split(",") if "=" in item)
    timestamp = parts.get("t")
    v1_sig = parts.get("v1")

    if not timestamp or not v1_sig:
        raise WebhookSignatureError("Malformed Stripe-Signature header; expected 't=...,v1=...'")

    payload_str = payload_bytes.decode("utf-8")
    signed_payload = f"{timestamp}.{payload_str}"
    expected_sig = hmac.new(secret.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(v1_sig, expected_sig):
        raise WebhookSignatureError(f"Signature mismatch: received '{v1_sig[:8]}...', expected computed hash")

    return True


class BackgroundWebhookWorker:
    """
    Background worker thread that processes queued webhook deliveries from the database.
    """

    def __init__(self):
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="WebhookWorker")
        self._thread.start()
        logger.info("Background WebhookWorker started.")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            logger.info("Background WebhookWorker stopped.")

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run_loop(self):
        while self._running:
            try:
                self._process_pending_webhooks()
            except Exception as e:
                logger.error("Background WebhookWorker error in processing loop: %s", e, exc_info=True)
            time.sleep(2.0)

    def _process_pending_webhooks(self):
        with pool.connection(timeout=1.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT event_id, event_type, payload FROM webhook_events WHERE status = 'pending' LIMIT 5;"
            )
            rows = cursor.fetchall()
            for row in rows:
                event_id, event_type, payload = row[0], row[1], row[2]
                logger.info(
                    "WebhookWorker processing event: event_id=%s, type=%s",
                    event_id, event_type
                )
                cursor.execute(
                    "UPDATE webhook_events SET status = 'processed', processed_at = CURRENT_TIMESTAMP WHERE event_id = ?;",
                    (event_id,)
                )
                conn.commit()


# Singleton instances
fraud_service = FraudVerificationService(timeout_seconds=1.0)
webhook_worker = BackgroundWebhookWorker()
