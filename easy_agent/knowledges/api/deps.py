"""知识库路由的请求级依赖：身份解析、服务装配、错误映射与审计。

路由模块只声明端点，鉴权、服务构造和异常转 HTTP 的规则集中在本模块，
保证所有端点使用同一套 fail-closed 语义。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from easy_agent.db import Database, get_database
from easy_agent.db.login_state import LoginRejected, bearer_token, verify_login_token
from easy_agent.db.repositories.user_repository import UserRepository
from easy_agent.knowledges.core.config import KnowledgeConfig
from easy_agent.knowledges.core.models import KnowledgePrincipal
from easy_agent.knowledges.repository.operations import KnowledgeOperationsRepository
from easy_agent.knowledges.repository import KnowledgeRepository
from easy_agent.knowledges.runtime.observability import resolve_request_id
from easy_agent.knowledges.service import KnowledgeService, KnowledgeServiceError


# ---------------------------------------------------------------------------
# 请求级身份（fail-closed，无遗留请求头/默认用户回退）
# ---------------------------------------------------------------------------
def get_knowledge_principal(
    http_request: Request,
    db: Annotated[Database, Depends(get_database)],
) -> KnowledgePrincipal:
    """解析可信用户，不接受遗留请求头/默认用户回退。

    登录态与主鉴权共用 ``verify_login_token``：不看 token 的 exp，按单点登录版本和空闲登出
    判定，状态码和提示文案也一致（被踢下线时前端据文案停止自动续期）。
    """

    try:
        user = verify_login_token(
            bearer_token(http_request), UserRepository(db), request=http_request
        )
    except LoginRejected as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.detail, headers=exc.headers
        ) from None

    department_id = user.organization_id.strip() or None
    http_request.state.actor_user_id = user.user_id
    http_request.state.actor_username = user.username
    http_request.state.actor_department_id = department_id
    return KnowledgePrincipal(
        user_id=user.user_id,
        username=user.username,
        department_id=department_id,
    )


def _get_knowledge_config(request: Request) -> KnowledgeConfig:
    """读取应用级知识模块配置，未注入时回退默认配置。"""

    return getattr(request.app.state, "knowledge_config", KnowledgeConfig())


def _request_id(request: Request) -> str:
    """解析请求追踪 ID：优先请求状态，其次归一化 X-Request-Id 头。"""

    state_request_id = getattr(request.state, "request_id", "")
    if state_request_id:
        return str(state_request_id)
    return resolve_request_id(request.headers.get("X-Request-Id"))


async def _record_manager_audit(
    request: Request,
    db: Database,
    principal: KnowledgePrincipal,
    *,
    action: str,
    object_id: str,
    department_id: str,
) -> None:
    """部门管理员变更审计：仅在审计开关打开时记录。"""

    config = _get_knowledge_config(request)
    if not config.audit.enabled:
        return
    await run_in_threadpool(
        KnowledgeOperationsRepository(db).record_audit,
        request_id=str(getattr(request.state, "request_id", "unknown")),
        actor_user_id=principal.user_id,
        actor_username=principal.username,
        action=action,
        object_type="department_manager",
        object_id=object_id,
        details={"department_id": department_id},
        max_details_bytes=config.audit.max_details_bytes,
    )


def _service(
    request: Request,
    db: Annotated[Database, Depends(get_database)],
) -> KnowledgeService:
    """构建知识服务实例；模块未启用或 Ragflow 客户端未就绪时返回 503。"""

    config = _get_knowledge_config(request)
    if not config.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "KNOWLEDGE_DISABLED", "message": "知识工程模块未启用"},
        )
    ragflow = getattr(request.app.state, "ragflow_client", None)
    if ragflow is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "KNOWLEDGE_NOT_READY", "message": "知识工程模块尚未就绪"},
        )
    original_store = getattr(request.app.state, "knowledge_original_store", None)
    return KnowledgeService(
        KnowledgeRepository(db), ragflow, config, original_store=original_store
    )


def knowledge_http_error(exc: KnowledgeServiceError) -> HTTPException:
    """把知识服务异常转换为带稳定错误码的 HTTP 异常。"""

    return HTTPException(
        status_code=exc.status_code,
        detail={
            "code": exc.code,
            "message": exc.message,
            "retryable": exc.retryable,
        },
    )


async def _call(method, /, *args, **kwargs):
    """统一执行服务调用，将业务错误转换为带错误码的 HTTP 异常。

    调用点传「异步方法对象 + 参数」而不是先构造协程：协程在 try 块内才创建，
    服务异常处理覆盖完整，也不会在调用点留下未 await 的协程对象。
    """

    try:
        return await method(*args, **kwargs)
    except KnowledgeServiceError as exc:
        raise knowledge_http_error(exc) from exc
