"""批次 1B 拆分回归：core.original_store / core.permissions 行为测试。

覆盖点：
* 原文存储的原子写读、幂等重传、路径穿越/符号链接拒绝、health 探测；
* owner / 部门 / 单库权限行的角色判定与 allowed_actions 排序；
* core 模块不得反向依赖 service（分层约束）。
"""

from __future__ import annotations

import hashlib
import inspect
import io
from pathlib import Path

import pytest

from easy_agent.knowledges.core import original_store, permissions
from easy_agent.knowledges.core.models import (
    AllowedAction,
    KnowledgeBaseRole,
    KnowledgePrincipal,
)
from easy_agent.knowledges.core.original_store import (
    DEFAULT_ORIGINALS_ROOT,
    LocalOriginalStore,
    OriginalReadHandle,
    OriginalStorageError,
)
from easy_agent.knowledges.core.permissions import (
    _MAINTAINER_ACTIONS,
    _MANAGER_ACTIONS,
    _ROLE_RANK,
    _VIEWER_ACTIONS,
    _as_role,
    _base_access_restricted,
    _matching_permission_roles,
    allowed_actions,
    effective_role,
)


def _principal(
    user_id: str = "u-1",
    *,
    username: str = "alice",
    department_id: str | None = "dept-a",
) -> KnowledgePrincipal:
    return KnowledgePrincipal(
        user_id=user_id, username=username, department_id=department_id
    )


def _team_base(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "owner_user_id": "u-owner",
        "space_type": "team",
        "department_id": "dept-a",
        "access_restricted": 0,
    }
    base.update(overrides)
    return base


class TestLocalOriginalStore:
    def test_atomic_write_and_read(self, tmp_path: Path) -> None:
        store = LocalOriginalStore(tmp_path / "originals")
        payload = b"knowledge-original-payload"
        key = store.build_key("base-aa", "doc-1", "Report.PDF")
        assert key == "v1/ba/base-aa/doc-1/original.pdf"

        size, digest = store.put_atomic(key, io.BytesIO(payload), len(payload))

        assert size == len(payload)
        assert digest == hashlib.sha256(payload).hexdigest()
        handle = store.open(key)
        assert isinstance(handle, OriginalReadHandle)
        assert handle.size_bytes == len(payload)
        assert b"".join(handle.iter_bytes()) == payload
        assert b"".join(handle.iter_bytes(1, 4)) == payload[1:5]
        assert b"".join(handle.iter_bytes(5, 2)) == b""
        with handle.open_binary() as source:
            assert source.read() == payload
        # 原子写入的临时 .part 文件必须清理干净
        assert not list(store.root.rglob("*.part"))

    def test_default_root_signature_unchanged(self, tmp_path: Path) -> None:
        default = inspect.signature(LocalOriginalStore.__init__).parameters["root"].default
        assert default is DEFAULT_ORIGINALS_ROOT
        assert DEFAULT_ORIGINALS_ROOT == Path("data") / "knowledge_originals"

        store = LocalOriginalStore(tmp_path / "nested" / "root")
        assert store.provider == "filesystem"
        assert store.root == (tmp_path / "nested" / "root").resolve()
        assert store.root.is_dir()

    def test_reupload_is_idempotent_then_conflict(self, tmp_path: Path) -> None:
        store = LocalOriginalStore(tmp_path / "originals")
        key = store.build_key("base-aa", "doc-1", "a.txt")
        payload = b"same-bytes"
        store.put_atomic(key, io.BytesIO(payload), len(payload))

        size, digest = store.put_atomic(key, io.BytesIO(payload), len(payload))
        assert (size, digest) == (len(payload), hashlib.sha256(payload).hexdigest())

        with pytest.raises(OriginalStorageError) as same_size:
            store.put_atomic(key, io.BytesIO(b"diff-bytes"), len(payload))
        assert same_size.value.code == "ORIGINAL_STORAGE_CONFLICT"
        assert same_size.value.retryable is False

        with pytest.raises(OriginalStorageError) as mismatch:
            store.put_atomic(key, io.BytesIO(b"longer"), 5)
        assert mismatch.value.code == "ORIGINAL_STORAGE_SIZE_MISMATCH"

    def test_missing_original_is_retryable(self, tmp_path: Path) -> None:
        store = LocalOriginalStore(tmp_path / "originals")
        key = store.build_key("base-aa", "doc-1", "a.txt")
        with pytest.raises(OriginalStorageError) as excinfo:
            store.open(key)
        assert excinfo.value.code == "ORIGINAL_STORAGE_NOT_AVAILABLE"
        assert excinfo.value.retryable is True

    def test_path_traversal_and_invalid_keys_rejected(self, tmp_path: Path) -> None:
        store = LocalOriginalStore(tmp_path / "originals")
        for key in ("", "../escape", "v1/../../etc/passwd", "/etc/passwd"):
            with pytest.raises(OriginalStorageError) as excinfo:
                store._path(key)
            assert excinfo.value.code == "ORIGINAL_STORAGE_INVALID_KEY", key
            assert excinfo.value.retryable is False

        for base_id, document_id in (("../base", "doc-1"), ("base-aa", "../doc")):
            with pytest.raises(OriginalStorageError) as excinfo:
                store.build_key(base_id, document_id, "a.txt")
            assert excinfo.value.code == "ORIGINAL_STORAGE_INVALID_KEY"

    def test_symlinked_component_rejected(self, tmp_path: Path) -> None:
        store = LocalOriginalStore(tmp_path / "originals")
        (store.root / "group").mkdir()
        (store.root / "link").symlink_to(store.root / "group")

        with pytest.raises(OriginalStorageError) as excinfo:
            store.open("link/original.bin")
        assert excinfo.value.code == "ORIGINAL_STORAGE_UNSAFE_PATH"
        assert excinfo.value.retryable is False

    def test_health_probe(self, tmp_path: Path) -> None:
        store = LocalOriginalStore(tmp_path / "originals")
        assert store.health() is True
        assert not list(store.root.glob(".health-*"))

        # 根目录被同路径文件占据时不可写，health 返回 False 而不是抛异常
        root = store.root
        root.rmdir()
        root.write_text("occupied", encoding="utf-8")
        assert store.health() is False


class TestPermissions:
    def test_owner_is_manager_even_without_permissions(self) -> None:
        base = _team_base()
        assert effective_role(base, _principal("u-owner"), []) is KnowledgeBaseRole.MANAGER

    def test_personal_base_is_owner_only(self) -> None:
        base = {"owner_user_id": "u-owner", "space_type": "personal"}
        rows = [{"subject_type": "user", "subject_id": "u-1", "role": "manager"}]
        assert effective_role(base, _principal("u-1"), rows) is None

    def test_team_viewer_flag_is_caller_controlled(self) -> None:
        base = _team_base()
        principal = _principal("u-in", department_id="dept-a")

        # 是否同部门由调用方（KnowledgeService._effective_role）计算后传入；
        # 纯函数只消费 team_viewer 标志。
        assert effective_role(base, principal, [], team_viewer=False) is None
        assert effective_role(base, principal, [], team_viewer=True) is KnowledgeBaseRole.VIEWER

    def test_team_viewer_yields_to_explicit_permission(self) -> None:
        base = _team_base()
        rows = [{"subject_type": "user", "subject_id": "u-1", "role": "maintainer"}]
        role = effective_role(base, _principal("u-1"), rows, team_viewer=True)
        assert role is KnowledgeBaseRole.MAINTAINER

    def test_shared_space_ignores_team_viewer(self) -> None:
        base = {"owner_user_id": "u-owner", "space_type": "shared"}
        assert effective_role(base, _principal("u-1"), [], team_viewer=True) is None

    def test_user_and_department_permissions_pick_strongest(self) -> None:
        base = _team_base()
        principal = _principal("u-1", department_id="dept-a")
        rows = [
            {"subject_type": "user", "subject_id": "u-1", "role": "viewer"},
            {"subject_type": "department", "subject_id": "dept-a", "role": "maintainer"},
            {"subject_type": "user", "subject_id": "u-1", "role": "manager"},
            {"subject_type": "user", "subject_id": "u-2", "role": "manager"},
            {"subject_type": "department", "subject_id": "dept-b", "role": "manager"},
            {"subject_type": "user", "subject_id": "u-1", "role": "bogus"},
        ]
        assert _matching_permission_roles(principal, rows) == [
            KnowledgeBaseRole.VIEWER,
            KnowledgeBaseRole.MAINTAINER,
            KnowledgeBaseRole.MANAGER,
        ]
        assert effective_role(base, principal, rows) is KnowledgeBaseRole.MANAGER

    def test_empty_department_does_not_match(self) -> None:
        principal = _principal("u-1", department_id=None)
        rows = [{"subject_type": "department", "subject_id": "", "role": "manager"}]
        assert _matching_permission_roles(principal, rows) == []

    def test_base_access_restricted_parsing(self) -> None:
        assert _base_access_restricted({"access_restricted": 1}) is True
        assert _base_access_restricted({"access_restricted": "1"}) is True
        assert _base_access_restricted({"access_restricted": 0}) is False
        assert _base_access_restricted({"access_restricted": "0"}) is False
        assert _base_access_restricted({}) is False
        assert _base_access_restricted({"access_restricted": None}) is False
        assert _base_access_restricted({"access_restricted": "bogus"}) is False

    def test_as_role_parsing(self) -> None:
        assert _as_role("viewer") is KnowledgeBaseRole.VIEWER
        assert _as_role("maintainer") is KnowledgeBaseRole.MAINTAINER
        assert _as_role("manager") is KnowledgeBaseRole.MANAGER
        assert _as_role("bogus") is None
        assert _as_role(None) is None

    def test_allowed_actions_scope_and_sorting(self) -> None:
        viewer = allowed_actions(KnowledgeBaseRole.VIEWER)
        maintainer = allowed_actions(KnowledgeBaseRole.MAINTAINER)
        manager = allowed_actions(KnowledgeBaseRole.MANAGER)
        none = allowed_actions(None)

        assert none == []
        assert [action.value for action in viewer] == [
            "ask",
            "download",
            "select_for_session",
            "view",
        ]
        for actions in (viewer, maintainer, manager):
            assert [action.value for action in actions] == sorted(
                action.value for action in actions
            )
        assert set(viewer) < set(maintainer) < set(manager)
        assert set(manager) == set(AllowedAction)

    def test_action_sets_and_rank_constants(self) -> None:
        assert _ROLE_RANK == {
            KnowledgeBaseRole.VIEWER: 1,
            KnowledgeBaseRole.MAINTAINER: 2,
            KnowledgeBaseRole.MANAGER: 3,
        }
        assert _VIEWER_ACTIONS < _MAINTAINER_ACTIONS < _MANAGER_ACTIONS
        assert len(_VIEWER_ACTIONS) == 4
        assert len(_MANAGER_ACTIONS) == len(AllowedAction)


class TestCoreModulesLayering:
    def test_core_modules_do_not_import_service(self) -> None:
        for module in (original_store, permissions):
            source = inspect.getsource(module)
            assert "from ..service" not in source
            assert "from .service" not in source