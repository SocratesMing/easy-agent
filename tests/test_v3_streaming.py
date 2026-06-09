"""
LangChain 流式消息输出测试脚本（基于 DeepAgents 框架）

使用 deepagents.create_deep_agent + easy_agent.model.create_model
通过 agent.astream(stream_mode='messages') 获取流式消息，
输出思考内容、正式内容、工具调用信息（名称、参数、结果）。

用法:
    python tests/test_v3_streaming.py                    # 基础对话
    python tests/test_v3_streaming.py --with-tools       # 工具调用
    python tests/test_v3_streaming.py --all              # 全部测试
"""
import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from easy_agent.config import Config
from easy_agent.model import create_model
from deepagents import create_deep_agent
from langchain_core.tools import tool


def load_config():
    config_path = project_root / "easy_agent" / "config" / "config.yaml"
    if not config_path.exists():
        print(f"配置文件不存在: {config_path}")
        sys.exit(1)
    return Config.from_yaml(config_path)


# ── 工具定义 ──────────────────────────────────────────────────────────

@tool
def get_weather(city: str) -> str:
    """获取指定城市的天气信息"""
    return f"{city}今天晴朗，气温25°C，微风。"


@tool
def calculate(expression: str) -> str:
    """计算数学表达式"""
    try:
        result = eval(expression)  # noqa: S307
        return f"{expression} = {result}"
    except Exception as e:
        return f"计算错误: {e}"


# ── 分隔线 ──────────────────────────────────────────────────────────

SEP = "=" * 70
THIN_SEP = "-" * 70


def _print_model_info(config):
    provider = config.llm.provider
    model_name = config.llm.model
    print(f"[模型] provider={provider} model={model_name}")


def _stream_chunk(chunk, node):
    """处理单个消息块，返回是否有关注的内容"""
    rc = chunk.additional_kwargs.get("reasoning_content", "") if hasattr(chunk, "additional_kwargs") else ""
    content = chunk.content or ""
    tcc = getattr(chunk, "tool_call_chunks", None) or []
    is_tool_msg = "ToolMessage" in type(chunk).__name__

    has_output = False

    if rc:
        print(rc, end="", flush=True)
        has_output = True

    if content and not tcc and not is_tool_msg:
        print(content, end="", flush=True)
        has_output = True

    for t in tcc:
        name = t.get("name", "") or ""
        args = str(t.get("args", "") or "")[:80]
        t.get("id", "") or ""
        if name:
            print(f"\n🔧 工具: {name}", flush=True)
            if args and args not in ("{}", "", "None"):
                print(f"  参数: {args}", flush=True)
            has_output = True

    if is_tool_msg:
        name = getattr(chunk, "name", "") or ""
        result = str(chunk.content)[:300]
        status = "成功" if not getattr(chunk, "additional_kwargs", {}).get("is_error") else "失败"
        print(f"📊 结果 [{name}] ({status}): {result}", flush=True)
        has_output = True

    return has_output


async def test_basic():
    """测试 1: 基础对话 — 思考 + 正文"""
    print(f"\n{SEP}")
    print("测试 1: 基础对话 (思考 + 正文)")
    print(SEP)

    config = load_config()
    _print_model_info(config)
    model = create_model(config)

    agent = create_deep_agent(
        model=model,
        system_prompt="你是一个会深度思考的助手。请先用中文思考，再给出最终答案。",
    )

    user_input = "请用三句话介绍人工智能。"
    print(f"\n用户: {user_input}\n")

    in_reasoning = False
    reasoning_done = False

    async for event in agent.astream(
        {"messages": [{"role": "user", "content": user_input}]},
        stream_mode="messages",
    ):
        chunk, metadata = event
        node = metadata.get("langgraph_node", "?")

        rc = chunk.additional_kwargs.get("reasoning_content", "") if hasattr(chunk, "additional_kwargs") else ""

        if rc:
            if not in_reasoning:
                print("🧠 ", end="", flush=True)
                in_reasoning = True
            print(rc, end="", flush=True)
        elif not in_reasoning:
            _stream_chunk(chunk, node)
        else:
            if not reasoning_done:
                print()  # reasoning → text 分隔
                reasoning_done = True
            _stream_chunk(chunk, node)

    print(f"\n{THIN_SEP}")
    print("测试 1 完成")


async def test_tool_calls():
    """测试 2: 工具调用 — 思考 + 工具信息 + 结果 + 正文"""
    print(f"\n{SEP}")
    print("测试 2: 工具调用流式输出")
    print(SEP)

    config = load_config()
    _print_model_info(config)
    model = create_model(config)

    agent = create_deep_agent(
        model=model,
        system_prompt="你是一个友好的助手。请用中文回答。当用户询问天气或需要计算时，使用提供的工具。",
        tools=[get_weather, calculate],
    )

    user_input = "北京天气怎么样？顺便帮我算一下 123 * 456"
    print(f"\n用户: {user_input}\n")

    in_reasoning = False
    reasoning_done = False

    async for event in agent.astream(
        {"messages": [{"role": "user", "content": user_input}]},
        stream_mode="messages",
    ):
        chunk, metadata = event
        node = metadata.get("langgraph_node", "?")

        rc = chunk.additional_kwargs.get("reasoning_content", "") if hasattr(chunk, "additional_kwargs") else ""

        if rc:
            if not in_reasoning:
                print("🧠 ", end="", flush=True)
                in_reasoning = True
            print(rc, end="", flush=True)
        elif not in_reasoning:
            _stream_chunk(chunk, node)
        else:
            if not reasoning_done:
                print()  # reasoning → text 分隔
                reasoning_done = True
            _stream_chunk(chunk, node)

    print(f"\n{THIN_SEP}")
    print("测试 2 完成")


async def test_state_snapshots():
    """测试 3: 状态快照 — agent.astream() 默认模式"""
    print(f"\n{SEP}")
    print("测试 3: 状态快照 (agent.astream 默认模式)")
    print(SEP)

    config = load_config()
    _print_model_info(config)
    model = create_model(config)

    agent = create_deep_agent(
        model=model,
        system_prompt="你是一个友好的助手。请用中文回答。",
        tools=[get_weather],
    )

    user_input = "上海天气如何？"
    print(f"\n用户: {user_input}\n")

    snapshot_count = 0
    async for state in agent.astream(
        {"messages": [{"role": "user", "content": user_input}]},
        stream_mode="values",
    ):
        snapshot_count += 1
        messages = state.get("messages", [])
        if not messages:
            continue

        last = messages[-1]
        t = type(last).__name__

        if t == "HumanMessage":
            print(f"  [快照 #{snapshot_count}] 用户输入: {last.content[:80]}", flush=True)
        elif t == "AIMessage":
            blocks = getattr(last, "content_blocks", None) or []
            summary = []
            for b in blocks:
                if isinstance(b, dict):
                    bt = b.get("type", "")
                    if bt == "reasoning":
                        summary.append(f"思考({len(b.get('reasoning',''))}字)")
                    elif bt == "text":
                        summary.append(f"正文({len(b.get('text',''))}字)")
                    elif bt == "tool_call":
                        summary.append(f"工具:{b.get('name','')}")
            print(f"  [快照 #{snapshot_count}] AI: {' | '.join(summary) if summary else str(last.content)[:80]}", flush=True)
        elif t == "ToolMessage":
            tool_name = getattr(last, "name", "") or ""
            result = str(last.content)[:100]
            print(f"  [快照 #{snapshot_count}] 工具[{tool_name}]: {result}", flush=True)

    print(f"\n  共 {snapshot_count} 次状态快照")

    print(f"\n{THIN_SEP}")
    print("测试 3 完成")


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="LangChain 流式消息输出测试（DeepAgents）")
    parser.add_argument("--with-tools", action="store_true", help="运行工具调用测试")
    parser.add_argument("--state", action="store_true", help="运行状态快照测试")
    parser.add_argument("--all", action="store_true", help="运行所有测试")
    args = parser.parse_args()

    if args.all or not any([args.with_tools, args.state]):
        await test_basic()

    if args.with_tools or args.all:
        await test_tool_calls()

    if args.state or args.all:
        await test_state_snapshots()

    print(f"\n{SEP}")
    print("所有测试完成！")
    print(SEP)


if __name__ == "__main__":
    asyncio.run(main())