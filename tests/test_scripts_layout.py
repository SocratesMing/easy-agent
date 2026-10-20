"""脚本目录结构守卫：按功能分组，禁止再把脚本堆回 scripts/ 根。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

GROUPS = {
    "quality",
    "db",
    "deps",
    "release",
    "docs",
    "skills",
    "dev",
}

EXPECTED_ENTRYPOINTS = {
    "quality/import_graph.py",
    "quality/check_double_write.py",
    "quality/check_config_schema.py",
    "quality/check_env_usage.py",
    "quality/check_multi_replica.py",
    "quality/double_write_allowlists.py",
    "quality/import_graph_core.py",
    "db/migrate_db.py",
    "db/migrate_sqlite_to_mysql.py",
    "db/mysql_target.py",
    "db/prune_checkpoint_threads.py",
    "deps/download_backend_deps.py",
    "docs/generate_api_docs.py",
    "skills/sync_skills.py",
    "release/release_preflight.py",
}


def test_scripts_root_has_no_loose_scripts():
    loose = sorted(
        path.name
        for path in SCRIPTS.iterdir()
        if path.is_file() and path.name not in {"README.md", "__init__.py"}
    )
    assert loose == [], f"脚本应按功能归入子目录，发现根目录散落: {loose}"


def test_expected_groups_and_entrypoints_exist():
    dirs = {path.name for path in SCRIPTS.iterdir() if path.is_dir()}
    assert GROUPS <= dirs
    for relative in EXPECTED_ENTRYPOINTS:
        assert (SCRIPTS / relative).is_file(), relative


def test_every_group_has_init_package():
    for group in GROUPS:
        assert (SCRIPTS / group / "__init__.py").is_file(), group
