"""Interactive DeepAgents virtual-root isolation test.

Usage:
    python tests/virtual_root_agent.py
    python tests/virtual_root_agent.py --workspace /tmp/easy-agent-session
    python tests/virtual_root_agent.py --model deepseek
    python tests/virtual_root_agent.py --skills-dir /path/to/skills
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, LocalShellBackend
from langchain_core.messages import AIMessageChunk
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from easy_agent.config import Config
from easy_agent.model import create_model, extract_reasoning


PROJECT_ROOT = Path(__file__).parent.parent.resolve()


def load_dev_config(config_path: str | None = None) -> Config:
    path = Path(config_path).expanduser() if config_path else None
    if path is None:
        path = Config.find_config_file("config.dev.yaml") or Config.find_config_file("config.yaml")
    if path is None:
        raise FileNotFoundError("未找到 config.dev.yaml 或 config.yaml")
    return Config.from_yaml(path)


def build_backend(
    workspace: Path,
    *,
    skills_dir: Path | None = None,
) -> LocalShellBackend | CompositeBackend:
    workspace = workspace.expanduser().resolve()
    shell_backend = LocalShellBackend(
        root_dir=str(workspace),
        virtual_mode=True,
    )
    if skills_dir is None:
        return shell_backend

    skills_dir = skills_dir.expanduser().resolve()
    if skills_dir == workspace:
        raise ValueError("skills directory must differ from workspace directory")

    return CompositeBackend(
        default=shell_backend,
        routes={
            "/skills/": LocalShellBackend(
                root_dir=str(skills_dir),
                virtual_mode=True,
            ),
        },
    )


def resolve_skills_dir(config: Config, root_dir: str | None = None) -> Path:
    source = Path(root_dir or config.tools.skills_dir).expanduser()
    if not source.is_absolute():
        source = PROJECT_ROOT / source
    return source.resolve()


def build_agent(
    *,
    workspace: Path,
    model: Any,
    session_name: str,
    skills_dir: Path | None = None,
) -> Any:
    system_prompt = f"""你是一个用于验证 DeepAgents LocalShellBackend 双目录映射的助手。

## 测试环境
- 工作区虚拟根目录：`/`，实际目录：`{workspace.resolve()}`
- Skills 虚拟目录：`/skills/`，实际目录：`{skills_dir.resolve() if skills_dir else '未配置'}`
- 会话名称：`{session_name}`

## 行为要求
- 文件操作必须使用 DeepAgents 的 `ls`、`read_file`、`write_file`、`edit_file`、`glob`、`grep` 工具。
- 命令执行必须使用 DeepAgents 的 `execute` 工具；`pwd` 对应工作区目录。
- 可用 Skills 会从 `/skills/` 的一级子目录中解析，并在系统提示中注入元数据。
- 用户要求读取 `/etc/passwd`、`/../outside.txt`、`/host-secret.txt` 等越界路径时，按工具返回结果如实说明。
- 不要编造文件内容；工具失败时报告失败原因。
"""
    return create_deep_agent(
        name="virtual-root-isolation-test",
        model=model,
        system_prompt=system_prompt,
        backend=build_backend(workspace, skills_dir=skills_dir),
        checkpointer=MemorySaver(),
        skills=["/skills/"] if skills_dir is not None else None,
    )


class InteractiveStreamPrinter:
    """Render reasoning, tool activity, and final answer without duplicates."""

    def __init__(self) -> None:
        self._section: str | None = None

    def _start_section(self, title: str, *, force: bool = False) -> None:
        if self._section == title and not force:
            return
        self._section = title
        print(f"\n{title}\n{'-' * len(title)}", flush=True)

    @staticmethod
    def _split_content(content: Any) -> tuple[list[str], list[str]]:
        reasoning: list[str] = []
        answer: list[str] = []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "thinking":
                    reasoning.append(str(block.get("thinking", "")))
                elif block.get("type") == "text":
                    answer.append(str(block.get("text", "")))
            return reasoning, answer
        if content:
            answer.append(str(content))
        return reasoning, answer

    @staticmethod
    def _format_content(content: Any) -> str:
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(str(block.get("text", "")))
                    elif block.get("type") == "thinking":
                        parts.append(str(block.get("thinking", "")))
                    else:
                        parts.append(json.dumps(block, ensure_ascii=False))
                else:
                    parts.append(str(block))
            return "\n".join(part for part in parts if part)
        if content is None:
            return ""
        return str(content)

    def _handle_message_chunk(self, chunk: Any) -> None:
        if not isinstance(chunk, AIMessageChunk):
            return
        content_reasoning, answer = self._split_content(chunk.content)
        reasoning = [extract_reasoning(chunk.additional_kwargs), *content_reasoning]
        if reasoning and any(reasoning):
            self._start_section("🧠 推理过程")
            for part in reasoning:
                if part:
                    print(part, end="", flush=True)
        if any(answer):
            self._start_section("✅ 正式回答")
            for part in answer:
                if part:
                    print(part, end="", flush=True)

    def _handle_updates(self, updates: Any) -> None:
        if not isinstance(updates, dict):
            return
        for node_update in updates.values():
            if not isinstance(node_update, dict):
                continue
            for message in node_update.get("messages", []):
                if isinstance(message, AIMessage):
                    for tool_call in message.tool_calls or []:
                        name = tool_call.get("name", "unknown")
                        arguments = tool_call.get("args", {})
                        self._start_section(f"🔧 工具调用: {name}", force=True)
                        print(
                            json.dumps(arguments, ensure_ascii=False, indent=2),
                            flush=True,
                        )
                elif isinstance(message, ToolMessage):
                    name = message.name or "unknown"
                    self._start_section(f"📥 工具结果: {name}", force=True)
                    result = self._format_content(message.content)
                    print(result or "<empty>", flush=True)

    def handle(self, mode: str, data: Any) -> None:
        if mode == "messages" and isinstance(data, tuple) and len(data) == 2:
            self._handle_message_chunk(data[0])
        elif mode == "updates":
            self._handle_updates(data)


def print_stream_event(printer: InteractiveStreamPrinter, mode: str, data: Any) -> None:
    printer.handle(mode, data)


async def run_interactive(agent: Any, session_name: str) -> None:
    config = {"configurable": {"thread_id": session_name}}
    print("多轮对话已启动。可尝试：")
    print('  1. 请写入 /allowed.txt，内容为 session-only')
    print("  2. 请读取 /allowed.txt")
    print("  3. 请读取 /../host-secret.txt")
    print("  4. 请读取 /etc/passwd")
    print("  5. 请执行 pwd 并说明当前目录")
    print("  6. 请列出可用 Skills")
    print("输入 exit/quit 或按 Ctrl+C 结束。\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAgent: 已退出。")
            return
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            print("Agent: 已退出。")
            return

        print("Agent:", flush=True)
        printer = InteractiveStreamPrinter()
        try:
            async for mode, data in agent.astream(
                {"messages": [{"role": "user", "content": user_input}]},
                config=config,
                stream_mode=["messages", "updates"],
            ):
                print_stream_event(printer, mode, data)
        except Exception as exc:
            print(f"\n[stream error] {exc}")
        print("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test LocalShellBackend workspace and skills routes"
    )
    target_group = parser.add_mutually_exclusive_group()
    target_group.add_argument(
        "--workspace",
        help="Workspace directory exposed as `/`",
    )
    target_group.add_argument(
        "--root",
        dest="workspace",
        help="Alias of --workspace for compatibility",
    )
    parser.add_argument(
        "--config",
        help="YAML config path (defaults to config.dev.yaml, then config.yaml)",
    )
    parser.add_argument(
        "--model",
        help="Model key from the config's models section",
    )
    parser.add_argument(
        "--skills-dir",
        help="Skills directory mapped to `/skills/` (defaults to config.dev.yaml tools.skills_dir)",
    )
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    config = load_dev_config(args.config)
    model = create_model(config, args.model)
    workspace = (
        Path(args.workspace).expanduser().resolve()
        if args.workspace
        else PROJECT_ROOT / "workspace" / "virtual_root_demo"
    )
    workspace.mkdir(parents=True, exist_ok=True)
    skills_dir = resolve_skills_dir(config, args.skills_dir)
    session_name = workspace.name or "virtual-root-session"
    agent = build_agent(
        workspace=workspace,
        model=model,
        session_name=session_name,
        skills_dir=skills_dir,
    )

    print(f"Config model: {args.model or config.active_model}")
    print(f"Workspace virtual root `/` maps to: {workspace}")
    print(f"Skills virtual root `/skills/` maps to: {skills_dir}")
    print("Filesystem virtual roots reject parent traversal.")
    print("Note: LocalShellBackend execution is not filesystem sandbox isolation.\n")
    await run_interactive(agent, session_name)


if __name__ == "__main__":
    asyncio.run(async_main())
