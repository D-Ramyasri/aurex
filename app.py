"""
Incident Response Agent — Production SRE Control Plane & Web Application.
Built with clean Enterprise SaaS Light Theme, Pale Blue Sidebar (#F1F6FC),
Real Telemetry, Hindsight Semantic Memory, Human-in-the-Loop Approval,
Real Execution, and Real Verification.

Features:
- Public Landing Page (/)
- User Authentication backed by existing SQLite database (/login, /signup)
- Authenticated SRE Incident Response Dashboard (/dashboard)
"""

import os
import json
import time
import html
import re
from typing import Any, Dict, List, Optional
import requests
import streamlit as st

BACKEND_URL = (os.getenv("BACKEND_URL") or "http://127.0.0.1:8001").rstrip("/")


def render_html(html_str: str):
    """Safely renders HTML without markdown codeblock indentation bugs."""
    clean_lines = [line.strip() for line in html_str.strip().splitlines() if line.strip()]
    cleaned_html = "\n".join(clean_lines)
    if hasattr(st, "html"):
        st.html(cleaned_html)
    else:
        st.markdown(cleaned_html, unsafe_allow_html=True)


st.set_page_config(
    page_title="Aurex | SRE Control Plane",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# =============================================================================
# GLOBAL ENTERPRISE LIGHT THEME STYLESHEET (MATCHING REFERENCE TARGET)
# =============================================================================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');
    
    /* 1. Global Page Background & Text */
    .stApp {
        background-color: #F8FAFC !important;
        color: #0F2747 !important;
    }
    
    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
        color: #0F2747 !important;
    }
    
    /* 2. Left Sidebar — Subtle Light Blue (#F1F6FC) */
    section[data-testid="stSidebar"],
    [data-testid="stSidebar"],
    [data-testid="stSidebarUserContent"],
    [data-testid="stSidebarContent"] {
        background-color: #F1F6FC !important;
        border-right: 1px solid #D9E2EC !important;
    }
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
        color: #0F2747 !important;
        font-weight: 700 !important;
    }
    [data-testid="stSidebar"] p, [data-testid="stSidebar"] span, [data-testid="stSidebar"] div, [data-testid="stSidebar"] label {
        color: #334155;
    }

    /* Headings */
    h1, h2, h3, h4, h5, h6 {
        color: #0F2747 !important;
        font-weight: 700 !important;
        letter-spacing: -0.02em;
    }
    p, span, label, div {
        color: #334155;
    }
    
    /* Code & Mono */
    code, pre {
        font-family: 'JetBrains Mono', monospace !important;
        background: #F1F5F9 !important;
        color: #0F2747 !important;
        padding: 2px 6px !important;
        border-radius: 4px !important;
        border: 1px solid #CBD5E1 !important;
        font-size: 0.88rem !important;
    }

    /* Clean Card Containers — Pure White with Subtle Slate Borders */
    .saas-card {
        background: #FFFFFF;
        border: 1px solid #D9E2EC;
        border-radius: 10px;
        padding: 22px;
        margin-bottom: 18px;
        box-shadow: 0 1px 3px rgba(15, 39, 71, 0.04);
        color: #0F2747;
    }
    .action-card {
        background: #FFFFFF;
        border: 1px solid #D9E2EC;
        border-left: 6px solid #2563EB;
        border-radius: 10px;
        padding: 20px;
        margin-top: 10px;
        margin-bottom: 16px;
        color: #0F2747;
        box-shadow: 0 1px 3px rgba(15, 39, 71, 0.04);
    }
    .outcome-box {
        background: #F0FDF4;
        border: 1px solid #BBF7D0;
        border-left: 6px solid #16A34A;
        border-radius: 10px;
        padding: 22px;
        margin-top: 20px;
        color: #14532D;
        box-shadow: 0 1px 4px rgba(22, 163, 74, 0.06);
    }
    .controlled-state-box {
        background: #FFFBEB;
        border: 1px solid #FCD34D;
        border-left: 6px solid #F59E0B;
        border-radius: 10px;
        padding: 20px;
        margin-top: 14px;
        margin-bottom: 16px;
        color: #78350F;
    }
    .incident-alert-box {
        background: #FEF2F2;
        border: 1.5px solid #FECACA;
        border-left: 6px solid #DC2626;
        border-radius: 10px;
        padding: 20px 24px;
        margin-bottom: 20px;
        color: #7F1D1D;
        box-shadow: 0 1px 4px rgba(220, 38, 38, 0.06);
    }
    .unreachable-box {
        background: #FFFBEB;
        border: 1.5px solid #FED7AA;
        border-left: 6px solid #EA580C;
        border-radius: 10px;
        padding: 20px 24px;
        margin-bottom: 18px;
        color: #7C2D12;
    }
    .healthy-box {
        background: #F0FDF4;
        border: 1.5px solid #86EFAC;
        border-left: 6px solid #16A34A;
        border-radius: 10px;
        padding: 20px 24px;
        margin-bottom: 20px;
        color: #14532D;
        box-shadow: 0 1px 4px rgba(22, 163, 74, 0.06);
    }
    .telemetry-card {
        background: #FFFFFF;
        border: 1px solid #D9E2EC;
        border-radius: 8px;
        padding: 16px 18px;
        margin-top: 8px;
        line-height: 1.6;
        box-shadow: 0 1px 2px rgba(15, 39, 71, 0.03);
    }
    .telemetry-card div {
        color: #1E293B !important;
    }

    /* Semantic Pastel Badges */
    .badge {
        display: inline-block;
        padding: 5px 12px;
        border-radius: 16px;
        font-size: 0.78rem;
        font-weight: 700;
        margin-right: 6px;
        letter-spacing: 0.02em;
    }
    .badge-memory {
        background: #EEF2FF;
        color: #3730A3;
        border: 1px solid #C7D2FE;
    }
    .badge-llm {
        background: #FEF3C7;
        color: #92400E;
        border: 1px solid #FDE68A;
    }
    .badge-status {
        background: #DCFCE7;
        color: #166534;
        border: 1px solid #BBF7D0;
    }
    .badge-real {
        background: #E0F2FE;
        color: #0369A1;
        border: 1px solid #BAE6FD;
    }
    .badge-tag {
        background: #F1F5F9;
        color: #334155;
        border: 1px solid #CBD5E1;
    }
    
    /* Severity Badges */
    .sev-critical {
        background: #FEE2E2;
        color: #991B1B;
        border: 1px solid #FCA5A5;
        font-weight: 700;
    }
    .sev-high {
        background: #FFEDD5;
        color: #9A3412;
        border: 1px solid #FED7AA;
        font-weight: 700;
    }
    .sev-medium {
        background: #FEF9C3;
        color: #854D0E;
        border: 1px solid #FEF08A;
        font-weight: 700;
    }
    .sev-low {
        background: #DCFCE7;
        color: #166534;
        border: 1px solid #BBF7D0;
        font-weight: 700;
    }

    /* Verification Status Badges */
    .v-badge-success {
        display: inline-block;
        padding: 8px 18px;
        border-radius: 8px;
        background: #DCFCE7;
        color: #15803D;
        border: 2px solid #22C55E;
        font-size: 1.05rem;
        font-weight: 800;
        letter-spacing: 0.03em;
    }
    .v-badge-failure {
        display: inline-block;
        padding: 8px 18px;
        border-radius: 8px;
        background: #FEE2E2;
        color: #B91C1C;
        border: 2px solid #EF4444;
        font-size: 1.05rem;
        font-weight: 800;
        letter-spacing: 0.03em;
    }
    .v-badge-uncertain {
        display: inline-block;
        padding: 8px 18px;
        border-radius: 8px;
        background: #FEF3C7;
        color: #B45309;
        border: 2px solid #F59E0B;
        font-size: 1.05rem;
        font-weight: 800;
        letter-spacing: 0.03em;
    }

    /* Horizontal Tabs Styling */
    [data-baseweb="tab-list"] {
        background-color: transparent !important;
        gap: 8px !important;
        border-bottom: 2px solid #D9E2EC !important;
        padding-bottom: 2px !important;
        margin-bottom: 20px !important;
    }
    [data-baseweb="tab"] {
        background-color: transparent !important;
        color: #52657A !important;
        font-weight: 600 !important;
        font-size: 0.95rem !important;
        border: none !important;
        padding: 10px 18px !important;
        border-radius: 8px 8px 0 0 !important;
    }
    [aria-selected="true"] {
        color: #0F2747 !important;
        font-weight: 700 !important;
        border-bottom: 3px solid #2563EB !important;
    }

    /* Buttons */
    button[kind="primary"], .stButton > button[kind="primary"] {
        background-color: #2563EB !important;
        color: #FFFFFF !important;
        border: none !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
        box-shadow: 0 1px 2px rgba(37, 99, 235, 0.2) !important;
    }
    button[kind="primary"]:hover, .stButton > button[kind="primary"]:hover {
        background-color: #1D4ED8 !important;
        box-shadow: 0 2px 4px rgba(37, 99, 235, 0.3) !important;
    }
    button[kind="secondary"], .stButton > button[kind="secondary"] {
        background-color: #FFFFFF !important;
        color: #0F2747 !important;
        border: 1px solid #D9E2EC !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
    }
    button[kind="secondary"]:hover, .stButton > button[kind="secondary"]:hover {
        background-color: #F1F6FC !important;
        border-color: #CBD5E1 !important;
    }

    /* Smooth Scrolling */
    html {
        scroll-behavior: smooth;
    }

    /* Top Navigation Bar */
    .top-nav-bar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 4px 0 16px 0;
        margin-bottom: 24px;
        border-bottom: 1px solid #D9E2EC;
    }
    .nav-brand {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        text-decoration: none !important;
        cursor: pointer !important;
    }
    .nav-brand:hover {
        text-decoration: none !important;
        opacity: 0.92;
    }
    .nav-links-group {
        display: flex;
        align-items: center;
        gap: 10px;
    }
    .nav-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
        font-size: 0.95rem !important;
        font-weight: 600 !important;
        padding: 7px 18px !important;
        border-radius: 8px !important;
        text-decoration: none !important;
        cursor: pointer !important;
        transition: all 0.15s ease-in-out !important;
        white-space: nowrap !important;
        line-height: 1.5 !important;
        box-sizing: border-box !important;
    }
    .nav-btn-secondary {
        background-color: #FFFFFF !important;
        color: #0F2747 !important;
        border: 1px solid #D9E2EC !important;
    }
    .nav-btn-secondary:hover {
        background-color: #F1F6FC !important;
        border-color: #CBD5E1 !important;
        color: #0F2747 !important;
        text-decoration: none !important;
    }
    .nav-btn-primary {
        background-color: #2563EB !important;
        color: #FFFFFF !important;
        border: 1px solid #2563EB !important;
        box-shadow: 0 1px 2px rgba(37, 99, 235, 0.2) !important;
    }
    .nav-btn-primary:hover {
        background-color: #1D4ED8 !important;
        border-color: #1D4ED8 !important;
        color: #FFFFFF !important;
        box-shadow: 0 2px 4px rgba(37, 99, 235, 0.3) !important;
        text-decoration: none !important;
    }

    /* Log Lines */
    .log-line-error {
        background: #FEE2E2;
        color: #991B1B;
        border-left: 4px solid #EF4444;
        padding: 6px 12px;
        margin-bottom: 4px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.82rem;
        border-radius: 4px;
    }
    .log-line-warn {
        background: #FEF3C7;
        color: #92400E;
        border-left: 4px solid #F59E0B;
        padding: 6px 12px;
        margin-bottom: 4px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.82rem;
        border-radius: 4px;
    }
    .log-line-info {
        background: #F1F5F9;
        color: #334155;
        border-left: 4px solid #94A3B8;
        padding: 6px 12px;
        margin-bottom: 4px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.82rem;
        border-radius: 4px;
    }

    /* RCA & Investigation Details */
    .rca-card {
        background: #FFFFFF;
        border: 1px solid #D9E2EC;
        border-radius: 10px;
        padding: 20px;
        line-height: 1.6;
        box-shadow: 0 1px 3px rgba(15, 39, 71, 0.04);
    }
    .rca-title {
        font-size: 0.88rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-top: 10px;
        margin-bottom: 4px;
    }
    .rca-root-cause {
        background: #FEE2E2;
        border: 1px solid #FECACA;
        border-left: 4px solid #DC2626;
        padding: 10px 14px;
        border-radius: 6px;
        font-size: 1.0rem;
        font-weight: 700;
        color: #991B1B;
        margin-bottom: 12px;
    }

    /* Grounded Causal Flow */
    .flow-chain-container {
        display: flex;
        flex-direction: column;
        gap: 10px;
        margin-top: 14px;
        margin-bottom: 24px;
    }
    .flow-card {
        padding: 16px 20px;
        border-radius: 8px;
        box-shadow: 0 1px 3px rgba(15, 39, 71, 0.04);
    }
    .flow-arrow {
        text-align: center;
        font-size: 1.3rem;
        font-weight: 900;
        color: #2563EB;
        line-height: 1;
        user-select: none;
    }
    .flow-current {
        background: #FEF2F2;
        border: 1px solid #FECACA;
        border-left: 6px solid #DC2626;
        color: #7F1D1D;
    }
    .flow-query {
        background: #F0F9FF;
        border: 1px solid #BAE6FD;
        border-left: 6px solid #0284C7;
        color: #0369A1;
    }
    .flow-memory {
        background: #EEF2FF;
        border: 1px solid #C7D2FE;
        border-left: 6px solid #4F46E5;
        color: #312E81;
    }
    .flow-no-history {
        background: #F8FAFC;
        border: 1px solid #D9E2EC;
        border-left: 6px solid #64748B;
        color: #334155;
    }
    .flow-recommendation {
        background: #F0FDF4;
        border: 1px solid #BBF7D0;
        border-left: 6px solid #16A34A;
        color: #14532D;
    }
    .flow-label {
        font-size: 0.84rem;
        letter-spacing: 0.08em;
        font-weight: 800;
        text-transform: uppercase;
        margin-bottom: 8px;
    }

    /* Timeline Stepper */
    .timeline-container {
        position: relative;
        padding-left: 20px;
        margin-top: 12px;
        border-left: 2px solid #D9E2EC;
    }
    .timeline-item {
        position: relative;
        margin-bottom: 16px;
        padding-left: 12px;
    }
    .timeline-dot {
        position: absolute;
        left: -27px;
        top: 4px;
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background: #FFFFFF;
        border: 2px solid #2563EB;
    }
    .timeline-dot-error { border-color: #DC2626; background: #FEE2E2; }
    .timeline-dot-warn { border-color: #D97706; background: #FEF3C7; }
    .timeline-dot-info { border-color: #0284C7; background: #E0F2FE; }

    /* Landing Page Features Grid */
    .feature-card {
        background: #FFFFFF;
        border: 1px solid #D9E2EC;
        border-radius: 10px;
        padding: 20px;
        box-shadow: 0 1px 3px rgba(15, 39, 71, 0.04);
        height: 100%;
    }
    .feature-card h4 {
        margin-top: 0;
        color: #0F2747;
        font-size: 1.05rem;
    }
    .feature-card p {
        color: #52657A;
        font-size: 0.92rem;
        line-height: 1.5;
        margin-bottom: 0;
    }

    /* Stepper Workflow Visualization */
    .stepper-box {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: center;
        gap: 8px;
        background: #FFFFFF;
        padding: 20px;
        border: 1px solid #D9E2EC;
        border-radius: 12px;
        box-shadow: 0 1px 3px rgba(15, 39, 71, 0.04);
        margin: 20px 0;
    }
    .step-pill {
        background: #F1F6FC;
        color: #0F2747;
        padding: 8px 14px;
        border-radius: 8px;
        font-weight: 600;
        font-size: 0.86rem;
        border: 1px solid #D9E2EC;
    }
    .step-pill.highlight {
        background: #DBEAFE;
        color: #1E40AF;
        border-color: #93C5FD;
    }
    .step-arrow {
        color: #64748B;
        font-weight: bold;
        font-size: 1.1rem;
    }
</style>
""", unsafe_allow_html=True)


# =============================================================================
# SESSION STATE & ROUTING INITIALIZATION
# =============================================================================
if "auth_token" not in st.session_state:
    st.session_state["auth_token"] = None
if "auth_user" not in st.session_state:
    st.session_state["auth_user"] = None
if "incident_id" not in st.session_state:
    st.session_state["incident_id"] = None
if "latest_inspection" not in st.session_state:
    st.session_state["latest_inspection"] = None
if "selected_app_id" not in st.session_state:
    st.session_state["selected_app_id"] = None

# Query parameter router support: ?page=home, login, signup, dashboard
qp_page = st.query_params.get("page", None)
if qp_page:
    st.session_state["page"] = qp_page
elif not st.session_state.get("auth_token"):
    st.session_state["page"] = "home"
elif "page" not in st.session_state:
    st.session_state["page"] = "dashboard"


def navigate_to(page_name: str):
    """Sets current page in state and query params, then re-runs."""
    st.session_state["page"] = page_name
    st.query_params["page"] = page_name
    st.rerun()


def get_auth_headers() -> Dict[str, str]:
    """Returns Authorization Bearer header for currently authenticated user."""
    token = st.session_state.get("auth_token")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def logout():
    """Logs out current user, clears auth tokens, and navigates home."""
    st.session_state.pop("auth_token", None)
    st.session_state.pop("auth_user", None)
    st.session_state["incident_id"] = None
    st.session_state["latest_inspection"] = None
    st.session_state["selected_app_id"] = None
    st.session_state.pop("remediation_result", None)
    st.session_state.pop("verification_result", None)
    st.session_state.pop("inspection_elapsed_ms", None)
    navigate_to("home")


def clean_field(val: Any, default: str = "Not available") -> str:
    """Format missing or empty fields cleanly without raw None/null."""
    if val is None or val == "" or str(val).strip().lower() in ["none", "null", "none (connection error)"]:
        return default
    return str(val).strip()


def format_preview_dict(data: Any, default: str = "None") -> str:
    """Format dict into readable key-value string instead of raw JSON dump."""
    if not data:
        return default
    if isinstance(data, dict):
        pairs = [f"{k}={v}" for k, v in data.items() if v is not None]
        return ", ".join(pairs) if pairs else default
    return str(data)


# Helper: Fetch Application Fleet
def get_registered_applications() -> List[Dict[str, Any]]:
    try:
        resp = requests.get(f"{BACKEND_URL}/api/applications", headers=get_auth_headers(), timeout=2.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    try:
        from app_registry import list_applications
        user_id = st.session_state.get("auth_user", {}).get("id") if st.session_state.get("auth_user") else None
        return [a.model_dump() for a in list_applications(user_id=user_id)]
    except Exception:
        return []


def probe_application_live_health(base_url: str, health_endpoint: str) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{health_endpoint.lstrip('/')}"
    try:
        t0 = time.perf_counter()
        r = requests.get(url, timeout=1.5)
        latency = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "reachable": True,
            "status_code": r.status_code,
            "latency_ms": latency,
            "healthy": r.status_code == 200
        }
    except Exception as e:
        return {
            "reachable": False,
            "status_code": None,
            "latency_ms": None,
            "healthy": False,
            "error": str(e)
        }


# =============================================================================
# TOP NAVIGATION (AUTHENTICATION STATE-AWARE)
# =============================================================================
is_authenticated = bool(st.session_state.get("auth_token"))

if is_authenticated:
    # Authenticated Top Bar (Only Branding + User + Logout; NO Home/Features/Dashboard links)
    top_c1, top_c2 = st.columns([8, 4])
    with top_c1:
        st.markdown("""
        <div style="display: flex; align-items: center; gap: 10px; padding: 4px 0;">
            <span style="font-size: 1.6rem;">🛡️</span>
            <span style="font-size: 1.3rem; font-weight: 800; color: #0F2747; letter-spacing: -0.02em;">
                Aurex
            </span>
        </div>
        """, unsafe_allow_html=True)
    with top_c2:
        u_col1, u_col2 = st.columns([7, 5])
        with u_col1:
            usr = st.session_state.get("auth_user", {})
            st.markdown(
                f"<div style='text-align: right; padding-top: 8px; font-size: 0.9rem; color: #0F2747; font-weight: 600;'>"
                f"👤 {usr.get('username', 'SRE Lead')}"
                f"</div>",
                unsafe_allow_html=True
            )
        with u_col2:
            if st.button("Logout", key="auth_nav_logout", use_container_width=True):
                logout()
    st.markdown("<hr style='margin: 8px 0 20px 0; border: none; border-top: 1px solid #D9E2EC;'>", unsafe_allow_html=True)

else:
    # Public Unauthenticated Top Bar (Branding + Home + Features + How It Works + Login + Sign Up)
    st.markdown("""
    <div class="top-nav-bar">
        <a href="?page=home#top" target="_self" class="nav-brand" title="Aurex">
            <span style="font-size: 1.6rem; line-height: 1;">🛡️</span>
            <span style="font-size: 1.3rem; font-weight: 800; color: #0F2747; letter-spacing: -0.02em;">
                Aurex
            </span>
        </a>
        <div class="nav-links-group">
            <a href="?page=home#top" target="_self" id="nav-btn-home" class="nav-btn nav-btn-secondary" onclick="if(window.location.search.includes('page=home')||window.location.search===''){window.scrollTo({top:0,behavior:'smooth'});return false;}">Home</a>
            <a href="?page=home#features" target="_self" id="nav-btn-features" class="nav-btn nav-btn-secondary" onclick="const el=document.getElementById('features');if(el){el.scrollIntoView({behavior:'smooth'});return false;}">Features</a>
            <a href="?page=home#how-it-works" target="_self" id="nav-btn-how" class="nav-btn nav-btn-secondary" onclick="const el=document.getElementById('how-it-works');if(el){el.scrollIntoView({behavior:'smooth'});return false;}">How It Works</a>
            <a href="?page=login" target="_self" id="nav-btn-login" class="nav-btn nav-btn-secondary">Login</a>
            <a href="?page=signup" target="_self" id="nav-btn-signup" class="nav-btn nav-btn-primary">Sign Up</a>
        </div>
    </div>
    """, unsafe_allow_html=True)


# =============================================================================
# ROUTE 1: PUBLIC HOME / LANDING PAGE (/)
# =============================================================================
if st.session_state["page"] == "home":
    st.markdown("""
    <div id="top" style="scroll-margin-top: 30px;"></div>
    <div style="text-align: center; padding: 40px 20px 20px 20px; max-width: 900px; margin: 0 auto;">
        <span class="badge badge-real" style="font-size: 0.9rem; padding: 6px 16px; margin-bottom: 16px;">
            Production-Ready Autonomous SRE Platform
        </span>
        <h1 style="font-size: 3.2rem; font-weight: 900; color: #0F2747; margin-top: 12px; margin-bottom: 16px; letter-spacing: -0.02em;">
            Aurex
        </h1>
        <p style="font-size: 1.25rem; color: #52657A; line-height: 1.6; margin-bottom: 24px;">
            Autonomous AI Incident Response Agent powered by real telemetry, RCA, Hindsight learning, human approval, and real execution.
        </p>
        <div style="display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; margin-bottom: 30px;">
            <span class="badge badge-real">REAL TELEMETRY</span>
            <span class="badge sev-critical">REAL INCIDENTS</span>
            <span class="badge badge-tag">REAL RCA</span>
            <span class="badge badge-memory">HINDSIGHT MEMORY</span>
            <span class="badge badge-llm">HUMAN APPROVAL</span>
            <span class="badge badge-status">REAL EXECUTION</span>
            <span class="badge badge-status">REAL VERIFICATION</span>
        </div>
        <p style="font-size: 0.92rem; color: #64748B; font-style: italic; margin-bottom: 30px;">
            Safety First: Automated recommendations strictly enforce mandatory Human Approval before any remediation action is dispatched to production infrastructure.
        </p>
    </div>
    """, unsafe_allow_html=True)

    cta_c1, cta_c2, cta_c3 = st.columns([4, 2, 4])
    with cta_c2:
        if st.session_state.get("auth_token"):
            if st.button("🚀 Go to Dashboard", type="primary", use_container_width=True):
                navigate_to("dashboard")
        else:
            b1, b2 = st.columns(2)
            with b1:
                if st.button("Get Started", type="primary", use_container_width=True):
                    navigate_to("signup")
            with b2:
                if st.button("Sign In", type="secondary", use_container_width=True):
                    navigate_to("login")

    # Workflow Visualization
    st.markdown("""
    <div id="how-it-works" style="scroll-margin-top: 80px;"></div>
    <h3 style='text-align: center; margin-top: 48px; color: #0F2747;'>⚡ 13-Stage Production Incident Workflow</h3>
    """, unsafe_allow_html=True)
    st.markdown("""
    <div class="stepper-box">
        <span class="step-pill">1. Application URL</span>
        <span class="step-arrow">→</span>
        <span class="step-pill">2. Real Telemetry</span>
        <span class="step-arrow">→</span>
        <span class="step-pill">3. Detection</span>
        <span class="step-arrow">→</span>
        <span class="step-pill">4. Incident</span>
        <span class="step-arrow">→</span>
        <span class="step-pill">5. Evidence</span>
        <span class="step-arrow">→</span>
        <span class="step-pill">6. RCA</span>
        <span class="step-arrow">→</span>
        <span class="step-pill highlight">7. Hindsight Recall</span>
        <span class="step-arrow">→</span>
        <span class="step-pill">8. Recommendation</span>
        <span class="step-arrow">→</span>
        <span class="step-pill highlight">9. Human Approval</span>
        <span class="step-arrow">→</span>
        <span class="step-pill">10. Real Execution</span>
        <span class="step-arrow">→</span>
        <span class="step-pill">11. Real Verification</span>
        <span class="step-arrow">→</span>
        <span class="step-pill">12. Resolution</span>
        <span class="step-arrow">→</span>
        <span class="step-pill highlight">13. Retention</span>
    </div>
    """, unsafe_allow_html=True)

    # 9 Feature Cards
    st.markdown("""
    <div id="features" style="scroll-margin-top: 80px;"></div>
    <h3 style='text-align: center; margin-top: 40px; margin-bottom: 24px; color: #0F2747;'>🛡️ Engineered SRE Capabilities</h3>
    """, unsafe_allow_html=True)
    f_cols1 = st.columns(3)
    with f_cols1[0]:
        st.markdown("""
        <div class="feature-card">
            <h4>1. Real-Time Telemetry</h4>
            <p>Probes live HTTP endpoints, monitors latency, parses streaming logs, and captures genuine operational metrics with zero hardcoded simulations.</p>
        </div>
        """, unsafe_allow_html=True)
    with f_cols1[1]:
        st.markdown("""
        <div class="feature-card">
            <h4>2. Incident Detection</h4>
            <p>Deterministic signal evaluation flags connection drops, HTTP 5xx anomalies, error bursts, and thread pool exhaustion immediately.</p>
        </div>
        """, unsafe_allow_html=True)
    with f_cols1[2]:
        st.markdown("""
        <div class="feature-card">
            <h4>3. Evidence Collection</h4>
            <p>Correlates HTTP probe status, active error logs, request IDs, and microservice health into structured, auditable evidence records.</p>
        </div>
        """, unsafe_allow_html=True)

    f_cols2 = st.columns(3)
    with f_cols2[0]:
        st.markdown("""
        <div class="feature-card">
            <h4>4. RCA / Investigation</h4>
            <p>Correlates causal event timelines and performs deep root-cause synthesis backed by Groq LLM and deterministic system heuristics.</p>
        </div>
        """, unsafe_allow_html=True)
    with f_cols2[1]:
        st.markdown("""
        <div class="feature-card">
            <h4>5. Hindsight Memory</h4>
            <p><strong>Recall:</strong> Retrieves past identical alerts. <strong>Reflect:</strong> Assesses historical efficacy. <strong>Retain:</strong> Commits new resolutions into memory.</p>
        </div>
        """, unsafe_allow_html=True)
    with f_cols2[2]:
        st.markdown("""
        <div class="feature-card">
            <h4>6. Human Approval</h4>
            <p>Guarantees cryptographic approval binding. No action is dispatched without explicit SRE operator authorization and audit logging.</p>
        </div>
        """, unsafe_allow_html=True)

    f_cols3 = st.columns(3)
    with f_cols3[0]:
        st.markdown("""
        <div class="feature-card">
            <h4>7. Real Execution</h4>
            <p>Directs remediation actions (service restart, replica scaling, cache flush) to real running service instances through authorized execution targets.</p>
        </div>
        """, unsafe_allow_html=True)
    with f_cols3[1]:
        st.markdown("""
        <div class="feature-card">
            <h4>8. Real Verification</h4>
            <p>Evaluates post-execution telemetry against strict 5-criteria checklist (C1-C5: probe success, error drop, latency stabilization, HTTP 200).</p>
        </div>
        """, unsafe_allow_html=True)
    with f_cols3[2]:
        st.markdown("""
        <div class="feature-card">
            <h4>9. Postmortem & Learning</h4>
            <p>Generates structured post-mortems with before/after metric comparisons and retains operational lessons for future automated recall.</p>
        </div>
        """, unsafe_allow_html=True)


# =============================================================================
# ROUTE 2: SIGNUP PAGE (/signup)
# =============================================================================
elif st.session_state["page"] == "signup":
    col_l, col_form, col_r = st.columns([3, 4, 3])
    with col_form:
        st.markdown("""
        <div style="text-align: center; margin-bottom: 20px;">
            <h2 style="margin: 0; font-size: 2.0rem; color: #0F2747;">Create SRE Account</h2>
            <p style="color: #52657A; font-size: 0.95rem; margin-top: 6px;">Register your credentials in the existing SQLite database.</p>
        </div>
        """, unsafe_allow_html=True)

        with st.form("signup_form"):
            su_username = st.text_input("Username", placeholder="e.g. jdoe_sre")
            su_email = st.text_input("Work Email", placeholder="e.g. jdoe@enterprise.com")
            su_pass = st.text_input("Password", type="password", placeholder="At least 6 characters")
            su_confirm = st.text_input("Confirm Password", type="password", placeholder="Re-enter password")

            su_submit = st.form_submit_button("Create Account", type="primary", use_container_width=True)

            if su_submit:
                if not su_username.strip():
                    st.error("Username is required")
                elif not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", su_email.strip()):
                    st.error("Invalid email format")
                elif len(su_pass) < 6:
                    st.error("Password must be at least 6 characters long")
                elif su_pass != su_confirm:
                    st.error("Password mismatch")
                else:
                    payload = {
                        "username": su_username.strip(),
                        "email": su_email.strip(),
                        "password": su_pass,
                        "confirm_password": su_confirm
                    }
                    try:
                        res = requests.post(f"{BACKEND_URL}/api/auth/signup", json=payload, timeout=5)
                        if res.status_code == 201:
                            data = res.json()
                            st.session_state["incident_id"] = None
                            st.session_state["latest_inspection"] = None
                            st.session_state["selected_app_id"] = None
                            st.session_state.pop("remediation_result", None)
                            st.session_state.pop("verification_result", None)
                            st.session_state.pop("inspection_elapsed_ms", None)
                            st.session_state["auth_token"] = data.get("token")
                            st.session_state["auth_user"] = data.get("user")
                            st.success("🎉 Account created successfully! Redirecting to Dashboard...")
                            time.sleep(0.5)
                            navigate_to("dashboard")
                        elif res.status_code == 409:
                            st.error(res.json().get("detail", "User with this email or username already exists"))
                        else:
                            st.error(res.json().get("detail", "Registration failed. Please check your details."))
                    except Exception as e:
                        st.error(f"Failed to connect to authentication server: {e}")

        st.markdown("""
        <div style="text-align: center; margin-top: 14px;">
            <span style="color: #52657A; font-size: 0.9rem;">Already have an account?</span>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Sign In to Existing Account", use_container_width=True):
            navigate_to("login")


# =============================================================================
# ROUTE 3: LOGIN PAGE (/login)
# =============================================================================
elif st.session_state["page"] == "login":
    col_l, col_form, col_r = st.columns([3, 4, 3])
    with col_form:
        st.markdown("""
        <div style="text-align: center; margin-bottom: 20px;">
            <h2 style="margin: 0; font-size: 2.0rem; color: #0F2747;">SRE Operator Login</h2>
            <p style="color: #52657A; font-size: 0.95rem; margin-top: 6px;">Authenticate against the SQLite database to access the Control Plane.</p>
        </div>
        """, unsafe_allow_html=True)

        with st.form("login_form"):
            li_ident = st.text_input("Username or Email", placeholder="e.g. jdoe_sre or jdoe@enterprise.com")
            li_pass = st.text_input("Password", type="password")

            li_submit = st.form_submit_button("Sign In", type="primary", use_container_width=True)

            if li_submit:
                if not li_ident.strip() or not li_pass:
                    st.error("Please provide both username/email and password")
                else:
                    payload = {
                        "username_or_email": li_ident.strip(),
                        "password": li_pass
                    }
                    try:
                        res = requests.post(f"{BACKEND_URL}/api/auth/login", json=payload, timeout=5)
                        if res.status_code == 200:
                            data = res.json()
                            st.session_state["incident_id"] = None
                            st.session_state["latest_inspection"] = None
                            st.session_state["selected_app_id"] = None
                            st.session_state.pop("remediation_result", None)
                            st.session_state.pop("verification_result", None)
                            st.session_state.pop("inspection_elapsed_ms", None)
                            st.session_state["auth_token"] = data.get("token")
                            st.session_state["auth_user"] = data.get("user")
                            st.success("✅ Login successful! Redirecting to Dashboard...")
                            time.sleep(0.5)
                            navigate_to("dashboard")
                        elif res.status_code == 401:
                            st.error("Invalid credentials")
                        else:
                            st.error(res.json().get("detail", "Login failed. Please verify credentials."))
                    except Exception as e:
                        st.error(f"Failed to connect to authentication server: {e}")

        st.markdown("""
        <div style="text-align: center; margin-top: 14px;">
            <span style="color: #52657A; font-size: 0.9rem;">Need an account?</span>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Create New Account", use_container_width=True):
            navigate_to("signup")


# =============================================================================
# ROUTE 4: AUTHENTICATED SRE DASHBOARD (/dashboard)
# =============================================================================
elif st.session_state["page"] == "dashboard":
    # Authentication Guard
    if not st.session_state.get("auth_token"):
        st.warning("⚠️ Unauthenticated access. Please sign in to access the Incident Response Dashboard.")
        time.sleep(0.5)
        navigate_to("login")

    current_user = st.session_state.get("auth_user", {})

    # Fetch Registered Applications for this authenticated user
    registered_apps = get_registered_applications()

    # Sort applications so primary monitored services appear first
    def app_sort_key(a: Dict[str, Any]):
        aid = a.get("application_id", "")
        if aid == "order-settlement-service":
            return (0, aid)
        elif aid == "checkout-service":
            return (1, aid)
        elif aid == "payment-test-api":
            return (2, aid)
        elif aid == "brand-new-zero-memory":
            return (3, aid)
        elif aid == "unreachable-service":
            return (4, aid)
        return (5, a.get("application_name", ""))

    registered_apps.sort(key=app_sort_key)

    app_options: Dict[str, Dict[str, Any]] = {}
    for app in registered_apps:
        aid = app.get("application_id", "")
        aname = app.get("application_name", app.get("name", "Service"))
        lbl = f"🏢 {aname} ({aid})"
        app_options[lbl] = app

    def run_app_inspection(target_app_id: str):
        """Helper to deterministically inspect an application and update state."""
        try:
            t0 = time.perf_counter()
            resp = requests.post(
                f"{BACKEND_URL}/api/incidents/inspect",
                json={"application_id": target_app_id},
                headers=get_auth_headers(),
                timeout=15
            )
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
            if resp.status_code == 200:
                data = resp.json()
                st.session_state["latest_inspection"] = data
                st.session_state["inspection_elapsed_ms"] = elapsed_ms
                incident_obj = data.get("incident")
                if data.get("incident_detected") and incident_obj and incident_obj.get("incident_id"):
                    st.session_state["incident_id"] = incident_obj["incident_id"]
                else:
                    st.session_state["incident_id"] = None
                return True, elapsed_ms
            else:
                st.error(f"Inspection error ({resp.status_code}): {resp.text}")
                return False, 0
        except Exception as e:
            st.error(f"Failed to communicate with backend: {e}")
            return False, 0

    # =========================================================================
    # LEFT SIDEBAR: LIGHT BLUE (#F1F6FC) ENTERPRISE CONSOLE
    # =========================================================================
    with st.sidebar:
        st.markdown("""
        <div style="margin-bottom: 14px;">
            <div style="font-size: 1.25rem; font-weight: 800; color: #0F2747; display: flex; align-items: center; gap: 8px;">
                <span>🛡️</span> Aurex
            </div>
        </div>
        """, unsafe_allow_html=True)

        usr_card = f"""
        <div style="background: #FFFFFF; border: 1px solid #D9E2EC; border-radius: 8px; padding: 12px 14px; margin-bottom: 12px; box-shadow: 0 1px 2px rgba(15, 39, 71, 0.03);">
            <div style="font-size: 0.80rem; color: #52657A; text-transform: uppercase; font-weight: 700; letter-spacing: 0.04em;">Logged-In Operator</div>
            <div style="font-size: 0.96rem; font-weight: 700; color: #0F2747; margin-top: 3px;">{current_user.get('username', 'oncall-sre')}</div>
            <div style="font-size: 0.82rem; color: #52657A; margin-top: 2px;">{current_user.get('email', 'sre@enterprise.com')}</div>
        </div>
        """
        render_html(usr_card)

        if st.button("🚪 Log Out", key="sb_logout", use_container_width=True):
            logout()

        st.markdown("<hr style='margin: 14px 0; border: none; border-top: 1px solid #D9E2EC;'>", unsafe_allow_html=True)
        st.markdown("<div style='font-size: 0.88rem; font-weight: 700; color: #0F2747; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 8px;'>System Status</div>", unsafe_allow_html=True)
        try:
            h_resp = requests.get(f"{BACKEND_URL}/health", timeout=1.5)
            if h_resp.status_code == 200:
                h_data = h_resp.json()
                sys_card = f"""
                <div style="background: #FFFFFF; border: 1px solid #D9E2EC; border-radius: 8px; padding: 12px 14px; margin-bottom: 14px; font-size: 0.86rem; line-height: 1.6; box-shadow: 0 1px 2px rgba(15, 39, 71, 0.03);">
                    <div style="display: flex; align-items: center; gap: 6px; color: #15803D; font-weight: 700;">
                        <span>●</span> Backend API: Connected
                    </div>
                    <div style="margin-top: 6px; color: #334155;"><b>Target:</b> <code style="font-size: 0.78rem;">Real Infrastructure</code></div>
                    <div style="color: #334155;"><b>Execution:</b> <code style="font-size: 0.78rem;">RealExecutionTarget</code></div>
                    <div style="color: #334155;"><b>Bank ID:</b> <code style="font-size: 0.78rem;">{h_data.get('memory_bank')}</code></div>
                    <div style="color: #334155;"><b>Model:</b> <code style="font-size: 0.78rem;">{h_data.get('llm_model')}</code></div>
                </div>
                """
                render_html(sys_card)
            else:
                st.warning(f"Backend status: {h_resp.status_code}")
        except Exception:
            render_html("""
            <div style="background: #FEE2E2; border: 1px solid #FECACA; border-radius: 8px; padding: 10px 14px; color: #991B1B; font-weight: 700; font-size: 0.88rem;">
                ● Backend API: Offline
            </div>
            """)

        st.markdown("<hr style='margin: 14px 0; border: none; border-top: 1px solid #D9E2EC;'>", unsafe_allow_html=True)
        st.markdown("<div style='font-size: 0.88rem; font-weight: 700; color: #0F2747; text-transform: uppercase; letter-spacing: 0.04em;'>Monitored Application Fleet</div>", unsafe_allow_html=True)
        st.caption("Live health telemetry collected directly from registered application endpoints.")

        if registered_apps:
            for app in registered_apps:
                base_url = app.get("base_url", "")
                health_ep = app.get("health_endpoint", "/health")
                app_name = app.get("application_name", app.get("name", "App"))
                app_id = app.get("application_id")

                probe = probe_application_live_health(base_url, health_ep)
                if probe["reachable"]:
                    if probe["healthy"]:
                        dot_color = "#15803D"
                        status_text = f"Reachable (HTTP {probe['status_code']}, {probe['latency_ms']}ms)"
                        status_color = "#15803D"
                    else:
                        dot_color = "#B91C1C"
                        status_text = f"Degraded (HTTP {probe['status_code']})"
                        status_color = "#B91C1C"
                else:
                    dot_color = "#B45309"
                    status_text = "Application Unreachable"
                    status_color = "#B45309"

                app_card = f"""
                <div style="background: #FFFFFF; border: 1px solid #D9E2EC; border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; box-shadow: 0 1px 2px rgba(15, 39, 71, 0.03);">
                    <div style="display: flex; align-items: center; justify-content: space-between;">
                        <span style="font-weight: 700; color: #0F2747; font-size: 0.90rem;">
                            <span style="color: {dot_color}; margin-right: 4px;">●</span> {app_name}
                        </span>
                        <code style="font-size: 0.72rem; background: #F1F6FC; color: #52657A; padding: 1px 5px; border-radius: 4px; border: 1px solid #D9E2EC;">{app_id}</code>
                    </div>
                    <div style="font-size: 0.80rem; margin-top: 4px; font-weight: 600; color: {status_color};">
                        {status_text}
                    </div>
                </div>
                """
                render_html(app_card)
        else:
            st.info("No applications registered.")

        if st.button("🔄 Refresh Fleet Status", use_container_width=True):
            st.rerun()

    # =========================================================================
    # HERO / SYSTEM SUMMARY CARD
    # =========================================================================
    st.markdown("""
    <div class="saas-card" style="margin-bottom: 20px; padding: 22px 26px;">
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px;">
            <div>
                <h2 style="margin: 0; font-size: 1.85rem; font-weight: 800; color: #0F2747; letter-spacing: -0.02em;">
                    🛡️ Aurex
                </h2>
                <p style="margin: 4px 0 0 0; color: #52657A; font-size: 0.98rem; font-weight: 500;">
                    Autonomous Production SRE Control Plane backed by Hindsight
                </p>
            </div>
            <div style="display: flex; flex-wrap: wrap; gap: 8px;">
                <span class="badge badge-memory">Hindsight Memory</span>
                <span class="badge badge-llm">Groq LLM Engine</span>
                <span class="badge badge-real">Real Telemetry</span>
                <span class="badge badge-status">FastAPI Backend</span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Top-Level Tabs
    tab_response, tab_registry, tab_archive = st.tabs([
        "🚨 Live Incident Response",
        "🏢 Application Fleet Registry",
        "📜 Incident Post-Mortem Archive"
    ])

    # =========================================================================
    # TAB 1: LIVE INCIDENT RESPONSE
    # =========================================================================
    with tab_response:
        st.subheader("1. Monitored Application & Live Telemetry")
        st.caption("Select any registered microservice to probe its real HTTP health and application log endpoints.")

        col_sel, col_info = st.columns([5, 5])
        selected_app = None

        if app_options:
            with col_sel:
                app_keys = list(app_options.keys())
                
                # Determine default index based on selected_app_id
                default_idx = 0
                sel_id = st.session_state.get("selected_app_id")
                if sel_id:
                    for idx, (lbl, app_obj) in enumerate(app_options.items()):
                        if app_obj.get("application_id") == sel_id:
                            default_idx = idx
                            break

                selected_label = st.selectbox(
                    "Select Monitored Application",
                    options=app_keys,
                    index=default_idx,
                    key="select_monitored_app"
                )
                selected_app = app_options[selected_label]
                cur_app_id = selected_app.get("application_id")
                if st.session_state.get("selected_app_id") != cur_app_id:
                    st.session_state["selected_app_id"] = cur_app_id
                    st.session_state["incident_id"] = None
                    st.session_state["latest_inspection"] = None

                inspect_btn = st.button("📡 Collect Live Telemetry & Inspect Application", type="primary", use_container_width=True)

            with col_info:
                app_id_card = f"""
                <div class="saas-card" style="padding: 16px 20px; margin-bottom: 0;">
                    <div style="font-size: 0.88rem; font-weight: 700; color: #0F2747; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 10px;">
                        Active Application Identity (from Registry)
                    </div>
                    <div style="font-size: 0.90rem; color: #334155; line-height: 1.7;">
                        <div><b>Application Name:</b> <span style="color: #0F2747; font-weight: 600;">{selected_app.get('application_name', selected_app.get('name'))}</span></div>
                        <div><b>Application ID:</b> <code>{selected_app.get('application_id')}</code></div>
                        <div><b>Service ID:</b> <code>{selected_app.get('service_id') or selected_app.get('service')}</code></div>
                        <div><b>Base URL:</b> <code>{selected_app.get('base_url')}</code></div>
                        <div><b>Health Probe:</b> <code>{selected_app.get('health_endpoint', '/health')}</code></div>
                        <div><b>Log Endpoint:</b> <span style="font-size: 0.82rem; color: #52657A;">{selected_app.get('log_collection_method', 'http')} -> {selected_app.get('base_url').rstrip('/')}/logs/recent</span></div>
                    </div>
                </div>
                """
                render_html(app_id_card)
        else:
            render_html("""
            <div class="saas-card" style="padding: 32px 24px; text-align: center; border: 2px dashed #CBD5E1; background: #FFFFFF; margin-top: 12px;">
                <div style="font-size: 2.4rem; margin-bottom: 8px;">🏢</div>
                <h3 style="margin: 0 0 8px 0; color: #0F2747; font-size: 1.25rem; font-weight: 700;">No Applications Registered Yet</h3>
                <p style="color: #52657A; font-size: 0.95rem; max-width: 520px; margin: 0 auto 16px auto; line-height: 1.6;">
                    You are logged into a clean, isolated workspace with zero incident history. Navigate to the <b>Application Fleet Registry</b> tab to register your target microservice and begin autonomous monitoring.
                </p>
            </div>
            """)
            inspect_btn = False

        # Execute Manual Inspection
        if inspect_btn and selected_app:
            app_id = selected_app.get("application_id")
            with st.spinner(f"Collecting real telemetry from {selected_app.get('application_name')} at {selected_app.get('base_url')}..."):
                ok, res_val = run_app_inspection(app_id)
                if ok:
                    st.success(f"Telemetry collected and evaluated in {res_val}ms!")
                    st.rerun()

        # Display Inspection Results
        inspection_data = st.session_state.get("latest_inspection")
        if inspection_data:
            st.markdown("---")
            incident_detected = inspection_data.get("incident_detected", False)
            incident = inspection_data.get("incident")
            evidence = inspection_data.get("evidence", {})
            health = evidence.get("health_check", {})
            logs = evidence.get("logs", {})

            h_status = health.get("status", "UNKNOWN")
            h_http = health.get("http_status")
            h_endpoint = health.get("endpoint", "")
            app_name_disp = selected_app.get("application_name", "Application") if selected_app else "Application"

            # Case A: Application Unreachable
            if h_status == "UNREACHABLE":
                unreach_html = f"""
                <div class="unreachable-box">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <span style="font-size: 1.25rem; font-weight: 800; color: #B45309;">
                            🟡 Application Unreachable: {app_name_disp}
                        </span>
                        <span class="badge sev-high">WARNING / UNREACHABLE</span>
                    </div>
                    <div style="font-size: 1.0rem; color: #78350F; margin-bottom: 6px;">
                        <b>Network Telemetry Failure:</b> {clean_field(health.get('error_message'), 'Connection refused / Target host unreachable')}
                    </div>
                    <div style="font-size: 0.92rem; color: #92400E;">
                        <b>Target Endpoint:</b> <code>{h_endpoint}</code> &nbsp;|&nbsp; 
                        <b>HTTP Status:</b> <span style="font-weight: 700;">Timeout / Connection Refused</span>
                    </div>
                </div>
                """
                render_html(unreach_html)

            # Case B: Incident Detected
            if incident_detected and incident:
                sev = incident.get("severity", "medium").lower()
                sev_class = f"sev-{sev}" if sev in ["critical", "high", "medium", "low"] else "sev-medium"

                alert_html = f"""
                <div class="incident-alert-box">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                        <div>
                            <span style="font-size: 1.3rem; font-weight: 800; color: #B91C1C;">
                                🔴 ACTIVE INCIDENT: {incident.get('incident_id')}
                            </span>
                        </div>
                        <div>
                            <span class="badge {sev_class}">SEVERITY: {sev.upper()}</span>
                            <span class="badge badge-tag">SERVICE: {incident.get('service')}</span>
                            <span class="badge badge-status">STATUS: {incident.get('status').upper()}</span>
                        </div>
                    </div>
                    <div style="font-size: 1.05rem; color: #7F1D1D; margin-bottom: 8px;">
                        <b>Description:</b> {incident.get('description')}
                    </div>
                    <div style="font-size: 0.92rem; color: #991B1B;">
                        <b>Detection Reason:</b> {inspection_data.get('reason')} &nbsp;|&nbsp; 
                        <b>Timestamp:</b> <span>{incident.get('timestamp')}</span>
                    </div>
                </div>
                """
                render_html(alert_html)

                col_symptoms, col_errors = st.columns(2)
                with col_symptoms:
                    st.markdown("#### ⚠️ Observed Symptoms")
                    symptoms = incident.get("symptoms", [])
                    if symptoms:
                        for s in symptoms:
                            st.markdown(f"- 🔴 **{s}**")
                    else:
                        st.caption("No specific symptoms recorded.")

                with col_errors:
                    st.markdown("#### 💥 Captured Error Messages")
                    errors = incident.get("errors", [])
                    if errors:
                        for err in errors:
                            st.markdown(f"- `{err}`")
                    else:
                        st.caption("No error payloads captured.")

                # Investigation Timeline & RCA
                investigation = inspection_data.get("investigation") or {}
                timeline = inspection_data.get("timeline") or investigation.get("timeline", [])
                analysis = inspection_data.get("analysis") or investigation.get("analysis")
                resolution = inspection_data.get("resolution") or investigation.get("resolution")
                hindsight_res = inspection_data.get("hindsight") or investigation.get("hindsight")

                if analysis:
                    st.markdown("---")
                    st.markdown("### 🔍 Incident Investigation & Root Cause Analysis")

                    overview_html = f"""
                    <div style="display:flex; flex-wrap:wrap; gap:10px; margin-bottom:16px;">
                        <span class="badge badge-tag" style="font-size:0.9rem;">CATEGORY: {analysis.get('incident_type')}</span>
                        <span class="badge {sev_class}" style="font-size:0.9rem;">SEVERITY: {str(analysis.get('severity', sev)).upper()}</span>
                        <span class="badge badge-memory" style="font-size:0.9rem;">CONFIDENCE: {int(analysis.get('confidence', 0.9) * 100)}%</span>
                        {"<span class='badge badge-llm' style='font-size:0.9rem;'>AI SRE Synthesized (Groq)</span>" if investigation.get("llm_assisted") else "<span class='badge badge-status' style='font-size:0.9rem;'>Deterministic Evidence RCA</span>"}
                    </div>
                    """
                    render_html(overview_html)

                    # Grounded Causal Workflow Chain
                    st.markdown("#### 🔄 Grounded Incident Investigation & Remediation Chain")

                    sig_items = []
                    h_status_clean = clean_field(health.get("status"), "UNKNOWN")
                    h_http_clean = clean_field(health.get("http_status"), "None")
                    h_endpoint_clean = clean_field(health.get("endpoint"), "N/A")

                    sig_items.append(f"<b>Health probe:</b> <span style='color:#B91C1C; font-weight:700;'>{h_status_clean}</span> &nbsp;|&nbsp; <b>HTTP code:</b> <code>{h_http_clean}</code>")
                    sig_items.append(f"<b>Endpoint:</b> <code>{h_endpoint_clean}</code>")

                    raw_signals = (hindsight_res.get("current_failure_signals") if hindsight_res else None) or []
                    if not raw_signals:
                        raw_signals = incident.get("symptoms", []) or ([analysis.get("failure")] if analysis and analysis.get("failure") else [])

                    for s in raw_signals:
                        if s and str(s).strip() and str(s).strip() not in sig_items:
                            sig_items.append(f"<b>Current symptom:</b> {s}")

                    cur_signals_html = "".join(
                        f"<div style='font-size:0.92rem; color:#1E293B; background:#FFFFFF; padding:8px 14px; border-radius:6px; border:1px solid #FECACA; margin-bottom:6px;'>"
                        f"<span style='color:#DC2626; font-weight:900; margin-right:8px;'>•</span>{item}</div>"
                        for item in sig_items
                    )

                    hs_query = (hindsight_res.get("query") if hindsight_res else None) or f"{app_name_disp} {analysis.get('incident_type', '').lower()} failure"
                    matched_mem = (hindsight_res.get("matched_memory") if hindsight_res else None) or {}
                    mem_id_raw = hindsight_res.get("matched_memory_id") or matched_mem.get("id")
                    has_real_memory = bool(mem_id_raw and str(mem_id_raw).strip().lower() not in ["none", "null", ""])
                    is_grounded = bool(has_real_memory and (resolution.get("memory_grounded", True) if resolution else True))

                    rec_actions = resolution.get("actions", []) if resolution else []
                    if not rec_actions and analysis and analysis.get("root_cause"):
                        rec_actions = [f"Investigate: {analysis.get('root_cause')}"]

                    rec_actions_html = "".join(
                        f"<div style='font-size:0.92rem; font-weight:600; color:#0F172A; background:#FFFFFF; padding:8px 14px; border-radius:6px; border:1px solid #BBF7D0; margin-bottom:6px; display:flex; align-items:flex-start; gap:8px;'>"
                        f"<span style='color:#16A34A; font-weight:900;'>✓</span><span>{act}</span></div>"
                        for act in rec_actions
                    )

                    if is_grounded:
                        mem_id = str(mem_id_raw).strip()
                        raw_title = matched_mem.get("title") or matched_mem.get("incident_id")
                        mem_title = clean_field(raw_title, "Not available")
                        mem_rc = clean_field(hindsight_res.get("historical_root_cause") or matched_mem.get("root_cause"), "Upstream latency / dependency failure")
                        mem_res = clean_field(hindsight_res.get("historical_remediation") or matched_mem.get("resolution"), "Circuit breaker + bounded retry + timeout adjustment")

                        memory_step_html = f"""
                        <div class="flow-card flow-memory">
                            <div class="flow-label" style="color:#3730A3;">🧠 3. MATCHED HINDSIGHT MEMORY</div>
                            <div style="display:flex; flex-direction:column; gap:8px; margin-top:8px;">
                                <div style="font-size:0.92rem; color:#0F172A;">
                                    <b>Memory ID:</b> <code style="background:#FFFFFF; color:#3730A3; padding:3px 8px; border-radius:4px; font-weight:700; border:1px solid #C7D2FE;">{mem_id}</code>
                                </div>
                                <div style="font-size:0.92rem; color:#0F172A;">
                                    <b>Historical Incident Reference:</b> <span style="font-weight:600; color:#1E1B4B;">{mem_title}</span>
                                </div>
                                <div style="font-size:0.92rem; color:#0F172A;">
                                    <b>Historical Root Cause:</b>
                                    <div style="margin-top:4px; color:#0F172A; background:#FFFFFF; padding:8px 12px; border-radius:6px; border:1px solid #C7D2FE; font-weight:500;">{mem_rc}</div>
                                </div>
                                <div style="font-size:0.92rem; color:#0F172A;">
                                    <b>Historical Remediation:</b>
                                    <div style="margin-top:4px; color:#0F172A; background:#FFFFFF; padding:8px 12px; border-radius:6px; border:1px solid #C7D2FE; font-weight:500;">{mem_res}</div>
                                </div>
                            </div>
                        </div>
                        """
                        rec_header = "🛠️ 4. MEMORY-GROUNDED RESOLUTION"
                        rec_sub = f"Derived from historical remediation in Hindsight Memory <code>{mem_id}</code>:"
                        source_tag = f"<b>Source:</b> Hindsight Memory <code>{mem_id}</code>"
                    else:
                        memory_step_html = f"""
                        <div class="flow-card flow-no-history">
                            <div class="flow-label" style="color:#334155;">🧠 3. NO RELEVANT HISTORICAL INCIDENTS</div>
                            <div style="font-size:1.0rem; font-weight:600; color:#0F2747; margin-top:6px; margin-bottom:6px;">
                                No relevant past incidents were found for this application/service.
                            </div>
                            <div style="font-size:0.92rem; color:#52657A;">
                                <b>Matched Memories:</b> <code style="background:#FFFFFF; color:#0F2747; padding:2px 8px; border-radius:4px; font-weight:700; border:1px solid #CBD5E1;">0</code>
                            </div>
                        </div>
                        """
                        rec_header = "🛠️ 4. CURRENT-EVIDENCE RESOLUTION"
                        rec_sub = "Guidance formulated directly from current runtime signals:"
                        source_tag = "<b>Source:</b> Current runtime telemetry (no prior historical match)"

                    flow_html = f"""
                    <div class="flow-chain-container">
                        <div class="flow-card flow-current">
                            <div class="flow-label" style="color:#991B1B;">🔴 1. CURRENT LIVE INCIDENT SIGNALS:</div>
                            {cur_signals_html}
                        </div>
                        <div class="flow-arrow">↓</div>
                        <div class="flow-card flow-query">
                            <div class="flow-label" style="color:#0369A1;">🔍 2. HINDSIGHT QUERY:</div>
                            <div style="font-size:1.0rem; font-weight:700; color:#0C4A6E; font-family:'JetBrains Mono', monospace; background:#FFFFFF; padding:12px 16px; border-radius:6px; border:1px solid #BAE6FD; word-break:break-word;">
                                "{hs_query}"
                            </div>
                        </div>
                        <div class="flow-arrow">↓</div>
                        {memory_step_html}
                        <div class="flow-arrow">↓</div>
                        <div class="flow-card flow-recommendation">
                            <div class="flow-label" style="color:#166534;">{rec_header}</div>
                            <div style="font-size:0.95rem; font-weight:700; color:#14532D; margin-bottom:12px;">
                                {rec_sub}
                            </div>
                            <div style="display:flex; flex-direction:column; gap:6px;">
                                {rec_actions_html}
                            </div>
                            <div style="margin-top:10px; font-size:0.86rem; color:#166534;">{source_tag}</div>
                        </div>
                    </div>
                    """
                    render_html(flow_html)

                    col_tl, col_rca = st.columns([5, 5])
                    with col_tl:
                        st.markdown("#### ⏱️ Incident Progression Timeline")
                        if timeline:
                            tl_items = []
                            for t in timeline:
                                lvl = (t.get("level") or "INFO").upper()
                                dot_cls = "timeline-dot-error" if lvl in ["ERROR", "CRITICAL"] else ("timeline-dot-warn" if lvl in ["WARN", "WARNING"] else "timeline-dot-info")
                                req_tag = f" <code style='font-size:0.75rem;'>req_id={t.get('request_id')}</code>" if t.get("request_id") else ""
                                tl_items.append(
                                    f'<div class="timeline-item">'
                                    f'<div class="timeline-dot {dot_cls}"></div>'
                                    f'<div style="font-size:0.8rem; color:#52657A;"><b>{t.get("timestamp")}</b> [{lvl}]{req_tag}</div>'
                                    f'<div style="font-size:0.88rem; color:#0F2747; margin-top:2px; font-weight:500;">{t.get("event")}</div>'
                                    f'</div>'
                                )
                            render_html(f'<div class="timeline-container">{"".join(tl_items)}</div>')
                        else:
                            st.caption("No timeline events correlated.")

                    with col_rca:
                        st.markdown("#### 🎯 Root Cause Analysis (RCA)")
                        rca_html = f"""
                        <div class="rca-card">
                            <div class="rca-title" style="color:#B45309;">⚠️ Failure:</div>
                            <div style="font-size:0.95rem; color:#0F2747; margin-bottom:12px; font-weight:500;">{analysis.get('failure')}</div>
                            <div class="rca-title" style="color:#B91C1C;">💥 Likely Root Cause:</div>
                            <div class="rca-root-cause">{analysis.get('root_cause')}</div>
                            <div class="rca-title" style="color:#0369A1;">🔬 Why (Causal Grounding):</div>
                            <div style="font-size:0.92rem; color:#334155; margin-bottom:12px; line-height:1.5;">{analysis.get('why')}</div>
                            <div class="rca-title" style="color:#9A3412;">📉 Impact:</div>
                            <div style="font-size:0.92rem; color:#9A3412; background:#FFF7ED; padding:8px 12px; border-radius:6px; border:1px solid #FED7AA; line-height:1.5;">{analysis.get('impact')}</div>
                        </div>
                        """
                        render_html(rca_html)

            # Case C: Healthy Application (Only when NO incident was detected)
            elif not incident_detected:
                healthy_html = f"""
                <div class="healthy-box">
                    <div style="font-size: 1.25rem; font-weight: 800; color: #15803D; margin-bottom: 8px;">
                        🟢 No Active Incident — Application Healthy
                    </div>
                    <div style="color: #166534; font-size: 0.95rem;">
                        <b>Probe Target:</b> <code>{h_endpoint}</code> &nbsp;|&nbsp; 
                        <b>HTTP Status:</b> <code style="color:#15803D; font-weight:700;">200 OK</code> &nbsp;|&nbsp; 
                        <b>Telemetry Evaluation:</b> <span>{inspection_data.get('reason')}</span>
                    </div>
                    <div style="margin-top: 8px; font-size: 0.88rem; color: #14532D;">
                        Real telemetry is being actively collected. All service health metrics are nominal. No incident response is required.
                    </div>
                </div>
                """
                render_html(healthy_html)

            # Detailed Real Telemetry Breakdown Cards
            st.markdown("#### 🔬 Live Telemetry & Evidence Breakdown")
            col_health, col_logs = st.columns(2)

            with col_health:
                st.markdown("**HTTP Health Probe Telemetry:**")
                h_latency = health.get("response_time_ms", 0)
                resp_body = health.get('response_body')
                resp_body_html = ""
                if resp_body:
                    body_prev = format_preview_dict(resp_body)
                    resp_body_html = f"<div style='margin-top:8px;'><b style='color:#52657A;'>Response Body:</b> <code>{html.escape(body_prev)}</code></div>"

                tc_health = f"""
                <div class="telemetry-card">
                    <div><b style="color:#52657A;">Status:</b> <code>{h_status}</code></div>
                    <div><b style="color:#52657A;">HTTP Status Code:</b> <code>{h_http if h_http is not None else 'None (Connection Error)'}</code></div>
                    <div><b style="color:#52657A;">Response Latency:</b> <code>{h_latency} ms</code></div>
                    <div><b style="color:#52657A;">Target Endpoint:</b> <code>{h_endpoint}</code></div>
                    {f"<div style='margin-top:8px; color:#B91C1C;'><b>Error Detail:</b> {health.get('error_message')}</div>" if health.get('error_message') else ""}
                    {resp_body_html}
                </div>
                """
                render_html(tc_health)

            with col_logs:
                st.markdown("**Application Log Telemetry:**")
                l_avail = logs.get("available", False)
                l_source = logs.get("source") or "None"
                l_err = logs.get("error_count", 0)
                l_warn = logs.get("warn_count", 0)
                l_active_err = logs.get("active_error_count")

                tc_logs = f"""
                <div class="telemetry-card">
                    <div><b style="color:#52657A;">Log Endpoint Available:</b> <code>{l_avail}</code></div>
                    <div><b style="color:#52657A;">Log Endpoint:</b> <code>{l_source}</code></div>
                    <div><b style="color:#52657A;">Errors in Collected Logs:</b> <span style="color: #B91C1C; font-weight: 700;">{l_err}</span></div>
                    <div><b style="color:#52657A;">Active Errors:</b> <span style="color: #B91C1C; font-weight: 700;">{l_active_err if l_active_err is not None else 'Metric unavailable'}</span></div>
                    <div><b style="color:#52657A;">Warnings in Collected Logs:</b> <span style="color: #B45309; font-weight: 700;">{l_warn}</span></div>
                    {f"<div style='margin-top:8px; color:#B91C1C;'><b>Log Error:</b> {logs.get('error_message')}</div>" if logs.get('error_message') else ""}
                </div>
                """
                render_html(tc_logs)

            # Log entries viewer
            log_entries = logs.get("entries", [])
            if log_entries:
                with st.expander(f"📜 View Recent Real Log Lines for {app_name_disp} ({len(log_entries)} captured)", expanded=False):
                    for entry in log_entries:
                        lvl = entry.get("level", "INFO")
                        msg = entry.get("raw", "")
                        css_class = "log-line-error" if lvl == "ERROR" else ("log-line-warn" if lvl == "WARN" else "log-line-info")
                        render_html(f"<div class='{css_class}'>[{lvl}] {msg}</div>")

        # =====================================================================
        # INCIDENT REMEDIATION: APPROVAL, REAL EXECUTION & VERIFICATION
        # =====================================================================
        active_incident_id = st.session_state.get("incident_id")
        incident_data = None

        if active_incident_id:
            try:
                inc_resp = requests.get(f"{BACKEND_URL}/incidents/{active_incident_id}", headers=get_auth_headers(), timeout=5.0)
                if inc_resp.status_code == 200:
                    incident_data = inc_resp.json()
            except Exception:
                pass

        if incident_data and selected_app:
            inc_app = incident_data.get("application_id")
            inc_svc = incident_data.get("service_id") or incident_data.get("service")
            cur_app = selected_app.get("application_id")
            cur_svc = selected_app.get("service_id") or selected_app.get("service")
            if (inc_app and cur_app and inc_app != cur_app) or (inc_svc and cur_svc and inc_svc != cur_svc):
                incident_data = None
                st.session_state["incident_id"] = None

        if not inspection_data or not inspection_data.get("incident_detected", False):
            incident_data = None
            st.session_state["incident_id"] = None

        if incident_data:
            inc_status = incident_data.get("status", "AWAITING_APPROVAL")
            rec_action = incident_data.get("recommended_action")
            approval_obj = incident_data.get("approval", {})
            app_state = approval_obj.get("state", "PENDING")
            cycle = incident_data.get("cycle", 1)

            if inc_status not in ("RESOLVED", "RESOLVED_CLOSED", "STALE_RECOVERED"):
                st.markdown("---")
                st.subheader("2. Recommended Remediation & Human Approval Gate")

                if app_state == "APPROVED":
                    state_badge = '<span class="badge badge-status" style="font-size: 0.95rem; padding: 6px 14px;">✅ APPROVED</span>'
                elif app_state == "REJECTED":
                    state_badge = '<span class="badge sev-critical" style="font-size: 0.95rem; padding: 6px 14px;">❌ REJECTED</span>'
                else:
                    state_badge = '<span class="badge" style="background:#FEF3C7; color:#B45309; border:1px solid #FCD34D; font-size: 0.95rem; padding: 6px 14px;">⏳ PENDING HUMAN APPROVAL</span>'

                st.markdown(f"""
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <span style="font-size: 1.1rem; font-weight: 700; color: #0F2747;">Remediation Action Proposal (Cycle #{cycle})</span>
                    <div>{state_badge}</div>
                </div>
                """, unsafe_allow_html=True)

                if rec_action:
                    action_fp = incident_data.get('action_fingerprint', '')
                    rec_params = rec_action.get('params') or {}
                    params_preview = format_preview_dict(rec_params, default="None (Standard execution)")

                    st.markdown(f"""
                    <div class="action-card">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 1.15rem; font-weight: 700; color: #1E40AF;">
                                ⚡ Recommended Action: <code>{rec_action.get('type')}</code> on <code>{rec_action.get('target_service')}</code>
                            </span>
                            <span style="font-size: 0.8rem; color: #52657A; font-family: monospace;">
                                Fingerprint: {action_fp[:16]}...
                            </span>
                        </div>
                        <div style="margin-top: 10px; font-size: 0.95rem; color: #0F2747;">
                            <b>Parameters:</b> <code>{params_preview}</code>
                        </div>
                        <div style="margin-top: 8px; font-size: 0.92rem; color: #52657A;">
                            <b>Rationale:</b> {rec_action.get('rationale', 'Automated recommendation')}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.warning("⚠️ No automatic action was recommended. Select an allowlisted action manually below:")

                # Approval Form
                if app_state == "PENDING":
                    col_app_inputs, col_btn = st.columns([7, 3])
                    with col_app_inputs:
                        approver_name = st.text_input("Approver Name / SRE ID", value=current_user.get("username", "oncall-sre"), key="approver_name_input")
                        approval_note = st.text_input("Approval / Override Note", value="Verified against live logs and Hindsight memory", key="approval_note_input")

                        override_action_enabled = st.checkbox(
                            "🛠️ Override / Custom Action Selection",
                            value=(rec_action is None),
                            key="override_action_cb"
                        )
                        if override_action_enabled or not rec_action:
                            svc_target = incident_data.get("service_id") or incident_data.get("service") or "payment-service"
                            override_type = st.selectbox("Action Type", ["restart_service", "scale_replicas", "set_config", "flush_cache", "rollback_deployment"])
                            override_service = st.text_input("Target Service", value=svc_target)
                            override_params_str = st.text_input("Action Params (JSON)", value="{}")

                    with col_btn:
                        st.write(" ")
                        st.write(" ")
                        if st.button("✅ Approve Action", type="primary", use_container_width=True):
                            payload = {"approver": approver_name.strip() or "oncall-sre", "note": approval_note.strip()}
                            if override_action_enabled or not rec_action:
                                try:
                                    params_dict = json.loads(override_params_str.strip() or "{}")
                                except Exception:
                                    params_dict = {}
                                payload["action"] = {
                                    "type": override_type,
                                    "target_service": override_service,
                                    "params": params_dict
                                }

                            app_resp = requests.post(f"{BACKEND_URL}/incidents/{active_incident_id}/approve", json=payload, headers=get_auth_headers(), timeout=10)
                            if app_resp.status_code == 200:
                                st.success("Action approved!")
                                st.rerun()
                            else:
                                st.error(f"Approval failed: {app_resp.text}")

                        if st.button("❌ Reject Action", type="secondary", use_container_width=True):
                            rej_payload = {"approver": approver_name.strip() or "oncall-sre", "note": approval_note.strip() or "Rejected by engineer"}
                            rej_resp = requests.post(f"{BACKEND_URL}/incidents/{active_incident_id}/reject", json=rej_payload, headers=get_auth_headers(), timeout=5)
                            if rej_resp.status_code == 200:
                                st.warning("Action rejected!")
                                st.rerun()
                            else:
                                st.error(f"Rejection failed: {rej_resp.text}")

                elif app_state == "REJECTED":
                    st.warning("⚠️ Action Rejected by SRE. Provide domain correction to re-diagnose with refined context:")
                    correction_input = st.text_area(
                        "Engineer Correction / Context",
                        placeholder="e.g. This is a deployment regression; restart will not resolve the issue. We must rollback.",
                        height=90,
                        key="correction_input_area"
                    )
                    if st.button("🔄 Re-diagnose with Correction", type="primary"):
                        if not correction_input.strip():
                            st.error("Please enter a correction note.")
                        else:
                            with st.spinner("Re-diagnosing with corrections..."):
                                red_resp = requests.post(
                                    f"{BACKEND_URL}/incidents/{active_incident_id}/rediagnose",
                                    json={"correction": correction_input.strip()},
                                    headers=get_auth_headers(),
                                    timeout=30
                                )
                                if red_resp.status_code == 200:
                                    st.success("Re-diagnosis complete!")
                                    st.rerun()
                                else:
                                    st.error(f"Re-diagnosis failed: {red_resp.text}")

                elif app_state == "APPROVED":
                    st.success(f"Action approved by **{approval_obj.get('decided_by')}** at {approval_obj.get('decided_at')}. Note: _{approval_obj.get('note')}_")
            else:
                st.markdown("---")
                st.success(f"🏆 Incident `{active_incident_id}` is **RESOLVED**. Review execution and verification below.")

            # Real Execution Target Dispatch
            if app_state == "APPROVED" and inc_status in ("APPROVED", "AWAITING_APPROVAL", "FAILED_RETRYING"):
                st.markdown("---")
                st.subheader("3. Real Execution Target Dispatch")
                st.markdown(
                    f"The approval gate has passed with cryptographic binding. "
                    f"Target application: **`{incident_data.get('application_name', 'Production Service')}`**."
                )

                if st.button("🚀 Dispatch Remediation to Real Target", type="primary", use_container_width=True):
                    with st.spinner("Dispatching action to RealExecutionTarget & verifying real application telemetry..."):
                        try:
                            t0 = time.time()
                            exec_resp = requests.post(f"{BACKEND_URL}/incidents/{active_incident_id}/execute", headers=get_auth_headers(), timeout=25.0)
                            dur = time.time() - t0
                            if exec_resp.status_code == 200:
                                st.success(f"Execution dispatched and verified in {dur:.2f}s!")
                                st.rerun()
                            else:
                                st.error(f"Execution request failed ({exec_resp.status_code}): {exec_resp.text}")
                        except Exception as e:
                            st.error(f"Communication error during execution: {e}")

            # Real Verification Results & Controlled State
            verifications = incident_data.get("verifications", [])
            executions = incident_data.get("executions", [])

            if executions:
                st.markdown("---")
                st.subheader("4. Real Execution & Verification Outcome")

                latest_exec = executions[-1]
                exec_status = latest_exec.get("status")
                exec_res = latest_exec.get("execution_result", {})

                if exec_status == "UNAVAILABLE" or exec_res.get("status") == "UNAVAILABLE":
                    st.markdown(f"""
                    <div class="controlled-state-box">
                        <div style="font-size: 1.2rem; font-weight: 700; color: #B45309; margin-bottom: 8px;">
                            ⚠️ Real Execution Capability Not Configured
                        </div>
                        <div style="font-size: 0.95rem; color: #78350F; margin-bottom: 6px;">
                            {exec_res.get('error', 'Action unavailable for this application: Real execution capability not configured')}
                        </div>
                        <div style="font-size: 0.88rem; color: #92400E;">
                            <b>Safety Control:</b> The system refused to fabricate execution success. Incident remains un-resolved awaiting manual engineering intervention or authorized remediation adapter.
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.info(f"Execution Target: `{latest_exec.get('target_type')}` | Execution ID: `{latest_exec.get('execution_id')}` | Duration: {latest_exec.get('duration_ms')}ms")

                if verifications:
                    latest_ver = verifications[-1]
                    ver_status = latest_ver.get("status", "UNCERTAIN")

                    if ver_status == "SUCCESS":
                        v_badge = '<div class="v-badge-success">🟢 VERIFICATION PASSED (RESOLVED)</div>'
                    elif ver_status == "FAILURE":
                        v_badge = '<div class="v-badge-failure">🔴 VERIFICATION FAILED (FAILURE)</div>'
                    else:
                        v_badge = '<div class="v-badge-uncertain">🟡 VERIFICATION UNCERTAIN</div>'

                    st.markdown(f"""
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                        {v_badge}
                        <span style="color: #52657A; font-size: 0.9rem;">
                            Verified against real <code>/health</code> and <code>/logs/recent</code> probes
                        </span>
                    </div>
                    """, unsafe_allow_html=True)

                    b_ev = latest_ver.get("before", {})
                    a_ev = latest_ver.get("after", {})

                    err_rate_val = a_ev.get("error_rate")
                    err_rate_disp = f"{err_rate_val * 100:.2f}%" if isinstance(err_rate_val, (int, float)) else "Metric unavailable"

                    p95_val = a_ev.get("p95_latency_ms")
                    p95_disp = f"{p95_val}ms" if isinstance(p95_val, (int, float)) else "Metric unavailable"

                    pass_ratio_val = a_ev.get("health_pass_ratio")
                    pass_ratio_disp = f"{pass_ratio_val * 100:.0f}%" if isinstance(pass_ratio_val, (int, float)) else "Metric unavailable"

                    svc_status_disp = str(a_ev.get("service_status", "unknown"))

                    c_m1, c_m2, c_m3, c_m4 = st.columns(4)
                    with c_m1:
                        st.metric("Error Rate", err_rate_disp)
                    with c_m2:
                        st.metric("P95 Latency", p95_disp)
                    with c_m3:
                        st.metric("Health Probes Pass Ratio", pass_ratio_disp)
                    with c_m4:
                        st.metric("Service Status", svc_status_disp)

                    st.markdown("#### 📋 5-Criteria Verification Checklist")
                    for crit in latest_ver.get("criteria", []):
                        passed = crit.get("passed", False)
                        icon = "✅" if passed else "❌"
                        color = "#15803D" if passed else "#B91C1C"
                        obs = crit.get("observed")
                        obs_disp = f"{obs}" if obs != "UNAVAILABLE" else "Metric unavailable"
                        st.markdown(
                            f"<span style='color:{color}; font-weight:700;'>{icon} {crit.get('id')}:</span> "
                            f"<b>{crit.get('description')}</b> &nbsp;|&nbsp; "
                            f"Observed: <code>{obs_disp}</code> (Threshold: <code>{crit.get('threshold')}</code>)",
                            unsafe_allow_html=True
                        )

                    if latest_ver.get("reasons"):
                        st.markdown("<b>Verification Factors:</b>", unsafe_allow_html=True)
                        for r in latest_ver.get("reasons", []):
                            st.markdown(f"- {r}")

            # Resolution Outcome & Hindsight Retention
            if inc_status in ("RESOLVED", "ESCALATED"):
                st.markdown("---")
                st.subheader("5. Structured Resolution Outcome & Hindsight Retention")

                try:
                    out_resp = requests.get(f"{BACKEND_URL}/incidents/{active_incident_id}/outcome", headers=get_auth_headers(), timeout=5.0)
                    if out_resp.status_code == 200:
                        outcome_data = out_resp.json()
                        mem_updated = outcome_data.get("memory_updated", False)
                        mem_indicator = "✅ YES (Retained in Hindsight Memory Bank)" if mem_updated else "⚠️ Local Memory Fallback"

                        st.markdown(f"""
                        <div class="outcome-box">
                            <h3 style="margin: 0; color: #15803D;">🏆 Incident Post-Mortem & Resolution Outcome</h3>
                            <div style="margin-top: 12px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px;">
                                <div><b>Final Status:</b> <code>{outcome_data.get('final_status')}</code></div>
                                <div><b>Total Attempts:</b> <code>{outcome_data.get('total_attempts')}</code></div>
                                <div><b>Time to Resolution:</b> <code>{outcome_data.get('time_to_resolution_seconds')}s</code></div>
                            </div>
                            <div style="margin-top: 12px;">
                                <b>Memory Updated in Hindsight:</b> {mem_indicator}
                            </div>
                        </div>
                        """, unsafe_allow_html=True)

                        with st.expander("🛠️ Technical Details / Raw API (JSON)", expanded=False):
                            st.json(outcome_data)
                except Exception as e:
                    st.error(f"Error fetching outcome: {e}")

    # =========================================================================
    # TAB 2: APPLICATION FLEET REGISTRY
    # =========================================================================
    with tab_registry:
        st.subheader("🏢 Monitored Application Registry")
        st.caption("Manage all microservices, API services, and infrastructure components monitored by the Incident Response Agent.")

        if registered_apps:
            table_rows = []
            for a in registered_apps:
                table_rows.append({
                    "Application Name": a.get("application_name", a.get("name")),
                    "Application ID": a.get("application_id"),
                    "Service ID": a.get("service_id") or a.get("service"),
                    "Base URL": a.get("base_url"),
                    "Health Probe": a.get("health_endpoint", "/health"),
                    "Log Method": a.get("log_collection_method", "http"),
                    "Description": a.get("description", "")
                })
            st.dataframe(table_rows, use_container_width=True, hide_index=True)
        else:
            st.info("No applications registered.")

        st.markdown("---")
        st.subheader("➕ Register New Application")
        with st.form("register_app_form"):
            r_c1, r_c2 = st.columns(2)
            with r_c1:
                new_app_name = st.text_input("Application Name", placeholder="e.g. Payment Gateway API")
                new_app_id = st.text_input("Application Slug ID", placeholder="e.g. payment-test-api")
                new_service_id = st.text_input("Service ID", placeholder="e.g. payment-service")
            with r_c2:
                new_base_url = st.text_input("Base URL (including port)", placeholder="e.g. http://127.0.0.1:9001")
                new_health_endpoint = st.text_input("Health Endpoint", value="/health")

            new_desc = st.text_input("Description (Optional)", placeholder="e.g. Real payment test API with live logs and health endpoints")
            submit_app = st.form_submit_button("💾 Save Application Configuration", type="primary")

            if submit_app:
                if not new_app_id or not new_app_name or not new_base_url:
                    st.error("Please fill in Application ID, Name, and Base URL.")
                else:
                    payload = {
                        "application_id": new_app_id.strip().lower(),
                        "application_name": new_app_name.strip(),
                        "service_id": (new_service_id or new_app_id).strip(),
                        "base_url": new_base_url.strip(),
                        "health_endpoint": (new_health_endpoint or "/health").strip(),
                        "log_collection_method": "http",
                        "description": new_desc.strip()
                    }
                    try:
                        r_resp = requests.post(f"{BACKEND_URL}/api/applications", json=payload, headers=get_auth_headers(), timeout=5)
                        if r_resp.status_code in [200, 201]:
                            st.success(f"🎉 Application '{new_app_name}' successfully registered and saved!")
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            st.error(f"Failed to register application ({r_resp.status_code}): {r_resp.text}")
                    except Exception as ex:
                        st.error(f"Backend communication error: {ex}")

    # =========================================================================
    # TAB 3: INCIDENT POST-MORTEM ARCHIVE
    # =========================================================================
    with tab_archive:
        st.subheader("📜 Incident Post-Mortem Archive")
        st.caption("Review all historical canonical incidents, investigation timelines, RCA reports, and verification outcomes.")

        try:
            incidents_resp = requests.get(f"{BACKEND_URL}/incidents", headers=get_auth_headers(), timeout=3.0)
            if incidents_resp.status_code == 200:
                all_incidents = incidents_resp.json()
                if all_incidents:
                    inc_list = []
                    for inc in all_incidents:
                        inc_list.append({
                            "Incident ID": inc.get("incident_id"),
                            "Application": inc.get("application_name", inc.get("application_id", "N/A")),
                            "Service": inc.get("service") or inc.get("service_id", "N/A"),
                            "Severity": str(inc.get("severity", "medium")).upper(),
                            "Status": inc.get("status"),
                            "Target Type": inc.get("execution_target_type", "real_service"),
                            "Created At": inc.get("created_at") or inc.get("timestamp")
                        })
                    st.dataframe(inc_list, use_container_width=True, hide_index=True)

                    st.markdown("#### Select Incident to Inspect Post-Mortem")
                    inc_id_choices = [i.get("incident_id") for i in all_incidents if i.get("incident_id")]
                    chosen_inc_id = st.selectbox("Choose Incident ID", options=inc_id_choices)
                    if chosen_inc_id:
                        chosen_inc = next((i for i in all_incidents if i.get("incident_id") == chosen_inc_id), None)
                        if chosen_inc:
                            c_app = chosen_inc.get("application_name") or chosen_inc.get("application_id") or "N/A"
                            c_svc = chosen_inc.get("service") or chosen_inc.get("service_id") or "N/A"
                            c_status = chosen_inc.get("status", "RESOLVED")
                            c_sev = str(chosen_inc.get("severity", "MEDIUM")).upper()
                            c_diag = chosen_inc.get("diagnosis", "Incident resolved")
                            c_action = chosen_inc.get("recommended_action") or {}
                            c_act_type = c_action.get("type", "remediation_action")

                            st.markdown(f"""
                            <div class="saas-card" style="border-left: 6px solid #2563EB;">
                                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                                    <span style="font-size:1.2rem; font-weight:700; color:#0F2747;">
                                        📄 Incident Post-Mortem: <code>{chosen_inc_id}</code>
                                    </span>
                                    <span class="badge badge-status">{c_status}</span>
                                </div>
                                <div style="display:grid; grid-template-columns:repeat(3, 1fr); gap:12px; margin-bottom:12px;">
                                    <div><b style="color:#52657A;">Application:</b> <span style="color:#0F2747; font-weight:600;">{c_app}</span></div>
                                    <div><b style="color:#52657A;">Service:</b> <code>{c_svc}</code></div>
                                    <div><b style="color:#52657A;">Severity:</b> <span style="color:#B45309; font-weight:700;">{c_sev}</span></div>
                                </div>
                                <div style="margin-bottom:8px;">
                                    <b style="color:#52657A;">Diagnosis / Root Cause:</b>
                                    <div style="color:#0F2747; background:#F1F5F9; padding:8px 12px; border-radius:6px; margin-top:4px; border:1px solid #D9E2EC;">{c_diag}</div>
                                </div>
                                <div>
                                    <b style="color:#52657A;">Remediation Action:</b> <code>{c_act_type}</code> on <code>{c_svc}</code>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)

                            with st.expander("🛠️ Technical Details / Raw API (JSON)", expanded=False):
                                st.json(chosen_inc)
                else:
                    st.info("No recorded incidents found in backend database.")
            else:
                st.warning("Could not fetch incidents list from backend.")
        except Exception as e:
            st.error(f"Failed to fetch incident archive: {e}")
