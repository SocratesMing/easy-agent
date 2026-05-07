"""FastAPI application entry point"""

import logging
import os
import platform
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .db import init_database
from .models.api import HealthResponse
from .api import (
    chat_router,
    sessions_router,
    files_router,
    auth_router,
    vector_store_router,
    bloom_router,
    forex_router,
    prompts_router,
)

logger = logging.getLogger(__name__)

frontend_dist = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist")

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
        fmt="[{asctime}]|{levelname}|FMQT|{runid}||{process}|{thread}|{threadName}|{funcName}:{lineno}| {message}",
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

    return log_file


class _RunidFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "runid"):
            record.runid = "-"
        return True


class _CustomFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        from datetime import datetime
        ct = datetime.fromtimestamp(record.created)
        if datefmt:
            s = ct.strftime(datefmt)
        else:
            s = ct.strftime("%Y-%m-%d %H:%M:%S")
        return f"{s}.{int(record.msecs * 1000):06d}"


def _setup_shared_deps(skills_root: str) -> str:
    if not skills_root:
        return ""

    shared_dir = Path(skills_root).parent / "shared_deps" / "node_modules"
    shared_dir.parent.mkdir(parents=True, exist_ok=True)

    skill_npm_packages = ["docx", "pptxgenjs"]

    if not shared_dir.exists():
        shared_dir.mkdir(parents=True, exist_ok=True)

    missing = [pkg for pkg in skill_npm_packages if not (shared_dir / pkg).exists()]
    if not missing:
        logger.info(f"[启动] ✅ 共享依赖已就绪: {shared_dir}")
        return str(shared_dir)

    lock_file = shared_dir.parent / ".install_lock"
    if lock_file.exists():
        logger.info("[启动] ⏳ 等待其他进程完成依赖安装...")
        import time
        for _ in range(60):
            time.sleep(1)
            if not lock_file.exists():
                break
        missing = [pkg for pkg in skill_npm_packages if not (shared_dir / pkg).exists()]
        if not missing:
            logger.info(f"[启动] ✅ 共享依赖已就绪 (由其他进程安装): {shared_dir}")
            return str(shared_dir)

    lock_file.touch()
    try:
        logger.info(f"[启动] 📦 安装共享 skill 依赖: {missing}")
        subprocess.run(
            ["npm", "install"] + missing,
            cwd=str(shared_dir.parent),
            capture_output=True,
            text=True,
            timeout=120,
        )
        still_missing = [pkg for pkg in missing if not (shared_dir / pkg).exists()]
        if still_missing:
            logger.warning(f"[启动] ⚠️ 以下包安装失败: {still_missing}")
        else:
            logger.info(f"[启动] ✅ 共享依赖安装完成: {shared_dir}")
    except Exception as e:
        logger.warning(f"[启动] ⚠️ 共享依赖安装失败: {e}")
    finally:
        lock_file.unlink(missing_ok=True)

    return str(shared_dir)


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
    logger.info("[启动] Easy Agent Web Service 初始化中...")
    logger.info(f"[启动] 项目目录: {project_root}")
    logger.info(f"[启动] 操作系统: {platform.system()} {platform.release()}")
    logger.info(f"[启动] 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    config_path = os.environ.get("EASY_CONFIG", "./easy_agent/config/config.yaml")
    if not os.path.exists(config_path):
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "config.yaml")

    try:
        from .config import Config
        config = Config.from_yaml(config_path)
        logger.info(f"[启动] ✅ 配置文件加载成功: {config_path}")
        logger.info(f"[启动] LLM Provider: {config.llm.provider}")
        logger.info(f"[启动] LLM Model: {config.llm.model}")
        logger.info(f"[启动] Database Type: {config.database.type}")
    except Exception as e:
        logger.warning(f"[启动] ⚠️ 配置文件加载失败，使用默认配置: {e}")
        config = None

    if config:
        try:
            from .model import create_model
            from langchain_core.messages import HumanMessage

            llm = create_model(config)
            logger.info(f"[启动] 🔌 正在测试 LLM 连接 | provider: {config.llm.provider} | model: {config.llm.model}")

            resp = await llm.ainvoke([HumanMessage(content="hi")])
            reply = resp.content if hasattr(resp, "content") else str(resp)
            logger.info(f"[启动] ✅ LLM 连接成功 | 回复: {reply[:100]}")
        except Exception as e:
            logger.warning(f"[启动] ⚠️ LLM 连接失败: {e}")
            logger.warning(f"[启动] ⚠️ 服务将继续启动，但聊天功能可能不可用")

    try:
        db_config = config.database.model_dump() if config else {}
        db = init_database(db_config)
        app.state.db = db
        db_instance = db
        logger.info("[启动] ✅ 数据库初始化完成")
    except Exception as e:
        logger.error(f"[启动] ❌ 数据库初始化失败: {e}")
        raise

    if config and hasattr(config, 'vector_store'):
        vs_config = config.vector_store.model_dump()
        if vs_config.get("enabled"):
            try:
                from .services.vector_store import init_vector_store
                vs = init_vector_store(vs_config)
                app.state.vector_store = vs
                logger.info(f"[启动] ✅ 向量数据库已启用 | provider: {vs_config.get('embedding_provider', 'unknown')}")
            except Exception as e:
                logger.warning(f"[启动] ⚠️ 向量数据库初始化失败: {e}")
                app.state.vector_store = None
        else:
            logger.info("[启动] ℹ️ 向量数据库未启用")

    if config and hasattr(config.agent, 'system_prompt_path') and config.agent.system_prompt_path:
        config_dir = os.path.dirname(os.path.abspath(config_path))
        system_prompt_path = os.path.join(config_dir, config.agent.system_prompt_path)
    else:
        system_prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "system_prompt.md")

    if os.path.exists(system_prompt_path):
        with open(system_prompt_path, "r", encoding="utf-8") as f:
            system_prompt = f.read()
        logger.info(f"[启动] ✅ 系统提示词加载成功: {system_prompt_path} ({len(system_prompt)} 字符)")
    else:
        system_prompt = "你是一个有帮助的 AI 助手。"
        logger.warning(f"[启动] ⚠️ 系统提示词文件不存在: {system_prompt_path}，使用默认提示词")

    if config:
        from .services import init_agent_config
        from .skills import find_skills_root, discover_skills

        skills_dir_config = config.tools.skills_dir if hasattr(config.tools, 'skills_dir') else None
        skills_root = find_skills_root(skills_dir_config)

        if skills_root:
            skills = discover_skills(skills_root)
            logger.info(f"[启动] 📂 Skills 目录: {skills_root}")
            logger.info(f"[启动] 📁 发现 {len(skills)} 个 skills:")
            for skill in skills:
                logger.info(f"[启动]   - {skill['name']}: {skill['path']}")
        else:
            skills_root = ""
            logger.info("[启动] ℹ️ 未发现任何 skills")

        shared_deps_path = _setup_shared_deps(skills_root)

        init_agent_config(
            config=config,
            system_prompt=system_prompt,
            skills_root=skills_root,
            shared_deps_path=shared_deps_path,
        )
        agent_config = {"config": config}
        logger.info("[启动] ✅ Agent 配置加载成功")

        try:
            import threading
            from .domain.bloom.bloom_scheduler import start_scheduler
            bloom_llm = create_model(config)
            bloom_thread = threading.Thread(
                target=start_scheduler,
                args=(db, bloom_llm),
                daemon=True,
                name="bloom-scheduler"
            )
            bloom_thread.start()
            logger.info("[启动] ✅ 彭博定时任务已启动 (每日 17:00)")
        except Exception as e:
            logger.warning(f"[启动] ⚠️ 彭博定时任务启动失败: {e}")
    else:
        logger.warning("[启动] ⚠️ Agent 配置未加载")

    logger.info("=" * 60)
    logger.info("[启动] 🚀 Easy Agent Web Service 启动完成")
    logger.info("=" * 60)
    yield

    if hasattr(app.state, 'db') and app.state.db:
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


@app.get("/api/health", summary="健康检查", response_model=HealthResponse)
async def health_check():
    return HealthResponse(
        status="healthy",
        agent_initialized=agent_config is not None,
        database_initialized=db_instance is not None,
    )


@app.get("/api/config", summary="获取Agent配置")
async def get_config():
    from .services import get_agent_config
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
    import uvicorn

    setup_logging()

    print("=" * 50)
    print("  Easy Agent API Server")
    print("=" * 50)
    print()
    print(f"  Swagger UI:  http://localhost:{port}/docs")
    print(f"  ReDoc:      http://localhost:{port}/redoc")
    print(f"  Health:     http://localhost:{port}/api/health")
    print()
    print(f"  Session-level Agent reuse: Enabled")
    print("=" * 50)
    print()

    uvicorn.run(
        "easy_agent.app:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
