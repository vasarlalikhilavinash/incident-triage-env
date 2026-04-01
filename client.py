"""Client for the Incident Triage Environment."""

from openenv.core import EnvClient
from openenv.core.client_types import StepResult
from openenv.core.env_server.types import State

try:
    from .models import TriageAction, TriageObservation, TriageState
except ImportError:
    from models import TriageAction, TriageObservation, TriageState

from typing import Any, Dict


class TriageEnvClient(EnvClient[TriageAction, TriageObservation, TriageState]):
    """Client for connecting to a remote Incident Triage Environment."""

    def _step_payload(self, action: TriageAction) -> dict:
        return action.model_dump(exclude_none=True)

    def _parse_result(self, payload: dict) -> StepResult[TriageObservation]:
        obs_data = payload.get("observation", payload)
        return StepResult(
            observation=TriageObservation(**obs_data),
            reward=payload.get("reward"),
            done=payload.get("done", False),
        )

    def _parse_state(self, payload: dict) -> TriageState:
        return TriageState(**payload)
