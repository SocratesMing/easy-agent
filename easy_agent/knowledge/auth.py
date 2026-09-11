"""Fail-closed identity dependency used only by knowledge routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from ..db import Database, get_database
from ..utils.auth import decode_access_token


@dataclass(frozen=True, slots=True)
class KnowledgePrincipal:
    user_id: str
    username: str
    department_id: str | None


async def get_knowledge_principal(
    http_request: Request,
    db: Annotated[Database, Depends(get_database)],
) -> KnowledgePrincipal:
    """Resolve a trusted user without legacy header/default-user fallbacks."""

    authorization = http_request.headers.get("Authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Knowledge authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(token.strip())
    username = payload.get("sub") if payload else None
    token_version = payload.get("v") if payload else None
    user = db.get_user_by_username(username) if username else None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired knowledge credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.account_status == "disabled":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account disabled",
        )

    if token_version is None or token_version != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Knowledge credentials have been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    department_id = user.organization_id.strip() or None
    http_request.state.actor_user_id = user.user_id
    http_request.state.actor_username = user.username
    http_request.state.actor_department_id = department_id
    return KnowledgePrincipal(
        user_id=user.user_id,
        username=user.username,
        department_id=department_id,
    )


__all__ = ["KnowledgePrincipal", "get_knowledge_principal"]
