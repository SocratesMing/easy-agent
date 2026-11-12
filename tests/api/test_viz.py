"""对话内可视化的沙箱框架页与图表库（/agent/viz/）。

覆盖：
- 框架页按 visual 配置改写 data-enabled / data-widgets，CSP 禁止一切网络请求，
  只在开启 HTML 交互组件时放行内联脚本；
- 静态资源只放行白名单里的几个文件（不做目录映射，路径穿越与其他文件一律 404）；
- 这些路由不需要登录（iframe 与 <script> 带不上 Authorization 头）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from easy_agent.api import viz
from easy_agent.bootstrap.config_models import VisualConfig


def _client(monkeypatch, visual: VisualConfig | None = None) -> TestClient:
    monkeypatch.setattr(viz, "current_visual_config", lambda: visual or VisualConfig())
    app = FastAPI()
    app.include_router(viz.router)
    return TestClient(app)


def _script_src(csp: str) -> str:
    return next(part.strip() for part in csp.split(";") if part.strip().startswith("script-src"))


def test_frame_page_enables_every_kind_by_default(monkeypatch):
    response = _client(monkeypatch).get("/agent/viz/frame.html")

    assert response.status_code == 200
    assert 'data-enabled="1"' in response.text
    assert 'data-widgets="1"' in response.text
    csp = response.headers["content-security-policy"]
    assert "connect-src 'none'" in csp
    assert "img-src data: blob:" in csp
    assert "default-src 'none'" in csp
    assert _script_src(csp) == "script-src 'self' 'unsafe-inline'"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_widgets_off_keeps_charts_but_blocks_inline_scripts(monkeypatch):
    response = _client(monkeypatch, VisualConfig(html_widgets=False)).get("/agent/viz/frame.html")

    assert 'data-enabled="1"' in response.text
    assert 'data-widgets="0"' in response.text
    assert _script_src(response.headers["content-security-policy"]) == "script-src 'self'"


def test_master_switch_off_disables_everything(monkeypatch):
    response = _client(monkeypatch, VisualConfig(enabled=False)).get("/agent/viz/frame.html")

    assert 'data-enabled="0"' in response.text
    assert 'data-widgets="0"' in response.text
    assert _script_src(response.headers["content-security-policy"]) == "script-src 'self'"


@pytest.mark.parametrize(
    ("path", "media"),
    [
        ("frame.js", "text/javascript"),
        ("frame.css", "text/css"),
        ("vendor/echarts.min.js", "text/javascript"),
        ("vendor/mermaid.min.js", "text/javascript"),
    ],
)
def test_whitelisted_assets_are_served_with_cache(monkeypatch, path, media):
    response = _client(monkeypatch).get(f"/agent/viz/{path}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media)
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "path",
    ["vendor/README.md", "vendor/LICENSE-echarts", "frame.html.bak", "../api/viz.py", "..%2Fapi%2Fviz.py", "vendor/"],
)
def test_other_paths_are_not_served(monkeypatch, path):
    assert _client(monkeypatch).get(f"/agent/viz/{path}").status_code == 404


def test_frame_routes_are_registered_on_the_app_without_auth():
    from easy_agent.app import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/agent/viz/frame.html" in paths
    with TestClient(app) as client:
        assert client.get("/agent/viz/frame.js").status_code == 200
