"""FastAPI server for Easy Agent Web UI"""

import logging
import os
import platform
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .db import init_database, get_database
from .models import HealthResponse
from .routes.chat import router as chat_router
from .routes.files import router as files_router
from .routes.sessions import router as sessions_router
from .routes.user import router as user_router, profile_router
from .routes.vector_store import router as vector_store_router

logger = logging.getLogger(__name__)

frontend_dist = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend", "dist")

agent_config = None
db_instance = None


def setup_logging():
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return None
    
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    log_file = log_dir / "easy_agent.log"
    
    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d [%(levelname)s] %(filename)s:%(lineno)d: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent_config, db_instance
    
    project_root = Path(__file__).parent.parent.parent
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
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "config.yaml")

    try:
        from ..config import Config
        config = Config.from_yaml(config_path)
        logger.info(f"[启动] ✅ 配置文件加载成功: {config_path}")
        logger.info(f"[启动] LLM Provider: {config.llm.provider}")
        logger.info(f"[启动] LLM Model: {config.llm.model}")
        logger.info(f"[启动] Database Type: {config.database.type}")
    except Exception as e:
        logger.warning(f"[启动] ⚠️ 配置文件加载失败，使用默认配置: {e}")
        config = None

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
                from .vector_store import init_vector_store
                vs = init_vector_store(vs_config)
                app.state.vector_store = vs
                logger.info(f"[启动] ✅ 向量数据库已启用 | provider: {vs_config.get('embedding_provider', 'unknown')}")
            except Exception as e:
                logger.warning(f"[启动] ⚠️ 向量数据库初始化失败: {e}")
                app.state.vector_store = None
        else:
            logger.info("[启动] ℹ️ 向量数据库未启用")

    system_prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "system_prompt.md")
    if os.path.exists(system_prompt_path):
        with open(system_prompt_path, "r", encoding="utf-8") as f:
            system_prompt = f.read()
        logger.info(f"[启动] ✅ 系统提示词加载成功 ({len(system_prompt)} 字符)")
    else:
        system_prompt = "你是一个有帮助的 AI 助手。"
        logger.warning("[启动] ⚠️ 使用默认系统提示词")

    if config:
        from .service import init_agent_config
        from ..skills import discover_skills
        
        skills = discover_skills(config.tools.skills_dir if hasattr(config.tools, 'skills_dir') else None)
        
        skills_dir_paths = []
        if skills:
            seen_dirs = set()
            for skill in skills:
                skill_parent = str(Path(skill['path']).parent)
                if skill_parent not in seen_dirs:
                    seen_dirs.add(skill_parent)
                    skills_dir_paths.append(skill_parent)
        
        if skills_dir_paths:
            logger.info(f"[启动] 📁 发现 {len(skills)} 个 skills:")
            for skill in skills:
                logger.info(f"[启动]   - {skill['name']}: {skill['path']}")
            logger.info(f"[启动] 📂 Skills 目录: {skills_dir_paths}")
        else:
            logger.info("[启动] ℹ️ 未发现任何 skills")
        
        init_agent_config(
            config=config,
            system_prompt=system_prompt,
            skills=skills_dir_paths,
        )
        agent_config = {"config": config}
        logger.info("[启动] ✅ Agent 配置加载成功")
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
app.include_router(user_router)
app.include_router(profile_router)
app.include_router(vector_store_router)


@app.get("/api/health", summary="健康检查", response_model=HealthResponse)
async def health_check():
    return HealthResponse(
        status="healthy",
        agent_initialized=agent_config is not None,
        database_initialized=db_instance is not None,
    )


@app.get("/api/config", summary="获取Agent配置")
async def get_config():
    from .service import get_agent_config
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
        "easy_agent.web.server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
