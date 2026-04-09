"""Core Agent implementation using DeepAgents framework"""

import json
import platform
import re
import time
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend

from .config import Config
from .logger import AgentLogger
from .model import create_model


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"


class EasyAgent:
    def __init__(self, config: Config, system_prompt: str, skills: list[str] | None = None, username: str = "default", session_id: str = None):
        self.config = config
        self.system_prompt = system_prompt
        self.username = username
        self.session_id = session_id
        # 使用用户隔离的workspace目录，每个会话独立目录
        safe_name = Config.sanitize_username(username)
        if session_id:
            # 如果有 session_id，使用 session 级别的隔离
            self.workspace_dir = Path(config.agent.workspace_dir) / safe_name / session_id
        else:
            # 否则使用用户级别的隔离
            self.workspace_dir = Path(config.agent.workspace_dir) / safe_name
        self.max_steps = config.agent.max_steps
        self.skills = skills or []
        self.backend_type = "Unknown"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

        if "Current Workspace" not in system_prompt:
            workspace_info = (
                f"\n\n## Current Workspace\n"
                f"You are currently working in: `{self.workspace_dir.absolute()}`\n"
                f"All files will be written to this user-isolated directory.\n"
                f"**IMPORTANT**: When using filesystem tools (read_file, write_file, etc.), "
                f"you MUST use virtual paths starting with `/` instead of Windows absolute paths. \n"
                f"- Virtual path `/file.txt` will be saved to `{self.workspace_dir.absolute()}/file.txt`\n"
                f"- Virtual path `/subdir/file.txt` will be saved to `{self.workspace_dir.absolute()}/subdir/file.txt`\n"
                f"- Relative paths like `file.txt` or `subdir/file.txt` will also work.\n"
                f"- NEVER use Windows absolute paths like `D:\\path\\file.txt`."
            )
            self.system_prompt = system_prompt + workspace_info

        # 追加操作系统信息到系统提示词
        if "Current OS" not in system_prompt:
            os_info = self._get_os_info()
            os_prompt = f"\n\n## Current OS\n{os_info}"
            self.system_prompt = self.system_prompt + os_prompt

        self.logger = AgentLogger()
        self.agent = self._create_deep_agent()
        self.tools_info = self._get_tools_info()

    def _get_os_info(self) -> str:
        """获取当前操作系统的详细信息，用于系统提示词"""
        system = platform.system()
        if system == "Windows":
            release = platform.release()
            version = platform.version()
            return (
                f"You are running on **Windows** (Release: {release}, Version: {version})\n"
                f"- Use Windows-style commands: `dir`, `type`, `copy`, `move`, `del`, `mkdir`, `rmdir`\n"
                f"- Use backslash `\\` or forward slash `/` in file paths\n"
                f"- Python commands: `python` (not `python3`)\n"
                f"- Package manager: `pip install`\n"
                f"- Shell: PowerShell or cmd.exe\n"
                f"- Path separator: `;` (semicolon) for environment variables\n"
                f"- Line ending: CRLF"
            )
        elif system == "Linux":
            release = platform.release()
            distro = ", ".join(filter(None, [
                platform.freedesktop_os_release().get("NAME", "") if hasattr(platform, "freedesktop_os_release") else "",
                platform.freedesktop_os_release().get("VERSION", "") if hasattr(platform, "freedesktop_os_release") else "",
            ])) or "Unknown Distro"
            return (
                f"You are running on **Linux** (Kernel: {release}, Distro: {distro})\n"
                f"- Use Unix-style commands: `ls`, `cat`, `cp`, `mv`, `rm`, `mkdir`, `rmdir`\n"
                f"- Use forward slash `/` in file paths\n"
                f"- Python commands: `python3` (not `python`)\n"
                f"- Package manager: `pip3 install` or `apt install`\n"
                f"- Shell: bash\n"
                f"- Path separator: `:` (colon) for environment variables\n"
                f"- Line ending: LF"
            )
        elif system == "Darwin":
            release = platform.release()
            mac_ver = platform.mac_ver()[0]
            return (
                f"You are running on **macOS** (Version: {mac_ver}, Kernel: {release})\n"
                f"- Use Unix-style commands: `ls`, `cat`, `cp`, `mv`, `rm`, `mkdir`, `rmdir`\n"
                f"- Use forward slash `/` in file paths\n"
                f"- Python commands: `python3`\n"
                f"- Package manager: `pip3 install` or `brew install`\n"
                f"- Shell: zsh or bash\n"
                f"- Path separator: `:` (colon) for environment variables\n"
                f"- Line ending: LF"
            )
        else:
            return f"You are running on **{system}** ({platform.platform()})"

    def _get_tools_info(self) -> list[dict]:
        return [
            {"name": "write_todos", "description": "Task planning", "category": "planning"},
            {"name": "ls", "description": "List directory", "category": "filesystem"},
            {"name": "read_file", "description": "Read file", "category": "filesystem"},
            {"name": "write_file", "description": "Write file", "category": "filesystem"},
            {"name": "edit_file", "description": "Edit file", "category": "filesystem"},
            {"name": "glob", "description": "Find files", "category": "filesystem"},
            {"name": "grep", "description": "Search content", "category": "filesystem"},
            {"name": "execute", "description": "Run commands", "category": "execution"},
            {"name": "task", "description": "Subagent", "category": "subagent"},
        ]

    def get_tools_count(self) -> int:
        return len(self.tools_info)

    async def get_tools_names(self) -> list[str]:
        return [t["name"] for t in self.tools_info]

    def _print_step_header(self, step_num: int, max_steps: int):
        BOX_WIDTH = 58
        print(f"\n{Colors.DIM}╭{'─' * BOX_WIDTH}╮{Colors.RESET}")
        step_text = f"💭 Step {step_num}/{max_steps}"
        padding = max(0, BOX_WIDTH - 4 - len(step_text))
        print(f"{Colors.DIM}│{Colors.RESET} {Colors.BRIGHT_MAGENTA}{step_text}{Colors.RESET}{' ' * padding}{Colors.DIM}│{Colors.RESET}")
        print(f"{Colors.DIM}╰{'─' * BOX_WIDTH}╯{Colors.RESET}")

    def _print_thinking(self, content: str, step: int = 0):
        step_info = f" (Step {step})" if step > 0 else ""
        print(f"\n{Colors.BOLD}{Colors.BRIGHT_CYAN}🧠 Thinking{step_info}:{Colors.RESET}")
        for line in content.strip().split('\n'):
            print(f"{Colors.DIM}{line}{Colors.RESET}")

    def _print_tool_call(self, tool_name: str, arguments: dict, step: int = 0):
        args_str = json.dumps(arguments, indent=4, ensure_ascii=False)
        lines = args_str.split('\n')
        step_info = f" (Step {step})" if step > 0 else ""
        print(f"\n{Colors.BOLD}{Colors.BRIGHT_YELLOW}🔧 Tool Call{step_info}:{Colors.RESET} {tool_name}")
        print(f"{Colors.DIM}   Arguments:{Colors.RESET}")
        for line in lines:
            print(f"{Colors.DIM}   {line}{Colors.RESET}")

    def _print_tool_result(self, content: str, success: bool = True, step: int = 0):
        step_info = f" (Step {step})" if step > 0 else ""
        if success:
            result_icon = f"{Colors.GREEN}✓ Result{step_info}:{Colors.RESET}"
        else:
            result_icon = f"{Colors.BRIGHT_RED}✗ Result{step_info} (FAILED):{Colors.RESET}"
        if len(content) > 500:
            preview = content[:500] + f"\n... ({len(content)} chars total)"
            print(f"\n{result_icon}")
            print(f"{Colors.DIM}{preview}{Colors.RESET}")
        else:
            print(f"\n{result_icon}")
            print(f"{Colors.DIM}{content}{Colors.RESET}")

    def _print_step_time(self, step_num: int, step_time: float, total_time: float):
        print(f"\n{Colors.DIM}⏱️  Step {step_num} completed in {step_time:.2f}s (total: {total_time:.2f}s){Colors.RESET}")

    def _print_assistant_response(self, content: str):
        print(f"\n{Colors.BOLD}{Colors.BRIGHT_BLUE}🤖 Assistant:{Colors.RESET}")
        print(content)

    def _extract_thinking(self, content: str) -> tuple[str, str]:
        thinking_text = ""
        response_text = ""

        if isinstance(content, str):
            pattern = r'<think[^>]*>([\s\S]*?)</think\s*>'
            matches = re.findall(pattern, content, re.IGNORECASE)
            
            for match in matches:
                if match.strip():
                    thinking_text += match.strip() + "\n"
            
            cleaned_content = re.sub(pattern, '', content, flags=re.IGNORECASE).strip()
            
            if cleaned_content:
                response_text = cleaned_content
            elif not thinking_text:
                response_text = content

        return thinking_text, response_text

    def _process_ai_message(self, msg) -> tuple[str, str, list]:
        content = getattr(msg, 'content', '')
        if not content and isinstance(msg, dict):
            content = msg.get('content', '')

        thinking_text, response_text = self._extract_thinking(content)

        additional_kwargs = getattr(msg, 'additional_kwargs', {})
        if not additional_kwargs and isinstance(msg, dict):
            additional_kwargs = msg.get('additional_kwargs', {})

        tool_calls = []
        if additional_kwargs.get('tool_calls'):
            for tc in additional_kwargs['tool_calls']:
                tc_name = getattr(tc, 'name', '') or tc.get('name', '')
                tc_args = getattr(tc, 'args', {}) or tc.get('args', {})
                tool_calls.append((tc_name, tc_args))
        
        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            for tc in msg.tool_calls:
                tc_name = getattr(tc, 'name', '') or tc.get('name', '')
                tc_args = getattr(tc, 'args', {}) or tc.get('args', {})
                tool_calls.append((tc_name, tc_args))

        return thinking_text, response_text, tool_calls

    async def run(self, user_input: str) -> str:
        """Execute agent with user input (Mini-Agent style output)"""
        self.logger.start_new_run()
        print(f"\n{Colors.DIM}📝 Log file: {self.logger.get_log_file_path()}{Colors.RESET}")

        messages = [{"role": "user", "content": user_input}]
        self.logger.log_request(messages=messages, tools=None)

        try:
            start_total_time = time.time()
            current_step = 0
            max_steps = self.max_steps or 100
            response_content = ""
            pending_tool_results = False
            step_start = time.time()
            current_step_thinking = ""  # 当前步骤的思考内容(每轮独立)

            has_streaming = hasattr(self.agent, 'astream')

            if has_streaming:
                # 使用 messages 模式获取逐 token 的流式输出
                accumulated_content = ""  # 累积的 AI 内容
                accumulated_tool_calls = []  # 累积的工具调用
                
                async for chunk in self.agent.astream(
                    {"messages": messages},
                    stream_mode="messages",
                    subgraphs=True,
                    version="v2",
                ):
                    # chunk 结构: (token, metadata)
                    if chunk["type"] != "messages":
                        continue
                    
                    token, metadata = chunk["data"]
                    ns = chunk.get("ns", [])  # 命名空间
                    
                    # 只处理主 agent 的消息
                    is_subagent = any(s.startswith("tools:") for s in ns)
                    if is_subagent:
                        continue
                    
                    # 调试输出
                    print(f"[DEBUG] token.type={token.type}, tool_call_chunks={bool(getattr(token, 'tool_call_chunks', None))}")
                    
                    # 处理工具调用 chunks
                    if hasattr(token, 'tool_call_chunks') and token.tool_call_chunks:
                        for tc_chunk in token.tool_call_chunks:
                            print(f"[DEBUG] tool chunk: name={tc_chunk.get('name')}, args={tc_chunk.get('args', '')[:50]}")
                    
                    # 处理 AI 内容 token
                    if token.type == 'ai' and token.content:
                        accumulated_content += token.content
                        # 实时打印
                        print(token.content, end='', flush=True)
                    
                    # 处理工具结果
                    if token.type == 'tool':
                        print(f"\n[DEBUG] 工具结果: {token.name}")
                        print(f"[DEBUG] 结果内容: {str(token.content)[:200]}")

                # 处理累积的完整内容
                if accumulated_content:
                    print(f"\n\n[DEBUG] 最终累积内容长度: {len(accumulated_content)}")
                    # 创建假的消息对象用于后续处理
                    from types import SimpleNamespace
                    fake_msg = SimpleNamespace(
                        type='ai',
                        content=accumulated_content,
                        tool_calls=[],
                    )
                    thinking_text, response_text, tool_calls = self._process_ai_message(fake_msg)
                    
                    if thinking_text.strip():
                        self._print_thinking(thinking_text.strip(), 0)
                    
                    if response_text.strip():
                        self._print_assistant_response(response_text.strip())

            else:
                current_step = 1
                step_start = time.time()

                self._print_step_header(current_step, max_steps)
                print(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}🤖 Easy Agent Processing...{Colors.RESET}\n")

                result = await self.agent.ainvoke({"messages": messages})

                response_content = ""
                if isinstance(result, dict):
                    if "messages" in result:
                        messages_result = result["messages"]
                        if messages_result:
                            last_message = messages_result[-1]
                            if hasattr(last_message, 'content'):
                                response_content = last_message.content
                            elif isinstance(last_message, dict):
                                response_content = last_message.get("content", "")
                    elif "output" in result:
                        response_content = result["output"]
                elif hasattr(result, 'content'):
                    response_content = result.content
                else:
                    response_content = str(result)

                if response_content:
                    self._print_assistant_response(response_content)

                step_time = time.time() - step_start
                total_time = time.time() - start_total_time
                self._print_step_time(current_step, step_time, total_time)

            total_time = time.time() - start_total_time
            self.logger.log_response(
                content=response_content if has_streaming else "",
                finish_reason="stop"
            )

            return response_content if has_streaming else ""

        except Exception as e:
            error_msg = f"Agent execution failed: {str(e)}"
            print(f"\n{Colors.BRIGHT_RED}❌ Error:{Colors.RESET} {error_msg}")
            self.logger.log_response(content="", finish_reason="error")
            return error_msg

    def _create_deep_agent(self):
        model = create_model(self.config)

        project_root = Path(__file__).parent.parent.absolute()

        # 使用 CompositeBackend 将 workspace 和 skills 目录都映射到虚拟文件系统
        # / -> 用户workspace目录 (Agent的主工作目录)
        # /skills/ -> 项目skills目录 (只读访问技能文件)
        try:
            from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend as _LSB

            workspace_backend = _LSB(root_dir=str(self.workspace_dir.absolute()))
            skills_backend = FilesystemBackend(
                root_dir=str(project_root / "easy_agent" / "skills"),
                virtual_mode=True,
            )

            backend = CompositeBackend(
                routes={
                    "/skills/": skills_backend,
                },
                default=workspace_backend,
            )
            self.backend_type = "CompositeBackend"
            logger_msg = f"CompositeBackend | workspace: {self.workspace_dir.absolute()} | skills: {project_root / 'easy_agent' / 'skills'}"
        except (ImportError, TypeError, Exception) as e:
            # 如果 CompositeBackend 不可用或出错，回退到 LocalShellBackend
            import logging
            logging.getLogger(__name__).warning(f"CompositeBackend 不可用，回退到 LocalShellBackend: {e}")
            backend = LocalShellBackend(root_dir=str(self.workspace_dir.absolute()))
            self.backend_type = "LocalShellBackend"
            logger_msg = f"LocalShellBackend | workspace: {self.workspace_dir.absolute()}"

        # 将skills的Windows绝对路径转换为虚拟路径
        # DeepAgents skills 参数需要能被 read_file 工具访问的路径
        virtual_skills = []
        for skill_path in self.skills:
            skill_path_obj = Path(skill_path)
            try:
                # 计算skills目录下的相对路径，转为虚拟路径 /skills/xxx
                skills_root = project_root / "easy_agent" / "skills"
                rel_path = skill_path_obj.relative_to(skills_root)
                virtual_skill = f"/skills/{rel_path.as_posix()}"
            except ValueError:
                # 如果不在标准skills目录下，尝试相对项目根
                try:
                    rel_path = skill_path_obj.relative_to(project_root)
                    virtual_skill = f"/{rel_path.as_posix()}"
                except ValueError:
                    virtual_skill = skill_path
            virtual_skills.append(virtual_skill)

        agent = create_deep_agent(
            name="easy-agent",
            model=model,
            system_prompt=self.system_prompt,
            backend=backend,
            skills=virtual_skills if virtual_skills else None,
        )

        return agent
