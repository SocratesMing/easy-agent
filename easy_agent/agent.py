"""Core Agent implementation using DeepAgents framework

DeepAgents provides built-in tools automatically:
- write_todos: task planning
- ls, read_file, write_file, edit_file, glob, grep: filesystem operations
- execute: shell command execution
- task: subagent spawning

No need to declare these tools manually.
"""

import logging
import os
import platform
import re
import shutil
import sys
import time
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from deepagents.middleware.summarization import (
    SummarizationMiddleware,
    SummarizationToolMiddleware,
)

from .config import Config
from .logger import AgentLogger


class _Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_CYAN = "\033[96m"


from .model import create_model

logger = logging.getLogger(__name__)


def _copy_deps(src: Path, dst: Path) -> None:
    """Copy node_modules packages from src to dst using symlinks for efficiency."""
    if not src.exists() or not src.is_dir():
        return
    for item in src.iterdir():
        target = dst / item.name
        if not target.exists():
            if item.is_dir():
                shutil.copytree(str(item), str(target), symlinks=True)
            else:
                shutil.copy2(str(item), str(target))


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
        shared_deps_path: str = "",
        workspace_name: str = "",
    ):
        self.config = config
        self.username = username
        self.session_id = session_id
        self.skills_root = skills_root
        self.shared_deps_path = shared_deps_path
        self.safe_username = Config.sanitize_username(username)

        # Resolve workspace directory: user-provided > auto-generated
        if workspace_dir:
            self.workspace_dir = Path(workspace_dir)
        else:
            safe_name = Config.sanitize_username(username)
            base = Path(config.agent.workspace_dir)
            if session_id:
                dir_name = workspace_name if workspace_name else session_id
                self.workspace_dir = base / safe_name / dir_name
            else:
                self.workspace_dir = base / safe_name

        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self._workspace_renamed = False

        # User-level shared dependencies directory: workspace/{username}/.deps/
        self.user_deps_dir = self.workspace_dir.parent / ".deps"
        self.user_deps_dir.mkdir(parents=True, exist_ok=True)
        self.user_node_modules = self.user_deps_dir / "node_modules"
        self.user_venv = self.user_deps_dir / ".venv"

        # Ensure user-level node_modules exists with seed packages from shared_deps
        if shared_deps_path:
            shared_node_modules = Path(shared_deps_path)
            if not self.user_node_modules.exists():
                self.user_node_modules.mkdir(parents=True, exist_ok=True)
                if shared_node_modules.exists():
                    try:
                        _copy_deps(shared_node_modules, self.user_node_modules)
                        logger.info(
                            f"User-level node_modules seeded from {shared_node_modules}"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to seed user node_modules: {e}")

        # Symlink session workspace node_modules -> user-level node_modules
        workspace_node_modules = self.workspace_dir / "node_modules"
        if not workspace_node_modules.exists() and self.user_node_modules.exists():
            try:
                workspace_node_modules.symlink_to(
                    self.user_node_modules.resolve(), target_is_directory=True
                )
                logger.info(
                    f"Session node_modules symlinked -> {self.user_node_modules}"
                )
            except OSError as e:
                logger.debug(f"Failed to symlink session node_modules: {e}")

        # Ensure user-level Python venv exists
        if not self.user_venv.exists():
            try:
                import subprocess

                subprocess.run(
                    [sys.executable, "-m", "venv", str(self.user_venv)],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                logger.info(f"User-level Python venv created: {self.user_venv}")
            except Exception as e:
                logger.warning(f"Failed to create user venv: {e}")

        # Ensure memories directory and user memory file exist
        memories_dir = Path("memories") / self.safe_username
        memories_dir.mkdir(parents=True, exist_ok=True)
        self.memory_file = memories_dir / f"{self.safe_username}_AGENTS.md"
        # Migration: move old flat file to per-user directory
        old_file = Path("memories") / f"{self.safe_username}_AGENTS.md"
        if old_file.exists() and not self.memory_file.exists():
            shutil.move(str(old_file), str(self.memory_file))
            logger.info(f"Memory file migrated: {old_file} -> {self.memory_file}")
        if not self.memory_file.exists():
            self.memory_file.write_text(
                f"# {username} 的长期记忆\n\n", encoding="utf-8"
            )

        # Augment system prompt with OS, workspace, and skills context
        skills_info = ""
        if self.skills_root:
            skills_info = f"## Skills Root: {Path(self.skills_root).absolute()}\n"

        shared_deps_info = ""
        if self.shared_deps_path:
            shared_deps_info = (
                f"## 用户依赖目录: {self.user_deps_dir.absolute()}\n"
                f"- 所有 Python 和 Node.js 依赖安装在此目录，该用户的所有会话共享\n"
                f"- Node.js: node_modules 位于 {self.user_node_modules.absolute()}，已通过软链接在 workspace 中可用\n"
                f"- Python: 虚拟环境位于 {self.user_venv.absolute()}，使用 `source {self.user_venv}/bin/activate` 激活\n"
                f"- 安装新依赖时请安装到用户依赖目录，不要在每个会话中重复安装\n"
                f"- npm install 示例: cd {self.workspace_dir.absolute()} && npm install <package>\n"
                f"- pip install 示例: source {self.user_venv}/bin/activate && pip install <package>\n"
            )

        self.system_prompt = (
            f"{system_prompt}\n"
            f"## Workspace: {self.workspace_dir.absolute()}\n"
            f"{skills_info}"
            f"{shared_deps_info}"
            f"## 长期记忆文件: /memories/{self.safe_username}_AGENTS.md\n"
            f"你可以在长期记忆文件中记录用户偏好、重要决策、项目上下文等信息，"
            f"以便在后续会话中使用。每次对话开始时读取此文件，对话结束时根据需要更新。\n"
            f"{self._get_os_info()}"
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
        import shutil

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

        self.system_prompt = self.system_prompt.replace(
            old_path,
            str(new_workspace.absolute()),
        )

        self.agent = self._create_agent()

        logger.info(f"Workspace renamed | {old_path} -> {new_workspace.absolute()}")
        return True

    def _get_os_info(self) -> str:
        system = platform.system()
        if system == "Windows":
            return (
                "## Operating System: Windows\n"
                "- Shell commands: `dir`, `type`, `copy`, `del`, `mkdir`, `rmdir`\n"
                "- Python: `python` (NOT `python3`)\n"
                "- Package: `uv add` or `uv pip install` (always use `uv`)\n"
                "- Run scripts: `uv run python script.py` or `uv run script.py`\n"
                "- Path separator in env vars: `;`\n"
                "- Newline: CRLF"
            )
        elif system == "Linux":
            release = platform.release()
            distro = (
                ", ".join(
                    filter(
                        None,
                        [
                            platform.freedesktop_os_release().get("NAME", "")
                            if hasattr(platform, "freedesktop_os_release")
                            else "",
                            platform.freedesktop_os_release().get("VERSION", "")
                            if hasattr(platform, "freedesktop_os_release")
                            else "",
                        ],
                    )
                )
                or "Unknown Distro"
            )
            return (
                f"You are running on **Linux** (Kernel: {release}, Distro: {distro})\n"
                "- Use Unix-style commands: `ls`, `cat`, `cp`, `mv`, `rm`, `mkdir`, `rmdir`\n"
                "- Use forward slash `/` in file paths\n"
                "- Python commands: `python3` (not `python`)\n"
                "- Package manager: `pip3 install` or `apt install`\n"
                "- Shell: bash\n"
                "- Path separator: `:` (colon) for environment variables\n"
                "- Line ending: LF"
            )
        elif system == "Darwin":
            release = platform.release()
            mac_ver = platform.mac_ver()[0]
            return (
                f"You are running on **macOS** (Version: {mac_ver}, Kernel: {release})\n"
                "- Use Unix-style commands: `ls`, `cat`, `cp`, `mv`, `rm`, `mkdir`, `rmdir`\n"
                "- Use forward slash `/` in file paths\n"
                "- Python commands: `python3`\n"
                "- Package manager: `pip3 install` or `brew install`\n"
                "- Shell: zsh or bash\n"
                "- Path separator: `:` (colon) for environment variables\n"
                "- Line ending: LF"
            )
        else:
            return f"You are running on **{system}** ({platform.platform()})"

    def _create_agent(self):
        """Create the DeepAgents agent with workspace + skills backends.

        DeepAgents automatically provides built-in tools (ls, read_file,
        write_file, edit_file, glob, grep, execute, task, write_todos).
        We only need to configure the backend for file/shell access and
        optionally load skills.
        """
        model = create_model(self.config)
        self.model = model

        # Build backend: workspace for user files, skills for read-only skill access, memories for long-term user memory
        workspace_env = {}
        if self.shared_deps_path:
            workspace_env["NODE_PATH"] = str(self.user_node_modules.absolute())
        if self.user_venv.exists():
            venv_bin = str(self.user_venv / "bin")
            workspace_env["PATH"] = f"{venv_bin}:{os.environ.get('PATH', '')}"
            workspace_env["VIRTUAL_ENV"] = str(self.user_venv.absolute())
        workspace_backend = LocalShellBackend(
            root_dir=str(self.workspace_dir.absolute()),
            env=workspace_env if workspace_env else None,
            inherit_env=True,
        )

        memories_dir = self.memory_file.parent
        memories_backend = LocalShellBackend(root_dir=str(memories_dir.absolute()))

        skills_paths = self._resolve_skills_paths()
        routes = {
            "/workspace/": workspace_backend,
            "/memories/": memories_backend,
        }
        if skills_paths and self.skills_root:
            skills_backend = FilesystemBackend(
                root_dir=self.skills_root,
                virtual_mode=True,
            )
            routes["/skills/"] = skills_backend

        backend = CompositeBackend(
            routes=routes,
            default=workspace_backend,
        )

        # Summarization middleware: auto-compress context + on-demand compact_conversation tool
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
                backend=backend,
                trigger=("tokens", trigger_tokens),
                keep=("tokens", keep_tokens),
                trim_tokens_to_summarize=None,
                truncate_args_settings={
                    "trigger": ("tokens", trigger_tokens),
                    "keep": ("tokens", keep_tokens),
                },
                history_path_prefix=f"/memories/{self.safe_username}/conversation_history",
            )
            summarization_tool = SummarizationToolMiddleware(summarization)
            middleware.append(summarization_tool)
        else:
            logger.info(f"[{self.session_id}] Summarization disabled")

        return create_deep_agent(
            name="easy-agent",
            model=model,
            system_prompt=self.system_prompt,
            backend=backend,
            skills=skills_paths or None,
            middleware=middleware,
        )

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
        """Execute agent with streaming output (CLI mode)."""
        self.logger.start_new_run()
        self.logger.log_request(
            messages=[{"role": "user", "content": user_input}], tools=None
        )

        start_time = time.time()
        full_response = ""

        try:
            async for chunk in self.agent.astream(
                {"messages": [{"role": "user", "content": user_input}]},
                stream_mode="messages",
                subgraphs=True,
                version="v2",
            ):
                if chunk["type"] != "messages":
                    continue

                token, _metadata = chunk["data"]
                ns = chunk.get("ns", [])

                if any(s.startswith("tools:") for s in ns):
                    continue

                content = getattr(token, "content", "")
                if content:
                    self._handle_content_chunk(str(content))

                if hasattr(token, "tool_call_chunks") and token.tool_call_chunks:
                    for tc in token.tool_call_chunks:
                        name = tc.get("name")
                        if name:
                            print(
                                f"\n{_Colors.BOLD}{_Colors.BRIGHT_YELLOW}🔧 Tool: {name}{_Colors.RESET}"
                            )

                if token.type == "tool":
                    self._handle_tool_result(token)

            print()

            if hasattr(self, "_accumulated_raw") and self._accumulated_raw:
                _, response_text = self._extract_thinking(self._accumulated_raw)
                full_response = response_text or self._accumulated_raw
            else:
                full_response = ""

            elapsed = time.time() - start_time
            print(f"\n{_Colors.DIM}⏱️  Completed in {elapsed:.2f}s{_Colors.RESET}")

        except Exception as e:
            error_msg = f"Agent execution failed: {e}"
            print(f"\n{_Colors.BRIGHT_RED}❌ Error:{_Colors.RESET} {error_msg}")
            self.logger.log_response(content="", finish_reason="error")
            return error_msg

        self.logger.log_response(content=full_response, finish_reason="stop")
        return full_response

    def _handle_content_chunk(self, content_str: str):
        """Process streaming content chunk, tracking think tags for display."""
        if not hasattr(self, "_think_state"):
            self._think_state = {
                "in_thinking": False,
                "current_step": 0,
                "shown_thinking_header": False,
                "shown_response_header": False,
            }
            self._accumulated_raw = ""

        st = self._think_state
        self._accumulated_raw += content_str

        if not st["in_thinking"] and "<think" in content_str.lower():
            st["in_thinking"] = True
            st["current_step"] += 1
            st["shown_thinking_header"] = False

        if st["in_thinking"] and "</think" in content_str.lower():
            st["in_thinking"] = False
            st["shown_response_header"] = False

        if st["in_thinking"]:
            if not st["shown_thinking_header"]:
                print(
                    f"\n{_Colors.BOLD}{_Colors.BRIGHT_CYAN}🧠 Thinking (Step {st['current_step']}):{_Colors.RESET}"
                )
                st["shown_thinking_header"] = True
            print(content_str, end="", flush=True)
        else:
            if not st["shown_response_header"]:
                print(
                    f"\n{_Colors.BOLD}{_Colors.BRIGHT_BLUE}🤖 Assistant:{_Colors.RESET}"
                )
                st["shown_response_header"] = True
            print(content_str, end="", flush=True)

    def _handle_tool_result(self, token):
        """Process a tool result token."""
        name = getattr(token, "name", "")
        result = str(getattr(token, "content", ""))
        is_err = any(
            e in result.lower()
            for e in ["exit code: 1", "error:", "failed", "traceback"]
        )
        icon = f"{_Colors.GREEN}✓" if not is_err else f"{_Colors.BRIGHT_RED}✗"
        print(f"\n{icon} {name} ({len(result)} chars){_Colors.RESET}")

    def _extract_thinking(self, content: str) -> tuple[str, str]:
        thinking_text = ""
        response_text = ""

        if isinstance(content, str):
            pattern = r"<think[^>]*>([\s\S]*?)</think\s*>"
            matches = re.findall(pattern, content, re.IGNORECASE)

            for match in matches:
                if match.strip():
                    thinking_text += match.strip() + "\n"

            cleaned_content = re.sub(pattern, "", content, flags=re.IGNORECASE).strip()

            if cleaned_content:
                response_text = cleaned_content
            elif not thinking_text:
                response_text = content

        return thinking_text, response_text

    def _process_ai_message(self, msg) -> tuple[str, str, list]:
        content = getattr(msg, "content", "")
        if not content and isinstance(msg, dict):
            content = msg.get("content", "")

        thinking_text, response_text = self._extract_thinking(content)

        additional_kwargs = getattr(msg, "additional_kwargs", {})
        if not additional_kwargs and isinstance(msg, dict):
            additional_kwargs = msg.get("additional_kwargs", {})

        tool_calls = []
        if additional_kwargs.get("tool_calls"):
            for tc in additional_kwargs["tool_calls"]:
                tc_name = getattr(tc, "name", "") or tc.get("name", "")
                tc_args = getattr(tc, "args", {}) or tc.get("args", {})
                tool_calls.append((tc_name, tc_args))

        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tc_name = getattr(tc, "name", "") or tc.get("name", "")
                tc_args = getattr(tc, "args", {}) or tc.get("args", {})
                tool_calls.append((tc_name, tc_args))

        return thinking_text, response_text, tool_calls
