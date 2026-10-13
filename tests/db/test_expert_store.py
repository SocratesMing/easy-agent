"""ExpertStore（expert_agents 表）的 CRUD 与归属校验。

覆盖点：
- 建表后表结构存在（与 EXPECTED_TABLES / SCHEMA_VERSION 联动）
- JSON 字段的编解码（落库是字符串，读出来是数组）
- 归属校验：他人资源一律查不到
- 列表分页 / 关键词 / 状态过滤 / disabled 默认排除
- 软删除与物理删除
"""

import pytest

from easy_agent.db.database import EXPECTED_TABLES, SCHEMA_VERSION
from easy_agent.db.expert_store import (
    STATUS_ACTIVE,
    STATUS_BUILDING,
    STATUS_DISABLED,
    ExpertStore,
)


def _make(store, expert_id="expert_a_00000001", user_id="u1", **overrides):
    fields = {
        "export_agent_id": expert_id,
        "session_id": "s1",
        "user_id": user_id,
        "tag_id": "finance",
        "export_name_en": "lianghua-abc12345",
        "export_name_cn": "量化策略专家",
        "export_prompt": "帮我创建一个量化策略专家",
        "export_skills_ids": [],
        "export_features_prompt": ["帮我选股", "帮我做因子分析"],
        "export_file_path": "/tmp/experts/expert_a_00000001/v1",
        "agent_status": STATUS_ACTIVE,
        "export_version": 1,
    }
    fields.update(overrides)
    store.create(fields)
    return fields


# ── 表结构 ────────────────────────────────────────────────────────────


def test_expert_agents_table_created(db):
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = {r[0] for r in cur.fetchall()}
    assert "expert_agents" in names
    assert "expert_agents" in EXPECTED_TABLES
    assert SCHEMA_VERSION >= 2


def test_expert_agents_columns(db):
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(expert_agents)")
        cols = {r[1] for r in cur.fetchall()}
    expected = {
        "export_agent_id",
        "session_id",
        "user_id",
        "tag_id",
        "export_name_en",
        "export_name_cn",
        "export_prompt",
        "export_skills_ids",
        "export_features_prompt",
        "export_file_path",
        "agent_status",
        "export_version",
        "create_timestamp",
        "update_timestamp",
    }
    assert expected.issubset(cols)
    # 需求已标注「（删除）」的字段不应被建出来
    assert "export_run_file_path" not in cols


def test_expert_agents_indexes(db):
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='index'")
        names = {r[0] for r in cur.fetchall()}
    assert "idx_expert_agents_user" in names
    assert "idx_expert_agents_status" in names


# ── 增删改查 ──────────────────────────────────────────────────────────


def test_create_and_get(db):
    store = ExpertStore(db)
    _make(store)

    record = store.get("expert_a_00000001")
    assert record is not None
    assert record["export_name_cn"] == "量化策略专家"
    assert record["agent_status"] == STATUS_ACTIVE
    assert record["export_version"] == 1
    assert record["create_timestamp"] and record["update_timestamp"]


def test_json_fields_roundtrip(db):
    """列表型字段落库为 JSON 字符串，读出来必须是 list。"""
    store = ExpertStore(db)
    _make(store)

    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT export_features_prompt FROM expert_agents WHERE export_agent_id=?",
            ("expert_a_00000001",),
        )
        raw = cur.fetchone()[0]
    assert isinstance(raw, str)  # 库里是字符串

    record = store.get("expert_a_00000001")
    assert record["export_features_prompt"] == ["帮我选股", "帮我做因子分析"]
    assert record["export_skills_ids"] == []


def test_json_field_bad_data_degrades_to_empty_list(db):
    """脏数据不应把列表接口打挂。"""
    store = ExpertStore(db)
    _make(store)
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE expert_agents SET export_features_prompt=? WHERE export_agent_id=?",
            ("not-a-json", "expert_a_00000001"),
        )
    record = store.get("expert_a_00000001")
    assert record["export_features_prompt"] == []


def test_update_fields_whitelist_and_timestamp(db):
    store = ExpertStore(db)
    _make(store)
    before = store.get("expert_a_00000001")

    store.update_fields(
        "expert_a_00000001",
        {
            "export_name_cn": "新名字",
            "agent_status": STATUS_ACTIVE,
            "export_version": 2,
            "export_agent_id": "hacked",  # 非白名单列，应被忽略
            "user_id": "hacked",          # 非白名单列，应被忽略
        },
    )

    after = store.get("expert_a_00000001")
    assert after["export_name_cn"] == "新名字"
    assert after["export_version"] == 2
    # 主键与归属键不可被改写
    assert after["export_agent_id"] == "expert_a_00000001"
    assert after["user_id"] == "u1"
    assert after["update_timestamp"] >= before["update_timestamp"]


def test_create_defaults(db):
    """只给必填字段时，其余列取默认值（先落 building 占位行的场景）。"""
    store = ExpertStore(db)
    store.create(
        {
            "export_agent_id": "expert_min_00000002",
            "user_id": "u1",
            "export_name_en": "min-00000002",
            "export_name_cn": "最小专家",
        }
    )
    record = store.get("expert_min_00000002")
    assert record["agent_status"] == STATUS_BUILDING
    assert record["export_version"] == 1
    assert record["session_id"] == ""
    assert record["tag_id"] == ""
    assert record["export_file_path"] == ""
    assert record["export_features_prompt"] == []


# ── 归属校验 ──────────────────────────────────────────────────────────


def test_get_owned_rejects_other_user(db):
    store = ExpertStore(db)
    _make(store, user_id="owner")

    assert store.get_owned("expert_a_00000001", "owner") is not None
    assert store.get_owned("expert_a_00000001", "intruder") is None
    assert store.get_owned("not-exist", "owner") is None
    assert store.get_owned("", "owner") is None


# ── 列表 ──────────────────────────────────────────────────────────────


def test_list_by_user_only_own(db):
    store = ExpertStore(db)
    _make(store, expert_id="e1", user_id="u1", export_name_cn="甲")
    _make(store, expert_id="e2", user_id="u1", export_name_cn="乙")
    _make(store, expert_id="e3", user_id="u2", export_name_cn="别人的")

    total, items = store.list_by_user("u1")
    assert total == 2
    assert {i["export_agent_id"] for i in items} == {"e1", "e2"}


def test_list_keyword_and_status_filter(db):
    store = ExpertStore(db)
    _make(store, expert_id="e1", user_id="u1", export_name_cn="量化策略专家")
    _make(store, expert_id="e2", user_id="u1", export_name_cn="财报分析师")
    _make(
        store,
        expert_id="e3",
        user_id="u1",
        export_name_cn="失败专家",
        agent_status="failed",
    )

    total, items = store.list_by_user("u1", keyword="量化")
    assert total == 1
    assert items[0]["export_agent_id"] == "e1"

    total, items = store.list_by_user("u1", status="failed")
    assert total == 1
    assert items[0]["export_agent_id"] == "e3"


def test_list_pagination(db):
    store = ExpertStore(db)
    for i in range(5):
        _make(store, expert_id=f"e{i}", user_id="u1", export_name_cn=f"专家{i}")

    total, page1 = store.list_by_user("u1", page=1, page_size=2)
    assert total == 5
    assert len(page1) == 2

    total, page3 = store.list_by_user("u1", page=3, page_size=2)
    assert total == 5
    assert len(page3) == 1


def test_list_excludes_disabled_by_default(db):
    store = ExpertStore(db)
    _make(store, expert_id="e1", user_id="u1")
    _make(store, expert_id="e2", user_id="u1")

    assert store.delete("e2", "u1", hard=False) is True

    total, _ = store.list_by_user("u1")
    assert total == 1

    total_all, _ = store.list_by_user("u1", include_disabled=True)
    assert total_all == 2


# ── 删除 ──────────────────────────────────────────────────────────────


def test_soft_delete_marks_disabled(db):
    store = ExpertStore(db)
    _make(store)

    assert store.delete("expert_a_00000001", "u1", hard=False) is True
    record = store.get("expert_a_00000001")
    assert record is not None
    assert record["agent_status"] == STATUS_DISABLED


def test_soft_delete_rejects_other_user(db):
    store = ExpertStore(db)
    _make(store, user_id="owner")
    assert store.delete("expert_a_00000001", "intruder", hard=False) is False
    assert store.get("expert_a_00000001")["agent_status"] == STATUS_ACTIVE


def test_hard_delete_removes_row(db):
    store = ExpertStore(db)
    _make(store)

    assert store.delete("expert_a_00000001", "u1", hard=True) is True
    assert store.get("expert_a_00000001") is None


def test_hard_delete_rejects_other_user(db):
    store = ExpertStore(db)
    _make(store, user_id="owner")
    assert store.delete("expert_a_00000001", "intruder", hard=True) is False
    assert store.get("expert_a_00000001") is not None
