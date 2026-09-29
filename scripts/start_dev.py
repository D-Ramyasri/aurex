"""Start the real test application, Agent backend, and optional Streamlit UI."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = "127.0.0.1"


@dataclass(frozen=True)
class Service:
    name: str
    port: int
    health_url: str
    command: list[str]
    validate: Callable[[int, str], tuple[bool, str]]
    environment: Optional[dict[str, str]] = None


def _http_get(url: str) -> tuple[Optional[int], str, str]:
    try:
        with urlopen(Request(url, method="GET"), timeout=1.0) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, body, ""
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return error.code, body, str(error)
    except (OSError, URLError) as error:
        return None, "", str(error)


def _port_is_listening(port: int) -> bool:
    try:
        with socket.create_connection((HOST, port), timeout=0.25):
            return True
    except OSError:
        return False


def _json_service(expected_service: str, expected_status: str = "healthy"):
    def validate(status_code: int, body: str) -> tuple[bool, str]:
        try:
            payload = json.loads(body)
        except ValueError:
            return False, f"HTTP {status_code} returned non-JSON health response"
        if payload.get("service") != expected_service:
            return False, f"health response identified service {payload.get('service')!r}, expected {expected_service!r}"
        if status_code != 200 or payload.get("status") != expected_status:
            return False, f"health reports HTTP {status_code}, status={payload.get('status')!r}"
        return True, "healthy"

    return validate


def _streamlit_health(status_code: int, body: str) -> tuple[bool, str]:
    if status_code == 200 and body.strip().lower() == "ok":
        return True, "healthy"
    return False, f"Streamlit health returned HTTP {status_code}: {body[:200]!r}"


def _command_text(command: list[str]) -> str:
    return subprocess.list2cmdline(command) if os.name == "nt" else " ".join(command)


def _service_specs(include_frontend: bool) -> list[Service]:
    python = sys.executable
    specs = [
        Service(
            name="Test application",
            port=9001,
            health_url="http://127.0.0.1:9001/health",
            command=[python, "-m", "uvicorn", "test_application.main:app", "--host", HOST, "--port", "9001"],
            validate=_json_service("payment-gateway-test"),
        ),
        Service(
            name="Backend",
            port=8001,
            health_url="http://127.0.0.1:8001/health",
            command=[python, "-m", "uvicorn", "main:app", "--host", HOST, "--port", "8001"],
            validate=_json_service("Incident Response Memory Agent"),
            environment={"ENABLE_SIMULATOR": "0"},
        ),
    ]
    if include_frontend:
        specs.append(
            Service(
                name="Frontend",
                port=8501,
                health_url="http://127.0.0.1:8501/_stcore/health",
                command=[python, "-m", "streamlit", "run", "app.py", "--server.address", HOST, "--server.port", "8501"],
                validate=_streamlit_health,
                environment={"BACKEND_URL": "http://127.0.0.1:8001"},
            )
        )
    return specs


def _health_check(service: Service) -> tuple[bool, str]:
    status_code, body, error = _http_get(service.health_url)
    if status_code is None:
        return False, error
    healthy, detail = service.validate(status_code, body)
    return healthy, detail


def _raise_startup_error(service: Service, reason: str) -> None:
    raise RuntimeError(
        f"Startup failed for {service.name}.\n"
        f"  Command: { _command_text(service.command) }\n"
        f"  Reason: {reason}\n"
        f"  Port: {service.port}\n"
        f"  Expected endpoint: {service.health_url}"
    )


def _ensure_running(service: Service, children: dict[str, subprocess.Popen]) -> str:
    if _port_is_listening(service.port):
        healthy, detail = _health_check(service)
        if healthy:
            return "RUNNING (reused existing service)"
        _raise_startup_error(
            service,
            f"port is already listening, but the expected service health check failed: {detail}; refusing to launch a duplicate",
        )

    environment = os.environ.copy()
    environment.update(service.environment or {})
    try:
        process = subprocess.Popen(service.command, cwd=ROOT, env=environment)
    except OSError as error:
        _raise_startup_error(service, f"could not launch process: {error}")

    children[service.name] = process
    deadline = time.monotonic() + 30.0
    last_detail = "health endpoint has not responded yet"
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            _raise_startup_error(service, f"process exited with code {exit_code}; last health result: {last_detail}")
        healthy, detail = _health_check(service)
        if healthy:
            return "RUNNING (started by launcher)"
        last_detail = detail
        time.sleep(0.25)

    _raise_startup_error(service, f"timed out waiting 30 seconds; last health result: {last_detail}")


def _stop_owned_processes(children: dict[str, subprocess.Popen]) -> None:
    for process in reversed(list(children.values())):
        if process.poll() is None:
            process.terminate()
    for process in reversed(list(children.values())):
        if process.poll() is None:
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the Incident Response Agent development environment.")
    parser.add_argument("--no-frontend", action="store_true", help="Start only the test application and backend")
    args = parser.parse_args()

    print("=" * 60)
    print("Incident Response Platform - Production Environment")
    print("=" * 60)
    print("Target Application: http://127.0.0.1:9001 (payment-gateway-test)")
    print("Backend API:        http://127.0.0.1:8001")
    print("Frontend UI:        " + ("http://127.0.0.1:8501" if not args.no_frontend else "not requested (--no-frontend)"))
    print("Execution Target:   RealExecutionTarget (Fail-Closed)")
    print("Telemetry Source:   Real HTTP /health + /logs/recent")
    print("=" * 60)
    print()

    children: dict[str, subprocess.Popen] = {}
    try:
        statuses = {}
        for service in _service_specs(include_frontend=not args.no_frontend):
            statuses[service.name] = _ensure_running(service, children)
        for service_name, service_status in statuses.items():
            print(f"[OK] {service_name}: {service_status}")
        print("\nAll systems operational. Press Ctrl+C to stop processes started by this launcher.")

        while True:
            for service_name, process in children.items():
                exit_code = process.poll()
                if exit_code is not None:
                    print(f"{service_name} exited unexpectedly with code {exit_code}.")
                    return 1
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping launcher-owned development processes...")
        return 0
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    finally:
        _stop_owned_processes(children)


if __name__ == "__main__":
    raise SystemExit(main())
