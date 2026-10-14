"""公共空间默认可见性回归测试。

取消「可查看」白名单后：公共（team）空间对同部门账号默认只读可见；
跨部门 / 无部门访问仍需 owner、管理白名单或单库权限行。
权限管理勾选名单模式（access_restricted=1）下，同部门默认可见与
部门授权行被屏蔽，仅勾选人员、owner、管理白名单与 admin 可见。
"""

from datetime import datetime

from easy_agent.knowledges.api import KnowledgePrincipal
from easy_agent.knowledges.core.models import (
    KnowledgeBaseRole,
    PermissionEntry,
    PermissionListResponse,
)
from easy_agent.knowledges.core.repository import KnowledgeRepository
from easy_agent.knowledges.service import KnowledgeService, LocalOriginalStore


def _now() -> str:
    return datetime.now().isoformat()


def _insert_user(db, user_id: str, username: str, department_id: str) -> None:
    with db.get_connection() as conn:
        db._execute(
            conn.cursor(),
            "INSERT INTO users (user_id, username, password_hash, department_id, "
            "account_status, token_version, created_at, updated_at) "
            "VALUES (?, ?, 'x', ?, 'active', 0, ?, ?)",
            (user_id, username, department_id, _now(), _now()),
        )


def _insert_team_base(db, base_id: str, department_id: str, owner_user_id: str) -> None:
    with db.get_connection() as conn:
        db._execute(
            conn.cursor(),
            "INSERT INTO knowledge_bases (id, name, description, space_type, owner_user_id, "
            "department_id, embedding_model, status, created_at, updated_at) "
            "VALUES (?, ?, '', 'team', ?, ?, 'test-model', 'active', ?, ?)",
            (base_id, base_id, owner_user_id, department_id, _now(), _now()),
        )


def _grant_team_space_manager(db, user_id: str) -> None:
    """公共空间管理白名单：team 库的 owner 需持白名单才可管理权限。"""
    with db.get_connection() as conn:
        db._execute(
            conn.cursor(),
            "INSERT INTO knowledge_team_space_managers (user_id, granted_by, granted_at, updated_at) "
            "VALUES (?, 'admin', ?, ?)",
            (user_id, _now(), _now()),
        )


def _visible_base_ids(db, principal: KnowledgePrincipal) -> set[str]:
    rows = KnowledgeRepository(db).list_candidate_bases(
        user_id=principal.user_id,
        department_id=principal.department_id,
    )
    return {str(row["id"]) for row in rows}


def _service(db, tmp_path) -> KnowledgeService:
    return KnowledgeService(
        KnowledgeRepository(db), None, None, original_store=LocalOriginalStore(tmp_path)
    )


def test_team_space_defaults_to_same_department_visibility(db):
    _insert_user(db, "u-same", "same", "dept-a")
    _insert_user(db, "u-other", "other", "dept-b")
    _insert_user(db, "u-nodept", "nodept", "")
    _insert_user(db, "u-owner", "owner", "dept-a")
    _insert_team_base(db, "base-a", "dept-a", owner_user_id="u-owner")
    _insert_team_base(db, "base-b", "dept-b", owner_user_id="u-owner")

    same = KnowledgePrincipal(user_id="u-same", username="same", department_id="dept-a")
    other = KnowledgePrincipal(user_id="u-other", username="other", department_id="dept-b")
    nodept = KnowledgePrincipal(user_id="u-nodept", username="nodept", department_id=None)
    owner = KnowledgePrincipal(user_id="u-owner", username="owner", department_id="dept-a")

    # 同部门默认可见；跨部门仅见自己部门；无部门看不到公共空间；owner 全可见
    assert _visible_base_ids(db, same) == {"base-a"}
    assert _visible_base_ids(db, other) == {"base-b"}
    assert _visible_base_ids(db, nodept) == set()
    assert _visible_base_ids(db, owner) == {"base-a", "base-b"}


async def test_same_department_user_gets_default_viewer_role(db, tmp_path):
    _insert_user(db, "u-same", "same", "dept-a")
    _insert_user(db, "u-other", "other", "dept-b")
    _insert_team_base(db, "base-a", "dept-a", owner_user_id="u-owner")

    repository = KnowledgeRepository(db)
    service = _service(db, tmp_path)
    base = repository.get_base("base-a")

    same = KnowledgePrincipal(user_id="u-same", username="same", department_id="dept-a")
    other = KnowledgePrincipal(user_id="u-other", username="other", department_id="dept-b")

    assert await service._effective_role(base, same, []) == KnowledgeBaseRole.VIEWER
    assert await service._effective_role(base, other, []) is None


async def test_restricted_team_base_only_visible_to_selected_users(db, tmp_path):
    """勾选名单模式：保存勾选人员后仅勾选者可见，取消后恢复默认。"""
    _insert_user(db, "u-owner", "owner", "dept-a")
    _insert_user(db, "u-alice", "alice", "dept-a")
    # bob 为跨部门账号：仅能通过勾选名单获得可见性
    _insert_user(db, "u-bob", "bob", "dept-b")
    _insert_team_base(db, "base-a", "dept-a", owner_user_id="u-owner")
    _grant_team_space_manager(db, "u-owner")

    repository = KnowledgeRepository(db)
    service = _service(db, tmp_path)
    owner = KnowledgePrincipal(user_id="u-owner", username="owner", department_id="dept-a")
    alice = KnowledgePrincipal(user_id="u-alice", username="alice", department_id="dept-a")
    bob = KnowledgePrincipal(user_id="u-bob", username="bob", department_id="dept-b")

    response = await service.replace_permissions(
        base_id="base-a",
        principal=owner,
        items=[PermissionEntry(subject_type="user", subject_id="u-bob", role="manager")],
        restrict_access=True,
    )
    # 请求角色一律归一为 viewer（新权限管理仅控制可见人员）
    assert isinstance(response, PermissionListResponse)
    assert response.restrict_access is True
    assert response.items == [
        PermissionEntry(subject_type="user", subject_id="u-bob", role="viewer")
    ]

    base = repository.get_base("base-a")
    assert await service._effective_role(base, alice, repository.list_permissions("base-a")) is None
    assert await service._effective_role(
        base, bob, repository.list_permissions("base-a")
    ) == KnowledgeBaseRole.VIEWER
    assert "base-a" not in _visible_base_ids(db, alice)
    assert "base-a" in _visible_base_ids(db, bob)
    assert "base-a" in _visible_base_ids(db, owner)

    # 清空勾选并保存：受限标记复位，同部门恢复默认只读可见
    await service.replace_permissions(
        base_id="base-a", principal=owner, items=[], restrict_access=False
    )
    base = repository.get_base("base-a")
    assert await service._effective_role(base, alice, repository.list_permissions("base-a")) == (
        KnowledgeBaseRole.VIEWER
    )
    assert "base-a" in _visible_base_ids(db, alice)
    assert "base-a" not in _visible_base_ids(db, bob)


async def test_permission_directory_lists_active_users_without_owner_and_admin(
    db, tmp_path
):
    """人员目录：在职账号全量返回（剔除 owner 与 bootstrap admin），带部门/处室/团队字段。"""
    _insert_user(db, "u-owner", "owner", "dept-a")
    _insert_user(db, "u-alice", "alice", "dept-a")
    with db.get_connection() as conn:
        db._execute(
            conn.cursor(),
            "UPDATE users SET display_name='爱丽丝', department_name='金融市场部', "
            "division_name='研究处', team_name='宏观团队' WHERE user_id='u-alice'",
        )
    _insert_team_base(db, "base-a", "dept-a", owner_user_id="u-owner")
    _grant_team_space_manager(db, "u-owner")

    service = _service(db, tmp_path)
    owner = KnowledgePrincipal(user_id="u-owner", username="owner", department_id="dept-a")
    directory = await service.get_permission_directory("base-a", owner)

    assert [item.user_id for item in directory.items] == ["u-alice"]
    item = directory.items[0]
    assert item.username == "alice"
    assert item.display_name == "爱丽丝"
    assert item.department_id == "dept-a"
    assert item.department_name == "金融市场部"
    assert item.division_name == "研究处"
    assert item.team_name == "宏观团队"


async def test_replace_permissions_rejects_department_subjects(db, tmp_path):
    """新权限管理仅支持按人员授权；部门主体请求直接拒绝。"""
    from easy_agent.knowledges.service import KnowledgeServiceError

    _insert_user(db, "u-owner", "owner", "dept-a")
    _insert_team_base(db, "base-a", "dept-a", owner_user_id="u-owner")
    _grant_team_space_manager(db, "u-owner")

    service = _service(db, tmp_path)
    owner = KnowledgePrincipal(user_id="u-owner", username="owner", department_id="dept-a")
    try:
        await service.replace_permissions(
            base_id="base-a",
            principal=owner,
            items=[PermissionEntry(subject_type="department", subject_id="dept-a", role="viewer")],
            restrict_access=True,
        )
        raise AssertionError("应当抛出 KNOWLEDGE_PERMISSION_SUBJECT_UNSUPPORTED")
    except KnowledgeServiceError as exc:
        assert exc.code == "KNOWLEDGE_PERMISSION_SUBJECT_UNSUPPORTED"
