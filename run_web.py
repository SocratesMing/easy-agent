"""Wukong Agent Web 服务启动脚本"""

import argparse
import os
import sys
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Wukong Agent Web 服务")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="启用热重载（开发模式）")
    parser.add_argument("--workers", type=int, default=1, help="工作进程数")
    args = parser.parse_args()

    os.environ.setdefault("WUKONG_CONFIG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "wukong_agent", "config", "config.yaml"))

    print(f"""
╔══════════════════════════════════════════╗
║     🐵 Wukong Agent Web Service          ║
╠══════════════════════════════════════════╣
║  地址: http://{args.host}:{args.port}            ║
║  模式: {'开发 (热重载)' if args.reload else '生产'}                    ║
╚══════════════════════════════════════════╝
""")

    uvicorn.run(
        "wukong_agent.web.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,
        log_level="info",
    )


if __name__ == "__main__":
    main()
