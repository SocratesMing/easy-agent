"""测试辅助：注入式测试业务与假校验器（不依赖数据库）。"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from easy_mcp_server.context import current_business, current_username


def build_hello() -> FastMCP:
    """最小测试业务：验证挂载、鉴权与身份注入链路。"""
    mcp = FastMCP(
        "hello",
        instructions="Test-only business.",
        streamable_http_path="/",
        stateless_http=True,
    )

    @mcp.tool()
    def whoami() -> dict:
        """Return the identity resolved from the API key."""
        return {"username": current_username(), "business": current_business()}

    @mcp.tool()
    def echo(text: str) -> str:
        """Echo the given text (requires a valid API key)."""
        current_username()
        return text

    return mcp


class FakeKeyStore:
    """内存版 (business, api_key) -> username 映射，支持重签/吊销。"""

    def __init__(self) -> None:
        self._keys: dict[tuple[str, str], str] = {}

    def issue(self, business: str, api_key: str, username: str) -> None:
        self._keys[(business, api_key)] = username

    def revoke(self, business: str, api_key: str) -> None:
        self._keys.pop((business, api_key), None)

    def verify(self, business: str, api_key: str):
        return self._keys.get((business, api_key))
