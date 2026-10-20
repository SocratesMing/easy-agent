"""Skill discovery utilities"""

import logging
import re
from collections.abc import Iterable
from typing import Any

from pathlib import Path


# 官方 Agent Skills 规范里的硬限制（deepagents 0.7.22+ 文档「Skills › Limitations」）
MAX_SKILL_MD_BYTES = 10 * 1024 * 1024
MAX_DESCRIPTION_CHARS = 1024
MAX_COMPATIBILITY_CHARS = 500
MAX_SKILL_NAME_CHARS = 64
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def find_skills_root(skills_dir: str | None = None) -> str | None:
    """Find the skills root directory.

    Args:
        skills_dir: Explicit skills directory path from config. When given,
            it is honored directly (no silent fallback to bundled skills) so
            that the runtime path stays consistent with the configuration.
            The directory is created if missing (best-effort, e.g. an external
            skills mount point).

    Returns:
        Absolute path to the skills root directory, or None if not found.
    """
    if skills_dir:
        # 显式配置优先：直接采用配置路径，不再因目录不存在而静默回退到包内 skills，
        # 保证启动日志与 config.tools.skills_dir 完全一致。
        p = Path(skills_dir)
        if not p.exists():
            try:
                p.mkdir(parents=True, exist_ok=True)
            except Exception:
                # 外部挂载点可能无需创建；即便为空也保持配置值，不回退
                pass
        return str(p.absolute())

    search_paths = [
        Path("skills"),
        Path("easy_agent") / "skills",
        Path(__file__).parent / "skills",
    ]

    for search_path in search_paths:
        if search_path.exists() and search_path.is_dir():
            # 仓库根 skills/ 为 canonical；包内 easy_agent/skills 仅作回退。
            return str(search_path.absolute())

    return None


def discover_skills(skills_dir: str | None = None) -> list[dict]:
    """从 skills 目录中发现可用的技能

    Args:
        skills_dir: Skills 目录路径

    Returns:
        技能信息字典列表，每项包含 name 和 path
    """
    skills = []
    seen_names = set()

    # 显式配置 skills_dir 时只扫描该路径（与 find_skills_root 行为一致，不回退包内 skills）；
    # 未配置时才回退到默认搜索路径。
    if skills_dir:
        search_paths = [Path(skills_dir)]
    else:
        search_paths = [
            Path("skills"),
            Path("easy_agent") / "skills",
            Path(__file__).parent / "skills",
        ]

    for search_path in search_paths:
        if not search_path.exists():
            continue

        for skill_dir in sorted(search_path.iterdir()):
            if not skill_dir.is_dir():
                continue

            skill_name = skill_dir.name
            if skill_name in seen_names:
                continue

            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                try:
                    size = skill_md.stat().st_size
                except OSError:
                    continue
                # 官方发现阶段会跳过 >10MB 的 SKILL.md；这里提前过滤，避免它出现在清单里却加载不到
                if size > MAX_SKILL_MD_BYTES:
                    logger.warning(
                        "技能 %s 的 SKILL.md 超过 10MB（%s 字节），已跳过", skill_name, size
                    )
                    continue
                seen_names.add(skill_name)
                skills.append(
                    {
                        "name": skill_name,
                        "path": str(skill_dir.absolute()),
                        "description_file": "SKILL.md",
                    }
                )

    return skills


logger = logging.getLogger(__name__)


def parse_skill_frontmatter(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """解析 SKILL.md 的 YAML frontmatter；返回 (meta, error)。"""
    if not text.startswith("---"):
        return None, "缺少 frontmatter（文件需以 --- 开头）"
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, "frontmatter 未闭合（缺少结束 ---）"
    raw = parts[1]
    try:
        import yaml

        meta = yaml.safe_load(raw)
    except Exception as exc:  # noqa: BLE001
        return None, f"frontmatter YAML 解析失败: {exc}"
    if not isinstance(meta, dict):
        return None, "frontmatter 不是键值映射"
    return meta, None



def declared_include_tools(skill_dirs: Iterable[str | Path] | None = None) -> frozenset[str]:
    """收集技能 frontmatter 里 ``metadata.include_tools`` 声明的工具名（DA-13）。

    官方约定是**空格分隔的字符串**（``include_tools: save_expert fetch_url``）；
    YAML 列表形式框架不会匹配，这里同样忽略并告警，避免"写了却不生效"。

    只扫描 ``<dir>/<skill>/SKILL.md`` 一层：与 SkillsMiddleware 的发现规则一致，
    不递归、不读 references/ 与 scripts/。

    Args:
        skill_dirs: 技能源目录（真实路径），通常为 canonical 公共技能目录 + 用户技能目录。

    Returns:
        工具名集合；目录不存在或 frontmatter 解析失败时跳过。
    """
    names: set[str] = set()
    for root in skill_dirs or ():
        base = Path(root)
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            skill_md = entry / "SKILL.md"
            if not entry.is_dir() or not skill_md.is_file():
                continue
            try:
                text = skill_md.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            meta, error = parse_skill_frontmatter(text)
            if error or not isinstance(meta, dict):
                continue
            extra = meta.get("metadata")
            raw = extra.get("include_tools") if isinstance(extra, dict) else None
            if raw is None:
                continue
            if not isinstance(raw, str):
                logger.warning(
                    "技能 %s 的 metadata.include_tools 不是字符串（YAML 列表不会被框架匹配），已忽略",
                    entry.name,
                )
                continue
            names.update(raw.split())
    return frozenset(names)


def _validate_skill_meta(dir_name: str, meta: dict[str, Any]) -> list[str]:
    """按官方 Agent Skills 规范校验单个技能的 frontmatter（name/description/上限）。"""
    errors: list[str] = []
    name = str(meta.get("name") or "").strip()
    if not name:
        errors.append("frontmatter.name 为空")
    else:
        if name != dir_name:
            errors.append(f"frontmatter.name（{name}）必须与目录名（{dir_name}）一致")
        if len(name) > MAX_SKILL_NAME_CHARS:
            errors.append(f"frontmatter.name 超过 {MAX_SKILL_NAME_CHARS} 字符")
        if not _SKILL_NAME_RE.match(name):
            errors.append("frontmatter.name 只能包含小写字母、数字与单个连字符（不能有空格/下划线/大写）")

    description = str(meta.get("description") or "").strip()
    if not description:
        errors.append("frontmatter.description 为空")
    elif len(description) > MAX_DESCRIPTION_CHARS:
        errors.append(f"frontmatter.description 超过 {MAX_DESCRIPTION_CHARS} 字符")

    compatibility = meta.get("compatibility")
    if compatibility is not None and len(str(compatibility)) > MAX_COMPATIBILITY_CHARS:
        errors.append(f"frontmatter.compatibility 超过 {MAX_COMPATIBILITY_CHARS} 字符")

    extra = meta.get("metadata")
    if isinstance(extra, dict):
        raw = extra.get("include_tools")
        if raw is not None and not isinstance(raw, str):
            errors.append(
                "metadata.include_tools 必须是空格分隔字符串（YAML 列表形式框架不会匹配）"
            )
    return errors


def validate_skills(skills_dir: str | None = None) -> list[dict]:
    """校验每个 SKILL.md 的 frontmatter（name/description 必填）。

    返回 ``[{"name", "path", "errors": [...]}]``；无问题的技能 ``errors`` 为空。
    """
    results: list[dict] = []
    for skill in discover_skills(skills_dir):
        path = Path(skill["path"]) / "SKILL.md"
        errors: list[str] = []
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            results.append({"name": skill["name"], "path": str(path), "errors": [f"读取失败: {exc}"]})
            continue
        meta, error = parse_skill_frontmatter(text)
        if error:
            errors.append(error)
        elif meta is not None:
            errors.extend(_validate_skill_meta(skill["name"], meta))
        results.append({"name": skill["name"], "path": str(path), "errors": errors})
    return results
