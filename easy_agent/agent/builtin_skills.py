"""公共技能的按用户启用清单（DA-13 S2）。

公共技能（仓库 ``skills/``，即 ``config.tools.skills_dir``）不再复制进用户目录：agent 经只读
路由 ``/skills/builtin/`` 直接读 canonical 目录，只看得到该用户「已添加且启用」的那些
（``backends/builtin_skills.py``）。清单与 ``skill-categories.json``、``mcp.json`` 同级::

    workspace/{username}/builtin-skills.json
    {"skills": {"docx": {"enabled": true, "added_at": "..."}}, "migrated": true}

``migrated``：S2 之前「添加」会把公共技能整份复制进 ``skills/`` / ``skills-disabled/``。
第一次读清单时迁移一次：与 canonical 内容完全一致的副本删掉、记进清单（启停状态沿用）；
被用户改过的副本保留为用户技能（同名覆盖公共技能，相当于 fork）。

本模块只做文件层面的事、只依赖 ``Config``（agent 层与 services 层都会用到它，不能反过来
import services）；调用方负责在启停后让缓存的 Agent 失效。用户技能目录名也以这里为准，
``services/skill_library.py`` 从这里取。

各函数的 ``config`` 缺省时按运行期配置解析用户目录（接口层）；``EasyAgent``、迁移脚本这类
持有自己配置的调用方要显式传入，否则会读写到运行期配置（或 ``./workspace``）下的清单。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from easy_agent.bootstrap.config import Config

logger = logging.getLogger("easy-agent")

STORE_FILE_NAME = "builtin-skills.json"
#: agent 看到的公共技能根（只读路由）
BUILTIN_VIRTUAL_ROOT = "/skills/builtin"
#: 用户技能目录（Agent 加载）与停用技能目录，均位于 workspace/{username}/ 下
USER_SKILLS_DIR_NAME = "skills"
USER_DISABLED_DIR_NAME = "skills-disabled"

#: 迁移时比较内容会跳过的条目（与技能统计口径一致）
_IGNORED_NAMES = frozenset({"__pycache__"})


# ── 路径 ──────────────────────────────────────────────────────────────


def builtin_root(config: Any = None) -> Path | None:
    """canonical 公共技能目录；未配置或不存在时返回 ``None``。

    ``config`` 缺省时读运行期配置（``get_runtime_agent_config()["config"]``）。
    """
    if config is None:
        from easy_agent.bootstrap.runtime_config import get_runtime_agent_config

        runtime = get_runtime_agent_config() or {}
        config = runtime.get("config") if isinstance(runtime, dict) else None
    tools = getattr(config, "tools", None)
    raw = getattr(tools, "skills_dir", "") if tools is not None else ""
    if not raw:
        return None
    root = Path(raw).absolute()
    return root if root.is_dir() else None


def store_path(username: str, config: Any = None) -> Path:
    return Config.get_user_workspace_dir(username, config) / STORE_FILE_NAME


def _is_skill_dir(path: Path) -> bool:
    return path.is_dir() and (path / "SKILL.md").is_file()


def is_safe_dir_name(name: str) -> bool:
    """技能目录名必须是单个路径段：非空、不以 ``.`` 开头、不含路径分隔符。

    名字来自请求体与清单文件，会拼进 ``root / name``、沙箱只读挂载的源路径；
    ``../其他用户/skills/x`` 这类名字一律当作不存在。
    """
    return bool(name) and not name.startswith(".") and not any(ch in name for ch in "/\\\0")


# ── 清单读写 ─────────────────────────────────────────────────────────


def load_store(username: str, config: Any = None) -> dict[str, Any]:
    path = store_path(username, config)
    if not path.is_file():
        return {"skills": {}, "migrated": False}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"读取公共技能清单失败，按空清单处理 | {path} | {e}")
        return {"skills": {}, "migrated": False}
    skills = raw.get("skills") if isinstance(raw, dict) else None
    entries = {
        str(name): {"enabled": bool((entry or {}).get("enabled", True)), "added_at": (entry or {}).get("added_at", "")}
        for name, entry in (skills or {}).items()
        if isinstance(entry, dict)
    }
    return {"skills": entries, "migrated": bool(raw.get("migrated")) if isinstance(raw, dict) else False}


def save_store(username: str, store: dict[str, Any], config: Any = None) -> None:
    """原子写入（临时文件 + rename），避免并发请求读到半截 JSON。"""
    path = store_path(username, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"skills": store.get("skills") or {}, "migrated": bool(store.get("migrated"))},
        ensure_ascii=False,
        indent=2,
    )
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".builtin-skills.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── 迁移 S2 之前复制出来的副本 ────────────────────────────────────────


def _tree_digest(root: Path) -> str:
    """目录内容指纹：相对路径 + 文件字节（跳过隐藏条目与 ``__pycache__``）。"""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part.startswith(".") or part in _IGNORED_NAMES for part in relative.parts):
            continue
        if path.is_file():
            digest.update(str(relative.as_posix()).encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def migrate_user_copies(
    username: str, root: Path, *, apply: bool = True, config: Any = None
) -> dict[str, list[str]]:
    """把 S2 之前复制进用户目录的公共技能副本迁移到清单。

    Returns:
        ``{"removed": [...], "kept": [...]}``：删掉的（与 canonical 一致）与保留为用户技能的
        （被改过）目录名。``apply=False`` 只返回计划，不改文件。
    """
    store = load_store(username, config)
    report: dict[str, list[str]] = {"removed": [], "kept": []}
    workspace = Config.get_user_workspace_dir(username, config)
    for enabled, user_root in (
        (True, workspace / USER_SKILLS_DIR_NAME),
        (False, workspace / USER_DISABLED_DIR_NAME),
    ):
        copies = sorted(user_root.iterdir(), key=lambda p: p.name) if user_root.is_dir() else []
        for copy in (c for c in copies if not c.name.startswith(".") and _is_skill_dir(c)):
            canonical = root / copy.name
            if not _is_skill_dir(canonical):
                continue
            if _tree_digest(copy) != _tree_digest(canonical):
                report["kept"].append(copy.name)
                continue
            report["removed"].append(copy.name)
            if apply:
                shutil.rmtree(copy)
                store["skills"].setdefault(copy.name, {"enabled": enabled, "added_at": _now()})
    if apply:
        store["migrated"] = True
        save_store(username, store, config)
        if report["removed"] or report["kept"]:
            logger.info(
                f"公共技能副本迁移 | 用户: {username} | 删除（与公共版本一致）: {report['removed']} | "
                f"保留为用户技能（已修改）: {report['kept']}"
            )
    return report


def ensure_migrated(username: str, root: Path | None = None, config: Any = None) -> None:
    """第一次读清单时迁移一次（失败只记日志，不影响列表）。``root`` 缺省按 ``config`` 解析。"""
    root = root if root is not None else builtin_root(config)
    if root is None or load_store(username, config).get("migrated"):
        return
    try:
        migrate_user_copies(username, root, config=config)
    except Exception as e:  # noqa: BLE001 - 迁移失败时副本原样保留，照旧当用户技能用
        logger.warning(f"公共技能副本迁移失败（副本保留，下次再试）| 用户: {username} | {e}")


# ── 查询与修改 ────────────────────────────────────────────────────────


def list_added(username: str, root: Path | None = None, config: Any = None) -> list[dict[str, Any]]:
    """用户已添加的公共技能（含已停用），每项 ``{dir_name, path, enabled, source="builtin"}``。

    canonical 目录里已不存在的条目（公共技能被下线）不返回。
    """
    root = root if root is not None else builtin_root(config)
    if root is None:
        return []
    ensure_migrated(username, root, config)
    items = []
    for name, entry in sorted(load_store(username, config)["skills"].items()):
        path = root / name
        if is_safe_dir_name(name) and _is_skill_dir(path):
            items.append({"dir_name": name, "path": path, "enabled": entry["enabled"], "source": "builtin"})
    return items


def enabled_names(username: str, root: Path | None = None, config: Any = None) -> tuple[str, ...]:
    return tuple(item["dir_name"] for item in list_added(username, root, config) if item["enabled"])


def add(username: str, dir_name: str, root: Path | None = None, config: Any = None) -> dict[str, Any]:
    """添加公共技能（默认启用）。不存在抛 ``FileNotFoundError``，已添加抛 ``FileExistsError``。"""
    root = root if root is not None else builtin_root(config)
    if root is None or not is_safe_dir_name(dir_name) or not _is_skill_dir(root / dir_name):
        raise FileNotFoundError(dir_name)
    ensure_migrated(username, root, config)
    store = load_store(username, config)
    if dir_name in store["skills"]:
        raise FileExistsError(dir_name)
    store["skills"][dir_name] = {"enabled": True, "added_at": _now()}
    save_store(username, store, config)
    logger.info(f"公共技能添加 | 用户: {username} | 技能: {dir_name}")
    return {"dir_name": dir_name, "path": root / dir_name, "enabled": True, "source": "builtin"}


def set_enabled(username: str, dir_name: str, enabled: bool, config: Any = None) -> dict[str, Any]:
    """启用 / 停用已添加的公共技能；未添加抛 ``FileNotFoundError``。"""
    store = load_store(username, config)
    entry = store["skills"].get(dir_name)
    if entry is None:
        raise FileNotFoundError(dir_name)
    if entry["enabled"] != enabled:
        entry["enabled"] = enabled
        save_store(username, store, config)
        logger.info(f"公共技能{'启用' if enabled else '停用'} | 用户: {username} | 技能: {dir_name}")
    root = builtin_root(config)
    return {"dir_name": dir_name, "path": (root / dir_name) if root else None, "enabled": enabled, "source": "builtin"}


def remove(username: str, dir_name: str, config: Any = None) -> bool:
    """从清单移除（canonical 目录不动）；未添加返回 ``False``。"""
    store = load_store(username, config)
    if store["skills"].pop(dir_name, None) is None:
        return False
    save_store(username, store, config)
    logger.info(f"公共技能移除 | 用户: {username} | 技能: {dir_name}")
    return True
