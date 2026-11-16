"""RAG 平台 HTTP API 客户端（knowledges 模块）。

客户端按资源域拆到同级 Mixin（transport / llm_models / datasets / documents /
chunks / retrieval / chat / files），本文件只做组合与连通性探测；对外仍是
一个 ``RagflowClient``，方法签名与行为不变。

设计约定:
- 接口路径、HTTP 方法、请求参数按《RAG平台应用接入接口文档 v1.1.1》
  （RI001–RI011，标准 /api/v1/* 路径）实现；文档未提及的 Ragflow v0.26.3
  原生接口（模型/聊天助手/会话/团队管理等）保持原实现不变。客户端内不读
  任何环境变量；base_url / api_key 由服务层从配置（RAGFLOW_BASE_URL /
  RAGFLOW_API_KEY）解析后经构造函数注入。
- 每个请求自动携带 ``Authorization: Bearer {api_key}`` 与
  ``stdsysserialnum`` 全局链路追踪流水号（令牌+yyyyMMddHHmmss+六位随机数）。
- 响应统一按 ``{"code": <int>, "data": ..., "message": "..."}`` 信封解码，
  成功码兼容平台统一信封 ``code=200`` 与 Ragflow 原生 ``code=0``，成功时
  返回 ``data`` 字段原样内容（可能为 dict / list / bool / None）；其它
  code（400/401/404/422/429/500/503 等）与非 2xx HTTP 状态码转译为
  errors.py 中的稳定异常。
"""

from __future__ import annotations

from easy_agent.knowledges.ragflow.chat import ChatMixin
from easy_agent.knowledges.ragflow.chunks import ChunkMixin
from easy_agent.knowledges.ragflow.datasets import DatasetMixin
from easy_agent.knowledges.ragflow.documents import DocumentMixin
from easy_agent.knowledges.ragflow.files import FileTreeMixin
from easy_agent.knowledges.ragflow.llm_models import ModelManagementMixin
from easy_agent.knowledges.ragflow.retrieval import RetrievalMixin
from easy_agent.knowledges.ragflow.transport import (
    RagflowBinary,
    RagflowTransportMixin,
)


class RagflowClient(
    RagflowTransportMixin,
    ModelManagementMixin,
    DatasetMixin,
    DocumentMixin,
    ChunkMixin,
    RetrievalMixin,
    ChatMixin,
    FileTreeMixin,
):
    """RAG 平台 HTTP API 客户端：39 个接口的类型化异步封装。

    覆盖《RAG平台应用接入接口文档 v1.1.1》RI001–RI011 全部 11 个接入接口，
    以及文档未提及、维持原状的 Ragflow v0.26.3 原生管理接口。

    用法::

        async with RagflowClient(base_url, api_key) as client:
            datasets = await client.list_datasets(page=1, page_size=30)
    """

    async def health(self) -> bool:
        """以最小列表请求探测连通性与认证有效性。"""
        await self.list_datasets(page=1, page_size=1)
        return True


__all__ = ["RagflowBinary", "RagflowClient"]
