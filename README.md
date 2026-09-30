## 🚨 The Problem We Are Solving

Modern cloud-native architectures comprise dozens or hundreds of distributed microservices. In production, incidents cascade rapidly—database connection pools exhaust, upstream dependencies fail with HTTP 504 timeouts, bad canary deployments introduce unhandled exceptions, and webhook secret mismatches reject critical events.

During high-severity incidents, on-call engineers face critical challenges:

1. **The "Postmortem Blindspot" (Loss of Negative Experience):**  
   Standard runbooks and postmortems document *only what eventually fixed the incident*. They omit the costly dead ends and failed remediation attempts. For example, when a bad deployment breaks a service, a common initial reaction is `restart_service`. Restarting simply brings the same broken container image back up, wasting critical MTTR (Mean Time to Resolution). Traditional systems forget that the restart failed, causing future engineers to repeat the exact same mistake.
2. **AI Hallucinations and Uncontrolled Execution:**  
   Naive LLM agents applied to infrastructure frequently hallucinate non-existent shell commands, invent reasons for failure, or attempt destructive operations without boundary constraints or sandboxes.
3. **Missing Telemetry Grounding:**  
   Most incident management tools treat alerts as isolated text snippets. They fail to dynamically inspect live endpoints, read recent runtime logs, correlate request IDs, or measure genuine system telemetry before and after taking an action.
4. **Lack of Cryptographic Action Governance:**  
   In systems with human approval, engineers often approve an action, but race conditions, background retries, or parameter mutations cause a *different* action or altered payload to execute against infrastructure.
5. **Binary Verification Fallacy:**  
   Systems often assume an action either worked (pass) or failed (fail). In reality, infrastructure exhibits intermediate states—e.g., error rates drop from 80% to 20%, but the service is still not operational. Treating partial improvements as complete success corrupts organizational runbooks.

---

## 💡 The Solution

**Aurex** solves these challenges by implementing a deterministic, closed-loop, memory-augmented incident management platform:

- **Empirical Signal Inspection:** Inspects registered microservices directly via HTTP health checks, live structured log scraping, and metrics collection—differentiating `UNREACHABLE` connection errors from HTTP 404, 500, and 503 statuses.
- **Bi-Directional Long-Term Memory (Hindsight):** Retains not only successful resolutions, but also **stores failed remediation attempts as explicit first-class negative memories** complete with empirical verifier reasons. When diagnosing, it asks Hindsight: *"What worked or failed in similar past incidents?"*
- **Strictly Allowlisted Remediation:** Proposes only safe, allowlisted remediation primitives (`restart_service`, `scale_replicas`, `set_config`, `flush_cache`, `rollback_deployment`).
- **Cryptographic Action Fingerprinting:** Computes a canonical `SHA-256` hash of `action_type:target_service:params`. Approval is cryptographically bound to that exact fingerprint; any parameter mutation immediately invalidates approval with HTTP 409 Conflict.
- **Dual Execution Targets:** Dispatches real remediations to registered application endpoints (`/remediation`) with fail-closed security, while providing an in-process simulated environment for safe automated testing.
- **5-Point Telemetry Verification (C1–C5):** Automatically collects 5 distinct HTTP health probes, latency metrics, error rates, and post-execution error logs. Employs a **3-way decision engine**: `SUCCESS`, `FAILURE`, or `UNCERTAIN`.
- **Intelligent Multi-Attempt Recovery Loop:** If an action fails or is uncertain, it is recorded in `failed_attempts`, excluded from subsequent LLM proposals, approval is reset to `PENDING`, and the cycle increments. If 3 attempts fail, the incident cleanly escalates to human on-call with full audit history.
- **Enterprise Web Control Plane:** A multi-tenant Streamlit UI backed by SQLite, bcrypt password hashing, and JWT tokens, offering complete visibility from live inspection to postmortem outcome generation.

---

## 🏗️ System Architecture & End-to-End Process Flow

```
                                      +--------------------------------------------------------------------+
                                      |                    AUREX INCIDENT RESPONSE CLOSED LOOP             |
                                      +--------------------------------------------------------------------+
                                                                        │
                                                [1. LIVE SIGNAL INGESTION / ALERT]
                                                Probes /health, scrapes /logs/recent, reads /metrics
                                                                        │
                                                                        ▼
                                                ┌──────────────────────────────────────────────────┐
                                                │        Deterministic Incident Detector           │
                                                │  (UNREACHABLE vs 404 vs 500/503 vs Telemetry)    │
                                                └──────────────────────────────────────────────────┘
                                                                        │ Incident Detected (or Healthy exit)
                                                                        ▼
                                                ┌──────────────────────────────────────────────────┐
                                                │       Investigation & Timeline Correlation       │
                                                │  - 17+ Error Signatures & Regex Extraction       │
                                                │  - Multi-Hypothesis Likelihood Evaluation        │
                                                │  - Structured Root Cause Analysis (RCA)          │
                                                └──────────────────────────────────────────────────┘
                                                                        │
                                                                        ▼
                                                ┌──────────────────────────────────────────────────┐
                                                │     Hindsight Semantic Recall & Reflection       │
                                                │  - Queries memory bank: "What worked or failed?" │
                                                │  - Returns past fixes & known dead ends          │
                                                │  - Local keyword-similarity fallback if offline  │
                                                └──────────────────────────────────────────────────┘
                                                                        │
                                                                        ▼
                                                ┌──────────────────────────────────────────────────┐
                                                │         Groq LLM Synthesis (gpt-oss-120b)        │
                                                │  - Synthesizes comprehensive diagnosis           │
                                                │  - Selects allowlisted remediation action        │
                                                │  - Computes SHA-256 Action Fingerprint           │
                                                └──────────────────────────────────────────────────┘
                                                                        │
                                                                        ▼
                                                ┌──────────────────────────────────────────────────┐
                                                │             Human Approval Gate                  │ ──> [REJECTED] ──> Rediagnose with
                                                │  - Cryptographically locks to SHA-256 fingerprint│                    Engineer Context
                                                │  - Rejects parameter tampering (HTTP 409)        │
                                                └──────────────────────────────────────────────────┘
                                                                        │ [APPROVED]
                                                                        ▼
                                                ┌──────────────────────────────────────────────────┐
                                                │               Execution Engine                   │
                                                │  - Captures BEFORE Telemetry Snapshot            │
                                                │  - Dispatches action over HTTP to Target Service │
                                                │  - Fails closed if capability unconfigured       │
                                                └──────────────────────────────────────────────────┘
                                                                        │
                                                                        ▼
                                                ┌──────────────────────────────────────────────────┐
                                                │        Automated Telemetry Verifier              │
                                                │  - 5 health probes (0.2s - 0.3s apart)           │
                                                │  - Strict C1 - C5 evaluation                     │
                                                │  - Tri-state Decision: SUCCESS, FAILURE, UNCERTAIN│
                                                └──────────────────────────────────────────────────┘
                                                                        │
                                      ┌─────────────────────────────────┴─────────────────────────────────┐
                                      │                                                                   │
                               [SUCCESS (All C1-C5)]                                             [FAILURE or UNCERTAIN]
                                      │                                                                   │
                                      ▼                                                                   ▼
                          ┌────────────────────────┐                                          ┌────────────────────────┐
                          │    Incident RESOLVED   │                                          │  Evaluate Attempt Count│
                          │ - Structured Outcome   │                                          └────────────────────────┘
                          │ - Retain Fix in Memory │                                                      │
                          └────────────────────────┘                                     ┌────────────────┴────────────────┐
                                                                                         │                                 │
                                                                                [Attempts < 3]                    [Attempts >= 3]
                                                                                         │                                 │
                                                                                         ▼                                 ▼
                                                                             ┌────────────────────────┐       ┌────────────────────────┐
                                                                             │  Multi-Attempt Loop    │       │   Incident ESCALATED   │
                                                                             │ - Exclude failed action│       │ - Retain Failure Record│
                                                                             │ - Record fail telemetry│       │ - Finalize Outcome JSON│
                                                                             │ - Reset to PENDING     │       │ - Notify On-Call Lead  │
                                                                             └────────────────────────┘       └────────────────────────┘
                                                                                         │
                                                                                         └───> Re-enter Approval Gate
```

---

## 🔍 Comprehensive Implementation Overview

### 1. Monitored Microservice Targets

The repository provides two execution targets for incident inspection and remediation:

#### A. Real Payment Gateway Service (`test_application/`)
A realistic, production-style Payment Gateway running as an independent HTTP microservice on **port 9001**:
- **Payment Processing (`POST /payments`):** Enforces authorization, idempotency, fraud score checks, account balances, and SQLite transactions.
- **Database Connection Pool (`DatabasePool`):** Genuine SQLite database (`test_application/data/payments.db`) with an active pool (max 5 connections, 2.0s acquire timeout, WAL mode).
- **Upstream Network Dependency:** Simulated pre-authorization Fraud / Acquirer network with a realistic 1.0s timeout SLA.
- **Stripe-like Webhooks (`POST /webhooks/payment`):** Verifies HMAC-SHA256 signatures and deduplicates event deliveries.
- **Background Worker:** `BackgroundWebhookWorker` thread polling and processing pending transactions from SQLite.
- **Telemetry Endpoints:** `GET /health` (live DB pool probe), `GET /logs/recent` (structured runtime logs with timestamps, log levels, request IDs, and stack traces), `GET /metrics`, and `POST /remediation`.

#### B. Simulated Microservice Fleet (`sim_env.py`)
An optional in-process simulated environment on **port 8001** (`ENABLE_SIMULATOR=1`):
- **5 Microservices:** `payment-gateway`, `auth-service`, `database-cluster`, `api-gateway`, and `checkout-service`.
- **5 Fault Injections (`POST /sim/inject`):**
  1. `db_pool_exhaustion` — Satures active DB connections, spikes error rate to 80%, increases latency to 1200ms.
  2. `redis_oom` — Auth session token cache fills to 100%, causing HTTP 500 session drops.
  3. `payment_timeout` — Upstream payment acquirer network timeout, elevating P95 latency to 3500ms.
  4. `stale_dns_502` — API gateway upstream resolution fails with HTTP 502 Bad Gateway.
  5. `bad_deploy` — Defective container deployment (`v2.4.1`) throwing null-pointer exceptions across checkout flows.

---

### 2. Application Registry & Multi-Tenancy

- **File:** `app_registry.py`, `applications_store.json`
- Dynamic registration, lookup, persistence, and deletion of monitored services.
- **Per-User Isolation:** Each registered application is scoped to an owner `user_id`. Default seed applications (`payment-api`, `order-api`, `auth-service`, `inventory-api`, `payment-test-api`) are available to default operators.
- **Configuration Attributes:** `application_id`, `application_name`, `service_id`, `base_url`, `health_endpoint`, `remediation_endpoint`, `metrics_endpoint`, `log_collection_method`, and `description`.

---

### 3. Evidence Collection & Deterministic Ingestion

- **Files:** `evidence_collector.py`, `incident_detector.py`
- **Zero Mocking, Real Signal Scraping:**
  - Probes the target's configured `health_endpoint` (measures HTTP status, round-trip latency, response body).
  - Fetches and parses structured logs from `base_url + /logs/recent` within an active temporal sliding window.
  - Queries runtime metrics from `metrics_endpoint`.
- **Deterministic Incident Detection Rules:**
  - `UNREACHABLE`: Connection refused or timed out $\rightarrow$ **Critical** severity.
  - `HTTP 404`: Service host reachable, but endpoint not found $\rightarrow$ **Medium** severity.
  - `HTTP 5xx` / `FAIL`: Internal server error on health check $\rightarrow$ **High** severity.
  - Telemetry Degradation: `status == "down"` or `error_rate > 0.01` $\rightarrow$ **High/Medium** severity.
  - Active Errors: Log error frequency exceeding safe thresholds $\rightarrow$ Structured incident created.

---

### 4. Incident Investigation & Root Cause Analysis (RCA)

- **File:** `incident_investigator.py`
- **17+ Deterministic Regex Signatures:** Matches specific production failures including `ConnectionPoolTimeoutError`, `UpstreamDependencyTimeoutError`, `HTTP 504 Gateway Timeout`, `FraudVerificationError`, `WebhookSignatureError`, `sqlite3.OperationalError`, `Circuit breaker OPEN`, and `bad_deploy`.
- **Chronological Timeline Generation:** Extracts timestamps, log levels, request IDs (`req_id`), and components to reconstruct the sequence of events leading to failure.
- **Multi-Hypothesis Reasoning Engine:** Formulates primary and alternative hypotheses, assigning likelihood scores with supporting and opposing empirical evidence.
- **Structured RCA Model:** Emits `incident_type`, `severity`, `failure`, `root_cause`, `why`, `observed_evidence`, and `impact`.

---

### 5. Semantic Memory & LLM Synthesis (Hindsight + Groq)

- **File:** `agent.py`
- **Hindsight Semantic Memory Integration:**
  - Connects to Hindsight via `hindsight_client.Hindsight` using memory bank `incident-response` (or per-user bank `incident-response-user-{id}`).
  - **Bi-directional Querying:** Queries Hindsight with: *"What has worked or failed in past incidents similar to: {alert_text}"*.
  - **Memory Reflection:** Calls `client.reflect(bank_id=..., query=..., budget="low")` to synthesize high-level patterns across multiple historical postmortems.
  - **Graceful Local Fallback:** If the external Hindsight instance is unreachable, it automatically falls back to an in-memory keyword/term-overlap engine seeded from `seed_incidents.json`, exposing `memory_source: "local_fallback"`.
- **Groq LLM Reasoning:**
  - Uses `openai/gpt-oss-120b` via Groq API.
  - Ingests incoming alerts, extracted metadata, telemetry evidence, recalled past memories, reflection patterns, and list of previously failed attempts.
  - Recommends an allowlisted remediation action accompanied by technical rationale.

---

### 6. Human-in-the-Loop Approval & Cryptographic Binding

- **File:** `incidents.py`, `main.py`
- **Allowlisted Action Primitives:**
  ```python
  ALLOWED_ACTIONS = {
      "restart_service",
      "scale_replicas",
      "set_config",
      "flush_cache",
      "rollback_deployment"
  }
  ```
- **Cryptographic Action Fingerprint:**
  ```python
  def compute_action_fingerprint(action_type, target_service, params=None):
      params_json = json.dumps(params or {}, sort_keys=True, separators=(",", ":"))
      raw = f"{action_type.strip()}:{target_service.strip()}:{params_json}"
      return hashlib.sha256(raw.encode("utf-8")).hexdigest()
  ```
- **Strict State Machine:**
  - States: `DETECTED` $\rightarrow$ `DIAGNOSED` $\rightarrow$ `AWAITING_APPROVAL` $\rightarrow$ `APPROVED` | `REJECTED` $\rightarrow$ `EXECUTING` $\rightarrow$ `VERIFYING` $\rightarrow$ `RESOLVED` | `ESCALATED`.
  - Only `PENDING` $\rightarrow$ `APPROVED` or `PENDING` $\rightarrow$ `REJECTED` transitions are permitted. Once decided, approval is immutable. Attempting to approve or reject an already decided incident returns **HTTP 409 Conflict**.
  - Attempting to execute an action whose fingerprint differs from the approved fingerprint returns **HTTP 409 Conflict**.

---

### 7. Execution Engine (Real vs. Simulator)

- **File:** `executor.py`
- **`RealExecutionTarget` (Production Mode):**
  - Dispatches actions via HTTP POST to the target's configured `remediation_endpoint`.
  - **Fails Closed:** If a registered application has no real remediation capability configured, execution returns status `UNAVAILABLE` rather than simulating success or running unverified commands.
- **`SimulatorExecutionTarget` (Integration Test Mode):**
  - Executes allowlisted actions against the in-process `/sim/{service}/actions` routes during automated workflow testing.
- **Pre-Execution Snapshot:** Always captures a complete `BEFORE` telemetry snapshot (health probe status, error rate, P95 latency, error logs) immediately prior to applying changes.

---

### 8. Automated Verification Engine (C1 – C5 Rules)

- **File:** `verifier.py`
- Following execution, the verifier probes the target service:
  - Spaced health checks (5 probes spaced 0.2s to 0.3s apart).
  - Collects post-execution metrics and post-execution logs generated strictly after `execution_start_iso`.
- **Evaluates 5 Strict Criteria (C1 to C5):**
  - **C1:** `health_pass_ratio == 1.0` (all 5 probes return HTTP 200).
  - **C2:** `error_rate <= 0.01` (error rate below 1%).
  - **C3:** `p95_latency_ms <= 500` (P95 latency normalized).
  - **C4:** `service_status in ("running", "healthy", "operational")`.
  - **C5:** `error_log_count == 0` (zero ERROR log lines recorded since execution start).
- **3-Way Decision Engine:**
  - `SUCCESS`: All criteria C1 through C5 pass, and execution status is `COMPLETED`.
  - `FAILURE`: Health pass ratio is 0.0, or service is down, or error rate showed no meaningful improvement ($error\_rate_{after} \ge 0.9 \times error\_rate_{before}$).
  - `UNCERTAIN`: Telemetry improved partially, or execution was unavailable, but the service did not satisfy all 5 criteria.

---

### 9. Multi-Attempt Recovery Loop & Negative Memory Retention

- **File:** `executor.py`, `agent.py`
- **When Verification Yields `FAILURE` or `UNCERTAIN`:**
  1. The failed attempt is appended to `incident["failed_attempts"]` with the exact action, parameters, and verifier reasons.
  2. **Negative Memory Retention:** The system writes the failed attempt directly to Hindsight memory:
     ```text
     "Attempted restart_service on checkout-service; it did NOT work because Real application status is 'degraded'; Observed 14 errors in real logs."
     ```
  3. If `attempts < 3`:
     - Cycle count increments (`cycle += 1`).
     - Approval state resets to `PENDING`.
     - The recovery loop requests a new action from Groq, passing all `failed_attempts` as explicit negative constraints.
  4. If `attempts >= 3`:
     - Incident status transitions to `ESCALATED`.
     - Structured outcome JSON is finalized with the full audit trail.

---

### 10. Enterprise SRE Control Plane (Streamlit UI)

- **File:** `app.py`
- Enterprise SaaS light theme with a pale blue sidebar (`#F1F6FC`), custom CSS, and Inter typography.
- **Three Core Navigation Views:**
  1. **Public Landing Page:** Overview of capabilities, architecture, and live system metrics.
  2. **Authentication Portal:** User registration (`/signup`) and login (`/login`) backed by SQLite, bcrypt, and JWT access tokens.
  3. **Authenticated SRE Dashboard (`/dashboard`):**
     - **Service Inspection Matrix:** Live health cards for all registered microservices.
     - **Single-Click Inspection:** Triggers deterministic evidence collection, log scraping, and incident detection.
     - **Incident Investigation & RCA View:** Visual chronological timeline, extracted evidence tags, multi-hypothesis breakdown, and root cause analysis summary.
     - **Semantic Memory Explorer:** Displays matched Hindsight memories, relevance scores, and historical pattern reflections.
     - **Human Approval & Action Gate:** Interactive UI displaying proposed action, rationale, parameter configuration, and SHA-256 fingerprint with one-click Approve / Reject buttons.
     - **Execution & Telemetry Monitor:** Real-time progress bar, live execution logs, and Before vs. After telemetry comparison.
     - **C1–C5 Telemetry Verification Badges:** Visual status indicators for all 5 criteria with pass/fail ratios and threshold compliance.
     - **Structured Postmortem Outcome:** Complete post-incident summary with MTTR metrics and Hindsight retention confirmation.

---

## 🎯 Verification Criteria (C1 – C5) Specification

| Criterion | Rule Name | Description | Target Threshold | Metric Source |
| :---: | :---: | :--- | :---: | :--- |
| **C1** | `health_pass_ratio` | Spaced HTTP health probes to service endpoint | `== 1.0` (100% 200s) | 5 sequential probes (0.2s–0.3s apart) |
| **C2** | `error_rate` | Normalized transaction error frequency | `lenient: <= 0.01` (1%) | Application metrics endpoint / telemetry |
| **C3** | `p95_latency_ms` | 95th percentile response latency | `<= 500 ms` | Application metrics endpoint / telemetry |
| **C4** | `service_status` | Runtime lifecycle health status | `running` / `healthy` | Parsed health check payload / status |
| **C5** | `error_log_count` | New errors logged since execution start | `== 0` | `/logs/recent` filtered by execution start ISO |

### Decision Rules:
- **`SUCCESS`**: $\text{C1} \land \text{C2} \land \text{C3} \land \text{C4} \land \text{C5} \land (\text{Execution Status} == \text{COMPLETED})$
- **`FAILURE`**: $\text{Pass Ratio} == 0.0 \lor \text{Status} \in \{\text{down}, \text{degraded}\} \lor (error\_rate_{after} \ge 0.9 \times error\_rate_{before})$
- **`UNCERTAIN`**: Any state showing partial telemetry recovery without meeting all 5 strict criteria.

---

## 📡 REST API Reference

The backend exposes a comprehensive RESTful API built with FastAPI:

### 1. Authentication Endpoints (`/api/auth`)
| Method | Endpoint | Description | Request Payload | Response |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/api/auth/signup` | Registers new user in SQLite database | `{username, email, password, confirm_password}` | `{status, token, user}` |
| `POST` | `/api/auth/login` | Authenticates user & returns JWT | `{username_or_email, password}` | `{status, token, user}` |
| `GET` | `/api/auth/me` | Retrieves current authenticated profile | Header: `Authorization: Bearer <token>` | `{id, username, email}` |

### 2. Application Registry Endpoints (`/api/applications`)
| Method | Endpoint | Description | Header / Payload |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/applications` | Lists all registered applications for user | `Authorization: Bearer <token>` |
| `GET` | `/api/applications/{id}` | Retrieves config for specific application | `Authorization: Bearer <token>` |
| `POST` | `/api/applications` | Registers or updates a microservice configuration | `ApplicationConfig` JSON payload |
| `DELETE`| `/api/applications/{id}` | Deletes microservice from registry | `Authorization: Bearer <token>` |

### 3. Inspection & Ingestion Endpoints (`/api/incidents`)
| Method | Endpoint | Description | Payload |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/incidents/inspect` | Probes application health, reads logs, detects incidents | `{"application_id": "payment-test-api"}` |
| `GET` | `/api/incidents/active/{app_id}`| Gets currently active incident for application | Path parameter `app_id` |

### 4. Diagnosis & Hindsight Memory Endpoints
| Method | Endpoint | Description | Payload |
| :--- | :--- | :--- | :--- |
| `POST` | `/diagnose` | Recalls memories from Hindsight, calls Groq LLM | `{"alert": "string", "incident_id": "optional"}` |
| `POST` | `/resolve` | Ingests resolved incident fix into Hindsight memory | `{alert, root_cause, fix, service, resolution_time_minutes}` |
| `GET` | `/health` | Health check confirming API and memory bank status | None |

### 5. Incident Lifecycle & Governance Endpoints (`/incidents`)
| Method | Endpoint | Description | Payload / Notes |
| :--- | :--- | :--- | :--- |
| `GET` | `/incidents` | Lists all incident records ordered by creation date | Scoped to authenticated user |
| `GET` | `/incidents/{id}` | Retrieves full incident record (approvals, verifications) | Incident ID path param |
| `POST` | `/incidents/{id}/approve` | Approves action & binds to SHA-256 fingerprint | `{"approver": "Engineer", "note": "...", "action": optional}` |
| `POST` | `/incidents/{id}/reject` | Rejects proposed remediation action | `{"approver": "Engineer", "note": "Reason"}` |
| `POST` | `/incidents/{id}/rediagnose`| Re-diagnoses with engineer correction; resets to PENDING | `{"correction": "Detailed engineer context"}` |
| `POST` | `/incidents/{id}/execute` | Executes approved action, verifies C1-C5, drives recovery | Enforces approved fingerprint |
| `GET` | `/incidents/{id}/outcome` | Returns finalized structured postmortem outcome JSON | Incident ID path param |

### 6. Simulated Environment Endpoints (`/sim/*`)
*(Available when `ENABLE_SIMULATOR=1`)*
- `GET /sim/services` — Live telemetry and status for all 5 simulated services.
- `GET /sim/{service}/health` — HTTP 200 if error rate < 0.05, else HTTP 503.
- `GET /sim/{service}/metrics` — Returns error rate, P95 latency, replicas, status.
- `GET /sim/{service}/logs` — Scrapes logs filtered by `since` timestamp and count `limit`.
- `POST /sim/inject` — Injects faults (`db_pool_exhaustion`, `redis_oom`, `payment_timeout`, `stale_dns_502`, `bad_deploy`).
- `POST /sim/reset` — Resets all services to healthy baseline.
- `POST /sim/{service}/actions` — Dispatches allowlisted remediation action.

---

## 📁 Directory & File Structure

```text
hackthon-agent/
│
├── app.py                      # Streamlit Enterprise SRE Control Plane (UI)
├── main.py                     # FastAPI backend application & routing
├── agent.py                    # Core agent logic, Hindsight integration, Groq LLM prompts
├── auth.py                     # SQLAlchemy models, bcrypt hashing, JWT authentication
├── app_registry.py             # Microservice application registry & storage
├── applications_store.json     # Persistent storage for registered microservices
├── models.py                   # Pydantic v2 domain schemas for evidence, incidents, RCA
├── incident_detector.py        # Deterministic incident detection rules
├── incident_investigator.py    # RCA pipeline, log signature matching, timeline builder
├── canonical_incident.py       # Canonical incident normalization layer
├── evidence_collector.py       # Real HTTP health probes and log scrapers
├── executor.py                 # RealExecutionTarget & SimulatorExecutionTarget engine
├── verifier.py                 # Telemetry verifier implementing strict C1 - C5 rules
├── incidents.py                # Incident lifecycle state machine & action fingerprinting
├── sim_env.py                  # In-process simulated microservice environment
│
├── test_application/           # Real local Payment Gateway target (port 9001)
│   ├── main.py                 # FastAPI payment processor app
│   ├── database.py             # Genuine SQLite DatabasePool (5 connections max)
│   ├── services.py             # Upstream fraud SLA timeout & webhook handlers
│   ├── test_standalone.py      # Standalone integration tests for 9001
│   └── README.md               # Target application documentation
│
├── scripts/
│   └── start_dev.py            # Automated multi-service development launcher
│
├── tests/                      # Automated test suite
│   ├── test_workflow.py        # 9-scenario integration test suite
│   ├── test_ingestion.py       # Ingestion & deterministic detection tests
│   ├── test_investigation.py   # RCA, timeline, and hypothesis tests
│   ├── test_live_investigation.py # Live end-to-end investigation against port 9001
│   └── test_canonical_pipeline.py # Canonical incident lifecycle tests
│
├── seed_incidents.json         # Seed incident memories for local fallback
├── requirements.txt            # Project dependencies
├── DEV_STARTUP.md              # Startup, port mapping, and troubleshooting guide
└── README.md                   # System documentation (this file)
```

---

## 🔌 Port Map

| Port | Service | Description | Health Endpoint |
| :---: | :--- | :--- | :--- |
| **9001** | **Target Microservice** | Real local Payment Gateway test application | `GET http://127.0.0.1:9001/health` |
| **8001** | **Agent Backend** | FastAPI backend with memory & lifecycle APIs | `GET http://127.0.0.1:8001/health` |
| **8501** | **SRE Control Plane** | Streamlit web frontend | `GET http://127.0.0.1:8501/_stcore/health` |

---

## 🚀 Installation & Quickstart Guide

### 1. Prerequisites
- Python 3.10, 3.11, or 3.12
- Git
- Virtual environment tool (`venv`)

### 2. Environment Setup

```bash
# Enter project directory
cd aurex

# Create and activate virtual environment
python -m venv venv
.\venv\Scripts\activate          # Windows PowerShell
# source venv/bin/activate       # Linux / macOS

# Install all dependencies
pip install -r requirements.txt
```

### 3. Configure Environment Variables (`.env`)

Copy `.env.example` to `.env` and configure your credentials:

```env
# Groq LLM API Key (Required for live LLM diagnosis & synthesis)
GROQ_API_KEY=gsk_your_groq_api_key_here

# Hindsight Memory Service (Optional; falls back to local memory if offline)
HINDSIGHT_API_URL=http://localhost:8888
HINDSIGHT_API_KEY=

# Backend & Simulator Settings
BACKEND_URL=http://127.0.0.1:8001
ENABLE_SIMULATOR=0
JWT_SECRET_KEY=your_random_secret_here
```

> **Note on Fallbacks:** If `GROQ_API_KEY` or `HINDSIGHT_API_URL` are omitted, the agent seamlessly operates using deterministic RCA and local keyword-overlap fallback memory without crashing.

---

### 4. Running the Complete System

#### Option A: One-Command Automatic Startup (Recommended)

Run the automated orchestrator from the repository root:

```bash
python scripts/start_dev.py
```

This starts the real target application (9001), the agent backend (8001), and the Streamlit frontend (8501). It validates ports, checks health endpoints, and reuses already-running services safely. Press `Ctrl+C` to terminate all launched processes cleanly.

#### Option B: Manual Startup (Separate Terminals)

**Terminal 1 — Target Microservice (Port 9001):**
```powershell
python -m uvicorn test_application.main:app --host 127.0.0.1 --port 9001
```

**Terminal 2 — Agent Backend (Port 8001):**
```powershell
$env:ENABLE_SIMULATOR = "0"; python -m uvicorn main:app --host 127.0.0.1 --port 8001
```

**Terminal 3 — Streamlit Frontend (Port 8501):**
```powershell
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

Open your browser to [http://localhost:8501](http://localhost:8501).

---

## 🧪 Automated Test Suites

The codebase includes comprehensive test suites covering unit, integration, security, and end-to-end scenarios.

### 1. Workflow & State Machine Tests (9 Scenarios)
Validates execution gates, fingerprint mismatches, immutable approvals, and recovery loops:
```bash
pytest tests/test_workflow.py -v
```
- `test_1_execute_before_approval_409_sim_unchanged` — Verifies execution without approval returns 409.
- `test_2_reject_then_execute_409` — Verifies rejected actions cannot execute.
- `test_3_approve_then_approve_or_reject_again_409_immutable` — Proves approval state is strictly immutable.
- `test_4_action_override_fingerprint_mismatch_409` — Proves parameter tampering is rejected.
- `test_5_bad_deploy_rollback_deployment_success` — Tests successful deployment rollback.
- `test_6_db_pool_exhaustion_restart_uncertain` — Verifies intermediate `UNCERTAIN` verifier state.
- `test_7_bad_deploy_restart_failure_and_retry` — Tests failure loop and subsequent recovery.
- `test_8_three_failed_attempts_escalated` — Verifies escalation after 3 unsuccessful attempts.
- `test_9_invalid_action_type_rejected_by_engine` — Verifies non-allowlisted actions are rejected.

### 2. Ingestion & Deterministic Detection Tests
Validates real HTTP scraping, port isolation, and error classification:
```bash
pytest tests/test_ingestion.py -v
```

### 3. Investigation & RCA Tests
Validates timeline extraction, regex signature matching, and hypothesis generation:
```bash
pytest tests/test_investigation.py -v
```

### 4. End-to-End Live Application Inspection Test
Tests the full inspection, diagnosis, and RCA pipeline against the running 9001 target:
```bash
python test_live_investigation.py
```

---

## 🛡️ Key Architectural Guarantees & Design Principles

1. **Store Negative Results as First-Class Memories:**  
   A failed remediation buried inside a resolved postmortem is nearly impossible to retrieve. Storing failed attempts as separate records ensures retrieval queries directly surface what *not* to do.
2. **Empirical Verification Over Model Interpretation:**  
   The reason for failure comes from the deterministic verifier measuring real telemetry, never from the language model's imagination.
3. **Tri-State Verification (`SUCCESS`, `FAILURE`, `UNCERTAIN`):**  
   A partial improvement (e.g., dropping error rate from 90% to 30%) must never be recorded as a confirmed fix. The `UNCERTAIN` state prevents corrupting memory with partial solutions.
4. **Cryptographic Action Binding:**  
   Every proposed action is hashed using SHA-256. Approval is bound to that exact fingerprint, guaranteeing that the action approved by the engineer is the exact action applied to infrastructure.
5. **Visible Degradation Over Silent Failure:**  
   If Hindsight or Groq are offline, the agent continues operating through local keyword fallbacks and deterministic RCA, while visibly marking responses with `memory_source: "local_fallback"` on the control plane.
