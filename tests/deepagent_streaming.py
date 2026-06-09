"""
DeepAgents 流式输出 Demo
演示如何使用 messages 模式实现逐 token 的流式输出
复用项目配置（api_key, api_base, model 等）
"""
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from easy_agent.config import Config
from easy_agent.model import create_model
from deepagents import create_deep_agent


def get_llm_config():
    """从项目配置获取 LLM 配置"""
    config_path = project_root / "easy_agent" / "config" / "config.yaml"
    if not config_path.exists():
        print(f"❌ 配置文件不存在: {config_path}")
        sys.exit(1)
    
    print(f"📄 加载配置文件: {config_path}")
    config = Config.from_yaml(config_path)
    
    print("✅ 配置信息:")
    print(f"   - Provider: {config.llm.provider}")
    print(f"   - Model: {config.llm.model}")
    print(f"   - API Base: {config.llm.api_base}")
    print(f"   - API Key: {config.llm.api_key[:20]}...")
    
    return config


async def test_streaming_messages():
    """使用 messages 模式测试流式输出"""
    print("=" * 80)
    print("测试 1: messages 模式（逐 token 流式）")
    print("=" * 80)
    
    # 获取项目配置
    config = get_llm_config()
    
    # 创建模型对象
    print("\n🤖 创建模型对象...")
    model = create_model(config)
    print(f"✅ 模型对象创建成功: {type(model).__name__}")
    
    # 创建 Agent
    print("🤖 创建 DeepAgent...")
    agent = create_deep_agent(
        model=model,
        system_prompt="你是一个友好的助手。回答用户问题时，先进行思考，然后给出正式回复。",
    )
    print("✅ Agent 创建成功")
    
    messages = [{"role": "user", "content": "你好，请介绍一下你自己"}]
    
    accumulated_content = ""
    
    print("\n开始流式输出：\n")
    
    async for chunk in agent.astream(
        {"messages": messages},
        stream_mode="messages",
        subgraphs=True,
        version="v2",
    ):
        if chunk["type"] != "messages":
            continue
        
        token, metadata = chunk["data"]
        ns = chunk.get("ns", [])
        
        # 只处理主 agent 的消息
        is_subagent = any(s.startswith("tools:") for s in ns)
        if is_subagent:
            continue
        
        # 打印调试信息
        token_type = token.type if hasattr(token, 'type') else type(token).__name__
        content = getattr(token, 'content', '')
        print(f"[DEBUG] type={token_type}, content_type={type(content).__name__}, content_length={len(str(content)) if content else 0}")
        if content and len(str(content)) < 100:
            print(f"[DEBUG] content预览: {content}")
        
        # 处理 AI 内容
        if content:
            content_str = str(content)
            accumulated_content += content_str
            # 实时打印 token
            print(content_str, end="", flush=True)
        
        # 处理工具调用
        if hasattr(token, "tool_call_chunks") and token.tool_call_chunks:
            for tc_chunk in token.tool_call_chunks:
                if tc_chunk.get("name"):
                    print(f"\n[工具调用] {tc_chunk['name']}")
                if tc_chunk.get("args"):
                    print(tc_chunk["args"], end="", flush=True)
        
        # 处理工具结果
        if token.type == "tool":
            print(f"\n[工具结果] {token.name}: {str(token.content)[:200]}")
    
    print("\n\n" + "=" * 80)
    print(f"累积内容总长度: {len(accumulated_content)}")
    print("=" * 80)
    
    # 提取思考内容和正式内容
    import re
    
    think_pattern = r'<think[^>]*>(.*?)</think[^>]*>'
    matches = re.findall(think_pattern, accumulated_content, re.IGNORECASE | re.DOTALL)
    
    if matches:
        thinking = "\n".join([m.strip() for m in matches if m.strip()])
        print("\n🤔 思考内容：")
        print("-" * 80)
        print(thinking)
        print("-" * 80)
        
        # 提取正式回复
        response = re.sub(think_pattern, '', accumulated_content, flags=re.IGNORECASE | re.DOTALL).strip()
        print("\n💬 正式回复：")
        print("-" * 80)
        print(response)
        print("-" * 80)
    else:
        print("\n未找到思考标签，完整内容：")
        print(accumulated_content)


async def test_streaming_updates():
    """使用 updates 模式测试（按节点更新）"""
    print("\n\n" + "=" * 80)
    print("测试 2: updates 模式（按节点更新）")
    print("=" * 80)
    
    # 获取项目配置
    config = get_llm_config()
    model = create_model(config)
    
    agent = create_deep_agent(
        model=model,
        system_prompt="你是一个友好的助手。",
    )
    
    messages = [{"role": "user", "content": "你好"}]
    
    print("\n开始 updates 流式输出：\n")
    
    async for chunk in agent.astream(
        {"messages": messages},
        stream_mode="updates",
        subgraphs=True,
        version="v2",
    ):
        if chunk["type"] != "updates":
            continue
        
        ns = chunk.get("ns", [])
        is_subagent = any(s.startswith("tools:") for s in ns)
        
        print(f"\n[{'子agent' if is_subagent else '主agent'}] 节点更新:")
        
        for node_name, data in chunk["data"].items():
            print(f"  节点: {node_name}")
            if "messages" in data:
                for msg in data["messages"]:
                    msg_type = getattr(msg, "type", "unknown")
                    content = getattr(msg, "content", "")
                    print(f"    消息类型: {msg_type}, 内容长度: {len(str(content))}")
                    if content and len(str(content)) < 200:
                        print(f"    内容: {content}")


async def test_streaming_combined():
    """同时使用多种流式模式"""
    print("\n\n" + "=" * 80)
    print("测试 3: 多模式组合流式")
    print("=" * 80)
    
    # 获取项目配置
    config = get_llm_config()
    model = create_model(config)
    
    agent = create_deep_agent(
        model=model,
        system_prompt="你是一个友好的助手。",
    )
    
    messages = [{"role": "user", "content": "你好，请做个自我介绍"}]
    
    print("\n开始多模式流式输出：\n")
    
    async for chunk in agent.astream(
        {"messages": messages},
        stream_mode=["messages", "updates"],
        subgraphs=True,
        version="v2",
    ):
        ns = chunk.get("ns", [])
        is_subagent = any(s.startswith("tools:") for s in ns)
        source = "子agent" if is_subagent else "主agent"
        
        if chunk["type"] == "messages":
            token, metadata = chunk["data"]
            
            if token.type == "ai" and token.content:
                # 流式打印内容
                print(token.content, end="", flush=True)
            
            elif token.type == "tool":
                print(f"\n[{source}] 工具结果: {token.name}")
        
        elif chunk["type"] == "updates":
            for node_name in chunk["data"]:
                print(f"\n[{source}] 步骤: {node_name}")
    
    print("\n\n完成！")


async def main():
    """主函数"""
    # 测试 1: messages 模式（推荐用于逐 token 流式）
    await test_streaming_messages()
    
    # 测试 2: updates 模式（按节点更新）
    # await test_streaming_updates()
    
    # 测试 3: 多模式组合
    # await test_streaming_combined()


if __name__ == "__main__":
    asyncio.run(main())
