#!/usr/bin/env python3
"""
Baseline inference script for the Incident Triage Environment.

Uses the OpenAI API client to run a model against the environment
across all three tasks (easy, medium, hard) and reports scores.

Required environment variables:
    API_BASE_URL   - The API endpoint for the LLM
    MODEL_NAME     - The model identifier to use for inference
    HF_TOKEN       - Your HuggingFace / API key
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

import websocket
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

TASK_IDS: List[str] = ["easy", "medium", "hard"]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert Site Reliability Engineer (SRE) on-call, triaging production incidents.

Each turn, respond with EXACTLY ONE JSON command — no other text.

COMMANDS:
{"command": "view_queue"}
{"command": "inspect", "incident_id": "INC-XXX"}
{"command": "set_severity", "incident_id": "INC-XXX", "value": "VALUE"}
{"command": "set_category", "incident_id": "INC-XXX", "value": "VALUE"}
{"command": "assign_team", "incident_id": "INC-XXX", "value": "VALUE"}
{"command": "add_action_item", "incident_id": "INC-XXX", "value": "free text description"}
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
2. inspect each incident individually — read ALL logs, metrics, and recent changes
3. For each incident, do ALL FOUR of these in order:
   a. set_severity — based on impact analysis
   b. set_category — based on root cause from logs
   c. assign_team — must match category per the table above
   d. add_action_item — REQUIRED for every incident! Write a specific, actionable remediation that references the actual technology/service from the logs (e.g., mention the specific service name, rollback version, config to change, tool to use)
4. Repeat steps 2-3 for ALL incidents in the queue
5. BEFORE submitting: verify triage_decisions shows ALL incidents have severity + category + team. If any are missing, triage them first.
6. submit ONLY when every single incident is fully triaged

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
# LLM client
# ---------------------------------------------------------------------------

client = OpenAI(base_url=API_BASE_URL, api_key=HF_TOKEN)


def call_llm(messages: List[Dict[str, Any]]) -> str:
    """Call the LLM and return the response text."""
    try:
        completion = client.chat.completions.create(
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


# ---------------------------------------------------------------------------
# Action parsing
# ---------------------------------------------------------------------------

def parse_action(response: str) -> Dict[str, Any]:
    """Extract a JSON action from the LLM response."""
    # Try to find JSON in the response
    # First try: direct JSON parse
    text = response.strip()
    if text.startswith("```"):
        # Strip markdown code blocks
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    try:
        action = json.loads(text)
        if isinstance(action, dict) and "command" in action:
            return action
    except json.JSONDecodeError:
        pass

    # Second try: find JSON object in the text
    match = re.search(r'\{[^{}]*"command"\s*:\s*"[^"]+?"[^{}]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Fallback: try to detect command from text
    if "view_queue" in text.lower():
        return {"command": "view_queue"}
    if "submit" in text.lower():
        return {"command": "submit"}

    return {"command": "view_queue"}


# ---------------------------------------------------------------------------
# WebSocket communication with the environment
# ---------------------------------------------------------------------------

class EnvSession:
    """Manages a WebSocket session with the environment."""

    def __init__(self, base_url: str) -> None:
        ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
        self._ws_url = f"{ws_url}/ws"
        self._ws: Optional[websocket.WebSocket] = None

    def connect(self) -> None:
        self._ws = websocket.create_connection(self._ws_url, timeout=30)

    def close(self) -> None:
        if self._ws:
            try:
                self._ws.send(json.dumps({"type": "close"}))
            except Exception:
                pass
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def reset(self, task_id: str = "easy") -> Dict[str, Any]:
        """Reset the environment and return the initial observation."""
        assert self._ws is not None
        msg = {"type": "reset", "data": {"task_id": task_id}}
        self._ws.send(json.dumps(msg))
        response = json.loads(self._ws.recv())
        if response.get("type") == "error":
            raise RuntimeError(f"Reset error: {response.get('data', {}).get('message', 'unknown')}")
        return response.get("data", {})

    def step(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Send an action and return the result."""
        assert self._ws is not None
        msg = {"type": "step", "data": action}
        self._ws.send(json.dumps(msg))
        response = json.loads(self._ws.recv())
        if response.get("type") == "error":
            raise RuntimeError(f"Step error: {response.get('data', {}).get('message', 'unknown')}")
        return response.get("data", {})


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

    print(f"  Initial: {obs.get('message', '')[:120]}...")

    conversation: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    # Add initial observation as first user message
    conversation.append({"role": "user", "content": _format_observation(obs, step=0)})

    for step in range(1, MAX_STEPS + 1):
        if done:
            break

        # Call LLM with full conversation
        response_text = call_llm(conversation)
        action = parse_action(response_text)

        # Add assistant response to conversation
        conversation.append({"role": "assistant", "content": json.dumps(action)})

        if DEBUG:
            print(f"  Step {step}: {json.dumps(action)}")

        # Execute action
        try:
            result = env.step(action)
        except Exception as exc:
            print(f"  Step {step} ERROR: {exc}")
            break

        obs = result.get("observation", {})
        reward = result.get("reward", 0.0) or 0.0
        done = result.get("done", False)

        # Add observation as next user message
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
