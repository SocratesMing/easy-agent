"""外部目录树同步（catalog_sync）单元测试。

不依赖外部 MySQL / Ragflow：SQL 解析为纯函数测试；同步引擎用临时
SQLite 主库验证目录树创建、幂等、层级与同名约束（知识库为纯本地
创建，不再依赖 Ragflow 客户端）。
"""

from types import SimpleNamespace

import pytest

from easy_agent.knowledges.catalog_sync import (
    CatalogSyncError,
    CatalogSyncer,
    run_catalog_sync,
    split_sql_statements,
    startup_catalog_sync,
)
from easy_agent.knowledges.config import (
    CatalogSyncConfig,
    KnowledgeConfig,
    KnowledgeConfigError,
)
from easy_agent.knowledges.repository import KnowledgeRepository
from easy_agent.knowledges.schema import initialize_knowledge_schema


class _FakeApp:
    def __init__(self):
        self.state = SimpleNamespace()


def _catalog_rows() -> list[dict]:
    """模拟 kb_catalog 目录树：库→年→月→日→机构（含不同日期下同名机构）。"""
    return [
        dict(id=509, parent_id=0, name="md投研报告", full_path="md投研报告",
             level=1, library="md投研报告", sort=0, status="active"),
        dict(id=730, parent_id=509, name="2026", full_path="md投研报告/2026",
             level=2, library="md投研报告", sort=0, status="active"),
        dict(id=731, parent_id=730, name="2026-09", full_path="md投研报告/2026/2026-09",
             level=3, library="md投研报告", sort=0, status="active"),
        dict(id=732, parent_id=731, name="2026-09-12",
             full_path="md投研报告/2026/2026-09/2026-09-12",
             level=4, library="md投研报告", sort=0, status="active"),
        dict(id=733, parent_id=732, name="中金公司",
             full_path="md投研报告/2026/2026-09/2026-09-12/中金公司",
             level=5, library="md投研报告", sort=0, status="active"),
        dict(id=734, parent_id=731, name="2026-09-13",
             full_path="md投研报告/2026/2026-09/2026-09-13",
             level=4, library="md投研报告", sort=1, status="active"),
        dict(id=735, parent_id=734, name="中金公司",
             full_path="md投研报告/2026/2026-09/2026-09-13/中金公司",
             level=5, library="md投研报告", sort=0, status="active"),
        dict(id=736, parent_id=734, name="中信证券",
             full_path="md投研报告/2026/2026-09/2026-09-13/中信证券",
             level=5, library="md投研报告", sort=1, status="active"),
        # 非活跃节点：不应同步
        dict(id=737, parent_id=731, name="2026-09-14",
             full_path="md投研报告/2026/2026-09/2026-09-14",
             level=4, library="md投研报告", sort=2, status="inactive"),
    ]


def _make_syncer(db, **sync_kwargs) -> CatalogSyncer:
    return CatalogSyncer(
        db=db,
        repository=KnowledgeRepository(db),
        config=KnowledgeConfig(enabled=True, base_url="http://ragflow", api_key="rk"),
        sync_config=CatalogSyncConfig(enabled=True, **sync_kwargs),
        owner_user_id="u-admin",
    )


def _query(db, sql: str, params: tuple = ()) -> list[dict]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(cursor, sql, params)
        return [dict(row) for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# SQL 语句分割
# ---------------------------------------------------------------------------
def test_split_sql_statements():
    sql = (
        "-- 头部注释\n"
        "SET NAMES utf8mb4;\n"
        "SET FOREIGN_KEY_CHECKS = 0;\n"
        "DROP TABLE IF EXISTS `kb_catalog`;\n"
        "CREATE TABLE `t` (`a` int, `b` varchar(10) COMMENT='x;y');\n"
        "INSERT INTO `t` VALUES ('it''s;fine', 'a\\;b', 1);\n"
        "/* 块注释 ; 分号 */\n"
        "INSERT INTO `t` VALUES (2);\n"
    )
    statements = split_sql_statements(sql)
    assert len(statements) == 6
    assert statements[0] == "SET NAMES utf8mb4"
    assert statements[2] == "DROP TABLE IF EXISTS `kb_catalog`"
    assert "COMMENT='x;y'" in statements[3]
    assert "'it''s;fine'" in statements[4]
    assert "'a\\;b'" in statements[4]
    assert statements[5] == "INSERT INTO `t` VALUES (2)"


def test_split_sql_statements_trailing_without_semicolon():
    statements = split_sql_statements("SELECT 1;\nSELECT 2")
    assert statements == ["SELECT 1", "SELECT 2"]


# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------
def test_catalog_sync_config_defaults():
    config = KnowledgeConfig.from_mapping({})
    assert config.catalog_sync.enabled is False
    assert config.catalog_sync.database == "ai_kb"
    assert config.catalog_sync.space_type == "team"
    assert config.catalog_sync.max_level == 5


def test_catalog_sync_config_section_and_env_fallback(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_CATALOG_SYNC_ENABLED", "true")
    monkeypatch.setenv("KNOWLEDGE_CATALOG_SYNC_SQL_FILE", "/tmp/dump.sql")
    config = KnowledgeConfig.from_mapping({})
    assert config.catalog_sync.enabled is True
    assert config.catalog_sync.sql_file == "/tmp/dump.sql"

    config2 = KnowledgeConfig.from_mapping({
        "enabled": True,
        "base_url": "http://r",
        "api_key": "k",
        "catalog_sync": {
            "enabled": True,
            "sql_file": "x.sql",
            "database": "ai_kb2",
            "space_type": "personal",
            "max_level": 4,
            "mysql": {"host": "h", "port": 3307, "user": "u", "password": "p"},
        },
    })
    assert config2.catalog_sync.sql_file == "x.sql"
    assert config2.catalog_sync.database == "ai_kb2"
    assert config2.catalog_sync.space_type == "personal"
    assert config2.catalog_sync.max_level == 4
    assert config2.catalog_sync.mysql.host == "h"
    assert config2.catalog_sync.mysql.port == 3307
    assert config2.catalog_sync.mysql.password.get_secret_value() == "p"


def test_catalog_sync_config_rejects_bad_space_type():
    with pytest.raises(KnowledgeConfigError):
        KnowledgeConfig.from_mapping({"catalog_sync": {"space_type": "shared"}})


# ---------------------------------------------------------------------------
# 同步引擎（SQLite 主库 + 假 Ragflow）
# ---------------------------------------------------------------------------
async def test_sync_creates_base_and_folder_tree(db):
    syncer = _make_syncer(db)
    stats = await syncer.sync(_catalog_rows())

    assert stats["bases_created"] == 1
    # 2026 / 2026-09 / 09-12 / 09-13 / 中金×2 / 中信 = 7（inactive 09-14 不同步）
    assert stats["folders_created"] == 7
    assert stats["skipped"] == 0

    bases = _query(db, "SELECT * FROM knowledge_bases")
    assert len(bases) == 1
    assert bases[0]["name"] == "md投研报告"
    assert bases[0]["status"] == "active"
    # 纯本地创建：不绑定自有数据集（NULL → 共享数据集模式）
    assert bases[0]["remote_dataset_id"] is None
    assert bases[0]["space_type"] == "team"
    assert bases[0]["owner_user_id"] == "u-admin"

    folders = _query(db, "SELECT * FROM knowledge_folders")
    by_name: dict[str, list[dict]] = {}
    for folder in folders:
        by_name.setdefault(folder["name"], []).append(folder)
    assert set(by_name) == {"2026", "2026-09", "2026-09-12", "2026-09-13", "中金公司", "中信证券"}
    # 年份挂在根级（parent NULL），月份挂年份
    assert by_name["2026"][0]["parent_id"] is None
    assert by_name["2026-09"][0]["parent_id"] == by_name["2026"][0]["id"]
    # 不同日期下允许同名机构文件夹（放宽后的唯一约束）
    assert len(by_name["中金公司"]) == 2
    date_ids = {by_name["2026-09-12"][0]["id"], by_name["2026-09-13"][0]["id"]}
    assert {folder["parent_id"] for folder in by_name["中金公司"]} == date_ids
    assert by_name["中信证券"][0]["parent_id"] == by_name["2026-09-13"][0]["id"]


async def test_sync_is_idempotent(db):
    syncer = _make_syncer(db)
    first = await syncer.sync(_catalog_rows())
    second = await syncer.sync(_catalog_rows())

    assert second["bases_created"] == 0
    assert second["bases_existing"] == 1
    assert second["folders_created"] == 0
    assert second["folders_existing"] == 7
    assert len(_query(db, "SELECT * FROM knowledge_bases")) == 1
    assert len(_query(db, "SELECT * FROM knowledge_folders")) == 7
    assert first["bases_created"] == 1  # 首次结果不受影响


async def test_sync_respects_max_level(db):
    syncer = _make_syncer(db, max_level=4)
    stats = await syncer.sync(_catalog_rows())
    # 机构层（level 5）被跳过：中金×2 + 中信
    assert stats["folders_created"] == 4
    assert stats["skipped"] == 3
    names = {folder["name"] for folder in _query(db, "SELECT * FROM knowledge_folders")}
    assert "中金公司" not in names


async def test_sync_reuses_legacy_base_with_own_dataset(db):
    # 存量库已绑定自有数据集：按名称复用且不改绑定（迁共享由迁移脚本负责）
    repo = KnowledgeRepository(db)
    existing = repo.create_base(
        name="md投研报告", description="", space_type="team", owner_user_id="u-admin",
        department_id=None, embedding_model="",
    )
    repo.update_base(existing["id"], remote_dataset_id="ds-legacy", status="active")

    syncer = _make_syncer(db)
    stats = await syncer.sync(_catalog_rows())

    assert stats["bases_existing"] == 1
    assert stats["bases_created"] == 0
    bases = _query(db, "SELECT * FROM knowledge_bases")
    assert len(bases) == 1
    assert bases[0]["remote_dataset_id"] == "ds-legacy"


# ---------------------------------------------------------------------------
# 文件夹唯一约束（新约束 + 存量迁移）
# ---------------------------------------------------------------------------
def test_folder_unique_constraint_scope(db):
    repo = KnowledgeRepository(db)
    base = repo.create_base(
        name="b", description="", space_type="team", owner_user_id="u",
        department_id=None, embedding_model="",
    )
    d1 = repo.create_folder(base_id=base["id"], name="d1")
    d2 = repo.create_folder(base_id=base["id"], name="d2")
    # 同父下同名 → 违反唯一约束
    repo.create_folder(base_id=base["id"], name="same", parent_id=d1["id"])
    with pytest.raises(Exception):
        repo.create_folder(base_id=base["id"], name="same", parent_id=d1["id"])
    # 不同父下同名 → 允许
    other = repo.create_folder(base_id=base["id"], name="same", parent_id=d2["id"])
    assert other["name"] == "same"
    # 根级查重兜底
    assert repo.find_folder_by_name(base["id"], "d1", None) is not None
    assert repo.find_folder_by_name(base["id"], "same", d1["id"]) is not None
    assert repo.find_folder_by_name(base["id"], "not-exist", None) is None


def test_legacy_folder_unique_constraint_migrated(db):
    # 模拟存量库：把唯一约束降回旧版 (base_id, name)
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DROP TABLE knowledge_folders")
        cursor.execute("""
            CREATE TABLE knowledge_folders (
                id VARCHAR(255) PRIMARY KEY,
                base_id VARCHAR(255) NOT NULL,
                parent_id VARCHAR(255),
                name VARCHAR(127) NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at VARCHAR(50) NOT NULL,
                updated_at VARCHAR(50) NOT NULL,
                UNIQUE(base_id, name)
            )
        """)
    # 幂等重跑 schema 初始化 → 迁移为 (base_id, parent_id, name)
    with db.get_connection() as conn:
        initialize_knowledge_schema(db, conn.cursor())

    repo = KnowledgeRepository(db)
    base = repo.create_base(
        name="legacy", description="", space_type="team", owner_user_id="u",
        department_id=None, embedding_model="",
    )
    d1 = repo.create_folder(base_id=base["id"], name="2026")
    d2 = repo.create_folder(base_id=base["id"], name="2027")
    repo.create_folder(base_id=base["id"], name="中金公司", parent_id=d1["id"])
    ok = repo.create_folder(base_id=base["id"], name="中金公司", parent_id=d2["id"])
    assert ok["name"] == "中金公司"


# ---------------------------------------------------------------------------
# 入口与启动钩子
# ---------------------------------------------------------------------------
async def test_run_catalog_sync_requires_sql_file(db):
    app = _FakeApp()
    app.state.knowledge_config = KnowledgeConfig(
        enabled=True, base_url="http://r", api_key="k",
        catalog_sync={"enabled": True},
    )
    app.state.db = db
    with pytest.raises(CatalogSyncError, match="sql_file"):
        await run_catalog_sync(app)


async def test_run_catalog_sync_rejects_missing_file(db, tmp_path):
    app = _FakeApp()
    app.state.knowledge_config = KnowledgeConfig(
        enabled=True, base_url="http://r", api_key="k",
        catalog_sync={"enabled": True, "sql_file": str(tmp_path / "nope.sql")},
    )
    app.state.db = db
    with pytest.raises(CatalogSyncError, match="SQL 文件不存在"):
        await run_catalog_sync(app)


async def test_startup_catalog_sync_disabled_is_noop(db):
    app = _FakeApp()
    app.state.knowledge_config = KnowledgeConfig(
        enabled=True, base_url="http://r", api_key="k",
    )
    await startup_catalog_sync(app)
    assert getattr(app.state, "catalog_sync_task", None) is None


async def test_startup_catalog_sync_skips_when_module_disabled(db):
    app = _FakeApp()
    app.state.knowledge_config = KnowledgeConfig(
        enabled=False,
        catalog_sync={"enabled": True, "sql_file": "x.sql"},
    )
    await startup_catalog_sync(app)
    assert getattr(app.state, "catalog_sync_task", None) is None
