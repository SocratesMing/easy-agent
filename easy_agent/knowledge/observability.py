"""Request correlation, bounded metrics and metadata-only audit helpers."""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import defaultdict
from typing import Any


_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_AUDITED_PREFIXES = ("/api/knowledge/v1", "/api/personnel")


class KnowledgeMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self._duration: dict[tuple[str, str], list[float]] = defaultdict(
            lambda: [0.0, 0.0]
        )

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        route = self.normalized_route(route)
        with self._lock:
            self._requests[(method, route, status)] += 1
            aggregate = self._duration[(method, route)]
            aggregate[0] += seconds
            aggregate[1] += 1

    @staticmethod
    def normalized_route(path: str) -> str:
        return re.sub(
            r"/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
            "/{id}",
            path,
        )[:240]

    def prometheus(self, task_counts: dict[str, int]) -> str:
        lines = [
            "# HELP easyagent_knowledge_http_requests_total Knowledge HTTP requests.",
            "# TYPE easyagent_knowledge_http_requests_total counter",
        ]
        with self._lock:
            for (method, route, status), count in sorted(self._requests.items()):
                lines.append(
                    'easyagent_knowledge_http_requests_total'
                    f'{{method="{method}",route="{route}",status="{status}"}} {count}'
                )
            lines.extend(
                [
                    "# HELP easyagent_knowledge_http_duration_seconds_sum Total request duration.",
                    "# TYPE easyagent_knowledge_http_duration_seconds_sum counter",
                ]
            )
            for (method, route), (duration, count) in sorted(self._duration.items()):
                labels = f'method="{method}",route="{route}"'
                lines.append(f"easyagent_knowledge_http_duration_seconds_sum{{{labels}}} {duration:.6f}")
                lines.append(f"easyagent_knowledge_http_duration_seconds_count{{{labels}}} {int(count)}")
        lines.extend(
            [
                "# HELP easyagent_knowledge_tasks Knowledge task count by state.",
                "# TYPE easyagent_knowledge_tasks gauge",
            ]
        )
        for state, count in sorted(task_counts.items()):
            lines.append(f'easyagent_knowledge_tasks{{status="{state}"}} {count}')
        return "\n".join(lines) + "\n"


metrics = KnowledgeMetrics()


def resolve_request_id(candidate: str | None) -> str:
    candidate = (candidate or "").strip()
    return candidate if _SAFE_REQUEST_ID.fullmatch(candidate) else str(uuid.uuid4())


def should_audit(path: str, method: str) -> bool:
    if not path.startswith(_AUDITED_PREFIXES):
        return False
    if path.endswith(("/capabilities", "/status", "/health", "/metrics")):
        return False
    return True


def audit_metadata(path: str, method: str, status_code: int, duration: float) -> dict[str, Any]:
    parts = [item for item in path.split("/") if item]
    object_id = parts[-1] if parts else "root"
    object_type = "personnel" if path.startswith("/api/personnel") else "knowledge"
    base_id = None
    if "bases" in parts and parts.index("bases") + 1 < len(parts):
        base_id = parts[parts.index("bases") + 1]
        object_type, object_id = "knowledge_base", base_id
    if "documents" in parts and parts.index("documents") + 1 < len(parts):
        candidate = parts[parts.index("documents") + 1]
        if candidate not in {"content", "retry", "restore"}:
            object_type, object_id = "document", candidate
    if "users" in parts and parts.index("users") + 1 < len(parts):
        object_type, object_id = "personnel", parts[parts.index("users") + 1]
    action = f"{method.lower()}:{KnowledgeMetrics.normalized_route(path)}"
    return {
        "action": action,
        "object_type": object_type,
        "object_id": object_id,
        "base_id": base_id,
        "outcome": "success" if status_code < 400 else "failure",
        "reason_code": None if status_code < 400 else f"HTTP_{status_code}",
        "details": {"status_code": status_code, "duration_ms": round(duration * 1000, 2)},
    }


__all__ = [
    "audit_metadata",
    "metrics",
    "resolve_request_id",
    "should_audit",
]
