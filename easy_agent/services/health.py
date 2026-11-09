"""Liveness / readiness probes for K8s."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def ping_database(db) -> tuple[bool, str]:
    if db is None:
        return False, "database not initialized"
    try:
        db.ping()
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def workspace_writable(workspace_dir: str) -> tuple[bool, str]:
    root = Path(workspace_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".ready-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
