"""FastAPI application entry point"""

import logging
import os
import platform
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from .initialization import initialize_runtime

runtime_initialization = initialize_runtime()

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from langchain_core.messages import HumanMessage

from .config import AgentConfig
from .db import init_database
from .model import create_model
from .models.api import HealthResponse
from .services import get_agent_config, init_agent_config
from .services import init_scheduler, shutdown_scheduler, reload_all_tasks
from .services.prompt_loader import load_system_prompt
from .skills import find_skills_root, discover_skills
from .api import (
    chat_router,
    sessions_router,
    files_router,
    auth_router,
    completion_router,
    prompts_router,
    settings_router,
    skill_center_router,
    scheduled_tasks_router,
)

# Web Terminal 依赖 pty（POSIX 专用），Windows 不支持，故不加载该模块
terminal_router = None
if platform.system() != "Windows":
    from .api import terminal_router

logger = logging.getLogger(__name__)

frontend_dist = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist"
)

agent_config = None
db_instance = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent_config, db_instance

    project_root = Path(__file__).parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    os.chdir(project_root)

    environment = runtime_initialization.environment
    agent_env = environment.agent_env
    config_path = runtime_initialization.config_path
    config = runtime_initialization.config
    config_error = runtime_initialization.config_error
    log_files = runtime_initialization.log_files

    logger.info("=" * 60)
    logger.info("Easy Agent Web Service 初始化中...")
    logger.info(f"项目目录: {project_root}")
    logger.info(f"操作系统: {platform.system()} {platform.release()}")
    logger.info(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    # 打印环境信息和配置文件
    logger.info("=" * 60)
    logger.info(f"AGENT_ENV: {agent_env or '(未设置, 默认 dev)'}")
    logger.info(f"配置文件: {config_path}")
    logger.info("=" * 60)

    if config_error:
        logger.error(
            f"❌ 配置文件加载失败，服务将以降级模式启动（聊天等功能不可用）: {config_error}"
        )
        logger.error(
            f"   请检查 {config_path} 是否存在，且其中的 ${{ENV_VAR}} 占位符都能在"
            "项目根 .env（或运行环境）中取到值；active model 也必须配置 api_key。"
        )

    if config:
        logger.info(f"✅ 配置文件加载成功: {config_path}")

        # 启动时创建配置文件中所有缺失的目录（workspace/memories/logs/sessions/
        # skills/prompts/sqlite 父目录/external_dirs 宿主机路径等）
        created_dirs = config.ensure_directories()
        if created_dirs:
            logger.info(f"📁 已创建 {len(created_dirs)} 个配置目录: {created_dirs}")
        else:
            logger.info("📁 配置目录均已存在，无需创建")

        logger.info(
            f"日志初始化完成 | 目录: {Path(log_files.get('proc', '')).parent} | "
            f"运行日志: {log_files.get('proc')} | 错误日志: {log_files.get('err')} | "
            f"通信日志: {log_files.get('comm')}"
        )
        logger.info(f"LLM Provider: {config.llm.provider}")
        logger.info(f"LLM Model: {config.llm.model}")
        logger.info(f"LLM Protocol: {config.llm.protocol}")
        logger.info(f"Database Type: {config.database.type}")
    else:
        logger.warning("⚠️ 配置未加载，后续将使用内置默认值（部分功能可能不可用）")

    if config:
        try:
            llm = create_model(config)
            logger.info(
                f"🔌 正在测试 LLM 连接 | provider: {config.llm.provider} | model: {config.llm.model}"
            )

            resp = await llm.ainvoke([HumanMessage(content="hi")])
            reply = resp.content if hasattr(resp, "content") else str(resp)
            logger.info(f"✅ LLM 连接成功 | 回复: {reply[:100]}")
        except Exception as e:
            logger.warning(f"⚠️ LLM 连接失败: {e}")
            logger.warning("⚠️ 服务将继续启动，但聊天功能可能不可用")

    try:
        db_config = config.database.model_dump() if config else {}
        db = init_database(db_config)
        app.state.db = db
        db_instance = db
        logger.info(
            f"✅ 数据库初始化完成 | 实际类型: {getattr(db, 'db_type', 'unknown')}"
        )
    except Exception as e:
        logger.error(f"❌ 数据库初始化失败: {e}")
        raise

    # 提示词统一由 prompt_loader 加载：
    # 优先 <config_dir>/prompts/system.md（+ fragments/*.md），
    # 其次兼容旧的 system_prompt_path 单文件，最后回落到内置默认提示词。
    config_dir = os.path.dirname(os.path.abspath(config_path)) if config_path else None
    configured_path = (
        config.agent.system_prompt_path
        if config and hasattr(config.agent, "system_prompt_path")
        else None
    )
    system_prompt = load_system_prompt(
        config_dir=config_dir, configured_path=configured_path
    )
    logger.info(f"✅ 系统提示词加载完成（{len(system_prompt)} 字符）")

    # 打印工作目录与记忆目录的绝对路径（记忆文件按用户/会话动态生成，故给出基目录与模板路径）
    # 配置未加载（config 为 None）时使用 AgentConfig 默认值，保证降级启动不崩溃
    _agent_cfg = config.agent if config else AgentConfig()
    _ws_abs = os.path.abspath(_agent_cfg.workspace_dir)
    _mem_abs = os.path.abspath(_agent_cfg.memories_dir)
    logger.info("=" * 60)
    logger.info(f"📁 工作目录 (workspace): {_ws_abs}")
    logger.info(f"🧠 记忆目录 (memories):  {_mem_abs}")
    logger.info(f"   长期记忆文件: {_mem_abs}/{{username}}/AGENTS.md")
    logger.info(f"   会话记忆文件: {_ws_abs}/{{username}}/session/{{workspace_name}}/memory.md")
    logger.info("=" * 60)

    # 注入当前时间和时区，供定时任务 cron 表达式生成参考
    now_dt = datetime.now().astimezone()
    tz_name = now_dt.strftime("%Z") or "Asia/Shanghai"
    system_prompt += (
        f"\n## 当前时间\n"
        f"{now_dt.strftime('%Y-%m-%d %H:%M:%S')} (时区: {tz_name})\n"
    )

    if config:
        skills_dir_config = (
            config.tools.skills_dir if hasattr(config.tools, "skills_dir") else None
        )
        skills_root = find_skills_root(skills_dir_config)

        if skills_root:
            skills = discover_skills(skills_root)
            logger.info(f"📂 Skills 目录 (配置: {skills_dir_config}): {skills_root}")
            logger.info(f"📁 发现 {len(skills)} 个 skills:")
            for skill in skills:
                logger.info(f"  - {skill['name']}: {skill['path']}")
        else:
            skills_root = ""
            logger.info("ℹ️ 未发现任何 skills")

        mcp_tools = []
        # MCP 不在启动时全局加载，改为按用户配置动态加载（见 agent_manager）
        logger.info("ℹ️ MCP 将按用户配置动态加载（不在启动时预加载）")

        if config:
            init_agent_config(
                config=config,
                system_prompt=system_prompt,
                skills_root=skills_root,
                agent_env=agent_env if agent_env in ("dev", "test", "prod") else "",
            )
            agent_config = {"config": config}
            logger.info("✅ Agent 配置加载成功")
        else:
            logger.warning("⚠️ 配置未加载，Agent 未初始化，聊天等功能将不可用")

        # 定时任务调度器（AsyncIOScheduler）
        try:
            scheduler = init_scheduler()
            scheduler.start()
            reload_all_tasks()
            logger.info("✅ 定时任务调度器已启动 (AsyncIOScheduler)")
        except Exception as e:
            logger.warning(f"⚠️ 定时任务调度器启动失败: {e}")
    else:
        logger.warning("⚠️ Agent 配置未加载")

    logger.info("=" * 60)
    logger.info("🚀 Easy Agent Web Service 启动完成")
    logger.info("=" * 60)
    yield

    try:
        shutdown_scheduler()
        logger.info("[关闭] 定时任务调度器已关闭")
    except Exception as e:
        logger.warning(f"[关闭] 定时任务调度器关闭失败: {e}")

    if hasattr(app.state, "db") and app.state.db:
        app.state.db.close()

    logger.info("[关闭] 👋 服务已关闭")


app = FastAPI(
    title="Easy Agent API",
    description="基于 DeepAgents 的智能体框架 API",
    version="1.0.0",
    lifespan=lifespan,
    # 接口文档使用 Redoc；OpenAPI 数据仍由本服务提供。
    docs_url=None,
    redoc_url="/docs",
)

# 跨域访问（CORS）：
#   - 默认放行所有来源（"*"），兼容开发期跨域直连与同 pod 部署；
#   - 生产环境如需收紧，设置环境变量 EASY_CORS_ALLOW_ORIGINS 为逗号分隔的可信域名，
#     例如 "https://app.example.com,https://admin.example.com"。
app.add_middleware(
    CORSMiddleware,
    allow_origins=runtime_initialization.environment.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_client_ip(request: Request) -> str:
    """从请求头或连接信息中提取客户端真实 IP。"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


# HTTP 访问日志：记录每个请求的方法、路径、客户端 IP。
# 便于确认登录/登出等关键操作是否有请求到达后端（业务日志见 api/auth.py）。
# 跳过静态资源、文档页与高频无意义路径，避免日志噪音。
_ACCESS_SKIP_PREFIXES = (
    "/docs",
    "/openapi.json",
    "/static",
    "/assets",
)
_ACCESS_SKIP_SUFFIXES = (
    ".js",
    ".css",
    ".svg",
    ".ico",
    ".png",
    ".woff",
    ".woff2",
    ".ttf",
    ".map",
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    path = request.url.path
    if (
        path in ("/", "/health")
        or path.startswith(_ACCESS_SKIP_PREFIXES)
        or path.endswith(_ACCESS_SKIP_SUFFIXES)
    ):
        return await call_next(request)
    client_ip = _get_client_ip(request)
    logger.info(f"[请求] {request.method} {path} | IP: {client_ip}")
    return await call_next(request)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"全局异常: {str(exc)}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


app.include_router(chat_router)
app.include_router(sessions_router)
app.include_router(files_router)
app.include_router(auth_router)
app.include_router(completion_router)
app.include_router(prompts_router)
app.include_router(settings_router)
app.include_router(skill_center_router)
if terminal_router is not None:
    app.include_router(terminal_router)
app.include_router(scheduled_tasks_router)


@app.get("/agent/health", summary="健康检查", response_model=HealthResponse)
async def health_check():
    return HealthResponse(
        status="healthy",
        agent_initialized=agent_config is not None,
        database_initialized=db_instance is not None,
    )


@app.get("/agent/config", summary="获取Agent配置")
async def get_config():
    _cfg = get_agent_config()
    if _cfg:
        return {
            "system_prompt": _cfg["system_prompt"][:100] + "...",
            "provider": _cfg["config"].llm.provider,
            "model": _cfg["config"].llm.model,
        }
    return {"status": "not initialized"}


@app.get("/", response_class=FileResponse)
async def serve_frontend():
    index_html = os.path.join(frontend_dist, "index.html")
    if os.path.exists(index_html):
        return FileResponse(index_html)
    return FileResponse(os.path.join(frontend_dist, "index.html"))


@app.get("/{full_path:path}", response_class=FileResponse)
async def serve_static(full_path: str):
    static_file = os.path.join(frontend_dist, full_path)
    if os.path.exists(static_file) and os.path.isfile(static_file):
        return FileResponse(static_file)

    index_html = os.path.join(frontend_dist, "index.html")
    if os.path.exists(index_html):
        return FileResponse(index_html)

    return JSONResponse({"error": "Not found"}, status_code=404)


def run_server(host: str = "0.0.0.0", port: int = 8000):
    uvicorn.run(
        "easy_agent.app:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
        log_config=None,
    )
