"""Easy Agent MCP server.

Each business domain lives in :mod:`easy_mcp_server.businesses` as a package
exposing a ``build() -> FastMCP`` factory. The package name becomes the URL
suffix, e.g. ``businesses/market`` is served at ``/mcp/market/``.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
