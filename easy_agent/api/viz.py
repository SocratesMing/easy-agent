"""对话内可视化的沙箱框架页与图表库（``/agent/viz/``）。

前端把 ```echarts / ```mermaid / ```widget 代码块和数据工具的图表信封交给一个
``sandbox="allow-scripts"`` 的 iframe 渲染（不同源：拿不到登录 token、localStorage 和外层页面），
iframe 加载的就是这里的 ``frame.html``。ECharts / Mermaid 只在这里托管、按需加载，不打进前端包。

这些路由不需要登录：iframe 和 ``<script>`` 都带不上 Authorization 头，内容也只是静态资源。
框架页的 CSP 禁止一切网络请求（``connect-src 'none'``，图片只认 data: / blob:），
模型写的组件脚本拿到的只有它自己的源码，没有东西可外传。设计见
``docs/design/2026-11-12-chat-visuals.md``。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from easy_agent.bootstrap.config_models import VisualConfig
from easy_agent.services.agents.manager import get_agent_config

router = APIRouter(prefix="/agent/viz", tags=["可视化"])

STATIC_DIR = Path(__file__).resolve().parent.parent / "static" / "viz"

# 只放行这几个文件，不做目录映射：避免路径穿越，也不会意外暴露目录里的其他文件
ASSETS: dict[str, str] = {
    "frame.js": "text/javascript; charset=utf-8",
    "frame.css": "text/css; charset=utf-8",
    "vendor/echarts.min.js": "text/javascript; charset=utf-8",
    "vendor/mermaid.min.js": "text/javascript; charset=utf-8",
}


def current_visual_config() -> VisualConfig:
    """当前生效的 ``visual`` 配置；应用尚未加载配置时（测试、启动早期）按默认值。"""
    loaded = get_agent_config()
    config = loaded.get("config") if loaded else None
    visual = getattr(config, "visual", None)
    return visual if isinstance(visual, VisualConfig) else VisualConfig()


def frame_csp(html_widgets: bool) -> str:
    """框架页的 CSP：只能加载本路由下的脚本 / 样式，任何网络请求都不放行。

    HTML 交互组件的脚本是内联的，开启时才加 ``'unsafe-inline'``——脚本跑在不同源的沙箱里，
    碰不到外层页面；CSP 在这里的作用是堵住外传数据的通道。
    """
    script_src = "script-src 'self'" + (" 'unsafe-inline'" if html_widgets else "")
    return "; ".join([
        "default-src 'none'",
        script_src,
        "style-src 'self' 'unsafe-inline'",
        "img-src data: blob:",
        "font-src data:",
        "connect-src 'none'",
        "frame-src 'none'",
        "form-action 'none'",
        "base-uri 'none'",
    ])


@router.get("/frame.html", include_in_schema=False)
def frame_page() -> HTMLResponse:
    visual = current_visual_config()
    widgets = visual.enabled and visual.html_widgets
    html = (STATIC_DIR / "frame.html").read_text(encoding="utf-8")
    html = html.replace('data-enabled="0"', f'data-enabled="{int(visual.enabled)}"', 1)
    html = html.replace('data-widgets="0"', f'data-widgets="{int(widgets)}"', 1)
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": frame_csp(widgets),
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get("/{path:path}", include_in_schema=False)
def frame_asset(path: str) -> FileResponse:
    media_type = ASSETS.get(path)
    if media_type is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(
        STATIC_DIR / path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )
