"""RAG 平台聊天助手与会话接口：助手配置、会话管理与对话。"""

from __future__ import annotations

from typing import Any

from easy_agent.knowledges.ragflow.errors import RagflowContractError


class ChatMixin:
    """聊天助手与会话方法；由 RagflowClient 组合，复用其请求与校验基础设施。"""

    async def create_chat_assistant(
        self,
        *,
        name: str,
        dataset_ids: list[str] | None = None,
        llm: dict[str, Any] | None = None,
        prompt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """创建聊天助手（文档 4.1：POST /api/v1/chats）。

        Args:
            name: 助手名称（必填）。
            dataset_ids: 关联知识库 ID 列表。
            llm: LLM 设置（model_name/temperature/top_p 等），缺省用默认值。
            prompt: 提示词设置（similarity_threshold/top_n/variables 等）。
        """
        self._require_non_empty(name, "name")
        body = self._clean_body(
            {
                "name": name,
                "dataset_ids": dataset_ids,
                "llm": llm,
                "prompt": prompt,
            }
        )
        data = await self._request("POST", "/api/v1/chats", json_body=body)
        if not isinstance(data, dict) or not data.get("id"):
            raise RagflowContractError("RAGFlow 创建聊天助手未返回有效对象")
        return data

    async def update_chat_assistant(
        self,
        chat_id: str,
        *,
        name: str | None = None,
        dataset_ids: list[str] | None = None,
        llm: dict[str, Any] | None = None,
        prompt: dict[str, Any] | None = None,
    ) -> None:
        """更新聊天助手配置（文档 4.2：PUT /api/v1/chats/{chat_id}）。"""
        self._require_non_empty(chat_id, "chat_id")
        body = self._clean_body(
            {
                "name": name,
                "dataset_ids": dataset_ids,
                "llm": llm,
                "prompt": prompt,
            }
        )
        if not body:
            raise ValueError("至少需要传入一个更新字段")
        path = self._format_path("/api/v1/chats/{chat_id}", chat_id=chat_id)
        await self._request("PUT", path, json_body=body)

    async def delete_chat_assistants(self, chat_ids: list[str]) -> None:
        """删除聊天助手（文档 4.3：DELETE /api/v1/chats，body 传 ids）。"""
        self._require_ids(chat_ids, "chat_ids")
        await self._request("DELETE", "/api/v1/chats", json_body={"ids": chat_ids})

    async def list_chat_assistants(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None,
        orderby: str | None = None,
        desc: bool | None = None,
        name: str | None = None,
        chat_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """查询聊天助手列表（文档 4.4：GET /api/v1/chats）。"""
        query = self._page_query(
            page,
            page_size,
            orderby=orderby,
            desc=desc,
            name=name,
            extra={"id": chat_id},
        )
        data = await self._request("GET", "/api/v1/chats", query=query)
        return self._expect_list(data, "RAGFlow 聊天助手列表格式不符")

    async def create_session(
        self,
        chat_id: str,
        *,
        name: str,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """创建聊天助手会话（文档 5.1：POST /api/v1/chats/{chat_id}/sessions）。"""
        self._require_non_empty(chat_id, "chat_id")
        self._require_non_empty(name, "name")
        body = self._clean_body({"name": name, "user_id": user_id})
        path = self._format_path(
            "/api/v1/chats/{chat_id}/sessions", chat_id=chat_id
        )
        data = await self._request("POST", path, json_body=body)
        if not isinstance(data, dict) or not data.get("id"):
            raise RagflowContractError("RAGFlow 创建会话未返回有效对象")
        return data

    async def update_session(
        self,
        chat_id: str,
        session_id: str,
        *,
        name: str,
        user_id: str | None = None,
    ) -> None:
        """更新会话名称（文档 5.2：PUT /api/v1/chats/{chat_id}/sessions/{session_id}）。"""
        self._require_non_empty(chat_id, "chat_id")
        self._require_non_empty(session_id, "session_id")
        self._require_non_empty(name, "name")
        body = self._clean_body({"name": name, "user_id": user_id})
        path = self._format_path(
            "/api/v1/chats/{chat_id}/sessions/{session_id}",
            chat_id=chat_id,
            session_id=session_id,
        )
        await self._request("PUT", path, json_body=body)

    async def list_sessions(
        self,
        chat_id: str,
        *,
        page: int | None = None,
        page_size: int | None = None,
        orderby: str | None = None,
        desc: bool | None = None,
        name: str | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """查询会话列表（文档 5.3：GET /api/v1/chats/{chat_id}/sessions）。"""
        self._require_non_empty(chat_id, "chat_id")
        query = self._page_query(
            page,
            page_size,
            orderby=orderby,
            desc=desc,
            name=name,
            extra={"id": session_id, "user_id": user_id},
        )
        path = self._format_path(
            "/api/v1/chats/{chat_id}/sessions", chat_id=chat_id
        )
        data = await self._request("GET", path, query=query)
        return self._expect_list(data, "RAGFlow 会话列表格式不符")

    async def delete_sessions(
        self, chat_id: str, session_ids: list[str]
    ) -> None:
        """删除会话（文档 5.4：DELETE /api/v1/chats/{chat_id}/sessions，body 传 ids）。"""
        self._require_non_empty(chat_id, "chat_id")
        self._require_ids(session_ids, "session_ids")
        path = self._format_path(
            "/api/v1/chats/{chat_id}/sessions", chat_id=chat_id
        )
        await self._request("DELETE", path, json_body={"ids": session_ids})

    async def ask_chat(
        self,
        chat_id: str,
        *,
        question: str,
        stream: bool = False,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """与聊天助手对话（文档 5.5：POST /api/v1/chats/{chat_id}/completions）。

        仅支持非流式模式（stream=False）；未提供 session_id 时服务端会
        自动创建新会话。

        Returns:
            ``{"answer": ..., "reference": ..., "session_id": ...}``。
        """
        self._require_non_empty(chat_id, "chat_id")
        self._require_non_empty(question, "question")
        if stream:
            raise ValueError(
                "ask_chat 不支持流式响应(stream=True)，请保持非流式调用"
            )
        body = self._clean_body(
            {
                "question": question,
                "stream": False,
                "session_id": session_id,
                "user_id": user_id,
            }
        )
        path = self._format_path(
            "/api/v1/chats/{chat_id}/completions", chat_id=chat_id
        )
        data = await self._request("POST", path, json_body=body)
        return self._expect_dict(data, "RAGFlow 对话响应格式不符")
