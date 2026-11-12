"""按真实登录用户查询已记录的 Token 用量。"""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

from easy_agent.api.deps import get_token_usage_repository, get_user_repository
from easy_agent.db.repositories.token_usage import TokenUsageRepository
from easy_agent.db.repositories.user_repository import UserRepository
from easy_agent.middleware import get_current_username

logger = logging.getLogger("easy_agent.usage")


def _usage_errors():
    """同时覆盖鉴权依赖内的用户查询故障，避免其先于路由返回 500。"""
    try:
        yield
    except (HTTPException, RequestValidationError):
        raise
    except Exception as exc:
        logger.error("Token statistics unavailable | error=%s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Token统计暂不可用，请重试",
            headers={"Cache-Control": "no-store"},
        ) from None


router = APIRouter(prefix="/agent/usage", tags=["Usage"], dependencies=[Depends(_usage_errors)])


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
    response: Response,
    username: Annotated[str, Depends(get_current_username)],
    repo: Annotated[TokenUsageRepository, Depends(get_token_usage_repository)],
    users: Annotated[UserRepository, Depends(get_user_repository)],
    days: int = 7,
) -> TokenUsageResponse:
    response.headers["Cache-Control"] = "no-store"
    if days not in (7, 30):
        raise HTTPException(
            status_code=422, detail="days must be 7 or 30", headers={"Cache-Control": "no-store"}
        )
    user = users.get_user_by_username(username)
    if user is None:
        raise HTTPException(
            status_code=401, detail="登录已失效，请重新登录", headers={"Cache-Control": "no-store"}
        )
    return TokenUsageResponse.model_validate(repo.query(user.user_id, days=days))
