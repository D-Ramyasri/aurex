import pytest

from models import Incident, Evidence, HealthCheckEvidence, LogEvidence, LogEntry, InvestigationReport, RootCauseAnalysis, ResolutionRecommendation, HindsightRecallResult
from incidents import create_incident, get_incident, clear_all_incidents, list_incidents
from canonical_incident import to_canonical_incident
from agent import diagnose_incident


def setup_function():
    clear_all_incidents()


def test_to_canonical_incident_preserves_detection_identity_and_fields():
    evidence = Evidence(
        application_id="app-42",
        service="payment-gateway",
        health_check=HealthCheckEvidence(status="FAIL", http_status=503, response_time_ms=1200, endpoint="http://example/internal/health", response_body="upstream timeout"),
        logs=LogEvidence(available=True, source="demo.log", entries=[LogEntry(timestamp="2024-01-01T00:00:00Z", level="ERROR", message="payment-gateway upstream timeout", raw="payment-gateway upstream timeout")]),
        summary="service degraded",
        service_status="degraded"
    )
    incident = Incident(
        incident_id="INC-ABCD1234",
        application_id="app-42",
        service="payment-gateway",
        severity="high",
        description="Payment gateway timed out",
        symptoms=["timeout"],
        errors=["upstream timeout"],
        impact="checkout impacted",
        incident_type="Dependency / Upstream",
        source="application_inspection",
        evidence=evidence
    )
    investigation = InvestigationReport(
        incident_detected=True,
        incident_id="INC-ABCD1234",
        service="payment-gateway",
        incident_type="Dependency / Upstream",
        severity="high",
        timeline=[],
        analysis=RootCauseAnalysis(
            incident_type="Dependency / Upstream",
            severity="high",
            failure="Upstream fraud timeout",
            root_cause="The fraud engine timed out after 1.0s", 
            why="A slow upstream provider delayed response",
            impact="checkout failed",
            confidence=0.9,
            supporting_evidence=["gateway timeout"]
        ),
        resolution=ResolutionRecommendation(
            summary="Add retry and circuit breaker",
            actions=["enable circuit breaker", "retry with backoff"],
            memory_grounded=False
        ),
        hindsight=HindsightRecallResult(
            query="fraud engine timeout",
            matched_memory={"id": "mem-7", "text": "enable circuit breaker"},
            similar_incidents=[{"id": "mem-7"}]
        )
    )

    canonical = to_canonical_incident(incident, evidence=evidence, investigation=investigation)

    assert canonical.incident_id == "INC-ABCD1234"
    assert canonical.service_id == "payment-gateway"
    assert canonical.application_id == "app-42"
    assert canonical.summary == "Payment gateway timed out"
    assert canonical.root_cause == "The fraud engine timed out after 1.0s"
    assert canonical.recommendation == "Add retry and circuit breaker"
    assert canonical.hindsight_query == "fraud engine timeout"
    assert canonical.matched_memory["id"] == "mem-7"


def test_create_incident_keeps_single_canonical_id_in_lifecycle_store():
    record = create_incident(
        alert="Payment timeout",
        extracted_details={"service": "payment-gateway", "severity": "high"},
        diagnosis="fraud engine slow",
        reflection="retry with backoff",
        raw_recalled_memories=[],
        recommended_action={"type": "restart_service", "target_service": "payment-gateway", "params": {}},
        incident_id="INC-CANONICAL-42",
        application_id="app-42",
        service_id="payment-gateway",
        source_type="application_inspection",
        summary="Payment timeout",
        classification="Dependency / Upstream"
    )

    assert record["id"] == "INC-CANONICAL-42"
    assert record["incident_id"] == "INC-CANONICAL-42"
    assert get_incident("INC-CANONICAL-42")["incident_id"] == "INC-CANONICAL-42"


def test_create_incident_requires_canonical_business_id(monkeypatch):
    monkeypatch.setattr("agent.call_groq_llm", lambda prompt: "stub diagnosis")
    monkeypatch.setattr("agent.extract_incident_details", lambda text: {"service": "checkout-service", "severity": "high", "symptom_type": "error", "summary": text[:60]})
    monkeypatch.setattr("agent.reflect_on_incident", lambda text: "stub reflection")
    monkeypatch.setattr("agent.recall_memories_from_hindsight", lambda text, return_source=False: ([], "local_fallback"))

    with pytest.raises(ValueError, match="incident_id"):
        create_incident(
            alert="Checkout service degraded",
            extracted_details={"service": "checkout-service"},
            diagnosis="stub diagnosis",
            reflection="stub reflection",
            raw_recalled_memories=[],
            recommended_action={"type": "restart_service", "target_service": "checkout-service", "params": {}},
        )


def test_diagnose_incident_creates_one_canonical_incident(monkeypatch):
    monkeypatch.setattr("agent.call_groq_llm", lambda prompt: "stub diagnosis")
    monkeypatch.setattr("agent.extract_incident_details", lambda text: {"service": "checkout-service", "severity": "high", "symptom_type": "error", "summary": text[:60]})
    monkeypatch.setattr("agent.reflect_on_incident", lambda text: "stub reflection")
    monkeypatch.setattr("agent.recall_memories_from_hindsight", lambda text, return_source=False: ([], "local_fallback"))

    result = diagnose_incident("Checkout service degraded")

    assert result["incident_id"].startswith("INC-")
    assert len(list_incidents()) == 1
    assert get_incident(result["incident_id"])["incident_id"] == result["incident_id"]


def test_diagnose_incident_reuses_existing_incident_id(monkeypatch):
    create_incident(
        alert="Existing incident",
        extracted_details={"service": "database-cluster", "severity": "high"},
        diagnosis="first diagnosis",
        reflection="first reflection",
        raw_recalled_memories=[],
        recommended_action={"type": "restart_service", "target_service": "database-cluster", "params": {}},
        incident_id="INC-EXISTING-01",
        source_type="diagnostic_alert",
        summary="Existing incident",
    )

    monkeypatch.setattr("agent.call_groq_llm", lambda prompt: "updated diagnosis")
    monkeypatch.setattr("agent.extract_incident_details", lambda text: {"service": "database-cluster", "severity": "high", "symptom_type": "error", "summary": text[:60]})
    monkeypatch.setattr("agent.reflect_on_incident", lambda text: "updated reflection")
    monkeypatch.setattr("agent.recall_memories_from_hindsight", lambda text, return_source=False: ([{"id": "m1", "text": "previous issue"}], "hindsight"))

    result = diagnose_incident("Existing incident", incident_id="INC-EXISTING-01")

    assert result["incident_id"] == "INC-EXISTING-01"
    assert len(list_incidents()) == 1
    assert get_incident("INC-EXISTING-01")["diagnosis"] == "updated diagnosis"
