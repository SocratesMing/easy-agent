"""Per-task audit logging for scheduled tasks.

Writes one JSONL log file per task at:
    workspace/{sanitized_username}/cron/{task_id}.log

Each line is a self-contained JSON object with timestamp, operation,
username, task_id, detail, and any additional structured fields.
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Config

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _get_log_path(username: str, task_id: str) -> Path:
    user_dir = Config.get_user_workspace_dir(username)
    return user_dir / "cron" / f"{task_id}.log"


def log_task_event(
    username: str,
    task_id: str,
    operation: str,
    detail: str = "",
    **extra: Any,
) -> None:
    """Append one audit entry to the task's JSONL log file.

    Never raises — logging failures are reported via the standard logger
    so task execution is never broken.
    """
    entry: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "operation": operation,
        "username": username,
        "task_id": task_id,
        "detail": detail,
    }
    entry.update(extra)

    line = json.dumps(entry, ensure_ascii=False)

    try:
        log_path = _get_log_path(username, task_id)
        with _lock:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:
        logger.warning(
            f"任务日志写入失败 | task_id={task_id} | operation={operation} | error={e}"
        )
