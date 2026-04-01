"""
Large incident pool with diagnostics, dependency chains, and expected answers.

20 diverse production incidents spanning all categories. Each incident includes:
- Surface-level info (visible via inspect)
- Deep diagnostics (revealed only via diagnose command)
- Dependency chain info (which incident caused this one)
- Expected correct answers for grading
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Full incident pool — 20 incidents
# ---------------------------------------------------------------------------

INCIDENT_POOL: List[Dict[str, Any]] = [
    # ── INC-001: Database Connection Pool Exhaustion ──────────────────
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
        "diagnostics": {
            "deep_logs": [
                "[14:18:00] DEBUG payment-service: BatchProcessor initialized with pool_size=20",
                "[14:18:05] DEBUG payment-service: BatchProcessor acquired 20 dedicated connections",
                "[14:19:30] TRACE postgres: Idle connections from batch_processor: 18 (holding locks)",
                "[14:20:00] TRACE postgres: Connection slot distribution: batch_processor=20, api=45, workers=33",
            ],
            "root_cause_analysis": (
                "The new batch processing feature (v2.3.1) acquires 20 dedicated connections at startup "
                "and never releases them, even when idle. This reduced available pool from 100 to 80, "
                "while normal load requires ~85 connections. Fix: configure batch processor to use a "
                "shared connection pool or reduce its dedicated pool size."
            ),
            "heap_dump": None,
            "network_trace": None,
        },
        "caused_by": None,
        "escalation_rate": 3,  # escalates every 3 steps
        "expected": {
            "severity": "P1",
            "category": "database",
            "team": "database-team",
            "key_actions": ["connection pool", "increase", "payment-service", "rollback", "batch"],
        },
    },
    # ── INC-002: API Gateway Timeout Spike ────────────────────────────
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
        "diagnostics": {
            "deep_logs": [
                "[09:08:00] DEBUG order-service: GC pause detected — 4200ms stop-the-world",
                "[09:09:10] DEBUG order-service: Heap usage 94% — approaching OOM",
                "[09:10:00] TRACE api-gateway: Connection pool to order-service: 48/50 in use",
                "[09:10:30] DEBUG order-service: Thread pool exhausted — 200 threads all blocked on DB query",
            ],
            "root_cause_analysis": (
                "order-service is experiencing garbage collection pauses and thread pool exhaustion. "
                "A slow database query (missing index on orders.created_at) is blocking threads, "
                "causing cascading timeouts at the API gateway. The circuit breaker correctly opened "
                "to prevent total failure. Fix: add missing index, increase thread pool, restart order-service."
            ),
            "heap_dump": "order-service heap: 94% used, largest objects: OrderCache (2.1GB), DBConnectionPool (800MB)",
            "network_trace": None,
        },
        "caused_by": None,
        "escalation_rate": 3,
        "expected": {
            "severity": "P1",
            "category": "api",
            "team": "platform-team",
            "key_actions": ["order-service", "circuit breaker", "upstream", "timeout", "restart"],
        },
    },
    # ── INC-003: Disk Space Warning ───────────────────────────────────
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
        "diagnostics": {
            "deep_logs": [
                "[09:25:00] DEBUG logrotate: /etc/cron.d/logrotate — last modified 48h ago during OS patch",
                "[09:25:01] DEBUG logrotate: Error in cron: /usr/sbin/logrotate: permission denied (SELinux context changed)",
                "[09:25:05] TRACE node-exporter: Largest files in /var/log: syslog (42GB), auth.log (18GB), kern.log (12GB)",
            ],
            "root_cause_analysis": (
                "OS security patch changed SELinux contexts, breaking logrotate's permissions. "
                "Logs have been accumulating for 48 hours. At current rate (~2GB/hr), disk will be "
                "full in ~4 hours. Fix: restore SELinux context for logrotate, run manual rotation, "
                "clean old logs."
            ),
            "heap_dump": None,
            "network_trace": None,
        },
        "caused_by": None,
        "escalation_rate": 8,
        "expected": {
            "severity": "P3",
            "category": "infrastructure",
            "team": "infra-team",
            "key_actions": ["logrotate", "disk", "clean", "rotation", "cron"],
        },
    },
    # ── INC-004: SSL Certificate Expiry ───────────────────────────────
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
        "diagnostics": {
            "deep_logs": [
                "[07:55:00] DEBUG cert-manager: ACME challenge initiated for api.internal.company.com",
                "[07:55:05] DEBUG cert-manager: DNS-01 challenge: creating TXT record via Route53 API",
                "[07:55:10] ERROR cert-manager: Route53 API returned 403 — credentials revoked",
                "[07:55:15] DEBUG cert-manager: Fallback: attempting HTTP-01 challenge",
                "[07:55:30] ERROR cert-manager: HTTP-01 failed — /.well-known/acme-challenge not reachable (firewall rule)",
            ],
            "root_cause_analysis": (
                "DNS provider was migrated from Route53 to Cloudflare but cert-manager ACME config "
                "still references Route53 API. The old Route53 credentials were revoked during migration. "
                "HTTP-01 challenge also fails due to firewall rules. Fix: update cert-manager to use "
                "Cloudflare DNS API credentials for DNS-01 challenges."
            ),
            "heap_dump": None,
            "network_trace": None,
        },
        "caused_by": None,
        "escalation_rate": 10,
        "expected": {
            "severity": "P2",
            "category": "security",
            "team": "security-team",
            "key_actions": ["certificate", "renew", "ACME", "DNS", "cloudflare"],
        },
    },
    # ── INC-005: Kubernetes Pod CrashLoopBackOff ──────────────────────
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
        "diagnostics": {
            "deep_logs": [
                "[16:02:00] DEBUG order-processor: ProductCatalogCache initialized — loading 45,000 items into memory",
                "[16:02:15] DEBUG order-processor: Cache size: 380Mi — exceeds expected 50Mi",
                "[16:02:30] TRACE order-processor: Cache key pattern: full product objects with images (base64 encoded)",
                "[16:03:00] DEBUG order-processor: Total heap: 490Mi = Cache(380Mi) + App(90Mi) + Overhead(20Mi)",
            ],
            "root_cause_analysis": (
                "v4.1.0's in-memory product catalog cache loads full product objects including base64-encoded "
                "images, consuming 380Mi per pod. With the 512Mi limit, only 132Mi remains for the application. "
                "Fix: rollback to v4.0.x, then fix cache to store only IDs/metadata (not images), "
                "or increase memory limit to 1Gi."
            ),
            "heap_dump": "order-processor heap: ProductCatalogCache=380Mi, OrderQueue=45Mi, gRPC=30Mi, Other=35Mi",
            "network_trace": None,
        },
        "caused_by": None,
        "escalation_rate": 3,
        "expected": {
            "severity": "P1",
            "category": "application",
            "team": "backend-team",
            "key_actions": ["memory", "OOM", "rollback", "v4.1.0", "cache", "limit"],
        },
    },
    # ── INC-006: Redis Cluster Node Failure ───────────────────────────
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
                "affected_services": ["session-manager", "rate-limiter", "checkout-service", "recommendation-engine"],
            },
            "recent_changes": "No recent changes to Redis. Node-3 hardware is 18 months old.",
        },
        "diagnostics": {
            "deep_logs": [
                "[16:00:00] DEBUG redis-node-3: SMART status: /dev/sda — Reallocated_Sector_Ct=148 (threshold=100)",
                "[16:00:30] ERROR redis-node-3: Disk I/O error on RDB save — Input/output error",
                "[16:01:00] FATAL redis-node-3: Background save failure — aborting",
                "[16:01:15] DEBUG redis-sentinel: Last successful RDB save on node-3: 2 hours ago",
            ],
            "root_cause_analysis": (
                "redis-node-3 suffered a disk hardware failure (SSD degradation — SMART errors above threshold). "
                "The RDB persistence failed, and the node crashed. The 18-month-old hardware was past its "
                "expected SSD lifespan. Fix: replace node-3 hardware, resync data from surviving replicas, "
                "add SMART monitoring alerts."
            ),
            "heap_dump": None,
            "network_trace": None,
        },
        "caused_by": None,
        "escalation_rate": 4,
        "expected": {
            "severity": "P1",
            "category": "database",
            "team": "database-team",
            "key_actions": ["redis", "node", "replace", "failover", "hardware"],
        },
    },
    # ── INC-007: Anomalous Traffic Spike ──────────────────────────────
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
        "diagnostics": {
            "deep_logs": [
                "[16:08:30] DEBUG cloudflare: User-Agent analysis: 95% of traffic from range uses 'python-requests/2.28'",
                "[16:09:00] DEBUG cloudflare: Request pattern: sequential product ID enumeration (id=1, id=2, id=3...)",
                "[16:09:15] TRACE search-api: No authentication tokens in requests from 182.45.0.0/24",
                "[16:09:45] DEBUG cloudflare: GeoIP: 182.45.0.0/24 maps to hosting provider (not residential)",
            ],
            "root_cause_analysis": (
                "Automated scraping attack from a hosting provider IP range. The attacker is sequentially "
                "enumerating product data via the search API. Not a volumetric DDoS but a data scraping "
                "operation. Fix: block the IP range at WAF, add authentication requirement for search API, "
                "implement anti-bot challenges."
            ),
            "heap_dump": None,
            "network_trace": "182.45.0.0/24 → search-api: 15k req/min, avg payload 2KB, all GET requests",
        },
        "caused_by": None,
        "escalation_rate": 5,
        "expected": {
            "severity": "P2",
            "category": "security",
            "team": "security-team",
            "key_actions": ["block", "rate limit", "IP", "DDoS", "WAF", "firewall"],
        },
    },
    # ── INC-008: Failed Deployment Rollback ───────────────────────────
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
        "diagnostics": {
            "deep_logs": [
                "[16:11:00] DEBUG argocd: Rollback reason: recommendation-engine v3.9.0 health checks failing",
                "[16:11:05] DEBUG argocd: v3.9.0 error: numpy version conflict — model inference returning NaN",
                "[16:12:30] TRACE registry: Image lookup: recom-engine:v3.8.2 — deleted 2024-03-01 by retention policy",
                "[16:12:35] TRACE registry: Available tags: v3.9.0, v3.8.5-hotfix, v3.7.0",
            ],
            "root_cause_analysis": (
                "v3.9.0 has a numpy version conflict causing model inference failures. Rollback to v3.8.2 "
                "failed because the image was garbage-collected. However, v3.8.5-hotfix is available in the "
                "registry and is a patched version of v3.8.x. Fix: rollback to v3.8.5-hotfix, then fix the "
                "numpy dependency in v3.9.1. Also update retention policy to keep at least 2 recent stable tags."
            ),
            "heap_dump": None,
            "network_trace": None,
        },
        "caused_by": None,
        "escalation_rate": 5,
        "expected": {
            "severity": "P2",
            "category": "deployment",
            "team": "platform-team",
            "key_actions": ["image", "registry", "rebuild", "deploy", "rollback", "v3.8"],
        },
    },
    # ── INC-009: Monitoring Alert Storm (CASCADE from INC-006) ────────
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
                "upstream_dependencies": ["redis-cluster", "payment-service", "inventory-api"],
            },
            "recent_changes": "No changes to checkout-service. Depends heavily on Redis for session and cache data.",
        },
        "diagnostics": {
            "deep_logs": [
                "[16:02:10] TRACE checkout-service: Redis connection to node-3 failed — connection refused",
                "[16:02:15] DEBUG checkout-service: Falling back to node-1 and node-2 for cache reads",
                "[16:02:30] DEBUG checkout-service: Cache miss rate spiked because node-3 held 33% of keyspace",
                "[16:03:00] TRACE checkout-service: Session store reads timing out — Redis overloaded from redistribution",
            ],
            "root_cause_analysis": (
                "This is NOT a checkout-service issue. The alert storm is caused by INC-006 (Redis node-3 failure). "
                "checkout-service depends on redis-cluster for session management and caching. When node-3 went "
                "down, 33% of cached keys became unavailable, causing cache miss spikes and session failures. "
                "Fix: resolve INC-006 first. checkout-service will recover automatically once Redis is healthy."
            ),
            "heap_dump": None,
            "network_trace": None,
        },
        "caused_by": "INC-006",
        "escalation_rate": 6,
        "expected": {
            "severity": "P3",
            "category": "monitoring",
            "team": "sre-team",
            "key_actions": ["cascade", "redis", "root cause", "INC-006", "upstream", "dependency"],
        },
    },
    # ── INC-010: Memory Leak in User Service ──────────────────────────
    {
        "id": "INC-010",
        "title": "Memory Leak in User Service",
        "summary": "user-service memory usage growing linearly; currently at 78% after 6 hours uptime",
        "details": {
            "alert_source": "Prometheus",
            "triggered_at": "2024-03-15T18:00:00Z",
            "service": "user-service",
            "environment": "production",
            "logs": [
                "[17:50:00] WARN  prometheus: user-service memory usage 72% and rising",
                "[17:55:00] WARN  prometheus: user-service memory usage 75% — linear growth detected",
                "[18:00:00] ALERT prometheus: user-service memory > 75% threshold, growth rate 4%/hour",
                "[18:00:05] INFO  user-service: GC frequency increased — full GC every 90s (normal: 300s)",
            ],
            "metrics": {
                "memory_usage_percent": 78,
                "memory_limit_mb": 2048,
                "uptime_hours": 6,
                "growth_rate_percent_per_hour": 4,
                "gc_frequency_seconds": 90,
                "normal_gc_frequency_seconds": 300,
                "active_user_sessions": 12400,
                "response_time_ms": 180,
                "normal_response_time_ms": 45,
            },
            "recent_changes": "user-service v5.2.0 deployed 6 hours ago (added user activity tracking middleware).",
        },
        "diagnostics": {
            "deep_logs": [
                "[12:00:00] DEBUG user-service: ActivityTracker middleware initialized",
                "[12:05:00] TRACE user-service: ActivityTracker storing request history in-memory HashMap",
                "[15:00:00] TRACE user-service: ActivityTracker HashMap size: 2.4M entries (no TTL configured)",
                "[17:00:00] DEBUG user-service: ActivityTracker HashMap size: 8.1M entries — never evicted",
                "[18:00:00] TRACE user-service: Heap breakdown — ActivityTracker: 1.2GB, UserCache: 200MB, Other: 180MB",
            ],
            "root_cause_analysis": (
                "The v5.2.0 user activity tracking middleware stores every request in an unbounded "
                "in-memory HashMap with no TTL or eviction policy. After 6 hours, it held 8.1M entries "
                "consuming 1.2GB. At current growth, OOM will occur in ~5.5 hours. "
                "Fix: add TTL-based eviction to ActivityTracker, or move tracking to Redis/Kafka. "
                "Immediate: restart pods to reset memory, then deploy hotfix."
            ),
            "heap_dump": "user-service heap: ActivityTracker=1.2GB, UserCache=200MB, gRPC=120MB, Netty=60MB",
            "network_trace": None,
        },
        "caused_by": None,
        "escalation_rate": 4,
        "expected": {
            "severity": "P2",
            "category": "application",
            "team": "backend-team",
            "key_actions": ["memory", "leak", "restart", "v5.2.0", "activity", "TTL", "eviction"],
        },
    },
    # ── INC-011: Network Partition Between Zones ──────────────────────
    {
        "id": "INC-011",
        "title": "Network Partition Between Availability Zones",
        "summary": "Intermittent packet loss between us-east-1a and us-east-1c; cross-zone latency 10x normal",
        "details": {
            "alert_source": "AWS CloudWatch",
            "triggered_at": "2024-03-15T17:30:00Z",
            "service": "vpc-networking",
            "environment": "production",
            "logs": [
                "[17:25:00] WARN  cloudwatch: Cross-AZ packet loss us-east-1a → us-east-1c: 15%",
                "[17:27:00] ERROR cloudwatch: Cross-AZ latency spike: 120ms (normal: 1.2ms)",
                "[17:28:00] WARN  cloudwatch: BGP route flapping detected on transit gateway",
                "[17:29:00] ALERT cloudwatch: Multiple services reporting cross-zone communication failures",
                "[17:30:00] ERROR cloudwatch: ELB health checks failing for targets in us-east-1c from us-east-1a",
            ],
            "metrics": {
                "packet_loss_percent": 15,
                "cross_zone_latency_ms": 120,
                "normal_cross_zone_latency_ms": 1.2,
                "bgp_route_flaps_last_5min": 23,
                "affected_az_pair": "us-east-1a ↔ us-east-1c",
                "services_affected": 34,
            },
            "recent_changes": "AWS reported no maintenance windows. Network ACL changes made 2 hours ago for compliance.",
        },
        "diagnostics": {
            "deep_logs": [
                "[17:20:00] DEBUG vpc-flow: Network ACL acl-0x4f changed: added DENY rule for 10.0.128.0/17 (us-east-1c CIDR)",
                "[17:20:05] DEBUG vpc-flow: Rule priority 50 — overrides ALLOW at priority 100",
                "[17:22:00] TRACE transit-gw: BGP session flapping due to unreachable next-hop in us-east-1c",
                "[17:25:00] DEBUG vpc-flow: Partial traffic allowed via secondary route through us-east-1b (adds 100ms)",
            ],
            "root_cause_analysis": (
                "A network ACL change made 2 hours ago for compliance accidentally added a DENY rule "
                "for the entire us-east-1c CIDR block (10.0.128.0/17) at priority 50, overriding the "
                "ALLOW rule at priority 100. Some traffic routes through us-east-1b as a fallback, "
                "explaining intermittent (not total) failure. Fix: remove the incorrect DENY rule from "
                "the network ACL immediately."
            ),
            "heap_dump": None,
            "network_trace": "us-east-1a → us-east-1c: 15% packet loss, 85% routed via us-east-1b (+100ms)",
        },
        "caused_by": None,
        "escalation_rate": 2,
        "expected": {
            "severity": "P1",
            "category": "network",
            "team": "infra-team",
            "key_actions": ["network ACL", "DENY", "remove", "rule", "AZ", "partition", "BGP"],
        },
    },
    # ── INC-012: Elasticsearch Cluster Yellow ─────────────────────────
    {
        "id": "INC-012",
        "title": "Elasticsearch Cluster Yellow Status",
        "summary": "3 unassigned shards after node restart; search queries degraded but functional",
        "details": {
            "alert_source": "Elastic Cloud",
            "triggered_at": "2024-03-15T11:00:00Z",
            "service": "elasticsearch-prod",
            "environment": "production",
            "logs": [
                "[10:55:00] INFO  elasticsearch: Node es-data-4 restarted after JVM upgrade",
                "[10:56:00] WARN  elasticsearch: 3 unassigned shards detected — cluster status YELLOW",
                "[10:57:00] INFO  elasticsearch: Shard allocation in progress — ETA 15 minutes",
                "[11:00:00] ALERT elastic-cloud: Cluster status YELLOW > 5 minute threshold",
            ],
            "metrics": {
                "cluster_status": "YELLOW",
                "unassigned_shards": 3,
                "total_shards": 450,
                "search_latency_ms": 85,
                "normal_search_latency_ms": 40,
                "indexing_rate_docs_per_sec": 8500,
            },
            "recent_changes": "es-data-4 restarted 5 minutes ago for JVM 17→21 upgrade. Rolling restart planned.",
        },
        "diagnostics": {
            "deep_logs": [
                "[10:55:30] DEBUG elasticsearch: es-data-4 rejoined cluster with empty data directory",
                "[10:56:00] DEBUG elasticsearch: Shard reallocation: shards 12, 87, 203 unassigned — waiting for disk watermark",
                "[10:56:30] TRACE elasticsearch: es-data-4 disk usage 82% — above high watermark (85% would block allocation)",
                "[10:57:00] DEBUG elasticsearch: Auto-rebalancing from es-data-1, es-data-2, es-data-3 to es-data-4",
            ],
            "root_cause_analysis": (
                "Expected behavior during rolling JVM upgrade. es-data-4 restarted with clean state and "
                "is receiving shard re-allocations. The 3 unassigned shards will be assigned once disk "
                "rebalancing completes (~15 min). No data loss — shards have replicas on other nodes. "
                "Fix: wait for auto-recovery. If shards remain unassigned after 30 min, manually reroute."
            ),
            "heap_dump": None,
            "network_trace": None,
        },
        "caused_by": None,
        "escalation_rate": 15,
        "expected": {
            "severity": "P3",
            "category": "database",
            "team": "database-team",
            "key_actions": ["shard", "allocation", "wait", "recovery", "JVM", "restart"],
        },
    },
    # ── INC-013: CI/CD Pipeline Deadlock ──────────────────────────────
    {
        "id": "INC-013",
        "title": "CI/CD Pipeline Build Queue Deadlock",
        "summary": "Jenkins build queue at 47 pending jobs; all 8 executors stuck on integration tests",
        "details": {
            "alert_source": "Jenkins",
            "triggered_at": "2024-03-15T14:00:00Z",
            "service": "ci-cd-pipeline",
            "environment": "staging",
            "logs": [
                "[13:45:00] WARN  jenkins: Build queue depth: 35 (threshold: 20)",
                "[13:50:00] WARN  jenkins: All 8 executors occupied for > 30 minutes",
                "[13:55:00] ERROR jenkins: Integration test suite for payment-service hanging on DB lock",
                "[14:00:00] ALERT jenkins: Pipeline deadlock detected — no executors freed in 45 minutes",
            ],
            "metrics": {
                "queue_depth": 47,
                "executors_total": 8,
                "executors_stuck": 8,
                "avg_build_time_minutes": 12,
                "current_build_time_minutes": 55,
                "production_impact": "none — staging only",
            },
            "recent_changes": "payment-service integration test added new DB migration test that acquires exclusive lock.",
        },
        "diagnostics": {
            "deep_logs": [
                "[13:40:00] DEBUG jenkins: payment-service test: acquired exclusive lock on test_payments table",
                "[13:40:05] DEBUG jenkins: order-service test: waiting for shared lock on test_payments table",
                "[13:40:10] DEBUG jenkins: payment-service test: waiting for lock on test_orders table (held by order-service test)",
                "[13:45:00] TRACE jenkins: Classic deadlock: payment-service holds test_payments, needs test_orders; order-service holds test_orders, needs test_payments",
            ],
            "root_cause_analysis": (
                "Classic database deadlock in integration tests. payment-service's new migration test "
                "acquires exclusive lock on test_payments, while order-service test holds test_orders "
                "and needs test_payments. Neither can proceed. All 8 executors are running variants of "
                "these tests. Fix: kill stuck builds, add lock ordering to tests, use separate schemas "
                "per test suite."
            ),
            "heap_dump": None,
            "network_trace": None,
        },
        "caused_by": None,
        "escalation_rate": 20,
        "expected": {
            "severity": "P3",
            "category": "deployment",
            "team": "devops-team",
            "key_actions": ["deadlock", "build", "kill", "test", "lock", "pipeline", "queue"],
        },
    },
    # ── INC-014: JWT Token Validation Failures ────────────────────────
    {
        "id": "INC-014",
        "title": "JWT Token Validation Failures",
        "summary": "30% of API requests failing authentication; JWKS endpoint returning stale keys",
        "details": {
            "alert_source": "Auth0",
            "triggered_at": "2024-03-15T10:30:00Z",
            "service": "auth-gateway",
            "environment": "production",
            "logs": [
                "[10:25:00] WARN  auth-gateway: JWT validation failure rate 15% and rising",
                "[10:27:00] ERROR auth-gateway: JWKS endpoint returned keys not matching token kid='key-2024-03'",
                "[10:28:00] ERROR auth-gateway: 401 Unauthorized returned to 847 requests in last 60s",
                "[10:29:00] WARN  auth-gateway: Cached JWKS last refreshed 45 minutes ago",
                "[10:30:00] ALERT auth0: Authentication failures > 25% threshold",
            ],
            "metrics": {
                "auth_failure_rate_percent": 30,
                "requests_per_minute": 3200,
                "failed_auth_per_minute": 960,
                "jwks_cache_age_minutes": 45,
                "affected_services": 8,
                "affected_users_estimate": 6000,
            },
            "recent_changes": "Auth0 tenant key rotation performed 1 hour ago as part of security audit.",
        },
        "diagnostics": {
            "deep_logs": [
                "[09:30:00] INFO  auth0: Key rotation initiated — new key kid='key-2024-03' created",
                "[09:30:05] INFO  auth0: Old key kid='key-2024-02' marked for deprecation (grace period: 24h)",
                "[09:45:00] DEBUG auth-gateway: JWKS cache refresh — fetched 2 keys (key-2024-02, key-2024-03)",
                "[10:00:00] ERROR auth0: Key rotation error — kid='key-2024-02' accidentally deleted instead of deprecated",
                "[10:15:00] DEBUG auth-gateway: Tokens signed with key-2024-02 now fail validation (key missing from JWKS)",
            ],
            "root_cause_analysis": (
                "During Auth0 key rotation, the old key (key-2024-02) was accidentally deleted immediately "
                "instead of being deprecated with a 24h grace period. Users with tokens signed by the old "
                "key can't validate. The 30% failure rate matches the proportion of users with old tokens. "
                "Fix: re-add old key to JWKS with deprecation, or force all affected users to re-authenticate."
            ),
            "heap_dump": None,
            "network_trace": None,
        },
        "caused_by": None,
        "escalation_rate": 2,
        "expected": {
            "severity": "P1",
            "category": "security",
            "team": "security-team",
            "key_actions": ["JWT", "key", "rotation", "JWKS", "restore", "authentication", "token"],
        },
    },
    # ── INC-015: Kafka Consumer Lag (RED HERRING — app not infra) ─────
    {
        "id": "INC-015",
        "title": "Kafka Consumer Lag Spike",
        "summary": "order-events consumer group lag at 500K messages; processing speed dropped 90%",
        "details": {
            "alert_source": "Confluent",
            "triggered_at": "2024-03-15T13:00:00Z",
            "service": "order-events-consumer",
            "environment": "production",
            "logs": [
                "[12:45:00] WARN  kafka: Consumer group order-events lag increasing: 100K messages",
                "[12:50:00] WARN  kafka: Consumer group order-events lag: 250K messages",
                "[12:55:00] ERROR kafka: Consumer group order-events processing rate: 200 msg/s (normal: 2000 msg/s)",
                "[13:00:00] ALERT confluent: Consumer lag > 400K threshold — order-events group",
            ],
            "metrics": {
                "consumer_lag_messages": 500000,
                "processing_rate_per_sec": 200,
                "normal_processing_rate_per_sec": 2000,
                "partition_count": 12,
                "consumer_instances": 4,
                "broker_health": "all 5 brokers healthy",
            },
            "recent_changes": "order-events-consumer v2.1.0 deployed 2 hours ago (changed Avro to Protobuf serialization).",
        },
        "diagnostics": {
            "deep_logs": [
                "[12:30:00] DEBUG consumer: Protobuf deserialization: avg time 45ms/message (Avro was 2ms/message)",
                "[12:35:00] TRACE consumer: SchemaRegistry lookup per message — not caching compiled schema",
                "[12:40:00] DEBUG consumer: CPU usage 95% on all 4 consumer instances — bottleneck is deserialization",
                "[12:45:00] TRACE consumer: Profiler: 89% of CPU time in ProtobufDeserializer.compile()",
            ],
            "root_cause_analysis": (
                "This looks like a Kafka infrastructure issue but is actually an application bug. "
                "The v2.1.0 migration from Avro to Protobuf has a performance bug: the consumer "
                "recompiles the Protobuf schema for EVERY message instead of caching the compiled "
                "descriptor. This causes 22x slowdown (45ms vs 2ms per message). Kafka brokers are "
                "healthy. Fix: cache the compiled Protobuf schema, or rollback to Avro (v2.0.x)."
            ),
            "heap_dump": "consumer heap: ProtobufDeserializer cache misses=500K, compiled_schemas_in_memory=1 (should be cached)",
            "network_trace": None,
        },
        "caused_by": None,
        "escalation_rate": 4,
        "expected": {
            "severity": "P2",
            "category": "application",
            "team": "backend-team",
            "key_actions": ["consumer", "protobuf", "deserialization", "schema", "cache", "rollback", "v2.1"],
        },
    },
    # ── INC-016: CDN Cache Stampede (CASCADE from INC-011) ────────────
    {
        "id": "INC-016",
        "title": "CDN Cache Stampede",
        "summary": "CDN cache hit rate dropped from 95% to 20%; origin servers at 98% CPU",
        "details": {
            "alert_source": "Cloudflare",
            "triggered_at": "2024-03-15T17:35:00Z",
            "service": "cdn-edge",
            "environment": "production",
            "logs": [
                "[17:32:00] WARN  cloudflare: Cache hit rate dropping — 70% (normal: 95%)",
                "[17:33:00] ERROR cloudflare: Origin pull rate 10x normal — possible cache stampede",
                "[17:34:00] WARN  origin: CPU usage 92% — incoming request rate exceeding capacity",
                "[17:35:00] ALERT cloudflare: Cache hit rate < 30% — all edge PoPs affected",
            ],
            "metrics": {
                "cache_hit_rate_percent": 20,
                "normal_cache_hit_rate_percent": 95,
                "origin_cpu_percent": 98,
                "origin_requests_per_sec": 45000,
                "normal_origin_requests_per_sec": 4500,
                "edge_pops_affected": "all",
            },
            "recent_changes": "No CDN config changes. Network issues reported between availability zones.",
        },
        "diagnostics": {
            "deep_logs": [
                "[17:30:00] DEBUG cloudflare: Cache invalidation event received for pattern '/*' (all content)",
                "[17:30:05] TRACE cloudflare: Invalidation source: automated purge triggered by deployment webhook",
                "[17:30:10] DEBUG cloudflare: Webhook payload origin IP: internal — from us-east-1c (affected zone)",
                "[17:31:00] TRACE cloudflare: The deployment webhook fired due to network timeout being misinterpreted as deploy failure",
            ],
            "root_cause_analysis": (
                "The network partition (INC-011) caused a deployment webhook timeout that was "
                "incorrectly interpreted as a deployment failure. The failure handler triggered a "
                "full CDN cache purge ('/*' pattern). With all caches empty, every request hits origin. "
                "Fix: resolve network partition (INC-011), implement stale-while-revalidate on CDN, "
                "add safety check to prevent wildcard cache purges."
            ),
            "heap_dump": None,
            "network_trace": None,
        },
        "caused_by": "INC-011",
        "escalation_rate": 2,
        "expected": {
            "severity": "P1",
            "category": "network",
            "team": "infra-team",
            "key_actions": ["cache", "purge", "origin", "stale-while-revalidate", "CDN", "stampede", "INC-011"],
        },
    },
    # ── INC-017: Database Replication Lag ─────────────────────────────
    {
        "id": "INC-017",
        "title": "PostgreSQL Replication Lag",
        "summary": "Read replica 3 minutes behind primary; read-after-write inconsistency reported",
        "details": {
            "alert_source": "pganalyze",
            "triggered_at": "2024-03-15T15:00:00Z",
            "service": "postgres-replica-2",
            "environment": "production",
            "logs": [
                "[14:50:00] WARN  postgres-replica-2: Replication lag 90 seconds and increasing",
                "[14:55:00] WARN  postgres-replica-2: Replication lag 150 seconds",
                "[14:58:00] ERROR postgres-replica-2: Write-ahead log (WAL) receiver falling behind",
                "[15:00:00] ALERT pganalyze: Replication lag > 120s threshold on replica-2",
            ],
            "metrics": {
                "replication_lag_seconds": 180,
                "wal_receiver_status": "streaming but slow",
                "replica_cpu_percent": 95,
                "replica_iops": 15000,
                "max_iops": 16000,
                "primary_write_rate_tps": 2800,
                "normal_write_rate_tps": 1200,
            },
            "recent_changes": "Analytics team started large batch job on primary 30 minutes ago (bulk INSERT of historical data).",
        },
        "diagnostics": {
            "deep_logs": [
                "[14:30:00] INFO  primary: Batch job started — INSERT INTO analytics_events SELECT ... (12M rows)",
                "[14:35:00] DEBUG primary: WAL generation rate spiked to 500MB/min (normal: 50MB/min)",
                "[14:40:00] TRACE replica-2: WAL apply rate bottlenecked by disk I/O — 14,800 IOPS near limit",
                "[14:50:00] DEBUG replica-2: IOPS saturation causing WAL apply backup — lag growing linearly",
            ],
            "root_cause_analysis": (
                "Large analytics batch INSERT job on primary is generating 10x normal WAL volume. "
                "Replica-2's disk can't keep up — IOPS near the 16K limit. Replica-1 has faster disks "
                "and is not affected. Fix: throttle the batch job, split into smaller batches, "
                "or upgrade replica-2 disk to provisioned IOPS. Lag will clear once batch job completes."
            ),
            "heap_dump": None,
            "network_trace": None,
        },
        "caused_by": None,
        "escalation_rate": 5,
        "expected": {
            "severity": "P2",
            "category": "database",
            "team": "database-team",
            "key_actions": ["replication", "lag", "WAL", "batch", "IOPS", "throttle", "replica"],
        },
    },
    # ── INC-018: Grafana Dashboard Stale Data (RED HERRING) ───────────
    {
        "id": "INC-018",
        "title": "Grafana Dashboard Showing Zero Traffic",
        "summary": "Main traffic dashboard shows 0 req/s but services report normal load via healthchecks",
        "details": {
            "alert_source": "Grafana",
            "triggered_at": "2024-03-15T09:00:00Z",
            "service": "monitoring-stack",
            "environment": "production",
            "logs": [
                "[08:50:00] WARN  grafana: Dashboard 'Production Traffic' showing 0 values for all panels",
                "[08:55:00] INFO  healthcheck: All 12 production services responding normally",
                "[08:58:00] WARN  grafana: Prometheus datasource returning empty results for rate() queries",
                "[09:00:00] ALERT grafana: Dashboard anomaly — zero traffic detected (possible monitoring gap)",
            ],
            "metrics": {
                "dashboard_traffic_shown": 0,
                "actual_service_health": "all healthy",
                "prometheus_targets_up": 45,
                "prometheus_targets_total": 48,
                "scrape_duration_seconds": 12.5,
                "normal_scrape_duration_seconds": 0.8,
            },
            "recent_changes": "Prometheus upgraded from v2.48 to v2.50 last night during maintenance window.",
        },
        "diagnostics": {
            "deep_logs": [
                "[02:00:00] INFO  prometheus: Upgrade v2.48 → v2.50 started",
                "[02:05:00] WARN  prometheus: TSDB compaction running — temporary query slowdown",
                "[02:10:00] ERROR prometheus: 3 scrape targets failing: node-exporter on 3 legacy hosts (deprecated metric names in v2.50)",
                "[08:30:00] DEBUG prometheus: rate() queries returning empty due to 6h gap in metrics during upgrade/compaction",
                "[08:45:00] INFO  prometheus: New data flowing correctly — gap will self-heal in rate() window (5m→1h panels)",
            ],
            "root_cause_analysis": (
                "This is a monitoring issue, NOT a traffic issue. The Prometheus upgrade caused a 6-hour "
                "gap in time-series data during TSDB compaction. rate() queries return empty results when "
                "there's no data in the window. Services are actually healthy — healthchecks confirm this. "
                "3 legacy scrape targets broke due to deprecated metric names in v2.50. "
                "Fix: update legacy exporters, wait for rate() windows to fill. No production impact."
            ),
            "heap_dump": None,
            "network_trace": None,
        },
        "caused_by": None,
        "escalation_rate": 50,  # basically never escalates
        "expected": {
            "severity": "P3",
            "category": "monitoring",
            "team": "sre-team",
            "key_actions": ["prometheus", "upgrade", "scrape", "metric", "dashboard", "TSDB", "gap"],
        },
    },
    # ── INC-019: HPA Thrashing (CASCADE from INC-005) ─────────────────
    {
        "id": "INC-019",
        "title": "Kubernetes HPA Thrashing",
        "summary": "order-processor HPA scaling up/down every 60s; replica count oscillating 2↔8",
        "details": {
            "alert_source": "Kubernetes",
            "triggered_at": "2024-03-15T16:20:00Z",
            "service": "order-processor",
            "environment": "production",
            "logs": [
                "[16:10:00] INFO  hpa: Scaled order-processor from 5 to 8 replicas (CPU above target)",
                "[16:12:00] INFO  hpa: Scaled order-processor from 8 to 3 replicas (3 pods OOMKilled, actual CPU dropped)",
                "[16:14:00] INFO  hpa: Scaled order-processor from 3 to 7 replicas (backlog increasing)",
                "[16:16:00] INFO  hpa: Scaled order-processor from 7 to 2 replicas (5 pods OOMKilled)",
                "[16:20:00] ALERT k8s: HPA order-processor unstable — 8 scaling events in 10 minutes",
            ],
            "metrics": {
                "scaling_events_last_10min": 8,
                "min_replicas_observed": 2,
                "max_replicas_observed": 8,
                "avg_pod_lifetime_seconds": 90,
                "order_backlog_growing": True,
                "cpu_target_utilization": 70,
            },
            "recent_changes": "No HPA config changes. Related: INC-005 reports OOMKill issues for same deployment.",
        },
        "diagnostics": {
            "deep_logs": [
                "[16:08:00] DEBUG hpa: order-processor CPU: 285% across 5 pods (target: 70% each = 350% total)",
                "[16:09:00] DEBUG hpa: Scaled up to 8 pods — new target: 560% total CPU",
                "[16:10:00] TRACE kubelet: 3 of 8 pods OOMKilled immediately after start (same v4.1.0 memory issue)",
                "[16:11:00] DEBUG hpa: Only 5 pods running, but HPA sees 8 desired — metric confusion",
                "[16:12:00] DEBUG hpa: average CPU per pod dropped (3 dead pods report 0) — scaling down",
            ],
            "root_cause_analysis": (
                "This is a downstream effect of INC-005 (order-processor OOMKill). The HPA tries to scale "
                "up to handle load, but new pods immediately OOMKill due to the v4.1.0 memory bug. The HPA "
                "then sees low average CPU (dead pods report 0) and scales down. This creates a thrashing "
                "loop. Fix: resolve INC-005 first (rollback v4.1.0 or increase memory limits). HPA will "
                "stabilize once pods stop OOMKilling."
            ),
            "heap_dump": None,
            "network_trace": None,
        },
        "caused_by": "INC-005",
        "escalation_rate": 3,
        "expected": {
            "severity": "P2",
            "category": "infrastructure",
            "team": "infra-team",
            "key_actions": ["HPA", "OOMKill", "INC-005", "thrashing", "scale", "stabilize", "memory"],
        },
    },
    # ── INC-020: S3 Bucket Access Denied ──────────────────────────────
    {
        "id": "INC-020",
        "title": "S3 Bucket Access Denied",
        "summary": "backup-service and report-generator failing to write to s3://company-data-lake; IAM policy changed",
        "details": {
            "alert_source": "AWS CloudTrail",
            "triggered_at": "2024-03-15T07:30:00Z",
            "service": "data-lake-storage",
            "environment": "production",
            "logs": [
                "[07:15:00] ERROR backup-service: PutObject denied for s3://company-data-lake/backups/2024-03-15/",
                "[07:20:00] ERROR report-generator: PutObject denied for s3://company-data-lake/reports/daily/",
                "[07:25:00] WARN  cloudtrail: 234 AccessDenied events for s3://company-data-lake in last 15 min",
                "[07:30:00] ALERT cloudtrail: Anomalous spike in S3 access denied events",
            ],
            "metrics": {
                "access_denied_events_last_hour": 450,
                "normal_denied_events_last_hour": 2,
                "affected_services": ["backup-service", "report-generator", "etl-pipeline"],
                "bucket_policy_last_modified": "2024-03-15T07:00:00Z",
                "data_backup_sla_at_risk": True,
            },
            "recent_changes": "IAM team applied S3 bucket policy changes at 07:00 as part of quarterly access review.",
        },
        "diagnostics": {
            "deep_logs": [
                "[07:00:00] INFO  iam: Bucket policy updated for s3://company-data-lake — removed 'legacy-write-access' statement",
                "[07:00:05] DEBUG iam: Removed statement allowed: Principal='arn:aws:iam::role/service-writer' Action='s3:PutObject'",
                "[07:00:10] TRACE iam: backup-service, report-generator, etl-pipeline all use role/service-writer",
                "[07:05:00] DEBUG cloudtrail: First AccessDenied from backup-service (role/service-writer)",
            ],
            "root_cause_analysis": (
                "The quarterly IAM access review removed the 'legacy-write-access' bucket policy statement, "
                "which was the ONLY statement granting s3:PutObject to the service-writer role. Despite being "
                "labeled 'legacy', 3 active production services depend on it. Fix: restore the bucket policy "
                "statement (or create a proper replacement), then update the access review to flag active "
                "policies before removal."
            ),
            "heap_dump": None,
            "network_trace": None,
        },
        "caused_by": None,
        "escalation_rate": 6,
        "expected": {
            "severity": "P2",
            "category": "security",
            "team": "security-team",
            "key_actions": ["IAM", "bucket policy", "restore", "PutObject", "access", "S3", "role"],
        },
    },
]

# ---------------------------------------------------------------------------
# Dependency map — which incidents are caused by other incidents
# ---------------------------------------------------------------------------

DEPENDENCY_MAP: Dict[str, str] = {
    "INC-009": "INC-006",  # checkout alert storm caused by Redis failure
    "INC-016": "INC-011",  # CDN stampede caused by network partition
    "INC-019": "INC-005",  # HPA thrashing caused by OOMKilled pods
}

# ---------------------------------------------------------------------------
# Lookup helper
# ---------------------------------------------------------------------------

_INCIDENT_BY_ID: Dict[str, Dict[str, Any]] = {inc["id"]: inc for inc in INCIDENT_POOL}


def get_incident(incident_id: str) -> Optional[Dict[str, Any]]:
    """Look up an incident by ID."""
    return _INCIDENT_BY_ID.get(incident_id)


def get_incidents_by_ids(ids: List[str]) -> List[Dict[str, Any]]:
    """Return incidents matching the given IDs, in order."""
    return [_INCIDENT_BY_ID[i] for i in ids if i in _INCIDENT_BY_ID]
