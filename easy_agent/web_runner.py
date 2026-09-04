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

    # 在 uvicorn 启动（含 --reload 子进程）之前加载项目根 .env，
    # 供运行时环境变量使用；应用配置值完全来自 YAML 文件。
    from easy_agent.utils.env_loader import load_project_env

    load_project_env()

    project_root = Path(__file__).parent.parent
    os.chdir(project_root)

    # 配置文件路径完全交给 Config.resolve_config_path 决定；
    # EASY_CONFIG / AGENT_ENV 只选择文件，不参与配置值解析。

    uvicorn.run(
        "easy_agent.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,
        log_level="info",
    )
