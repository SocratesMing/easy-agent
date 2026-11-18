"""手动恢复坏事件无需等到有足够历史再次摘要。"""

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from easy_agent.agent.manual_compaction import terminal_update_node
from easy_agent.services.sessions import compaction
from tests.services.test_manual_compaction import agent, repo, runtime


async def test_manual_short_history_clears_failed_event_atomically(tmp_path):
    rows = [{"role": "user", "content": "查询金市营收", "turn_id": "goal"},
            {"role": "assistant", "content": "尚未完成"}]
    cfg = {"configurable": {"thread_id": "s1"}}
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoint.db")) as saver:
        a = agent(saver, tmp_path)
        await a.agent.aupdate_state(cfg, {
            "messages": [HumanMessage("查询金市营收", id="goal"), AIMessage("尚未完成")],
            "_summarization_event": {
                "cutoff_index": 1, "file_path": None,
                "summary_message": HumanMessage("Here is a summary of the conversation to date:\n\nPrevious conversation was too long to summarize."),
            },
        }, as_node=terminal_update_node(a.agent))
        result = await compaction._prepare_and_commit(
            repo(rows), runtime(tmp_path), a, repo(rows).get_owned_session("s1", "tester"),
            "operation", "recover-short",
        )
        state = (await a.agent.aget_state(cfg)).values
        assert result["status"] == "skipped"
        assert state["_summarization_event"] is None
        assert "recover-short" in state["context_compactions"]
        assert state["messages"][0].content == "查询金市营收"
        assert not a.model.inputs
