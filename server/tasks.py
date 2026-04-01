"""Task definitions, incident data, and grading logic for the Incident Triage Environment."""

from __future__ import annotations

from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Incident data for each task
# ---------------------------------------------------------------------------

EASY_INCIDENTS: List[Dict[str, Any]] = [
    {
        "id": "INC-001",
        "title": "Database Connection Pool Exhaustion",
        "summary": "PostgreSQL connection pool at 98% capacity on production payment database",
        "details": {
            "alert_source": "Datadog",
            "triggered_at": "2024-03-15T14:23:00Z",
            "service": "payment-service",
            "environment": "production",
            "logs": [
                "[14:20:12] WARN  payment-service: Connection pool utilization at 85%",
                "[14:21:45] ERROR payment-service: Connection acquisition timeout after 5000ms",
                "[14:22:03] FATAL postgres: too many connections for role 'payment_svc' (max: 100, current: 98)",
                "[14:22:15] ERROR payment-service: 23 payment transactions failed in last 60s",
                "[14:23:00] ALERT Datadog: PostgreSQL connection pool > 95% threshold breached",
            ],
            "metrics": {
                "active_connections": 98,
                "max_connections": 100,
                "avg_query_time_ms": 450,
                "failed_transactions_last_5min": 47,
                "affected_users_estimate": 1200,
            },
            "recent_changes": (
                "payment-service v2.3.1 deployed 2 hours ago "
                "(added new batch processing feature that opens dedicated connections)"
            ),
        },
    },
]

MEDIUM_INCIDENTS: List[Dict[str, Any]] = [
    {
        "id": "INC-002",
        "title": "API Gateway Timeout Spike",
        "summary": "503 errors on /api/v2/orders endpoint; latency 200ms → 8000ms",
        "details": {
            "alert_source": "PagerDuty",
            "triggered_at": "2024-03-15T09:12:00Z",
            "service": "api-gateway",
            "environment": "production",
            "logs": [
                "[09:10:31] WARN  api-gateway: Upstream timeout for order-service (5000ms exceeded)",
                "[09:11:02] ERROR api-gateway: 503 returned for GET /api/v2/orders — upstream unavailable",
                "[09:11:15] WARN  api-gateway: Circuit breaker OPEN for order-service (failure rate 62%)",
                "[09:11:45] ERROR api-gateway: 147 requests queued, backpressure engaged",
                "[09:12:00] ALERT PagerDuty: API p99 latency > 5000ms for 2 consecutive minutes",
            ],
            "metrics": {
                "p99_latency_ms": 8200,
                "error_rate_percent": 34.5,
                "requests_per_second": 1200,
                "failed_orders_last_5min": 312,
                "affected_users_estimate": 4500,
            },
            "recent_changes": "No recent deployments. order-service last deployed 3 days ago.",
        },
    },
    {
        "id": "INC-003",
        "title": "Disk Space Warning on Worker Node",
        "summary": "/var/log partition at 92% on worker-node-7, growing ~2%/hour",
        "details": {
            "alert_source": "Prometheus",
            "triggered_at": "2024-03-15T09:30:00Z",
            "service": "worker-node-7",
            "environment": "production",
            "logs": [
                "[09:28:00] WARN  node-exporter: /var/log disk usage 90%",
                "[09:29:00] WARN  node-exporter: /var/log disk usage 91%",
                "[09:30:00] ALERT Prometheus: disk_usage_percent{mount='/var/log',node='worker-node-7'} > 90%",
                "[09:30:05] INFO  logrotate: Last rotation was 48 hours ago (expected: 24h)",
            ],
            "metrics": {
                "disk_usage_percent": 92,
                "disk_total_gb": 100,
                "disk_available_gb": 8,
                "growth_rate_gb_per_hour": 2.1,
                "services_on_node": ["cache-warmer", "log-aggregator", "metrics-proxy"],
            },
            "recent_changes": "logrotate cron job failed silently 48 hours ago after OS patch.",
        },
    },
    {
        "id": "INC-004",
        "title": "SSL Certificate Expiry Warning",
        "summary": "Certificate for api.internal.company.com expires in 48 hours",
        "details": {
            "alert_source": "CertManager",
            "triggered_at": "2024-03-15T08:00:00Z",
            "service": "internal-api",
            "environment": "production",
            "logs": [
                "[08:00:00] WARN  cert-manager: Certificate for api.internal.company.com expires 2024-03-17T08:00:00Z",
                "[08:00:01] INFO  cert-manager: Auto-renewal attempted but failed — ACME challenge DNS timeout",
                "[08:00:05] WARN  cert-manager: 12 services depend on this certificate",
            ],
            "metrics": {
                "hours_until_expiry": 48,
                "dependent_services": 12,
                "renewal_attempts_failed": 3,
                "last_successful_renewal": "2023-12-15",
            },
            "recent_changes": "DNS provider changed from Route53 to Cloudflare 1 week ago; ACME config not updated.",
        },
    },
]

HARD_INCIDENTS: List[Dict[str, Any]] = [
    {
        "id": "INC-005",
        "title": "Kubernetes Pod CrashLoopBackOff",
        "summary": "order-processor pods OOMKilled; 3 of 5 replicas down in production namespace",
        "details": {
            "alert_source": "Kubernetes",
            "triggered_at": "2024-03-15T16:05:00Z",
            "service": "order-processor",
            "environment": "production",
            "logs": [
                "[16:03:22] WARN  kubelet: Container order-processor memory usage 480Mi/512Mi (93%)",
                "[16:04:01] ERROR kubelet: OOMKilled container order-processor in pod order-processor-7b9f4",
                "[16:04:15] WARN  kubelet: Pod order-processor-7b9f4 entering CrashLoopBackOff",
                "[16:04:30] ERROR kubelet: OOMKilled container order-processor in pod order-processor-a2c81",
                "[16:05:00] ALERT k8s: 3/5 replicas of deployment/order-processor unavailable",
            ],
            "metrics": {
                "replicas_available": 2,
                "replicas_desired": 5,
                "memory_limit_mi": 512,
                "peak_memory_mi": 510,
                "restart_count_last_hour": 14,
                "order_processing_backlog": 847,
            },
            "recent_changes": "order-processor v4.1.0 deployed 45 min ago (added in-memory caching for product catalog).",
        },
    },
    {
        "id": "INC-006",
        "title": "Redis Cluster Node Failure",
        "summary": "redis-node-3 not responding; cluster degraded to 2/3 nodes; read latency spiking",
        "details": {
            "alert_source": "Redis Sentinel",
            "triggered_at": "2024-03-15T16:02:00Z",
            "service": "redis-cluster",
            "environment": "production",
            "logs": [
                "[16:01:30] WARN  redis-sentinel: redis-node-3 not responding to PING for 15s",
                "[16:01:45] ERROR redis-sentinel: redis-node-3 marked as SDOWN (subjectively down)",
                "[16:02:00] ERROR redis-sentinel: redis-node-3 marked as ODOWN (objectively down) — quorum reached",
                "[16:02:10] WARN  redis-cluster: Resharding slots from node-3 to surviving nodes",
                "[16:02:30] WARN  redis-cluster: Read latency increased 3x due to redistribution",
            ],
            "metrics": {
                "nodes_healthy": 2,
                "nodes_total": 3,
                "read_latency_ms": 45,
                "normal_read_latency_ms": 12,
                "cache_hit_rate_percent": 67,
                "normal_cache_hit_rate_percent": 94,
                "affected_services": [
                    "session-manager",
                    "rate-limiter",
                    "checkout-service",
                    "recommendation-engine",
                ],
            },
            "recent_changes": "No recent changes to Redis. Node-3 hardware is 18 months old.",
        },
    },
    {
        "id": "INC-007",
        "title": "Anomalous Traffic Spike",
        "summary": "10x normal request rate on /api/v2/search from single IP range; rate limiting partially engaged",
        "details": {
            "alert_source": "Cloudflare WAF",
            "triggered_at": "2024-03-15T16:10:00Z",
            "service": "search-api",
            "environment": "production",
            "logs": [
                "[16:08:00] WARN  cloudflare: Unusual traffic pattern detected on /api/v2/search",
                "[16:09:00] WARN  cloudflare: Request rate from 182.45.0.0/24 at 15,000 req/min (normal: 1,500)",
                "[16:09:30] INFO  cloudflare: Rate limiting engaged — 60% of requests from range being challenged",
                "[16:10:00] ALERT cloudflare: Traffic anomaly — DDoS characteristics detected",
                "[16:10:15] WARN  search-api: Response times elevated but service operational",
            ],
            "metrics": {
                "requests_per_minute": 15000,
                "normal_requests_per_minute": 1500,
                "source_ip_range": "182.45.0.0/24",
                "rate_limited_percent": 60,
                "search_latency_ms": 340,
                "normal_search_latency_ms": 85,
            },
            "recent_changes": "No service changes. Similar pattern seen 2 months ago from different IP range.",
        },
    },
    {
        "id": "INC-008",
        "title": "Failed Deployment Rollback",
        "summary": "deployment/recommendation-engine stuck mid-rollback; previous image not in registry",
        "details": {
            "alert_source": "ArgoCD",
            "triggered_at": "2024-03-15T16:15:00Z",
            "service": "recommendation-engine",
            "environment": "production",
            "logs": [
                "[16:12:00] INFO  argocd: Rollback initiated for recommendation-engine to v3.8.2",
                "[16:12:30] ERROR argocd: Image pull failed — registry.company.com/recom-engine:v3.8.2 not found",
                "[16:13:00] ERROR kubelet: Failed to pull image: manifest not found",
                "[16:14:00] WARN  argocd: Rollback stuck — 0/3 new replicas ready",
                "[16:15:00] ALERT argocd: Deployment recommendation-engine in degraded state",
            ],
            "metrics": {
                "replicas_available": 2,
                "replicas_desired": 3,
                "rollback_target_version": "v3.8.2",
                "current_version": "v3.9.0",
                "image_registry_status": "v3.8.2 purged by retention policy",
            },
            "recent_changes": (
                "recommendation-engine v3.9.0 deployed 30 min ago. "
                "v3.8.2 image was garbage-collected by registry retention (>30 day old tags)."
            ),
        },
    },
    {
        "id": "INC-009",
        "title": "Monitoring Alert Storm — checkout-service",
        "summary": "47 alerts fired for checkout-service in 3 minutes; alert correlation suggests single root cause",
        "details": {
            "alert_source": "Grafana OnCall",
            "triggered_at": "2024-03-15T16:04:00Z",
            "service": "checkout-service",
            "environment": "production",
            "logs": [
                "[16:03:00] ALERT grafana: checkout-service latency > 2000ms",
                "[16:03:05] ALERT grafana: checkout-service error rate > 10%",
                "[16:03:10] ALERT grafana: checkout-service cache miss rate > 50%",
                "[16:03:15] ALERT grafana: checkout-service session lookup failures",
                "[16:03:30] WARN  grafana: 47 alerts correlated — probable cascade from upstream dependency",
                "[16:04:00] INFO  grafana: checkout-service depends on: redis-cluster, payment-service, inventory-api",
            ],
            "metrics": {
                "alert_count_3min": 47,
                "checkout_latency_ms": 3200,
                "normal_checkout_latency_ms": 350,
                "cache_miss_rate_percent": 58,
                "session_lookup_failure_rate_percent": 42,
                "upstream_dependencies": [
                    "redis-cluster",
                    "payment-service",
                    "inventory-api",
                ],
            },
            "recent_changes": "No changes to checkout-service. Depends heavily on Redis for session and cache data.",
        },
    },
]


# ---------------------------------------------------------------------------
# Expected answers per task
# ---------------------------------------------------------------------------

EXPECTED_EASY: Dict[str, Dict[str, Any]] = {
    "INC-001": {
        "severity": "P1",
        "category": "database",
        "team": "database-team",
        "key_actions": [
            "connection pool",
            "increase",
            "payment-service",
            "rollback",
            "batch",
        ],
    },
}

EXPECTED_MEDIUM: Dict[str, Dict[str, Any]] = {
    "INC-002": {
        "severity": "P1",
        "category": "api",
        "team": "platform-team",
        "key_actions": [
            "order-service",
            "circuit breaker",
            "upstream",
            "timeout",
            "restart",
        ],
    },
    "INC-003": {
        "severity": "P3",
        "category": "infrastructure",
        "team": "infra-team",
        "key_actions": [
            "logrotate",
            "disk",
            "clean",
            "rotation",
            "cron",
        ],
    },
    "INC-004": {
        "severity": "P2",
        "category": "security",
        "team": "security-team",
        "key_actions": [
            "certificate",
            "renew",
            "ACME",
            "DNS",
            "cloudflare",
        ],
    },
}

EXPECTED_HARD: Dict[str, Dict[str, Any]] = {
    "INC-005": {
        "severity": "P1",
        "category": "application",
        "team": "backend-team",
        "key_actions": [
            "memory",
            "OOM",
            "rollback",
            "v4.1.0",
            "cache",
            "limit",
        ],
    },
    "INC-006": {
        "severity": "P1",
        "category": "database",
        "team": "database-team",
        "key_actions": [
            "redis",
            "node",
            "replace",
            "failover",
            "hardware",
        ],
    },
    "INC-007": {
        "severity": "P2",
        "category": "security",
        "team": "security-team",
        "key_actions": [
            "block",
            "rate limit",
            "IP",
            "DDoS",
            "WAF",
            "firewall",
        ],
    },
    "INC-008": {
        "severity": "P2",
        "category": "deployment",
        "team": "platform-team",
        "key_actions": [
            "image",
            "registry",
            "rebuild",
            "deploy",
            "rollback",
            "v3.8",
        ],
    },
    "INC-009": {
        "severity": "P3",
        "category": "monitoring",
        "team": "sre-team",
        "key_actions": [
            "cascade",
            "redis",
            "root cause",
            "INC-006",
            "upstream",
            "dependency",
        ],
    },
}


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
    },
    "hard": {
        "name": "Cascading Failure Investigation",
        "description": (
            "Triage five production incidents including cascading failures and red herrings. "
            "Some incidents are symptoms of others — identify root causes vs. downstream effects. "
            "Correctly prioritize, categorize, assign, and recommend actions for all incidents."
        ),
        "max_steps": 50,
        "incidents": HARD_INCIDENTS,
        "expected": EXPECTED_HARD,
    },
}


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
    # Need at least 2 keyword hits for any credit
    if hits < 2:
        return 0.0
    return min(hits / max(len(key_actions), 1), 1.0)


def grade_task(
    task_id: str,
    decisions: Dict[str, Dict[str, Any]],
    steps_taken: int,
) -> float:
    """
    Grade a completed task. Returns a score between 0.0 and 1.0.

    Weights:
      - Severity correctness: 0.30
      - Category correctness: 0.25
      - Team assignment: 0.25
      - Action item quality: 0.15
      - Efficiency bonus: 0.05

    Args:
        task_id: Identifier for the task.
        decisions: Dict mapping incident_id to the agent's triage decisions.
        steps_taken: Number of steps the agent used.

    Returns:
        Float score 0.0–1.0.
    """
    task = TASKS.get(task_id)
    if task is None:
        return 0.0

    expected = task["expected"]
    max_steps = task["max_steps"]

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

    # Efficiency bonus: full bonus if ≤ half max_steps, zero if at max
    if max_steps > 0 and steps_taken <= max_steps:
        ratio = steps_taken / max_steps
        efficiency = max(0.0, 1.0 - ratio) if ratio > 0.5 else 1.0
    else:
        efficiency = 0.0

    score = (
        0.30 * avg_severity
        + 0.25 * avg_category
        + 0.25 * avg_team
        + 0.15 * avg_actions
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

    Returns a small positive reward when the agent makes a correct decision,
    a small negative reward for incorrect ones, and zero for neutral actions.
    """
    task = TASKS.get(task_id)
    if task is None:
        return 0.0

    expected = task["expected"]
    reward = 0.0

    for inc_id, exp in expected.items():
        curr = decisions.get(inc_id, {})
        prev = prev_decisions.get(inc_id, {})

        # Check each field for new decisions
        for field, grader, weight in [
            ("severity", _grade_severity, 0.06),
            ("category", _grade_category, 0.05),
            ("team", _grade_team, 0.05),
        ]:
            curr_val = curr.get(field, "")
            prev_val = prev.get(field, "")
            if curr_val and curr_val != prev_val:
                grade = grader(curr_val, exp[field])
                reward += weight * (grade * 2 - 1)  # Maps 0→-weight, 0.5→0, 1→+weight

        # Action items: small reward for adding relevant ones
        curr_items = curr.get("action_items", [])
        prev_items = prev.get("action_items", [])
        if len(curr_items) > len(prev_items):
            new_item = curr_items[-1] if curr_items else ""
            combined = new_item.lower()
            hits = sum(1 for kw in exp["key_actions"] if kw.lower() in combined)
            if hits > 0:
                reward += 0.02
            else:
                reward -= 0.01

    return round(reward, 4)
