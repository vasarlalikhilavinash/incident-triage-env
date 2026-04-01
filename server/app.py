"""
FastAPI application for the Incident Triage Environment.

Endpoints:
    - POST /reset: Reset the environment
    - POST /step: Execute an action
    - GET /state: Get current environment state
    - GET /schema: Get action/observation schemas
    - WS /ws: WebSocket endpoint for persistent sessions

Usage:
    uvicorn server.app:app --reload --host 0.0.0.0 --port 8000
"""

try:
    from openenv.core.env_server.http_server import create_app
except Exception as e:
    raise ImportError(
        "openenv is required. Install with: uv sync"
    ) from e

try:
    from ..models import TriageAction, TriageObservation
    from .incident_triage_env_environment import IncidentTriageEnvironment
except ImportError:
    from models import TriageAction, TriageObservation
    from server.incident_triage_env_environment import IncidentTriageEnvironment


app = create_app(
    IncidentTriageEnvironment,
    TriageAction,
    TriageObservation,
    env_name="incident_triage_env",
    max_concurrent_envs=1,
)


def main(host: str = "0.0.0.0", port: int = 8000):
    """Entry point for: uv run --project . server"""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    main(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
