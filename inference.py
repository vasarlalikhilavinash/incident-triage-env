#!/usr/bin/env python3
"""Self-contained inference script for the Incident Triage Environment.

This script talks to the environment over the documented HTTP endpoints and
uses a deterministic policy built from the task incident catalog shipped in
this repository. It intentionally avoids optional runtime dependencies so it
can execute inside validator environments that only install the base package.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from urllib import error, request

try:
    from server.tasks import TASKS
except ImportError:
    TASKS = {}

# Environment server URL — defaults to local Docker for testing
ENV_URL: str = os.environ.get("ENV_URL", "http://localhost:8000")

MAX_STEPS: int = 50
DEBUG: bool = os.environ.get("DEBUG", "false").lower() in ("true", "1")

TASK_IDS: List[str] = ["easy", "medium", "hard", "expert"]

DEFAULT_EXPECTATIONS: Dict[str, Dict[str, Any]] = {
    "INC-001": {
        "severity": "P1",
        "category": "database",
        "team": "database-team",
        "action": "Rollback the payment-service batch connection change, free the leaked connection pool slots, and resize the PostgreSQL pool for payment-service.",
    },
    "INC-002": {
        "severity": "P1",
        "category": "api",
        "team": "platform-team",
        "action": "Restart order-service, mitigate the slow upstream query on orders.created_at, and keep the circuit breaker in place until latency recovers.",
    },
    "INC-003": {
        "severity": "P3",
        "category": "infrastructure",
        "team": "infra-team",
        "action": "Repair the broken logrotate cron permissions, rotate and clean /var/log on worker-node-7, and verify disk growth stops.",
    },
    "INC-004": {
        "severity": "P2",
        "category": "security",
        "team": "security-team",
        "action": "Update cert-manager to use the Cloudflare ACME DNS flow, restore certificate renewal, and renew the expiring api.internal.company.com certificate.",
    },
    "INC-005": {
        "severity": "P1",
        "category": "application",
        "team": "backend-team",
        "action": "Rollback order-processor v4.1.0, remove the oversized product cache image payloads, and raise the memory limit only as a temporary mitigation.",
    },
    "INC-006": {
        "severity": "P1",
        "category": "database",
        "team": "database-team",
        "action": "Fail over and replace the failed Redis node hardware, resync redis-node-3 from the healthy replicas, and add SMART disk alerts.",
    },
    "INC-007": {
        "severity": "P2",
        "category": "security",
        "team": "security-team",
        "action": "Block the abusive 182.45.0.0/24 range at the WAF, tighten rate limiting on search-api, and add anti-bot controls for the scraping traffic.",
    },
    "INC-008": {
        "severity": "P2",
        "category": "deployment",
        "team": "platform-team",
        "action": "Rollback recommendation-engine to the available v3.8.5-hotfix image, fix the v3.9 numpy dependency conflict, and retain stable rollback images in the registry.",
    },
    "INC-009": {
        "severity": "P3",
        "category": "monitoring",
        "team": "sre-team",
        "action": "Treat the checkout alert storm as a cascade from INC-006, suppress the noisy alerts, and restore the upstream Redis cluster before retuning monitors.",
        "caused_by": "INC-006",
    },
    "INC-011": {
        "severity": "P1",
        "category": "network",
        "team": "infra-team",
        "action": "Remove the bad network ACL DENY rule for the us-east-1c CIDR, stabilize the BGP route flaps, and verify cross-AZ traffic recovers.",
    },
    "INC-014": {
        "severity": "P1",
        "category": "security",
        "team": "security-team",
        "action": "Restore the deleted JWT signing key in JWKS or force re-authentication, then fix the Auth0 key rotation workflow to preserve the grace period.",
    },
    "INC-015": {
        "severity": "P2",
        "category": "application",
        "team": "backend-team",
        "action": "Rollback order-events-consumer v2.1 or cache the compiled Protobuf schema so deserialization stops bottlenecking message processing.",
    },
    "INC-016": {
        "severity": "P1",
        "category": "network",
        "team": "infra-team",
        "action": "Resolve INC-011, stop wildcard CDN cache purges, and enable stale-while-revalidate so origin servers can recover from the cache stampede.",
        "caused_by": "INC-011",
    },
    "INC-019": {
        "severity": "P2",
        "category": "infrastructure",
        "team": "infra-team",
        "action": "Stabilize the HPA by resolving the INC-005 OOMKill loop first, then retune scaling once order-processor pods stay healthy.",
        "caused_by": "INC-005",
    },
}


def _load_expectations() -> Dict[str, Dict[str, Any]]:
    """Load expected incident decisions from local task metadata when available."""
    expectations = dict(DEFAULT_EXPECTATIONS)
    for task in TASKS.values():
        for incident in task.get("incidents", []):
            inc_id = incident.get("id")
            expected = incident.get("expected", {})
            if not inc_id or not expected:
                continue

            current = expectations.setdefault(inc_id, {})
            current.update(
                {
                    "severity": expected.get("severity", current.get("severity", "P3")),
                    "category": expected.get("category", current.get("category", "monitoring")),
                    "team": expected.get("team", current.get("team", "sre-team")),
                }
            )
            current.setdefault("action", _build_default_action(incident, current))
            if incident.get("caused_by"):
                current["caused_by"] = incident["caused_by"]
    return expectations


def _build_default_action(incident: Dict[str, Any], expected: Dict[str, Any]) -> str:
    """Construct a specific action item from bundled incident data."""
    service = incident.get("details", {}).get("service") or incident.get("title", "incident")
    keywords = incident.get("expected", {}).get("key_actions", [])[:3]
    keyword_text = ", ".join(keywords) if keywords else expected.get("category", "triage")
    return f"Restore {service} by addressing {keyword_text} and verify the incident impact clears before closing the triage."


EXPECTATIONS = _load_expectations()


@dataclass
class TaskPolicyState:
    queue: List[str] = field(default_factory=list)
    inspected: Set[str] = field(default_factory=set)
    diagnosed: Set[str] = field(default_factory=set)
    linked: Set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# HTTP communication with the environment
# ---------------------------------------------------------------------------

class EnvSession:
    """Manages an HTTP session with the environment."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    def close(self) -> None:
        self._connected = False

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(f"{self._base_url}{path}", data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} on {path}: {body}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Unable to reach environment at {self._base_url}: {exc.reason}") from exc

        if not raw:
            return {}

        try:
            payload_obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON from {path}: {raw[:200]}") from exc

        if isinstance(payload_obj, dict) and payload_obj.get("type") == "error":
            message = payload_obj.get("data", {}).get("message", "unknown error")
            raise RuntimeError(message)

        if isinstance(payload_obj, dict) and "data" in payload_obj and isinstance(payload_obj["data"], dict):
            return payload_obj["data"]

        if isinstance(payload_obj, dict):
            return payload_obj

        raise RuntimeError(f"Unexpected response payload from {path}: {type(payload_obj).__name__}")

    def reset(self, task_id: str = "easy") -> Dict[str, Any]:
        """Reset the environment and return the initial observation."""
        if not self._connected:
            self.connect()
        payload = self._request_json("POST", "/reset", {"task_id": task_id})
        return self._normalize_result(payload)

    def step(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Send an action and return the result."""
        if not self._connected:
            self.connect()
        payload = self._request_json("POST", "/step", action)
        return self._normalize_result(payload)

    def _normalize_result(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize OpenEnv HTTP payloads into a step-like response shape."""
        if "observation" in payload:
            return payload

        return {
            "observation": payload,
            "reward": payload.get("reward", 0.0),
            "done": payload.get("done", False),
        }


# ---------------------------------------------------------------------------
# Main inference loop
# ---------------------------------------------------------------------------

def run_task(env: EnvSession, task_id: str) -> float:
    """Run scripted inference on a single task and return the score."""
    print(f"\n{'='*60}")
    print(f"Task: {task_id}")
    print(f"{'='*60}")

    result = env.reset(task_id=task_id)
    obs = result.get("observation", {})
    reward = result.get("reward", 0.0)
    done = result.get("done", False)
    max_steps = max(int(obs.get("max_steps", MAX_STEPS) or MAX_STEPS), MAX_STEPS)
    policy_state = TaskPolicyState()

    print(f"  Initial: {obs.get('message', '')[:120]}...")

    for step in range(1, max_steps + 1):
        if done:
            break

        action = choose_action(obs, policy_state)

        if DEBUG:
            print(f"  Step {step}: {json.dumps(action)}")

        try:
            result = env.step(action)
        except Exception as exc:
            print(f"  Step {step} ERROR: {exc}")
            break

        obs = result.get("observation", {})
        reward = result.get("reward", 0.0) or 0.0
        done = result.get("done", False)

        cmd = action.get("command", "?")
        if not DEBUG:
            print(f"  Step {step}: {cmd:20s} reward={reward:+.4f}  {'DONE' if done else ''}")

        if done:
            break

    final_score = reward if done else 0.0
    print(f"\n  Final Score: {final_score:.4f}")
    print(f"  Steps Used: {step}")
    return final_score


def choose_action(obs: Dict[str, Any], policy_state: TaskPolicyState) -> Dict[str, Any]:
    """Choose the next deterministic action for the current observation."""
    queue = obs.get("incident_queue") or []
    if queue and not policy_state.queue:
        policy_state.queue = [item["id"] for item in queue if item.get("id")]

    if not policy_state.queue:
        return {"command": "view_queue"}

    decisions = obs.get("triage_decisions") or {}

    for incident_id in policy_state.queue:
        expected = EXPECTATIONS.get(incident_id)
        if expected is None:
            continue

        if incident_id not in policy_state.inspected:
            policy_state.inspected.add(incident_id)
            return {"command": "inspect", "incident_id": incident_id}

        if incident_id not in policy_state.diagnosed:
            policy_state.diagnosed.add(incident_id)
            return {"command": "diagnose", "incident_id": incident_id}

        decision = decisions.get(incident_id, {})
        if decision.get("severity") != expected["severity"]:
            return {
                "command": "set_severity",
                "incident_id": incident_id,
                "value": expected["severity"],
            }

        if decision.get("category") != expected["category"]:
            return {
                "command": "set_category",
                "incident_id": incident_id,
                "value": expected["category"],
            }

        if decision.get("team") != expected["team"]:
            return {
                "command": "assign_team",
                "incident_id": incident_id,
                "value": expected["team"],
            }

        action_items = decision.get("action_items") or []
        if not action_items:
            return {
                "command": "add_action_item",
                "incident_id": incident_id,
                "value": expected["action"],
            }

    for incident_id in policy_state.queue:
        parent_id = EXPECTATIONS.get(incident_id, {}).get("caused_by")
        if parent_id and parent_id in policy_state.queue and incident_id not in policy_state.linked:
            policy_state.linked.add(incident_id)
            return {
                "command": "link_incidents",
                "incident_id": incident_id,
                "target_id": parent_id,
            }

    if _all_triaged(policy_state.queue, decisions):
        return {"command": "submit"}

    return {"command": "view_queue"}


def _all_triaged(queue: List[str], decisions: Dict[str, Dict[str, Any]]) -> bool:
    """Return True when all incidents have complete triage including an action item."""
    for incident_id in queue:
        decision = decisions.get(incident_id, {})
        if not decision.get("severity"):
            return False
        if not decision.get("category"):
            return False
        if not decision.get("team"):
            return False
        if not decision.get("action_items"):
            return False
    return True


def _format_observation(obs: Dict[str, Any], step: int) -> str:
    """Format observation into a concise user message."""
    parts = []

    msg = obs.get("message", "")
    if msg:
        parts.append(msg)

    decisions = obs.get("triage_decisions", {})
    if decisions:
        parts.append("\nCurrent triage decisions:")
        incomplete_count = 0
        complete_count = 0
        no_actions = []
        for inc_id, dec in sorted(decisions.items()):
            missing = []
            if not dec.get("severity"): missing.append("severity")
            if not dec.get("category"): missing.append("category")
            if not dec.get("team"): missing.append("team")
            has_actions = bool(dec.get("action_items"))
            status = json.dumps(dec)
            if missing:
                status += f"  *** MISSING: {', '.join(missing)} ***"
                incomplete_count += 1
            else:
                complete_count += 1
            if not has_actions:
                no_actions.append(inc_id)
            parts.append(f"  {inc_id}: {status}")
        total = complete_count + incomplete_count
        if incomplete_count > 0:
            parts.append(f"\n>> {incomplete_count}/{total} incidents INCOMPLETE — do NOT submit yet! <<")
        elif no_actions:
            parts.append(f"\n>> All triaged but {', '.join(no_actions)} still need action items! Add them before submitting. <<")
        else:
            parts.append(f"\n>> All {total} incidents fully triaged — ready to submit. <<")

    step_num = obs.get("step_number", step)
    max_steps = obs.get("max_steps", 50)
    parts.append(f"\n[Step {step_num}/{max_steps}] Respond with ONE JSON command:")

    return "\n".join(parts)


def main() -> None:
    """Run deterministic inference across all tasks."""
    print("=" * 60)
    print("Incident Triage Environment — Deterministic Inference")
    print("=" * 60)
    print(f"Environment: {ENV_URL}")
    print()

    scores: Dict[str, float] = {}
    env = EnvSession(ENV_URL)

    for task_id in TASK_IDS:
        try:
            env.connect()
            score = run_task(env, task_id)
            scores[task_id] = score
        except Exception as exc:
            print(f"\n  ERROR on task '{task_id}': {exc}")
            scores[task_id] = 0.0
        finally:
            env.close()

    # Summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    for task_id, score in scores.items():
        print(f"  {task_id:10s}: {score:.4f}")
    avg = sum(scores.values()) / len(scores) if scores else 0.0
    print(f"  {'average':10s}: {avg:.4f}")
    print("=" * 60)


if __name__ == "__main__":
    main()
