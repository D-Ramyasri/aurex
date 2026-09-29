"""
Unit and Integration Tests for Real Production-Grade Payment Gateway Test API.
Tests:
1. Health check operational state (HTTP 200)
2. Normal payment processing (HTTP 201) with database persistence
3. Idempotency collision check (HTTP 409)
4. Upstream dependency network timeout (HTTP 504)
5. Fraud check decline (HTTP 402)
6. Webhook HMAC-SHA256 signature verification (Valid -> 200, Tampered -> 401)
7. Duplicate webhook event delivery handling (HTTP 409)
8. Real Database Connection Pool Exhaustion (HTTP 503)
9. Real on-disk runtime log verification (contains actual timestamps, req_ids, and error context)
"""

import os
import json
import time
import uuid
import threading
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from test_application.main import app, LOG_FILE
from test_application.database import pool
from test_application.services import generate_webhook_signature, webhook_worker


class TestProductionPaymentGateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Initialize pool and tables
        pool.initialize()
        webhook_worker.start()

    @classmethod
    def tearDownClass(cls):
        webhook_worker.stop()

    def setUp(self):
        self.client = TestClient(app)

    def test_health_check_operational(self):
        """GET /health must probe real DB and return HTTP 200 when operational."""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["service"], "payment-gateway-test")
        self.assertIn("database_pool", data["components"])
        self.assertEqual(data["components"]["database_pool"]["status"], "operational")

    def test_successful_payment_processing(self):
        """POST /payments processes real transaction, writes to SQLite, queues webhook."""
        idem = f"idem_test_{uuid.uuid4().hex[:8]}"
        payload = {
            "account_id": "acc_merch_001",
            "amount": 89.50,
            "currency": "USD",
            "card_number": "4242-4242-4242-4242",
            "cvv": "123",
            "idempotency_key": idem
        }
        resp = self.client.post("/payments", json=payload)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "succeeded")
        self.assertEqual(data["amount"], 89.50)
        self.assertEqual(data["card_last4"], "4242")

        # Verify transaction actually exists in SQLite database
        with pool.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, amount FROM payments WHERE payment_id = ?;", (data["payment_id"],))
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "succeeded")
            self.assertEqual(row[1], 89.50)

    def test_idempotency_collision(self):
        """Sending the exact same idempotency_key triggers real 409 conflict."""
        idem = f"idem_collision_{uuid.uuid4().hex[:8]}"
        payload = {
            "account_id": "acc_merch_001",
            "amount": 25.00,
            "currency": "USD",
            "card_number": "4242-4242-4242-4242",
            "cvv": "123",
            "idempotency_key": idem
        }
        resp1 = self.client.post("/payments", json=payload)
        self.assertEqual(resp1.status_code, 201)

        # Second request with identical idempotency_key
        resp2 = self.client.post("/payments", json=payload)
        self.assertEqual(resp2.status_code, 409)
        self.assertIn("already processed", resp2.json()["detail"])

    def test_upstream_dependency_timeout(self):
        """Card starting with 9999 triggers real upstream fraud network timeout (HTTP 504)."""
        payload = {
            "account_id": "acc_merch_001",
            "amount": 500.00,
            "currency": "USD",
            "card_number": "9999-1234-5678-9012",
            "cvv": "999"
        }
        t0 = time.time()
        resp = self.client.post("/payments", json=payload)
        elapsed = time.time() - t0
        self.assertEqual(resp.status_code, 504)
        self.assertGreaterEqual(elapsed, 1.0)
        self.assertIn("Gateway Timeout", resp.json()["detail"])

    def test_fraud_verification_rejection(self):
        """Card starting with 5555 triggers real fraud network decline (HTTP 402)."""
        payload = {
            "account_id": "acc_merch_001",
            "amount": 1200.00,
            "currency": "USD",
            "card_number": "5555-4321-8765-4321",
            "cvv": "555"
        }
        resp = self.client.post("/payments", json=payload)
        self.assertEqual(resp.status_code, 402)
        self.assertIn("Payment Declined", resp.json()["detail"])

    def test_webhook_signature_verification(self):
        """Tests valid vs tampered HMAC-SHA256 signature on POST /webhooks/payment."""
        event_id = f"evt_{uuid.uuid4().hex[:10]}"
        payload_data = {
            "id": event_id,
            "type": "payment_intent.succeeded",
            "data": {"amount": 5000}
        }
        payload_str = json.dumps(payload_data)

        # 1. Valid signature
        sig_header = generate_webhook_signature(payload_str)
        resp_valid = self.client.post(
            "/webhooks/payment",
            content=payload_str,
            headers={"Content-Type": "application/json", "Stripe-Signature": sig_header}
        )
        self.assertEqual(resp_valid.status_code, 200)
        self.assertTrue(resp_valid.json()["received"])

        # 2. Tampered signature -> HTTP 401
        bad_sig = "t=1234567,v1=tampered_signature_hash"
        resp_bad = self.client.post(
            "/webhooks/payment",
            content=payload_str,
            headers={"Content-Type": "application/json", "Stripe-Signature": bad_sig}
        )
        self.assertEqual(resp_bad.status_code, 401)

        # 3. Duplicate event delivery -> HTTP 409
        resp_dup = self.client.post(
            "/webhooks/payment",
            content=payload_str,
            headers={"Content-Type": "application/json", "Stripe-Signature": sig_header}
        )
        self.assertEqual(resp_dup.status_code, 409)

    def test_real_connection_pool_exhaustion(self):
        """Acquiring all 5 connections from pool causes subsequent requests to fail with HTTP 503."""
        conns = []
        try:
            # Sature the pool by acquiring all max_connections (5)
            for _ in range(pool.max_connections):
                conns.append(pool.acquire(timeout=0.5))

            # Attempt a payment while pool is 100% saturated
            payload = {
                "account_id": "acc_merch_001",
                "amount": 10.00,
                "currency": "USD",
                "card_number": "4242-0000-0000-0000",
                "cvv": "100"
            }
            resp = self.client.post("/payments", json=payload)
            self.assertEqual(resp.status_code, 503)
            self.assertIn("Database connection timeout", resp.json()["detail"])

            # /health must also reflect degraded status with HTTP 503
            health_resp = self.client.get("/health")
            self.assertEqual(health_resp.status_code, 503)
            self.assertEqual(health_resp.json()["status"], "degraded")

        finally:
            # Release all connections to restore health
            for c in conns:
                pool.release(c)

        # Verify pool is restored and healthy again
        restore_health = self.client.get("/health")
        self.assertEqual(restore_health.status_code, 200)
        self.assertEqual(restore_health.json()["status"], "healthy")

    def test_real_runtime_log_verification(self):
        """Verifies that actual runtime logs on disk contain real timestamps, req_ids, and error context."""
        self.assertTrue(LOG_FILE.exists(), f"Log file must exist at {LOG_FILE}")
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        # Check for genuine log patterns produced by real runtime execution
        self.assertIn("[payment-gateway-test", content)
        self.assertIn("req_id=", content)
        self.assertIn("Database pool initialized", content)
        self.assertIn("Connection pool timeout", content)


if __name__ == "__main__":
    unittest.main()
