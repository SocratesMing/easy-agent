"""Core Agent implementation using DeepAgents framework

DeepAgents provides built-in tools automatically:
- write_todos: task planning
- ls, read_file, write_file, edit_file, glob, grep: filesystem operations
- execute: shell command execution
- task: subagent spawning

No need to declare these tools manually.
"""

import logging
import platform
import re
import time
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend

from .config import Config
from .display import Colors
from .logger import AgentLogger
from .model import create_model

logger = logging.getLogger(__name__)


class EasyAgent:
    """EasyAgent wraps DeepAgents with per-user/per-session workspace isolation.

    Args:
        config: Application configuration.
        system_prompt: Base system prompt (will be augmented with OS/workspace info).
        skills: List of skill directory paths to load.
        username: Username for workspace isolation.
        session_id: Session ID for workspace isolation.
        workspace_dir: Override workspace directory. If provided, this takes
            priority over the auto-generated path from config + username + session_id.
    """

    def __init__(
        self,
        config: Config,
        system_prompt: str,
        skills: list[str] | None = None,
        username: str = "default",
        session_id: str = None,
        workspace_dir: str | Path | None = None,
    ):
        self.config = config
        self.username = username
        self.session_id = session_id
        self.skills = skills or []

        # Resolve workspace directory: user-provided > auto-generated
        if workspace_dir:
            self.workspace_dir = Path(workspace_dir)
        else:
            safe_name = Config.sanitize_username(username)
            base = Path(config.agent.workspace_dir)
            self.workspace_dir = base / safe_name / session_id if session_id else base / safe_name

        self.workspace_dir.mkdir(parents=True, exist_ok=True)

        # Augment system prompt with OS and workspace context
        self.system_prompt = (
            f"{system_prompt}\n"
            f"## Workspace: {self.workspace_dir.absolute()}\n"
            f"{self._get_os_info()}"
        )

        self.max_steps = config.agent.max_steps
        self.logger = AgentLogger()
        self.agent = self._create_agent()

        logger.info(f"EasyAgent initialized | workspace: {self.workspace_dir.absolute()} | user: {username} | session: {session_id}")

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
            distro = ", ".join(filter(None, [
                platform.freedesktop_os_release().get("NAME", "") if hasattr(platform, "freedesktop_os_release") else "",
                platform.freedesktop_os_release().get("VERSION", "") if hasattr(platform, "freedesktop_os_release") else "",
            ])) or "Unknown Distro"
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
        project_root = Path(__file__).parent.parent.absolute()

        # Build backend: workspace for user files, skills for read-only skill access
        workspace_backend = LocalShellBackend(root_dir=str(self.workspace_dir.absolute()))

        skills_paths = self._resolve_skills_paths(project_root)
        if skills_paths:
            skills_backend = FilesystemBackend(
                root_dir=str(project_root / "easy_agent" / "skills"),
                virtual_mode=True,
            )
            backend = CompositeBackend(
                routes={
                    "/workspace/": workspace_backend,
                    "/skills/": skills_backend,
                },
                default=workspace_backend,
            )
        else:
            backend = workspace_backend

        return create_deep_agent(
            name="easy-agent",
            model=model,
            system_prompt=self.system_prompt,
            backend=backend,
            skills=skills_paths or None,
        )

    def _resolve_skills_paths(self, project_root: Path) -> list[str]:
        """Resolve skill paths to virtual paths relative to the backend root."""
        if not self.skills:
            return []

        skills_root = project_root / "easy_agent" / "skills"
        virtual_skills = []
        for skill_path in self.skills:
            p = Path(skill_path)
            try:
                rel = p.relative_to(skills_root)
                virtual_skills.append(f"/skills/{rel.as_posix()}")
            except ValueError:
                try:
                    rel = p.relative_to(project_root)
                    virtual_skills.append(f"/{rel.as_posix()}")
                except ValueError:
                    virtual_skills.append(str(p))
        return virtual_skills

    async def run(self, user_input: str) -> str:
        """Execute agent with streaming output (CLI mode)."""
        self.logger.start_new_run()
        self.logger.log_request(messages=[{"role": "user", "content": user_input}], tools=None)

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
                            print(f"\n{Colors.BOLD}{Colors.BRIGHT_YELLOW}🔧 Tool: {name}{Colors.RESET}")

                if token.type == "tool":
                    self._handle_tool_result(token)

            print()

            if hasattr(self, "_accumulated_raw") and self._accumulated_raw:
                _, response_text = self._extract_thinking(self._accumulated_raw)
                full_response = response_text or self._accumulated_raw
            else:
                full_response = ""

            elapsed = time.time() - start_time
            print(f"\n{Colors.DIM}⏱️  Completed in {elapsed:.2f}s{Colors.RESET}")

        except Exception as e:
            error_msg = f"Agent execution failed: {e}"
            print(f"\n{Colors.BRIGHT_RED}❌ Error:{Colors.RESET} {error_msg}")
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
                print(f"\n{Colors.BOLD}{Colors.BRIGHT_CYAN}🧠 Thinking (Step {st['current_step']}):{Colors.RESET}")
                st["shown_thinking_header"] = True
            print(content_str, end="", flush=True)
        else:
            if not st["shown_response_header"]:
                print(f"\n{Colors.BOLD}{Colors.BRIGHT_BLUE}🤖 Assistant:{Colors.RESET}")
                st["shown_response_header"] = True
            print(content_str, end="", flush=True)

    def _handle_tool_result(self, token):
        """Process a tool result token."""
        name = getattr(token, "name", "")
        result = str(getattr(token, "content", ""))
        is_err = any(e in result.lower() for e in ["exit code: 1", "error:", "failed", "traceback"])
        icon = f"{Colors.GREEN}✓" if not is_err else f"{Colors.BRIGHT_RED}✗"
        print(f"\n{icon} {name} ({len(result)} chars){Colors.RESET}")

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
