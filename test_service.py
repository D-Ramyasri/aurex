"""
Standalone Test Microservice for Testing Incident Agent Ingestion.
Runs a lightweight FastAPI service on a configurable port with configurable health status.

Usage:
    python test_service.py --port 9002 --status 200
    python test_service.py --port 9002 --status 404
    python test_service.py --port 9002 --status 503
"""

import argparse
import uvicorn
from fastapi import FastAPI, Response

def create_app(status_code: int = 200, service_name: str = "test-service", error_msg: str = ""):
    app = FastAPI(title=f"Test Service ({service_name})")

    @app.get("/health")
    def health_endpoint(response: Response):
        response.status_code = status_code
        if status_code == 200:
            return {
                "status": "healthy",
                "service": service_name,
                "uptime_seconds": 1240,
                "database_connected": True
            }
        elif status_code == 404:
            return {"detail": "Not Found"}
        elif status_code == 503:
            return {
                "status": "unhealthy",
                "service": service_name,
                "error": error_msg or "Service Unavailable: connection pool exhausted",
                "active_workers": 0
            }
        else:
            return {
                "status": "error",
                "service": service_name,
                "code": status_code,
                "error": error_msg or f"HTTP {status_code} Error"
            }

    @app.get("/healthz")
    def healthz_endpoint(response: Response):
        return health_endpoint(response)

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Microservice for Incident Response Agent")
    parser.add_argument("--port", type=int, default=9002, help="Port to listen on (e.g. 9002)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface")
    parser.add_argument("--status", type=int, default=200, help="HTTP status code for /health (e.g. 200, 404, 503)")
    parser.add_argument("--service", type=str, default="order-service", help="Service name identifier")
    parser.add_argument("--error", type=str, default="", help="Error message if unhealthy")

    args = parser.parse_args()
    print(f"Starting test microservice '{args.service}' on http://{args.host}:{args.port}/health (status: {args.status})...")
    test_app = create_app(status_code=args.status, service_name=args.service, error_msg=args.error)
    uvicorn.run(test_app, host=args.host, port=args.port, log_level="info")
