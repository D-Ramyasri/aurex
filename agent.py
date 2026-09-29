"""
Incident Response Memory Agent core logic.
Integrates Hindsight memory system (bank_id="incident-response")
with Groq LLM (model: openai/gpt-oss-120b) for incident diagnosis and resolution retention.
"""

import os
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional, Dict, List
from dotenv import load_dotenv

from incidents import create_incident, compute_action_fingerprint, get_incident, update_incident
from canonical_incident import generate_incident_id

# Load environment variables from .env
load_dotenv()

logger = logging.getLogger("incident_agent")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BANK_ID = "incident-response"
GROQ_MODEL = "openai/gpt-oss-120b"

# Local in-memory store as a graceful fallback if external Hindsight server is offline
_local_memory_bank: list[dict[str, Any]] = []


def _load_local_fallback_memories():
    """Populates local memory store from seed_incidents.json with structured fields."""
    global _local_memory_bank
    if _local_memory_bank:
        return
    seed_file = Path(__file__).resolve().parent / "seed_incidents.json"
    if seed_file.exists():
        try:
            with open(seed_file, "r", encoding="utf-8") as f:
                incidents = json.load(f)
                for idx, inc in enumerate(incidents, 1):
                    service = inc.get("service", "service")
                    symptom = inc.get("symptom", "")
                    root_cause = inc.get("root_cause", "")
                    fix = inc.get("fix", "")
                    res_time = inc.get("resolution_time_minutes", 15)

                    # Generate clean titles and concise root causes for Hindsight display
                    s_lower = symptom.lower()
                    if "fraud" in s_lower or "acquirer" in s_lower:
                        title = "Fraud engine timeout"
                        concise_rc = "upstream latency"
                        concise_res = "bounded retry + circuit breaker + timeout adjustment"
                    elif "pool" in s_lower or "database" in s_lower:
                        title = "Database connection pool exhaustion"
                        concise_rc = "connection pool saturated (5/5 active)"
                        concise_res = "scale pool capacity + try-finally release + wait alerts"
                    elif "webhook" in s_lower or "hmac" in s_lower:
                        title = "Webhook signature verification failure"
                        concise_rc = "HMAC secret mismatch or mutated raw payload"
                        concise_res = "synchronize secret key + preserve raw payload bytes"
                    else:
                        title = symptom.split(",")[0][:45]
                        concise_rc = root_cause[:60]
                        concise_res = fix[:60]

                    text = f"Production incident in {service}: Symptom was '{symptom}'. Root cause identified: {root_cause}. Remediation fix applied: {fix}. Total time to resolution: {res_time} minutes."
                    _local_memory_bank.append({
                        "id": f"Memory #{idx}",
                        "title": title,
                        "symptom": symptom,
                        "root_cause": concise_rc,
                        "resolution": concise_res,
                        "full_root_cause": root_cause,
                        "full_fix": fix,
                        "text": text,
                        "type": "experience",
                        "context": f"service: {service}",
                        "metadata": {"service": service, "resolution_time_minutes": str(res_time)}
                    })
        except Exception as e:
            logger.warning("Could not pre-load fallback memories: %s", e)


def get_hindsight_client():
    """Initializes and returns the official Hindsight client."""
    from hindsight_client import Hindsight
    base_url = os.getenv("HINDSIGHT_API_URL", "http://localhost:8888")
    api_key = os.getenv("HINDSIGHT_API_KEY") or None
    # 2.5s timeout for local servers so offline tests fail fast; 20.0s for remote cloud servers
    timeout = 2.5
    return Hindsight(base_url=base_url, api_key=api_key, timeout=timeout, max_attempts=1)


_user_memory_banks: Dict[int, List[Dict[str, Any]]] = {}


def recall_memories_from_hindsight(alert_text: str, return_source: bool = False, user_id: Optional[int] = None) -> Any:
    """
    Calls Hindsight recall scoped to the authenticated user's memory bank.
    Falls back to user-scoped local memory if server is offline.
    """
    recalled_list = []
    client = None
    target_bank = BANK_ID if (user_id in (None, 2)) else f"incident-response-user-{user_id}"

    try:
        client = get_hindsight_client()
        logger.info("Calling Hindsight recall(bank_id='%s', query='%s')...", target_bank, alert_text[:80])
        response = client.recall(bank_id=target_bank, query=alert_text)
        
        results = getattr(response, "results", []) or []
        for idx, r in enumerate(results, 1):
            r_text = getattr(r, "text", str(r))
            r_meta = getattr(r, "metadata", {}) or {}
            
            # Extract structured summary attributes
            title = r_meta.get("title") or getattr(r, "title", None)
            root_cause = r_meta.get("root_cause")
            resolution = r_meta.get("resolution") or r_meta.get("fix")

            t_lower = r_text.lower()
            if not title:
                if "fraud" in t_lower:
                    title = "Upstream fraud engine timeout"
                elif "pool" in t_lower or "connection" in t_lower:
                    title = "Database connection pool exhaustion"
                elif "duplicate webhook" in t_lower:
                    title = "Duplicate webhook delivery"
                elif "signature" in t_lower:
                    title = "Webhook signature verification failure"
                elif "settlement" in t_lower or "504" in t_lower:
                    title = "Upstream dependency HTTP 504"
                elif "slow database" in t_lower or "ledger" in t_lower:
                    title = "Slow database / database timeout"
                elif "worker" in t_lower:
                    title = "Webhook worker failure"
                elif "circuit" in t_lower:
                    title = "Circuit-breaker/recovery incident"
                elif "degradation" in t_lower:
                    title = "Service dependency degradation"
                else:
                    title = f"Historical Incident #{idx}"

            if not root_cause:
                if "root cause identified:" in t_lower:
                    part = r_text.split("Root cause identified:", 1)[1]
                    root_cause = part.split("Remediation", 1)[0].split(". Remediation", 1)[0].strip()
                elif "caused by" in t_lower:
                    part = r_text.split("caused by", 1)[1]
                    root_cause = part.split("; it was", 1)[0].split(". It was", 1)[0].split(";", 1)[0].strip()
                else:
                    root_cause = "Upstream latency / dependency failure"

            if not resolution:
                if "remediation fix applied:" in t_lower:
                    part = r_text.split("Remediation fix applied:", 1)[1]
                    resolution = part.split("Total time", 1)[0].strip().rstrip(".")
                elif "remediated by" in t_lower:
                    part = r_text.split("remediated by", 1)[1]
                    resolution = part.split(". Total", 1)[0].strip().rstrip(".")
                elif "remediation included" in t_lower:
                    part = r_text.split("remediation included", 1)[1]
                    resolution = part.split(". Total", 1)[0].strip().rstrip(".")
                else:
                    resolution = "Bounded retry + circuit breaker + timeout adjustment"

            m_id = getattr(r, "id", None) or f"hsk-{idx}"
            recalled_list.append({
                "id": str(m_id),
                "title": title,
                "symptom": r_meta.get("symptom", r_text[:80]),
                "root_cause": root_cause,
                "resolution": resolution,
                "text": r_text,
                "type": getattr(r, "type", "memory"),
                "context": getattr(r, "context", None),
                "metadata": r_meta,
                "scores": str(getattr(r, "scores", None)) if getattr(r, "scores", None) is not None else None
            })
        logger.info("Hindsight recall returned %d memories.", len(recalled_list))
        if return_source:
            return recalled_list, "hindsight"
        return recalled_list

    except Exception as e:
        logger.warning("Hindsight server call failed (%s). Checking local memory fallback.", e)
        # If user is a new user (not Ramya / 2), use their isolated local memory bank
        if user_id not in (None, 2):
            active_bank = _user_memory_banks.get(int(user_id), [])
            if not active_bank:
                logger.info("New user %s has completely fresh memory bank (0 memories).", user_id)
                if return_source:
                    return [], "local_fallback"
                return []
        else:
            _load_local_fallback_memories()
            active_bank = _local_memory_bank

        # Keyword & term overlap similarity matching across title, symptom, and text
        query_words = set(alert_text.lower().replace("-", " ").replace("_", " ").replace("\"", "").replace("'", "").split())
        scored = []
        for item in active_bank:
            searchable_text = f"{item.get('title', '')} {item.get('symptom', '')} {item.get('root_cause', '')} {item['text']}".lower()
            overlap = sum(2 for w in query_words if len(w) > 3 and w in item.get('title', '').lower())
            overlap += sum(1 for w in query_words if len(w) > 3 and w in searchable_text)
            if overlap > 0:
                scored.append((overlap, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        fallback_results = [item for _, item in scored[:3]]
        logger.info("Local fallback recall retrieved %d memories.", len(fallback_results))
        if return_source:
            return fallback_results, "local_fallback"
        return fallback_results
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass


ALLOWED_ACTIONS = {
    "restart_service",
    "scale_replicas",
    "set_config",
    "flush_cache",
    "rollback_deployment"
}

ALLOWED_SERVICES = {
    "payment-gateway",
    "auth-service",
    "database-cluster",
    "api-gateway",
    "checkout-service",
    "order-service"
}


def get_allowed_services() -> set[str]:
    """Returns static allowed services combined with any dynamically registered services."""
    services = set(ALLOWED_SERVICES)
    try:
        from app_registry import list_applications
        for app in list_applications():
            if getattr(app, "service_id", None):
                services.add(app.service_id)
            if getattr(app, "application_id", None):
                services.add(app.application_id)
    except Exception:
        pass
    return services


def normalize_service_name(svc: Optional[str]) -> str:
    """Normalizes service names, aliases, and approximations to allowed or registered services."""
    if not svc:
        return "api-gateway"
    s = str(svc).lower().strip().replace("_", "-")
    allowed = get_allowed_services()
    if s in allowed:
        return s
    if any(k in s for k in ["test", "payment-test"]):
        if "payment-gateway-test" in allowed:
            return "payment-gateway-test"
        return "payment-gateway"
    if any(k in s for k in ["pay", "stripe", "billing"]):
        return "payment-gateway"
    if any(k in s for k in ["auth", "token", "jwt", "login", "identity"]):
        return "auth-service"
    if any(k in s for k in ["data", "db", "sql", "postgres", "cluster", "pool"]):
        return "database-cluster"
    if any(k in s for k in ["check", "order", "cart"]):
        return "checkout-service"
    if any(k in s for k in ["gate", "api", "ingress", "proxy", "router"]):
        return "api-gateway"
    return "api-gateway"


def extract_json_object(text: str) -> Optional[dict]:
    """Robustly extracts a JSON dictionary from LLM string output."""
    if not text:
        return None
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # Strip markdown fences ```json ... ``` or ``` ... ```
    if "```" in text:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass

    # Search for outermost { and }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace:last_brace + 1]
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return None


def get_fallback_action(extracted_details: dict[str, Any], previous_failures: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """Generates a safe, allowlisted fallback remediation action based on service and symptom."""
    svc = normalize_service_name(extracted_details.get("service"))
    symptom = str(extracted_details.get("symptom_type", "")).lower()
    summary = str(extracted_details.get("summary", "")).lower()

    failed_actions = set()
    if previous_failures:
        for f in previous_failures:
            act = f.get("action") or {}
            if isinstance(act, dict) and act.get("type"):
                failed_actions.add(act["type"])

    candidate_actions = []
    if any(k in symptom or k in summary for k in ["deploy", "version", "syntax", "null", "typeerror", "regression"]):
        candidate_actions.append({
            "type": "rollback_deployment",
            "target_service": svc,
            "params": {},
            "rationale": f"Roll back deployment of {svc} to clear software regression."
        })
    if any(k in symptom or k in summary for k in ["timeout", "latency", "load", "504", "capacity", "traffic"]):
        candidate_actions.append({
            "type": "scale_replicas",
            "target_service": svc,
            "params": {"replicas": 4},
            "rationale": f"Scale {svc} replicas to absorb elevated traffic and resolve latency."
        })
    if any(k in symptom or k in summary for k in ["cache", "redis", "oom", "stale", "memory"]):
        candidate_actions.append({
            "type": "flush_cache",
            "target_service": svc,
            "params": {},
            "rationale": f"Flush cache on {svc} to clear invalid cached state or memory saturation."
        })
    if any(k in symptom or k in summary for k in ["pool", "connection", "exhaustion", "database"]):
        candidate_actions.append({
            "type": "set_config",
            "target_service": svc,
            "params": {"key": "pool_size", "value": 50},
            "rationale": f"Increase pool_size config on {svc} to resolve connection exhaustion."
        })

    candidate_actions.extend([
        {"type": "restart_service", "target_service": svc, "params": {}, "rationale": f"Restart {svc} to restore service to clean initial state."},
        {"type": "rollback_deployment", "target_service": svc, "params": {}, "rationale": f"Roll back {svc} to previous known-good deployment version."},
        {"type": "scale_replicas", "target_service": svc, "params": {"replicas": 3}, "rationale": f"Scale {svc} to provide additional capacity."},
        {"type": "flush_cache", "target_service": svc, "params": {}, "rationale": f"Flush cache on {svc} to refresh transient state."}
    ])

    for candidate in candidate_actions:
        if candidate["type"] not in failed_actions and candidate["type"] in ALLOWED_ACTIONS:
            return candidate

    return {"type": "restart_service", "target_service": svc, "params": {}, "rationale": f"Fallback remediation for {svc}."}


def call_groq_llm(prompt: str) -> str:
    """Invokes Groq chat completion API using model openai/gpt-oss-120b."""
    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        logger.warning("GROQ_API_KEY not configured. Generating offline diagnostic synthesis.")
        return (
            "### Likely Root Cause\n"
            "Deterministic SRE analysis based on recalled memory: Upstream timeouts or connection pool saturation.\n\n"
            "### Suggested Fix\n"
            "Adjust circuit breaker thresholds, scale worker pool, and inspect connection leaks.\n\n"
            "### Past Incident Reference\n"
            "Resembles past incident referenced in memory bank (set GROQ_API_KEY for live Groq gpt-oss-120b synthesis)."
        )

    from groq import Groq
    client = Groq(api_key=groq_api_key)

    system_instruction = (
        "You are an elite Senior Staff Site Reliability Engineer and Incident Commander. "
        "Your mission is to rapidly diagnose production alerts using historical incident memories.\n"
        "Provide a crisp, actionable diagnosis with the following Markdown headings:\n"
        "### Likely Root Cause\n"
        "### Suggested Fix\n"
        "### Past Incident Reference\n"
        "In 'Past Incident Reference', explicitly state which past incident (if any) this resembles, "
        "explaining the architectural parallels. If no similar past incident was found, explicitly state: "
        "'No similar past incidents found in memory bank.'"
    )

    for attempt in range(2):
        try:
            completion = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=800
            )
            return completion.choices[0].message.content.strip()
        except Exception as e:
            err_msg = str(e).lower()
            if ("rate_limit" in err_msg or "429" in err_msg) and attempt == 0:
                logger.warning("Groq rate limit in call_groq_llm, backing off 2.5s...")
                time.sleep(2.5)
                continue
            logger.error("Error invoking Groq LLM: %s", e)
            raise


def extract_incident_details(alert_text: str) -> dict[str, Any]:
    """Extracts structured incident details and keeps the legacy keys while adding richer alert metadata."""
    norm_initial = normalize_service_name(alert_text)
    fallback_details = {
        "service": norm_initial,
        "severity": "medium",
        "symptom_type": "unknown",
        "summary": alert_text[:100].strip() if alert_text else "No summary available",
        "errors": [],
        "impact": "No impact summary available",
        "affected_components": [],
        "incident_type": "application"
    }

    if not alert_text or not alert_text.strip():
        return fallback_details

    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        logger.warning("GROQ_API_KEY not set. Using fallback incident details.")
        return fallback_details

    try:
        from groq import Groq
        client = Groq(api_key=groq_api_key)

        prompt = f"""Extract structured incident metadata from the following production alert.
Choose the closest matching service from the ALLOWED SERVICES list:
- payment-gateway
- auth-service
- database-cluster
- api-gateway
- checkout-service

Return ONLY a valid JSON object with no surrounding markdown and no commentary.
Format:
{{
  "service": "one of the 5 allowed services listed above",
  "severity": "one of: low, medium, high, critical",
  "symptom_type": "short category (e.g. timeout, memory, auth failure, connection pool, crash)",
  "summary": "one-sentence plain-English summary of the issue"
}}

ALERT:
\"\"\"{alert_text.strip()}\"\"\""""

        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": "You are an expert SRE triage parser. Output valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=300
        )

        raw_content = completion.choices[0].message.content.strip()
        data = extract_json_object(raw_content)
        if isinstance(data, dict):
            sev = str(data.get("severity", "medium")).lower()
            if sev not in ["low", "medium", "high", "critical"]:
                sev = "medium"

            svc = normalize_service_name(data.get("service") or alert_text)

            extracted = {
                "service": svc,
                "severity": sev,
                "symptom_type": str(data.get("symptom_type") or "unknown"),
                "summary": str(data.get("summary") or alert_text[:100].strip()),
                "errors": data.get("errors") if isinstance(data.get("errors"), list) else [],
                "impact": str(data.get("impact") or "No impact summary available"),
                "affected_components": data.get("affected_components") if isinstance(data.get("affected_components"), list) else [],
                "incident_type": str(data.get("incident_type") or "application")
            }
            if not extracted["errors"]:
                extracted["errors"] = [str(alert_text)[:200]]
            return extracted
        return fallback_details
    except Exception as e:
        logger.warning("extract_incident_details failed (%s). Using fallback defaults.", e)
        return fallback_details


def reflect_on_incident(alert_text: str) -> str:
    """
    Calls Hindsight's reflect() method (bank_id="incident-response")
    to synthesize what has worked or failed in similar past incidents.
    Returns reflection text or a graceful fallback string.
    """
    fallback_msg = "Reflection unavailable — proceeding with recall-based reasoning only."
    if not alert_text or not alert_text.strip():
        return fallback_msg

    client = None
    try:
        client = get_hindsight_client()
        query = f"What has worked or failed in past incidents similar to: {alert_text}"
        logger.info("Calling Hindsight reflect(bank_id='%s')...", BANK_ID)
        response = client.reflect(bank_id=BANK_ID, query=query, budget="low")

        reflect_text = None
        if hasattr(response, "text") and response.text:
            reflect_text = response.text
        elif isinstance(response, dict) and response.get("text"):
            reflect_text = response.get("text")
        elif hasattr(response, "response") and response.response:
            reflect_text = response.response

        if reflect_text and str(reflect_text).strip():
            logger.info("Hindsight reflect succeeded.")
            return str(reflect_text).strip()
        return fallback_msg
    except Exception as e:
        logger.warning("Hindsight reflect call failed (%s). Returning fallback string.", e)
        return fallback_msg
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass


def propose_action(
    alert_text: str,
    extracted_details: dict[str, Any],
    recalled_memories: list[dict[str, Any]],
    reflection: str,
    previous_failures: Optional[list[dict[str, Any]]] = None,
    investigation_analysis: Optional[Any] = None,
    investigation_resolution: Optional[Any] = None,
    application_config: Optional[Any] = None,
) -> Optional[dict[str, Any]]:
    """Proposes an allowlisted remediation action using canonical incident identity, application registry, and evidence. Does not query simulator."""
    if application_config and getattr(application_config, "service_id", None):
        preferred_target = application_config.service_id
    elif extracted_details.get("service"):
        preferred_target = normalize_service_name(extracted_details.get("service"))
    else:
        preferred_target = "payment-gateway"

    target_service = preferred_target

    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        logger.warning("GROQ_API_KEY not configured. propose_action using fallback action.")
        fallback = get_fallback_action(extracted_details, previous_failures)
        if fallback.get("target_service") != target_service and target_service in get_allowed_services():
            fallback["target_service"] = target_service
        return fallback

    try:
        from groq import Groq
        client = Groq(api_key=groq_api_key)

        failures_context = ""
        if previous_failures:
            failures_context = (
                "\n\nCRITICAL - PREVIOUS FAILED ATTEMPTS (DO NOT REPEAT ANY OF THESE):\n"
                + json.dumps(previous_failures, indent=2, default=str)
                + "\nYou MUST choose a DIFFERENT action or parameters that address the issue without repeating the failures above."
            )

        mem_snippets = [
            {"text": str(m.get("text", ""))[:200], "context": str(m.get("context", ""))}
            for m in (recalled_memories or [])[:3]
        ]

        allowed_svcs_list = sorted(list(get_allowed_services()))
        allowed_svcs_str = "\n".join(f"- {s}" for s in allowed_svcs_list)

        prompt = f"""You are an expert automated SRE remediation engine.
Recommend an allowlisted remediation action for this production incident.

APPLICATION / SERVICE IDENTITY:
Target Service: {target_service}

ALLOWLISTED ACTION TYPES:
- restart_service: params {{}}
- scale_replicas: params {{"replicas": <int 1-20>}}
- set_config: params {{"key": "<string>", "value": "<string|number|bool>"}}
- flush_cache: params {{}}
- rollback_deployment: params {{"version": "<previous_version>"}}

ALLOWLISTED TARGET SERVICES:
{allowed_svcs_str}

INCIDENT ALERT:
\"\"\"{alert_text.strip()[:500]}\"\"\"

EXTRACTED DETAILS:
{json.dumps(extracted_details, default=str)}

RECALLED HISTORICAL EXPERIENCES:
{json.dumps(mem_snippets, default=str)}

REFLECTION ON WHAT WORKED/FAILED:
{reflection[:1000] if reflection else 'None'}
{failures_context}

Return ONLY a valid JSON object with no markdown fences, no backticks, and no commentary.
Format:
{{
  "type": "<one of the 5 allowlisted action types>",
  "target_service": "{target_service}",
  "params": {{ ... }},
  "rationale": "<technical explanation for why this action fixes the root cause>"
}}
"""

        raw = None
        for attempt in range(2):
            try:
                completion = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[
                        {"role": "system", "content": "You are an automated remediation planner. Output valid JSON only."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=800
                )
                raw = completion.choices[0].message.content.strip()
                break
            except Exception as ge:
                if ("rate_limit" in str(ge).lower() or "429" in str(ge)) and attempt == 0:
                    logger.warning("Groq rate limit in propose_action, waiting 2s...")
                    time.sleep(2.0)
                    continue
                raise

        if not raw:
            return get_fallback_action(extracted_details, previous_failures)

        data = extract_json_object(raw)
        if not isinstance(data, dict):
            logger.warning("propose_action could not parse JSON from LLM: %s. Using fallback.", raw[:150])
            return get_fallback_action(extracted_details, previous_failures)

        action_type = data.get("type")
        raw_target_svc = data.get("target_service") or target_service
        final_target = normalize_service_name(raw_target_svc)
        params = data.get("params") or {}
        rationale = data.get("rationale") or "Automated remediation recommendation"

        if action_type not in ALLOWED_ACTIONS:
            logger.warning("propose_action returned disallowed action type: %s. Using fallback.", action_type)
            return get_fallback_action(extracted_details, previous_failures)

        allowed_all = get_allowed_services()
        if final_target not in allowed_all:
            final_target = target_service

        if not isinstance(params, dict):
            params = {}

        if action_type == "scale_replicas":
            try:
                rep = int(params.get("replicas", 0))
                if not (1 <= rep <= 20):
                    rep = 3
                params["replicas"] = rep
            except Exception:
                params["replicas"] = 3
        elif action_type == "set_config":
            if "key" not in params or "value" not in params:
                params = {"key": "timeout_seconds", "value": 8}

        return {
            "type": str(action_type),
            "target_service": str(final_target),
            "params": params,
            "rationale": str(rationale)
        }

    except Exception as e:
        logger.warning("propose_action failed (%s). Returning safe fallback action.", e)
        fallback = get_fallback_action(extracted_details, previous_failures)
        if fallback.get("target_service") != target_service and target_service in get_allowed_services():
            fallback["target_service"] = target_service
        return fallback


def diagnose_incident(alert_text: str, incident_id: Optional[str] = None) -> dict[str, Any]:
    """
    Diagnoses a production incident:
    1. Extracts structured details (service, severity, symptom_type, summary)
    2. Fetches similar past incidents via Hindsight recall()
    3. Fetches pattern synthesis via Hindsight reflect()
    4. Builds LLM prompt including alert + extracted details + recalled memories + reflection
    5. Queries Groq (model: openai/gpt-oss-120b)
    6. Proposes allowlisted remediation action
    7. Creates incident record in incident lifecycle store
    8. Returns diagnosis, extracted_details, reflection, raw recalled memories, incident_id, recommended_action, and memory_source
    """
    if not alert_text or not alert_text.strip():
        return {
            "status": "error",
            "message": "Alert text cannot be empty.",
            "diagnosis": "No alert text provided.",
            "extracted_details": {
                "service": "unknown",
                "severity": "medium",
                "symptom_type": "unknown",
                "summary": "No alert text provided"
            },
            "reflection": "Reflection unavailable — proceeding with recall-based reasoning only.",
            "raw_recalled_memories": [],
            "incident_id": None,
            "recommended_action": None,
            "memory_source": "local_fallback"
        }

    # Step 1: Extract incident details (Feature 1)
    logger.info("Extracting incident details via Groq...")
    extracted_details = extract_incident_details(alert_text)

    # Step 2: Fetch similar past incidents from Hindsight recall
    recalled_memories, memory_source = recall_memories_from_hindsight(alert_text, return_source=True)

    # Step 3: Fetch reflection on past patterns from Hindsight reflect (Feature 2)
    reflection_text = reflect_on_incident(alert_text)

    # Step 4: Build prompt for Groq
    if recalled_memories:
        memory_snippets = []
        for idx, mem in enumerate(recalled_memories[:3], 1):
            text = str(mem.get("text", ""))[:250]
            context = str(mem.get("context", ""))[:100]
            memory_snippets.append(f"[Memory #{idx}] {text} (Context: {context})")
        memories_text = "\n\n".join(memory_snippets)
    else:
        memories_text = "No similar past incidents found in Hindsight memory."

    prompt = (
        f"INCOMING PRODUCTION ALERT / ERROR LOG:\n"
        f"\"\"\"\n{alert_text.strip()[:600]}\n\"\"\"\n\n"
        f"EXTRACTED INCIDENT METADATA:\n"
        f"Service: {extracted_details.get('service', 'unknown')} | Severity: {extracted_details.get('severity', 'medium')} | Symptom Type: {extracted_details.get('symptom_type', 'unknown')}\n"
        f"Summary: {extracted_details.get('summary', '')}\n\n"
        f"RECALLED PAST INCIDENTS FROM HINDSIGHT MEMORY BANK:\n"
        f"\"\"\"\n{memories_text}\n\"\"\"\n\n"
        f"REFLECTION ON PAST PATTERNS:\n"
        f"\"\"\"\n{reflection_text[:1200] if reflection_text else 'None'}\n\"\"\"\n\n"
        f"TASK:\n"
        f"1. Identify the likely root cause of this incoming alert.\n"
        f"2. Suggest an immediate technical fix / remediation steps.\n"
        f"3. Factor in the reflection on past patterns and explicitly reference which past incident (if any) this resembles and how past resolutions apply.\n"
        f"If no past incidents were found or none match, note that clearly."
    )

    # Step 5: Call Groq
    try:
        diagnosis_response = call_groq_llm(prompt)
    except Exception as e:
        logger.error("Error invoking Groq LLM: %s", e)
        diagnosis_response = f"Error during Groq LLM diagnosis: {str(e)}"

    # Step 5b: Propose action
    recommended_action = propose_action(
        alert_text=alert_text,
        extracted_details=extracted_details,
        recalled_memories=recalled_memories,
        reflection=reflection_text
    )

    # Step 5c: Create or update the same canonical incident record.
    resolved_incident_id = incident_id
    if resolved_incident_id:
        existing_record = get_incident(resolved_incident_id)
        if existing_record:
            incident_record = dict(existing_record)
            incident_record.update(
                {
                    "alert": alert_text,
                    "alert_text": alert_text,
                    "summary": existing_record.get("summary") or extracted_details.get("summary") or alert_text,
                    "extracted_details": extracted_details,
                    "diagnosis": diagnosis_response,
                    "reflection": reflection_text,
                    "raw_recalled_memories": recalled_memories,
                    "hindsight_results": recalled_memories,
                    "recommended_action": recommended_action,
                    "memory_source": memory_source,
                    "updated_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    "status": existing_record.get("status") or "AWAITING_APPROVAL",
                }
            )
            update_incident(resolved_incident_id, incident_record)
        else:
            resolved_incident_id = generate_incident_id()
            incident_record = create_incident(
                alert=alert_text,
                extracted_details=extracted_details,
                diagnosis=diagnosis_response,
                reflection=reflection_text,
                raw_recalled_memories=recalled_memories,
                recommended_action=recommended_action,
                memory_source=memory_source,
                incident_id=resolved_incident_id,
                source_type="diagnostic_alert",
            )
    else:
        resolved_incident_id = generate_incident_id()
        incident_record = create_incident(
            alert=alert_text,
            extracted_details=extracted_details,
            diagnosis=diagnosis_response,
            reflection=reflection_text,
            raw_recalled_memories=recalled_memories,
            recommended_action=recommended_action,
            memory_source=memory_source,
            incident_id=resolved_incident_id,
            source_type="diagnostic_alert",
        )

    # Step 6: Return dict with diagnosis, extracted_details, reflection, AND raw recalled memories
    return {
        "status": "success",
        "alert": alert_text,
        "extracted_details": extracted_details,
        "reflection": reflection_text,
        "diagnosis": diagnosis_response,
        "raw_recalled_memories": recalled_memories,
        "memories_count": len(recalled_memories),
        "bank_id": BANK_ID,
        "model": GROQ_MODEL,
        "incident_id": resolved_incident_id,
        "recommended_action": recommended_action,
        "memory_source": memory_source
    }


def resolve_incident(alert_text: str, root_cause: str, fix: str, service: str = "general", resolution_time_minutes: int = 15, user_id: Optional[int] = None) -> dict[str, Any]:
    """
    Saves a newly resolved incident to Hindsight memory via retain()
    so future similar alerts benefit from it, scoped to user_id.
    """
    target_bank = BANK_ID if (user_id in (None, 2)) else f"incident-response-user-{user_id}"
    content = (
        f"Resolved production incident in {service}: "
        f"Symptom was '{alert_text}'. "
        f"Root cause: {root_cause}. "
        f"Fix applied: {fix}. "
        f"Resolution time: {resolution_time_minutes} minutes."
    )
    context_str = f"service: {service}, resolution_time: {resolution_time_minutes}m, status: resolved"
    metadata = {
        "service": str(service),
        "resolution_time_minutes": str(resolution_time_minutes),
        "status": "resolved"
    }

    # Store in user-scoped local fallback memory bank as well
    if user_id not in (None, 2):
        if int(user_id) not in _user_memory_banks:
            _user_memory_banks[int(user_id)] = []
        _user_memory_banks[int(user_id)].append({
            "id": f"local-user-{len(_user_memory_banks[int(user_id)])+1}",
            "text": content,
            "type": "experience",
            "context": context_str,
            "metadata": metadata
        })
    else:
        _local_memory_bank.append({
            "id": f"local-{len(_local_memory_bank)+1}",
            "text": content,
            "type": "experience",
            "context": context_str,
            "metadata": metadata
        })

    hindsight_success = False
    hindsight_error = None
    client = None

    try:
        client = get_hindsight_client()
        logger.info("Calling Hindsight retain() for newly resolved incident (service: %s, bank: %s)...", service, target_bank)
        response = client.retain(
            bank_id=target_bank,
            content=content,
            context=context_str,
            metadata=metadata
        )
        hindsight_success = getattr(response, "success", True)
        logger.info("Hindsight retain succeeded: %s", hindsight_success)
    except Exception as e:
        hindsight_error = str(e)
        logger.warning("Hindsight retain call failed (%s). Saved to local fallback memory bank.", e)
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass

    return {
        "status": "success",
        "message": "Incident resolution successfully recorded in memory.",
        "retained_content": content,
        "hindsight_stored": hindsight_success,
        "hindsight_error": hindsight_error,
        "bank_id": target_bank
    }


def retain_outcome(incident: dict[str, Any]) -> bool:
    """
    Saves incident final outcome and failed attempts into Hindsight memory (or local fallback).
    """
    rec_action = incident.get("recommended_action") or {}
    service = rec_action.get("target_service") or incident.get("extracted_details", {}).get("service", "unknown")
    alert = incident.get("alert", "")
    final_status = incident.get("status", "RESOLVED")
    attempts_count = len(incident.get("executions", []))

    first_ver = incident.get("verifications", [{}])[0] if incident.get("verifications") else {}
    last_ver = incident.get("verifications", [{}])[-1] if incident.get("verifications") else {}

    before_err = first_ver.get("before", {}).get("error_rate", "N/A")
    after_err = last_ver.get("after", {}).get("error_rate", "N/A")
    before_p95 = first_ver.get("before", {}).get("p95_latency_ms", "N/A")
    after_p95 = last_ver.get("after", {}).get("p95_latency_ms", "N/A")
    last_ver_status = last_ver.get("status", "UNKNOWN")

    action_str = f"{rec_action.get('type')}({rec_action.get('params', {})})"
    main_summary = (
        f"Incident on {service}: {alert}. "
        f"Attempted {action_str} -> verification {last_ver_status} "
        f"(error_rate before {before_err} -> after {after_err}, p95 {before_p95} -> {after_p95}). "
        f"Final: {final_status} after {attempts_count} attempts."
    )

    items_to_retain = [
        {
            "content": main_summary,
            "context": f"service: {service}, status: {final_status}, attempts: {attempts_count}",
            "metadata": {
                "service": service,
                "status": final_status,
                "attempts": str(attempts_count),
                "type": "resolution_outcome"
            }
        }
    ]

    # Retain each failed attempt separately
    for fa in incident.get("failed_attempts", []):
        fa_action = fa.get("action") or {}
        fa_act_str = f"{fa_action.get('type')}({fa_action.get('params', {})})"
        fa_service = fa_action.get("target_service", service)
        fa_reasons = "; ".join(fa.get("reasons", [])) or "criteria failed"
        fa_summary = f"Attempted {fa_act_str} on {fa_service} for '{alert}'; it did NOT work because {fa_reasons}."
        items_to_retain.append({
            "content": fa_summary,
            "context": f"service: {fa_service}, status: failed_attempt",
            "metadata": {
                "service": fa_service,
                "status": "failed_attempt",
                "type": "failed_remediation"
            }
        })

    # Save to user-scoped local memory bank
    user_id = incident.get("user_id")
    target_bank = BANK_ID if (user_id in (None, 2)) else f"incident-response-user-{user_id}"

    if user_id not in (None, 2):
        if int(user_id) not in _user_memory_banks:
            _user_memory_banks[int(user_id)] = []
        user_bank = _user_memory_banks[int(user_id)]
    else:
        user_bank = _local_memory_bank

    for item in items_to_retain:
        user_bank.append({
            "id": f"local-{len(user_bank)+1}",
            "text": item["content"],
            "type": "experience",
            "context": item["context"],
            "metadata": item["metadata"]
        })

    # Retain in Hindsight
    client = None
    try:
        client = get_hindsight_client()
        for item in items_to_retain:
            client.retain(
                bank_id=target_bank,
                content=item["content"],
                context=item["context"],
                metadata=item["metadata"]
            )
        logger.info("retain_outcome successfully retained %d records in Hindsight (bank: %s).", len(items_to_retain), target_bank)
    except Exception as e:
        logger.warning("retain_outcome Hindsight retain failed (%s); saved %d records to local memory bank fallback.", e, len(items_to_retain))
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass
    return True
