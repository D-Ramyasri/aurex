"""
Comprehensive test suite for Feature 1: Dynamic Application Registration & Incident Ingestion.
Tests:
1. Application Registration & Persistence
2. Port and Configuration Isolation (Different ports per app)
3. Target Application Running (HTTP 200 -> Healthy)
4. Target Application Not Running (UNREACHABLE / Connection Error != HTTP 404)
5. Target Application Returns HTTP 404 (Reachable but 404 != Connection Refused)
6. Target Application Returns HTTP 503 (Incident Created with high severity)
7. Per-Application Log Source Isolation
8. Backend Route Validation (/api/applications and /api/incidents/inspect)
9. Log Path Traversal Security Validation
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import requests

from main import app
from models import ApplicationConfig, Evidence, HealthCheckEvidence, LogEntry, LogEvidence
from app_registry import (
    register_application,
    get_application,
    list_applications,
    delete_application,
    reset_registry,
)
from evidence_collector import (
    collect_health_check,
    collect_logs,
    collect_evidence,
)
from incident_detector import detect_incident
from incident_investigator import investigate_incident
from test_application.main import app as payment_test_app, forced_failure


class TestApplicationRegistrationAndIngestion(unittest.TestCase):
    def setUp(self):
        reset_registry()
        self.client = TestClient(app)

    # -------------------------------------------------------------------------
    # TEST 1: Application Registration
    # -------------------------------------------------------------------------
    def test_application_registration(self):
        new_app_data = {
            "application_id": "test-reg-app",
            "application_name": "Test Payment Gateway",
            "service_id": "payment-gateway",
            "base_url": "http://127.0.0.1:8001",
            "health_endpoint": "/health",
            "description": "Test registration app"
        }
        # 1. Test POST /api/applications
        resp = self.client.post("/api/applications", json=new_app_data)
        self.assertEqual(resp.status_code, 201)
        created = resp.json()
        self.assertTrue(created["success"])
        self.assertEqual(created["application"]["application_id"], "test-reg-app")
        self.assertEqual(created["application"]["base_url"], "http://127.0.0.1:8001")
        self.assertEqual(created["application"]["log_collection_method"], "http")
        self.assertEqual(set(created["application"]), {
            "application_id",
            "application_name",
            "service_id",
            "base_url",
            "health_endpoint",
            "log_collection_method",
            "description",
        })

        # 2. Test POST /api/applications/register alias
        new_app_data["application_id"] = "test-reg-alias"
        resp_alias = self.client.post("/api/applications/register", json=new_app_data)
        self.assertEqual(resp_alias.status_code, 201)
        self.assertTrue(resp_alias.json()["success"])

        # 3. Test duplicate update (same application_id)
        new_app_data["description"] = "Updated description"
        resp_update = self.client.post("/api/applications", json=new_app_data)
        self.assertEqual(resp_update.status_code, 201)
        self.assertEqual(resp_update.json()["application"]["description"], "Updated description")

        # Verify it appears in the listing
        list_resp = self.client.get("/api/applications")
        self.assertEqual(list_resp.status_code, 200)
        app_ids = [a["application_id"] for a in list_resp.json()]
        self.assertIn("test-reg-app", app_ids)
        self.assertIn("test-reg-alias", app_ids)

    # -------------------------------------------------------------------------
    # TEST 2: Different Application Configuration (Port Isolation)
    # -------------------------------------------------------------------------
    @patch("requests.get")
    def test_different_application_ports_isolation(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok"}
        mock_logs = MagicMock()
        mock_logs.status_code = 200
        mock_logs.json.return_value = {"logs": ["2026-09-28 14:00:00 [INFO] Order API healthy"]}
        mock_get.side_effect = [mock_resp, mock_logs]

        app_order = ApplicationConfig(
            application_id="order-api",
            application_name="Order API",
            service_id="order-service",
            base_url="http://127.0.0.1:9002",
            health_endpoint="/health"
        )
        register_application(app_order)

        # Inspect order-api
        inspect_resp = self.client.post("/api/incidents/inspect", json={"application_id": "order-api"})
        self.assertEqual(inspect_resp.status_code, 200)

        # Both signal requests must use this application's base URL.
        self.assertEqual(mock_get.call_args_list[0].args[0], "http://127.0.0.1:9002/health")
        self.assertEqual(mock_get.call_args_list[1].args[0], "http://127.0.0.1:9002/logs/recent")

    # -------------------------------------------------------------------------
    # TEST 3: Target Application Running (HTTP 200) -> Healthy
    # -------------------------------------------------------------------------
    @patch("requests.get")
    def test_target_application_running_healthy(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "healthy", "uptime": 3600}
        mock_logs = MagicMock()
        mock_logs.status_code = 200
        mock_logs.json.return_value = {"logs": ["2026-09-28 14:00:00 [INFO] Service healthy"]}
        mock_get.side_effect = [mock_resp, mock_logs]

        config = ApplicationConfig(
            application_id="healthy-app",
            application_name="Healthy Service",
            service_id="healthy-svc",
            base_url="http://127.0.0.1:9002",
            health_endpoint="/health"
        )
        register_application(config)

        evidence = collect_evidence(config)
        self.assertEqual(evidence.health_check.status, "PASS")
        self.assertEqual(evidence.health_check.http_status, 200)

        detected, reason, incident = detect_incident(evidence, config)
        self.assertFalse(detected)
        self.assertIsNone(incident)
        self.assertIn("health check passed", reason.lower())

    # -------------------------------------------------------------------------
    # TEST 4: Target Application Not Running (UNREACHABLE != HTTP 404)
    # -------------------------------------------------------------------------
    @patch("requests.get")
    def test_target_application_not_running_connection_refused(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError(
            "Failed to establish a new connection: [WinError 10061] No connection could be made because target machine actively refused it"
        )

        config = ApplicationConfig(
            application_id="offline-app",
            application_name="Offline Service",
            service_id="offline-svc",
            base_url="http://127.0.0.1:9999",
            health_endpoint="/health"
        )
        register_application(config)

        evidence = collect_evidence(config)
        self.assertEqual(evidence.health_check.status, "UNREACHABLE")
        self.assertIsNone(evidence.health_check.http_status)  # MUST NOT be 404
        self.assertIn("Connection refused", evidence.health_check.error_message)

        detected, reason, incident = detect_incident(evidence, config)
        self.assertTrue(detected)
        self.assertIsNotNone(incident)
        self.assertEqual(incident.severity, "critical")
        self.assertIn("unreachable", reason.lower())

    # -------------------------------------------------------------------------
    # TEST 5: Target Application Returns HTTP 404 (Reachable but 404)
    # -------------------------------------------------------------------------
    @patch("requests.get")
    def test_target_application_returns_http_404(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.json.side_effect = Exception("Not JSON")
        mock_resp.text = "404 page not found"
        mock_logs = MagicMock()
        mock_logs.status_code = 404
        mock_get.side_effect = [mock_resp, mock_logs]

        config = ApplicationConfig(
            application_id="wrong-endpoint-app",
            application_name="App with Wrong Health Endpoint",
            service_id="wrong-svc",
            base_url="http://127.0.0.1:9002",
            health_endpoint="/missing_health_route"
        )
        register_application(config)

        evidence = collect_evidence(config)
        self.assertEqual(evidence.health_check.status, "FAIL")
        self.assertEqual(evidence.health_check.http_status, 404)
        self.assertIn("404", evidence.health_check.error_message)

        detected, reason, incident = detect_incident(evidence, config)
        self.assertTrue(detected)
        self.assertEqual(incident.severity, "medium")
        self.assertIn("404", reason)

    # -------------------------------------------------------------------------
    # TEST 6: Target Application Returns HTTP 503 (Incident Created)
    # -------------------------------------------------------------------------
    @patch("requests.get")
    def test_target_application_returns_http_503(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.json.return_value = {"status": "unhealthy", "error": "Database connection pool saturated"}
        mock_logs = MagicMock()
        mock_logs.status_code = 200
        mock_logs.json.return_value = {"logs": ["2026-09-28 14:00:00 [ERROR] pool exhausted"]}
        mock_get.side_effect = [mock_resp, mock_logs]

        config = ApplicationConfig(
            application_id="pool-fail-app",
            application_name="Database Service",
            service_id="db-service",
            base_url="http://127.0.0.1:8001",
            health_endpoint="/health"
        )
        register_application(config)

        evidence = collect_evidence(config)
        self.assertEqual(evidence.health_check.status, "FAIL")
        self.assertEqual(evidence.health_check.http_status, 503)

        detected, reason, incident = detect_incident(evidence, config)
        self.assertTrue(detected)
        self.assertIsNotNone(incident)
        self.assertEqual(incident.severity, "high")
        self.assertTrue(incident.incident_id.startswith("INC-"))
        self.assertTrue(len(incident.symptoms) > 0)
        self.assertTrue(len(incident.errors) > 0)

    @patch("requests.get")
    def test_target_application_returns_http_500_distinctly(self, mock_get):
        health_resp = MagicMock(status_code=500)
        health_resp.json.return_value = {"status": "error"}
        logs_resp = MagicMock(status_code=200)
        logs_resp.json.return_value = {"logs": []}
        mock_get.side_effect = [health_resp, logs_resp]

        config = ApplicationConfig(
            application_id="server-error-app",
            application_name="Server Error Service",
            service_id="server-error-service",
            base_url="http://127.0.0.1:9002"
        )
        evidence = collect_evidence(config)
        self.assertEqual(evidence.health_check.status, "FAIL")
        self.assertEqual(evidence.health_check.http_status, 500)
        self.assertIn("status code 500", evidence.health_check.error_message)

    def test_stale_and_recovered_errors_do_not_create_active_incidents(self):
        config = ApplicationConfig(
            application_id="log-state-app",
            application_name="Log State Service",
            service_id="log-state-service",
            base_url="http://127.0.0.1:9002"
        )
        now = datetime.now(timezone.utc)
        stale_timestamp = (now - timedelta(hours=1)).isoformat()
        fresh_timestamp = now.isoformat()

        def evidence_with(
            entries,
            health_status="PASS",
            http_status=200,
            service_status="running",
            error_rate=0.0,
        ):
            error_count = sum(entry.level == "ERROR" for entry in entries)
            return Evidence(
                application_id=config.application_id,
                service=config.service_id,
                health_check=HealthCheckEvidence(
                    status=health_status, http_status=http_status, endpoint="http://127.0.0.1:9002/health"
                ),
                logs=LogEvidence(
                    available=True,
                    source="http://127.0.0.1:9002/logs/recent",
                    entries=entries,
                    error_count=error_count,
                ),
                service_status=service_status,
                recent_metrics={"error_rate": error_rate}
            )

        stale_evidence = evidence_with([
            LogEntry(timestamp=stale_timestamp, level="ERROR", message="Old outage", raw="Old outage")
        ])
        detected, _, _ = detect_incident(stale_evidence, config)
        self.assertFalse(detected)
        investigation = investigate_incident(stale_evidence, None, config)
        self.assertFalse(investigation.incident_detected)

        active_error = LogEntry(timestamp=fresh_timestamp, level="ERROR", message="Current outage", raw="Current outage")
        active_evidence = evidence_with([active_error])
        detected, _, _ = detect_incident(active_evidence, config)
        self.assertTrue(detected)

        current_failure_evidence = evidence_with(
            [active_error], health_status="FAIL", http_status=503,
            service_status="degraded", error_rate=0.25
        )
        detected, _, incident = detect_incident(current_failure_evidence, config)
        self.assertTrue(detected)
        self.assertEqual(incident.severity, "high")

        recovered_evidence = evidence_with([
            LogEntry(
                timestamp=(now + timedelta(seconds=1)).isoformat(),
                level="INFO",
                message="Recovery simulation completed by operator",
                raw="Recovery simulation completed by operator"
            ),
            active_error,
        ])
        detected, _, _ = detect_incident(recovered_evidence, config)
        self.assertFalse(detected)

    def test_payment_api_logs_endpoint_reads_real_log_file(self):
        forced_failure.clear()
        with TestClient(payment_test_app) as target_client:
            health = target_client.get("/health")
            logs = target_client.get("/logs/recent", params={"lines": 50})
            self.assertEqual(health.status_code, 200)
            self.assertEqual(logs.status_code, 200)
            self.assertNotIn("log_file", logs.json())
            log_lines = logs.json()["logs"]
            self.assertTrue(log_lines)
            self.assertTrue(any("Health check" in line for line in log_lines))

            failed = target_client.post("/simulate/failure")
            degraded_health = target_client.get("/health")
            self.assertEqual(failed.status_code, 200)
            self.assertEqual(degraded_health.status_code, 503)
            self.assertTrue(any("Failure simulation" in line for line in target_client.get("/logs/recent").json()["logs"]))

            recovered = target_client.post("/simulate/recover")
            healthy_again = target_client.get("/health")
            recovered_logs = target_client.get("/logs/recent").json()["logs"]
            self.assertEqual(recovered.status_code, 200)
            self.assertEqual(healthy_again.status_code, 200)
            self.assertTrue(any("Recovery simulation completed" in line for line in recovered_logs))
        forced_failure.clear()

    # -------------------------------------------------------------------------
    # TEST 7: Log Source Isolation Per Application
    # -------------------------------------------------------------------------
    @patch("requests.get")
    def test_log_collection_uses_each_application_base_url(self, mock_get):
        response_a = MagicMock(status_code=200)
        response_a.json.return_value = {"logs": ["2026-09-28 14:00:00 [ERROR] App A failure"]}
        response_b = MagicMock(status_code=200)
        response_b.json.return_value = {"logs": ["2026-09-28 14:00:00 [INFO] App B healthy"]}
        mock_get.side_effect = [response_a, response_b]

        app_a = ApplicationConfig(application_id="app-a", application_name="App A", service_id="service-a", base_url="http://127.0.0.1:9001")
        app_b = ApplicationConfig(application_id="app-b", application_name="App B", service_id="service-b", base_url="http://127.0.0.1:9002")
        logs_a = collect_logs(app_a)
        logs_b = collect_logs(app_b)

        self.assertEqual(mock_get.call_args_list[0].args[0], "http://127.0.0.1:9001/logs/recent")
        self.assertEqual(mock_get.call_args_list[1].args[0], "http://127.0.0.1:9002/logs/recent")
        self.assertEqual(logs_a.error_count, 1)
        self.assertEqual(logs_b.error_count, 0)

    @patch("requests.get")
    def test_log_collection_separates_active_from_recovered_errors(self, mock_get):
        now = datetime.now().astimezone()
        old_timestamp = (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        recent_timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "logs": [
                f"{old_timestamp} [ERROR] Old database timeout",
                f"{recent_timestamp} [INFO] Recovery completed successfully",
                f"{recent_timestamp} [ERROR] Current upstream timeout",
                f"{recent_timestamp} [WARNING] Retry scheduled",
            ]
        }
        mock_get.return_value = response
        config = ApplicationConfig(
            application_id="error-window-app",
            application_name="Error Window Service",
            service_id="error-window-service",
            base_url="http://127.0.0.1:9002"
        )

        logs = collect_logs(config)

        self.assertEqual(logs.error_count, 2)
        self.assertEqual(logs.warn_count, 1)
        self.assertEqual(logs.active_error_count, 1)

    # -------------------------------------------------------------------------
    # TEST 8: Backend Routes Verification (No 404 Mismatches)
    # -------------------------------------------------------------------------
    def test_backend_routes_exist(self):
        # 1. GET /api/applications
        resp_list = self.client.get("/api/applications")
        self.assertEqual(resp_list.status_code, 200)

        # 2. POST /api/incidents/inspect (valid app)
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "ok"}
            mock_get.return_value = mock_resp

            resp_inspect = self.client.post("/api/incidents/inspect", json={"application_id": "payment-api"})
            self.assertEqual(resp_inspect.status_code, 200)
            data = resp_inspect.json()
            self.assertIn("incident_detected", data)
            self.assertIn("evidence", data)

        # 3. POST /api/incidents/inspect with unregistered app returns explicit 404
        resp_unknown = self.client.post("/api/incidents/inspect", json={"application_id": "unregistered-id-123"})
        self.assertEqual(resp_unknown.status_code, 404)
        self.assertIn("not registered", resp_unknown.json()["detail"])

    # -------------------------------------------------------------------------
    # TEST 9: Legacy filesystem paths are ignored
    # -------------------------------------------------------------------------
    @patch("requests.get")
    def test_legacy_log_path_is_not_used(self, mock_get):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"logs": []}
        mock_get.return_value = mock_resp
        legacy_app = ApplicationConfig(
            application_id="unsafe-app",
            application_name="Unsafe Path App",
            service_id="unsafe-svc",
            base_url="http://127.0.0.1:8001",
            log_source="../../../../../windows/system32/drivers/etc/hosts"
        )
        log_ev = collect_logs(legacy_app)
        self.assertTrue(log_ev.available)
        mock_get.assert_called_once_with("http://127.0.0.1:8001/logs/recent", params={"lines": 50}, timeout=3.0)


if __name__ == "__main__":
    unittest.main()
