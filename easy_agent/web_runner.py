"""Web server runner - entry point for 'uv run easy-web'"""

import argparse
import os
from pathlib import Path

import uvicorn


def run_web():
    """Start the Easy Agent Web Server"""
    parser = argparse.ArgumentParser(description="Easy Agent Web 服务")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="启用热重载（开发模式）")
    parser.add_argument("--workers", type=int, default=1, help="工作进程数")
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent
    os.chdir(project_root)

    # 仅当未显式指定 EASY_CONFIG 且未设置 AGENT_ENV 时，才回退到 config.yaml。
    # 若设置了 AGENT_ENV，交给 app.py 的 lifespan 按 dev/test/prod 选择 config.{env}.yaml。
    if not os.environ.get("EASY_CONFIG") and not os.environ.get("AGENT_ENV"):
        config_path = project_root / "easy_agent" / "config" / "config.yaml"
        os.environ.setdefault("EASY_CONFIG", str(config_path))

    uvicorn.run(
        "easy_agent.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,
        log_level="info",
    )
