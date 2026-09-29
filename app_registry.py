"""
Application Registry for Monitored Microservices.
Provides persistent storage, dynamic registration, lookup, and configuration management
for inspectable applications.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from models import ApplicationConfig

logger = logging.getLogger("app_registry")

WORKSPACE_ROOT = Path(__file__).resolve().parent
STORE_FILE = WORKSPACE_ROOT / "applications_store.json"

# Default seed applications
_DEFAULT_APPLICATIONS: List[ApplicationConfig] = [
    ApplicationConfig(
        application_id="payment-api",
        application_name="Payment Gateway API",
        service_id="payment-gateway",
        base_url="http://127.0.0.1:8001",
        health_endpoint="/health",
        description="Core credit card and Stripe webhook checkout processing service"
    ),
    ApplicationConfig(
        application_id="order-api",
        application_name="Order Processing API",
        service_id="order-service",
        base_url="http://127.0.0.1:9002",
        health_endpoint="/health",
        description="Order fulfillment and status dispatch service"
    ),
    ApplicationConfig(
        application_id="auth-service",
        application_name="Auth & Session Service",
        service_id="auth-service",
        base_url="http://127.0.0.1:8002",
        health_endpoint="/health",
        description="User identity, JWT token issuance, and session revocation store"
    ),
    ApplicationConfig(
        application_id="inventory-api",
        application_name="Inventory & Catalog API",
        service_id="inventory-service",
        base_url="http://127.0.0.1:8003",
        health_endpoint="/health",
        description="Product SKU inventory tracking and checkout reservation service"
    ),
    ApplicationConfig(
        application_id="user-api",
        application_name="User Profile Service",
        service_id="user-profile",
        base_url="http://127.0.0.1:9003",
        health_endpoint="/health",
        description="User avatar and account profile management service"
    ),
    ApplicationConfig(
        application_id="payment-test-api",
        application_name="Payment Gateway Test API",
        service_id="payment-gateway-test",
        base_url="http://127.0.0.1:9001",
        health_endpoint="/health",
        remediation_endpoint="/remediation",
        metrics_endpoint="/metrics",
        description="Standalone test target running on port 9001 for manual inspection testing"
    ),
    ApplicationConfig(
        application_id="unreachable-service",
        application_name="Legacy Billing Server (Unreachable Test)",
        service_id="billing-legacy",
        base_url="http://127.0.0.1:9999",
        health_endpoint="/health",
        description="Offline service node used to verify unreachable connection handling"
    ),
    ApplicationConfig(
        application_id="brand-new-zero-memory",
        application_name="Quantum Ingestion Worker (Brand New - Zero Memory)",
        service_id="quantum-worker-zero-history",
        base_url="http://127.0.0.1:9997",
        health_endpoint="/health",
        description="A completely new application with zero incident history in Hindsight"
    )
]

# Internal in-memory registry map
_REGISTRY: Dict[str, ApplicationConfig] = {}


def _save_to_store() -> None:
    """Persists the in-memory registry to applications_store.json."""
    try:
        data = [app.model_dump() for app in _REGISTRY.values()]
        with open(STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error("Failed to save application configurations to store: %s", e)


def _load_from_store() -> None:
    """Loads application configurations from applications_store.json or seeds defaults."""
    global _REGISTRY
    if STORE_FILE.exists():
        try:
            with open(STORE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                _REGISTRY = {
                    item["application_id"].strip().lower(): ApplicationConfig(**item)
                    for item in data
                }
                logger.info("Loaded %d applications from persistent store %s", len(_REGISTRY), STORE_FILE.name)
                if any("log_source" in item or "log_collection_method" not in item for item in data):
                    _save_to_store()
                return
        except Exception as e:
            logger.warning("Could not read applications store (%s). Re-seeding defaults.", e)

    # Seed defaults
    _REGISTRY = {app.application_id.strip().lower(): app for app in _DEFAULT_APPLICATIONS}
    _save_to_store()


# Initial load on module import
_load_from_store()


def list_applications(user_id: Optional[int] = None) -> List[ApplicationConfig]:
    """Returns application configurations scoped to the authenticated user."""
    if not _REGISTRY:
        _load_from_store()
    apps = list(_REGISTRY.values())
    if user_id is not None:
        return [app for app in apps if (getattr(app, "user_id", 2) or 2) == int(user_id)]
    return apps


def get_application(application_id: str, user_id: Optional[int] = None) -> Optional[ApplicationConfig]:
    """Retrieves an application configuration by its unique ID, verifying ownership if user_id is specified."""
    if not _REGISTRY:
        _load_from_store()
    app = _REGISTRY.get(application_id.strip().lower())
    if app and user_id is not None:
        app_user = getattr(app, "user_id", 2) or 2
        if int(app_user) != int(user_id):
            return None
    return app


def register_application(config: ApplicationConfig, user_id: Optional[int] = None) -> ApplicationConfig:
    """Registers or updates an application configuration and persists to storage."""
    key = config.application_id.strip().lower()
    if user_id is not None:
        config.user_id = int(user_id)
    elif config.user_id is None:
        config.user_id = 2
    _REGISTRY[key] = config
    _save_to_store()
    logger.info("Registered application '%s' (%s) for user_id=%s -> Base URL: %s", config.application_name, key, config.user_id, config.base_url)
    return config


def delete_application(application_id: str, user_id: Optional[int] = None) -> bool:
    """Deletes an application configuration from registry and store."""
    key = application_id.strip().lower()
    if key in _REGISTRY:
        if user_id is not None:
            app_user = getattr(_REGISTRY[key], "user_id", 2) or 2
            if int(app_user) != int(user_id):
                return False
        del _REGISTRY[key]
        _save_to_store()
        return True
    return False


def reset_registry() -> None:
    """Resets the registry back to default applications (useful for test isolation)."""
    global _REGISTRY
    _REGISTRY = {app.application_id.strip().lower(): app for app in _DEFAULT_APPLICATIONS}
    _save_to_store()
