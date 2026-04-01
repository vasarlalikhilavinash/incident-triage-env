"""Incident Triage Environment — server-side implementation.

Features:
  - Multi-step investigation: inspect (surface) → diagnose (root cause)
  - Dependency chain identification: link_incidents command
  - Escalation mechanics: untriaged incidents worsen over time
  - 4 difficulty tiers: easy, medium, hard, expert
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4

from openenv.core.env_server.interfaces import Environment
from openenv.core.env_server.types import State

try:
    from ..models import (
        AVAILABLE_COMMANDS,
        CATEGORIES,
        SEVERITY_LEVELS,
        TEAMS,
        TriageAction,
        TriageObservation,
        TriageState,
    )
except ImportError:
    from models import (
        AVAILABLE_COMMANDS,
        CATEGORIES,
        SEVERITY_LEVELS,
        TEAMS,
        TriageAction,
        TriageObservation,
        TriageState,
    )

try:
    from .tasks import TASKS, compute_step_reward, get_escalation_message, grade_task
except ImportError:
    from server.tasks import TASKS, compute_step_reward, get_escalation_message, grade_task


class IncidentTriageEnvironment(
    Environment[TriageAction, TriageObservation, TriageState]
):
    """
    Production Incident Triage Environment.

    An AI agent acts as an on-call SRE triaging production incidents.
    Features multi-step investigation, dependency mapping, and escalation.
    """

    def __init__(self) -> None:
        super().__init__()
        self._task_id: str = "easy"
        self._task: Dict[str, Any] = TASKS["easy"]
        self._incidents: List[Dict[str, Any]] = []
        self._decisions: Dict[str, Dict[str, Any]] = {}
        self._declared_links: Dict[str, str] = {}  # child → parent
        self._diagnosed: Set[str] = set()  # incidents that were diagnosed
        self._step_count: int = 0
        self._done: bool = False
        self._episode_id: str = str(uuid4())
        self._final_score: float = 0.0

    # ------------------------------------------------------------------
    # OpenEnv interface
    # ------------------------------------------------------------------

    def reset(
        self,
        seed: Optional[int] = None,
        episode_id: Optional[str] = None,
        task_id: str = "easy",
        **kwargs: Any,
    ) -> TriageObservation:
        """Reset the environment for a given task."""
        if task_id not in TASKS:
            task_id = "easy"

        self._task_id = task_id
        self._task = TASKS[task_id]
        self._incidents = copy.deepcopy(self._task["incidents"])
        self._decisions = {}
        self._declared_links = {}
        self._diagnosed = set()
        self._step_count = 0
        self._done = False
        self._episode_id = episode_id or str(uuid4())
        self._final_score = 0.0

        # Build feature description based on task difficulty
        features = []
        if task_id in ("hard", "expert"):
            features.append(
                "Use 'diagnose' on incidents to reveal deep root-cause analysis."
            )
            features.append(
                "Use 'link_incidents' to indicate when one incident caused another."
            )
        if task_id == "expert":
            features.append(
                "⚠ Incidents will ESCALATE if not triaged promptly — prioritize wisely!"
            )

        feature_text = "\n".join(features)
        if feature_text:
            feature_text = "\n\n" + feature_text

        return TriageObservation(
            done=False,
            reward=0.0,
            message=(
                f"=== Incident Triage: {self._task['name']} ===\n"
                f"{self._task['description']}\n\n"
                f"You have {len(self._incidents)} incident(s) to triage.\n"
                f"Maximum steps: {self._task['max_steps']}.\n\n"
                f"Available commands: {', '.join(AVAILABLE_COMMANDS)}\n"
                f"Start by running 'view_queue' to see all incidents."
                f"{feature_text}"
            ),
            task_id=self._task_id,
            step_number=0,
            max_steps=self._task["max_steps"],
        )

    def step(
        self,
        action: TriageAction,
        timeout_s: Optional[float] = None,
        **kwargs: Any,
    ) -> TriageObservation:
        """Execute one step in the environment."""
        if self._done:
            return self._make_observation(
                message="Episode is already done. Call reset() to start a new episode.",
                reward=0.0,
                done=True,
            )

        self._step_count += 1

        # Check step limit
        if self._step_count > self._task["max_steps"]:
            self._done = True
            self._final_score = grade_task(
                self._task_id,
                self._decisions,
                self._step_count,
                declared_links=self._declared_links,
                diagnosed_incidents=self._diagnosed,
            )
            return self._make_observation(
                message=(
                    f"Step limit ({self._task['max_steps']}) exceeded. "
                    f"Episode ended. Final score: {self._final_score:.4f}"
                ),
                reward=self._final_score,
                done=True,
            )

        prev_decisions = copy.deepcopy(self._decisions)

        cmd = action.command.lower().strip()
        handler = {
            "view_queue": self._cmd_view_queue,
            "inspect": self._cmd_inspect,
            "diagnose": self._cmd_diagnose,
            "set_severity": self._cmd_set_severity,
            "set_category": self._cmd_set_category,
            "assign_team": self._cmd_assign_team,
            "add_action_item": self._cmd_add_action_item,
            "link_incidents": self._cmd_link_incidents,
            "submit": self._cmd_submit,
        }.get(cmd)

        if handler is None:
            return self._make_observation(
                message=(
                    f"Unknown command: '{action.command}'. "
                    f"Valid commands: {', '.join(AVAILABLE_COMMANDS)}"
                ),
                reward=-0.01,
            )

        obs = handler(action)

        # Add incremental reward for decision-making steps
        if cmd not in ("view_queue", "inspect", "diagnose", "submit", "link_incidents"):
            step_reward = compute_step_reward(
                self._task_id, self._decisions, prev_decisions
            )
            obs.reward = (obs.reward or 0.0) + step_reward

        # Check for escalation warnings (hard/expert only)
        if self._task_id in ("hard", "expert") and not self._done:
            esc_msg = get_escalation_message(
                self._incidents, self._decisions, self._step_count
            )
            if esc_msg:
                obs.message = obs.message + "\n\n" + esc_msg

        return obs

    @property
    def state(self) -> TriageState:
        """Return current environment state."""
        decisions_made = sum(
            1
            for d in self._decisions.values()
            if d.get("severity") and d.get("category") and d.get("team")
        )
        return TriageState(
            episode_id=self._episode_id,
            step_count=self._step_count,
            task_id=self._task_id,
            incidents_count=len(self._incidents),
            decisions_made=decisions_made,
            current_score=self._final_score,
        )

    def close(self) -> None:
        """Clean up resources."""
        pass

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _cmd_view_queue(self, action: TriageAction) -> TriageObservation:
        queue_summary = []
        for inc in self._incidents:
            dec = self._decisions.get(inc["id"], {})
            status_parts = []
            if dec.get("severity"):
                status_parts.append(f"severity={dec['severity']}")
            if dec.get("category"):
                status_parts.append(f"category={dec['category']}")
            if dec.get("team"):
                status_parts.append(f"team={dec['team']}")
            status = ", ".join(status_parts) if status_parts else "not triaged"

            queue_summary.append(
                {
                    "id": inc["id"],
                    "title": inc["title"],
                    "summary": inc["summary"],
                    "triage_status": status,
                }
            )

        lines = ["=== Incident Queue ==="]
        for item in queue_summary:
            lines.append(
                f"\n[{item['id']}] {item['title']}\n"
                f"  Summary: {item['summary']}\n"
                f"  Triage: {item['triage_status']}"
            )

        # Show dependency links if any declared
        if self._declared_links:
            lines.append("\n--- Declared Dependencies ---")
            for child, parent in sorted(self._declared_links.items()):
                lines.append(f"  {child} → caused by {parent}")

        lines.append(
            f"\nUse 'inspect' with an incident_id to view details, "
            f"or 'diagnose' for deep root-cause analysis."
        )

        return self._make_observation(
            message="\n".join(lines),
            incident_queue=queue_summary,
            reward=0.0,
        )

    def _cmd_inspect(self, action: TriageAction) -> TriageObservation:
        inc_id = action.incident_id
        if not inc_id:
            return self._make_observation(
                message="Error: 'inspect' requires an incident_id. Example: incident_id='INC-001'",
                reward=-0.01,
            )

        incident = self._find_incident(inc_id)
        if incident is None:
            valid_ids = [i["id"] for i in self._incidents]
            return self._make_observation(
                message=f"Error: Incident '{inc_id}' not found. Valid IDs: {', '.join(valid_ids)}",
                reward=-0.01,
            )

        details = incident["details"]
        lines = [
            f"=== Incident Details: {incident['id']} ===",
            f"Title: {incident['title']}",
            f"Summary: {incident['summary']}",
            f"",
            f"Alert Source: {details['alert_source']}",
            f"Triggered At: {details['triggered_at']}",
            f"Service: {details['service']}",
            f"Environment: {details['environment']}",
            f"",
            "--- Logs ---",
        ]
        for log_line in details["logs"]:
            lines.append(f"  {log_line}")

        lines.append("\n--- Metrics ---")
        for k, v in details["metrics"].items():
            lines.append(f"  {k}: {v}")

        lines.append(f"\n--- Recent Changes ---")
        lines.append(f"  {details['recent_changes']}")

        dec = self._decisions.get(inc_id, {})
        if dec:
            lines.append(f"\n--- Current Triage Decisions ---")
            for k, v in dec.items():
                lines.append(f"  {k}: {v}")

        # Hint about diagnose for complex tasks
        if self._task_id in ("hard", "expert"):
            lines.append(
                f"\n💡 Tip: Use 'diagnose' on {inc_id} for deeper root-cause analysis."
            )

        return self._make_observation(
            message="\n".join(lines),
            current_incident=incident,
            reward=0.0,
        )

    def _cmd_diagnose(self, action: TriageAction) -> TriageObservation:
        """Deep diagnosis — reveals root cause analysis not visible in inspect."""
        inc_id = action.incident_id
        if not inc_id:
            return self._make_observation(
                message="Error: 'diagnose' requires an incident_id. Example: incident_id='INC-001'",
                reward=-0.01,
            )

        incident = self._find_incident(inc_id)
        if incident is None:
            valid_ids = [i["id"] for i in self._incidents]
            return self._make_observation(
                message=f"Error: Incident '{inc_id}' not found. Valid IDs: {', '.join(valid_ids)}",
                reward=-0.01,
            )

        # Mark as diagnosed
        self._diagnosed.add(inc_id)

        diagnostics = incident.get("diagnostics", {})
        lines = [
            f"=== Deep Diagnosis: {incident['id']} ===",
            f"Title: {incident['title']}",
            "",
            "--- Deep Investigation Logs ---",
        ]

        deep_logs = diagnostics.get("deep_logs", [])
        if deep_logs:
            for log_line in deep_logs:
                lines.append(f"  {log_line}")
        else:
            lines.append("  (No additional logs available)")

        rca = diagnostics.get("root_cause_analysis", "")
        if rca:
            lines.append("\n--- Root Cause Analysis ---")
            lines.append(f"  {rca}")

        heap = diagnostics.get("heap_dump")
        if heap:
            lines.append(f"\n--- Heap Analysis ---")
            lines.append(f"  {heap}")

        net = diagnostics.get("network_trace")
        if net:
            lines.append(f"\n--- Network Trace ---")
            lines.append(f"  {net}")

        # Check for dependency hints
        caused_by = incident.get("caused_by")
        if caused_by:
            lines.append(
                f"\n⚠ DEPENDENCY DETECTED: This incident appears to be caused by {caused_by}. "
                f"Use 'link_incidents' to record this relationship."
            )

        return self._make_observation(
            message="\n".join(lines),
            current_incident=incident,
            reward=0.0,
        )

    def _cmd_link_incidents(self, action: TriageAction) -> TriageObservation:
        """Link a symptom incident to its root cause."""
        child_id = action.incident_id
        parent_id = action.target_id

        if not child_id or not parent_id:
            return self._make_observation(
                message=(
                    "Error: 'link_incidents' requires incident_id (the symptom) "
                    "and target_id (the root cause). "
                    "Example: {\"command\": \"link_incidents\", \"incident_id\": \"INC-009\", \"target_id\": \"INC-006\"}"
                ),
                reward=-0.01,
            )

        if self._find_incident(child_id) is None:
            return self._make_observation(
                message=f"Error: Incident '{child_id}' not found.",
                reward=-0.01,
            )
        if self._find_incident(parent_id) is None:
            return self._make_observation(
                message=f"Error: Incident '{parent_id}' not found.",
                reward=-0.01,
            )
        if child_id == parent_id:
            return self._make_observation(
                message="Error: Cannot link an incident to itself.",
                reward=-0.01,
            )

        self._declared_links[child_id] = parent_id

        # Check if the link is correct for immediate feedback
        expected_deps = self._task.get("expected_deps", {})
        if expected_deps.get(child_id) == parent_id:
            return self._make_observation(
                message=(
                    f"✓ Dependency recorded: {child_id} is caused by {parent_id}. "
                    f"This looks correct — the incidents are related."
                ),
                reward=0.03,
            )
        else:
            return self._make_observation(
                message=(
                    f"Dependency recorded: {child_id} → caused by {parent_id}. "
                    f"Noted, but verify this relationship carefully."
                ),
                reward=0.0,
            )

    def _cmd_set_severity(self, action: TriageAction) -> TriageObservation:
        inc_id = action.incident_id
        value = (action.value or "").upper().strip()

        if not inc_id:
            return self._make_observation(
                message="Error: 'set_severity' requires incident_id.",
                reward=-0.01,
            )
        if self._find_incident(inc_id) is None:
            return self._make_observation(
                message=f"Error: Incident '{inc_id}' not found.",
                reward=-0.01,
            )
        if value not in SEVERITY_LEVELS:
            return self._make_observation(
                message=f"Error: Invalid severity '{value}'. Must be one of: {', '.join(SEVERITY_LEVELS)}",
                reward=-0.01,
            )

        self._ensure_decision(inc_id)
        self._decisions[inc_id]["severity"] = value
        return self._make_observation(
            message=f"Set severity of {inc_id} to {value}.",
            reward=0.0,
        )

    def _cmd_set_category(self, action: TriageAction) -> TriageObservation:
        inc_id = action.incident_id
        value = (action.value or "").lower().strip()

        if not inc_id:
            return self._make_observation(
                message="Error: 'set_category' requires incident_id.",
                reward=-0.01,
            )
        if self._find_incident(inc_id) is None:
            return self._make_observation(
                message=f"Error: Incident '{inc_id}' not found.",
                reward=-0.01,
            )
        if value not in CATEGORIES:
            return self._make_observation(
                message=f"Error: Invalid category '{value}'. Must be one of: {', '.join(CATEGORIES)}",
                reward=-0.01,
            )

        self._ensure_decision(inc_id)
        self._decisions[inc_id]["category"] = value
        return self._make_observation(
            message=f"Set category of {inc_id} to {value}.",
            reward=0.0,
        )

    def _cmd_assign_team(self, action: TriageAction) -> TriageObservation:
        inc_id = action.incident_id
        value = (action.value or "").lower().strip()

        if not inc_id:
            return self._make_observation(
                message="Error: 'assign_team' requires incident_id.",
                reward=-0.01,
            )
        if self._find_incident(inc_id) is None:
            return self._make_observation(
                message=f"Error: Incident '{inc_id}' not found.",
                reward=-0.01,
            )
        if value not in TEAMS:
            return self._make_observation(
                message=f"Error: Invalid team '{value}'. Must be one of: {', '.join(TEAMS)}",
                reward=-0.01,
            )

        self._ensure_decision(inc_id)
        self._decisions[inc_id]["team"] = value
        return self._make_observation(
            message=f"Assigned {inc_id} to {value}.",
            reward=0.0,
        )

    def _cmd_add_action_item(self, action: TriageAction) -> TriageObservation:
        inc_id = action.incident_id
        value = (action.value or "").strip()

        if not inc_id:
            return self._make_observation(
                message="Error: 'add_action_item' requires incident_id.",
                reward=-0.01,
            )
        if self._find_incident(inc_id) is None:
            return self._make_observation(
                message=f"Error: Incident '{inc_id}' not found.",
                reward=-0.01,
            )
        if not value:
            return self._make_observation(
                message="Error: 'add_action_item' requires a non-empty value.",
                reward=-0.01,
            )

        self._ensure_decision(inc_id)
        items = self._decisions[inc_id].setdefault("action_items", [])
        if len(items) >= 5:
            return self._make_observation(
                message=f"Error: Maximum 5 action items per incident. {inc_id} already has {len(items)}.",
                reward=-0.01,
            )
        items.append(value)
        return self._make_observation(
            message=f"Added action item to {inc_id}: '{value}'",
            reward=0.0,
        )

    def _cmd_submit(self, action: TriageAction) -> TriageObservation:
        # Check completeness
        missing = []
        for inc in self._incidents:
            dec = self._decisions.get(inc["id"], {})
            fields_missing = []
            if not dec.get("severity"):
                fields_missing.append("severity")
            if not dec.get("category"):
                fields_missing.append("category")
            if not dec.get("team"):
                fields_missing.append("team")
            if fields_missing:
                missing.append(f"  {inc['id']}: missing {', '.join(fields_missing)}")

        if missing:
            return self._make_observation(
                message=(
                    "Cannot submit — incomplete triage decisions:\n"
                    + "\n".join(missing)
                    + "\n\nEach incident needs severity, category, and team set before submission."
                ),
                reward=-0.02,
            )

        self._done = True
        self._final_score = grade_task(
            self._task_id,
            self._decisions,
            self._step_count,
            declared_links=self._declared_links,
            diagnosed_incidents=self._diagnosed,
        )

        # Build summary
        lines = ["=== Triage Submitted ===", ""]
        for inc in self._incidents:
            dec = self._decisions.get(inc["id"], {})
            lines.append(f"[{inc['id']}] {inc['title']}")
            lines.append(f"  Severity: {dec.get('severity', '—')}")
            lines.append(f"  Category: {dec.get('category', '—')}")
            lines.append(f"  Team: {dec.get('team', '—')}")
            items = dec.get("action_items", [])
            if items:
                lines.append(f"  Action Items:")
                for item in items:
                    lines.append(f"    - {item}")
            lines.append("")

        # Show dependency identification
        if self._declared_links:
            lines.append("--- Dependency Links Identified ---")
            expected_deps = self._task.get("expected_deps", {})
            for child, parent in sorted(self._declared_links.items()):
                correct = expected_deps.get(child) == parent
                mark = "✓" if correct else "✗"
                lines.append(f"  {mark} {child} → caused by {parent}")
            lines.append("")

        # Show diagnosis coverage
        if self._diagnosed:
            lines.append(f"--- Diagnosed Incidents: {', '.join(sorted(self._diagnosed))} ---")
            lines.append("")

        lines.append(f"Final Score: {self._final_score:.4f}")
        lines.append(f"Steps Taken: {self._step_count}")

        return self._make_observation(
            message="\n".join(lines),
            reward=self._final_score,
            done=True,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_incident(self, inc_id: str) -> Optional[Dict[str, Any]]:
        for inc in self._incidents:
            if inc["id"] == inc_id:
                return inc
        return None

    def _ensure_decision(self, inc_id: str) -> None:
        if inc_id not in self._decisions:
            self._decisions[inc_id] = {}

    def _make_observation(
        self,
        message: str,
        reward: float = 0.0,
        done: bool = False,
        incident_queue: Optional[List[Dict[str, Any]]] = None,
        current_incident: Optional[Dict[str, Any]] = None,
    ) -> TriageObservation:
        if done:
            self._done = True
        return TriageObservation(
            done=self._done,
            reward=reward,
            message=message,
            incident_queue=incident_queue,
            current_incident=current_incident,
            triage_decisions=copy.deepcopy(self._decisions),
            task_id=self._task_id,
            step_number=self._step_count,
            max_steps=self._task["max_steps"],
        )
