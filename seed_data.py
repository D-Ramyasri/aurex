"""
Seed Data Script for Incident Response Memory Agent.
Generates 15 realistic synthetic past production incidents using Groq (model: openai/gpt-oss-120b),
saves them to seed_incidents.json, and retains each in Hindsight memory system (bank_id="incident-response").
"""

import json
import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_data")

# Fallback realistic incidents in case Groq API key is not yet set or fails
FALLBACK_INCIDENTS = [
    {
        "service": "payment-gateway",
        "symptom": "Payment checkout endpoints returning HTTP 504 Gateway Timeout during peak hours; stripe webhook processing stalled.",
        "root_cause": "Third-party payment provider webhook latency exceeded internal 2-second timeout, exhausting upstream synchronous connection pools.",
        "fix": "Increased circuit breaker timeout threshold from 2s to 8s and switched webhook ingestion to an asynchronous Redis task queue.",
        "resolution_time_minutes": 35
    },
    {
        "service": "auth-service",
        "symptom": "Users randomly logged out across mobile and web; JWT verification returning 500 Internal Server Error.",
        "root_cause": "Redis node holding token revocation blacklist ran out of memory (OOM) because TTL expiration keys were omitted during batch session invalidations.",
        "fix": "Configured Redis eviction policy to allkeys-lru, purged stale revocation tokens, and added explicit 24-hour TTL to revocation keys.",
        "resolution_time_minutes": 25
    },
    {
        "service": "database-cluster",
        "symptom": "API servers reporting 'FATAL: remaining connection slots are reserved for non-replication superuser connections' on PostgreSQL primary.",
        "root_cause": "Background reporting microservice spawned unclosed long-lived queries marked 'idle in transaction', exhausting all 500 database pool slots.",
        "fix": "Configured 'idle_in_transaction_session_timeout = 30000' (30s) in PostgreSQL and bounced PgBouncer connection poolers.",
        "resolution_time_minutes": 20
    },
    {
        "service": "inventory-service",
        "symptom": "Checkout API failing with HTTP 500 and Postgres error 'deadlock detected' during promotional flash sale.",
        "root_cause": "Concurrent transactions reserved inventory rows in arbitrary order, causing circular wait conditions across item rows.",
        "fix": "Enforced sorting of item/SKU IDs deterministically before acquiring row-level 'SELECT FOR UPDATE' locks, and added retry with jitter.",
        "resolution_time_minutes": 45
    },
    {
        "service": "notification-worker",
        "symptom": "RabbitMQ queue length surging past 150,000 unacknowledged messages; CPU at 100% on worker pods.",
        "root_cause": "Malformed push notification payload containing unescaped UTF-8 characters caused unhandled JSON parser exceptions, looping without ACKs.",
        "fix": "Routed unparseable messages to a Dead Letter Exchange (DLX) after 3 failed attempts and patched parser with try-catch sanitation.",
        "resolution_time_minutes": 30
    },
    {
        "service": "search-indexer",
        "symptom": "Elasticsearch cluster health turned RED; document indexing rejected with 429 Too Many Requests.",
        "root_cause": "Disk utilization crossed 85% high watermark on two data nodes, causing Elasticsearch to block index writes and unassign replica shards.",
        "fix": "Deleted old log indices past 30 days retention policy, expanded EBS volume sizes by 100GB, and rerouted unassigned shards.",
        "resolution_time_minutes": 40
    },
    {
        "service": "api-gateway",
        "symptom": "Traffic returning Nginx 502 Bad Gateway intermittently after blue-green deployment of user-profile service.",
        "root_cause": "Nginx cached upstream pod IP addresses at startup and did not re-resolve DNS when Kubernetes pods rotated IPs.",
        "fix": "Updated Nginx configuration to use dynamic DNS resolution using 'resolver kube-dns.kube-system.svc.cluster.local valid=10s'.",
        "resolution_time_minutes": 15
    },
    {
        "service": "order-processor",
        "symptom": "Kafka consumer group lag increasing by 5,000 messages/min; consumer pods continuously restarting.",
        "root_cause": "Long-running PDF receipt generation inside consumer loop exceeded max.poll.interval.ms (30s), triggering continuous consumer group rebalances.",
        "fix": "Offloaded PDF generation to an async worker queue and increased Kafka consumer 'max.poll.interval.ms' to 300,000 (5 minutes).",
        "resolution_time_minutes": 50
    },
    {
        "service": "billing-cron",
        "symptom": "Multiple customers charged twice for monthly subscription invoices on the 1st of the month.",
        "root_cause": "Kubernetes CronJob concurrencyPolicy was set to 'Allow', causing a delayed previous run to overlap with the new scheduled job.",
        "fix": "Set concurrencyPolicy to 'Forbid', implemented distributed Redlock on billing accounts, and added unique transaction idempotency keys.",
        "resolution_time_minutes": 60
    },
    {
        "service": "user-avatar-service",
        "symptom": "Profile avatar image uploads failing with HTTP 403 Forbidden 'AccessDenied' to AWS S3.",
        "root_cause": "IAM role trust policy on EKS service account was overwritten during Terraform plan apply, removing s3:PutObject permissions.",
        "fix": "Restored IAM trust relationship policy for EKS service account via Terraform and reattached the S3 PutObject bucket policy.",
        "resolution_time_minutes": 20
    },
    {
        "service": "frontend-cdn",
        "symptom": "Static assets (bundle.js, css) returning 504 Gateway Timeout globally on Cloudflare edge.",
        "root_cause": "Origin shield proxy pods hit max file descriptor (ulimit -n) limit of 1024, rejecting keep-alive connections from CDN edges.",
        "fix": "Increased ulimit nofile to 65535 on origin proxy containers and enabled Cloudflare stale-while-revalidate caching.",
        "resolution_time_minutes": 25
    },
    {
        "service": "analytics-pipeline",
        "symptom": "Apache Spark streaming jobs failing with ExitCode 137 (OOMKilled) on worker nodes.",
        "root_cause": "A single enterprise tenant generated 90% of log traffic, creating extreme data skew on partition key 'tenant_id'.",
        "fix": "Salted the partition key with a random suffix (0-9) to distribute partitions evenly across executors before shuffle operations.",
        "resolution_time_minutes": 55
    },
    {
        "service": "email-service",
        "symptom": "Transactional password reset and OTP emails delayed by up to 45 minutes; SMTP error '421 Too many concurrent connections'.",
        "root_cause": "Shared outbound IP was throttled by major email providers due to sudden spike in marketing campaign emails from same IP.",
        "fix": "Separated transactional traffic onto dedicated IP pool and migrated transactional email sending to HTTP REST API with rate limits.",
        "resolution_time_minutes": 35
    },
    {
        "service": "recommendation-engine",
        "symptom": "Homepage API p99 latency spiked from 40ms to 1800ms; CPU saturation on Vector DB nodes.",
        "root_cause": "Vector database index was accidentally dropped during schema migration, causing queries to fall back to brute-force flat scans.",
        "fix": "Recreated HNSW index on embeddings collection with M=16, efConstruction=200 and enabled warm cache preloading.",
        "resolution_time_minutes": 40
    },
    {
        "service": "file-export-service",
        "symptom": "Large CSV report downloads failing halfway with 'No space left on device' (ENOSPC).",
        "root_cause": "Worker nodes stored multi-gigabyte temporary CSV export files in the root volume (/tmp) without automated cleanups.",
        "fix": "Refactored export pipeline to stream generated rows directly to S3 multipart upload without buffering full file to local disk.",
        "resolution_time_minutes": 30
    }
]


def generate_incidents_with_groq() -> list[dict]:
    """Generate 15 realistic incidents using Groq API (openai/gpt-oss-120b)."""
    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        logger.warning("GROQ_API_KEY not found in environment. Using high-quality synthetic fallback incidents.")
        return FALLBACK_INCIDENTS

    try:
        from groq import Groq
        logger.info("Calling Groq API (model: openai/gpt-oss-120b) to generate 15 realistic production incidents...")
        client = Groq(api_key=groq_api_key)

        prompt = """You are a Principal Site Reliability Engineer. Generate exactly 15 realistic production incident post-mortems across modern microservices (e.g., auth, database, payment, cache, kafka, kubernetes, cdn, search, etc.).
Output ONLY a raw valid JSON list of 15 objects, with NO surrounding markdown backticks and NO commentary.
Each object must have exactly these keys:
{
  "service": "string (name of service)",
  "symptom": "string (clear error message, HTTP codes, or alerts observed)",
  "root_cause": "string (underlying technical cause)",
  "fix": "string (actionable remediation or architectural fix applied)",
  "resolution_time_minutes": integer
}"""

        completion = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": "You are a JSON-generating SRE expert."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=4000
        )

        response_content = completion.choices[0].message.content.strip()
        # Strip potential markdown code blocks
        if response_content.startswith("```"):
            response_content = response_content.strip("`")
            if response_content.startswith("json"):
                response_content = response_content[4:].strip()

        incidents = json.loads(response_content)
        if isinstance(incidents, list) and len(incidents) >= 10:
            logger.info("Successfully generated %d incidents via Groq.", len(incidents))
            return incidents[:15]
        else:
            logger.warning("Groq output did not match expected structure. Using fallback incidents.")
            return FALLBACK_INCIDENTS
    except Exception as e:
        logger.error("Failed to generate incidents via Groq: %s. Using fallback incidents.", e)
        return FALLBACK_INCIDENTS


def save_incidents_to_file(incidents: list[dict], filepath: Path) -> None:
    """Save incident list to JSON file."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(incidents, f, indent=2)
    logger.info("Saved %d incidents to %s", len(incidents), filepath)


def seed_hindsight_memory(incidents: list[dict], bank_id: str = "incident-response") -> None:
    """Retain each incident in the Hindsight memory system."""
    hindsight_url = os.getenv("HINDSIGHT_API_URL", "http://localhost:8888")
    hindsight_key = os.getenv("HINDSIGHT_API_KEY") or None

    timeout = 2.5 if ("localhost" in hindsight_url or "127.0.0.1" in hindsight_url) else 25.0
    client = None
    try:
        from hindsight_client import Hindsight
        client = Hindsight(
            base_url=hindsight_url,
            api_key=hindsight_key,
            timeout=timeout,
            max_attempts=1
        )
    except Exception as e:
        logger.error("Failed to initialize Hindsight client: %s", e)
        return

    success_count = 0
    failure_count = 0

    try:
        for idx, inc in enumerate(incidents, 1):
            service = inc.get("service", "unknown-service")
            symptom = inc.get("symptom", "")
            root_cause = inc.get("root_cause", "")
            fix = inc.get("fix", "")
            res_time = inc.get("resolution_time_minutes", 15)

            # Content combining symptom + root_cause + fix into a natural sentence
            content = (
                f"Production incident in {service}: "
                f"Symptom was '{symptom}'. "
                f"Root cause identified: {root_cause} "
                f"Remediation fix applied: {fix} "
                f"Total time to resolution: {res_time} minutes."
            )

            context_str = f"service: {service}, resolution_time: {res_time}m"
            metadata = {
                "service": str(service),
                "resolution_time_minutes": str(res_time),
                "source": "seed_data"
            }

            try:
                logger.info("[%d/%d] Retaining memory for %s...", idx, len(incidents), service)
                response = client.retain(
                    bank_id=bank_id,
                    content=content,
                    context=context_str,
                    metadata=metadata
                )
                logger.info("  -> Retained successfully (status: %s)", getattr(response, "success", True))
                success_count += 1
            except Exception as e:
                logger.warning("  -> Retain call failed for incident #%d (%s): %s", idx, service, e)
                failure_count += 1
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass

    logger.info("Seeding completed: %d succeeded, %d failed out of %d total.", success_count, failure_count, len(incidents))


def main():
    script_dir = Path(__file__).resolve().parent
    seed_file = script_dir / "seed_incidents.json"

    logger.info("Starting seed data generation...")
    incidents = generate_incidents_with_groq()
    save_incidents_to_file(incidents, seed_file)
    seed_hindsight_memory(incidents, bank_id="incident-response")
    logger.info("Seed process finished.")


if __name__ == "__main__":
    main()
