"""FastAPI application entry point"""

import logging
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
from fastapi.responses import FileResponse, JSONResponse
from langchain_core.messages import HumanMessage

from .config import Config
from .db import init_database
from .domain.bloom.bloom_scheduler import start_scheduler
from .model import create_model
from .models.api import HealthResponse
from .services import get_agent_config, init_agent_config
from .services import init_scheduler, shutdown_scheduler, reload_all_tasks
from .services.vector_store import VectorStore
from .skills import find_skills_root, discover_skills
from .api import (
    chat_router,
    sessions_router,
    files_router,
    auth_router,
    vector_store_router,
    bloom_router,
    forex_router,
    prompts_router,
    settings_router,
    skill_center_router,
    terminal_router,
    scheduled_tasks_router,
)

logger = logging.getLogger(__name__)

frontend_dist = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist"
)

agent_config = None
db_instance = None


def setup_logging():
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return None

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    log_file = log_dir / "easy_agent.log"

    formatter = _CustomFormatter(
        fmt="[{asctime}]|{levelname}|{funcName}:{lineno}| {message}",
        datefmt="%Y-%m-%d %H:%M:%S",
        style="{",
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(_RunidFilter())

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(_RunidFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_logger.setLevel(logging.INFO)
    uvicorn_logger.addHandler(console_handler)
    uvicorn_logger.addHandler(file_handler)

    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    uvicorn_access_logger.setLevel(logging.INFO)
    uvicorn_access_logger.addHandler(console_handler)
    uvicorn_access_logger.addHandler(file_handler)

    # deepagents 的技能名校验仅允许小写字母+连字符，但本项目部分技能
    # （如 strategy_fx）因 Python 反射加载要求必须使用下划线命名，无法改名。
    #该校验仅为 WARNING 且不影响加载（向后兼容），故屏蔽此噪声日志。
    logging.getLogger("deepagents.middleware.skills").setLevel(logging.ERROR)

    return log_file


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

    log_file = setup_logging()
    logger.info(f"日志文件: {log_file}")

    logger.info("=" * 60)
    logger.info("Easy Agent Web Service 初始化中...")
    logger.info(f"项目目录: {project_root}")
    logger.info(f"操作系统: {platform.system()} {platform.release()}")
    logger.info(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    # ── 环境识别 ──
    # AGENT_ENV 决定运行环境: dev | test | prod
    # 1. 若设置了 EASY_CONFIG 环境变量，直接使用（entrypoint.sh 场景）
    # 2. 若设置了 AGENT_ENV，按环境选择 config.{env}.yaml
    # 3. 默认使用 config.yaml
    agent_env = os.environ.get("AGENT_ENV", "").lower()
    config_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config"
    )

    if os.environ.get("EASY_CONFIG"):
        config_path = os.environ["EASY_CONFIG"]
    elif agent_env in ("dev", "test", "prod"):
        candidate = os.path.join(config_dir, f"config.{agent_env}.yaml")
        config_path = candidate if os.path.exists(candidate) else os.path.join(config_dir, "config.yaml")
    else:
        config_path = os.path.join(config_dir, "config.yaml")

    # 打印环境信息和配置文件
    logger.info("=" * 60)
    logger.info(f"AGENT_ENV: {agent_env or '(未设置, 使用默认)'}")
    logger.info(f"配置文件: {config_path}")
    logger.info("=" * 60)

    try:
        config = Config.from_yaml(config_path)
        logger.info(f"✅ 配置文件加载成功: {config_path}")
        logger.info(f"LLM Provider: {config.llm.provider}")
        logger.info(f"LLM Model: {config.llm.model}")
        logger.info(f"LLM Protocol: {config.llm.protocol}")
        logger.info(f"Database Type: {config.database.type}")
    except Exception as e:
        logger.warning(f"⚠️ 配置文件加载失败，使用默认配置: {e}")
        config = None

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

    if config and hasattr(config, "vector_store"):
        vs_config = config.vector_store.model_dump()
        if vs_config.get("enabled"):
            try:
                vs = VectorStore(vs_config)
                app.state.vector_store = vs
                logger.info(
                    f"✅ 向量数据库已启用 | provider: {vs_config.get('embedding_provider', 'unknown')}"
                )
            except Exception as e:
                logger.warning(f"⚠️ 向量数据库初始化失败: {e}")
                app.state.vector_store = None
        else:
            logger.info("ℹ️ 向量数据库未启用")

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

    # 打印系统提示词完整内容，便于启动时核查
    logger.info("=" * 60)
    logger.info("📝 系统提示词内容:")
    logger.info("-" * 60)
    for line in system_prompt.splitlines():
        logger.info(line)
    logger.info("-" * 60)
    logger.info(f"📝 系统提示词结束 (共 {len(system_prompt)} 字符)")
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
            logger.info(f"📂 Skills 目录: {skills_root}")
            logger.info(f"📁 发现 {len(skills)} 个 skills:")
            for skill in skills:
                logger.info(f"  - {skill['name']}: {skill['path']}")
        else:
            skills_root = ""
            logger.info("ℹ️ 未发现任何 skills")

        mcp_tools = []
        # MCP 不在启动时全局加载，改为按用户配置动态加载（见 agent_manager）
        logger.info("ℹ️ MCP 将按用户配置动态加载（不在启动时预加载）")

        init_agent_config(
            config=config,
            system_prompt=system_prompt,
            skills_root=skills_root,
        )
        agent_config = {"config": config}
        logger.info("✅ Agent 配置加载成功")

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
            reload_all_tasks()
            scheduler.start()
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
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"全局异常: {str(exc)}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


app.include_router(chat_router)
app.include_router(sessions_router)
app.include_router(files_router)
app.include_router(auth_router)
app.include_router(vector_store_router)
app.include_router(bloom_router)
app.include_router(forex_router)
app.include_router(prompts_router)
app.include_router(settings_router)
app.include_router(skill_center_router)
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
            "model": _cfg["config"].llm.model_name,
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
