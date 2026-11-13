"""按真实登录用户查询已记录的 Token 用量。"""

import logging
import sqlite3
from typing import Annotated, Literal

from dbutils.pooled_db import TooManyConnectionsError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from pymysql.err import InterfaceError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from easy_agent.api.deps import get_token_usage_repository, get_user_repository
from easy_agent.db.repositories.token_usage import TokenUsageRepository
from easy_agent.db.repositories.user_repository import UserRepository
from easy_agent.middleware import get_current_username

logger = logging.getLogger("easy_agent.usage")


class UsageRoute(APIRoute):
    """仅统计路由的响应策略，包含依赖鉴权、输入和输出校验。"""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def usage_response(request: Request):
            try:
                response = await handler(request)
            except StarletteHTTPException as exc:
                response = await http_exception_handler(request, exc)
            except RequestValidationError as exc:
                response = await request_validation_exception_handler(request, exc)
            except (
                sqlite3.OperationalError, sqlite3.InterfaceError,
                OperationalError, InterfaceError, TooManyConnectionsError,
            ):
                logger.exception("Token statistics database unavailable")
                response = JSONResponse(
                    status_code=503, content={"detail": "Token统计暂不可用，请重试"},
                )
            except Exception:
                logger.exception("Token statistics unexpected failure")
                response = JSONResponse(
                    status_code=500, content={"detail": "Internal Server Error"},
                )
            response.headers["Cache-Control"] = "no-store"
            return response

        return usage_response


router = APIRouter(prefix="/agent/usage", tags=["Usage"], route_class=UsageRoute)


class UsageSummary(BaseModel):
    today: int
    last_7_days: int
    last_30_days: int
    all_time: int


class UsageSeries(BaseModel):
    model_key: str
    model_name: str
    values: list[int]


class UsageDaily(BaseModel):
    dates: list[str]
    totals: list[int]
    series: list[UsageSeries]


class TokenUsageResponse(BaseModel):
    timezone: Literal["Asia/Shanghai"]
    as_of: str
    days: Literal[7, 30]
    summary: UsageSummary
    daily: UsageDaily


@router.get("", response_model=TokenUsageResponse)
def get_usage(
    username: Annotated[str, Depends(get_current_username)],
    repo: Annotated[TokenUsageRepository, Depends(get_token_usage_repository)],
    users: Annotated[UserRepository, Depends(get_user_repository)],
    days: int = 7,
) -> TokenUsageResponse:
    if days not in (7, 30):
        raise HTTPException(status_code=422, detail="days must be 7 or 30")
    user = users.get_user_by_username(username)
    if user is None:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    return TokenUsageResponse.model_validate(repo.query(user.user_id, days=days))
