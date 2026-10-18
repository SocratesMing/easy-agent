"""Skill discovery utilities"""

import logging
from typing import Any

from pathlib import Path


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
            if not str(meta.get("name") or "").strip():
                errors.append("frontmatter.name 为空")
            if not str(meta.get("description") or "").strip():
                errors.append("frontmatter.description 为空")
        results.append({"name": skill["name"], "path": str(path), "errors": errors})
    return results
