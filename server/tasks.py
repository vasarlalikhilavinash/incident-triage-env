"""
Task definitions, grading logic, and escalation mechanics for the Incident Triage Environment.

Grading dimensions:
  - Severity correctness: 0.25
  - Category correctness: 0.20
  - Team assignment: 0.20
  - Action item quality: 0.10
  - Dependency identification: 0.10
  - Diagnosis depth: 0.10
  - Efficiency bonus: 0.05
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Set

from .incidents import (
    DEPENDENCY_MAP,
    INCIDENT_POOL,
    get_incidents_by_ids,
)


# ---------------------------------------------------------------------------
# Keep original incident lists for backward compat
# ---------------------------------------------------------------------------
EASY_INCIDENTS = get_incidents_by_ids(["INC-001"])
MEDIUM_INCIDENTS = get_incidents_by_ids(["INC-002", "INC-003", "INC-004"])
HARD_INCIDENTS = get_incidents_by_ids(["INC-005", "INC-006", "INC-007", "INC-008", "INC-009"])

# Expert tier: 8 incidents — includes red herrings, dependency chains, and misleading logs
EXPERT_INCIDENTS = get_incidents_by_ids([
    "INC-005",  # OOMKill (root cause)
    "INC-006",  # Redis failure (root cause)
    "INC-009",  # Alert storm (cascade from INC-006)
    "INC-011",  # Network partition (root cause)
    "INC-014",  # JWT failures
    "INC-015",  # Kafka lag (red herring — looks infra, is app)
    "INC-016",  # CDN stampede (cascade from INC-011)
    "INC-019",  # HPA thrashing (cascade from INC-005)
])

# ---------------------------------------------------------------------------
# Expected answers — pulled from incident pool
# ---------------------------------------------------------------------------

def _build_expected(incidents: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Build expected answers dict from incident list."""
    return {inc["id"]: inc["expected"] for inc in incidents}


EXPECTED_EASY = _build_expected(EASY_INCIDENTS)
EXPECTED_MEDIUM = _build_expected(MEDIUM_INCIDENTS)
EXPECTED_HARD = _build_expected(HARD_INCIDENTS)
EXPECTED_EXPERT = _build_expected(EXPERT_INCIDENTS)

# ---------------------------------------------------------------------------
# Dependencies expected for each task
# ---------------------------------------------------------------------------

def _build_expected_dependencies(incidents: List[Dict[str, Any]]) -> Dict[str, str]:
    """Build expected dependency links for incidents in a task."""
    inc_ids = {inc["id"] for inc in incidents}
    deps = {}
    for inc in incidents:
        caused_by = inc.get("caused_by")
        if caused_by and caused_by in inc_ids:
            deps[inc["id"]] = caused_by
    return deps


EXPECTED_DEPS_EASY = _build_expected_dependencies(EASY_INCIDENTS)
EXPECTED_DEPS_MEDIUM = _build_expected_dependencies(MEDIUM_INCIDENTS)
EXPECTED_DEPS_HARD = _build_expected_dependencies(HARD_INCIDENTS)
EXPECTED_DEPS_EXPERT = _build_expected_dependencies(EXPERT_INCIDENTS)

# ---------------------------------------------------------------------------
# Task definitions
# ---------------------------------------------------------------------------

TASKS: Dict[str, Dict[str, Any]] = {
    "easy": {
        "name": "Single Incident Triage",
        "description": (
            "Triage a single clear-cut production incident. "
            "Inspect the incident, determine its severity, categorize it, "
            "assign it to the right team, and recommend an action."
        ),
        "max_steps": 15,
        "incidents": EASY_INCIDENTS,
        "expected": EXPECTED_EASY,
        "expected_deps": EXPECTED_DEPS_EASY,
    },
    "medium": {
        "name": "Multi-Incident Prioritization",
        "description": (
            "Triage three production incidents with varying urgency. "
            "Prioritize correctly: some need immediate attention, others can wait. "
            "Each incident requires severity assessment, categorization, team assignment, "
            "and action recommendation."
        ),
        "max_steps": 30,
        "incidents": MEDIUM_INCIDENTS,
        "expected": EXPECTED_MEDIUM,
        "expected_deps": EXPECTED_DEPS_MEDIUM,
    },
    "hard": {
        "name": "Cascading Failure Investigation",
        "description": (
            "Triage five production incidents including cascading failures and red herrings. "
            "Some incidents are symptoms of others — identify root causes vs. downstream effects. "
            "Use the 'diagnose' command for deeper investigation and 'link_incidents' to map dependencies. "
            "Correctly prioritize, categorize, assign, and recommend actions for all incidents."
        ),
        "max_steps": 50,
        "incidents": HARD_INCIDENTS,
        "expected": EXPECTED_HARD,
        "expected_deps": EXPECTED_DEPS_HARD,
    },
    "expert": {
        "name": "Complex Infrastructure Crisis",
        "description": (
            "Triage eight production incidents during a major infrastructure crisis. "
            "Multiple cascading failures, red herrings (incidents that look like infrastructure "
            "issues but are actually application bugs), and hidden dependency chains. "
            "You MUST use 'diagnose' to uncover root causes and 'link_incidents' to identify "
            "which incidents are symptoms of others. Incidents escalate if not triaged promptly. "
            "Time pressure is real — prioritize wisely."
        ),
        "max_steps": 80,
        "incidents": EXPERT_INCIDENTS,
        "expected": EXPECTED_EXPERT,
        "expected_deps": EXPECTED_DEPS_EXPERT,
    },
}


# ---------------------------------------------------------------------------
# Escalation mechanics
# ---------------------------------------------------------------------------

def get_escalation_message(
    incidents: List[Dict[str, Any]],
    decisions: Dict[str, Dict[str, Any]],
    step: int,
) -> Optional[str]:
    """
    Check if any untriaged incidents should escalate at this step.
    Returns a warning message or None.
    """
    escalated = []
    for inc in incidents:
        inc_id = inc["id"]
        dec = decisions.get(inc_id, {})
        is_triaged = dec.get("severity") and dec.get("category") and dec.get("team")
        if is_triaged:
            continue

        rate = inc.get("escalation_rate", 5)
        if rate > 0 and step > 0 and step % rate == 0:
            escalated.append(inc_id)

    if not escalated:
        return None

    return (
        f"⚠ ESCALATION WARNING: Incidents {', '.join(escalated)} have not been triaged "
        f"and their impact is growing. Prioritize these immediately!"
    )


# ---------------------------------------------------------------------------
# Grading helpers
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _grade_severity(actual: str, expected: str) -> float:
    """Grade severity assignment. Full credit for exact, 50% if off by one."""
    if actual == expected:
        return 1.0
    a = _SEVERITY_ORDER.get(actual)
    e = _SEVERITY_ORDER.get(expected)
    if a is not None and e is not None and abs(a - e) == 1:
        return 0.5
    return 0.0


def _grade_category(actual: str, expected: str) -> float:
    """Grade category assignment. Exact match only."""
    return 1.0 if actual.lower() == expected.lower() else 0.0


def _grade_team(actual: str, expected: str) -> float:
    """Grade team assignment. Exact match only."""
    return 1.0 if actual.lower() == expected.lower() else 0.0


def _grade_actions(action_items: List[str], key_actions: List[str]) -> float:
    """Grade action items. Partial credit based on keyword overlap."""
    if not action_items:
        return 0.0
    combined = " ".join(action_items).lower()
    hits = sum(1 for kw in key_actions if kw.lower() in combined)
    if hits < 2:
        return 0.0
    return min(hits / max(len(key_actions), 1), 1.0)


def _grade_dependencies(
    declared_links: Dict[str, str],
    expected_deps: Dict[str, str],
) -> float:
    """
    Grade dependency identification.
    declared_links: {child_id: parent_id} from agent's link_incidents commands
    expected_deps: {child_id: parent_id} ground truth

    Returns 0.0-1.0 based on correct links identified.
    """
    if not expected_deps:
        return 1.0  # no dependencies to find = full marks

    if not declared_links:
        return 0.0

    correct = 0
    for child, parent in expected_deps.items():
        if declared_links.get(child) == parent:
            correct += 1

    return correct / len(expected_deps)


def _grade_diagnosis(
    diagnosed_incidents: Set[str],
    all_incidents: List[Dict[str, Any]],
) -> float:
    """
    Grade diagnosis depth — did the agent use 'diagnose' on incidents
    that have important diagnostic info?
    Gives credit for diagnosing incidents that have non-trivial diagnostics.
    """
    if not all_incidents:
        return 1.0

    # Incidents where diagnosis matters (have dependency or complex root cause)
    needs_diagnosis = [
        inc["id"] for inc in all_incidents
        if inc.get("caused_by") or inc.get("diagnostics", {}).get("root_cause_analysis", "")
    ]

    if not needs_diagnosis:
        return 1.0

    diagnosed_count = sum(1 for iid in needs_diagnosis if iid in diagnosed_incidents)
    return diagnosed_count / len(needs_diagnosis)


def grade_task(
    task_id: str,
    decisions: Dict[str, Dict[str, Any]],
    steps_taken: int,
    declared_links: Optional[Dict[str, str]] = None,
    diagnosed_incidents: Optional[Set[str]] = None,
) -> float:
    """
    Grade a completed task. Returns a score between 0.0 and 1.0.

    Weights:
      - Severity correctness:    0.25
      - Category correctness:    0.20
      - Team assignment:         0.20
      - Action item quality:     0.10
      - Dependency identification: 0.10
      - Diagnosis depth:         0.10
      - Efficiency bonus:        0.05
    """
    task = TASKS.get(task_id)
    if task is None:
        return 0.0

    expected = task["expected"]
    expected_deps = task.get("expected_deps", {})
    max_steps = task["max_steps"]
    incidents = task["incidents"]

    if not expected:
        return 0.0

    total_severity = 0.0
    total_category = 0.0
    total_team = 0.0
    total_actions = 0.0
    num_incidents = len(expected)

    for inc_id, exp in expected.items():
        dec = decisions.get(inc_id, {})

        sev = dec.get("severity", "")
        cat = dec.get("category", "")
        team = dec.get("team", "")
        items = dec.get("action_items", [])

        total_severity += _grade_severity(sev, exp["severity"])
        total_category += _grade_category(cat, exp["category"])
        total_team += _grade_team(team, exp["team"])
        total_actions += _grade_actions(items, exp["key_actions"])

    avg_severity = total_severity / num_incidents
    avg_category = total_category / num_incidents
    avg_team = total_team / num_incidents
    avg_actions = total_actions / num_incidents

    # Dependency identification
    dep_score = _grade_dependencies(declared_links or {}, expected_deps)

    # Diagnosis depth
    diag_score = _grade_diagnosis(diagnosed_incidents or set(), incidents)

    # Efficiency bonus: full bonus if ≤ half max_steps, zero if at max
    if max_steps > 0 and steps_taken <= max_steps:
        ratio = steps_taken / max_steps
        efficiency = max(0.0, 1.0 - ratio) if ratio > 0.5 else 1.0
    else:
        efficiency = 0.0

    score = (
        0.25 * avg_severity
        + 0.20 * avg_category
        + 0.20 * avg_team
        + 0.10 * avg_actions
        + 0.10 * dep_score
        + 0.10 * diag_score
        + 0.05 * efficiency
    )

    return round(min(max(score, 0.0), 1.0), 4)


def compute_step_reward(
    task_id: str,
    decisions: Dict[str, Dict[str, Any]],
    prev_decisions: Dict[str, Dict[str, Any]],
) -> float:
    """
    Compute incremental reward for a single step.
    Positive for correct decisions, negative for incorrect ones.
    """
    task = TASKS.get(task_id)
    if task is None:
        return 0.0

    expected = task["expected"]
    reward = 0.0

    for inc_id, exp in expected.items():
        dec = decisions.get(inc_id, {})
        prev = prev_decisions.get(inc_id, {})

        # Check severity change
        if dec.get("severity") != prev.get("severity") and dec.get("severity"):
            if _grade_severity(dec["severity"], exp["severity"]) > 0:
                reward += 0.06
            else:
                reward -= 0.06

        # Check category change
        if dec.get("category") != prev.get("category") and dec.get("category"):
            if _grade_category(dec["category"], exp["category"]) > 0:
                reward += 0.05
            else:
                reward -= 0.05

        # Check team change
        if dec.get("team") != prev.get("team") and dec.get("team"):
            if _grade_team(dec["team"], exp["team"]) > 0:
                reward += 0.05
            else:
                reward -= 0.05

        # Check action items added
        curr_items = dec.get("action_items", [])
        prev_items = prev.get("action_items", [])
        if len(curr_items) > len(prev_items):
            reward += 0.02

    return reward
