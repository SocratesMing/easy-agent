"""Web server runner - entry point for 'uv run easy-web'"""

import argparse
import os
import platform

import uvicorn

from easy_agent.utils.paths import PROJECT_ROOT

# 根据操作系统选择默认监听地址：
#   Linux / macOS -> 0.0.0.0（允许外部访问，适合服务器/容器）
#   Windows       -> 127.0.0.1（仅本机访问，避免 Windows 防火墙弹窗）
DEFAULT_HOST = "127.0.0.1" if platform.system() == "Windows" else "0.0.0.0"


def run_web():
    """Start the Easy Agent Web Server"""
    parser = argparse.ArgumentParser(description="Easy Agent Web 服务")
    parser.add_argument("--host", type=str, default=DEFAULT_HOST, help=f"监听地址（默认 {DEFAULT_HOST}）")
    parser.add_argument("--port", type=int, default=8080, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="启用热重载（开发模式）")
    parser.add_argument("--workers", type=int, default=1, help="工作进程数")
    parser.add_argument(
        "--log-level",
        choices=("debug", "info", "warning", "error"),
        help="日志级别（默认 info，等同环境变量 EASY_LOG_LEVEL）；debug 时大模型请求上下文与返回报文写进 proc 日志",
    )
    args = parser.parse_args()
    if args.log_level:
        # 先于 .env 加载写入：命令行优先于 .env 文件；--reload / 多 worker 的子进程也会继承
        os.environ["EASY_LOG_LEVEL"] = args.log_level

    os.chdir(PROJECT_ROOT)

    from easy_agent.bootstrap.initialization import initialize_runtime  # 延迟导入：测试 monkeypatch 语义：按模块属性替换，函数内导入才能取到替换后的对象

    initialize_runtime()

    uvicorn.run(
        "easy_agent.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,
        log_level="info",
        log_config=None,
    )
