"""T3：``easy_agent.db.providers`` 是服务层获取领域 repository 的唯一接缝。

providers 在 ``easy_agent/db/`` 内部调用 ``get_database()``，因此门禁里
「``db/`` 之外 ``get_database()`` 调用数」不会因为服务层需要 repository 而上涨
（见 ``scripts/quality/check_double_write.py`` 的 DW212）。
"""

from __future__ import annotations

import pytest

from easy_agent.db import providers
from easy_agent.db.repositories.agent_runs import AgentRunRepository
from easy_agent.db.repositories.message_repository import MessageRepository
from easy_agent.db.repositories.session_repository import SessionRepository
from easy_agent.db.repositories.session_runtime import SessionRuntimeRepository
from easy_agent.db.repositories.stream_events import StreamEventRepository

_PROVIDERS = (
    (providers.get_session_repository, SessionRepository),
    (providers.get_message_repository, MessageRepository),
    (providers.get_agent_run_repository, AgentRunRepository),
    (providers.get_session_runtime_repository, SessionRuntimeRepository),
    (providers.get_stream_event_repository, StreamEventRepository),
)


@pytest.mark.parametrize(("provider", "repository_cls"), _PROVIDERS)
def test_provider_wraps_the_process_database(monkeypatch, provider, repository_cls):
    """每个 provider 都基于进程级 ``Database`` 构造领域 repository（无状态薄包装）。"""
    sentinel = object()
    monkeypatch.setattr(providers, "get_database", lambda: sentinel)

    repository = provider()

    assert isinstance(repository, repository_cls)
    assert repository._db is sentinel


def test_old_session_repository_path_is_the_same_object():
    """``db/session_repository.py`` 只留一行 re-export（T3 收敛到 repositories/）。"""
    from easy_agent.db.repositories.session_repository import (
        SessionRepository as Canonical,
    )
    from easy_agent.db.session_repository import SessionRepository as Legacy

    assert Legacy is Canonical