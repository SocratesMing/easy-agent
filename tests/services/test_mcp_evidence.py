"""MCP 证据信封测试（FW-14）：不改工具契约，离线验证来源 / 错误 / 截断 / 幂等。"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from types import SimpleNamespace
from typing import Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from easy_agent.services import mcp_evidence
from easy_agent.services.mcp_evidence import wrap_mcp_tool, wrap_mcp_tools


class EchoArgs(BaseModel):
    text: str = ""


class SyncEchoTool(BaseTool):
    name: str = "echo"
    description: str = "Echo text."
    args_schema: type[BaseModel] = EchoArgs

    def _run(self, text: str = "") -> str:
        return f"echo:{text}"


class AsyncEchoTool(BaseTool):
    name: str = "async_echo"
    description: str = "Async echo text."
    args_schema: type[BaseModel] = EchoArgs

    def _run(self, text: str = "") -> str:
        raise NotImplementedError("async-only test double")

    async def _arun(self, text: str = "") -> str:
        await asyncio.sleep(0)
        return f"async:{text}"


class SyncErrorTool(BaseTool):
    name: str = "broken"
    description: str = "Always fails."
    args_schema: type[BaseModel] = EchoArgs

    def _run(self, text: str = "") -> str:
        raise RuntimeError("upstream exploded")


class AsyncErrorTool(BaseTool):
    name: str = "async_broken"
    description: str = "Always fails asynchronously."
    args_schema: type[BaseModel] = EchoArgs

    def _run(self, text: str = "") -> str:
        raise NotImplementedError("async-only test double")

    async def _arun(self, text: str = "") -> str:
        await asyncio.sleep(0)
        raise RuntimeError("async upstream exploded")


class TextErrorTool(BaseTool):
    name: str = "text_error"
    description: str = "Returns an error as plain text."
    args_schema: type[BaseModel] = EchoArgs

    def _run(self, text: str = "") -> str:
        return "Error: upstream returned 500"


class JsonErrorTool(BaseTool):
    name: str = "json_error"
    description: str = "Returns a JSON error as plain text."
    args_schema: type[BaseModel] = EchoArgs

    def _run(self, text: str = "") -> str:
        return '{"success": false, "error": "db down"}'


class ImageTool(BaseTool):
    name: str = "image"
    description: str = "Returns text plus an image block."
    args_schema: type[BaseModel] = EchoArgs

    def _run(self, text: str = "") -> list:
        return [
            {"type": "text", "text": f"caption:{text}"},
            {"type": "image", "base64": "AAAA", "mime_type": "image/png"},
        ]


class ArtifactTool(BaseTool):
    name: str = "artifact"
    description: str = "Returns content and artifact."
    args_schema: type[BaseModel] = EchoArgs
    response_format: Literal["content", "content_and_artifact"] = (
        "content_and_artifact"
    )

    def _run(self, text: str = "") -> tuple[str, dict]:
        return f"data:{text}", {"raw": text}


def _tool_call(tool: BaseTool, text: str = "hello", call_id: str = "call_1") -> dict:
    return {
        "type": "tool_call",
        "name": tool.name,
        "args": {"text": text},
        "id": call_id,
    }


def test_evidence_envelope_exposes_required_fields():
    evidence = mcp_evidence.MCPEvidence(
        server="srv",
        tool="tool",
        ok=True,
        is_error=False,
        fetched_at="2026-10-18T00:00:00Z",
        truncated=False,
        content="payload",
    )
    data = evidence.to_dict()
    for key in (
        "server",
        "tool",
        "ok",
        "is_error",
        "fetched_at",
        "truncated",
        "content",
    ):
        assert key in data
    assert data["content"] == "payload"


def test_wrap_preserves_tool_contract():
    wrapped = wrap_mcp_tool(SyncEchoTool(), server="demo")

    assert isinstance(wrapped, BaseTool)
    assert wrapped.name == "echo"
    assert wrapped.description == "Echo text."
    assert wrapped.args_schema is EchoArgs
    assert wrapped.response_format == "content"


def test_sync_success_result_contains_source_and_payload():
    wrapped = wrap_mcp_tool(SyncEchoTool(), server="demo")

    out = wrapped.invoke({"text": "hello"})

    assert isinstance(out, str)
    assert out.startswith("[MCP source: demo/echo;")
    assert "is_error=false" in out
    assert "ok=true" in out
    assert "truncated=false" in out
    assert out.endswith("echo:hello")
    match = re.search(r"fetched_at=([^;]+);", out)
    assert match is not None
    datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%SZ")


def test_tool_call_returns_toolmessage_with_marker():
    wrapped = wrap_mcp_tool(SyncEchoTool(), server="demo")

    msg = wrapped.invoke(_tool_call(wrapped))

    assert isinstance(msg, ToolMessage)
    assert msg.tool_call_id == "call_1"
    assert "[MCP source: demo/echo;" in msg.content
    assert "echo:hello" in msg.content


async def test_async_success_result_contains_source_and_payload():
    wrapped = wrap_mcp_tool(AsyncEchoTool(), server="demo")

    out = await wrapped.ainvoke({"text": "hello"})
    msg = await wrapped.ainvoke(_tool_call(wrapped))

    assert "is_error=false" in out
    assert "async:hello" in out
    assert isinstance(msg, ToolMessage)
    assert "[MCP source: demo/async_echo;" in msg.content


def test_sync_exception_returns_error_evidence():
    wrapped = wrap_mcp_tool(SyncErrorTool(), server="demo")

    out = wrapped.invoke({"text": "hello"})

    assert "is_error=true" in out
    assert "ok=false" in out
    assert "MCP tool call failed:" in out
    assert "upstream exploded" in out


async def test_async_exception_returns_error_evidence():
    wrapped = wrap_mcp_tool(AsyncErrorTool(), server="demo")

    out = await wrapped.ainvoke({"text": "hello"})

    assert "is_error=true" in out
    assert "ok=false" in out
    assert "async upstream exploded" in out


def test_text_wrapped_error_is_flagged_without_changing_payload():
    wrapped = wrap_mcp_tool(TextErrorTool(), server="demo")

    out = wrapped.invoke({"text": "hello"})

    assert "is_error=true" in out
    assert "ok=false" in out
    assert "error_source=text" in out
    assert "Error: upstream returned 500" in out


def test_json_error_text_is_flagged():
    wrapped = wrap_mcp_tool(JsonErrorTool(), server="demo")

    out = wrapped.invoke({"text": "hello"})

    assert "is_error=true" in out
    assert "error_source=text" in out
    assert '{"success": false, "error": "db down"}' in out


def test_wrap_is_idempotent():
    wrapped = wrap_mcp_tool(SyncEchoTool(), server="demo")

    again = wrap_mcp_tool(wrapped, server="other")

    assert again is wrapped
    out = again.invoke({"text": "hello"})
    assert out.count("[MCP source:") == 1
    assert "demo/echo" in out


def test_wrap_tools_by_server_keeps_names_and_attribution():
    tools = wrap_mcp_tools(
        {
            "alpha": [SyncEchoTool(name="alpha_echo")],
            "beta": [SyncEchoTool(name="beta_echo")],
        }
    )

    assert [t.name for t in tools] == ["alpha_echo", "beta_echo"]
    assert "alpha/alpha_echo" in tools[0].invoke({"text": "x"})
    assert "beta/beta_echo" in tools[1].invoke({"text": "x"})


def test_truncation_caps_content_and_marks_evidence(monkeypatch):
    monkeypatch.delenv("EASY_MCP_MAX_CONTENT_CHARS", raising=False)
    monkeypatch.setattr(mcp_evidence, "DEFAULT_MAX_CONTENT_CHARS", 20)
    wrapped = wrap_mcp_tool(SyncEchoTool(), server="demo")

    out = wrapped.invoke({"text": "x" * 100})

    assert "truncated=true" in out
    assert "MCP output truncated:" in out
    assert "chars omitted" in out
    assert len(out) < 250


def test_multimodal_blocks_keep_images():
    wrapped = wrap_mcp_tool(ImageTool(), server="demo")

    msg = wrapped.invoke(_tool_call(wrapped))

    assert isinstance(msg, ToolMessage)
    assert isinstance(msg.content, list)
    assert msg.content[0]["type"] == "text"
    assert "[MCP source: demo/image;" in msg.content[0]["text"]
    assert any(block.get("type") == "image" for block in msg.content)
    assert any(
        block.get("type") == "text" and "caption:hello" in block["text"]
        for block in msg.content
    )


def test_content_and_artifact_preserves_artifact():
    wrapped = wrap_mcp_tool(ArtifactTool(), server="demo")

    msg = wrapped.invoke(_tool_call(wrapped))

    assert isinstance(msg, ToolMessage)
    assert msg.artifact == {"raw": "hello"}
    assert "data:hello" in msg.content


def test_wrap_supports_lightweight_tool_doubles():
    wrapped = wrap_mcp_tool(SimpleNamespace(name="fake_tool"), server="demo")

    assert wrapped.name == "fake_tool"


async def test_get_mcp_tools_wraps_each_server(monkeypatch, tmp_path):
    from easy_agent.services import mcp

    config_file = tmp_path / "mcp.json"
    config_file.write_text(json.dumps({"servers": {}}), encoding="utf-8")
    servers = {"alpha": {"command": "a"}, "beta": {"command": "b"}}

    class FakeClient:
        def __init__(self, config):
            self.config = config

        async def get_tools(self, server_name=None):
            return [SyncEchoTool(name=f"{server_name}_echo")]

    mcp._mcp_tools_cache.clear()
    mcp._mcp_clients.clear()
    monkeypatch.setattr(
        mcp, "_resolve_mcp_config_path", lambda username=None: config_file
    )
    monkeypatch.setattr(mcp, "load_mcp_config", lambda username=None: dict(servers))
    monkeypatch.setattr(mcp, "MultiServerMCPClient", FakeClient)

    try:
        tools = await mcp.get_mcp_tools()

        assert [tool.name for tool in tools] == ["alpha_echo", "beta_echo"]
        assert "alpha/alpha_echo" in tools[0].invoke({"text": "x"})
        assert "beta/beta_echo" in tools[1].invoke({"text": "x"})

        # 缓存命中后再次返回：仍是新包装器，但一次调用只出现一个前缀
        again = await mcp.get_mcp_tools()
        out = again[0].invoke({"text": "x"})
        assert out.count("[MCP source:") == 1
    finally:
        mcp._mcp_tools_cache.clear()
        mcp._mcp_clients.clear()