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
import os
import platform
import re
import shutil
import time
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse
from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain_core.messages import AIMessageChunk, ToolMessage

from .config import Config
from .logger import AgentLogger
from .model import _parse_mcp_content, create_model


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# read_file 默认 limit
# DeepAgents 默认 limit=100，导致模型频繁分页读取 skill/代码文件。
# 这里调高到 1000，减少翻页次数。
# ---------------------------------------------------------------------------
try:
    from deepagents.middleware import filesystem as _ds_fs

    _ds_fs.DEFAULT_READ_LIMIT = 1000
    logger.info(f"DeepAgents read_file DEFAULT_READ_LIMIT → {_ds_fs.DEFAULT_READ_LIMIT}")
except Exception:
    pass


class _PathTranslatingShell(LocalShellBackend):
    """LocalShellBackend 包装：execute 命令中自动将虚拟路径翻译为实际路径。

    模型可能混用虚拟路径（/skills/、/workspace/xxx/）和实际路径，
    此类在命令执行前统一翻译，无需模型在提示词中区分。
    """

    def __init__(self, path_mappings: dict[str, str], *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 预编译替换规则，使用 lookbehind 确保只匹配独立的虚拟路径
        # （前面是空格/引号/命令开头），避免替换真实路径中的子串
        self._rules = [
            (re.compile(rf'(^|[\s"\'&|;(])({re.escape(v)})'), real)
            for v, real in sorted(path_mappings.items(), key=lambda x: -len(x[0]))
        ]

    def execute(self, command: str, *, timeout: int | None = None) -> "ExecuteResponse":
        translated = command
        for pat, real in self._rules:
            translated = pat.sub(lambda m: m.group(1) + real, translated)
        if translated != command:
            logger.debug("execute path translation: %s → %s", command[:80], translated[:80])
        return super().execute(translated, timeout=timeout)


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
    ):
        self.config = config
        self.username = username
        self.session_id = session_id
        self.skills_root = skills_root
        self.mcp_tools = mcp_tools or []
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
        skills_info = ""
        if self.skills_root:
            skills_info = (
                f"## Skills: `/skills/`（例：`/skills/docx/SKILL.md`）\n"
            )

        self.system_prompt = (
            f"{system_prompt}\n"
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

        self.system_prompt = (
            self.system_prompt
            .replace(old_path, str(new_workspace.absolute()))
            .replace(old_virtual, self.workspace_virtual_path)
        )

        self.agent = self._create_agent()

        logger.info(f"Workspace renamed | {old_path} -> {new_workspace.absolute()}")
        return True

    def _get_os_info(self) -> str:
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

        logger.info(
            f"[{self.session_id}] 📋 系统提示词:\n{self.system_prompt}"
        )

        skills_paths = self._resolve_skills_paths()
        backend = self._build_backend(skills_paths)
        middleware = self._build_middleware(model, backend)
        tools = list(self.mcp_tools) if self.mcp_tools else None

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
            f"summarization: {self.config.summarization.enabled}"
        )

        memory_path = f"{self.memory_virtual_path}/AGENTS.md"

        logger.info(
            f"[{self.session_id}] 🧠 记忆文件 | "
            f"虚拟路径: {memory_path} | 实际路径: {self.memory_file}"
        )

        return create_deep_agent(
            name="easy-agent",
            model=model,
            system_prompt=self.system_prompt,
            backend=backend,
            skills=skills_paths or None,
            middleware=middleware,
            tools=tools,
            memory=[memory_path],
        )

    def _build_backend(self, skills_paths: list[str]):
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

        if skills_paths and self.skills_root:
            skills_backend = FilesystemBackend(
                root_dir=self.skills_root,
                virtual_mode=True,
            )
            routes["/skills/"] = skills_backend

        logger.info(
            f"[{self.session_id}] 🗺️ CompositeBackend routes:\n"
            + "\n".join(
                f"    {vp:40s} → {b.cwd}"
                for vp, b in sorted(routes.items())
            )
        )

        def backend_factory(runtime):
            ws_real = self.workspace_dir.absolute()
            ws_real.mkdir(parents=True, exist_ok=True)
            ws_str = str(ws_real)

            path_mappings = {
                f"{self.workspace_virtual_path}/": ws_str + "/",
                f"{self.memory_virtual_path}/": str(self.memory_file.parent.absolute()) + "/",
            }
            if self.skills_root:
                path_mappings["/skills/"] = str(Path(self.skills_root).absolute()) + "/"

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

        return backend_factory

    def _build_middleware(self, model, backend) -> list:
        middleware = []
        if self.config.summarization.enabled:
            max_tokens = self.config.llm.max_input_tokens
            threshold = self.config.summarization.compression_threshold
            target = self.config.summarization.compression_target
            trigger_tokens = int(max_tokens * threshold)
            keep_tokens = int(max_tokens * target)
            logger.info(
                f"[{self.session_id}] Summarization enabled | "
                f"max_input_tokens={max_tokens}, threshold={threshold}, "
                f"trigger={trigger_tokens}, keep={keep_tokens}"
            )
            summarization = SummarizationMiddleware(
                model=model,
                trigger=("tokens", trigger_tokens),
                keep=("tokens", keep_tokens),
                trim_tokens_to_summarize=None,
            )
            middleware.append(summarization)
        else:
            logger.info(f"[{self.session_id}] Summarization disabled")

        return middleware

    def _resolve_skills_paths(self) -> list[str]:
        """Discover skills from skills_root and return virtual paths."""
        if not self.skills_root:
            return []

        skills_root = Path(self.skills_root)
        if not skills_root.exists():
            return []

        virtual_skills = []
        for skill_dir in sorted(skills_root.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            skill_readme = skill_dir / "README.md"
            if skill_md.exists() or skill_readme.exists():
                virtual_skills.append(f"/skills/{skill_dir.name}")
        return virtual_skills

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
                chunk, metadata = event

                if isinstance(chunk, AIMessageChunk):
                    rc = chunk.additional_kwargs.get("reasoning_content", "") if hasattr(chunk, "additional_kwargs") else ""
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
                                    args_data = parsed if isinstance(parsed, dict) else {"value": parsed}
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
                                        tool_call_accumulated_args[list(tool_call_accumulated_args.keys())[-1]] = parsed
                                except json.JSONDecodeError:
                                    pass

                elif isinstance(chunk, ToolMessage):
                    tool_name = getattr(chunk, "name", "") or ""
                    result = _parse_mcp_content(chunk.content) if chunk.content else ""
                    truncate_len = getattr(self.config.tools, 'result_log_truncate', 200)
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
