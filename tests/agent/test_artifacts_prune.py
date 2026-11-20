"""AgentArtifactsBackend 大工具结果落盘按上限 LRU 清理测试。"""

import os
from types import SimpleNamespace

from easy_agent.backends.artifacts import AgentArtifactsBackend


def test_large_tool_results_prunes_oldest_files_beyond_limit(tmp_path):
    backend = AgentArtifactsBackend(
        root_dir=tmp_path,
        shell=SimpleNamespace(id="fake-shell"),
        max_large_tool_results=3,
    )
    large_dir = tmp_path / ".agent" / "large_tool_results"
    for i in range(5):
        res = backend.write(f"/.agent/large_tool_results/res_{i}.txt", f"payload-{i}")
        assert res.error is None
        path = large_dir / f"res_{i}.txt"
        os.utime(path, (1000 + i, 1000 + i))
    # 触发一次写入后仅保留最新的 3 个文件
    res = backend.write("/.agent/large_tool_results/res_5.txt", "payload-5")
    assert res.error is None
    remaining = sorted(p.name for p in large_dir.iterdir() if p.is_file())
    assert len(remaining) == 3
    assert "res_5.txt" in remaining
    assert "res_0.txt" not in remaining
    assert "res_1.txt" not in remaining
