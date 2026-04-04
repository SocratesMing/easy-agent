"""Core Agent implementation using DeepAgents framework"""

import json
import re
import time
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from .config import Config
from .logger import AgentLogger


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


class WukongAgent:
    def __init__(self, config: Config, system_prompt: str, skills: list[str] | None = None):
        self.config = config
        self.system_prompt = system_prompt
        self.workspace_dir = Path(config.agent.workspace_dir)
        self.max_steps = config.agent.max_steps
        self.skills = skills or []
        self.backend_type = "Unknown"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

        if "Current Workspace" not in system_prompt:
            workspace_info = f"\n\n## Current Workspace\nYou are currently working in: `{self.workspace_dir.absolute()}`\nAll relative paths will be resolved relative to this directory."
            self.system_prompt = system_prompt + workspace_info

        self.logger = AgentLogger()
        self.agent = self._create_deep_agent()
        self.tools_info = self._get_tools_info()

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

    def _print_thinking(self, content: str):
        print(f"\n{Colors.BOLD}{Colors.BRIGHT_CYAN}🧠 Thinking:{Colors.RESET}")
        for line in content.strip().split('\n'):
            print(f"{Colors.DIM}{line}{Colors.RESET}")

    def _print_tool_call(self, tool_name: str, arguments: dict):
        args_str = json.dumps(arguments, indent=4, ensure_ascii=False)
        lines = args_str.split('\n')
        print(f"\n{Colors.BOLD}{Colors.BRIGHT_YELLOW}🔧 Tool Call:{Colors.RESET} {tool_name}")
        print(f"{Colors.DIM}   Arguments:{Colors.RESET}")
        for line in lines:
            print(f"{Colors.DIM}   {line}{Colors.RESET}")

    def _print_tool_result(self, content: str):
        if len(content) > 500:
            preview = content[:500] + f"\n... ({len(content)} chars total)"
            print(f"\n{Colors.GREEN}✓ Result:{Colors.RESET}")
            print(f"{Colors.DIM}{preview}{Colors.RESET}")
        else:
            print(f"\n{Colors.GREEN}✓ Result:{Colors.RESET}")
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

            has_streaming = hasattr(self.agent, 'astream')

            if has_streaming:
                async for event in self.agent.astream({"messages": messages}):
                    if not isinstance(event, dict):
                        continue

                    for node_name, node_output in event.items():
                        if not isinstance(node_output, dict) or 'messages' not in node_output:
                            continue

                        msgs = node_output['messages']
                        msgs_list = []
                        
                        if hasattr(msgs, '__iter__') and not isinstance(msgs, str):
                            try:
                                msgs_list = list(msgs)
                            except Exception:
                                if hasattr(msgs, 'value'):
                                    msgs_list = [msgs.value]
                        elif isinstance(msgs, list):
                            msgs_list = msgs

                        for msg in msgs_list:
                            msg_type = getattr(msg, 'type', None)
                            if msg_type is None and isinstance(msg, dict):
                                msg_type = msg.get('type')

                            if msg_type == 'ai':
                                thinking_text, response_text, tool_calls = self._process_ai_message(msg)

                                if tool_calls:
                                    if pending_tool_results:
                                        step_time = time.time() - step_start
                                        total_time = time.time() - start_total_time
                                        self._print_step_time(current_step, step_time, total_time)
                                    
                                    current_step += 1
                                    step_start = time.time()
                                    self._print_step_header(current_step, max_steps)
                                    pending_tool_results = True

                                if thinking_text.strip():
                                    self._print_thinking(thinking_text)

                                for tc_name, tc_args in tool_calls:
                                    self._print_tool_call(tc_name, tc_args)

                                if response_text.strip():
                                    response_content = response_text.strip()
                                    self._print_assistant_response(response_content)
                                    pending_tool_results = False

                            elif msg_type == 'tool':
                                content = getattr(msg, 'content', '')
                                if not content and isinstance(msg, dict):
                                    content = msg.get('content', '')
                                self._print_tool_result(str(content)[:1000])

                if pending_tool_results:
                    step_time = time.time() - step_start
                    total_time = time.time() - start_total_time
                    self._print_step_time(current_step, step_time, total_time)

            else:
                current_step = 1
                step_start = time.time()

                self._print_step_header(current_step, max_steps)
                print(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}🤖 Wukong Agent Processing...{Colors.RESET}\n")

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
        llm_config = self.config.llm

        if llm_config.provider == "anthropic":
            model = ChatAnthropic(
                api_key=llm_config.api_key,
                model=llm_config.model,
                base_url=llm_config.api_base,
                max_retries=llm_config.retry.max_retries if llm_config.retry.enabled else 0,
            )
        elif llm_config.provider == "openai":
            base_url = llm_config.api_base
            if base_url and not base_url.endswith('/v1'):
                base_url = base_url.rstrip('/') + '/v1'

            model = ChatOpenAI(
                api_key=llm_config.api_key,
                model=llm_config.model,
                base_url=base_url,
                max_retries=llm_config.retry.max_retries if llm_config.retry.enabled else 0,
            )
        elif llm_config.provider == "minimax":
            import httpx

            api_base = llm_config.api_base or "https://api.minimaxi.com"

            model = ChatOpenAI(
                api_key=llm_config.api_key,
                model=llm_config.model,
                base_url=f"{api_base.rstrip('/')}/v1",
                http_async_client=httpx.AsyncClient(timeout=120.0),
                max_retries=llm_config.retry.max_retries if llm_config.retry.enabled else 0,
            )
        else:
            raise ValueError(f"Unsupported LLM provider: {llm_config.provider}")

        project_root = Path(__file__).parent.parent.absolute()
        backend = LocalShellBackend(root_dir=str(project_root))
        self.backend_type = "LocalShellBackend"

        agent = create_deep_agent(
            name="wukong-agent",
            model=model,
            system_prompt=self.system_prompt,
            backend=backend,
            skills=self.skills if self.skills else None,
        )

        return agent
