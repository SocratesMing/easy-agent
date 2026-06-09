"""
DeepAgents 纯净流式输出测试脚本

不传入 skills 和 memory 路径，只使用 DeepAgents 内置工具，
逐步输出各个节点的消息：思考内容、工具参数名称和执行结果、正式内容。

用法:
    python tests/test_v4_pure_agent.py
    python tests/test_v4_pure_agent.py --prompt "帮我写一个hello world的python脚本"
"""
import asyncio
import json
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from easy_agent.config import Config
from easy_agent.model import create_model
import easy_agent.agent  # noqa: F401  触发 read_file 全量读取 monkey patch
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend


def load_config():
    config_path = project_root / "easy_agent" / "config" / "config.yaml"
    if not config_path.exists():
        print(f"配置文件不存在: {config_path}")
        sys.exit(1)
    return Config.from_yaml(config_path)


SEP = "=" * 70
THIN_SEP = "-" * 70


def _build_workspace_backend(workspace_dir: Path):
    print(f"Workspace directory: {workspace_dir.absolute()}")
    workspace_backend = FilesystemBackend(
        root_dir=str(workspace_dir.absolute()),
        virtual_mode=True,
    )

    routes = {
        "/workspace/": workspace_backend,
    }

    def backend_factory(runtime):
        return CompositeBackend(
            default=StateBackend(runtime),
            routes=routes,
        )

    return backend_factory


async def run_agent(user_input: str):
    config = load_config()
    model = create_model(config)

    workspace_dir = project_root / "workspace" / "test_v4"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    system_prompt = (
        "你是一个全栈 AI 助手，能够帮助用户完成编程、写作等任务。\n"
        f"## Workspace 实际路径: {workspace_dir.absolute()}\n"
        f"## OS: Linux\n"
        "\n"
        "## 文件系统说明（重要）\n"
        "- 使用文件相关工具（ls / read_file / write_file / glob / grep / edit）时，**必须使用虚拟路径**：\n"
        "  - workspace 目录的虚拟路径前缀为 `/workspace/`\n"
        "  - 例如：列出 workspace 下的文件 → `ls(path=\"/workspace/\")`\n"
        "  - 例如：写文件 → `write_file(file_path=\"/workspace/hello.py\", content=\"...\")`\n"
        "- 不要使用真实绝对路径（如 /home/sututu/...），那样会路由到内存状态后端，返回空。\n"
        "- 如果需要执行系统命令（如查看真实路径下的文件），使用 `execute` / shell 工具。\n"
    )

    backend = _build_workspace_backend(workspace_dir)

    print(f"\n{SEP}")
    print(f"📋 系统提示词:\n{system_prompt}")
    print(SEP)
    print(f"🏗️ 创建智能体参数 | model: {config.llm.model} | provider: {config.llm.provider} | protocol: {config.llm.protocol}")
    print(f"  workspace: {workspace_dir.absolute()}")
    print(f"  skills: None | memory: None | mcp_tools: 0")
    print(SEP)
    print(f"\n用户: {user_input}\n")

    agent = create_deep_agent(
        name="test-v4-agent",
        model=model,
        system_prompt=system_prompt,
        backend=backend,
        skills="/home/sututu/code/easy-agent/easy_agent/skills",
        memory="./memories/tests/test.md",
    )

    start_time = time.time()

    current_step = 0
    in_reasoning = False
    reasoning_done = False
    tool_call_accumulated = {}
    step_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    async for event in agent.astream(
        {"messages": [{"role": "user", "content": user_input}]},
        stream_mode="messages",
    ):
        chunk, metadata = event
        node = metadata.get("langgraph_node", "?")

        # ── Step 检测 ──────────────────────────────────────────────
        step_meta = metadata.get("step", 0)
        if step_meta != current_step:
            if current_step > 0 and step_usage["total_tokens"] > 0:
                print(f"\n{THIN_SEP}")
                print(
                    f"📊 Step {current_step} Token | "
                    f"输入: {step_usage['input_tokens']} | 输出: {step_usage['output_tokens']} | "
                    f"合计: {step_usage['total_tokens']} | 累计总 Token: {total_usage['total_tokens']}"
                )
            current_step = step_meta
            step_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            print(f"\n{THIN_SEP}")
            print(f"📌 Step {current_step} | 节点: {node}")

        # ── Token 统计 ─────────────────────────────────────────────
        if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
            um = chunk.usage_metadata
            step_usage["input_tokens"] += um.get("input_tokens", 0)
            step_usage["output_tokens"] += um.get("output_tokens", 0)
            step_usage["total_tokens"] += um.get("total_tokens", 0)
            total_usage["input_tokens"] += um.get("input_tokens", 0)
            total_usage["output_tokens"] += um.get("output_tokens", 0)
            total_usage["total_tokens"] += um.get("total_tokens", 0)

        # ── ToolMessage（工具执行结果）─────────────────────────────
        chunk_type = type(chunk).__name__
        if chunk_type == "ToolMessage":
            tool_name = getattr(chunk, "name", "") or ""
            tool_call_id = getattr(chunk, "tool_call_id", "") or ""

            # 打印工具调用参数（在收到结果时参数一定已完整）
            if tool_call_id and tool_call_id in tool_call_accumulated:
                stored = tool_call_accumulated[tool_call_id]
                if not stored.get("_printed"):
                    stored["_printed"] = True
                    stored_name = stored.get("name", tool_name)
                    merged = stored.get("merged_args", {})
                    print(f"\n🔧 工具调用: {stored_name}")
                    print(f"  ID: {tool_call_id}")
                    if merged:
                        for k, v in merged.items():
                            val_str = str(v)
                            if len(val_str) > 500:
                                val_str = val_str[:500] + "..."
                            print(f"  参数 [{k}]: {val_str}")
                    else:
                        print(f"  参数: (无)")

            raw_content = chunk.content
            if isinstance(raw_content, list):
                parts = []
                for item in raw_content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    elif hasattr(item, "text"):
                        parts.append(str(item.text))
                    else:
                        parts.append(str(item))
                result = "\n".join(parts) if parts else str(raw_content)
            else:
                result = str(raw_content) if raw_content else ""

            is_error = getattr(chunk, "additional_kwargs", {}).get("is_error", False)
            if "Error invoking tool" in result:
                is_error = True
            status = "❌ 失败" if is_error else "✅ 成功"

            if len(result) > 2000:
                result_display = result[:2000] + f"\n  ... (共 {len(result)} 字符)"
            else:
                result_display = result

            print(f"\n📊 工具结果 [{tool_name}] (id: {tool_call_id}, {status}):")
            print(f"  {result_display}")
            continue

        # ── AIMessageChunk 处理 ────────────────────────────────────
        if chunk_type != "AIMessageChunk":
            continue

        # ── 工具调用（流式分片累积）──────────────────────────────────
        tcc = getattr(chunk, "tool_call_chunks", None) or []
        for t in tcc:
            name = t.get("name", "") or ""
            args_str = str(t.get("args", "") or "")
            tid = t.get("id", "") or ""

            if name:
                if tid not in tool_call_accumulated:
                    tool_call_accumulated[tid] = {"name": name, "args_str": ""}
                tool_call_accumulated[tid]["name"] = name

            if tid and args_str:
                if tid not in tool_call_accumulated:
                    tool_call_accumulated[tid] = {"name": name or "", "args_str": ""}
                tool_call_accumulated[tid]["args_str"] += args_str

        # ── 完整工具调用（流式结束时触发）──────────────────────────
        if hasattr(chunk, "tool_calls") and chunk.tool_calls:
            for tc in chunk.tool_calls:
                tc_name = tc.get("name", "") or ""
                tc_args = tc.get("args", {})
                tc_id = tc.get("id") or ""

                if not tc_name and not tc_args:
                    continue

                merged_args = {}
                matched_id = tc_id
                matched_name = tc_name

                if tc_id and tc_id in tool_call_accumulated:
                    stored = tool_call_accumulated[tc_id]
                    if not matched_name and stored.get("name"):
                        matched_name = stored["name"]
                    stored_input = stored.get("input", {})
                    if stored_input and stored_input != {}:
                        merged_args.update(stored_input)
                    raw = stored.get("args_str", "")
                    if raw and raw != "{}":
                        try:
                            parsed = json.loads(raw)
                            if isinstance(parsed, dict):
                                merged_args.update(parsed)
                        except (json.JSONDecodeError, ValueError):
                            pass
                elif not tc_id and tc_args:
                    for tid, stored in tool_call_accumulated.items():
                        if stored.get("_printed"):
                            continue
                        if stored.get("name") and not stored.get("_has_args"):
                            matched_id = tid
                            matched_name = stored["name"]
                            stored_input = stored.get("input", {})
                            if stored_input and stored_input != {}:
                                merged_args.update(stored_input)
                            break

                if tc_args and tc_args != {}:
                    merged_args.update(tc_args)

                if matched_name and matched_id:
                    tool_call_accumulated.setdefault(matched_id, {})
                    tool_call_accumulated[matched_id]["name"] = matched_name
                    if tc_args and tc_args != {}:
                        tool_call_accumulated[matched_id]["_has_args"] = True
                        tool_call_accumulated[matched_id]["merged_args"] = merged_args
                    else:
                        tool_call_accumulated[matched_id]["_has_args"] = tool_call_accumulated[matched_id].get("_has_args", False)
                        if merged_args:
                            tool_call_accumulated[matched_id]["merged_args"] = merged_args

        # ── 思考内容（两种格式）─────────────────────────────────────
        # 格式1: DeepSeek/OpenAI — additional_kwargs.reasoning_content
        rc = ""
        if hasattr(chunk, "additional_kwargs"):
            rc = chunk.additional_kwargs.get("reasoning_content", "")

        if rc:
            if not in_reasoning:
                print("🧠 思考: ", end="", flush=True)
                in_reasoning = True
            print(rc, end="", flush=True)
            continue

        # 格式2: Anthropic — content blocks list
        content = chunk.content
        if isinstance(content, list) and content:
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")

                if block_type == "thinking":
                    thinking_text = block.get("thinking", "")
                    if thinking_text:
                        if not in_reasoning:
                            print("🧠 思考: ", end="", flush=True)
                            in_reasoning = True
                        print(thinking_text, end="", flush=True)

                elif block_type == "text":
                    text = block.get("text", "")
                    if text:
                        if in_reasoning and not reasoning_done:
                            print()
                            reasoning_done = True
                            in_reasoning = False
                        print(text, end="", flush=True)

                elif block_type == "tool_use":
                    tool_name = block.get("name", "")
                    tool_id = block.get("id", "")
                    tool_input = block.get("input", {})
                    if tool_id not in tool_call_accumulated:
                        tool_call_accumulated[tool_id] = {"name": tool_name, "args_str": "", "input": tool_input}
                    else:
                        tool_call_accumulated[tool_id]["name"] = tool_name
                        if tool_input and tool_input != {}:
                            tool_call_accumulated[tool_id]["input"] = tool_input
            continue

        # 格式3: 纯文本 content（str）
        if isinstance(content, str) and content and not tcc:
            if in_reasoning and not reasoning_done:
                print()
                reasoning_done = True
                in_reasoning = False
            print(content, end="", flush=True)

    # ── 最终统计 ───────────────────────────────────────────────────
    if step_usage["total_tokens"] > 0:
        print(f"\n{THIN_SEP}")
        print(
            f"📊 Step {current_step} Token | "
            f"输入: {step_usage['input_tokens']} | 输出: {step_usage['output_tokens']} | "
            f"合计: {step_usage['total_tokens']} | 累计总 Token: {total_usage['total_tokens']}"
        )

    elapsed = time.time() - start_time
    print(f"\n{SEP}")
    print(
        f"✅ 完成 | 总步骤: {current_step} | 耗时: {elapsed:.2f}s | "
        f"总 Token: {total_usage['total_tokens']} "
        f"(输入: {total_usage['input_tokens']} | 输出: {total_usage['output_tokens']})"
    )
    print(SEP)


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="DeepAgents 纯净流式输出测试")
    parser.add_argument("--prompt", type=str, default="帮我写一个智能体介绍的docx文件", help="用户输入")
    args = parser.parse_args()

    await run_agent(args.prompt)


if __name__ == "__main__":
    asyncio.run(main())
