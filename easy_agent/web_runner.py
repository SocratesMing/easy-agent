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


def graceful_shutdown_seconds() -> int:
    """Ctrl+C 后最多等多久再强制退出（EASY_GRACEFUL_SHUTDOWN_SECONDS，默认 5 秒）。

    uvicorn 默认 `timeout_graceful_shutdown=None`，含义是**无限等待**现有连接结束，
    它只会打印 "Shutting down" 然后一直卡住。本项目有多条长连接/长任务会占住它：

    - SSE 流式输出 `/agent/chat/.../stream` 与 `/agent/chat/stream/live`
      （后者是 `while True: await q.get()`，只要事件中枢还在就不返回）
    - 终端 WebSocket `/agent/terminal/ws`（pty 读取循环 + 子进程）
    - 与其解耦的后台流式任务（`_detached_bg_tasks`，设计上客户端断开也不取消）

    所以必须兜一个上限：超时后 uvicorn 会取消剩余连接与任务再退出。
    设为 0 表示不等待、直接取消（开发时想秒退可以这么配）。
    """
    raw = (os.getenv("EASY_GRACEFUL_SHUTDOWN_SECONDS") or "").strip()
    return int(raw) if raw.isdigit() else 5


def run_web():
    """Start the Easy Agent Web Server"""
    parser = argparse.ArgumentParser(description="Easy Agent Web 服务")
    parser.add_argument("--host", type=str, default=DEFAULT_HOST, help=f"监听地址（默认 {DEFAULT_HOST}）")
    parser.add_argument("--port", type=int, default=8080, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="启用热重载（开发模式）")
    parser.add_argument("--workers", type=int, default=1, help="工作进程数")
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent
    os.chdir(project_root)

    from easy_agent.initialization import initialize_runtime

    initialize_runtime()

    uvicorn.run(
        "easy_agent.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,
        log_level="info",
        log_config=None,
        # 不给上限的话 Ctrl+C 会永远停在 "Shutting down"，见 graceful_shutdown_seconds()
        timeout_graceful_shutdown=graceful_shutdown_seconds(),
    )
