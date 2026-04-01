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

You interact with an incident triage environment using structured commands.
Each command is a JSON object with "command", and optionally "incident_id" and "value" fields.

AVAILABLE COMMANDS:
- {"command": "view_queue"} — View all incidents in the queue
- {"command": "inspect", "incident_id": "INC-XXX"} — View full details of a specific incident
- {"command": "set_severity", "incident_id": "INC-XXX", "value": "P0|P1|P2|P3"} — Set severity level
  - P0: Critical — total service outage or data loss
  - P1: Major — significant impact, partial outage, revenue loss
  - P2: Moderate — degraded service but workarounds available
  - P3: Low — minor issue, no immediate user impact
- {"command": "set_category", "incident_id": "INC-XXX", "value": "CATEGORY"} — Set category
  Categories: database, api, infrastructure, security, application, deployment, monitoring, network
- {"command": "assign_team", "incident_id": "INC-XXX", "value": "TEAM"} — Assign to response team
  Teams: database-team, platform-team, infra-team, security-team, backend-team, frontend-team, devops-team, sre-team
- {"command": "add_action_item", "incident_id": "INC-XXX", "value": "description"} — Add recommended action
- {"command": "submit"} — Submit all triage decisions (ends the episode)

WORKFLOW:
1. First, view_queue to see all incidents
2. Inspect each incident to understand its details (logs, metrics, recent changes)
3. For each incident, set severity, category, and team
4. Add at least one action item per incident describing the recommended immediate response
5. Once all incidents are triaged, submit

IMPORTANT:
- Always inspect incidents before making decisions
- Consider cascading failures — some incidents may be symptoms of others
- Be efficient — use as few steps as possible
- Respond with ONLY a single valid JSON command per turn, no additional text
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

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]
    history: List[str] = []

    for step in range(1, MAX_STEPS + 1):
        if done:
            break

        # Build the user prompt with current observation
        user_prompt = _build_user_prompt(obs, step, history)
        messages_for_llm = [
            messages[0],  # system prompt
            {"role": "user", "content": user_prompt},
        ]

        # Call LLM
        response_text = call_llm(messages_for_llm)
        action = parse_action(response_text)

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

        # Track history for context
        cmd = action.get("command", "?")
        msg_preview = obs.get("message", "")[:80]
        history_line = f"Step {step}: {cmd} -> reward={reward:+.4f}"
        if action.get("incident_id"):
            history_line += f" (incident={action['incident_id']})"
        history.append(history_line)

        if not DEBUG:
            print(f"  Step {step}: {cmd:20s} reward={reward:+.4f}  {'DONE' if done else ''}")

        if done:
            break

    final_score = reward if done else 0.0
    print(f"\n  Final Score: {final_score:.4f}")
    print(f"  Steps Used: {step}")
    return final_score


def _build_user_prompt(obs: Dict[str, Any], step: int, history: List[str]) -> str:
    """Build the user prompt from the current observation."""
    parts = [
        f"Step {step} of {obs.get('max_steps', 50)}. Task: {obs.get('task_id', '?')}",
        "",
        "Current observation:",
        obs.get("message", "(no message)"),
    ]

    decisions = obs.get("triage_decisions", {})
    if decisions:
        parts.append("\nYour current triage decisions:")
        for inc_id, dec in decisions.items():
            parts.append(f"  {inc_id}: {json.dumps(dec)}")

    if history:
        parts.append(f"\nRecent actions ({len(history)} total):")
        for line in history[-5:]:
            parts.append(f"  {line}")

    parts.append("\nRespond with a single JSON command:")

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
