"""Opt-in real-provider billing checks; all usage is stored in the test database.

Run with EASY_TEST_USAGE_PROVIDER_CONFIG pointing to a local model config.
"""

import asyncio
import os

import pytest

from easy_agent.agent import token_usage
from easy_agent.agent.model import create_model, resolve_llm_config, resolve_model_key
from easy_agent.bootstrap.config import Config
from easy_agent.db.repositories.token_usage import TokenUsageRepository
from easy_agent.utils.usage_context import usage_scope


pytestmark = pytest.mark.skipif(
    not os.getenv("EASY_TEST_USAGE_PROVIDER_CONFIG"),
    reason="EASY_TEST_USAGE_PROVIDER_CONFIG is not configured; real provider not verified",
)


@pytest.mark.parametrize("stream", [False, True], ids=["completion", "stream-tail"])
async def test_real_provider_records_reported_usage_once(db, monkeypatch, stream):
    repo = TokenUsageRepository(db)
    monkeypatch.setattr(token_usage, "get_token_usage_repository", lambda: repo)
    config = Config.from_yaml(os.environ["EASY_TEST_USAGE_PROVIDER_CONFIG"])
    llm = create_model(config).bind(max_tokens=64)

    async with asyncio.timeout(45):
        with usage_scope("provider-test-user", "provider-test-session"):
            if stream:
                chunks = [chunk async for chunk in llm.astream("Reply only: OK")]
                assert chunks, "Provider returned no streaming chunks"
                response = chunks[0]
                for chunk in chunks[1:]:
                    response = response + chunk
            else:
                response = await llm.ainvoke("Reply only: OK")

    usage = response.usage_metadata
    assert usage and usage["input_tokens"] > 0 and usage["output_tokens"] > 0
    with db.get_connection() as conn:
        records = [dict(row) for row in conn.execute("SELECT * FROM token_usage")]
    assert len(records) == 1
    record = records[0]
    assert record["user_id"] == "provider-test-user"
    assert record["session_id"] == "provider-test-session"
    assert record["model_key"] == resolve_model_key(config, None)
    assert record["model_name"] == resolve_llm_config(config, None).model
    assert record["input_tokens"] == usage["input_tokens"]
    assert record["output_tokens"] == usage["output_tokens"]
    # Stream chunks carry internal generation evidence until the callback's
    # aggregate is finalized; only ainvoke returns the cleaned final message.
    if not stream:
        assert token_usage.USAGE_KEY not in response.response_metadata
