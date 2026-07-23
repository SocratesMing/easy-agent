"""
测试：使用 deepagents 创建模型（连接 dev 配置文件），流式打印
「正文内容」「推理内容」以及「token 用量」。

说明：
  - 配置：优先加载 easy_agent/config/config.dev.yaml，回退到 config.yaml。
  - 模型：用 create_model(config) 创建（内置 reasoning_content 提取）。
  - 推理：兼容 OpenAI/DeepSeek 的 reasoning_content 与 Anthropic 的 thinking block。
  - token：开启 stream_options.include_usage，流式结束后打印 input/output/total。

运行：
    python tests/test_dev_streaming.py
    python tests/test_dev_streaming.py --prompt "你的问题"
"""
import argparse
import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from easy_agent.config import Config
from easy_agent.model import create_model
import easy_agent.agent  # noqa: F401  触发 read_file 全量读取 monkey patch
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend

SEP = "=" * 70
THIN = "-" * 70


def load_dev_config() -> Config:
    """加载 dev 配置文件（config.dev.yaml），回退到 config.yaml。"""
    path = Config.find_config_file("config.dev.yaml") or Config.find_config_file(
        "config.yaml"
    )
    if path is None:
        raise FileNotFoundError("未找到 config.dev.yaml 或 config.yaml")
    print(f"✅ 加载配置文件: {path}")
    return Config.from_yaml(path)


def _build_backend(workspace_dir: Path):
    """构造 deepagents 的文件系统 + 状态复合后端（虚拟路径 /workspace/）。"""
    fs_backend = FilesystemBackend(
        root_dir=str(workspace_dir.absolute()),
        virtual_mode=True,
    )
    routes = {"/workspace/": fs_backend}

    def backend_factory(runtime):
        return CompositeBackend(
            default=StateBackend(runtime),
            routes=routes,
        )

    return backend_factory


async def run_agent(user_input: str, config: Config, model) -> None:
    workspace_dir = project_root / "workspace" / "test_dev_streaming"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    system_prompt = (
        "你是一个全栈 AI 助手，能够帮助用户完成编程、写作等任务。\n"
        f"## Workspace 实际路径: {workspace_dir.absolute()}\n"
        "## 文件系统说明：使用文件相关工具时，请使用虚拟路径前缀 /workspace/。\n"
    )

    backend = _build_backend(workspace_dir)

    print(f"\n{SEP}")
    print(f"📦 模型: {config.active_model} / {config.llm.model}")
    print(SEP)
    print(f"\n💬 用户: {user_input}\n")

    agent = create_deep_agent(
        name="test-dev-streaming",
        model=model,
        system_prompt=system_prompt,
        backend=backend,
    )

    in_reasoning = False
    reasoning_done = False
    last_usage = None  # 流式结束后取最后一次 usage_metadata 作为总量

    async for event in agent.astream(
        {"messages": [{"role": "user", "content": user_input}]},
        stream_mode="messages",
    ):
        chunk, _metadata = event
        chunk_type = type(chunk).__name__

        # 工具执行结果与本测试无关，跳过
        if chunk_type == "ToolMessage":
            continue
        if chunk_type != "AIMessageChunk":
            continue

        # ── token 用量（最后一次非空的 usage_metadata 即为总量）──
        um = getattr(chunk, "usage_metadata", None)
        if um:
            last_usage = um

        # ── 推理内容：OpenAI / DeepSeek 的 reasoning_content ──
        rc = ""
        if hasattr(chunk, "additional_kwargs"):
            rc = chunk.additional_kwargs.get("reasoning_content", "")
        if rc:
            if not in_reasoning:
                print("🧠 [推理内容]\n", end="", flush=True)
                in_reasoning = True
            print(rc, end="", flush=True)
            continue

        # ── 推理内容：Anthropic 的 thinking block / 正文 text block ──
        content = chunk.content
        if isinstance(content, list) and content:
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "thinking":
                    t = block.get("thinking", "")
                    if t:
                        if not in_reasoning:
                            print("🧠 [推理内容]\n", end="", flush=True)
                            in_reasoning = True
                        print(t, end="", flush=True)
                elif block_type == "text":
                    txt = block.get("text", "")
                    if txt:
                        if in_reasoning and not reasoning_done:
                            print("\n")
                            reasoning_done = True
                            in_reasoning = False
                        print(txt, end="", flush=True)
            continue

        # ── 正文内容（纯文本 str）──
        if isinstance(content, str) and content:
            if in_reasoning and not reasoning_done:
                print("\n")
                reasoning_done = True
                in_reasoning = False
            print(content, end="", flush=True)

    # ── 打印 token 用量 ──
    print(f"\n\n{SEP}")
    print("📊 Token 用量:")
    if last_usage:
        print(f"  输入  tokens: {last_usage.get('input_tokens')}")
        print(f"  输出  tokens: {last_usage.get('output_tokens')}")
        print(f"  总计  tokens: {last_usage.get('total_tokens')}")
        details = last_usage.get("output_token_details")
        if details:
            print(f"  推理  tokens: {details.get('reasoning')}")
    else:
        print(
            "  （未返回 usage_metadata，请确认模型支持 stream_options.include_usage）"
        )
    print(SEP)


async def main() -> None:
    parser = argparse.ArgumentParser(description="deepagents 流式打印测试（dev 配置）")
    parser.add_argument(
        "--prompt",
        type=str,
        default="请用 step by step 的方式解释如何泡一杯好喝的茶",
        help="用户输入",
    )
    args = parser.parse_args()

    config = load_dev_config()
    model = create_model(config)

    # 确保流式响应携带 token 用量统计
    try:
        model = model.model_copy(update={"stream_options": {"include_usage": True}})
    except Exception as e:  # pragma: no cover
        print(f"⚠️ 设置 stream_options 失败（token 用量可能为空）: {e}")

    await run_agent(args.prompt, config, model)


if __name__ == "__main__":
    asyncio.run(main())
