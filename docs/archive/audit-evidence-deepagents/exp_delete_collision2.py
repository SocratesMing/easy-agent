import warnings; warnings.filterwarnings("ignore")
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain.tools import tool
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from langgraph.checkpoint.memory import MemorySaver

@tool
def delete(record_id: str) -> str:
    """Delete a CRM record (pretend MCP tool)."""
    return f"CUSTOM delete ran for CRM record {record_id}"

class FakeModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kw):
        for t in tools:
            n = getattr(t, "name", None)
            if n == "delete":
                schema = t.args if hasattr(t, "args") else t
                print("BOUND delete schema args:", list(schema.keys()) if isinstance(schema, dict) else schema)
        return self

msgs = iter([
    AIMessage(content="", tool_calls=[{"name": "delete", "args": {"record_id": "42"}, "id": "c1"}]),
    AIMessage(content="done"),
])
model = FakeModel(messages=msgs)
agent = create_deep_agent(model=model, tools=[delete], backend=StateBackend(), checkpointer=MemorySaver())
out = agent.invoke({"messages": [{"role": "user", "content": "delete record 42"}]}, config={"configurable": {"thread_id": "t"}})
for m in out["messages"]:
    if m.type == "tool":
        print("TOOL RESULT:", m.name, "|", str(m.content)[:200])
