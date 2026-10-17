"""C1 剩余域：``ExpertRepository`` 委托、归属校验与会话持久化。"""

from easy_agent.db.database import Database
from easy_agent.db.expert_store import STATUS_ACTIVE
from easy_agent.db.repositories.expert_repository import ExpertRepository
from easy_agent.models.db import SessionModel


def _make_db(tmp_path) -> Database:
    db = Database(
        {"type": "sqlite", "sqlite": {"path": str(tmp_path / "experts.db")}}
    )
    db.init_tables()
    return db


def _expert_fields(expert_id: str, user_id: str, name: str = "量化策略专家") -> dict:
    return {
        "export_agent_id": expert_id,
        "session_id": "",
        "user_id": user_id,
        "tag_id": "finance",
        "export_name_en": "lianghua",
        "export_name_cn": name,
        "export_prompt": "帮我创建一个量化策略专家",
        "export_description": "面向量化投研场景。",
        "export_skills_names": ["因子分析"],
        "export_features_prompt": ["帮我选股"],
        "export_file_path": "/tmp/experts/v1",
        "agent_status": STATUS_ACTIVE,
        "export_version": 1,
    }


def test_repository_delegates_expert_crud_and_owner_scoping(tmp_path):
    db = _make_db(tmp_path)
    repo = ExpertRepository(db)
    alice = db.register_user("alice", "secret123")
    db.register_user("bob", "secret123")

    assert repo.database is db
    assert repo.get_user_by_username("alice").user_id == alice.user_id

    repo.create(_expert_fields("expert-a", alice.user_id))
    record = repo.get_owned("expert-a", alice.user_id)
    assert record["export_name_cn"] == "量化策略专家"
    assert repo.get_owned("expert-a", "other-user") is None
    assert repo.get("expert-a")["export_name_cn"] == "量化策略专家"

    repo.update_fields("expert-a", {"export_description": "更新后的描述"})
    assert repo.get("expert-a")["export_description"] == "更新后的描述"

    assert repo.find_by_name_cn("wrong-user", "量化策略专家") is None
    assert repo.find_by_name_cn(alice.user_id, " 量化策略专家 ") is not None
    repo.create(_expert_fields("expert-b", alice.user_id, name="量化策略专家"))
    assert repo.find_by_name_cn(alice.user_id, "量化策略专家") is not None

    total, items = repo.list_by_user(alice.user_id)
    assert total == 2
    assert {item["export_agent_id"] for item in items} == {"expert-a", "expert-b"}

    assert repo.delete("expert-b", alice.user_id) is True
    total, items = repo.list_by_user(alice.user_id)
    assert total == 1
    assert items[0]["export_agent_id"] == "expert-a"

    assert repo.delete("expert-a", alice.user_id, hard=True) is True
    assert repo.get("expert-a") is None
    assert repo.get_owned("expert-a", alice.user_id) is None


def test_repository_delegates_expert_session_and_message_writes(tmp_path):
    db = _make_db(tmp_path)
    repo = ExpertRepository(db)
    now = "2026-10-18T00:00:00"
    session = SessionModel(
        session_id="sess-expert",
        title="专家会话",
        messages=[],
        created_at=now,
        updated_at=now,
        username="alice",
        workspace_name="ws-expert",
    )

    repo.create_session(session)
    repo.update_session_workspace_name("sess-expert", "ws-renamed")
    loaded = repo.get_session("sess-expert")
    assert loaded.workspace_name == "ws-renamed"

    repo.add_message(
        "sess-expert",
        {"role": "user", "content": "你好", "timestamp": now},
    )
    repo.update_session_meta("sess-expert", "新标题", now, "alice")
    loaded = repo.get_session("sess-expert")
    assert loaded.title == "新标题"
    assert [m["content"] for m in loaded.messages] == ["你好"]

    assert repo.renew_stream("missing") is False
