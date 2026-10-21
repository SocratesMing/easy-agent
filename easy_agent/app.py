"""FastAPI application entry point"""

import asyncio
import gzip
import logging
import mimetypes
import os
from easy_agent.bootstrap.env import env_bool
import platform
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from easy_agent.bootstrap.initialization import initialize_runtime

runtime_initialization = initialize_runtime()

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from easy_agent.middleware import get_current_username
from easy_agent.middleware.metrics import MetricsMiddleware
from easy_agent.middleware.rate_limit import RateLimitMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from langchain_core.messages import HumanMessage
from starlette.concurrency import run_in_threadpool

from easy_agent.bootstrap.config import AgentConfig
from easy_agent.db import init_database
from easy_agent.knowledge_impl import (
    KNOWLEDGE_IMPL,
    LegacyHostRoutes,
    KnowledgeOperationsRepository,
    audit_metadata,
    metrics,
    resolve_request_id,
    should_audit,
    shutdown_catalog_sync,
    shutdown_knowledge,
    startup_catalog_sync,
    startup_knowledge,
)
from easy_agent.knowledge_impl import knowledge_router
from easy_agent.knowledges.personnel import router as personnel_router
from easy_agent.agent.model import create_model
from easy_agent.models.api import HealthResponse
from easy_agent.utils.paths import PROJECT_ROOT
from easy_agent.services import (
    drain_stream_tasks,
    flush_all_stream_events,
    get_agent_config,
    init_agent_config,
)
from easy_agent.services import init_scheduler, shutdown_scheduler, reload_all_tasks
from easy_agent.services.prompt_loader import (
    configure_prompts_dir,
    get_prompts_dir,
    load_system_prompt,
)
from easy_agent.agent.skills import find_skills_root, discover_skills, validate_skills
from easy_agent.api import (
    chat_router,
    sessions_router,
    files_router,
    auth_router,
    completion_router,
    prompts_router,
    settings_router,
    skill_center_router,
    connectors_router,
    scheduled_tasks_router,
    expert_router,
)

# Web Terminal 依赖 pty（POSIX 专用），Windows 不支持，故不加载该模块
terminal_router = None
if platform.system() != "Windows":
    from easy_agent.api import terminal_router

logger = logging.getLogger(__name__)

# 前端静态目录：2026-10-10 起以 frontend-vue2/（开发基线）的构建产物为准
frontend_dist = str(PROJECT_ROOT / "frontend-vue2" / "dist")

agent_config = None
db_instance = None


async def _check_llm_connection(config, agent_env: str = "") -> None:
    """启动时探测 LLM 连通性；失败只告警，不阻断服务启动。

    生产/多副本滚动发布时跳过（避免每个 pod 真打一发模型）。
    设置 ``EASY_LLM_PROBE=1`` 可强制探测。
    """
    if not env_bool("EASY_LLM_PROBE") and (
        agent_env == "prod" or env_bool("EASY_SKIP_LLM_PROBE")
    ):
        logger.info("跳过启动 LLM 探测（生产/多副本默认关闭，设置 EASY_LLM_PROBE=1 可打开）")
        return
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


def _init_database_and_state(app, config) -> None:
    """初始化数据库，并挂到 app.state.db 与全局 db_instance。"""
    global db_instance

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


def _prepare_system_prompt(config, config_path) -> str:
    """加载提示词目录与系统提示词，返回最终 system_prompt。"""
    # 提示词统一由 prompt_loader 从 agent.prompt_path 指向的目录加载：
    # system.md + fragments/*.md，缺失时逐级回落到内置默认提示词。
    # 同时把该目录设为全局提示词目录，使记忆类提示词也从同一处读取。
    config_dir = os.path.dirname(os.path.abspath(config_path)) if config_path else None
    prompt_path = (
        getattr(config.agent, "prompt_path", "prompts") if config else "prompts"
    )
    configure_prompts_dir(prompt_path, base_dir=config_dir)
    system_prompt = load_system_prompt(prompt_path=prompt_path, config_dir=config_dir)
    logger.info(
        f"✅ 系统提示词加载完成 | 提示词目录: {get_prompts_dir()} "
        f"({len(system_prompt)} 字符)"
    )

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

    return system_prompt


def _start_scheduler() -> None:
    """启动定时任务调度器（AsyncIOScheduler）；失败只告警。"""
    # 定时任务调度器（AsyncIOScheduler）
    try:
        scheduler = init_scheduler()
        scheduler.start()
        reload_all_tasks()
        logger.info("✅ 定时任务调度器已启动 (AsyncIOScheduler)")
    except Exception as e:
        logger.warning(f"⚠️ 定时任务调度器启动失败: {e}")


def _shutdown_app(app) -> None:
    """关闭定时任务调度器与数据库连接。"""
    try:
        shutdown_scheduler()
        logger.info("[关闭] 定时任务调度器已关闭")
    except Exception as e:
        logger.warning(f"[关闭] 定时任务调度器关闭失败: {e}")

    if hasattr(app.state, "db") and app.state.db:
        app.state.db.close()

    logger.info("[关闭] 👋 服务已关闭")


def _log_startup_banner(agent_env: str, config_path) -> None:
    logger.info("=" * 60)
    logger.info("Easy Agent Web Service 初始化中...")
    logger.info(f"项目目录: {PROJECT_ROOT}")
    logger.info(f"操作系统: {platform.system()} {platform.release()}")
    logger.info(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    # 打印环境信息和配置文件
    logger.info("=" * 60)
    logger.info(f"AGENT_ENV: {agent_env or '(未设置, 默认 dev)'}")
    logger.info(f"配置文件: {config_path}")
    logger.info("=" * 60)


def _report_config(config, config_path, config_error, log_files) -> None:
    """记录配置加载结果；加载成功时创建配置里所有缺失的目录。"""
    if config_error:
        logger.error(
            f"❌ 配置文件加载失败，服务将以降级模式启动（聊天等功能不可用）: {config_error}"
        )
        logger.error(
            f"   请检查 {config_path} 是否存在，且其中的 ${{ENV_VAR}} 占位符都能在"
            "项目根 .env（或运行环境）中取到值；active model 也必须配置 api_key。"
        )
    if not config:
        logger.warning("⚠️ 配置未加载，后续将使用内置默认值（部分功能可能不可用）")
        return

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


async def _recover_stale_runs() -> None:
    """Phase 2B：DB 初始化完成、开始接流量前，恢复 Pod 崩溃/驱逐遗留的 stale run。

    只把 running 标记为 interrupted 并补一条 error 事件，避免前端一直“执行中”；
    不自动重新执行（执行器属于 Phase 2C）。任何异常只告警，不阻断启动。
    """
    try:
        from easy_agent.services.run_registry import recover_stale_runs

        recovered = await asyncio.to_thread(recover_stale_runs, db_instance)
        if recovered:
            logger.warning(
                f"♻️ 已恢复 {recovered} 个 stale run（标记 interrupted，未自动重跑）"
            )
        else:
            logger.info("♻️ 无 stale run 需要恢复")
    except Exception as exc:  # noqa: BLE001 - 恢复失败不阻断启动
        logger.warning(f"⚠️ stale run 恢复失败（忽略）: {exc}")


def _check_deployment(agent_env: str) -> None:
    """部署形态 fail-fast：生产多 worker/副本 + SQLite = 运行期随机丢消息/锁表，
    这里在启动期直接拒绝（用户决策：生产强制 MySQL）。"""
    from easy_agent.utils.deployment import (
        deployment_errors,
        deployment_warnings,
    )

    db_type = getattr(db_instance, "db_type", "unknown")
    for msg in deployment_warnings(db_type=db_type, agent_env=agent_env, workers=None):
        logger.warning("⚠️ 部署配置 | %s", msg)
    errors = deployment_errors(db_type=db_type, agent_env=agent_env, workers=None)
    if errors:
        for msg in errors:
            logger.error("❌ 部署配置错误 | %s", msg)
        raise RuntimeError("; ".join(errors))


def _discover_skills_root(config) -> str:
    """查找技能根目录并列出发现的技能，返回根目录（没有时返回空串）。

    FW-06：启动期校验 frontmatter，坏技能只告警不阻断启动。
    """
    skills_dir_config = (
        config.tools.skills_dir if hasattr(config.tools, "skills_dir") else None
    )
    skills_root = find_skills_root(skills_dir_config)
    if not skills_root:
        logger.info("ℹ️ 未发现任何 skills")
        return ""

    skills = discover_skills(skills_root)
    logger.info(f"📂 Skills 目录 (配置: {skills_dir_config}): {skills_root}")
    logger.info(f"📁 发现 {len(skills)} 个 skills:")
    for skill in skills:
        logger.info(f"  - {skill['name']}: {skill['path']}")
    for issue in validate_skills(skills_root):
        if issue["errors"]:
            logger.warning(
                "技能 frontmatter 校验失败 | %s | %s",
                issue["name"],
                "; ".join(issue["errors"]),
            )
    return skills_root


def _register_execute_profiles(config) -> None:
    """FW-04：execute 的 HITL 文案改由官方 HarnessProfile 注入（不再 monkeypatch
    FilesystemMiddleware）。启动期为配置中的每个模型注册一次；失败只报错，不阻断启动
    （首次建图前 agent.py 还会兜底重试）。"""
    from easy_agent.agent.hitl import register_execute_description_profiles

    profile_keys = register_execute_description_profiles(config)
    if profile_keys:
        logger.info(f"🧩 execute 描述 HarnessProfile 已注册 | keys={list(profile_keys)}")
    else:
        logger.error(
            "❌ execute 描述 HarnessProfile 注册失败，"
            "模型将看不到「删除类命令需人工审批」的提示"
        )


def _log_public_experts() -> None:
    """公共（预置）专家目录：把「解析到哪个目录、来源是谁」打出来，
    出问题时能一眼看出是配置写错还是目录没挂上。"""
    try:
        from easy_agent.services import public_expert_service

        public_experts = public_expert_service.discover_public_experts()
        if public_experts:
            logger.info(
                f"📂 公共专家目录: {public_expert_service.public_experts_root_hint()} | "
                f"数量: {len(public_experts)}"
            )
            for item in public_experts:
                logger.info(f"  - {item['export_name_cn']} ({item['export_agent_id']})")
        else:
            logger.warning(
                "⚠️ 未发现任何公共（预置）专家 | "
                f"解析目录: {public_expert_service.public_experts_root_hint()}"
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"⚠️ 公共专家目录扫描失败: {type(exc).__name__}: {exc}")


async def _start_checkpointing(agent_env: str) -> None:
    """FW-09：默认官方 checkpointer（auto：sqlite → legacy）。

    两种后端都不能跨副本共享，prod 多副本在启动期 fail-fast（可用
    EASY_ALLOW_LEGACY_CHECKPOINTER=1 显式豁免），配置错误不再被静默吞掉。
    """
    from easy_agent.persistence.checkpoints import (
        CheckpointDeploymentError,
        start_checkpoint_backend,
        start_checkpoint_gc,
    )

    try:
        checkpoint_backend = await start_checkpoint_backend(agent_env=agent_env)
        logger.info(f"✅ HITL checkpointer 后端: {checkpoint_backend}")
    except CheckpointDeploymentError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "HITL checkpointer 启动失败，回退业务库 MemorySaver 快照: "
            f"{type(exc).__name__}: {exc}"
        )
    # 删除会话时只有处理请求的进程能当场删 checkpoint；多副本下别的 pod 文件里的残留
    # 由各自的后台对账补删（官方 SQLite saver 激活时才启动，shutdown_checkpointers 负责停）
    start_checkpoint_gc()


async def _shutdown_services(app) -> None:
    """关闭顺序：停周期恢复 → 等流式任务结束 → 刷出事件 → 关 MCP、Store、checkpointer、知识库
    → 调度器与数据库。"""
    from easy_agent.services.run_registry import stop_run_recovery

    await stop_run_recovery()
    await drain_stream_tasks()
    try:
        # 流式任务已结束：把各会话写入器队列里剩余的事件 flush 到 stream_events，
        # 避免 shutdown 丢掉尾部 done/error（跨 Pod 重连仍能回放完整序列）。
        await flush_all_stream_events()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"flush SSE 事件写入器失败: {exc}")
    try:
        from easy_agent.services.mcp_sessions import aclose_mcp_sessions

        await aclose_mcp_sessions()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"关闭 MCP 持久 session 失败: {exc}")
    from easy_agent.persistence.stores import reset_store

    reset_store()
    try:
        from easy_agent.persistence.checkpoints import shutdown_checkpointers

        await shutdown_checkpointers()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"关闭 checkpointer 失败: {exc}")
    # 没启动过 worker / 客户端时是空操作
    await shutdown_knowledge(app)
    await shutdown_catalog_sync(app)
    _shutdown_app(app)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent_config

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    os.chdir(PROJECT_ROOT)

    agent_env = runtime_initialization.environment.agent_env

    # B3：生产环境 JWT 密钥 fail-fast。必须在启动期（而非模块 import 期）校验，
    # 避免 pytest / 工具脚本仅 import 就失败；dev/test 保留 data/.jwt_secret 回退。
    from easy_agent.utils.auth import validate_production_jwt_secret

    validate_production_jwt_secret(env=agent_env)

    # Phase 0：严格多副本门禁。默认关闭；Phase 2/3 完成前开启会明确失败，
    # 避免在 SSE/run/file/sandbox 尚未外置时误宣称多副本安全。
    from easy_agent.bootstrap.runtime_features import check_multi_replica_startup

    check_multi_replica_startup(agent_env)

    # 两道 fail-fast 之后才读配置
    config_path = runtime_initialization.config_path
    config = runtime_initialization.config
    _log_startup_banner(agent_env, config_path)
    _report_config(
        config,
        config_path,
        runtime_initialization.config_error,
        runtime_initialization.log_files,
    )

    # 知识库：配置里没有 knowledge 段时按默认配置（未启用）加载，不起 worker
    await startup_knowledge(app, config_path)
    logger.info(f"📚 知识库实现: {KNOWLEDGE_IMPL}")

    if config:
        await _check_llm_connection(config, agent_env=agent_env)

    _init_database_and_state(app, config)
    await _recover_stale_runs()
    _check_deployment(agent_env)

    # 外部目录树同步（ai_kb → 知识库板块）：由 knowledge.catalog_sync.enabled
    # 开关控制，后台执行，失败仅记日志不阻断启动
    await startup_catalog_sync(app)

    system_prompt = _prepare_system_prompt(config, config_path)

    if config:
        skills_root = _discover_skills_root(config)
        # MCP 不在启动时全局加载，改为按用户配置动态加载（见 agent_manager）
        logger.info("ℹ️ MCP 将按用户配置动态加载（不在启动时预加载）")

        init_agent_config(
            config=config,
            system_prompt=system_prompt,
            skills_root=skills_root,
            agent_env=agent_env if agent_env in ("dev", "test", "prod") else "",
        )
        agent_config = {"config": config}
        logger.info("✅ Agent 配置加载成功")

        _register_execute_profiles(config)
        _log_public_experts()
        await _start_checkpointing(agent_env)

        # LangGraph Store 是业务库 SqlStore，首次使用时绑定业务库；先丢掉同一进程里
        # 之前缓存的实例（测试会在一个进程里换着业务库多次启动应用）
        from easy_agent.persistence.stores import reset_store

        reset_store()

        # 对象存储 A：EASY_UPLOAD_BACKEND=store 时上传原始文件必须落 LangGraph
        # Store，这里做启动期 fail-closed 自检（local 模式为空操作）。宁可启动
        # 失败，也不要跑起来后每次上传都 503。
        from easy_agent.services.upload_storage import preflight_upload_store

        preflight_upload_store()

        _start_scheduler()
    else:
        logger.warning("⚠️ Agent 配置未加载")

    # 新 pod 往往在旧 pod 的 run 租约过期前就起来了，启动时那一轮扫不到，之后定期再扫。
    # 放在所有 fail-fast 检查之后：启动失败时不留下没人收尾的后台任务
    from easy_agent.services.run_registry import start_run_recovery

    start_run_recovery()

    logger.info("=" * 60)
    logger.info("🚀 Easy Agent Web Service 启动完成")
    logger.info("=" * 60)
    yield

    await _shutdown_services(app)


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
_cors_origins = runtime_initialization.environment.cors_allow_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(MetricsMiddleware)


def _get_client_ip(request: Request) -> str:
    from easy_agent.utils.client_ip import get_client_ip as _ip

    return _ip(request)


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
    request_id = resolve_request_id(request.headers.get("X-Request-Id"))
    request.state.request_id = request_id
    path = request.url.path
    if (
        path in ("/", "/health", "/api/health", "/agent/health", "/api/live", "/agent/live", "/api/ready", "/agent/ready")
        or path.startswith(_ACCESS_SKIP_PREFIXES)
        or path.endswith(_ACCESS_SKIP_SUFFIXES)
    ):
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    client_ip = _get_client_ip(request)
    started = time.monotonic()
    status_code = 500
    logger.info("[请求] %s %s | IP: %s | request_id=%s", request.method, path, client_ip, request_id)
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-Id"] = request_id
        return response
    finally:
        duration = time.monotonic() - started
        if path.startswith(("/api/knowledge/", "/api/personnel")):
            metrics.observe(request.method, path, status_code, duration)
        if should_audit(path, request.method):
            db = getattr(request.app.state, "db", None)
            config = getattr(request.app.state, "knowledge_config", None)
            if db is not None and (config is None or config.audit.enabled):
                metadata = audit_metadata(path, request.method, status_code, duration)
                try:
                    await asyncio.to_thread(
                        KnowledgeOperationsRepository(db).record_audit,
                        request_id=request_id,
                        actor_user_id=str(getattr(request.state, "actor_user_id", "anonymous")),
                        actor_username=str(getattr(request.state, "actor_username", "anonymous")),
                        max_details_bytes=getattr(getattr(config, "audit", None), "max_details_bytes", 4096),
                        **metadata,
                    )
                except Exception:
                    logger.exception("审计记录写入失败 | request_id=%s", request_id)


# knowledge 可观测性中间件（additive）：只新增 request_id、X-Request-Id 响应头与
# 日志；不修改响应体/状态码。仅当 knowledge 已启用且 audit.enabled 时，对
# /agent/knowledge 请求写审计记录，写入失败只告警。
@app.middleware("http")
async def knowledge_observability(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id", "").strip()
    if (
        not request_id
        or len(request_id) > 128
        or "\r" in request_id
        or "\n" in request_id
    ):
        request_id = str(uuid.uuid4())
    request.state.request_id = request_id

    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000

    response.headers["X-Request-Id"] = request_id
    logger.info(
        f"[响应] {request.method} {request.url.path} -> {response.status_code} "
        f"| {duration_ms:.1f}ms | request_id={request_id}"
    )

    knowledge_config = getattr(request.app.state, "knowledge_config", None)
    if (
        knowledge_config is not None
        and knowledge_config.enabled
        and knowledge_config.audit.enabled
        # 复用模块自身策略：只审计写入路径，跳过端点已单独审计的读路径
        # （should_audit 排除 /capabilities、/status、/health、/metrics 等）。
        and should_audit(request.url.path, request.method)
    ):
        try:
            db = getattr(request.app.state, "db", None)
            if db is not None:
                await run_in_threadpool(
                    KnowledgeOperationsRepository(db).record_audit,
                    request_id=request_id,
                    actor_user_id=str(
                        getattr(request.state, "actor_user_id", "") or "anonymous"
                    ),
                    actor_username=str(
                        getattr(request.state, "actor_username", "") or "anonymous"
                    ),
                    **audit_metadata(
                        request.url.path,
                        request.method,
                        response.status_code,
                        duration_ms / 1000,
                    ),
                    max_details_bytes=knowledge_config.audit.max_details_bytes,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"知识库审计写入失败: {exc}")

    return response

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """统一 HTTP 错误体：保留 detail（前端现有契约）+ 稳定 code。"""
    detail = exc.detail
    if isinstance(detail, (str, int, float, bool)) or detail is None:
        payload = {"detail": detail or "请求失败"}
    else:
        payload = {"detail": detail}
    payload["code"] = f"HTTP_{exc.status_code}"
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        payload["request_id"] = request_id
    headers = dict(exc.headers or {})
    if request_id:
        headers.setdefault("X-Request-Id", str(request_id))
    return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(payload), headers=headers)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """统一 422 校验错误体，避免把 Pydantic 内部对象直接返回给前端。"""
    request_id = getattr(request.state, "request_id", None)
    payload = {
        "detail": "请求参数校验失败",
        "code": "VALIDATION_ERROR",
        "errors": jsonable_encoder(exc.errors()),
    }
    if request_id:
        payload["request_id"] = request_id
    return JSONResponse(status_code=422, content=payload)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = str(getattr(request.state, "request_id", "unknown"))
    logger.error("全局异常 | request_id=%s", request_id, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务内部错误", "request_id": request_id},
        headers={"X-Request-Id": request_id},
    )


app.include_router(chat_router)
app.add_middleware(LegacyHostRoutes)
app.include_router(sessions_router)
app.include_router(files_router)
app.include_router(auth_router)
app.include_router(completion_router)
app.include_router(prompts_router)
app.include_router(settings_router)
app.include_router(skill_center_router)
app.include_router(connectors_router)
if terminal_router is not None:
    app.include_router(terminal_router)
app.include_router(scheduled_tasks_router)
app.include_router(knowledge_router)
app.include_router(personnel_router)
app.include_router(expert_router)


@app.get("/agent/live", summary="存活检查（liveness）", response_model=HealthResponse)
@app.get("/api/live", summary="存活检查（liveness）", response_model=HealthResponse)
async def liveness():
    return HealthResponse(status="live")


@app.get("/agent/health", summary="健康检查", response_model=HealthResponse)
async def health_check():
    return await liveness()


async def _readiness_payload() -> HealthResponse:
    from easy_agent.services.health import ping_database, workspace_writable

    db_ok, db_detail = ping_database(db_instance)
    workspace_dir = "./workspace"
    try:
        cfg = get_agent_config()
        if cfg and cfg.get("config"):
            workspace_dir = cfg["config"].agent.workspace_dir
    except Exception:
        pass
    ws_ok, ws_detail = workspace_writable(workspace_dir)
    ready = db_ok and ws_ok and agent_config is not None
    detail = ""
    if not db_ok:
        detail = f"database: {db_detail}"
    elif not ws_ok:
        detail = f"workspace: {ws_detail}"
    elif agent_config is None:
        detail = "agent not initialized"
    return HealthResponse(
        status="ready" if ready else "not_ready",
        agent_initialized=agent_config is not None,
        database_initialized=db_instance is not None,
        database_ready=db_ok,
        workspace_ready=ws_ok,
        detail=detail,
    )


@app.get("/agent/ready", summary="就绪检查（readiness）", response_model=HealthResponse)
@app.get("/api/ready", summary="就绪检查（readiness）", response_model=HealthResponse)
async def readiness():
    payload = await _readiness_payload()
    if payload.status != "ready":
        return JSONResponse(status_code=503, content=payload.model_dump())
    return payload


@app.get("/agent/metrics", summary="运行指标（RED，仅管理员）")
async def runtime_metrics(
    username: str = Depends(get_current_username),
):
    """进程内 RED 指标 JSON 快照；仅 admin 可读。

    指标是进程内聚合的，多 worker/多副本需逐实例抓取后由外部汇总。
    """
    if username != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可以查看运行指标")
    from easy_agent.services.metrics import snapshot_metrics

    return JSONResponse(content=snapshot_metrics())


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



@app.get("/api/health", summary="健康检查", response_model=HealthResponse)
async def api_health_check():
    return await health_check()


@app.get("/api/config", summary="获取Agent配置")
async def api_get_config():
    return await get_config()


@app.get("/", response_class=FileResponse)
async def serve_frontend():
    index_html = os.path.join(frontend_dist, "index.html")
    if os.path.exists(index_html):
        # 入口 HTML 禁止缓存：打包产物文件名固定（/js/app.js），旧缓存会指向
        # 已下线的接口/入口，出现 405、旧界面等问题。
        return FileResponse(
            index_html,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    return FileResponse(os.path.join(frontend_dist, "index.html"))


def _is_within_dir(root: str, path: str) -> bool:
    """``path`` 是否在 ``root`` 目录内（两者都应是 realpath）；跨盘符视为不在。"""
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:
        return False


# ── 静态资源 gzip ────────────────────────────────────────────────────
# 构建产物 27MB（monaco 单 chunk 12MB），后端直接托管 dist 时若不压缩，
# 首屏要拉几十 MB。这里按扩展名在路由内压缩，而不是挂全局 GZipMiddleware：
# 全局中间件会把 text/event-stream 攒包，SSE 会变成「一次性到达」
# （2026-10-15 在 webpack-dev-server 上踩过同样的坑）。
_STATIC_GZIP_EXTS = {".js", ".mjs", ".css", ".svg", ".json", ".woff", ".woff2", ".ttf"}
_STATIC_GZIP_MIN_BYTES = 1024
_STATIC_GZIP_CACHE_MAX = 64
_static_gzip_cache: dict[str, tuple[int, bytes]] = {}


def _accepts_gzip(request: Request) -> bool:
    encoding = (request.headers.get("accept-encoding") or "").lower()
    return "gzip" in encoding or "*" in encoding


def _gzip_static_file(path: str) -> bytes | None:
    """读取并压缩静态文件；按 (路径, mtime) 缓存，避免每次请求重复压缩。"""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    cached = _static_gzip_cache.get(path)
    if cached and cached[0] == stat.st_mtime_ns:
        return cached[1]
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) < _STATIC_GZIP_MIN_BYTES:
        return None
    body = gzip.compress(raw, compresslevel=6)
    if len(_static_gzip_cache) >= _STATIC_GZIP_CACHE_MAX:
        _static_gzip_cache.clear()
    _static_gzip_cache[path] = (stat.st_mtime_ns, body)
    return body


@app.get("/{full_path:path}", response_class=FileResponse)
async def serve_static(full_path: str, request: Request):
    # /agent/* 是接口命名空间，未匹配到任何路由时必须返回 JSON 404。
    # 否则会落到下面的 SPA 兜底、以 200 + text/html 返回 index.html，
    # 前端 response.json() 报 "Unexpected token '<'"，把「接口不存在
    # （后端没重启 / 版本过旧）」这个真实原因完全掩盖掉。
    if full_path == "agent" or full_path.startswith("agent/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    # SEC-02：先解析再校验必须落在构建目录内。os.path.join 遇到绝对路径会丢掉
    # 前缀（``GET //etc/passwd``），``%2e%2e/`` 也能逃出目录。
    dist_root = os.path.realpath(frontend_dist)
    static_file = os.path.realpath(os.path.join(dist_root, full_path))
    if not _is_within_dir(dist_root, static_file):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if os.path.isfile(static_file):
        # 静态产物文件名固定（/js/app.js）：no-cache 禁止启发式强缓存，
        # 强制走协商缓存（ETag/Last-Modified 变了即拉新），新版本部署后普通刷新即可生效。
        if (
            os.path.splitext(static_file)[1].lower() in _STATIC_GZIP_EXTS
            and _accepts_gzip(request)
        ):
            compressed = await run_in_threadpool(_gzip_static_file, static_file)
            if compressed is not None:
                etag = f'W/"{os.stat(static_file).st_mtime_ns}-{len(compressed)}"'
                if request.headers.get("if-none-match") == etag:
                    return Response(
                        status_code=304,
                        headers={"ETag": etag, "Vary": "Accept-Encoding", "Cache-Control": "no-cache"},
                    )
                media_type = mimetypes.guess_type(static_file)[0] or "application/octet-stream"
                return Response(
                    content=compressed,
                    media_type=media_type,
                    headers={
                        "Content-Encoding": "gzip",
                        "Vary": "Accept-Encoding",
                        "Cache-Control": "no-cache",
                        "ETag": etag,
                    },
                )
        return FileResponse(static_file, headers={"Cache-Control": "no-cache"})

    index_html = os.path.join(frontend_dist, "index.html")
    if os.path.exists(index_html):
        return FileResponse(
            index_html,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    return JSONResponse({"error": "Not found"}, status_code=404)
