# Development Startup

## Manual Startup

Run these commands from the repository root in separate terminals. Install the repository dependencies first with `python -m pip install -r requirements.txt` if needed.

1. Start the real 9001 test application:

   ```powershell
   python -m uvicorn test_application.main:app --host 127.0.0.1 --port 9001
   ```

2. Start the Incident Response Agent backend on its existing port. The simulator API is disabled for this real-telemetry configuration:

   ```powershell
   $env:ENABLE_SIMULATOR = "0"; python -m uvicorn main:app --host 127.0.0.1 --port 8001
   ```

3. Start the Streamlit frontend:

   ```powershell
   python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
   ```

The 9001 service health endpoint is `GET http://127.0.0.1:9001/health`; recent real application logs are available at `GET http://127.0.0.1:9001/logs/recent`. The live investigation test triggers its upstream-timeout scenario through `POST http://127.0.0.1:9001/payments`.

## Automatic Startup

From the repository root, run:

```powershell
python scripts/start_dev.py
```

This starts the test application, Agent backend, and frontend. It checks each port before starting and reuses an already-running service only when that service's expected HTTP health endpoint identifies it and reports healthy. If a port is occupied by another service, the launcher reports the exact command, reason, port, and expected endpoint rather than starting a duplicate. Use `python scripts/start_dev.py --no-frontend` to omit Streamlit.

The launcher starts the Agent backend on port 8001 with `ENABLE_SIMULATOR=0`. It does not start a separate simulator process or expose simulator routes in the launcher-owned backend. Processes already running are reused and are not stopped by the launcher. Press Ctrl+C to stop only processes it started.

## Port Map

| Port | Use |
|---|---|
| 9001 | Real local Payment Gateway test application used for HTTP health/log collection and live investigation. |
| 8001 | Incident Response Agent backend. Simulator routes are an optional execution-only development feature and are disabled by the launcher. The existing app combines the backend and simulator routes in one process when `ENABLE_SIMULATOR=1`. |
| 8501 | Streamlit frontend. |
| 8000 | External CogniTrace project. This repository does not use or start it. |

There is no separate simulator process in the current repository. `sim_env.py` provides optional in-process `/sim/*` routes when enabled; it is not part of the launcher's real 9001 detection/investigation path.

## Environment and Initialization

The test application requires no application-specific environment variables. Its FastAPI lifespan initializes the SQLite database and starts its webhook worker. The repository-level `requirements.txt` includes its runtime dependencies as well as the Agent and Streamlit dependencies. SQLite is provided by Python.

`GROQ_API_KEY`, `HINDSIGHT_API_URL`, and `HINDSIGHT_API_KEY` are optional for starting the services. Without external credentials/services, the Agent uses its existing fallback behavior; configure them separately when live LLM or Hindsight services are desired.

## Troubleshooting

- **9001 `ConnectionRefusedError`:** Start the test application with the manual command above or run the automatic launcher. Check `GET http://127.0.0.1:9001/health`; the expected service is `payment-gateway-test`. A merely open TCP port is not sufficient.
- **8001 simulator unavailable:** In the automatic real-telemetry configuration, `/sim/*` routes are intentionally disabled. This does not prevent registry-based health/log collection from 9001. To explicitly run simulator demos, start the backend separately with `$env:ENABLE_SIMULATOR = "1"` and use the simulator-only demo workflows.
- **Backend unavailable:** Check `GET http://127.0.0.1:8001/health`; the expected service is `Incident Response Memory Agent`. The live investigation test currently calls this fixed backend URL.
- **Frontend unavailable:** Check `http://127.0.0.1:8501/_stcore/health` and confirm Streamlit is installed from the repository requirements.
- **Port already occupied:** The launcher reuses a healthy matching service. If the port belongs to another service or its health check fails, stop or reconfigure that service yourself; the launcher will not kill it or spawn a duplicate.
