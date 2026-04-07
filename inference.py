#!/usr/bin/env python3
"""Baseline inference script for the Incident Triage Environment.

Uses the OpenAI API client to run a model against the environment
across all four tasks (easy, medium, hard, expert) and reports scores.

Required environment variables:
    API_BASE_URL   - The API endpoint for the LLM
    MODEL_NAME     - The model identifier to use for inference
    HF_TOKEN       - Your HuggingFace / API key (also reads OPENAI_API_KEY)
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional
from urllib import error, request

from openai import OpenAI

# ---------------------------------------------------------------------------
# Configuration from environment variables
# ---------------------------------------------------------------------------

API_BASE_URL: str = os.environ.get("API_BASE_URL", "https://api.openai.com/v1")
MODEL_NAME: str = os.environ.get("MODEL_NAME", "gpt-4o-mini")
HF_TOKEN: str = os.environ.get("HF_TOKEN", os.environ.get("OPENAI_API_KEY", ""))

# Environment server URL — defaults to local Docker for testing
ENV_URL: str = os.environ.get("ENV_URL", "http://localhost:8000")

MAX_STEPS: int = 50
TEMPERATURE: float = 0.2
MAX_TOKENS: int = 1024
DEBUG: bool = os.environ.get("DEBUG", "false").lower() in ("true", "1")

TASK_IDS: List[str] = ["easy", "medium", "hard", "expert"]

# ---------------------------------------------------------------------------
# LLM client — initialised lazily so missing API key doesn't crash at import
# ---------------------------------------------------------------------------

_llm_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _llm_client
    if _llm_client is None:
        _llm_client = OpenAI(base_url=API_BASE_URL, api_key=HF_TOKEN)
    return _llm_client


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert Site Reliability Engineer (SRE) on-call, triaging production incidents.

Each turn, respond with EXACTLY ONE JSON command — no other text.

COMMANDS:
{"command": "view_queue"}
{"command": "inspect", "incident_id": "INC-XXX"}
{"command": "diagnose", "incident_id": "INC-XXX"}
{"command": "set_severity", "incident_id": "INC-XXX", "value": "VALUE"}
{"command": "set_category", "incident_id": "INC-XXX", "value": "VALUE"}
{"command": "assign_team", "incident_id": "INC-XXX", "value": "VALUE"}
{"command": "add_action_item", "incident_id": "INC-XXX", "value": "free text description"}
{"command": "link_incidents", "incident_id": "INC-CHILD", "target_id": "INC-PARENT"}
{"command": "submit"}

SEVERITY LEVELS (choose carefully based on CURRENT impact, not potential):
- P0: Complete outage NOW — total service down, active data loss, zero availability
- P1: Major impact NOW — significant user-facing failures, revenue actively being lost, multiple replicas down, data store nodes failing, connection pools exhausted causing transaction failures
- P2: Degraded but functional — service still works but slower or with workarounds, cert expiring in days (not yet expired), failed deployments with some replicas still running, DDoS being partially mitigated, elevated latency
- P3: Minor or no user impact RIGHT NOW — disk filling slowly with hours of runway, alert storms that are SYMPTOMS of other incidents (not root cause), cosmetic issues, stale logrotate

CATEGORY CLASSIFICATION (read logs carefully):
- database: ANY data store issue — PostgreSQL, MySQL, Redis, Memcached, Elasticsearch, etc. Redis IS a database.
- api: API gateway issues, upstream service timeouts, HTTP error spikes, circuit breaker trips
- infrastructure: Physical/VM/node hardware, disk, CPU, network infra failures
- security: DDoS, traffic anomalies, unauthorized access, certificate issues, WAF alerts
- application: App-level bugs, OOM kills, memory leaks, crashes caused by code changes/deployments to app services
- deployment: Failed deploys, stuck rollbacks, image pull failures, ArgoCD/CI-CD pipeline issues
- monitoring: Alert storms, alert correlation issues, cascading alert noise caused by UPSTREAM dependency failures (not the root cause itself)
- network: DNS, routing, BGP, load balancer, connectivity issues

TEAM ASSIGNMENT (must match category):
- database → database-team
- api → platform-team
- infrastructure → infra-team
- security → security-team
- application → backend-team
- deployment → platform-team
- monitoring → sre-team
- network → infra-team

WORKFLOW — follow strictly for EACH incident:
1. view_queue — see all incidents
2. inspect each incident — read ALL logs, metrics, and recent changes
3. diagnose the incident — reveals deep root cause analysis and hidden dependency info
4. For each incident, do ALL FOUR of these in order:
   a. set_severity — based on impact analysis
   b. set_category — based on root cause from logs AND diagnosis
   c. assign_team — must match category per the table above
   d. add_action_item — REQUIRED for every incident! Write a specific, actionable remediation that references the actual technology/service from the logs (e.g., mention the specific service name, rollback version, config to change, tool to use)
5. If diagnosis reveals one incident is caused by another, use link_incidents to record the dependency
6. Repeat for ALL incidents in the queue
7. BEFORE submitting: verify triage_decisions shows ALL incidents have severity + category + team. If any are missing, triage them first.
8. submit ONLY when every single incident is fully triaged

CLASSIFICATION TIPS:
- If an incident is an alert storm or cascade of alerts caused by an upstream dependency failing, classify it as "monitoring" (the alerts are the problem, not the service itself).
- Redis/Memcached node failures are "database" — they are data stores even though they run on infrastructure.
- OOMKilled pods due to a recent code deployment with memory issues → "application" (the code caused it, not the infrastructure).
- Failed image pulls during rollback → "deployment" (CI/CD pipeline issue).
- DDoS or anomalous traffic patterns → "security" even if they affect APIs.
- Certificate expiry → "security".

CRITICAL RULES:
- Use EXACTLY the values listed above (case-sensitive).
- NEVER submit until ALL incidents have severity, category, and team. Count them.
- If triage_decisions shows any incident with missing fields, triage it before submitting.
- Respond with ONLY the JSON command, nothing else.
"""


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def call_llm(messages: List[Dict[str, Any]]) -> str:
    """Call the LLM and return the response text."""
    try:
        completion = _get_client().chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            stream=False,
        )
        return completion.choices[0].message.content or ""
    except Exception as exc:
        if DEBUG:
            print(f"  [DEBUG] LLM call failed: {exc}", flush=True)
        return '{"command": "view_queue"}'


def parse_action(response: str) -> Dict[str, Any]:
    """Extract a JSON action from the LLM response."""
    text = response.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    try:
        action = json.loads(text)
        if isinstance(action, dict) and "command" in action:
            return action
    except json.JSONDecodeError:
        pass

    match = re.search(r'\{[^{}]*"command"\s*:\s*"[^"]+?"[^{}]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    if "view_queue" in text.lower():
        return {"command": "view_queue"}
    if "submit" in text.lower():
        return {"command": "submit"}

    return {"command": "view_queue"}



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
    """Run inference on a single task and return the score."""
    print(f"\n{'='*60}")
    print(f"Task: {task_id}")
    print(f"{'='*60}")

    result = env.reset(task_id=task_id)
    obs = result.get("observation", {})
    reward = result.get("reward", 0.0)
    done = result.get("done", False)
    max_steps = max(int(obs.get("max_steps", MAX_STEPS) or MAX_STEPS), MAX_STEPS)

    print(f"  Initial: {obs.get('message', '')[:120]}...")

    conversation: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _format_observation(obs, step=0)},
    ]

    for step in range(1, max_steps + 1):
        if done:
            break

        response_text = call_llm(conversation)
        action = parse_action(response_text)

        conversation.append({"role": "assistant", "content": json.dumps(action)})

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

        conversation.append({"role": "user", "content": _format_observation(obs, step=step)})

        # Keep conversation manageable — trim middle if too long
        if len(conversation) > 30:
            conversation = conversation[:3] + conversation[-20:]

        cmd = action.get("command", "?")
        if not DEBUG:
            print(f"  Step {step}: {cmd:20s} reward={reward:+.4f}  {'DONE' if done else ''}")

        if done:
            break

    final_score = reward if done else 0.0
    print(f"\n  Final Score: {final_score:.4f}")
    print(f"  Steps Used: {step}")
    return final_score


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
    """Run baseline inference across all tasks."""
    print("=" * 60)
    print("Incident Triage Environment — Baseline Inference")
    print("=" * 60)
    print(f"LLM: {MODEL_NAME} @ {API_BASE_URL}")
    print(f"Environment: {ENV_URL}")
    print()

    if not HF_TOKEN:
        print("WARNING: No API key found. Set HF_TOKEN or OPENAI_API_KEY.")
        sys.exit(1)

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
