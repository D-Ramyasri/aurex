# 💳 Payment Gateway API (Realistic Monitored Service)

A realistic, production-style Payment Gateway microservice used as the target application for the **Incident Memory & Resolution Agent**.

---

## 🌟 Architecture & Real Components

The service behaves like a small real-world payment processor (similar to a mini-Stripe gateway) on port **9001**:

* **Payment Processing Engine:** Handles real payment requests at `POST /payments`, enforcing authorization, idempotency, fraud verification, account balances, and database transactions.
* **Database & Connection Pool:** Genuine SQLite database (`data/payments.db`) with an active `DatabasePool` (max 5 connections, 2.0s acquire timeout, WAL journal mode).
* **Upstream Dependency:** Pre-authorization Fraud / Acquirer network verification client with a real network timeout SLA (1.0s).
* **Stripe-like Webhooks:** `POST /webhooks/payment` with cryptographic HMAC-SHA256 signature verification and unique event deduplication.
* **Background Worker:** `BackgroundWebhookWorker` thread that polls and processes queued webhook deliveries from the database.
* **Health Monitoring:** `GET /health` probes the live database connection pool and worker threads, returning `HTTP 200` when operational or `HTTP 503` when degraded.
* **Real Runtime Logging:** Keeps live structured logs with timestamps, levels, request IDs (`req_id`), component tags, and genuine stack traces. `GET /logs/recent` exposes recent entries to registered collectors.

---

## 🚀 How to Run the Service

From the repository root:
```bash
python -m uvicorn test_application.main:app --host 127.0.0.1 --port 9001
```
Or directly:
```bash
python test_application/main.py
```

* **Interactive Dashboard:** `http://127.0.0.1:9001/`
* **Health Probe:** `http://127.0.0.1:9001/health`
* **Recent Logs:** `http://127.0.0.1:9001/logs/recent`

---

## 💥 Realistic Runtime Failure Scenarios (No Mocking)

Every failure condition below originates from a **real runtime event** in the application stack:

### 1. Normal Payment (HTTP 201)
Normal transaction processing with a standard card:
```bash
curl -X POST http://127.0.0.1:9001/payments \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "acc_merch_001",
    "amount": 49.99,
    "currency": "USD",
    "card_number": "4242-4242-4242-4242",
    "cvv": "123"
  }'
```
* **Result:** `HTTP 201 Created`
* **Real Log:** `[INFO] [payment-gateway-test] [req_id=...] Payment pay_... succeeded (amount=49.99 USD)`

---

### 2. Upstream Dependency Timeout (HTTP 504)
Testing with a card routed through an unresponsive upstream node (`9999-...`):
```bash
curl -X POST http://127.0.0.1:9001/payments \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "acc_merch_001",
    "amount": 250.00,
    "currency": "USD",
    "card_number": "9999-0000-0000-1234",
    "cvv": "999"
  }'
```
* **Result:** `HTTP 504 Gateway Timeout`
* **Real Log:**
  ```
  [ERROR] [payment-gateway-test.services] [req_id=...] Dependency failure: Upstream fraud engine timed out after 1.0s while verifying card 1234
  [ERROR] [payment-gateway-test] [req_id=...] Transaction pay_... failed due to upstream network timeout
  ```

---

### 3. Fraud Verification Decline (HTTP 402)
Testing with a card flagged by the fraud network (`5555-...`):
```bash
curl -X POST http://127.0.0.1:9001/payments \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "acc_merch_001",
    "amount": 1200.00,
    "currency": "USD",
    "card_number": "5555-0000-0000-4321",
    "cvv": "555"
  }'
```
* **Result:** `HTTP 402 Payment Required`
* **Real Log:** `[WARNING] [payment-gateway-test] [req_id=...] Transaction pay_... declined by fraud verification: High-risk score (98/100)`

---

### 4. Database Connection Pool Exhaustion (HTTP 503)
When 5 concurrent workers hold database connections, subsequent requests block and timeout:
```bash
# Hold all 5 connections from the pool
for i in {1..5}; do
  curl -s -X POST http://127.0.0.1:9001/pool/acquire-hold -H "Content-Type: application/json" -d '{"seconds": 4.0}' &
done

# Attempt an immediate payment or health check while pool is saturated
curl -i -X POST http://127.0.0.1:9001/payments \
  -H "Content-Type: application/json" \
  -d '{"amount": 10.0, "card_number": "4242-0000-0000-0000", "cvv": "100"}'
```
* **Result:** `HTTP 503 Service Unavailable`
* **Real Log:**
  ```
  [ERROR] [payment-gateway-test.database] [req_id=...] Connection pool timeout: pool='payment_db' exhausted (active=5/5, timeout=1.5s). Connection request rejected.
  [ERROR] [payment-gateway-test] [req_id=...] Health check returned HTTP 503: Database connection pool 'payment_db' exhausted
  ```

---

### 5. Webhook Signature Verification Failure (HTTP 401)
Sending a webhook with an invalid or tampered `Stripe-Signature` header:
```bash
curl -i -X POST http://127.0.0.1:9001/webhooks/payment \
  -H "Content-Type: application/json" \
  -H "Stripe-Signature: t=1690000000,v1=tampered_signature" \
  -d '{"id": "evt_tampered", "type": "charge.refunded"}'
```
* **Result:** `HTTP 401 Unauthorized`
* **Real Log:** `[ERROR] [payment-gateway-test] [req_id=...] Webhook rejected: Signature mismatch: received 'tampered...'`

---

## 🔍 Investigation Flow for Incident Response Agent

1. **Monitored Application URL:** `http://127.0.0.1:9001`
2. **Log Collection:** `GET /logs/recent` on the registered application's base URL
3. The Incident Response Agent:
   - Probes `GET http://127.0.0.1:9001/health`
  - Collects genuine log entries from `http://127.0.0.1:9001/logs/recent`
   - Detects real runtime errors (`ConnectionPoolTimeoutError`, `UpstreamDependencyTimeoutError`, `sqlite3.IntegrityError`, etc.)
   - Correlates telemetry evidence with symptoms and performs real Root Cause Analysis (RCA).
