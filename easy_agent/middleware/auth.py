"""Authentication dependencies for FastAPI"""

import logging
from typing import Annotated

from fastapi import Depends, Request

from ..db import Database, get_database
from ..utils.auth import get_username_from_token

logger = logging.getLogger(__name__)


async def get_current_username(
    http_request: Request,
    db: Annotated[Database, Depends(get_database)],
) -> str:
    auth_header = http_request.headers.get("Authorization")

    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
        username_from_token = get_username_from_token(token)
        if username_from_token:
            user = db.get_user_by_username(username_from_token)
            if user:
                return username_from_token

    username_header = http_request.headers.get("X-Username")
    if username_header:
        user = db.get_user_by_username(username_header)
        if user:
            return username_header

    default_user = db.get_or_create_default_user()
    return default_user.username
