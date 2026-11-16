"""用户级审批模式设置（设置 → 权限设置）的读写。

存储位置：``{workspace_dir}/.settings/{safe_username}.json``
（路径由 :meth:`Config.get_user_approval_settings_path` 解析，**在用户工作区之外**，
避免 AI 通过 ``write_file`` / ``execute`` 改写自己的权限配置）。

为什么是文件而不是表：

* 与 ``mcp.json`` / ``skill-categories.json`` / ``builtin-skills.json`` 同属用户级 JSON，
  读写方式一致，零 schema 迁移；
* k8s 部署里 ``workspace`` 是 ``ReadWriteMany`` 共享卷，多副本读同一份文件；
* 跨 Pod 一致性不靠「保存后广播驱逐」，而是**每轮装配 Agent 前读一次**（约 200 字节）：
  A 副本保存、请求落到 B 副本时，B 下一次装配就能读出新值。

读失败 / 文件损坏 / 字段非法一律回退 ``manual``——坏文件绝不能默认成「自动审批」。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from easy_agent.agent.hitl import (
    APPROVAL_MODE_MANUAL,
    APPROVAL_MODES,
    normalize_approval_mode,
)
from easy_agent.bootstrap.config import Config

logger = logging.getLogger("easy-agent.approval")

#: 配置文件自身的版本号，便于以后加字段做兼容。
SETTINGS_VERSION = 1

#: 时间戳统一按北京时间落盘（与其它用户级 JSON 的排查习惯一致）。
_CST = timezone(timedelta(hours=8))

#: 写串行化：同一进程内多个请求同时保存时不产生交叉的临时文件。
#: 跨进程/跨 Pod 的并发由 ``os.replace`` 的原子性兜底（后写覆盖先写，不会写坏文件）。
_WRITE_LOCK = threading.Lock()


def _settings_path(username: str, config: Config | None) -> Path:
    return Config.get_user_approval_settings_path(username, config)


def _now() -> str:
    return datetime.now(_CST).isoformat(timespec="seconds")


def _default_payload(username: str, config: Config | None) -> dict[str, Any]:
    return {
        "mode": APPROVAL_MODE_MANUAL,
        "version": SETTINGS_VERSION,
        "revision": 0,
        "updated_at": "",
        "path": str(_settings_path(username, config)),
    }


def _load_raw(path: Path, safe_name: str) -> dict[str, Any]:
    """读取并校验配置文件；任何异常都回退到「无配置」（调用方补默认值）。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(f"审批设置文件读取失败，按 manual 处理 | 用户: {safe_name} | {path} | {exc}")
        return {}
    if not isinstance(data, dict):
        logger.warning(f"审批设置文件结构非法（非对象），按 manual 处理 | 用户: {safe_name} | {path}")
        return {}
    return data


def _mode_of(data: dict[str, Any], safe_name: str) -> str:
    """取并校验 ``mode`` 字段；非法值记 warning 后回落 ``manual``（安全默认值）。"""
    raw = data.get("mode")
    if raw is not None and str(raw).strip().lower() not in APPROVAL_MODES:
        logger.warning(
            f"审批设置里 mode 非法，按 manual 处理 | 用户: {safe_name} | 值: {raw!r}"
        )
    return normalize_approval_mode(raw)


def _revision_of(data: dict[str, Any]) -> int:
    """取 ``revision`` 字段；缺失或非法按 0（首次保存即 1）。"""
    revision = data.get("revision")
    return revision if isinstance(revision, int) and revision >= 0 else 0


def read_approval_mode(username: str, config: Config | None = None) -> str:
    """读当前用户的审批模式；非法值 / 文件损坏 / 无权限一律回退 ``manual``。"""
    safe_name = Config.sanitize_username(username)
    return _mode_of(_load_raw(_settings_path(username, config), safe_name), safe_name)


def read_approval_settings(username: str, config: Config | None = None) -> dict[str, Any]:
    """设置页用的完整读取结果（模式 + 版本 + 修改时间 + 文件路径）。"""
    safe_name = Config.sanitize_username(username)
    data = _load_raw(_settings_path(username, config), safe_name)
    payload = _default_payload(username, config)
    if data:
        payload["mode"] = _mode_of(data, safe_name)
        payload["revision"] = _revision_of(data)
        updated_at = data.get("updated_at")
        payload["updated_at"] = updated_at if isinstance(updated_at, str) else ""
    return payload


def write_approval_settings(
    username: str, mode: str, config: Config | None = None
) -> dict[str, Any]:
    """原子写审批模式（``tmp`` + ``os.replace``），返回写入后的完整结果。

    ``revision`` 每次保存 +1，用于排查「到底保存成功了没有」以及将来做乐观并发。
    """
    value = str(mode or "").strip().lower()
    if value not in APPROVAL_MODES:
        raise ValueError(f"非法审批模式: {mode!r}（可选 {', '.join(APPROVAL_MODES)}）")

    safe_name = Config.sanitize_username(username)
    path = _settings_path(username, config)
    with _WRITE_LOCK:
        previous = _load_raw(path, safe_name)
        payload = {
            "version": SETTINGS_VERSION,
            "mode": value,
            "updated_at": _now(),
            "revision": _revision_of(previous) + 1,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            # 半截临时文件不留在目录里，避免下次被误读
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    logger.info(
        f"审批设置已保存 | 用户: {safe_name} | mode: {value} | revision: {payload['revision']} | {path}"
    )
    return {
        "mode": value,
        "version": SETTINGS_VERSION,
        "revision": payload["revision"],
        "updated_at": payload["updated_at"],
        "path": str(path),
    }


__all__ = [
    "SETTINGS_VERSION",
    "read_approval_mode",
    "read_approval_settings",
    "write_approval_settings",
]
