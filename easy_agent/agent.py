"""Core Agent implementation using DeepAgents framework

DeepAgents provides built-in tools automatically:
- write_todos: task planning
- ls, read_file, write_file, edit_file, glob, grep: filesystem operations
- execute: shell command execution
- task: subagent spawning

No need to declare these tools manually.
"""

import json
import logging
import platform
import shutil
import time
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, LocalShellBackend
from deepagents.middleware.filesystem import FilesystemOperation, FilesystemPermission
from langchain_core.messages import AIMessageChunk, ToolMessage

from .backends.shell import _PathTranslatingShell
from .backends.context import ContextBackend
from .checkpoints import get_session_checkpointer, release_session_checkpointer
from .config import Config
from .fs_safety import _check_external_dir_safety
from .hitl import (
    EXECUTE_HITL_TOOL_DESCRIPTION,
    HITL_SYSTEM_NOTE,
    HITL_WHEN_PREDICATES,
    build_interrupt_config,
    execute_description_installed,
    register_execute_description_profiles,
)
from .stores import get_agent_store
from .agent_graph_cache import (
    SessionContext,
    build_graph_key,
    config_fingerprint,
    contextualize_tools,
    get_agent_graph_cache,
    graph_cache_enabled,
    model_fingerprint,
    summarization_fingerprint,
)
from .subagents import DEFAULT_SUBAGENTS
from .summarization import configured_summarization
from .logger import AgentLogger
from .model import create_model, extract_reasoning, resolve_context_length
from .prompt_guard import assemble_system_prompt
from .tools.web_search import WEB_SEARCH_SYSTEM_PROMPT
from .utils.prompt_probe import describe_prompt, prompt_fingerprint


logger = logging.getLogger(__name__)


# 长期记忆（跨会话 AGENTS.md）在虚拟文件系统中的挂载点：
# _build_backend 把 memories/{username}/ 挂成 /memories/ 路由，
# create_deep_agent(memory=[...]) 再经该虚拟路径加载（MemoryMiddleware 读 backend）。
LONG_TERM_MEMORY_VIRTUAL_PATH = "/memories/AGENTS.md"


def _deepagents_version() -> str:
    """当前 deepagents 版本（适配失效时的排查提示用）。"""
    try:
        from importlib.metadata import version

        return version("deepagents")
    except Exception:  # noqa: BLE001
        return "unknown"


def framework_patch_status() -> dict[str, bool]:
    """自检 execute 描述是否已通过官方 ``HarnessProfile`` 注册。

    FW-04 起不再 monkeypatch ``FilesystemMiddleware.__init__``；这里反映的是
    ``hitl.register_execute_description_profiles`` 是否已在当前进程生效。
    """
    return {"execute_tool": execute_description_installed()}


#: 注册表 ``hitl_predicate`` 名称 → 实际 ``when`` 谓词。
#: 谓词实现与绑定都在 ``hitl``（FW-12）；这里保留别名供既有自检/测试导入，
#: 两边不一致时启动期日志会报错（测试也会失败）。
_HITL_WHEN_PREDICATES = HITL_WHEN_PREDICATES


_patch_status_logged = False


def log_framework_patch_status(sid: str = "startup") -> None:
    """只打印一次框架适配体检结果；任一项失效按 ERROR 报出并说明降级后果。"""
    global _patch_status_logged
    if _patch_status_logged:
        return
    _patch_status_logged = True

    status = framework_patch_status()
    detail = " | ".join(f"{k}={'生效' if v else '失效'}" for k, v in status.items())
    if all(status.values()):
        logger.info(f"[{sid}] 🔧 deepagents {_deepagents_version()} 适配体检: {detail}")
        return

    failed = [k for k, v in status.items() if not v]
    logger.error(
        f"[{sid}] ❌ deepagents 适配失效: {failed} | {detail} | 降级后果——"
        f"execute_tool=execute 描述未通过 HarnessProfile.tool_description_overrides "
        f"注册（hitl.register_execute_description_profiles）。"
        f"当前 deepagents {_deepagents_version()}，请对照其 profiles API 修正适配代码。"
    )


def build_subagent_specs(
    session_id: str = "", result_log_truncate: int = 500
) -> list[dict]:
    """构造传给 create_deep_agent 的 subagent specs。

    官方语义：自定义 subagent **不继承**主 agent 的 skills，且 middleware 栈
    也不会自动带上用户的 LoggingMiddleware。这里显式补上：
    - skills=["/user-skills/"]：office-writer / knowledge-researcher 能读技能文档；
    - middleware=[LoggingMiddleware(...)]：子代理内工具异常不会直接中断 task。

    D2：``session_id`` 不再写进 spec（否则同一张编译图会被会话绑定、无法复用）。
    LoggingMiddleware 只做工具异常兜底，日志前缀使用通用 ``agent``；保留参数
    仅为兼容既有调用方。
    """
    from .services.logging_middleware import LoggingMiddleware

    specs: list[dict] = []
    for base in DEFAULT_SUBAGENTS:
        item = dict(base)
        item.setdefault("skills", ["/user-skills/"])
        item["middleware"] = [
            LoggingMiddleware(result_log_truncate=result_log_truncate)
        ]
        specs.append(item)
    return specs


def _shared_graph_checkpointer():
    """返回可跨会话共享的 checkpointer；legacy 按 session 的 saver 返回 None。

    D2 只复用「官方全局 saver」：``AsyncPostgresSaver`` / ``AsyncSqliteSaver``
    由进程共享，thread_id 已含 session 维度。MySQL 无官方 saver 时 checkpoints.py
    会按 session 创建 ``PersistentMemorySaver``，该对象不能塞进共享图，此时
    由调用方显式降级为按会话建图。
    """
    from .checkpoints import postgres_checkpointer_active, sqlite_checkpointer_active

    if not (postgres_checkpointer_active() or sqlite_checkpointer_active()):
        return None
    return get_session_checkpointer("__graph_cache__")


def _build_backend_for_context(
    config: Config,
    context: SessionContext,
    skills_paths: list[str],
    *,
    safe_external: dict[str, str] | None = None,
    sid: str = "",
) -> CompositeBackend:
    """按运行期 ``SessionContext`` 构建 CompositeBackend（D2 共享图使用）。

    与旧 ``EasyAgent._build_backend`` 的路由/权限语义逐项一致，唯一区别是
    workspace / user-skills / memories 路径来自 context，而不是编译时的
    ``EasyAgent`` 实例。backend callable 每次请求都会调用它，因此同一张图可
    服务多个 session。
    """
    workspace_virtual_path = context.workspace_virtual_path or "/workspace"
    workspace_dir = Path(context.workspace_dir)
    user_skills_dir = Path(context.user_skills_dir)
    long_term_memory_file = Path(context.long_term_memory_file)
    shell_env = dict(getattr(context, "shell_env", {}) or {})
    sid = sid or getattr(context, "sid", "") or "graph"

    if safe_external is None:
        external_dirs = config.agent.external_dirs or {}
        workspace_root = Path(config.agent.workspace_dir)
        safe_external = {}
        for vpath, real_path in external_dirs.items():
            real = Path(real_path)
            ok, reason = _check_external_dir_safety(
                real, workspace_root, context.safe_username
            )
            if not ok:
                logger.warning(
                    f"[{sid}] 🚫 拒绝挂载外部目录 {vpath} -> {real_path}：{reason}。"
                    f"该挂载已跳过，以防止越权访问其他用户的会话数据。"
                )
                continue
            vp = vpath if vpath.endswith("/") else vpath + "/"
            safe_external[vp] = str(real.absolute())

    workspace_backend = LocalShellBackend(
        root_dir=str(workspace_dir.absolute()),
        virtual_mode=True,
        env=shell_env,
    )
    routes = {f"{workspace_virtual_path}/": workspace_backend}

    # 长期记忆（跨会话 AGENTS.md）挂载为 /memories/ 路由，供
    # create_deep_agent(memory=[LONG_TERM_MEMORY_VIRTUAL_PATH]) 经虚拟路径读取。
    # D2 记忆源固定（文件不存在时 MemoryMiddleware 自动跳过），因此路由总是挂载，
    # 避免「首轮无记忆 / 二轮有记忆」改变编译图结构。
    try:
        long_term_memory_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"[{sid}] 创建长期记忆目录失败: {e}")
    routes["/memories/"] = LocalShellBackend(
        root_dir=str(long_term_memory_file.parent.absolute()),
        virtual_mode=True,
        env=shell_env,
    )

    # 技能库路由总是挂载（即使当前一个技能都没有）：否则模型没有可写路径来创建
    # 第一个技能。目录不存在时先建出来，保证 route 与 path_mappings 保持一致。
    try:
        user_skills_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"[{sid}] 创建用户技能目录失败: {e}")
    if skills_paths or user_skills_dir.exists():
        routes["/user-skills/"] = LocalShellBackend(
            root_dir=str(user_skills_dir.absolute()),
            virtual_mode=True,
            env=shell_env,
        )

    # 挂载外部目录（skill 需要访问的宿主机目录）为虚拟路径路由。
    # 仅挂载通过安全校验的目录（safe_external），防止把工作区根或其他用户
    # 子树挂载出来导致越权访问其他用户的会话数据。
    for vp, real_abs in (safe_external or {}).items():
        real = Path(real_abs)
        real.mkdir(parents=True, exist_ok=True)
        routes[vp] = LocalShellBackend(
            root_dir=str(real.absolute()),
            virtual_mode=True,
            env=shell_env,
        )

    logger.info(
        f"[{sid}] 🗺️ CompositeBackend routes:\n"
        + "\n".join(f"    {vp:40s} → {b.cwd}" for vp, b in sorted(routes.items()))
    )

    ws_real = workspace_dir.absolute()
    ws_real.mkdir(parents=True, exist_ok=True)
    ws_str = str(ws_real)

    path_mappings = {f"{workspace_virtual_path}/": ws_str + "/"}
    if "/user-skills/" in routes:
        path_mappings["/user-skills/"] = str(user_skills_dir.absolute()) + "/"
    for vp, real_abs in (safe_external or {}).items():
        path_mappings[vp] = real_abs + "/"

    return CompositeBackend(
        default=_PathTranslatingShell(
            path_mappings=path_mappings,
            root_dir=ws_str,
            virtual_mode=True,
            inherit_env=True,
            timeout=120,
            sandbox_enabled=config.agent.sandbox_enabled,
        ),
        routes=routes,
    )


class EasyAgent:
    """EasyAgent wraps DeepAgents with per-user/per-session workspace isolation.

    EXECUTE_HITL_TOOL_DESCRIPTION is the public execute-tool copy injected via
    the official ``HarnessProfile.tool_description_overrides`` (FW-04).

    Args:
        config: Application configuration.
        system_prompt: Base project prompt, augmented only with runtime context.
        skills_root: Path to the skills root directory. Skills are discovered automatically.
        username: Username for workspace isolation.
        session_id: Session ID for workspace isolation.
        workspace_dir: Override workspace directory. If provided, this takes
            priority over the auto-generated path from config + username + session_id.
        workspace_name: Custom workspace directory name (e.g. '20260501_143022_a1b2c').
            If provided, used instead of session_id as the directory name.
    """

    EXECUTE_HITL_TOOL_DESCRIPTION = EXECUTE_HITL_TOOL_DESCRIPTION

    def __init__(
        self,
        config: Config,
        system_prompt: str,
        skills_root: str = "",
        username: str = "default",
        session_id: str = None,
        workspace_dir: str | Path | None = None,
        workspace_name: str = "",
        mcp_tools: list | None = None,
        organization_id: str = "",
       enable_hitl: bool = True,
       model_name: str | None = None,
        enable_web_search: bool = False,
        system_prompt_extra: str = "",
   ):
        """初始化 EasyAgent 实例，配置工作区隔离和运行时上下文。

        根据用户名和会话 ID 构建隔离的工作区目录，设置记忆文件路径，
        拼接 DeepAgents 无法自动得知的运行时上下文，最终调用
        _create_agent() 创建 DeepAgents 智能体实例。

        Args:
            config: 应用配置对象，包含 LLM、agent、tools 等配置。
            system_prompt: 基础系统提示词，将追加用户、机构、工作区与系统信息。
            skills_root: 公共技能根目录路径，当前版本仅用于参考，不自动加载。
            username: 用户名，用于工作区路径隔离和记忆文件定位。
            session_id: 会话 ID，用作工作区子目录名。
            workspace_dir: 自定义工作区目录路径。提供时优先使用，覆盖自动生成的路径。
            workspace_name: 自定义工作区目录名（如 '20260501_143022_a1b2c'）。
                提供时替代 session_id 作为目录名。
            mcp_tools: MCP 工具列表，作为额外工具注入智能体。
            organization_id: 用户所属机构ID，注册后不可更改，将注入系统提示词。
            enable_web_search: 是否开启联网搜索。为 True 时在系统提示词中加入
                联网搜索行为约束（web_search 工具本身由 agent_manager 注入，
                仅在 web_search 配置了 api_key 时才为 True）。
        """
        self.config = config
        self.username = username
        self.session_id = session_id
        # 日志用的短前缀（取 sid 末 5 位），与 chat_service 等模块保持一致。
        self.sid = (session_id or "new")[-5:]
        self.skills_root = skills_root
        self.mcp_tools = mcp_tools or []
        self.organization_id = organization_id or ""
        self.enable_hitl = enable_hitl
        self.enable_web_search = enable_web_search
        self.safe_username = Config.sanitize_username(username)
        # 注入给 shell 执行环境的变量（LocalShellBackend 默认 inherit_env=False，
        # 环境为空 dict，这里只追加用户名，不继承宿主环境以免泄露密钥等变量）
        self.shell_env = {"EASY_USERNAME": self.safe_username}
        self.model_name = model_name or config.active_model
        # D2：workspace_name 进运行期 context（知识库/工具按它解析会话目录）
        self.workspace_name = ""

        if workspace_dir:
            self.workspace_dir = Path(workspace_dir)
            dir_name = self.workspace_dir.name
        else:
            base = Path(config.agent.workspace_dir)
            if session_id:
                dir_name = workspace_name if workspace_name else session_id
                self.workspace_dir = base / self.safe_username / "session" / dir_name
            else:
                self.workspace_dir = base / self.safe_username
                dir_name = self.safe_username

        # Don't create directory eagerly - LocalShellBackend.write will create it on first write
        self.workspace_virtual_path = "/workspace"
        self._workspace_renamed = False

        # 会话级记忆文件：workspace/{username}/session/{workspace_name}/memory.md
        # 首轮对话时不创建，第一轮回复完成后自动生成
        # 第二轮及后续对话时自动加载，提供当前会话的上下文连续性
        self.memory_file = self.workspace_dir / "memory.md"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

        # 用户长期记忆文件：memories/{username}/AGENTS.md
        # 跨会话持久化，记录用户偏好与可复用经验，首轮对话时加载
        memories_base = Path(config.agent.memories_dir)
        self.long_term_memory_file = memories_base / self.safe_username / "AGENTS.md"

        # 用户技能目录: workspace/{username}/skills/（用户级，非 session 级）
        self.user_skills_dir = Path(config.agent.workspace_dir) / self.safe_username / "skills"
        # D2：技能目录是否存在于「建图时」会影响 permissions 指纹；先建出来，
        # 避免首个请求调用 backend factory 后才创建，导致同一用户第二次建图键变化。
        try:
            self.user_skills_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # noqa: BLE001
            logger.warning(f"[{self.sid}] 创建用户技能目录失败: {exc}")
        # 目录名即会话工作区的真实名字（rename_workspace 会更新它）
        self.workspace_name = dir_name

        # D2：会话差异集中到运行期 context；编译图缓存键不包含这些字段。
        self.session_context = self._make_session_context()

        user_info = (
            f"## 当前用户\n"
            f"- 用户名: `{self.safe_username}`（技能文档与脚本中的 `{{userId}}` / `{{username}}` 均指它）\n"
            + (f"- 机构ID: `{self.organization_id}`（注册后不可更改）\n" if self.organization_id else "")
            + f"- 工作区: `{self.workspace_virtual_path}/`\n"
            + f"{self._get_os_info()}\n"
        )

        # 组装顺序由 prompt_guard 统一保证：
        # 基础规则 → 运行时上下文 → 联网附加 → 从属补充内容 → HITL 说明（最后）。
        self.system_prompt = assemble_system_prompt(
            base_prompt=system_prompt,
            user_info=user_info,
            web_search_prompt=WEB_SEARCH_SYSTEM_PROMPT
            if self.enable_web_search
            else "",
            extra=system_prompt_extra,
            hitl_note=HITL_SYSTEM_NOTE if self.enable_hitl else "",
        )

        # ── 系统提示词可观测日志（专家 Agent 提示词链路校验，可观测点 ③） ──
        # 记录终稿的「总字符数 + 指纹 + 各分段字符数」，用于验证：
        #   · 专家提示词确实进了模型终稿（extra 非空且语法长度吻合）；
        #   · 指纹能与 api/expert.py / agent_manager.py 记录的指纹串成一条链；
        #   · 未挂专家（通用 Agent）时明确显示 extra 为空，避免误判。
        logger.info(
            f"[{self.sid}] 🧾 系统提示词已组装 | 终稿={len(self.system_prompt)} 字符 | "
            f"指纹={prompt_fingerprint(self.system_prompt)} | "
            f"基础={len(system_prompt.strip())} 字符 | "
            f"联网搜索附加={len(WEB_SEARCH_SYSTEM_PROMPT.strip()) if self.enable_web_search else 0} 字符 | "
            f"动态附加={len(system_prompt_extra.strip())} 字符"
        )
        if system_prompt_extra.strip():
            logger.info(
                f"[{self.sid}] ✅ 动态附加内容已注入系统提示词 | "
                f"{describe_prompt('extra（含专家提示词）', system_prompt_extra)}"
            )
        else:
            logger.info(
                f"[{self.sid}] ➖ 无动态附加内容（通用 Agent，未注入专家提示词）"
            )

        self.max_steps = config.agent.max_steps
        self.logger = AgentLogger()
        self.agent_graph_key: str | None = None
        self.agent = self._create_agent()

        logger.info(
            f"EasyAgent initialized | workspace: {self.workspace_dir.absolute()} | user: {username} | session: {session_id}"
        )

    def rename_workspace(self, new_name: str) -> bool:
        """Rename workspace directory after streaming completes.

        Moves the directory and rebuilds the agent with the new path.
        Should only be called AFTER streaming completes to avoid
        breaking in-progress tool calls.
        """
        parent = self.workspace_dir.parent
        new_workspace = parent / new_name

        if new_workspace.resolve() == self.workspace_dir.resolve():
            return False

        if new_workspace.exists():
            for item in self.workspace_dir.iterdir():
                dest = new_workspace / item.name
                if dest.exists():
                    if item.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                shutil.move(str(item), str(dest))
            self.workspace_dir.rmdir()
        else:
            self.workspace_dir.rename(new_workspace)

        old_path = str(self.workspace_dir.absolute())
        self.workspace_dir = new_workspace
        # 扁平化后虚拟路径恒为 /workspace，重命名只改真实目录，虚拟路径不变。

        # 记忆文件基于 workspace_dir 推导，重命名后必须同步更新，
        # 否则 update_memory_after_session 会写回旧目录（且 mkdir 会重建旧目录）。
        self.memory_file = self.workspace_dir / "memory.md"
        self.workspace_name = new_workspace.name
        self.session_context = self._make_session_context()

        self.system_prompt = self.system_prompt.replace(
            old_path, str(new_workspace.absolute())
        )

        self.agent = self._create_agent()

        logger.info(f"Workspace renamed | {old_path} -> {new_workspace.absolute()}")
        return True

    def _load_memory_file(self, memory_path: Path, label: str) -> str:
        """读取记忆文件内容（不存在或读取失败时返回空字符串）。

        Args:
            memory_path: 记忆文件路径。
            label: 日志中使用的记忆名称（如 "会话记忆"/"长期记忆"）。
        """
        if not memory_path or not memory_path.exists():
            return ""
        try:
            content = memory_path.read_text(encoding="utf-8").strip()
            if content:
                logger.info(
                    f"[{self.session_id[-5:] if self.session_id else '?'}] "
                    f"📥 加载{label} | 文件: {memory_path} | 长度: {len(content)} 字符"
                )
            return content
        except Exception as e:
            logger.warning(f"加载{label}失败: {e}")
        return ""

    def load_session_memory(self) -> str:
        """加载当前会话的记忆文件（workspace/.../memory.md）。

        在后续用户输入时调用，将历史会话记忆作为上下文注入。
        若文件不存在（如首轮对话），返回空字符串。
        """
        return self._load_memory_file(self.memory_file, "会话记忆")

    def load_long_term_memory(self) -> str:
        """加载用户长期记忆文件（memories/{username}/AGENTS.md）。

        跨会话持久化，记录用户偏好与可复用经验。
        在新会话首轮对话时加载，提供跨会话上下文。
        """
        return self._load_memory_file(self.long_term_memory_file, "长期记忆")

    def _get_os_info(self) -> str:
        """获取当前操作系统信息，返回格式化的系统提示词片段。

        检测运行环境并将系统名称统一映射为友好显示名
        （如 Darwin → macOS），用于拼接到系统提示词中。

        Returns:
            格式化的操作系统信息字符串，如 '## OS: Linux'。
        """
        system = platform.system()
        os_name = {"Windows": "Windows", "Linux": "Linux", "Darwin": "macOS"}.get(
            system, system
        )
        os_info = f"## OS: {os_name}"
        if system == "Windows":
            os_info += (
                "\n- Shell 为 cmd.exe；请使用 dir/type/copy/mkdir 等命令。"
                "\n- Python 命令使用 python，不要使用 python3。"
            )
        return os_info

    def _make_session_context(self) -> SessionContext:
        """把当前 EasyAgent 的会话差异冻结成运行期 context（不进缓存键）。"""
        return SessionContext(
            session_id=self.session_id or "",
            username=self.username,
            safe_username=self.safe_username,
            workspace_dir=str(self.workspace_dir.absolute()),
            workspace_virtual_path=self.workspace_virtual_path,
            user_skills_dir=str(self.user_skills_dir.absolute()),
            memory_file=str(self.memory_file.absolute()),
            long_term_memory_file=str(self.long_term_memory_file.absolute()),
            organization_id=self.organization_id or "",
            shell_env=dict(self.shell_env),
            workspace_name=self.workspace_name,
        )

    def _uploads_manifest_enabled(self) -> bool:
        cfg = getattr(self.config, "file_context", None)
        return cfg is not None and bool(getattr(cfg, "enabled", True))

    def _memory_sources(self) -> list[str]:
        """固定的记忆源列表（文件不存在时 MemoryMiddleware 自动跳过）。

        D2 之前 memory 列表按「文件当前是否存在」变化，导致同一会话首轮/二轮
        编译出不同图。改为固定列表后，运行期按需加载，既减少重复编译，也让
        记忆在写入后下一轮立即可见。
        """
        sources = [
            f"{self.workspace_virtual_path}/memory.md",
            LONG_TERM_MEMORY_VIRTUAL_PATH,
        ]
        if self._uploads_manifest_enabled():
            from .services.session_uploads import MANIFEST_FILENAME, UPLOADS_DIR_NAME

            sources.append(
                f"{self.workspace_virtual_path}/{UPLOADS_DIR_NAME}/{MANIFEST_FILENAME}"
            )
        return sources

    def _build_interrupt_config(self):
        """构造 HITL interrupt_on 配置（FW-12：按实际注入工具动态构造）。

        改造前无论工具是否注入，都会把 ``execute`` / ``save_expert`` /
        ``create_scheduled_task`` / ``access_knowledge_original`` 以及 MCP 写工具
        写进 ``interrupt_on``；未注入的名字会被 DeepAgents 静默忽略（T1-14）。
        现在交给 :func:`easy_agent.hitl.build_interrupt_config` 过滤：注册表只提供
        声明（决策 / 条件谓词 / 顺序），是否进入配置取决于本会话实际工具集。

        ``self.mcp_tools`` 实际承载「MCP + 应用注入工具」全集（历史命名），
        因此这里直接把它作为实际工具来源；``execute`` 等框架内置工具由
        ``build_interrupt_config`` 内部补入。
        """
        return build_interrupt_config(
            getattr(self, "mcp_tools", None) or [],
            enable_hitl=bool(getattr(self, "enable_hitl", True)),
            sid=getattr(self, "sid", ""),
        )

    def _graph_cache_block_reason(
        self, llm_cfg, shared_checkpointer
    ) -> str:
        """返回不可复用原因（"" = 可以复用）。"""
        if not graph_cache_enabled():
            return "EASY_AGENT_GRAPH_CACHE=0（显式关闭）"
        bound = [
            key
            for key, value in (getattr(llm_cfg, "headers", {}) or {}).items()
            if "{session_id}" in str(value)
        ]
        if bound:
            return f"模型请求头绑定 session_id: {bound}"
        if shared_checkpointer is None:
            return (
                "checkpointer 为按 session 创建的 legacy saver"
                "（非官方全局 Postgres/SQLite saver），无法跨会话共享"
            )
        return ""

    def _make_backend_factory(self, skills_paths: list[str], fallback_context=None):
        """返回 DeepAgents 0.7 兼容的 BackendProtocol 实例。

        0.7 移除了 callable backend；这里用 ContextBackend 实例在运行期按
        ToolRuntime.context 解析 SessionContext，再构建 CompositeBackend。
        """
        config = self.config
        expected_user = self.safe_username

        def _builder(ctx):
            return _build_backend_for_context(config, ctx, skills_paths)

        return ContextBackend(
            _builder,
            expected_user=expected_user,
            fallback_context=fallback_context,
        )

    def _create_agent(self):
        """Create the DeepAgents agent, reusing a process-wide compiled graph when safe.

        D2：编译期输入（模型 / 工具集 / 系统提示词 / middleware / HITL /
        subagent specs / skills / permissions / memory 源）用哈希做键；会话差异
        （workspace、session_id、thread_id）通过 ``context=SessionContext`` 传入
        运行期。无法安全共享时（模型头绑定 session、legacy checkpointer）显式
        降级为按会话建图。
        """
        from .model import resolve_llm_config

        actual_llm_cfg = resolve_llm_config(self.config, self.model_name)
        logger.info(
            f"[{self.sid}] 🤖 当前使用模型 | "
            f"model_key: {self.model_name} | "
            f"context_length: {actual_llm_cfg.context_length}"
        )
        logger.info(f"[{self.sid}] 📋 系统提示词 | 预览(前100字符): {self.system_prompt[:100]}")

        skills_paths = self._resolve_skills_paths()
        safe_external = self._safe_external_dirs()
        permissions = self._build_permissions(skills_paths, safe_external)
        self._log_user_skills()
        middleware = self._build_middleware()
        interrupt_on_config = self._build_interrupt_config()
        memory_list = self._memory_sources()
        raw_tools = list(self.mcp_tools) if self.mcp_tools else []
        subagents = build_subagent_specs(
            result_log_truncate=getattr(self.config.tools, "result_log_truncate", 500)
        )

        store = None
        try:
            store = get_agent_store()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{self.sid}] LangGraph Store 不可用，跨线程记忆仅走文件: {exc}")

        # FW-04：启动期由 app.py 注册；这里兜底非 Web 入口（调度器/脚本/测试）
        # 首次建图前也能拿到 execute 描述（注册幂等）。
        register_execute_description_profiles(self.config)
        log_framework_patch_status(self.sid)

        logger.info(
            f"[{self.sid}] 🏗️ 创建智能体参数 | "
            f"model: {actual_llm_cfg.model} | "
            f"provider: {actual_llm_cfg.provider} | "
            f"protocol: {actual_llm_cfg.protocol} | "
            f"max_steps: {self.max_steps} | "
            f"workspace: {self.workspace_dir.absolute()} | "
            f"skills: {skills_paths or []} | "
            f"mcp_tools: {len(raw_tools)} | "
            f"middleware: {[type(m).__name__ for m in middleware]} | "
            f"memory_file: {self.memory_file} | "
            f"summarization: {self.config.summarization.enabled} | "
            f"permissions(denied): {len(permissions)} 条"
        )

        memory_path = f"{self.workspace_virtual_path}/memory.md"
        logger.info(
            f"[{self.sid}] 🧠 记忆文件 | "
            f"会话: {memory_path}（存在则运行期加载） | "
            f"长期: {LONG_TERM_MEMORY_VIRTUAL_PATH}（存在则运行期加载） | "
            f"上传清单: {'启用' if self._uploads_manifest_enabled() else '关闭'} | "
            f"实际路径: {self.memory_file} / {self.long_term_memory_file} | "
            f"记忆源: {memory_list}"
        )

        shared_checkpointer = _shared_graph_checkpointer()
        block_reason = self._graph_cache_block_reason(actual_llm_cfg, shared_checkpointer)

        def _build(checkpointer, model, *, fallback_context: bool = False):
            # 共享图不带任何会话 fallback；降级路径才把当前会话作为兜底。
            backend = self._make_backend_factory(
                skills_paths,
                fallback_context=self.session_context if fallback_context else None,
            )
            graph_tools = contextualize_tools(raw_tools)
            with configured_summarization(self.config):
                return create_deep_agent(
                    name="easy-agent",
                    model=model,
                    system_prompt=self.system_prompt,
                    backend=backend,
                    skills=skills_paths or None,
                    middleware=middleware,
                    tools=graph_tools or None,
                    memory=memory_list or None,
                    permissions=permissions or None,
                    checkpointer=checkpointer,
                    interrupt_on=interrupt_on_config,
                    store=store,
                    subagents=subagents,
                    context_schema=SessionContext,
                )

        if block_reason:
            get_agent_graph_cache().record_disabled()
            logger.info(f"[{self.sid}] D2 graph-cache 未启用 | {block_reason}")
            model = create_model(
                self.config, model_name=self.model_name, session_id=self.session_id
            )
            self.model = model
            self.agent_graph_key = None
            return _build(
                get_session_checkpointer(self.session_id or "default"),
                model,
                fallback_context=True,
            )

        key = build_graph_key(
            model=model_fingerprint(actual_llm_cfg),
            tools=raw_tools,
            system_prompt=self.system_prompt,
            middleware=middleware,
            hitl={
                "enabled": bool(interrupt_on_config),
                "tools": sorted((interrupt_on_config or {}).keys()),
            },
            subagents=subagents,
            skills=skills_paths,
            permissions=permissions,
            memory=memory_list,
            summarization=summarization_fingerprint(self.config),
            config=config_fingerprint(self.config),
            checkpointer_id=f"{type(shared_checkpointer).__name__}:{id(shared_checkpointer)}",
            store_id=f"{type(store).__name__}:{id(store)}",
        )

        cache = get_agent_graph_cache()
        entry = cache.get(key.digest)
        if entry is not None:
            self.model = entry.model
            self.agent_graph_key = key.digest
            return entry.graph

        logger.info(
            f"[{self.sid}] D2 graph-cache 未命中，编译新图 | "
            f"key={key.digest[:12]} | {key.label}"
        )
        model = create_model(
            self.config, model_name=self.model_name, session_id=self.session_id
        )
        self.model = model
        graph = _build(shared_checkpointer, model)
        cache.put(
            key.digest,
            graph,
            model=model,
            username=self.safe_username,
            label=key.label,
        )
        self.agent_graph_key = key.digest
        return graph

    def _safe_external_dirs(self) -> dict[str, str]:
        """返回通过安全校验的外部目录映射 {虚拟路径(带/): 实际绝对路径}。

        跳过任何可能越权暴露其他用户会话数据的外部目录：工作区根目录本身、
        其祖先、或工作区根下不属于当前用户的子树。被跳过的目录会记录安全告警，
        确保即使配置失误也不会把其他用户的会话目录挂载给当前会话的智能体。
        """
        external_dirs = self.config.agent.external_dirs or {}
        workspace_root = Path(self.config.agent.workspace_dir)
        result: dict[str, str] = {}
        for vpath, real_path in external_dirs.items():
            real = Path(real_path)
            ok, reason = _check_external_dir_safety(
                real, workspace_root, self.safe_username
            )
            if not ok:
                logger.warning(
                    f"[{self.sid}] 🚫 拒绝挂载外部目录 {vpath} -> {real_path}：{reason}。"
                    f"该挂载已跳过，以防止越权访问其他用户的会话数据。"
                )
                continue
            vp = vpath if vpath.endswith("/") else vpath + "/"
            result[vp] = str(real.absolute())
        return result

    def _build_backend(self, skills_paths: list[str], safe_external=None):
        """构建 CompositeBackend（兼容旧调用；实际逻辑按 context 解析）。

        旧测试/旧调用会用 ``EasyAgent.__new__`` 只设置少量属性，再直接调用本方法；
        此时没有 ``session_context``，按现有属性构造一个等价 context，保持兼容。
        """
        context = getattr(self, "session_context", None)
        if context is None:
            workspace_dir = Path(getattr(self, "workspace_dir", Path(".")))
            safe_username = getattr(self, "safe_username", "default")
            config_agent = getattr(getattr(self, "config", None), "agent", None)
            memories_dir = Path(
                getattr(config_agent, "memories_dir", "./memories") or "./memories"
            )
            user_skills_dir = Path(
                getattr(self, "user_skills_dir", workspace_dir / "skills")
            )
            context = SessionContext(
                session_id=getattr(self, "session_id", "") or "",
                username=getattr(self, "username", safe_username),
                safe_username=safe_username,
                workspace_dir=str(workspace_dir.absolute()),
                workspace_virtual_path=getattr(self, "workspace_virtual_path", "/workspace"),
                user_skills_dir=str(user_skills_dir.absolute()),
                memory_file=str((workspace_dir / "memory.md").absolute()),
                long_term_memory_file=str(
                    (memories_dir / safe_username / "AGENTS.md").absolute()
                ),
                organization_id=getattr(self, "organization_id", "") or "",
                shell_env=dict(getattr(self, "shell_env", {}) or {}),
                workspace_name=getattr(self, "workspace_name", "") or "",
            )
        return _build_backend_for_context(
            self.config,
            context,
            skills_paths,
            safe_external=safe_external,
            sid=self.sid,
        )

    def _build_middleware(self) -> list:
        """构建自定义中间件列表。

        SummarizationMiddleware 由 create_deep_agent 自动添加（使用模型感知的
        默认值 trigger=0.85, keep=0.10），此处无需重复添加，
        否则会导致 AssertionError。

        Returns:
            空列表，表示无额外自定义中间件。
        """
       # SummarizationMiddleware is automatically added by create_deep_agent
       # with model-aware defaults (trigger=0.85, keep=0.10).
       # No need to add it here - duplicate middleware causes AssertionError.
        from .services.logging_middleware import LoggingMiddleware
        from langchain.agents.middleware import TodoListMiddleware

        middleware = [
            # 0.7 起 write_todos 需要显式 TodoListMiddleware（0.6.8 是默认内置）。
            TodoListMiddleware(),
            # D2：middleware 进共享编译图，不能再绑定 session_id（否则同图跨会话
            # 复用会把日志前缀写成首个会话的）。工具异常兜底本身与会话无关。
            LoggingMiddleware(
                result_log_truncate=getattr(
                    self.config.tools, "result_log_truncate", 500
                ),
            )
        ]
        try:
            from langchain.agents.middleware import (
                ModelCallLimitMiddleware,
                ToolCallLimitMiddleware,
            )

            run_limit = max(1, int(self.max_steps or 50))
            middleware.extend(
                [
                    # 超限时显式报错，避免「模型步数用尽但前端以为正常结束」；
                    # 错误会经 streaming 的异常分支转成 SSE error。
                    ModelCallLimitMiddleware(
                        run_limit=run_limit, exit_behavior="error"
                    ),
                    ToolCallLimitMiddleware(
                        run_limit=run_limit * 4, exit_behavior="error"
                    ),
                ]
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("未加载 call-limit 中间件: %s", exc)
        return middleware

    def _build_permissions(self, skills_paths: list[str] | None = None, safe_external: dict[str, str] | None = None) -> list[FilesystemPermission]:
        """根据配置构建文件系统权限规则。

        读取 config.agent.denied_dirs 中配置的虚拟路径目录列表，
        为每个目录生成一条 deny 权限规则。

        支持两种配置格式：
        - 字符串 ``"/user-skills"``：默认禁止 read+write
        - 字典 ``{path: "/user-skills", operations: ["write"]}``：只禁止指定操作

        operations 可选值：``read``、``write``，默认两者都禁止。
        paths 使用 glob 模式：自动为目录路径补充 /** 后缀以递归匹配子目录。
        注意：使用 CompositeBackend + sandbox default 时，permission path
        必须限定在已知 route 前缀下（如 /memories/、/user-skills/），
        否则会抛出 NotImplementedError。

        ``/user-skills`` 的 write 权限额外由 ``agent.skill_write`` 接管（默认
        new-only：可新建技能、不可改写已有技能），见 ``_build_skill_write_permissions``。
        规则顺序即优先级（框架取第一条命中），因此技能库规则排在最前。

        Args:
            skills_paths: 已解析的技能虚拟路径列表，用于判断 /user-skills/
                路由是否已挂载。未挂载时跳过对应 deny 规则，避免
                FilesystemMiddleware NotImplementedError。

        Returns:
            FilesystemPermission 列表，无配置且技能库未挂载时返回空列表。
        """
        denied = self.config.agent.denied_dirs or []
        policy = self._skill_write_policy()
        # 与 _build_backend 的挂载条件保持一致：技能目录存在即已挂载路由
        skills_mounted = bool(skills_paths) or self.user_skills_dir.exists()

        existing_prefixes = [
            f"{self.workspace_virtual_path}/",
        ]
        if skills_mounted:
            existing_prefixes.append("/user-skills/")
        for vp in (safe_external or {}):
            existing_prefixes.append(vp)

        permissions: list[FilesystemPermission] = []

        # 技能库写权限规则排在最先：框架按列表顺序取第一条命中，细规则必须先于通用规则
        if skills_mounted and policy != "allow":
            permissions.extend(self._build_skill_write_permissions(policy))
            logger.info(
                f"[{self.sid}] 🔒 技能库写权限 | skill_write={policy} | "
                f"已有技能 {len(self._discover_user_skill_names())} 个（其内容禁止改写）"
            )

        for item in denied:
            if isinstance(item, dict):
                path = str(item.get("path", "")).rstrip("/")
                raw_ops = item.get("operations", ["read", "write"])
            else:
                path = str(item).rstrip("/")
                raw_ops = ["read", "write"]
            if not path:
                continue
            if path == "/user-skills" and "write" in raw_ops:
                # /user-skills 的写权限由 agent.skill_write 接管：new-only 下需要允许
                # 新建技能目录，整体禁写请改用 skill_write=deny，避免两套规则互相冲突
                raw_ops = [op for op in raw_ops if op != "write"]
                logger.info(
                    f"[{self.sid}] ℹ️ /user-skills 的 write 权限由 "
                    f"agent.skill_write={policy} 接管，已忽略 denied_dirs 中的同名禁令"
                )
            glob_path = f"{path}/**"
            if not any(glob_path.startswith(prefix) for prefix in existing_prefixes):
                logger.info(
                    f"[{self.sid}] ⏭️ 跳过 deny 规则 | "
                    f"路径 {path} 未挂载路由，避免 NotImplementedError"
                )
                continue
            ops: list[FilesystemOperation] = [
                op for op in raw_ops if op in ("read", "write")
            ]
            if not ops:
                continue
            permissions.append(
                FilesystemPermission(
                    operations=ops,
                    paths=[glob_path],
                    mode="deny",
                )
            )

        logger.info(
            f"[{self.sid}] 🔒 文件系统权限 | "
            f"已配置 {len(permissions)} 条 deny 规则: {denied}"
        )
        return permissions

    def _skill_write_policy(self) -> str:
        """技能库写权限策略：new-only（默认）/ deny / allow，非法值回落 new-only。"""
        policy = str(getattr(self.config.agent, "skill_write", "") or "").strip().lower()
        return policy if policy in ("new-only", "deny", "allow") else "new-only"

    def _build_skill_write_permissions(
        self, policy: str
    ) -> list[FilesystemPermission]:
        """生成技能库写权限规则（第一条命中即生效，顺序有意义）。

        目的：让模型能在对话里**新建**技能目录并写入 ``SKILL.md``，同时改不动、
        删不掉用户已有的技能。

        - ``deny``：整库禁写（含隐藏路径），旧行为
        - ``new-only``：
          1. 隐藏路径禁写：wcmatch 未开 DOTMATCH，``/user-skills/**`` 匹配不到点开头的
             名字，必须显式列出才堵得住绕过；
          2. 已有技能目录内容禁写：只放行"新建"，不允许改写/删除既有技能；
          3. 技能库根下的一级条目禁写：挡住散落的单文件（``/user-skills/xxx.md``），
             正常写入路径 ``/user-skills/<新技能>/SKILL.md`` 是两级，不受影响。
        """
        if policy == "deny":
            return [
                FilesystemPermission(
                    operations=["write"],
                    paths=[
                        "/user-skills/**",
                        "/user-skills/.**",
                        "/user-skills/.*/**",
                    ],
                    mode="deny",
                )
            ]

        rules = [
            FilesystemPermission(
                operations=["write"],
                paths=["/user-skills/.**", "/user-skills/.*/**"],
                mode="deny",
            )
        ]
        for name in self._discover_user_skill_names():
            rules.append(
                FilesystemPermission(
                    operations=["write"],
                    paths=[f"/user-skills/{name}/**"],
                    mode="deny",
                )
            )
        rules.append(
            FilesystemPermission(
                operations=["write"],
                paths=["/user-skills/*"],
                mode="deny",
            )
        )
        return rules

    def _discover_user_skill_names(self) -> list[str]:
        """发现用户 workspace/{username}/skills/ 目录下的技能名称列表"""
        if not self.user_skills_dir.exists():
            return []
        names = []
        for skill_dir in sorted(self.user_skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            if (skill_dir / "SKILL.md").exists():
                names.append(skill_dir.name)
        return names

    def _log_user_skills(self) -> None:
        """打印当前用户技能目录及其中已添加的所有技能。

        在每次智能体加载时调用，便于排查技能中心添加的技能是否正确加载。
        """
        names = self._discover_user_skill_names()
        user_dir = str(self.user_skills_dir.absolute())

        if not names:
            logger.info(
                f"[{self.sid}] 🧩 用户技能 | 用户 '{self.username}' 未添加任何技能 | "
                f"目录: {user_dir}"
            )
            return

        lines = [
            f"[{self.sid}] 🧩 用户技能 | 用户 '{self.username}' 已添加 {len(names)} 个技能",
            f"    📁 技能目录: {user_dir}",
        ]
        for name in names:
            skill_dir = self.user_skills_dir / name
            desc = self._read_skill_description(skill_dir)
            lines.append(f"    - {name}" + (f"  ({desc})" if desc else ""))
        logger.info("\n".join(lines))

    @staticmethod
    def _read_skill_description(skill_dir: Path) -> str:
        """从 SKILL.md 的 frontmatter 中读取 description 字段。

        仅取第一行描述，超长截断到 60 字符。
        """
        for md_name in ("SKILL.md",):
            md_path = skill_dir / md_name
            if not md_path.exists():
                continue
            try:
                text = md_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # YAML frontmatter: ---\n key: value \n ---
            if text.startswith("---"):
                end = text.find("\n---", 3)
                if end != -1:
                    front = text[3:end]
                    for raw in front.splitlines():
                        line = raw.strip()
                        if line.lower().startswith("description:"):
                            desc = line.split(":", 1)[1].strip().strip('"').strip("'")
                            return desc[:60]
            return ""
        return ""

    def _resolve_skills_paths(self) -> list[str]:
        """返回用户技能的父级目录虚拟路径。

        SkillsMiddleware 会自动扫描父级目录下所有含 SKILL.md 的子目录，
        因此只需传入 /user-skills/ 即可，无需逐个列出每个技能路径。
        仅当用户技能目录存在且有技能时才返回。
        """
        if not self.user_skills_dir.exists():
            return []
        if not self._discover_user_skill_names():
            return []
        return ["/user-skills/"]

    async def run(self, user_input: str) -> str:
        """Execute agent with streaming output (CLI mode).

        Uses agent.astream(stream_mode='messages') for raw message-level streaming,
        which correctly captures DeepSeek's reasoning_content as per-token chunks.
        """
        self.logger.start_new_run()
        self.logger.log_request(
            messages=[{"role": "user", "content": user_input}], tools=None
        )

        start_time = time.time()
        full_response = ""
        thinking_shown = False
        response_shown = False
        tool_call_accumulated_args = {}

        try:
            async for event in self.agent.astream(
                {"messages": [{"role": "user", "content": user_input}]},
                stream_mode="messages",
                context=self.session_context,
            ):
                chunk, _metadata = event

                if isinstance(chunk, AIMessageChunk):
                    rc = (
                        extract_reasoning(chunk.additional_kwargs)
                        if hasattr(chunk, "additional_kwargs")
                        else ""
                    )
                    content = chunk.content or ""
                    tcc = getattr(chunk, "tool_call_chunks", None) or []

                    if rc:
                        if not thinking_shown:
                            logger.info("🧠 Thinking:")
                            thinking_shown = True
                        print(rc, end="", flush=True)

                    if content and not tcc:
                        if thinking_shown:
                            thinking_shown = False
                            print()  # newline separator
                        if not response_shown:
                            logger.info("🤖 Assistant:")
                            response_shown = True
                        print(content, end="", flush=True)
                        full_response += content

                    for tc in tcc:
                        name = tc.get("name", "") or ""
                        args_str = str(tc.get("args", "") or "")
                        tid = tc.get("id", "") or ""
                        if name:
                            args_data = args_str
                            if args_str:
                                try:
                                    parsed = json.loads(args_str)
                                    args_data = (
                                        parsed
                                        if isinstance(parsed, dict)
                                        else {"value": parsed}
                                    )
                                except json.JSONDecodeError:
                                    args_data = {}
                            else:
                                args_data = {}
                            tool_call_accumulated_args[name] = args_data
                            logger.info(f"🔧 Tool: {name} | Args: {args_data}")
                        elif tid and tid in tool_call_accumulated_args:
                            if args_str:
                                try:
                                    parsed = json.loads(args_str)
                                    if isinstance(parsed, dict):
                                        tool_call_accumulated_args[
                                            list(tool_call_accumulated_args.keys())[-1]
                                        ] = parsed
                                except json.JSONDecodeError:
                                    pass

                elif isinstance(chunk, ToolMessage):
                    from .services.mcp import _parse_mcp_content

                    tool_name = getattr(chunk, "name", "") or ""
                    result = _parse_mcp_content(chunk.content) if chunk.content else ""
                    truncate_len = getattr(
                        self.config.tools, "result_log_truncate", 200
                    )
                    logger.info(f"📊 Result [{tool_name}]: {result[:truncate_len]}")

            if thinking_shown or response_shown:
                print()  # final newline

            elapsed = time.time() - start_time
            logger.info(f"⏱️  Completed in {elapsed:.2f}s")

        except Exception as e:
            error_msg = f"Agent execution failed: {e}"
            logger.error(f"❌ {error_msg}")
            self.logger.log_response(content="", finish_reason="error")
            return error_msg

        self.logger.log_response(content=full_response, finish_reason="stop")
        return full_response
