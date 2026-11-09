"""checkpoint thread_id 的命名规则。

- 会话 thread：``thread_id`` 就是会话 id（UUID；会话 thread 模式）；
- 每条消息一个的 thread：``{session_id}-{message_id}``（默认模式，也是给前端的审批句柄）；
- 定时任务：``sched-{task_id}-{run_id}``（每次运行结束自己删）。

``scripts/db/prune_checkpoint_threads.py`` 和删除会话后的 checkpoint 对账
（``easy_agent/persistence/checkpoints.py``）共用。
"""

from __future__ import annotations

import re

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_SESSION_THREAD = re.compile(rf"^({_UUID})$")
_PER_MESSAGE_THREAD = re.compile(rf"^({_UUID})-.+$")


def thread_kind(thread_id: str) -> str:
    """``session`` / ``per-message`` / ``scheduled`` / ``other``。"""
    if _SESSION_THREAD.match(thread_id):
        return "session"
    if _PER_MESSAGE_THREAD.match(thread_id):
        return "per-message"
    if thread_id.startswith("sched-"):
        return "scheduled"
    return "other"


def session_id_of_thread(thread_id: str) -> str | None:
    """会话 thread 与每条消息一个的 thread 返回会话 id；定时任务和认不出的返回 None。"""
    match = _SESSION_THREAD.match(thread_id) or _PER_MESSAGE_THREAD.match(thread_id)
    return match.group(1) if match else None
