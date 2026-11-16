"""RAG 平台模型管理接口：添加/删除模型与全局模型配置。"""

from __future__ import annotations

from typing import Any

from easy_agent.knowledges.ragflow.errors import RagflowContractError


class ModelManagementMixin:
    """模型管理方法；由 RagflowClient 组合，复用其请求与校验基础设施。"""

    async def add_model(
        self,
        *,
        model_factory: str,
        model_type: str,
        model_name: str,
        api_base: str,
        api_key: str | None = None,
        max_tokens: str | int | None = None,
        user_defined_model_name: str | None = None,
        x_custom_device: str | None = None,
    ) -> bool:
        """添加模型（文档 0.1：POST /api/v1/models）。

        Args:
            model_factory: 部署类型，'CangJie'（需 api_key）或 'Xinference'。
            model_type: 'chat' / 'embedding' / 'rerank'。
            model_name: 模型名称，如 "qwen2.5-72B-instruct-int4"。
            api_base: 模型访问路径。
            api_key: 仓颉服务模型密钥（Xinference 不需要）。
            max_tokens: chat 模型必传，控制最大 token 长度。
            user_defined_model_name: 用户自定义唯一名称，缺省同 model_name。
            x_custom_device: 仓颉特定显卡路由头，不涉及请忽略。
        """
        self._require_all(
            (model_factory, model_type, model_name, api_base),
            "model_factory/model_type/model_name/api_base 均为必填",
        )
        if model_type == "chat" and max_tokens is None:
            raise ValueError("添加 chat 类型模型时必须传入 max_tokens")
        body = self._clean_body(
            {
                "model_factory": model_factory,
                "model_type": model_type,
                "model_name": model_name,
                "api_base": api_base,
                "api_key": api_key,
                "max_tokens": max_tokens,
                "user_defined_model_name": user_defined_model_name,
                "x_custom_device": x_custom_device,
            }
        )
        data = await self._request("POST", "/api/v1/models", json_body=body)
        return True if data is None else bool(data)

    async def delete_models(self, *, model_factory: str, model_name: str) -> bool:
        """删除模型（文档 0.2：DELETE /api/v1/models）。

        Args:
            model_factory: 部署类型，'CangJie' 或 'Xinference'。
            model_name: 需传 add_model 中 user_defined_model_name 对应的值。
        """
        self._require_non_empty(model_factory, "model_factory")
        self._require_non_empty(model_name, "model_name")
        data = await self._request(
            "DELETE",
            "/api/v1/models",
            json_body={"model_factory": model_factory, "model_name": model_name},
        )
        return True if data is None else bool(data)

    async def list_models(self) -> list[dict[str, Any]]:
        """获取已添加的模型列表（文档 0.3：GET /api/v1/models）。

        RAGFlow 不同版本返回格式有差异：社区版/定制版可能直接返回 list，
        也可能把 list 包在 ``{"models": [...]}`` dict 里。这里统一归一为 list。
        """
        data = await self._request("GET", "/api/v1/models")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("models"), list):
            return list(data["models"])
        raise RagflowContractError("RAGFlow 模型列表格式不符")

    async def get_global_models(self) -> dict[str, Any]:
        """查看系统全局模型配置（文档 0.4：GET /api/v1/global_models）。"""
        data = await self._request("GET", "/api/v1/global_models")
        return self._expect_dict(data, "RAGFlow 全局模型配置格式不符")

    async def set_global_models(
        self,
        *,
        chat_model: str | None = None,
        embedding_model: str | None = None,
        rerank_model: str | None = None,
    ) -> bool:
        """配置系统全局模型（文档 0.5：PUT /api/v1/global_models）。

        模型名称格式为 "model_name"@"model_factory"，
        三个参数至少需要传入一个。
        """
        body = self._clean_body(
            {
                "chat_model": chat_model,
                "embedding_model": embedding_model,
                "rerank_model": rerank_model,
            }
        )
        if not body:
            raise ValueError(
                "chat_model/embedding_model/rerank_model 至少需要传入一个"
            )
        data = await self._request("PUT", "/api/v1/global_models", json_body=body)
        return True if data is None else bool(data)
