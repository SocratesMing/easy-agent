import sys
"""Exp 5: how the project's resume builder maps decisions when 2 interrupts are pending
(e.g. two parallel `task` subagents each paused on HITL)."""
import asyncio, sys, types, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
from easy_agent.services.streaming.orchestrator import build_resume_command
from easy_agent.services.hitl_state import HitlDecisionError, _extract_hitl_request

def intr(iid, name, args):
    return types.SimpleNamespace(id=iid, value={
        "action_requests": [{"name": name, "args": args, "description": "..."}],
        "review_configs": [{"action_name": name, "allowed_decisions": ["approve", "reject"]}]})

state = types.SimpleNamespace(next=("tools",), values={"messages": []}, tasks=[
    types.SimpleNamespace(interrupts=[intr("A", "delete", {"file_path": "/workspace/draft.docx"})]),
    types.SimpleNamespace(interrupts=[intr("B", "create_scheduled_task", {"cron": "* * * * *", "prompt": "..."})]),
])
class G:
    async def aget_state(self, cfg): return state
agent = types.SimpleNamespace(agent=G())
print("UI is shown only:", [a["name"] for a in _extract_hitl_request(state)["action_requests"]])
for decisions in ([{"type": "approve"}], [{"type": "approve"}, {"type": "reject"}]):
    sent = [d["type"] for d in decisions]
    try:
        cmd = asyncio.run(build_resume_command(agent=agent, stream_config={}, decisions=decisions, sid="exp"))
    except HitlDecisionError as e:  # after the DA-02 fix: rejected before resuming (HTTP 400)
        print("frontend sent", sent, "-> rejected before resume:", e)
        continue
    print("frontend sent", sent, "-> resume =", cmd.resume)
