"""Exp 4: the CompositeBackend default route aliases /workspace/ (same real dir),
so a deny rule scoped to /workspace/... can be sidestepped via the root alias."""
import sys, tempfile, types, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
from easy_agent.agent.backend import _build_backend_for_context
from easy_agent.agent.graph_cache import SessionContext
from deepagents.middleware.filesystem import FilesystemPermission, _permission_error

import atexit, shutil
tmp = Path(tempfile.mkdtemp(dir=sys.argv[1] if len(sys.argv) > 1 else None))
atexit.register(shutil.rmtree, tmp, ignore_errors=True)
ws = tmp / "workspace" / "alice" / "session" / "s1"; ws.mkdir(parents=True)
skills = tmp / "workspace" / "alice" / "skills"; skills.mkdir(parents=True)
mem = tmp / "memories" / "alice" / "AGENTS.md"
cfg = types.SimpleNamespace(agent=types.SimpleNamespace(external_dirs={}, workspace_dir=str(tmp / "workspace"), sandbox_enabled=True))
ctx = SessionContext(session_id="s1", username="alice", safe_username="alice", workspace_dir=str(ws),
                     workspace_virtual_path="/workspace", user_skills_dir=str(skills), memory_file=str(ws / "memory.md"),
                     long_term_memory_file=str(mem), organization_id="", shell_env={}, workspace_name="s1")
backend = _build_backend_for_context(cfg, ctx, ["/user-skills/"])
(ws / "secret").mkdir(); (ws / "secret" / "a.txt").write_text("TOP-SECRET")
rules = [FilesystemPermission(operations=["read", "write"], paths=["/workspace/secret/**"], mode="deny")]
for p in ["/workspace/secret/a.txt", "/secret/a.txt"]:
    err = _permission_error(rules, "read", backend, p)
    res = backend.read(p)
    content = (res.file_data or {}).get("content") if getattr(res, "file_data", None) else res.error
    print(f"{p:28} permission={'DENIED' if err else 'allowed':8} read -> {str(content)[:40]!r}")

# internal artifacts written by FilesystemMiddleware / SummarizationMiddleware land in the user's workspace
for p in ["/large_tool_results/call_abc", "/conversation_history/session_x.md"]:
    backend.write(p, "agent-internal artifact")
print("artifacts on disk under session workspace:", sorted(str(x.relative_to(ws)) for x in ws.rglob("*") if x.is_file() and "secret" not in str(x)))
