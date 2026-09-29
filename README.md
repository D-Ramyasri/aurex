# 🛡️ Incident Response Execution Agent

An AI Site Reliability Engineer (SRE) assistant that autonomously diagnoses live production incidents, recommends allowlisted remediation actions, enforces human approval gates with cryptographic action binding, executes actions against an in-process simulated infrastructure environment, automatically verifies recovery using 5 strict telemetry criteria, drives an intelligent multi-attempt recovery loop, and updates long-term organizational memory via **Hindsight** and **Groq** (`openai/gpt-oss-120b`).

---

## 🌟 Architecture & Workflow Loop

```
+----------------------------------------------------------------------------------------------------+
|                                    INCIDENT RESPONSE CLOSED LOOP                                   |
+----------------------------------------------------------------------------------------------------+
                                                  │
                                          [1. INCOMING ALERT]
                                                  │
                                                  ▼
                                      ┌───────────────────────┐
                                      │   Diagnose Incident   │ <── Hindsight Semantic Recall &
                                      │ (Groq gpt-oss-120b)   │     Pattern Reflection
                                      └───────────────────────┘
                                                  │
                                                  ▼
                                      ┌───────────────────────┐
                                      │ Propose Action (JSON) │ <── Allowlisted Actions & Services
                                      │  SHA-256 Fingerprint  │
                                      └───────────────────────┘
                                                  │
                                                  ▼
                                      ┌───────────────────────┐
                                      │  Human Approval Gate  │ ──> Reject & Re-diagnose
                                      │  (State Machine/409)  │
                                      └───────────────────────┘
                                                  │ APPROVED (Bound to Fingerprint)
                                                  ▼
                                      ┌───────────────────────┐
                                      │    Execution Engine   │ ──> Captures BEFORE Telemetry
                                      │ (executor.py -> /sim) │ ──> Applies Action over HTTP
                                      └───────────────────────┘
                                                  │
                                                  ▼
                                      ┌───────────────────────┐
                                      │ Telemetry Verification│ ──> 5 Health Probes (0.3s apart)
                                      │ (C1-C5 Strict Rules)  │ ──> Metrics & Log Evaluation
                                      └───────────────────────┘
                                                  │
                      ┌───────────────────────────┴───────────────────────────┐
                      │                                                       │
               [SUCCESS (All C1-C5)]                               [FAILURE or UNCERTAIN]
                      │                                                       │
                      ▼                                                       ▼
          ┌───────────────────────┐                               ┌───────────────────────┐
          │   Incident RESOLVED   │                               │ Check Attempts Count  │
          │ Structured Outcome    │                               └───────────────────────┘
          │ Retain in Hindsight   │                                           │
          └───────────────────────┘                          ┌────────────────┴────────────────┐
                                                             │                                 │
                                                    [Attempts < 3]                    [Attempts >= 3]
                                                             │                                 │
                                                             ▼                                 ▼
                                                 ┌───────────────────────┐         ┌───────────────────────┐
                                                 │ Multi-Attempt Recovery│         │  Incident ESCALATED   │
                                                 │ Loop (Exclude Fails)  │         │  Outcome Finalized    │
                                                 │ Cycle++, Reset PENDING│         │  Retain Failure Learn │
                                                 └───────────────────────┘         └───────────────────────┘
                                                             │
                                                             └───> Re-enter Approval Gate
```

---

## 📡 REST API Endpoints

### Core Incident Diagnostics & Memory
- `POST /diagnose` — Extracts metadata, recalls past post-mortems from Hindsight, synthesizes diagnosis via Groq, creates incident lifecycle record, proposes remediation action, and returns `incident_id`, `recommended_action`, and `memory_source`.
- `POST /resolve` — Ingests resolved incident fixes into Hindsight memory.
- `GET /health` — Backend and memory bank connectivity status.

### Simulated Infrastructure (`/sim`)
All endpoints are synchronous (`def`) to allow reliable HTTP self-calls without event loop blocking.
- `GET /sim/services` — Live telemetry and state for all 5 services (`payment-gateway`, `auth-service`, `database-cluster`, `api-gateway`, `checkout-service`).
- `GET /sim/{service}/health` — HTTP 200 `{"status":"healthy"}` if `error_rate < 0.05` and `status != "down"`, else HTTP 503 `{"status":"unhealthy"}`.
- `GET /sim/{service}/metrics` — Returns `error_rate`, `p95_latency_ms`, `replicas`, `version`, `status`, `config`.
- `GET /sim/{service}/logs?since=&limit=` — Fetches log entries filtered by ISO timestamp and count limit.
- `POST /sim/inject` — Injects one of 5 fault scenarios (`db_pool_exhaustion`, `redis_oom`, `payment_timeout`, `stale_dns_502`, `bad_deploy`).
- `POST /sim/reset` — Restores all simulated services to healthy baseline.
- `POST /sim/{service}/actions` — Dispatches allowlisted action (`restart_service`, `scale_replicas`, `set_config`, `flush_cache`, `rollback_deployment`).

### Incident Lifecycle & Governance (`/incidents`)
- `GET /incidents` — Lists all incident records ordered by creation date.
- `GET /incidents/{id}` — Retrieves full incident record (status, approval, executions, verifications, failed attempts).
- `POST /incidents/{id}/approve` — Approves action and cryptographically binds to `action_fingerprint`. Supports optional action override.
- `POST /incidents/{id}/reject` — Rejects proposal; approval state immutable once decided.
- `POST /incidents/{id}/rediagnose` — Re-diagnoses with engineer correction; increments cycle and resets approval to PENDING.
- `POST /incidents/{id}/execute` — Backend-enforced execution engine. Re-checks approval fingerprint, captures BEFORE telemetry, applies action, runs automated verification against C1-C5, and triggers recovery loop.
- `GET /incidents/{id}/outcome` — Returns finalized structured post-mortem outcome JSON.

---

## 🎯 Verification Criteria (C1 – C5)

Automated telemetry verification is driven purely by evidence collected across real HTTP probes:
- **C1**: `health_pass_ratio == 1.0` (all 5 health probes spaced 0.3s apart return HTTP 200).
- **C2**: `error_rate <= 0.01` (error rate below 1%).
- **C3**: `p95_latency_ms <= 500` (P95 latency normalized).
- **C4**: `service_status == "running"`.
- **C5**: `error_log_count == 0` (zero ERROR log lines recorded since execution start).

**Decision Engine:**
- **SUCCESS**: All criteria C1 through C5 pass.
- **FAILURE**: `health_pass_ratio == 0` OR `service_status == "down"` OR `error_rate_after >= 0.9 * error_rate_before`.
- **UNCERTAIN**: Partial telemetry improvement not satisfying all 5 criteria.

---

## 🚀 Setup & Execution

### 1. Environment Setup

```bash
# Clone and enter directory
cd hackthon-agent

# Create and activate virtual environment
python -m venv venv
.\venv\Scripts\activate   # Windows
# source venv/bin/activate # macOS/Linux

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment (`.env`)

Copy `.env.example` to `.env` and set your credentials:
```env
HINDSIGHT_API_URL=http://localhost:8888
HINDSIGHT_API_KEY=
GROQ_API_KEY=gsk_your_groq_api_key_here
```

### 3. Run Automated Tests

Run the complete 9-scenario workflow test suite using pytest:

```bash
pytest tests/test_workflow.py -v
```

**Scenarios Tested:**
1. `test_1_execute_before_approval_409_sim_unchanged`
2. `test_2_reject_then_execute_409`
3. `test_3_approve_then_approve_or_reject_again_409_immutable`
4. `test_4_action_override_fingerprint_mismatch_409`
5. `test_5_bad_deploy_rollback_deployment_success`
6. `test_6_db_pool_exhaustion_restart_uncertain`
7. `test_7_bad_deploy_restart_failure_and_retry`
8. `test_8_three_failed_attempts_escalated`
9. `test_9_invalid_action_type_rejected_by_engine`

### 4. Run the End-to-End Demo Script

Executes the autonomous loop: fault injection ➔ diagnosis ➔ wrong action approval ➔ verification failure ➔ recovery loop ➔ correct action approval ➔ verification recovery ➔ structured outcome JSON:

```bash
python demo_workflow.py
```

### 5. Launch Application

Start the FastAPI backend:
```bash
uvicorn main:app --host 127.0.0.1 --port 8001 --reload
```

In a separate terminal, launch the Streamlit UI:
```bash
streamlit run app.py --server.port 8501
```
Open [http://localhost:8501](http://localhost:8501) in your browser.
The Streamlit UI connects to `http://127.0.0.1:8001` by default. Set `BACKEND_URL` if the backend runs on a different address.
