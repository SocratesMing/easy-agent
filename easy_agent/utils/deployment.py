"""部署形态校验（不依赖 app/DB 实现，便于单测与启动期 fail-fast）。

约束（用户决策）：生产多 worker/多副本必须使用 MySQL；SQLite 仅单机开发。
"""

from __future__ import annotations

import os
import re
import socket

from easy_agent.bootstrap.env import env_int, env_str

# 实例名会拼进文件路径（checkpoint 路径里的 ``{pod}``），只允许 k8s pod 名里出现的字符
_INSTANCE_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,252}$")


def detect_worker_count() -> int:
    """检测进程/副本数（WEB_CONCURRENCY / UVICORN_WORKERS / EASY_REPLICA_COUNT）。"""
    for name in ("EASY_REPLICA_COUNT", "WEB_CONCURRENCY", "UVICORN_WORKERS"):
        value = env_int(name, 0)
        if value > 1:
            return value
    return 1


def detect_replica_count() -> int:
    """副本（pod）数：``EASY_REPLICA_COUNT``，未设置时为 1。"""
    return max(env_int("EASY_REPLICA_COUNT", 1), 1)


def detect_workers_per_pod() -> int:
    """单个 pod 里的 worker 进程数：``WEB_CONCURRENCY`` / ``UVICORN_WORKERS`` 取较大者，未设置时为 1。"""
    return max(env_int("WEB_CONCURRENCY", 1), env_int("UVICORN_WORKERS", 1), 1)


def instance_name() -> str:
    """本实例（pod）的名字：依次取 ``EASY_POD_NAME``、``POD_NAME``、``HOSTNAME``，都没有时用主机名。

    k8s 用 Downward API 注入 ``EASY_POD_NAME``（``metadata.name``）；不注入时容器 hostname
    默认就是 pod 名。不做校验：拼进路径之前由 :func:`is_valid_instance_name` 把关。
    """
    for name in ("EASY_POD_NAME", "POD_NAME", "HOSTNAME"):
        value = env_str(name)
        if value:
            return value
    return socket.gethostname().strip()


def instance_owner() -> str:
    """本进程持有跨副本锁（会话流锁等）时的身份：``主机名:进程号``。"""
    return f"{socket.gethostname()}:{os.getpid()}"


def is_valid_instance_name(name: str) -> bool:
    """实例名能否安全地拼进路径：字母、数字、``.``、``_``、``-``，不以 ``.`` 开头，最长 253 个字符。"""
    return bool(_INSTANCE_NAME.match(name or ""))


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
