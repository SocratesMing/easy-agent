"""会话知识范围：读取/替换会话选择的知识库，并做归属与模型一致性校验。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from easy_agent.knowledges.core.models import (
    AllowedAction,
    SessionKnowledgeScopeResponse,
)
from easy_agent.knowledges.service.contracts import KnowledgeServiceError, _not_found

if TYPE_CHECKING:
    from easy_agent.knowledges.core.models import KnowledgePrincipal


class SessionScopeMixin:
    """会话知识范围方法；由 KnowledgeService 组合，复用其仓储与鉴权能力。"""

    async def _require_session(
        self, session_id: str, principal: KnowledgePrincipal
    ) -> Any:
        """读取会话并校验归属；不存在或属于他人时按 404 处理。"""

        session = await self._repo(self.repository.db.get_session, session_id)
        if session is None or session.username != principal.username:
            raise _not_found("会话不存在")
        return session

    async def get_session_scope(
        self, *, session_id: str, principal: KnowledgePrincipal
    ) -> SessionKnowledgeScopeResponse:
        """读取会话知识范围，并剔除已失去权限的知识库。"""

        await self._require_session(session_id, principal)
        base_ids = await self._repo(
            self.repository.get_session_scope,
            session_id=session_id,
            user_id=principal.user_id,
        )
        visible: list[str] = []
        for base_id in base_ids:
            try:
                await self._authorized_base(
                    base_id, principal, AllowedAction.SELECT_FOR_SESSION
                )
            except KnowledgeServiceError:
                continue
            visible.append(base_id)
        if visible != base_ids:
            await self._repo(
                self.repository.replace_session_scope,
                session_id=session_id,
                user_id=principal.user_id,
                base_ids=visible,
            )
        return SessionKnowledgeScopeResponse(session_id=session_id, base_ids=visible)

    async def replace_session_scope(
        self,
        *,
        session_id: str,
        principal: KnowledgePrincipal,
        base_ids: list[str],
    ) -> SessionKnowledgeScopeResponse:
        """替换会话知识范围：要求所选知识库向量模型一致。"""

        await self._require_session(session_id, principal)
        embedding_models: set[str] = set()
        for base_id in base_ids:
            base, _ = await self._authorized_base(
                base_id, principal, AllowedAction.SELECT_FOR_SESSION
            )
            embedding_models.add(str(base["embedding_model"]))
        if len(embedding_models) > 1:
            raise KnowledgeServiceError(
                "KNOWLEDGE_EMBEDDING_MODEL_MISMATCH",
                "所选知识库的向量模型不一致",
                status_code=409,
            )
        saved = await self._repo(
            self.repository.replace_session_scope,
            session_id=session_id,
            user_id=principal.user_id,
            base_ids=base_ids,
        )
        return SessionKnowledgeScopeResponse(session_id=session_id, base_ids=saved)
