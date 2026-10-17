"""部署形态校验（不依赖 app/DB 实现，便于单测与启动期 fail-fast）。

约束（用户决策）：生产多 worker/多副本必须使用 MySQL；SQLite 仅单机开发。
"""

from __future__ import annotations

import os
from ..env import env_int


def detect_worker_count() -> int:
    """检测进程/副本数（WEB_CONCURRENCY / UVICORN_WORKERS / EASY_REPLICA_COUNT）。"""
    for name in ("EASY_REPLICA_COUNT", "WEB_CONCURRENCY", "UVICORN_WORKERS"):
        value = env_int(name, 0)
        if value > 1:
            return value
    return 1


def deployment_errors(
    *, db_type: str, agent_env: str, workers: int | None = None
) -> list[str]:
    """返回必须阻止启动的部署配置错误列表（空列表 = 通过）。

    规则：AGENT_ENV=prod 且 worker/replica > 1 时，SQLite 无法支撑多进程写，
    必须在启动期显式失败，而不是运行期随机 `database is locked`。
    """
    count = workers if workers is not None else detect_worker_count()
    errors: list[str] = []
    if count > 1 and str(agent_env or "").lower() == "prod" and db_type != "mysql":
        errors.append(
            f"生产环境检测到 {count} 个 worker/副本，但数据库类型为 {db_type!r}；"
            "多实例必须配置 MySQL（database.type=mysql），SQLite 仅允许单机运行。"
            "若确需单机多 worker 调试，请设置 AGENT_ENV=dev。"
        )
    return errors


def deployment_warnings(
    *, db_type: str, agent_env: str, workers: int | None = None
) -> list[str]:
    """非阻断告警（dev/test 多 worker + SQLite 时提示风险）。"""
    count = workers if workers is not None else detect_worker_count()
    if count > 1 and str(agent_env or "").lower() != "prod" and db_type != "mysql":
        return [
            f"检测到 {count} 个 worker/副本但使用 SQLite：仅建议单机调试，"
            "多 worker 写入可能出现 database is locked。"
        ]
    return []
