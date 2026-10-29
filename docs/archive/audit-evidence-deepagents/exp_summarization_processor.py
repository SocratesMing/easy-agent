import sys
"""Exp 3: feed the same stream into the project's StreamProcessor."""
import asyncio, time, warnings, sys; warnings.filterwarnings("ignore")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware.summarization import SummarizationMiddleware
from langgraph.checkpoint.memory import MemorySaver
from easy_agent.services.stream_processor import StreamProcessor

class FakeModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kw):
        return self

def responses():
    yield AIMessage(content="SUMMARY-TEXT: the user talked about apples")
    while True:
        yield AIMessage(content="FINAL-ANSWER")

async def main():
    model = FakeModel(messages=responses())
    backend = StateBackend()
    summ = SummarizationMiddleware(model, backend=backend, trigger=("messages", 4), keep=("messages", 2))
    agent = create_deep_agent(model=model, backend=backend, middleware=[summ], checkpointer=MemorySaver())
    history = []
    for i in range(4):
        history += [HumanMessage(content=f"q{i}"), AIMessage(content=f"a{i}")]
    history.append(HumanMessage(content="now?"))
    proc = StreamProcessor(sid="exp", session_id="exp", db=None, message_id="m1", session_logger=None,
                           context_length=1000, auto_compress_tokens=800, start_time=time.time())
    async for mode, data in agent.astream({"messages": history}, stream_mode=["messages", "updates"],
                                          config={"configurable": {"thread_id": "t1"}}):
        for ev in proc.handle(mode, data):
            pass
    print("accumulated_response =", repr(proc.accumulated_response))

asyncio.run(main())
