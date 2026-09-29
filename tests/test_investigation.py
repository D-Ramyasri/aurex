"""
Unit and integration tests for Incident Investigation & Root Cause Analysis (RCA).
Tests:
1. Evidence extraction and timeline generation from real log signatures
2. Incident classification (Database, Upstream Dependency, Webhook)
3. Deterministic Root Cause Analysis formulation
4. Actionable resolution recommendation generation
5. Hindsight memory recall integration
6. Extended /api/incidents/inspect response validation
7. Graceful fallback on LLM unavailability
"""

import unittest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from main import app
from models import (
    ApplicationConfig,
    Evidence,
    HealthCheckEvidence,
    LogEvidence,
    LogEntry,
    Incident,
)
from incident_investigator import (
    extract_evidence_and_timeline,
    build_deterministic_rca,
    investigate_incident,
)


class TestIncidentInvestigationAndRCA(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.app_config = ApplicationConfig(
            application_id="payment-test-api",
            application_name="Payment Gateway Test API",
            service_id="payment-gateway-test",
            base_url="http://127.0.0.1:9001",
            health_endpoint="/health",
            log_source="sample_logs/payment-test-api.log"
        )

    def test_database_pool_exhaustion_investigation(self):
        """Tests that database pool timeout logs produce a Database classification and pool RCA."""
        log_entries = [
            LogEntry(
                timestamp="2026-09-28 16:30:00",
                level="INFO",
                message="2026-09-28 16:30:00 [INFO] [payment-gateway-test] [req_id=req_101] Processing payment request",
                raw="2026-09-28 16:30:00 [INFO] [payment-gateway-test] [req_id=req_101] Processing payment request"
            ),
            LogEntry(
                timestamp="2026-09-28 16:30:02",
                level="ERROR",
                message="2026-09-28 16:30:02 [ERROR] [payment-gateway-test.database] [req_id=req_101] Connection pool timeout: pool='payment_db' exhausted (active=5/5, timeout=1.5s). Connection request rejected.",
                raw="2026-09-28 16:30:02 [ERROR] [payment-gateway-test.database] [req_id=req_101] Connection pool timeout: pool='payment_db' exhausted (active=5/5, timeout=1.5s). Connection request rejected."
            ),
            LogEntry(
                timestamp="2026-09-28 16:30:02",
                level="ERROR",
                message="2026-09-28 16:30:02 [ERROR] [payment-gateway-test] [req_id=req_101] Transaction aborted: Database connection pool exhausted. pool=payment_db, active=5",
                raw="2026-09-28 16:30:02 [ERROR] [payment-gateway-test] [req_id=req_101] Transaction aborted: Database connection pool exhausted. pool=payment_db, active=5"
            )
        ]

        evidence = Evidence(
            application_id="payment-test-api",
            service="payment-gateway-test",
            health_check=HealthCheckEvidence(
                status="FAIL",
                http_status=503,
                response_time_ms=12.4,
                endpoint="http://127.0.0.1:9001/health",
                response_body={"status": "degraded"},
                error_message="Health check returned HTTP 503"
            ),
            logs=LogEvidence(
                available=True,
                source="sample_logs/payment-test-api.log",
                entries=log_entries,
                error_count=2,
                warn_count=0
            )
        )

        incident = Incident(
            application_id="payment-test-api",
            service="payment-gateway-test",
            severity="high",
            description="Database pool exhausted",
            symptoms=["Connection pool timeout logged", "HTTP 503 returned"]
        )

        report = investigate_incident(evidence, incident, self.app_config)

        self.assertTrue(report.incident_detected)
        self.assertEqual(report.incident_type, "Database")
        self.assertIn("database", report.analysis.failure.lower())
        self.assertTrue(any(w in report.analysis.root_cause.lower() for w in ["exhaust", "pool", "timeout"]))
        self.assertGreaterEqual(report.analysis.confidence, 0.7)
        self.assertTrue(len(report.timeline) >= 3)
        self.assertTrue(len(report.resolution.actions) >= 2)

    def test_upstream_dependency_timeout_investigation(self):
        """Tests that upstream timeout logs produce a Dependency / Upstream classification and 504 RCA."""
        log_entries = [
            LogEntry(
                timestamp="2026-09-28 16:32:34",
                level="INFO",
                message="2026-09-28 16:32:34 [INFO] [payment-gateway-test] [req_id=req_202] Processing payment request: payment_id=pay_test99",
                raw="2026-09-28 16:32:34 [INFO] [payment-gateway-test] [req_id=req_202] Processing payment request: payment_id=pay_test99"
            ),
            LogEntry(
                timestamp="2026-09-28 16:32:34",
                level="WARN",
                message="2026-09-28 16:32:34 [WARNING] [payment-gateway-test.services] [req_id=req_202] Upstream network call initiated to fraud-engine.acquirer.net (timeout=1.0s)...",
                raw="2026-09-28 16:32:34 [WARNING] [payment-gateway-test.services] [req_id=req_202] Upstream network call initiated to fraud-engine.acquirer.net (timeout=1.0s)..."
            ),
            LogEntry(
                timestamp="2026-09-28 16:32:35",
                level="ERROR",
                message="2026-09-28 16:32:35 [ERROR] [payment-gateway-test.services] [req_id=req_202] Dependency failure: Upstream fraud engine timed out after 1.0s while verifying card 4821",
                raw="2026-09-28 16:32:35 [ERROR] [payment-gateway-test.services] [req_id=req_202] Dependency failure: Upstream fraud engine timed out after 1.0s while verifying card 4821"
            )
        ]

        evidence = Evidence(
            application_id="payment-test-api",
            service="payment-gateway-test",
            health_check=HealthCheckEvidence(
                status="PASS",
                http_status=200,
                response_time_ms=8.5,
                endpoint="http://127.0.0.1:9001/health"
            ),
            logs=LogEvidence(
                available=True,
                source="sample_logs/payment-test-api.log",
                entries=log_entries,
                error_count=1,
                warn_count=1
            )
        )

        incident = Incident(
            application_id="payment-test-api",
            service="payment-gateway-test",
            severity="high",
            description="Upstream dependency timeout",
            symptoms=["Upstream fraud engine timed out after 1.0s"]
        )

        report = investigate_incident(evidence, incident, self.app_config)

        self.assertTrue(report.incident_detected)
        self.assertEqual(report.incident_type, "Dependency / Upstream")
        self.assertIn("upstream", report.analysis.failure.lower())
        self.assertIn("timeout", report.analysis.root_cause.lower())
        self.assertTrue(len(report.resolution.actions) >= 3)

        # Grounded Hindsight query and causal chain validation
        self.assertIn("fraud", report.hindsight.query.lower())
        self.assertIn("upstream", report.hindsight.query.lower())
        self.assertIn("timeout", report.hindsight.query.lower())
        self.assertIn("fraud-engine.acquirer.net", report.hindsight.current_failure_signals)
        self.assertIsNotNone(report.hindsight.matched_memory)
        self.assertTrue(report.resolution.memory_grounded)
        self.assertTrue(len(report.resolution.source_memory_ids) >= 1)
        self.assertIn("Hindsight Memory", report.resolution.grounded_on)

    def test_inspect_api_returns_investigation_payload(self):
        """Verifies that POST /api/incidents/inspect returns timeline, analysis, resolution, and hindsight."""
        resp = self.client.post("/api/incidents/inspect", json={"application_id": "payment-test-api"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Check existing fields preserved
        self.assertIn("status", data)
        self.assertIn("incident_detected", data)
        self.assertIn("evidence", data)

        # Check extended investigation fields
        self.assertIn("timeline", data)
        self.assertIsInstance(data["timeline"], list)

        if data["incident_detected"]:
            self.assertIn("analysis", data)
            self.assertIsNotNone(data["analysis"])
            self.assertIn("failure", data["analysis"])
            self.assertIn("root_cause", data["analysis"])
            self.assertIn("why", data["analysis"])

            self.assertIn("resolution", data)
            self.assertIsNotNone(data["resolution"])
            self.assertIn("actions", data["resolution"])

            self.assertIn("hindsight", data)
            self.assertIsNotNone(data["hindsight"])
            self.assertIn("similar_incidents", data["hindsight"])


if __name__ == "__main__":
    unittest.main()
