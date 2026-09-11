"""Fail-closed identity and the frozen MVP permission matrix."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from easy_agent.knowledge.domain import (
    AllowedAction,
    KnowledgeBaseRole,
)
from easy_agent.knowledge.auth import (
    KnowledgePrincipal,
    get_knowledge_principal,
)
from easy_agent.models.db import UserModel
from easy_agent.knowledge.service import allowed_actions, effective_role
from easy_agent.utils.auth import create_access_token


def _request(**headers: str) -> Request:
    encoded = [(name.lower().encode(), value.encode()) for name, value in headers.items()]
    return Request({"type": "http", "headers": encoded})


@pytest.mark.asyncio
async def test_knowledge_identity_rejects_legacy_header_and_default_user(db):
    db.create_user(
        UserModel(
            user_id="user-1",
            username="alice",
            password_hash="",
            organization_id="dept-market",
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_knowledge_principal(_request(**{"X-Username": "alice"}), db)

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_knowledge_identity_comes_from_valid_token_and_server_user(db):
    db.create_user(
        UserModel(
            user_id="user-1",
            username="alice",
            password_hash="",
            organization_id="dept-market",
        )
    )
    token = create_access_token({"sub": "alice", "v": 0})

    principal = await get_knowledge_principal(
        _request(Authorization=f"Bearer {token}", **{"X-Username": "attacker"}), db
    )

    assert principal == KnowledgePrincipal(
        user_id="user-1", username="alice", department_id="dept-market"
    )


@pytest.mark.asyncio
async def test_knowledge_identity_rejects_revoked_token_version(db):
    db.create_user(
        UserModel(
            user_id="user-1",
            username="alice",
            password_hash="",
            organization_id="dept-market",
        )
    )
    token = create_access_token({"sub": "alice", "v": 0})
    db.increment_user_token_version("alice")

    with pytest.raises(HTTPException) as exc_info:
        await get_knowledge_principal(
            _request(Authorization=f"Bearer {token}"), db
        )

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_knowledge_identity_rejects_disabled_user(db):
    db.create_user(
        UserModel(
            user_id="user-1",
            username="alice",
            password_hash="",
            organization_id="dept-market",
            account_status="disabled",
        )
    )
    token = create_access_token({"sub": "alice", "v": 0})

    with pytest.raises(HTTPException) as exc_info:
        await get_knowledge_principal(
            _request(Authorization=f"Bearer {token}"), db
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_knowledge_identity_rejects_legacy_token_without_version(db):
    db.create_user(
        UserModel(
            user_id="user-1",
            username="alice",
            password_hash="",
            organization_id="dept-market",
        )
    )
    token = create_access_token({"sub": "alice"})

    with pytest.raises(HTTPException) as exc_info:
        await get_knowledge_principal(
            _request(Authorization=f"Bearer {token}"), db
        )

    assert exc_info.value.status_code == 401


@pytest.mark.parametrize(
    ("space_type", "base_department", "permissions", "expected"),
    [
        ("personal", None, [], None),
        ("team", "dept-market", [], KnowledgeBaseRole.VIEWER),
        ("team", "dept-other", [], None),
        (
            "shared",
            None,
            [{"subject_type": "department", "subject_id": "dept-market", "role": "viewer"}],
            KnowledgeBaseRole.VIEWER,
        ),
        (
            "shared",
            None,
            [
                {"subject_type": "department", "subject_id": "dept-market", "role": "viewer"},
                {"subject_type": "user", "subject_id": "user-2", "role": "maintainer"},
            ],
            KnowledgeBaseRole.MAINTAINER,
        ),
    ],
)
def test_effective_role_uses_strongest_direct_or_department_grant(
    space_type, base_department, permissions, expected
):
    principal = KnowledgePrincipal("user-2", "bob", "dept-market")
    base = {
        "owner_user_id": "user-1",
        "space_type": space_type,
        "department_id": base_department,
    }

    assert effective_role(base, principal, permissions) == expected


def test_owner_is_always_manager_and_empty_department_never_matches():
    owner = KnowledgePrincipal("owner", "alice", None)
    stranger = KnowledgePrincipal("stranger", "carol", None)
    base = {
        "owner_user_id": "owner",
        "space_type": "team",
        "department_id": "",
    }

    assert effective_role(base, owner, []) == KnowledgeBaseRole.MANAGER
    assert effective_role(base, stranger, []) is None


def test_role_actions_follow_frozen_matrix():
    viewer = set(allowed_actions(KnowledgeBaseRole.VIEWER))
    maintainer = set(allowed_actions(KnowledgeBaseRole.MAINTAINER))
    manager = set(allowed_actions(KnowledgeBaseRole.MANAGER))

    assert AllowedAction.ASK in viewer
    assert AllowedAction.UPLOAD not in viewer
    assert AllowedAction.UPLOAD in maintainer
    assert AllowedAction.MANAGE_PERMISSIONS not in maintainer
    assert AllowedAction.MANAGE_PERMISSIONS in manager
    assert AllowedAction.DELETE_BASE in manager
