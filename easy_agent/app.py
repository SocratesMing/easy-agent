"""FastAPI application entry point"""

import logging
from logging.handlers import RotatingFileHandler
import os
import platform
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage

from .config import Config, AgentConfig
from .db import init_database
from .domain.bloom.bloom_scheduler import start_scheduler
from .model import create_model
from .models.api import HealthResponse
from .services import get_agent_config, init_agent_config
from .services import init_scheduler, shutdown_scheduler, reload_all_tasks
from .skills import find_skills_root, discover_skills
from .api import (
    chat_router,
    sessions_router,
    files_router,
    auth_router,
    bloom_router,
    forex_router,
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


def setup_logging(log_config: dict | None = None):
    """按配置文件中的 log 段初始化日志。

    log_config 字段：
        dir:    日志目录（环境变量 EASY_LOG_DIR 可覆盖）
        file:   日志文件名（留空则默认 easy_agent.log）
        format: logging 格式串（% 风格：%(asctime)s 等）
        level:  日志级别（默认 info）
    lifespan 会调用两次（先默认、后按配置），故每次调用都按新配置重建 handler。
    """
    cfg = log_config or {}
    log_dir = os.getenv("EASY_LOG_DIR") or cfg.get("dir") or "./logs"
    log_file_name = cfg.get("file") or "easy_agent.log"
    fmt = (
        cfg.get("format")
        or "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    level_name = (cfg.get("level") or "info").lower()
    level = getattr(logging, level_name.upper(), logging.INFO)

    root_logger = logging.getLogger()
    # 先移除已有 handler，确保按新配置重建（支持运行期按配置重设）
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass

    log_dir_path = Path(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)
    log_file = log_dir_path / log_file_name

    formatter = _CustomFormatter(
        fmt=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        style="%",
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(_RunidFilter())

    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(_RunidFilter())

    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_logger.setLevel(level)
    uvicorn_logger.addHandler(console_handler)
    uvicorn_logger.addHandler(file_handler)

    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    uvicorn_access_logger.setLevel(level)
    uvicorn_access_logger.addHandler(console_handler)
    uvicorn_access_logger.addHandler(file_handler)

    # deepagents 的技能名校验仅允许小写字母+连字符，但本项目部分技能
    # （如 strategy_fx）因 Python 反射加载要求必须使用下划线命名，无法改名。
    #该校验仅为 WARNING 且不影响加载（向后兼容），故屏蔽此噪声日志。
    logging.getLogger("deepagents.middleware.skills").setLevel(logging.ERROR)

    return str(log_file)


class _RunidFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "runid"):
            record.runid = "-"
        return True


class _CustomFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        ct = datetime.fromtimestamp(record.created)
        if datefmt:
            s = ct.strftime(datefmt)
        else:
            s = ct.strftime("%Y-%m-%d %H:%M:%S")
        return f"{s}.{int(record.msecs * 1000):06d}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent_config, db_instance

    project_root = Path(__file__).parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    os.chdir(project_root)

    # ── 环境识别 & 配置路径解析（先静默确定配置，再初始化日志格式）──
    # AGENT_ENV 决定运行环境: dev | test | prod
    # 1. 若设置了 EASY_CONFIG 环境变量，直接使用（entrypoint.sh 场景）
    # 2. 若设置了 AGENT_ENV，按环境选择 config.{env}.yaml
    # 3. 未设置时：优先 config.dev.yaml（开发默认），兜底 config.yaml
    agent_env = os.environ.get("AGENT_ENV", "").lower()
    # Windows 启动默认使用 dev 环境（除非用户显式设置了 AGENT_ENV 或 EASY_CONFIG）
    if platform.system() == "Windows" and not os.environ.get("AGENT_ENV") and not os.environ.get("EASY_CONFIG"):
        agent_env = "dev"
    config_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config"
    )

    if os.environ.get("EASY_CONFIG"):
        config_path = os.environ["EASY_CONFIG"]
    elif agent_env in ("dev", "test", "prod"):
        candidate = os.path.join(config_dir, f"config.{agent_env}.yaml")
        config_path = candidate if os.path.exists(candidate) else os.path.join(config_dir, "config.yaml")
    else:
        # 未设置 AGENT_ENV：优先 dev 配置，兜底 config.yaml
        dev_candidate = os.path.join(config_dir, "config.dev.yaml")
        config_path = dev_candidate if os.path.exists(dev_candidate) else os.path.join(config_dir, "config.yaml")
        agent_env = "dev" if os.path.exists(dev_candidate) else "(默认)"

    # 先加载配置并按其 log 段初始化日志格式，使启动日志从一开始就使用
    # 配置文件中的 format（而非默认的 " - " 分隔格式）。
    config = None
    try:
        config = Config.from_yaml(config_path)
        log_cfg = config.log.model_dump()
    except Exception as e:
        logger.error(
            f"❌ 配置文件加载失败，服务将以降级模式启动（聊天等功能不可用）: {e}\n"
            f"   请检查配置文件（{config_path}）的 active model 是否配置了 api_key，"
            f"或对应的 ${{ENV_VAR}} 环境变量是否已设置。"
        )
        log_cfg = None

    log_file = setup_logging(log_cfg)

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
            f"日志初始化完成 | 目录: {log_cfg.get('dir')} | 文件: {log_file} | 级别: {log_cfg.get('level')}"
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
        logger.info("✅ 数据库初始化完成")
    except Exception as e:
        logger.error(f"❌ 数据库初始化失败: {e}")
        raise

    if (
        config
        and hasattr(config.agent, "system_prompt_path")
        and config.agent.system_prompt_path
    ):
        config_dir = os.path.dirname(os.path.abspath(config_path))
        system_prompt_path = os.path.join(config_dir, config.agent.system_prompt_path)
    else:
        system_prompt_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config", "system_prompt.md"
        )

    if os.path.exists(system_prompt_path):
        with open(system_prompt_path, "r", encoding="utf-8") as f:
            system_prompt = f.read()
        logger.info(
            f"✅ 系统提示词加载成功: {system_prompt_path} ({len(system_prompt)} 字符)"
        )
    else:
        system_prompt = "你是一个有帮助的 AI 助手。"
        logger.warning(f"⚠️ 系统提示词文件不存在: {system_prompt_path}，使用默认提示词")

    # 打印工作目录与记忆目录的绝对路径（记忆文件按用户/会话动态生成，故给出基目录与模板路径）
    # 配置未加载（config 为 None）时使用 AgentConfig 默认值，保证降级启动不崩溃
    _agent_cfg = config.agent if config else AgentConfig()
    _ws_abs = os.path.abspath(_agent_cfg.workspace_dir)
    _mem_abs = os.path.abspath(_agent_cfg.memories_dir)
    logger.info("=" * 60)
    logger.info(f"📁 工作目录 (workspace): {_ws_abs}")
    logger.info(f"🧠 记忆目录 (memories):  {_mem_abs}")
    logger.info(f"   长期记忆文件: {_mem_abs}/{{username}}/AGENTS.md")
    logger.info(f"   会话记忆文件: {_ws_abs}/{{username}}/{{workspace_name}}/memory.md")
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

        try:
            bloom_llm = create_model(config)
            bloom_thread = threading.Thread(
                target=start_scheduler,
                args=(db, bloom_llm),
                daemon=True,
                name="bloom-scheduler",
            )
            bloom_thread.start()
            logger.info("✅ 彭博定时任务已启动 (每日 17:00)")
        except Exception as e:
            logger.warning(f"⚠️ 彭博定时任务启动失败: {e}")

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
    # 禁用默认 /docs、/redoc（默认从 CDN 加载 Swagger UI 资源，离线环境无法访问）。
    # 下面用本地静态资源重新提供 /docs，离线可用。
    docs_url=None,
    redoc_url=None,
)

# 跨域访问（CORS）：
#   - 默认放行所有来源（"*"），兼容开发期跨域直连与同 pod 部署；
#   - 生产环境如需收紧，设置环境变量 EASY_CORS_ALLOW_ORIGINS 为逗号分隔的可信域名，
#     例如 "https://app.example.com,https://admin.example.com"。
_cors_raw = os.getenv("EASY_CORS_ALLOW_ORIGINS")
if _cors_raw:
    allow_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]
else:
    allow_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 离线 Swagger UI：挂载本地 vendored 的 swagger-ui-dist 静态资源，
# 并自定义 /docs 路由指向本地 JS/CSS，避免从 CDN 加载（离线环境 CDN 不可达）。
_swagger_static_dir = Path(__file__).parent / "static" / "swagger-ui"
if _swagger_static_dir.is_dir():
    app.mount("/swagger-ui-assets", StaticFiles(directory=str(_swagger_static_dir)), name="swagger-ui-assets")

    @app.get("/docs", include_in_schema=False)
    async def custom_swagger_ui_html():
        return get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title=f"{app.title} - Swagger UI",
            swagger_js_url="/swagger-ui-assets/swagger-ui-bundle.js",
            swagger_css_url="/swagger-ui-assets/swagger-ui.css",
            oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        )

    @app.get(app.swagger_ui_oauth2_redirect_url, include_in_schema=False)
    async def swagger_ui_redirect():
        return HTMLResponse(app.swagger_ui_oauth2_redirect_html)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"全局异常: {str(exc)}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


app.include_router(chat_router)
app.include_router(sessions_router)
app.include_router(files_router)
app.include_router(auth_router)
app.include_router(bloom_router)
app.include_router(forex_router)
app.include_router(prompts_router)
app.include_router(settings_router)
app.include_router(skill_center_router)
if terminal_router is not None:
    app.include_router(terminal_router)
app.include_router(scheduled_tasks_router)


@app.get("/api/health", summary="健康检查", response_model=HealthResponse)
async def health_check():
    return HealthResponse(
        status="healthy",
        agent_initialized=agent_config is not None,
        database_initialized=db_instance is not None,
    )


@app.get("/api/config", summary="获取Agent配置")
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
    setup_logging()

    uvicorn.run(
        "easy_agent.app:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
