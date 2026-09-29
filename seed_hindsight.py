"""
Seeds the 10 core payment-gateway historical incidents into Hindsight memory bank 'incident-response'.
Safe to run multiple times: checks if a matching memory is already retained before adding.
"""
import os
import sys
import json
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_hindsight")

BANK_ID = "incident-response"

PAYMENT_GATEWAY_HISTORICAL_INCIDENTS = [
    {
        "service": "payment-gateway",
        "title": "Upstream fraud engine timeout",
        "symptom": "Upstream fraud engine timed out after 1.0s latency spike, HTTP 504 Gateway Timeout returned on payment authorization",
        "root_cause": "Upstream acquirer fraud verification network latency exceeded client synchronous SLA timeout window (1.0s)",
        "fix": "Configured circuit breaker on acquirer fraud client, enabled bounded retry with exponential backoff (max 3 attempts), and increased client timeout window from 1.0s to 3.0s",
        "resolution_time_minutes": 35
    },
    {
        "service": "payment-gateway",
        "title": "Database connection pool exhaustion",
        "symptom": "Database connection pool exhausted, ConnectionPoolTimeoutError, HTTP 503 Service Unavailable returned",
        "root_cause": "Connection pool reached maximum concurrent capacity (5/5) with worker threads blocked waiting for connection acquisition",
        "fix": "Increased DatabasePool max_connections from 5 to 15, ensured try-finally connection release in all query handlers, and added wait queue monitoring alerts",
        "resolution_time_minutes": 25
    },
    {
        "service": "payment-gateway",
        "title": "Duplicate webhook delivery",
        "symptom": "Duplicate webhook delivery detected for event_id, UNIQUE constraint failed on webhook_events database table",
        "root_cause": "Merchant payment webhook dispatcher retried unacknowledged events without idempotency handling in receiver",
        "fix": "Added idempotent duplicate detection returning HTTP 200 OK immediately for previously processed event_ids without re-executing business logic",
        "resolution_time_minutes": 15
    },
    {
        "service": "payment-gateway",
        "title": "Webhook signature verification failure",
        "symptom": "Webhook signature verification failed with HMAC mismatch, HTTP 401 Unauthorized on incoming payment notifications",
        "root_cause": "Webhook signing secret mismatch or mutated payload raw bytes prior to HMAC-SHA256 signature comparison",
        "fix": "Synchronized webhook secret key in secret manager and preserved raw request body bytes for signature verification",
        "resolution_time_minutes": 20
    },
    {
        "service": "payment-gateway",
        "title": "Upstream dependency HTTP 504",
        "symptom": "Upstream dependency HTTP 504 Gateway Timeout during payment settlement transaction processing",
        "root_cause": "External banking acquirer gateway gateway timeout under heavy batch settlement processing",
        "fix": "Implemented asynchronous decoupled settlement queue with dead-letter queue (DLQ) retry mechanism instead of synchronous HTTP blocking",
        "resolution_time_minutes": 40
    },
    {
        "service": "payment-gateway",
        "title": "Slow database / database timeout",
        "symptom": "Slow database queries and database query timeout after 5000ms on payments transaction ledger",
        "root_cause": "Missing composite index on (account_id, created_at) causing sequential table scan during transaction history queries",
        "fix": "Added composite B-tree index on payments ledger table and tuned query statement timeout threshold to 2000ms",
        "resolution_time_minutes": 30
    },
    {
        "service": "payment-gateway",
        "title": "Webhook worker failure",
        "symptom": "Background WebhookWorker failure and thread crash with sqlite3.OperationalError database is locked",
        "root_cause": "Concurrent writes between main API thread and background worker thread caused SQLite database lock contention",
        "fix": "Configured SQLite WAL (Write-Ahead Logging) mode and increased busy_timeout to 5000ms to allow concurrent reads and serialized writes",
        "resolution_time_minutes": 20
    },
    {
        "service": "payment-gateway",
        "title": "Service dependency degradation",
        "symptom": "Service dependency degradation with acquirer latency increasing by 400ms across payment authorizations",
        "root_cause": "Regional DNS latency degradation and unoptimized TLS handshake negotiation on upstream partner endpoints",
        "fix": "Enabled HTTP/2 persistent connection keep-alive pooling and pre-warmed DNS resolver cache",
        "resolution_time_minutes": 35
    },
    {
        "service": "payment-gateway",
        "title": "Payment processing failure caused by external dependency",
        "symptom": "Payment processing failure caused by external dependency returning HTTP 503 Service Unavailable",
        "root_cause": "Downstream card network gateway maintenance window not communicated in automated health status",
        "fix": "Implemented dynamic multi-acquirer failover routing to instantly reroute transactions to backup payment processor",
        "resolution_time_minutes": 45
    },
    {
        "service": "payment-gateway",
        "title": "Circuit-breaker/recovery incident",
        "symptom": "Circuit-breaker tripped open after consecutive upstream timeouts, rejecting outbound requests",
        "root_cause": "Circuit breaker threshold was overly sensitive (failure_threshold=3) causing premature trips during brief network blips",
        "fix": "Adjusted circuit breaker failure threshold to 5 consecutive errors over 30s window and configured half-open probe recovery interval",
        "resolution_time_minutes": 25
    }
]


def seed_hindsight():
    from hindsight_client import Hindsight
    base_url = os.getenv("HINDSIGHT_API_URL", "http://localhost:8888")
    api_key = os.getenv("HINDSIGHT_API_KEY") or None
    client = Hindsight(base_url=base_url, api_key=api_key, timeout=25.0, max_attempts=2)

    logger.info("Connecting to Hindsight at %s (bank: %s)...", base_url, BANK_ID)

    # First, recall existing memories to avoid duplicates
    existing_memories = []
    try:
        recall_resp = client.recall(bank_id=BANK_ID, query="payment gateway incident")
        existing_memories = getattr(recall_resp, "results", []) or []
        logger.info("Found %d existing memories in bank '%s'", len(existing_memories), BANK_ID)
    except Exception as e:
        logger.warning("Could not recall existing memories: %s", e)

    stored_records = []

    for idx, inc in enumerate(PAYMENT_GATEWAY_HISTORICAL_INCIDENTS, 1):
        title = inc["title"]
        symptom = inc["symptom"]
        root_cause = inc["root_cause"]
        fix = inc["fix"]
        res_time = inc["resolution_time_minutes"]

        # Check if already present
        already_present = any(
            title.lower() in getattr(m, "text", "").lower() or symptom[:40].lower() in getattr(m, "text", "").lower()
            for m in existing_memories
        )

        content = (
            f"Production incident in payment-gateway: [{title}] "
            f"Symptom was '{symptom}'. "
            f"Root cause identified: {root_cause}. "
            f"Remediation fix applied: {fix}. "
            f"Total time to resolution: {res_time} minutes."
        )

        if already_present:
            logger.info("[%d/10] Already seeded: %s", idx, title)
        else:
            logger.info("[%d/10] Retaining in Hindsight: %s...", idx, title)
            try:
                resp = client.retain(
                    bank_id=BANK_ID,
                    content=content,
                    context=f"service: payment-gateway, title: {title}",
                    metadata={
                        "service": "payment-gateway",
                        "title": title,
                        "symptom": symptom,
                        "root_cause": root_cause,
                        "fix": fix,
                        "resolution": fix,
                        "resolution_time_minutes": str(res_time)
                    }
                )
                logger.info("  -> Retained: %s", getattr(resp, "success", True))
            except Exception as e:
                logger.error("  -> Retain failed for %s: %s", title, e)

    # Now verify recall for each of the 10 incidents and retrieve actual memory IDs
    logger.info("\n=== VERIFYING STORED HINDSIGHT MEMORIES & EXTRACTING ACTUAL IDS ===")
    verified_ids = {}

    for idx, inc in enumerate(PAYMENT_GATEWAY_HISTORICAL_INCIDENTS, 1):
        q = inc["title"]
        try:
            res = client.recall(bank_id=BANK_ID, query=q)
            results = getattr(res, "results", []) or []
            if results:
                matched = results[0]
                m_id = getattr(matched, "id", None)
                m_text = getattr(matched, "text", "")
                verified_ids[inc["title"]] = {
                    "id": m_id,
                    "title": inc["title"],
                    "root_cause": inc["root_cause"],
                    "remediation": inc["fix"],
                    "text": m_text[:120]
                }
                logger.info("Match for '%s': ID=%s", inc["title"], m_id)
            else:
                logger.warning("No match found for '%s'", q)
        except Exception as e:
            logger.error("Recall verification failed for %s: %s", q, e)

    client.close()
    
    # Save seed_incidents.json as well so local bank is in 100% sync
    seed_json_path = Path(__file__).resolve().parent / "seed_incidents.json"
    with open(seed_json_path, "w", encoding="utf-8") as f:
        json.dump(PAYMENT_GATEWAY_HISTORICAL_INCIDENTS, f, indent=2)
    logger.info("Updated seed_incidents.json with all 10 historical incidents.")

    return verified_ids


if __name__ == "__main__":
    ids = seed_hindsight()
    print("\nFINAL ACTUAL HINDSIGHT MEMORY IDS:")
    print(json.dumps(ids, indent=2))
