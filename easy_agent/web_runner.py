"""Web server runner - entry point for 'uv run easy-web'"""

import argparse
import os
import platform
from pathlib import Path

import uvicorn

# 根据操作系统选择默认监听地址：
#   Linux / macOS -> 0.0.0.0（允许外部访问，适合服务器/容器）
#   Windows       -> 127.0.0.1（仅本机访问，避免 Windows 防火墙弹窗）
DEFAULT_HOST = "127.0.0.1" if platform.system() == "Windows" else "0.0.0.0"


def run_web():
    """Start the Easy Agent Web Server"""
    parser = argparse.ArgumentParser(description="Easy Agent Web 服务")
    parser.add_argument("--host", type=str, default=DEFAULT_HOST, help=f"监听地址（默认 {DEFAULT_HOST}）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="启用热重载（开发模式）")
    parser.add_argument("--workers", type=int, default=1, help="工作进程数")
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent
    os.chdir(project_root)

    # 配置文件路径完全交给 app.py 的 lifespan 决定：
    #   - EASY_CONFIG 优先（显式指定）
    #   - AGENT_ENV=dev/test/prod -> config.{env}.yaml
    #   - 未设置 -> 优先 config.dev.yaml，兜底 config.yaml
    # 此处不再强制设置 EASY_CONFIG，避免覆盖 app.py 的回退逻辑。

    uvicorn.run(
        "easy_agent.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,
        log_level="info",
    )
