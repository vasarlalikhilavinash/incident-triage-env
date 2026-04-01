from typing import Any, Dict, List, Optional

from pydantic import Field

from openenv.core.env_server.types import Action, Observation, State


AVAILABLE_COMMANDS = [
    "view_queue",
    "inspect",
    "set_severity",
    "set_category",
    "assign_team",
    "add_action_item",
    "submit",
]

SEVERITY_LEVELS = ["P0", "P1", "P2", "P3"]

CATEGORIES = [
    "database",
    "api",
    "infrastructure",
    "security",
    "application",
    "deployment",
    "monitoring",
    "network",
]

TEAMS = [
    "database-team",
    "platform-team",
    "infra-team",
    "security-team",
    "backend-team",
    "frontend-team",
    "devops-team",
    "sre-team",
]


class TriageAction(Action):
    """Action for the incident triage environment."""

    command: str = Field(
        description=(
            "Command to execute. One of: view_queue, inspect, set_severity, "
            "set_category, assign_team, add_action_item, submit"
        )
    )
    incident_id: Optional[str] = Field(
        default=None,
        description="Incident ID (e.g. 'INC-001') for commands targeting a specific incident",
    )
    value: Optional[str] = Field(
        default=None,
        description="Value for set_severity, set_category, assign_team, or add_action_item commands",
    )


class TriageObservation(Observation):
    """Observation returned by the incident triage environment."""

    message: str = Field(default="", description="Human-readable feedback")
    incident_queue: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="Summary list of incidents in the queue"
    )
    current_incident: Optional[Dict[str, Any]] = Field(
        default=None, description="Full details of currently inspected incident"
    )
    triage_decisions: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict, description="Current triage decisions keyed by incident ID"
    )
    available_commands: List[str] = Field(
        default_factory=lambda: list(AVAILABLE_COMMANDS),
        description="Available commands",
    )
    available_categories: List[str] = Field(
        default_factory=lambda: list(CATEGORIES),
        description="Valid incident categories",
    )
    available_teams: List[str] = Field(
        default_factory=lambda: list(TEAMS),
        description="Valid team assignments",
    )
    available_severities: List[str] = Field(
        default_factory=lambda: list(SEVERITY_LEVELS),
        description="Valid severity levels",
    )
    task_id: str = Field(default="", description="Current task identifier")
    step_number: int = Field(default=0, description="Current step number")
    max_steps: int = Field(default=30, description="Maximum allowed steps")


class TriageState(State):
    """Internal state of the incident triage environment."""

    task_id: str = Field(default="")
    incidents_count: int = Field(default=0)
    decisions_made: int = Field(default=0)
    current_score: float = Field(default=0.0)
