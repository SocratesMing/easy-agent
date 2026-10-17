"""C2 projector 纯函数测试：HITL 投影、resume 快照、去重与用量载荷。"""

from types import SimpleNamespace

from easy_agent.services.streaming.projector import (
    apply_decision_status_to_blocks,
    apply_decision_status_to_records,
    build_usage_payload,
    decision_status_map,
    extract_file_paths_from_command,
    finalize_blocks,
    get_model_context_limit,
    last_user_message,
    load_resume_snapshot,
    mark_hitl_pending,
    merge_tool_call_records,
    thinking_duration,
)


def test_extract_file_paths_from_command_handles_compound_and_quotes():
    assert extract_file_paths_from_command("rm -rf a.txt") == ["a.txt"]
    assert extract_file_paths_from_command(
        "ls -la && rm -rf 'my dir' && echo done"
    ) == ["my dir"]
    assert extract_file_paths_from_command("echo rm x.txt") == []
    assert extract_file_paths_from_command("") == []
    assert extract_file_paths_from_command("rm foo 2>/dev/null") == ["foo"]


def test_mark_hitl_pending_marks_only_matching_tool_calls():
    partial = {
        "tool_calls": [
            {"tool_call_id": "c1", "tool_name": "execute"},
            {"tool_call_id": "c2", "tool_name": "read_file"},
        ],
        "blocks": [
            {
                "type": "tool_call",
                "tool_call_id": "c1",
                "tool_name": "execute",
            },
            {
                "type": "tool_call",
                "tool_call_id": "c2",
                "tool_name": "read_file",
            },
        ],
    }
    action_requests = [
        {
            "name": "execute",
            "args": {"command": "rm -rf a.txt"},
            "description": "删除文件",
        }
    ]

    payload = mark_hitl_pending(
        partial,
        ["c1"],
        action_requests,
        {"execute": {"allowed_decisions": ["approve", "reject"]}},
        "s1-m1",
    )

    assert payload == [
        {
            "tool_call_id": "c1",
            "tool_name": "execute",
            "arguments": {"command": "rm -rf a.txt"},
            "description": "删除文件",
            "allowed_decisions": ["approve", "reject"],
            "file_paths": ["a.txt"],
        }
    ]
    assert partial["tool_calls"][0]["approval_status"] == "pending"
    assert partial["tool_calls"][0]["file_paths"] == ["a.txt"]
    assert "approval_status" not in partial["tool_calls"][1]
    assert partial["blocks"][0]["pending_approval"] is True
    assert partial["blocks"][0]["file_paths"] == ["a.txt"]
    assert "pending_approval" not in partial["blocks"][1]
    assert partial["pending_approval"] == {
        "thread_id": "s1-m1",
        "action_requests": payload,
    }


def test_mark_hitl_pending_defaults_allowed_decisions():
    partial = {"tool_calls": [], "blocks": []}
    payload = mark_hitl_pending(
        partial, [], [{"name": "execute", "args": {}}], {}, "s1-m1"
    )

    assert payload[0]["tool_call_id"] == ""
    assert payload[0]["allowed_decisions"] == ["approve", "reject"]


def test_load_resume_snapshot_reads_last_assistant_and_pending_ids():
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "old", "blocks": [], "tool_calls": []},
        {"role": "user", "content": "delete it"},
        {
            "role": "assistant",
            "content": "deleting",
            "thinking": "hmm",
            "usage": {"step_count": 2},
            "tool_calls": [
                {
                    "tool_name": "execute",
                    "tool_call_id": "c1",
                    "arguments": {"command": "rm a"},
                    "result": "",
                    "success": True,
                    "duration": None,
                    "step": 2,
                    "approval_status": "pending",
                }
            ],
            "blocks": [
                {
                    "type": "tool_call",
                    "tool_call_id": "c1",
                    "approval_status": "pending",
                },
                {
                    "type": "tool_call",
                    "tool_call_id": "c1",
                    "approval_status": "pending",
                },
                {
                    "type": "tool_call",
                    "tool_call_id": "c2",
                    "approval_status": "pending",
                },
            ],
        },
    ]

    snapshot = load_resume_snapshot(messages)

    assert snapshot.accumulated_response == "deleting"
    assert snapshot.accumulated_thinking == "hmm"
    assert snapshot.current_step == 2
    assert snapshot.blocks == messages[-1]["blocks"]
    assert snapshot.tool_call_records == [
        [
            "execute",
            "c1",
            {"command": "rm a"},
            "",
            True,
            None,
            2,
            "pending",
        ]
    ]
    assert snapshot.pending_tool_call_ids == ["c1", "c2"]
    assert last_user_message(messages) == "delete it"
    assert last_user_message([]) == ""


def test_decision_status_map_and_apply():
    status = decision_status_map(
        ["c1", "c2", "c3"],
        [{"type": "approve"}, {"type": "reject"}],
    )
    assert status == {"c1": "approved", "c2": "rejected"}

    records = [["execute", "c1"], ["execute", "c2"], ["execute", "c3"]]
    apply_decision_status_to_records(records, status)
    assert records[0][7] == "approved"
    assert records[1][7] == "rejected"
    assert len(records[2]) == 2

    blocks = [
        {"type": "tool_call", "tool_call_id": "c1", "pending_approval": True},
        {"type": "tool_call", "tool_call_id": "c2", "pending_approval": True},
        {"type": "thinking", "content": "x"},
    ]
    apply_decision_status_to_blocks(blocks, status)
    assert blocks[0] == {"type": "tool_call", "tool_call_id": "c1", "approval_status": "approved"}
    assert blocks[1] == {"type": "tool_call", "tool_call_id": "c2", "approval_status": "rejected"}
    assert blocks[2] == {"type": "thinking", "content": "x"}


def test_merge_tool_call_records_prefers_result_and_keeps_approval():
    records = [
        ["execute", "c1", {}, "", True, None, 1, "approved"],
        ["execute", "c1", {}, "done", True, 0.2, 2, None],
        ["execute", "c2", {}, "ok", True, 0.1, 1, None],
    ]

    merged = merge_tool_call_records(records)

    assert [rec[1] for rec in merged] == ["c1", "c2"]
    c1 = merged[0]
    assert c1[3] == "done"
    assert c1[6] == 2
    assert c1[7] == "approved"


def test_finalize_blocks_applies_decisions_and_dedupes():
    blocks = [
        {"type": "thinking", "content": "t"},
        {
            "type": "tool_call",
            "tool_call_id": "c1",
            "result": "",
            "approval_status": "pending",
            "pending_approval": True,
        },
        {
            "type": "tool_call",
            "tool_call_id": "c1",
            "result": "done",
        },
        {
            "type": "tool_call",
            "tool_call_id": "c2",
            "result": "ok",
            "approval_status": "approved",
        },
    ]

    final = finalize_blocks(blocks, {"c1": "approved"})

    assert [block["type"] for block in final] == ["thinking", "tool_call", "tool_call"]
    c1 = next(block for block in final if block.get("tool_call_id") == "c1")
    assert c1["result"] == "done"
    assert c1["approval_status"] == "approved"
    assert "pending_approval" not in c1
    assert final[2]["approval_status"] == "approved"


def test_thinking_duration_sums_thinking_blocks_only():
    blocks = [
        {"type": "thinking", "duration": 0.4},
        {"type": "thinking", "duration": 1.26},
        {"type": "content", "duration": 9},
    ]

    assert thinking_duration(blocks) == 1.66


def test_build_usage_payload_chat_and_resume_shapes():
    last_usage = {"input_tokens": 10, "output_tokens": 4, "reasoning_tokens": 2}

    chat = build_usage_payload(
        last_usage=last_usage,
        context_length=131072,
        auto_compress_tokens=100000,
        last_context_tokens=0,
        step_count=3,
    )
    assert list(chat) == [
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "context_length",
        "auto_compress_tokens",
        "context_tokens",
        "step_count",
    ]
    assert chat["context_tokens"] == 10

    resume = build_usage_payload(
        last_usage=last_usage,
        context_length=131072,
        auto_compress_tokens=100000,
        last_context_tokens=42,
        step_count=3,
        elapsed_time=1.234,
    )
    assert resume["context_tokens"] == 42
    assert resume["elapsed_time"] == 1.23
    assert list(resume)[-2:] == ["elapsed_time", "step_count"]


def test_get_model_context_limit_matches_model_name():
    assert get_model_context_limit(None) is None
    assert get_model_context_limit(SimpleNamespace(model_name="")) is None
    assert get_model_context_limit(SimpleNamespace(model_name="deepseek-chat")) == 131072
    assert get_model_context_limit(SimpleNamespace(model="claude-sonnet")) == 200000
    assert get_model_context_limit(SimpleNamespace(model_name="unknown")) is None
