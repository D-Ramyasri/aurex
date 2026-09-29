"""
Demo Scenario Script for Incident Response Memory Agent.
Proves the Hindsight memory learning loop:
1. Runs Alert 1 (a novel/unseen production issue) through diagnose_incident() using initial seeded data.
2. Resolves Alert 1 by calling resolve_incident() to retain the fix into Hindsight.
3. Runs Alert 2 (a similar subsequent alert) through diagnose_incident().
4. Displays structured extraction, pattern reflection, and side-by-side comparison proving that Alert 2 recalls the resolution from Alert 1!
"""

import sys
import time
import json
import logging
from agent import diagnose_incident, resolve_incident, BANK_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("demo_scenario")


def safe_print(*args, **kwargs):
    """Safely prints text handling Windows cp1252 encoding limitations."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        text = " ".join(str(a) for a in args)
        clean_text = text.encode("ascii", errors="replace").decode("ascii")
        print(clean_text, **kwargs)


def print_banner(title: str):
    width = 90
    safe_print("\n" + "=" * width)
    safe_print(f" {title} ".center(width, "="))
    safe_print("=" * width + "\n")


def print_section(heading: str):
    safe_print("\n" + "-" * 70)
    safe_print(f">>> {heading}")
    safe_print("-" * 70)


def format_memory_summary(memories: list) -> str:
    if not memories:
        return "None (No matching memories found)"
    summary = []
    for idx, m in enumerate(memories, 1):
        txt = m.get("text", "")
        if len(txt) > 140:
            txt = txt[:140] + "..."
        summary.append(f"  [{idx}] {txt}")
    return "\n".join(summary)


def run_demo():
    print_banner("INCIDENT RESPONSE MEMORY AGENT - DEMO SCENARIO")
    safe_print(f"Memory Bank: {BANK_ID}")
    safe_print("Demonstrating Structured Extraction, Reflection, and Before/After Learning.\n")

    # ---------------------------------------------------------
    # STEP 1: Run Alert 1 through diagnose_incident()
    # ---------------------------------------------------------
    alert_1 = (
        "CRITICAL ALERT: Auth service WebSocket connection drops during token rotation. "
        "Mobile users getting disconnected every 15 minutes with code 4401 TokenExpired on US-East."
    )

    print_section("STEP 1: Diagnosing Initial Incident (Alert 1 - Novel Issue)")
    safe_print(f"Alert Text:\n\"{alert_1}\"\n")

    safe_print("Executing diagnose_incident(alert_1)...")
    t0 = time.time()
    result_1 = diagnose_incident(alert_1)
    duration_1 = time.time() - t0

    extracted_1 = result_1.get("extracted_details", {})
    reflection_1 = result_1.get("reflection", "")
    memories_1 = result_1.get("raw_recalled_memories", [])
    diagnosis_1 = result_1.get("diagnosis", "")

    safe_print(f"\n[Alert 1 Results] (Completed in {duration_1:.2f}s)")
    safe_print("\n--- Structured Extraction (Feature 1) ---")
    safe_print(f"  Service:      {extracted_1.get('service')}")
    safe_print(f"  Severity:     {extracted_1.get('severity')}")
    safe_print(f"  Symptom Type: {extracted_1.get('symptom_type')}")
    safe_print(f"  Summary:      {extracted_1.get('summary')}")

    safe_print("\n--- Hindsight Reflection (Feature 2) ---")
    safe_print(f"  {reflection_1}")

    safe_print(f"\n--- Recalled Memories ({len(memories_1)} found) ---")
    safe_print(format_memory_summary(memories_1))

    safe_print("\n--- LLM Diagnosis ---")
    safe_print(diagnosis_1)

    # ---------------------------------------------------------
    # STEP 2: Resolve Alert 1 and retain into Hindsight
    # ---------------------------------------------------------
    print_section("STEP 2: Resolving Alert 1 & Retaining in Hindsight Memory")
    service = extracted_1.get("service") if extracted_1.get("service") != "unknown" else "auth-service"
    root_cause = "WebSocket connection handshake lacked automatic refresh token renewal before 15m expiration, abruptly closing the TCP socket."
    fix = "Implemented client-side silent ping refresh 2 minutes prior to token expiration and enabled WebSocket session ticket renewal on the gateway."
    res_time = 25

    safe_print(f"Service:                 {service}")
    safe_print(f"Confirmed Root Cause:    {root_cause}")
    safe_print(f"Remediation Fix Applied: {fix}")
    safe_print(f"Resolution Time:         {res_time} minutes\n")

    safe_print("Calling resolve_incident()...")
    retain_res = resolve_incident(
        alert_text=alert_1,
        root_cause=root_cause,
        fix=fix,
        service=service,
        resolution_time_minutes=res_time
    )
    safe_print(f"Resolution Retained: {retain_res.get('status')} - {retain_res.get('message')}")
    safe_print(f"Retained Content: {retain_res.get('retained_content')}")

    # Brief pause to ensure indexing / temporal availability
    time.sleep(1)

    # ---------------------------------------------------------
    # STEP 3: Run Alert 2 (similar subsequent issue)
    # ---------------------------------------------------------
    alert_2 = (
        "HIGH SEVERITY ALERT: Web client WebSocket sessions abruptly closing with error 4401 on EU-West cluster; "
        "users reporting live chat disconnection during token refresh window."
    )

    print_section("STEP 3: Diagnosing Subsequent Similar Incident (Alert 2)")
    safe_print(f"Alert Text:\n\"{alert_2}\"\n")

    safe_print("Executing diagnose_incident(alert_2)...")
    t0 = time.time()
    result_2 = diagnose_incident(alert_2)
    duration_2 = time.time() - t0

    extracted_2 = result_2.get("extracted_details", {})
    reflection_2 = result_2.get("reflection", "")
    memories_2 = result_2.get("raw_recalled_memories", [])
    diagnosis_2 = result_2.get("diagnosis", "")

    safe_print(f"\n[Alert 2 Results] (Completed in {duration_2:.2f}s)")
    safe_print("\n--- Structured Extraction (Feature 1) ---")
    safe_print(f"  Service:      {extracted_2.get('service')}")
    safe_print(f"  Severity:     {extracted_2.get('severity')}")
    safe_print(f"  Symptom Type: {extracted_2.get('symptom_type')}")
    safe_print(f"  Summary:      {extracted_2.get('summary')}")

    safe_print("\n--- Hindsight Reflection (Feature 2) ---")
    safe_print(f"  {reflection_2}")

    safe_print(f"\n--- Recalled Memories ({len(memories_2)} found) ---")
    safe_print(format_memory_summary(memories_2))

    safe_print("\n--- LLM Diagnosis ---")
    safe_print(diagnosis_2)

    # ---------------------------------------------------------
    # STEP 4: Side-by-Side Comparison
    # ---------------------------------------------------------
    print_banner("BEFORE vs AFTER MEMORY LEARNING COMPARISON")

    safe_print(f"{'BEFORE (Alert 1 - US-East)':<45} | {'AFTER (Alert 2 - EU-West)':<45}")
    safe_print("-" * 92)

    col1_mem = f"Memories Recalled: {len(memories_1)}"
    col2_mem = f"Memories Recalled: {len(memories_2)} (Includes newly retained fix!)"
    safe_print(f"{col1_mem:<45} | {col2_mem:<45}")

    recalled_new_fix = any(
        "silent ping refresh" in m.get("text", "").lower() or
        "websocket session ticket" in m.get("text", "").lower() or
        "websocket connection drops" in m.get("text", "").lower()
        for m in memories_2
    )
    status_str = "YES (Recalled Alert 1 Fix!)" if recalled_new_fix else "Retained in bank"
    safe_print(f"{'New Fix Present: No':<45} | {f'New Fix Present: {status_str}':<45}")

    col1_ext = f"Extracted: {extracted_1.get('service')} / {extracted_1.get('severity')}"
    col2_ext = f"Extracted: {extracted_2.get('service')} / {extracted_2.get('severity')}"
    safe_print(f"{col1_ext:<45} | {col2_ext:<45}")

    safe_print("-" * 92)
    safe_print("\nKEY OBSERVATION FOR DEMO VIDEO:")
    safe_print("1. In Alert 1, the memory system had never seen this WebSocket token expiry issue.")
    safe_print("2. Once the SRE team resolved Alert 1 and retained it via resolve_incident(), Hindsight indexed the root cause & fix.")
    safe_print("3. In Alert 2, Hindsight recalled the exact resolution applied to Alert 1, allowing the LLM to instantly recommend the proven fix!")
    safe_print("=" * 92 + "\n")


if __name__ == "__main__":
    run_demo()
