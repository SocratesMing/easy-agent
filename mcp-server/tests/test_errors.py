"""错误契约单元测试（FW-14）：脱敏、日志与对外文案分离。"""

from __future__ import annotations

import logging

from mcp.server.fastmcp.exceptions import ToolError

from easy_mcp_server.errors import redact, tool_failure


def test_redact_masks_password_token_and_bearer():
    text = (
        "connect failed password=hunter2 token: abc123 "
        "Authorization: Bearer sk-live-xyz"
    )
    cleaned = redact(text)
    assert "hunter2" not in cleaned
    assert "abc123" not in cleaned
    assert "sk-live-xyz" not in cleaned
    assert "***" in cleaned


def test_redact_masks_literal_mysql_password(monkeypatch):
    monkeypatch.setenv("MYSQL_PASSWORD", "s3cr3t-value")
    cleaned = redact("driver said: s3cr3t-value rejected")
    assert "s3cr3t-value" not in cleaned
    assert "***" in cleaned


def test_redact_collapses_newlines_and_caps_length():
    cleaned = redact("line1\nline2\r\n" + "x" * 2000)
    assert "\n" not in cleaned
    assert len(cleaned) <= 530


def test_tool_failure_message_has_code_only():
    err = tool_failure(
        "market_quotes_unavailable",
        "行情数据暂时不可用，请稍后重试",
        RuntimeError("password=hunter2 host=10.0.0.1"),
        tool="get_latest_quotes",
    )
    assert isinstance(err, ToolError)
    message = str(err)
    assert message == "market_quotes_unavailable: 行情数据暂时不可用，请稍后重试"
    assert "hunter2" not in message
    assert "RuntimeError" not in message


def test_tool_failure_logs_redacted_exception(caplog):
    with caplog.at_level(logging.ERROR, logger="easy-mcp-server"):
        tool_failure(
            "market_quotes_unavailable",
            "行情数据暂时不可用，请稍后重试",
            RuntimeError("connect failed password=hunter2"),
            tool="get_latest_quotes",
        )
    logged = caplog.text
    assert "market_quotes_unavailable" in logged
    assert "get_latest_quotes" in logged
    assert "hunter2" not in logged
