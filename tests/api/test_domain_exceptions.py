"""T11：领域异常 → HTTP 映射的契约测试。

数据层 / 服务层不再抛 ``HTTPException``，改为抛
:mod:`easy_agent.models.exceptions` 里的领域异常；``easy_agent/app.py`` 的
``domain_exception_handler`` 负责把它们映射回**既有的** HTTP 错误体：
``{"detail": <message>, "code": "HTTP_<status>", "request_id": ...}``
（与 ``http_exception_handler`` 逐字一致，见 ``tests/api/test_error_contract.py``）。

这里直接调用处理器（不经过 TestClient），把 400 / 404 / 409 三种映射、子类
匹配和未映射兜底固定下来；HTTP 级状态码 / 响应体由 ``test_error_contract.py``、
``test_expert.py``、``test_sessions.py`` 等黑盒用例兜底。
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import easy_agent.models.exceptions as exceptions_module
from easy_agent.app import domain_exception_handler
from easy_agent.models.exceptions import (
    BadRequestError,
    ConflictError,
    DomainError,
    NotFoundError,
)


def _request(request_id: str | None = "req-1"):
    """处理器只用到 ``request.state.request_id``，用最小替身即可。"""

    return SimpleNamespace(state=SimpleNamespace(request_id=request_id))


@pytest.mark.parametrize(
    ("exc", "status_code"),
    [
        (BadRequestError("专家已停用或生成失败，无法执行"), 400),
        (NotFoundError("会话不存在"), 404),
        (ConflictError("专家名称已存在"), 409),
    ],
)
async def test_domain_error_maps_to_existing_http_error_body(exc, status_code):
    response = await domain_exception_handler(_request(), exc)

    assert response.status_code == status_code
    body = json.loads(response.body)
    assert body["detail"] == exc.message
    assert body["code"] == f"HTTP_{status_code}"
    assert body["request_id"] == "req-1"
    assert response.headers["X-Request-Id"] == "req-1"


async def test_domain_error_without_request_id_omits_field():
    response = await domain_exception_handler(_request(None), NotFoundError("会话不存在"))

    assert response.status_code == 404
    body = json.loads(response.body)
    assert body == {"detail": "会话不存在", "code": "HTTP_404"}


async def test_domain_error_subclass_uses_parent_mapping():
    """子类按 MRO 命中父类映射（路由层可以定义更细的业务异常）。"""

    class NoSuchExpert(NotFoundError):
        pass

    response = await domain_exception_handler(_request(), NoSuchExpert("专家不存在"))

    assert response.status_code == 404
    assert json.loads(response.body)["detail"] == "专家不存在"


async def test_unmapped_domain_error_goes_to_global_500_handler():
    """未登记映射的 ``DomainError`` 不自己造 500 响应体，交给全局处理器。

    全局处理器会记 traceback 并返回统一的「服务内部错误」，避免把内部文案
    通过 500 响应泄露给客户端。
    """

    with pytest.raises(DomainError):
        await domain_exception_handler(_request(), DomainError("未知领域错误"))


def test_domain_exceptions_do_not_import_fastapi():
    """L0 的领域异常模块不依赖 web 框架（分层门禁的运行期补充）。"""

    source = Path(exceptions_module.__file__).read_text(encoding="utf-8")
    assert "fastapi" not in source
    assert "starlette" not in source
