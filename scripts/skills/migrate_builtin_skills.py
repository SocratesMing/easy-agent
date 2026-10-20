#!/usr/bin/env python3
"""把复制进用户目录的公共技能副本迁移到公共技能清单（DA-13 S2）。

S2 之前，技能中心「添加」会把公共技能整份复制进 ``workspace/{user}/skills/``。S2 起公共技能
只读共享（``/skills/builtin/``），添加只在 ``builtin-skills.json`` 里记一条。迁移规则：

- 副本与公共技能内容完全一致：删掉副本，记进清单（启停状态沿用）；
- 副本被用户改过：保留为用户技能（同名覆盖公共技能，相当于 fork）。

每个用户第一次访问技能库时也会自动迁移一次；本脚本用于上线时一次性处理全部用户
（与 ``easy-web`` 一样先切到项目根，配置里的相对路径按项目根解析）::

    python scripts/skills/migrate_builtin_skills.py              # 预览，不改文件
    python scripts/skills/migrate_builtin_skills.py --apply      # 执行
    python scripts/skills/migrate_builtin_skills.py --user alice --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from easy_agent.agent.builtin_skills import (  # noqa: E402
    USER_DISABLED_DIR_NAME,
    USER_SKILLS_DIR_NAME,
    builtin_root,
    load_store,
    migrate_user_copies,
)


def users_with_skills(workspace_root: Path) -> list[str]:
    """工作区根下有技能目录（启用或停用）的用户。"""
    if not workspace_root.is_dir():
        return []
    return sorted(
        entry.name
        for entry in workspace_root.iterdir()
        if entry.is_dir()
        and not entry.name.startswith(".")
        and ((entry / USER_SKILLS_DIR_NAME).is_dir() or (entry / USER_DISABLED_DIR_NAME).is_dir())
    )


def migrate_all(usernames: list[str], root: Path, *, apply: bool, config=None) -> list[tuple[str, dict | None]]:
    """逐个用户迁移；已迁移过的跳过（报告为 ``None``）。"""
    results = []
    for username in usernames:
        if load_store(username, config).get("migrated"):
            results.append((username, None))
            continue
        results.append((username, migrate_user_copies(username, root, apply=apply, config=config)))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="公共技能副本迁移到公共技能清单（DA-13 S2）")
    parser.add_argument("--apply", action="store_true", help="执行迁移（默认只预览）")
    parser.add_argument("--user", action="append", default=[], help="只处理指定用户（可重复）")
    args = parser.parse_args(argv)

    from easy_agent.bootstrap.initialization import initialize_runtime

    os.chdir(PROJECT_ROOT)
    config = initialize_runtime().config
    if config is None:
        print("ERROR: 配置未加载", file=sys.stderr)
        return 2
    root = builtin_root(config)
    if root is None:
        print("未配置公共技能目录（tools.skills_dir），无需迁移")
        return 0

    usernames = args.user or users_with_skills(Path(config.agent.workspace_dir))
    removed = kept = 0
    for username, report in migrate_all(usernames, root, apply=args.apply, config=config):
        if report is None:
            print(f"{username}: 已迁移过，跳过")
            continue
        removed += len(report["removed"])
        kept += len(report["kept"])
        print(f"{username}: 删除副本 {report['removed'] or '-'}；保留为用户技能（已修改） {report['kept'] or '-'}")
    print(f"合计：删除 {removed} 个与公共版本一致的副本，保留 {kept} 个改过的副本")
    if not args.apply:
        print("预览模式：没有修改任何文件；确认后加 --apply 执行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
