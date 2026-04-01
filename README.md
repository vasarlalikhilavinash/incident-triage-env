---
title: Incident Triage Environment
emoji: 🚨
colorFrom: red
colorTo: red
sdk: docker
pinned: false
app_port: 8000
tags:
  - openenv
---

# 🚨 Incident Triage Environment

A real-world **production incident triage** environment for [OpenEnv](https://github.com/meta-pytorch/OpenEnv), where an AI agent acts as an on-call Site Reliability Engineer (SRE).

The agent must inspect production incidents, assess severity, categorize root causes, assign response teams, and recommend immediate actions — just like a real SRE would during an on-call shift.

## Motivation

Incident triage is a critical, high-stakes task performed daily by thousands of SREs worldwide. Poor triage leads to delayed response, extended outages, and escalated customer impact. This environment provides a structured benchmark for evaluating how well AI agents can:

- **Analyze** complex system alerts with logs, metrics, and recent changes
- **Prioritize** incidents based on business impact and urgency
- **Identify** root causes vs. downstream symptoms in cascading failures
- **Assign** the right response team with relevant expertise
- **Recommend** actionable remediation steps

## Action Space

The agent sends structured JSON actions with a `command` field:

| Command | Fields | Description |
|---------|--------|-------------|
| `view_queue` | — | View all incidents with status summaries |
| `inspect` | `incident_id` | View full incident details (logs, metrics, changes) |
| `set_severity` | `incident_id`, `value` | Set severity: `P0` (critical) / `P1` (major) / `P2` (moderate) / `P3` (low) |
| `set_category` | `incident_id`, `value` | Set category: `database`, `api`, `infrastructure`, `security`, `application`, `deployment`, `monitoring`, `network` |
| `assign_team` | `incident_id`, `value` | Assign team: `database-team`, `platform-team`, `infra-team`, `security-team`, `backend-team`, `frontend-team`, `devops-team`, `sre-team` |
| `add_action_item` | `incident_id`, `value` | Add a recommended action (free text, max 5 per incident) |
| `submit` | — | Submit all triage decisions and end the episode |

### Example Action

```json
{"command": "set_severity", "incident_id": "INC-001", "value": "P1"}
```

## Observation Space

Each observation includes:

| Field | Type | Description |
|-------|------|-------------|
| `message` | `str` | Human-readable feedback for the last action |
| `incident_queue` | `list[dict]` | Summary of all incidents (after `view_queue`) |
| `current_incident` | `dict` | Full incident details (after `inspect`) |
| `triage_decisions` | `dict` | All current triage decisions keyed by incident ID |
| `task_id` | `str` | Current task identifier |
| `step_number` | `int` | Current step number |
| `max_steps` | `int` | Maximum allowed steps |
| `done` | `bool` | Whether the episode has ended |
| `reward` | `float` | Reward signal |

## Tasks

### Task 1: Easy — Single Incident Triage

A single clear-cut production incident (database connection pool exhaustion). The agent needs to inspect it, set the correct severity/category/team, and submit.

**Expected difficulty**: Straightforward — one incident with obvious triage decisions.  
**Max steps**: 15

### Task 2: Medium — Multi-Incident Prioritization

Three concurrent production incidents with varying urgency:
- API gateway timeouts (high impact, customer-facing)
- Disk space warning (growing but not immediate)
- SSL certificate expiry (48 hours to act)

**Expected difficulty**: Moderate — requires correct prioritization and nuanced categorization across multiple incidents.  
**Max steps**: 30

### Task 3: Hard — Cascading Failure Investigation

Five simultaneous incidents including cascading failures and red herrings:
- Kubernetes pods crash-looping (OOMKilled)
- Redis cluster node failure
- Anomalous traffic spike (possible DDoS)
- Failed deployment rollback
- Alert storm caused by Redis failure (downstream effect, not root cause)

**Expected difficulty**: Challenging — the agent must identify that the alert storm (INC-009) is a downstream symptom of the Redis failure (INC-006), not an independent incident. Requires root cause analysis across related incidents.  
**Max steps**: 50

## Reward Function

### Per-step rewards
- Correct severity assignment: +0.06
- Correct category: +0.05
- Correct team: +0.05
- Relevant action items: +0.02
- Wrong decisions: small negative penalty
- Invalid commands: -0.01

### Final score (0.0–1.0) on submit
| Component | Weight |
|-----------|--------|
| Severity correctness | 30% |
| Category correctness | 25% |
| Team assignment | 25% |
| Action item quality | 15% |
| Efficiency (fewer steps) | 5% |

Severity grading: exact match = 100%, off by one level = 50%, off by two+ = 0%.

## Setup & Usage

### Prerequisites

- Python 3.10+
- Docker (for containerized execution)

### Local Development

```bash
# Install dependencies
uv sync

# Start the server
uv run server
```

### Docker

```bash
# Build the container
docker build -t incident-triage-env -f server/Dockerfile .

# Run
docker run -p 8000:8000 incident-triage-env
```

### Running the Baseline

The inference script uses the **OpenAI client** and reads credentials from environment variables:

```bash
export API_BASE_URL="https://api.openai.com/v1"   # LLM API endpoint
export MODEL_NAME="gpt-4o-mini"                    # Model identifier
export HF_TOKEN="your-api-key"                     # API key (also reads OPENAI_API_KEY)
export ENV_URL="http://localhost:8000"              # Environment server URL

python inference.py
```

Runtime: < 20 minutes on 2 vCPU / 8 GB RAM.

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/reset` | POST | Reset environment (accepts `task_id` in body) |
| `/step` | POST | Execute action |
| `/state` | GET | Get current state |
| `/schema` | GET | Get action/observation JSON schemas |
| `/ws` | WebSocket | Persistent session (used by inference script) |

### Validate

```bash
pip install openenv-core
openenv validate
```

## Baseline Scores

| Task | Score | Steps |
|------|-------|-------|
| easy | ~0.85 | 6–8 |
| medium | ~0.70 | 15–20 |
| hard | ~0.55 | 25–35 |
| **average** | **~0.70** | — |

*Scores may vary depending on the model used.*

## Project Structure

```
incident-triage-env/
├── __init__.py             # Package exports
├── models.py               # TriageAction, TriageObservation, TriageState (typed Pydantic)
├── client.py               # EnvClient subclass for remote connection
├── server/
│   ├── __init__.py
│   ├── incident_triage_env_environment.py  # IncidentTriageEnvironment (step/reset/state)
│   ├── tasks.py            # 9 incidents, 3 tasks, graders (0.0–1.0)
│   ├── app.py              # FastAPI app via openenv create_app()
│   ├── Dockerfile          # Container definition
│   └── requirements.txt    # Server dependencies
├── Dockerfile              # Root Dockerfile for HF Spaces
├── openenv.yaml            # OpenEnv manifest
├── pyproject.toml          # Dependencies & package config
├── uv.lock                 # Locked dependencies
├── inference.py            # Baseline inference script (OpenAI client)
└── README.md               # This file
```

## License

MIT
