"""知识库角色与操作权限判定（纯函数）。

输入知识库行、请求身份与授权行，输出有效角色与允许操作；不访问数据库，
也不依赖 ``KnowledgeService``。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from easy_agent.knowledges.core.models import (
    AllowedAction,
    KnowledgeBaseRole,
    KnowledgeBaseVisibility,
    KnowledgePrincipal,
)


_ROLE_RANK = {
    KnowledgeBaseRole.VIEWER: 1,
    KnowledgeBaseRole.MAINTAINER: 2,
    KnowledgeBaseRole.MANAGER: 3,
}

_VIEWER_ACTIONS = frozenset(
    {
        AllowedAction.VIEW,
        AllowedAction.ASK,
        AllowedAction.DOWNLOAD,
        AllowedAction.SELECT_FOR_SESSION,
    }
)
_MAINTAINER_ACTIONS = _VIEWER_ACTIONS | frozenset(
    {
        AllowedAction.CREATE_FOLDER,
        AllowedAction.EDIT_FOLDER,
        AllowedAction.DELETE_FOLDER,
        AllowedAction.UPLOAD,
        AllowedAction.MOVE_DOCUMENT,
        AllowedAction.DELETE_DOCUMENT,
        AllowedAction.RETRY_DOCUMENT,
    }
)
_MANAGER_ACTIONS = _MAINTAINER_ACTIONS | frozenset(
    {
        AllowedAction.EDIT_BASE,
        AllowedAction.MANAGE_PERMISSIONS,
        AllowedAction.DELETE_BASE,
    }
)


def _as_role(value: object) -> KnowledgeBaseRole | None:
    try:
        return KnowledgeBaseRole(str(value))
    except ValueError:
        return None


def _matching_permission_roles(
    principal: KnowledgePrincipal,
    permissions: Iterable[Mapping[str, object]],
) -> list[KnowledgeBaseRole]:
    """筛出与当前用户（本人或所在部门）匹配的授权角色。"""

    roles: list[KnowledgeBaseRole] = []
    for permission in permissions:
        subject_type = str(permission.get("subject_type", ""))
        subject_id = str(permission.get("subject_id", ""))
        matches_user = subject_type == "user" and subject_id == principal.user_id
        matches_department = (
            subject_type == "department"
            and bool(principal.department_id)
            and subject_id == principal.department_id
        )
        if matches_user or matches_department:
            role = _as_role(permission.get("role"))
            if role is not None:
                roles.append(role)
    return roles


def _base_access_restricted(base: Mapping[str, object]) -> bool:
    """知识库是否处于勾选名单（仅勾选人员可见）模式。"""

    try:
        return bool(int(base.get("access_restricted") or 0))
    except (TypeError, ValueError):
        return False


def effective_role(
    base: Mapping[str, object],
    principal: KnowledgePrincipal,
    permissions: Iterable[Mapping[str, object]],
    *,
    team_viewer: bool = False,
) -> KnowledgeBaseRole | None:
    """计算用户在单个知识库上的最强角色。

    公共（team）空间默认对本部门账号只读可见（team_viewer），
    跨部门访问须通过单库权限行或 owner 身份获得。
    """
    if str(base.get("owner_user_id", "")) == principal.user_id:
        return KnowledgeBaseRole.MANAGER
    visibility = KnowledgeBaseVisibility(str(base["space_type"]))
    if visibility == KnowledgeBaseVisibility.PERSONAL:
        return None
    candidates: list[KnowledgeBaseRole] = []
    if visibility == KnowledgeBaseVisibility.TEAM and team_viewer:
        candidates.append(KnowledgeBaseRole.VIEWER)
    candidates.extend(_matching_permission_roles(principal, permissions))
    if not candidates:
        return None
    return max(candidates, key=_ROLE_RANK.__getitem__)


def allowed_actions(role: KnowledgeBaseRole | None) -> list[AllowedAction]:
    """按角色映射允许的操作清单（排序后返回）。"""

    if role == KnowledgeBaseRole.MANAGER:
        actions = _MANAGER_ACTIONS
    elif role == KnowledgeBaseRole.MAINTAINER:
        actions = _MAINTAINER_ACTIONS
    elif role == KnowledgeBaseRole.VIEWER:
        actions = _VIEWER_ACTIONS
    else:
        actions = frozenset()
    return sorted(actions, key=lambda item: item.value)


__all__ = [
    "allowed_actions",
    "effective_role",
]
