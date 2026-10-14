"""技能库辅助：用户技能目录（启用/停用）、统计信息、SKILL.md 正文解析。

目录约定（与 Agent 加载目录严格一致）：:

    workspace/{username}/skills/            ← Agent 实际扫描的技能目录（= 启用）
    workspace/{username}/skills-disabled/   ← 停用技能存放处（Agent 不扫，不加载）

「停用」= 把技能目录从 ``skills/`` 原子重命名到 ``skills-disabled/``，因此停用后
deepagents 侧（``_resolve_skills_paths`` 只认 ``skills/``）自然不再加载它；重新启用
即移回。这样不需要给 deepagents 传额外的过滤参数，也不改动技能内容本身。

本模块只做文件系统层面的事，不依赖 Agent 运行期配置。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Config

logger = logging.getLogger("easy-agent")

#: 技能定义文件（按优先级）
SKILL_FILES = ("SKILL.md", "README.md")

#: 停用技能目录名（位于用户 workspace 下，与 skills/ 同级）
DISABLED_DIR_NAME = "skills-disabled"

#: 统计时的文件数上限，防御性上限（技能目录正常只有几个文件）
MAX_STAT_FILES = 5000


# ── 路径 ──────────────────────────────────────────────────────────────


def user_skills_dir(username: str) -> Path:
    """Agent 实际加载的用户技能目录。"""
    return Config.get_user_workspace_dir(username) / "skills"


def user_disabled_dir(username: str) -> Path:
    """停用技能目录（Agent 不加载）。"""
    return Config.get_user_workspace_dir(username) / DISABLED_DIR_NAME


def is_skill_dir(path: Path) -> bool:
    """目录内存在 SKILL.md 或 README.md 才算技能目录。"""
    return path.is_dir() and any((path / name).exists() for name in SKILL_FILES)


def iter_skill_dirs(root: Path) -> list[Path]:
    """列出目录下所有技能子目录（按名字排序，忽略隐藏目录）。"""
    if not root.exists():
        return []
    return [
        child
        for child in sorted(root.iterdir(), key=lambda p: p.name)
        if not child.name.startswith(".") and is_skill_dir(child)
    ]


# ── frontmatter ───────────────────────────────────────────────────────


def parse_frontmatter(text: str) -> dict[str, str]:
    """解析 SKILL.md 顶部 YAML frontmatter 里的 name/description/icon/version。

    只认单行 `key: value`（现有技能文件都是这种极简形态），解析失败返回空字段。
    """
    meta: dict[str, str] = {}
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return meta
    for line in match.group(1).split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key in ("name", "description", "icon", "version") and value:
            meta[key] = value
    return meta


def read_skill_document(skill_dir: Path) -> tuple[str, str]:
    """读取技能定义正文，返回 (文件名, 内容)；都没有时返回 ("", "")。"""
    for name in SKILL_FILES:
        filepath = skill_dir / name
        if filepath.is_file():
            try:
                return name, filepath.read_text(encoding="utf-8", errors="replace")
            except OSError as e:  # 极端情况（权限/损坏）降级为空正文
                logger.warning(f"读取技能文件失败 | {filepath} | {e}")
                return name, ""
    return "", ""


def skill_metadata(skill_dir: Path) -> dict[str, Any]:
    """技能元数据：name/description/icon/version（frontmatter 优先，缺失时降级）。"""
    filename, content = read_skill_document(skill_dir)
    front = parse_frontmatter(content) if content else {}

    description = front.get("description", "")
    if not description and content:
        # 没有 frontmatter 描述时，取第一段有实际内容的正文：
        # 跳过标题/分隔线，并跳过纯符号行（如只有 ">" 的引用符号），避免卡片上出现 ">"
        for line in content.split("\n"):
            stripped = line.strip().lstrip(">").strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("---"):
                continue
            if not re.search(r"[\w\u4e00-\u9fff]", stripped):
                continue
            description = stripped[:200]
            break

    return {
        "name": front.get("name") or skill_dir.name,
        "description": description,
        "icon": front.get("icon", ""),
        "version": front.get("version", ""),
        "doc_file": filename,
    }


# ── 统计 ──────────────────────────────────────────────────────────────


def skill_stats(skill_dir: Path) -> dict[str, Any]:
    """技能目录真实统计：文件数 / 总字节数 / 最近更新时间（ISO8601，UTC）。

    用于卡片底部信息行，替代截图里的「浏览量/下载量」这类本地拿不到的指标。
    """
    file_count = 0
    size_bytes = 0
    latest = 0.0
    truncated = False

    for root, dirs, files in os.walk(skill_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
        for filename in files:
            if filename.startswith("."):
                continue
            filepath = Path(root) / filename
            try:
                stat = filepath.stat()
            except OSError:
                continue
            file_count += 1
            size_bytes += stat.st_size
            latest = max(latest, stat.st_mtime)
            if file_count >= MAX_STAT_FILES:
                truncated = True
                break
        if truncated:
            break

    updated_at = (
        datetime.fromtimestamp(latest, tz=timezone.utc).isoformat()
        if latest
        else None
    )
    return {
        "file_count": file_count,
        "size_bytes": size_bytes,
        "updated_at": updated_at,
        "truncated": truncated,
    }


# ── 启用 / 停用 ───────────────────────────────────────────────────────


def list_user_skill_dirs(username: str) -> list[dict[str, Any]]:
    """列出用户技能（含停用），每项 {dir_name, path, enabled}。"""
    items: list[dict[str, Any]] = []
    for enabled, root in (
        (True, user_skills_dir(username)),
        (False, user_disabled_dir(username)),
    ):
        for skill_dir in iter_skill_dirs(root):
            items.append({
                "dir_name": skill_dir.name,
                "path": skill_dir,
                "enabled": enabled,
            })
    return items


def find_user_skill(username: str, dir_name: str) -> dict[str, Any] | None:
    """按目录名找用户技能（启用与停用都找），返回 None 表示不存在。"""
    for root, enabled in (
        (user_skills_dir(username), True),
        (user_disabled_dir(username), False),
    ):
        candidate = root / dir_name
        if is_skill_dir(candidate):
            return {"dir_name": dir_name, "path": candidate, "enabled": enabled}
    return None


def set_skill_enabled(username: str, dir_name: str, enable: bool) -> dict[str, Any]:
    """启用/停用用户技能（原子重命名），返回操作后的 {dir_name, enabled, path}。

    已处于目标状态时直接返回成功（幂等），目录不存在抛 FileNotFoundError，
    目标处已有同名技能抛 FileExistsError。
    """
    source_root = user_disabled_dir(username) if enable else user_skills_dir(username)
    target_root = user_skills_dir(username) if enable else user_disabled_dir(username)
    source = source_root / dir_name
    target = target_root / dir_name

    if not is_skill_dir(source):
        if is_skill_dir(target):
            # 已经是目标状态
            return {"dir_name": dir_name, "enabled": enable, "path": target}
        raise FileNotFoundError(dir_name)

    if target.exists():
        raise FileExistsError(f"{target_root.name}/{dir_name}")

    target_root.mkdir(parents=True, exist_ok=True)
    source.rename(target)
    logger.info(
        f"技能{'启用' if enable else '停用'} | 用户: {username} | 技能: {dir_name} | 位置: {target}"
    )
    return {"dir_name": dir_name, "enabled": enable, "path": target}


def remove_user_skill(username: str, dir_name: str) -> Path:
    """彻底删除用户技能（启用或停用目录里的都删），返回被删路径。"""
    found = find_user_skill(username, dir_name)
    if not found:
        raise FileNotFoundError(dir_name)
    shutil.rmtree(str(found["path"]))
    logger.info(f"技能移除 | 用户: {username} | 技能: {dir_name}")
    return found["path"]


# ── 登记（对话创建 / 工作区收编）───────────────────────────────────────────

#: 合法技能目录名：小写字母或数字开头，仅含小写字母、数字、连字符、下划线
SKILL_DIR_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def normalize_skill_dir_name(name: str) -> str:
    """把候选名字规范成合法技能目录名（小写、连字符分隔）；无法规范化时返回空串。"""
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    cleaned = cleaned[:64].strip("-")
    return cleaned if SKILL_DIR_NAME_RE.match(cleaned) else ""


def unique_skill_dir_name(username: str, preferred: str) -> str:
    """在技能库（启用 + 停用）里挑一个不冲突的目录名：重名时追加 -2、-3…"""
    base = preferred or "skill"
    taken = {item["dir_name"] for item in list_user_skill_dirs(username)}
    if base not in taken:
        return base
    index = 2
    while f"{base}-{index}" in taken:
        index += 1
    return f"{base}-{index}"


def register_skill_source(
    username: str,
    source_dir: Path | str,
    dir_name: str | None = None,
) -> dict[str, Any]:
    """把一个目录登记为用户技能（移动进技能库），返回登记结果。

    用于「对话创建技能」：模型可能把技能写在工作区，也可能直接写进技能库，
    收尾时统一走这里入库。

    - 校验：必须是目录、含非空 ``SKILL.md``，否则 ``ValueError``
    - 命名：优先用 ``dir_name``（缺省取源目录名），规范化为安全目录名
    - 重名：与既有技能（启用 + 停用）冲突时追加 ``-2``、``-3``…，绝不覆盖
    - 移动：``shutil.move`` 到 ``skills/``，源目录不再保留（工作区不留副本）

    Returns:
        ``{"dir_name", "path", "renamed", "moved_from", "warnings"}``
    """
    source = Path(source_dir)
    if not source.is_dir():
        raise ValueError(f"源目录不存在: {source}")
    skill_file = source / "SKILL.md"
    if not skill_file.is_file():
        raise ValueError(f"目录内缺少 SKILL.md: {source}")
    content = skill_file.read_text(encoding="utf-8", errors="replace")
    if not content.strip():
        raise ValueError(f"SKILL.md 内容为空: {skill_file}")

    preferred = normalize_skill_dir_name(dir_name or source.name)
    if not preferred:
        raise ValueError(f"技能目录名不合法: {dir_name or source.name}")

    target_root = user_skills_dir(username)
    target_root.mkdir(parents=True, exist_ok=True)
    final_name = unique_skill_dir_name(username, preferred)
    target = target_root / final_name
    if target.exists():  # 并发兜底：极端情况下同名技能刚被别人创建
        raise FileExistsError(str(target))

    shutil.move(str(source), str(target))

    # 目标名与源目录名不同（规范化或重名加后缀）时算重命名
    renamed = final_name != source.name
    warnings: list[str] = []
    front = parse_frontmatter(content)
    if not front.get("name"):
        warnings.append("SKILL.md frontmatter 缺少 name（将按目录名展示）")
    if not front.get("description"):
        warnings.append("SKILL.md frontmatter 缺少 description（触发依赖描述，建议补上）")

    logger.info(
        f"技能登记 | 用户: {username} | 技能: {final_name}"
        f"{'（源目录名 ' + source.name + ' 已重命名）' if renamed else ''}"
        f" | 来源: {source} | 目标: {target}"
        f"{' | 提示: ' + '; '.join(warnings) if warnings else ''}"
    )
    return {
        "dir_name": final_name,
        "path": target,
        "renamed": renamed,
        "moved_from": source,
        "warnings": warnings,
    }
