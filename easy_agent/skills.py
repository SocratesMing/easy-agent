"""Skill discovery utilities"""

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
            skill_readme = skill_dir / "README.md"

            if skill_md.exists() or skill_readme.exists():
                seen_names.add(skill_name)
                skills.append(
                    {
                        "name": skill_name,
                        "path": str(skill_dir.absolute()),
                        "description_file": "SKILL.md"
                        if skill_md.exists()
                        else "README.md",
                    }
                )

    return skills
