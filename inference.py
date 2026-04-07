#!/usr/bin/env python3
"""Baseline inference script for the Incident Triage Environment.

Uses an LLM (via the OpenAI-compatible API) inside a persistent WebSocket
session to run a full agent loop across all four tasks and report scores.

Required environment variables:
    API_BASE_URL   - LLM endpoint (default: https://api.openai.com/v1)
    MODEL_NAME     - Model identifier  (default: gpt-4o-mini)
    HF_TOKEN       - API key
    ENV_URL        - Environment server URL (default: http://localhost:8000)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

import websocket  # websocket-client (sync)
from openai import OpenAI

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE_URL: str = os.environ.get("API_BASE_URL", "https://api.openai.com/v1")
MODEL_NAME: str = os.environ.get("MODEL_NAME", "gpt-4o-mini")
HF_TOKEN: str = os.environ.get("HF_TOKEN") or ""

# Optional — only needed when using from_docker_image()
LOCAL_IMAGE_NAME: str = os.environ.get("LOCAL_IMAGE_NAME") or ""

ENV_URL: str = os.environ.get("ENV_URL", "http://localhost:8000")

MAX_STEPS: int = 50
TEMPERATURE: float = 0.2
MAX_TOKENS: int = 1024
DEBUG: bool = os.environ.get("DEBUG", "false").lower() in ("true", "1")

TASK_IDS: List[str] = ["easy", "medium", "hard", "expert"]

# Connection retry settings
_WS_CONNECT_RETRIES: int = 5
_WS_CONNECT_DELAY_S: float = 3.0

# ---------------------------------------------------------------------------
# LLM client — created lazily so import-time failures are impossible
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
   d. add_action_item — REQUIRED for every incident! Write a specific, actionable remediation that references the actual technology/service from the logs
5. If diagnosis reveals one incident is caused by another, use link_incidents to record the dependency
6. Repeat for ALL incidents in the queue
7. BEFORE submitting: verify triage_decisions shows ALL incidents have severity + category + team. If any are missing, triage them first.
8. submit ONLY when every single incident is fully triaged

CLASSIFICATION TIPS:
- Alert storms caused by an upstream dependency failing → classify as "monitoring"
- Redis/Memcached node failures → "database"
- OOMKilled pods due to a recent code deployment → "application"
- Failed image pulls during rollback → "deployment"
- DDoS or anomalous traffic patterns → "security"
- Certificate expiry → "security"

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
    """Call the LLM and return the response text. Never raises."""
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
    """Extract a JSON action dict from the LLM response. Never raises."""
    text = response.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    # Direct JSON parse
    try:
        action = json.loads(text)
        if isinstance(action, dict) and "command" in action:
            return action
    except (json.JSONDecodeError, ValueError):
        pass

    # Find embedded JSON object with a "command" key
    match = re.search(r'\{[^{}]*"command"\s*:\s*"[^"]+?"[^{}]*\}', text)
    if match:
        try:
            action = json.loads(match.group())
            if isinstance(action, dict) and "command" in action:
                return action
        except (json.JSONDecodeError, ValueError):
            pass

    # Keyword fallbacks
    if "submit" in text.lower():
        return {"command": "submit"}

    return {"command": "view_queue"}


# ---------------------------------------------------------------------------
# WebSocket session with the environment
# ---------------------------------------------------------------------------

class EnvSession:
    """Manages a persistent WebSocket session with the environment server."""

    def __init__(self, base_url: str) -> None:
        ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
        self._ws_url = f"{ws_url.rstrip('/')}/ws"
        self._ws: Optional[websocket.WebSocket] = None

    def connect(self) -> None:
        """Open the WebSocket connection, retrying if the server is still starting."""
        last_exc: Exception = RuntimeError("connect never attempted")
        for attempt in range(1, _WS_CONNECT_RETRIES + 1):
            try:
                self._ws = websocket.create_connection(self._ws_url, timeout=30)
                return
            except Exception as exc:
                last_exc = exc
                if attempt < _WS_CONNECT_RETRIES:
                    if DEBUG:
                        print(
                            f"  [DEBUG] WS connect attempt {attempt} failed: {exc}. "
                            f"Retrying in {_WS_CONNECT_DELAY_S}s…",
                            flush=True,
                        )
                    time.sleep(_WS_CONNECT_DELAY_S)
        raise RuntimeError(
            f"Could not connect to {self._ws_url} "
            f"after {_WS_CONNECT_RETRIES} attempts: {last_exc}"
        )

    def close(self) -> None:
        """Close the WebSocket session gracefully. Never raises."""
        ws = self._ws
        self._ws = None
        if ws is None:
            return
        try:
            ws.send(json.dumps({"type": "close"}))
        except Exception:
            pass
        try:
            ws.close()
        except Exception:
            pass

    def _send_recv(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Send a WS message and return the parsed data payload. Raises on errors."""
        if self._ws is None:
            raise RuntimeError("WebSocket not connected — call connect() first")

        try:
            self._ws.send(json.dumps(msg))
            raw = self._ws.recv()
        except websocket.WebSocketConnectionClosedException as exc:
            raise RuntimeError("WebSocket connection closed unexpectedly") from exc
        except websocket.WebSocketTimeoutException as exc:
            raise RuntimeError("WebSocket receive timed out") from exc

        if not raw:
            raise RuntimeError("Empty response from environment server")

        try:
            response = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"Invalid JSON from server: {raw[:200]}") from exc

        if not isinstance(response, dict):
            raise RuntimeError(f"Unexpected response type: {type(response).__name__}")

        if response.get("type") == "error":
            data = response.get("data")
            msg_text = (data.get("message", "") if isinstance(data, dict) else "") or str(response)
            raise RuntimeError(f"Server error: {msg_text}")

        data = response.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(
                f"Unexpected response shape — expected 'data' dict, got: {response}"
            )

        return data

    def reset(self, task_id: str = "easy") -> Dict[str, Any]:
        """Reset the environment for a given task. Returns the initial state dict."""
        return self._send_recv({"type": "reset", "data": {"task_id": task_id}})

    def step(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Execute one action. Returns the resulting state dict."""
        return self._send_recv({"type": "step", "data": action})


# ---------------------------------------------------------------------------
# Observation formatter
# ---------------------------------------------------------------------------

def _format_observation(obs: Dict[str, Any], step: int) -> str:
    """Format the env observation into a concise LLM user message."""
    if not isinstance(obs, dict):
        obs = {}

    parts: List[str] = []

    msg = obs.get("message") or ""
    if msg:
        parts.append(str(msg))

    decisions: Dict[str, Any] = obs.get("triage_decisions") or {}
    if decisions:
        parts.append("\nCurrent triage decisions:")
        incomplete = 0
        complete = 0
        no_actions: List[str] = []

        for inc_id, dec in sorted(decisions.items()):
            if not isinstance(dec, dict):
                continue
            missing: List[str] = []
            if not dec.get("severity"):
                missing.append("severity")
            if not dec.get("category"):
                missing.append("category")
            if not dec.get("team"):
                missing.append("team")

            status = json.dumps(dec)
            if missing:
                status += f"  *** MISSING: {', '.join(missing)} ***"
                incomplete += 1
            else:
                complete += 1

            if not dec.get("action_items"):
                no_actions.append(inc_id)

            parts.append(f"  {inc_id}: {status}")

        total = complete + incomplete
        if incomplete > 0:
            parts.append(
                f"\n>> {incomplete}/{total} incidents INCOMPLETE — do NOT submit yet! <<"
            )
        elif no_actions:
            parts.append(
                f"\n>> All triaged but {', '.join(no_actions)} still need action items! "
                "Add them before submitting. <<"
            )
        else:
            parts.append(f"\n>> All {total} incidents fully triaged — ready to submit. <<")

    try:
        step_num = int(obs.get("step_number") or step)
    except (TypeError, ValueError):
        step_num = step
    try:
        max_s = int(obs.get("max_steps") or MAX_STEPS)
    except (TypeError, ValueError):
        max_s = MAX_STEPS

    parts.append(f"\n[Step {step_num}/{max_s}] Respond with ONE JSON command:")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Per-task inference loop
# ---------------------------------------------------------------------------

def run_task(env: EnvSession, task_id: str) -> float:
    """Run a full agent episode on one task and return the final score."""
    print(f"[START] task_id={task_id}", flush=True)

    result = env.reset(task_id=task_id)

    # WS reset response shape: {"observation": {...}, "reward": float|None, "done": bool}
    obs: Dict[str, Any] = result.get("observation") or {}
    reward: float = float(result.get("reward") or 0.0)
    done: bool = bool(result.get("done", False))

    try:
        max_steps = max(int(obs.get("max_steps") or MAX_STEPS), MAX_STEPS)
    except (TypeError, ValueError):
        max_steps = MAX_STEPS

    conversation: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _format_observation(obs, step=0)},
    ]

    step = 0
    for step in range(1, max_steps + 1):
        if done:
            break

        response_text = call_llm(conversation)
        action = parse_action(response_text)

        conversation.append({"role": "assistant", "content": json.dumps(action)})

        cmd = action.get("command", "?")

        try:
            result = env.step(action)
        except Exception as exc:
            print(f"[STEP] task_id={task_id} step={step} error={exc}", flush=True)
            break

        obs = result.get("observation") or {}
        reward = float(result.get("reward") or 0.0)
        done = bool(result.get("done", False))

        conversation.append({"role": "user", "content": _format_observation(obs, step=step)})

        # Trim conversation: keep system + first 2 turns + last 20 turns
        if len(conversation) > 30:
            conversation = conversation[:3] + conversation[-20:]

        print(f"[STEP] task_id={task_id} step={step} action={cmd} reward={reward:.4f}", flush=True)

        if done:
            break

    final_score = reward if done else 0.0
    print(f"[END] task_id={task_id} score={final_score:.4f} steps={step}", flush=True)
    return final_score


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run baseline inference across all tasks and print a summary."""
    print(f"model={MODEL_NAME} api_base={API_BASE_URL} env={ENV_URL}", flush=True)

    if not HF_TOKEN:
        print("ERROR: No API key found. Set HF_TOKEN.", file=sys.stderr)
        sys.exit(1)

    scores: Dict[str, float] = {}

    for task_id in TASK_IDS:
        env = EnvSession(ENV_URL)
        try:
            env.connect()
            scores[task_id] = run_task(env, task_id)
        except Exception as exc:
            print(f"[END] task_id={task_id} score=0.0000 error={exc}", flush=True)
            scores[task_id] = 0.0
        finally:
            env.close()

    avg = sum(scores.values()) / len(scores) if scores else 0.0
    summary = " ".join(f"{t}={s:.4f}" for t, s in scores.items())
    print(f"[SUMMARY] {summary} average={avg:.4f}", flush=True)

if __name__ == "__main__":
    main()
