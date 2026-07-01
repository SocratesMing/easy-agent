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
import re
import shutil
import time
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse
from deepagents.middleware.filesystem import FilesystemOperation, FilesystemPermission
from langchain.agents.middleware import InterruptOnConfig, ToolCallRequest
from langchain_core.messages import AIMessageChunk, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from .config import Config
from .logger import AgentLogger
from .model import _parse_mcp_content, create_model


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Human-in-the-loop: 检测 execute 工具中的文件删除命令
# ---------------------------------------------------------------------------
_DESTRUCTIVE_CMD_PATTERNS = [
    r"\brm\b",
    r"\brmdir\b",
    r"\bunlink\b",
    r"\bshred\b",
    r"\bfind\s+.*\s-delete\b",
]

# 目录删除命令模式：rmdir、rm -r/-R/-d、find -delete、shred -r
_DIR_DELETION_PATTERNS = [
    r"\brmdir\b",
    r"\brm\b\s+(-\w*[rRd])",
    r"\bfind\s+.*\s-delete\b",
    r"\bshred\b\s+(-\w*[rR])",
]


def _is_directory_deletion(command: str) -> bool:
    """检查命令是否试图删除目录（而非仅删除文件）。"""
    if not command:
        return False
    return any(re.search(pat, command) for pat in _DIR_DELETION_PATTERNS)


def _is_destructive_command(request: ToolCallRequest) -> bool:
    """检查 execute 工具调用是否包含文件删除命令。

    目录删除命令（rmdir、rm -r 等）不触发 HITL 审批，
    而是由 _PathTranslatingShell.execute() 直接拒绝。
    仅文件删除命令（rm、unlink、shred）触发 HITL 审批。
    """
    tool_call = request.tool_call if hasattr(request, "tool_call") else {}
    command = (tool_call.get("args", {}) if isinstance(tool_call, dict) else {}).get("command", "")
    if not command:
        return False
    if not any(re.search(pat, command) for pat in _DESTRUCTIVE_CMD_PATTERNS):
        return False
    # 目录删除由 execute() 直接拒绝，不触发 HITL
    if _is_directory_deletion(command):
        return False
    return True


# ---------------------------------------------------------------------------
# read_file 默认 limit
# DeepAgents 默认 limit=100，导致模型频繁分页读取 skill/代码文件。
# 这里调高到 1000，减少翻页次数。
# ---------------------------------------------------------------------------
try:
    from deepagents.middleware import filesystem as _ds_fs

    _ds_fs.DEFAULT_READ_LIMIT = 1000
    logger.info(
        f"DeepAgents read_file DEFAULT_READ_LIMIT → {_ds_fs.DEFAULT_READ_LIMIT}"
    )
except Exception:
    pass


class _PathTranslatingShell(LocalShellBackend):
    """LocalShellBackend 包装：execute 命令中自动将虚拟路径翻译为实际路径。

    模型可能混用虚拟路径（/skills/、/workspace/xxx/）和实际路径，
    此类在命令执行前统一翻译，无需模型在提示词中区分。

    同时对 execute 返回结果做反向翻译（实际路径→虚拟路径），
    避免命令输出（如 pwd、ls、错误堆栈）向模型暴露宿主机绝对路径，
    否则模型会从输出中"学到"绝对路径并在后续命令中直接使用。
    """

    def __init__(self, path_mappings: dict[str, str], *args, **kwargs):
        """初始化路径翻译 Shell 后端。

        预编译虚拟路径→实际路径的替换规则，按路径长度降序排列，
        确保最长前缀优先匹配，避免短前缀误替换长路径中的子串。

        Args:
            path_mappings: 虚拟路径前缀到实际路径的映射字典，
                如 {'/workspace/user/session/': '/abs/path/to/session/'}。
            *args: 传递给 LocalShellBackend 的位置参数。
            **kwargs: 传递给 LocalShellBackend 的关键字参数。
        """
        super().__init__(*args, **kwargs)
        # 正向规则：虚拟路径 → 实际路径（用于翻译输入命令）
        # 去掉结尾斜杠，使 mkdir/cd 等不带斜杠的路径也能匹配；
        # 用 lookahead 确保路径后是边界字符（斜杠/空格/引号/结尾等），
        # 避免 /workspace/szm/sess 误匹配 /workspace/szm/sessionXYZ
        self._rules = [
            (
                re.compile(rf'(^|[\s"\'&|;(=])({re.escape(v.rstrip("/"))})(?=/|$|[\s"\'&|;),=])'),
                real.rstrip("/"),
            )
            for v, real in sorted(path_mappings.items(), key=lambda x: -len(x[0]))
        ]
        # 反向规则：实际路径 → 虚拟路径（用于翻译输出结果）
        # 去掉结尾斜杠，使 pwd 等不带斜杠的输出也能匹配；
        # 按实际路径长度降序排列，避免短路径误替换长路径中的子串
        self._reverse_rules = [
            (real.rstrip('/'), v.rstrip('/'))
            for v, real in sorted(path_mappings.items(), key=lambda x: -len(x[1]))
        ]

    def execute(self, command: str, *, timeout: int | None = None) -> "ExecuteResponse":
        """执行 shell 命令，执行前翻译虚拟路径，执行后反向翻译输出。

        执行前将命令中的虚拟路径前缀替换为实际路径；
        执行后将输出中的实际路径替换回虚拟路径，避免暴露宿主机路径。
        目录删除命令（rmdir、rm -r 等）将被直接拒绝。

        Args:
            command: 原始 shell 命令字符串，可能包含虚拟路径。
            timeout: 命令执行超时时间（秒），None 表示不限制。

        Returns:
            ExecuteResponse: 命令执行结果，输出中的实际路径已翻译为虚拟路径。
        """
        translated = command
        for pat, real in self._rules:
            translated = pat.sub(lambda m: m.group(1) + real, translated)
        if translated != command:
            logger.debug(
                "execute path translation: %s → %s", command[:80], translated[:80]
            )
        # 拦截目录删除命令：项目根目录和 workspace 下的文件夹不可删除
        if _is_directory_deletion(translated):
            logger.warning(
                f"[execute] 拒绝目录删除命令: {translated[:120]}"
            )
            return ExecuteResponse(
                output="Error: 不允许删除目录/文件夹。项目根目录和 workspace 下的文件夹受保护，"
                       "无法通过 rmdir、rm -r 等命令删除。如需删除文件，请使用 rm（不带 -r/-R/-d 参数）。",
                exit_code=1,
                truncated=False,
            )
        result = super().execute(translated, timeout=timeout)
        # 反向翻译输出：实际路径 → 虚拟路径
        if self._reverse_rules and result.output:
            output = result.output
            for real, v in self._reverse_rules:
                if real in output:
                    output = output.replace(real, v)
            if output != result.output:
                result = ExecuteResponse(
                    output=output,
                    exit_code=result.exit_code,
                    truncated=result.truncated,
                )
        return result


class EasyAgent:
    """EasyAgent wraps DeepAgents with per-user/per-session workspace isolation.

    Args:
        config: Application configuration.
        system_prompt: Base system prompt (will be augmented with OS/workspace info).
        skills_root: Path to the skills root directory. Skills are discovered automatically.
        username: Username for workspace isolation.
        session_id: Session ID for workspace isolation.
        workspace_dir: Override workspace directory. If provided, this takes
            priority over the auto-generated path from config + username + session_id.
        workspace_name: Custom workspace directory name (e.g. '20260501_143022_a1b2c').
            If provided, used instead of session_id as the directory name.
    """

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
    ):
        """初始化 EasyAgent 实例，配置工作区隔离、记忆文件和系统提示词。

        根据用户名和会话 ID 构建隔离的工作区目录，设置记忆文件路径，
        发现用户已添加的技能，拼接包含虚拟路径规范的系统提示词，
        最终调用 _create_agent() 创建 DeepAgents 智能体实例。

        Args:
            config: 应用配置对象，包含 LLM、agent、tools 等配置。
            system_prompt: 基础系统提示词，将被增强加入工作区、技能、记忆等路径信息。
            skills_root: 公共技能根目录路径，当前版本仅用于参考，不自动加载。
            username: 用户名，用于工作区路径隔离和记忆文件定位。
            session_id: 会话 ID，用作工作区子目录名。
            workspace_dir: 自定义工作区目录路径。提供时优先使用，覆盖自动生成的路径。
            workspace_name: 自定义工作区目录名（如 '20260501_143022_a1b2c'）。
                提供时替代 session_id 作为目录名。
            mcp_tools: MCP 工具列表，作为额外工具注入智能体。
            organization_id: 用户所属机构ID，注册后不可更改，将注入系统提示词。
        """
        self.config = config
        self.username = username
        self.session_id = session_id
        self.skills_root = skills_root
        self.mcp_tools = mcp_tools or []
        self.organization_id = organization_id or ""
        self.enable_hitl = enable_hitl
        self.safe_username = Config.sanitize_username(username)

        if workspace_dir:
            self.workspace_dir = Path(workspace_dir)
            dir_name = self.workspace_dir.name
        else:
            base = Path(config.agent.workspace_dir)
            if session_id:
                dir_name = workspace_name if workspace_name else session_id
                self.workspace_dir = base / self.safe_username / dir_name
            else:
                self.workspace_dir = base / self.safe_username
                dir_name = self.safe_username

        # Don't create directory eagerly — FilesystemBackend.write will create it on first write
        self.workspace_virtual_path = f"/workspace/{self.safe_username}/{dir_name}"
        self.memory_virtual_path = f"/memories/{self.safe_username}"
        self._workspace_renamed = False

        # Ensure memories directory and user memory file exist
        memories_base = Path(config.agent.memories_dir)
        memories_dir = memories_base / self.safe_username
        memories_dir.mkdir(parents=True, exist_ok=True)
        self.memory_file = memories_dir / "AGENTS.md"
        old_file = memories_base / f"{self.safe_username}_AGENTS.md"
        if old_file.exists() and not self.memory_file.exists():
            shutil.move(str(old_file), str(self.memory_file))
            logger.info(f"Memory file migrated: {old_file} -> {self.memory_file}")
        if not self.memory_file.exists():
            self.memory_file.write_text(
                f"# {username} 的长期记忆\n\n", encoding="utf-8"
            )

        # Augment system prompt with virtual paths only.
        # _PathTranslatingShell 会自动把虚拟路径翻译为实际路径，
        # 模型无需知道实际路径，统一使用虚拟路径即可。
        # 仅加载用户已添加的技能（/user-skills/），不再加载全部公共技能。
        skills_info = ""

        # 用户技能目录: workspace/{username}/skills/
        self.user_skills_dir = self.workspace_dir.parent / "skills"
        user_skill_names = self._discover_user_skill_names()
        if user_skill_names:
            skills_info = "## User Skills: `/user-skills/`（例：`/user-skills/my_skill/SKILL.md`）\n"

        # 将用户机构ID注入系统提示词
        org_info = ""
        if self.organization_id:
            org_info = f"## 当前用户机构\n机构ID: `{self.organization_id}`（注册后不可更改）\n"

        self.system_prompt = (
            f"{system_prompt}\n"
            f"{org_info}"
            f"## Workspace: `{self.workspace_virtual_path}/`\n"
            f"{skills_info}"
            f"## Memory: `{self.memory_virtual_path}/AGENTS.md`\n"
            f"{self._get_os_info()}\n"
        )

        self.max_steps = config.agent.max_steps
        self.logger = AgentLogger()
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
        old_virtual = self.workspace_virtual_path
        self.workspace_dir = new_workspace
        self.workspace_virtual_path = f"/workspace/{self.safe_username}/{new_name}"

        self.system_prompt = self.system_prompt.replace(
            old_path, str(new_workspace.absolute())
        ).replace(old_virtual, self.workspace_virtual_path)

        self.agent = self._create_agent()

        logger.info(f"Workspace renamed | {old_path} -> {new_workspace.absolute()}")
        return True

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
        return f"## OS: {os_name}"

    def _create_agent(self):
        """Create the DeepAgents agent with workspace + skills backends.

        DeepAgents automatically provides built-in tools (ls, read_file,
        write_file, edit_file, glob, grep, execute, task, write_todos).
        We only need to configure the backend for file/shell access and
        optionally load skills.
        """
        model = create_model(self.config)
        self.model = model

        logger.info(f"[{self.session_id}] 📋 系统提示词:\n{self.system_prompt}")

        skills_paths = self._resolve_skills_paths()
        self._log_user_skills()
        backend = self._build_backend(skills_paths)
        middleware = self._build_middleware()
        tools = list(self.mcp_tools) if self.mcp_tools else None
        permissions = self._build_permissions(skills_paths)

        logger.info(
            f"[{self.session_id}] 🏗️ 创建智能体参数 | "
            f"model: {self.config.llm.model} | "
            f"provider: {self.config.llm.provider} | "
            f"protocol: {self.config.llm.protocol} | "
            f"max_steps: {self.max_steps} | "
            f"workspace: {self.workspace_dir.absolute()} | "
            f"skills: {skills_paths or []} | "
            f"mcp_tools: {len(tools) if tools else 0} | "
            f"middleware: {[type(m).__name__ for m in middleware]} | "
            f"memory_file: {self.memory_file} | "
            f"summarization: {self.config.summarization.enabled} | "
            f"permissions(denied): {len(permissions)} 条"
        )

        memory_path = f"{self.memory_virtual_path}/AGENTS.md"

        logger.info(
            f"[{self.session_id}] 🧠 记忆文件 | "
            f"虚拟路径: {memory_path} | 实际路径: {self.memory_file}"
        )

        interrupt_on_config = None
        if self.enable_hitl:
            interrupt_on_config = {
                "execute": InterruptOnConfig(
                    allowed_decisions=["approve", "reject"],
                    when=_is_destructive_command,
                ),
            }

        return create_deep_agent(
            name="easy-agent",
            model=model,
            system_prompt=self.system_prompt,
            backend=backend,
            skills=skills_paths or None,
            middleware=middleware,
            tools=tools,
            memory=[memory_path],
            permissions=permissions or None,
            checkpointer=MemorySaver(),
            interrupt_on=interrupt_on_config,
        )

    def _build_backend(self, skills_paths: list[str]):
        """构建 CompositeBackend 实例，配置多路由文件系统后端。

        创建记忆、工作区和用户技能三组 FilesystemBackend 路由，
        以 CompositeBackend 组合返回。default 后端使用 _PathTranslatingShell
        提供命令执行和虚拟路径翻译能力。

        Args:
            skills_paths: 已解析的技能虚拟路径列表，用于判断是否需要
                挂载用户技能后端路由。

        Returns:
            CompositeBackend: 组合后端实例，包含所有路由和路径映射。
        """
        memories_dir = self.memory_file.parent
        memories_backend = FilesystemBackend(
            root_dir=str(memories_dir.absolute()),
            virtual_mode=True,
        )

        workspace_backend = FilesystemBackend(
            root_dir=str(self.workspace_dir.absolute()),
            virtual_mode=True,
        )

        routes = {
            f"{self.memory_virtual_path}/": memories_backend,
            f"{self.workspace_virtual_path}/": workspace_backend,
        }

        if skills_paths and self.user_skills_dir.exists():
            user_skills_backend = FilesystemBackend(
                root_dir=str(self.user_skills_dir.absolute()),
                virtual_mode=True,
            )
            routes["/user-skills/"] = user_skills_backend

        # 挂载外部目录（skill 需要访问的宿主机目录）为虚拟路径路由
        external_dirs = self.config.agent.external_dirs or {}
        for vpath, real_path in external_dirs.items():
            vp = vpath if vpath.endswith("/") else vpath + "/"
            real = Path(real_path)
            real.mkdir(parents=True, exist_ok=True)
            routes[vp] = FilesystemBackend(
                root_dir=str(real.absolute()),
                virtual_mode=True,
            )

        logger.info(
            f"[{self.session_id}] 🗺️ CompositeBackend routes:\n"
            + "\n".join(f"    {vp:40s} → {b.cwd}" for vp, b in sorted(routes.items()))
        )

        ws_real = self.workspace_dir.absolute()
        ws_real.mkdir(parents=True, exist_ok=True)
        ws_str = str(ws_real)

        path_mappings = {
            f"{self.workspace_virtual_path}/": ws_str + "/",
            f"{self.memory_virtual_path}/": str(self.memory_file.parent.absolute()) + "/",
        }
        if self.user_skills_dir.exists():
            path_mappings["/user-skills/"] = str(self.user_skills_dir.absolute()) + "/"
        for vpath, real_path in external_dirs.items():
            vp = vpath if vpath.endswith("/") else vpath + "/"
            path_mappings[vp] = str(Path(real_path).absolute()) + "/"

        return CompositeBackend(
            default=_PathTranslatingShell(
                path_mappings=path_mappings,
                root_dir=ws_str,
                virtual_mode=True,
                inherit_env=True,
                timeout=120,
            ),
            routes=routes,
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
        # No need to add it here — duplicate middleware causes AssertionError.
        return []

    def _build_permissions(self, skills_paths: list[str] | None = None) -> list[FilesystemPermission]:
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

        Args:
            skills_paths: 已解析的技能虚拟路径列表，用于判断 /user-skills/
                路由是否已挂载。未挂载时跳过对应 deny 规则，避免
                FilesystemMiddleware NotImplementedError。

        Returns:
            FilesystemPermission 列表，无配置时返回空列表。
        """
        denied = self.config.agent.denied_dirs or []
        if not denied:
            return []

        existing_prefixes = [
            f"{self.memory_virtual_path}/",
            f"{self.workspace_virtual_path}/",
        ]
        if skills_paths:
            existing_prefixes.append("/user-skills/")
        external_dirs = self.config.agent.external_dirs or {}
        for vpath in external_dirs:
            vp = vpath if vpath.endswith("/") else vpath + "/"
            existing_prefixes.append(vp)

        permissions = []
        for item in denied:
            if isinstance(item, dict):
                path = str(item.get("path", "")).rstrip("/")
                raw_ops = item.get("operations", ["read", "write"])
            else:
                path = str(item).rstrip("/")
                raw_ops = ["read", "write"]
            if not path:
                continue
            glob_path = f"{path}/**"
            if not any(glob_path.startswith(prefix) for prefix in existing_prefixes):
                logger.info(
                    f"[{self.session_id}] ⏭️ 跳过 deny 规则 | "
                    f"路径 {path} 未挂载路由，避免 NotImplementedError"
                )
                continue
            ops: list[FilesystemOperation] = [
                op for op in raw_ops if op in ("read", "write")
            ] or ["read", "write"]
            permissions.append(
                FilesystemPermission(
                    operations=ops,
                    paths=[glob_path],
                    mode="deny",
                )
            )

        logger.info(
            f"[{self.session_id}] 🔒 文件系统权限 | "
            f"已配置 {len(permissions)} 条 deny 规则: {denied}"
        )
        return permissions

    def _discover_user_skill_names(self) -> list[str]:
        """发现用户 workspace/{username}/skills/ 目录下的技能名称列表"""
        if not self.user_skills_dir.exists():
            return []
        names = []
        for skill_dir in sorted(self.user_skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            if (skill_dir / "SKILL.md").exists() or (skill_dir / "README.md").exists():
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
                f"[{self.session_id}] 🧩 用户技能 | 用户 '{self.username}' 未添加任何技能 | "
                f"目录: {user_dir}"
            )
            return

        lines = [
            f"[{self.session_id}] 🧩 用户技能 | 用户 '{self.username}' 已添加 {len(names)} 个技能",
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
        for md_name in ("SKILL.md", "README.md"):
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
            ):
                chunk, _metadata = event

                if isinstance(chunk, AIMessageChunk):
                    rc = (
                        chunk.additional_kwargs.get("reasoning_content", "")
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
