"""Skill discovery utilities"""

from pathlib import Path


def find_skills_root(skills_dir: str | None = None) -> str | None:
    """Find the first existing skills root directory.

    Args:
        skills_dir: Explicit skills directory path from config.

    Returns:
        Absolute path to the skills root directory, or None if not found.
    """
    search_paths = []
    if skills_dir:
        search_paths.append(Path(skills_dir))

    search_paths.extend(
        [
            Path("skills"),
            Path("easy_agent") / "skills",
            Path(__file__).parent / "skills",
        ]
    )

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

    search_paths = []
    if skills_dir:
        search_paths.append(Path(skills_dir))

    search_paths.extend(
        [
            Path("skills"),
            Path("easy_agent") / "skills",
            Path(__file__).parent / "skills",
        ]
    )

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
