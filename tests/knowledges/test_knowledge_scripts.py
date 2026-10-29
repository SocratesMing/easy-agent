"""easy_agent/knowledges/scripts 下的运维脚本在独立进程里跑：业务库要按主配置的 database 段初始化。

回归：migrate_knowledge_to_shared.py / sync_source_docs.py 原来调 init_database() 不传配置，
总去初始化 cwd 下的 ./data/easy_agent.db（生产配的 MySQL 被忽略）；也没导入
easy_agent.knowledges.personnel，init_database 建默认管理员时报「默认管理员策略未注册」退出
（pytest 进程里 conftest 早就导入过了，所以只能在子进程里测）。
"""

from pathlib import Path

import pytest

from tests.db.test_migrate_scripts import _run
from tests.db.test_schema_slimming import FRESH_TABLES, _names

SCRIPTS = Path(__file__).resolve().parents[2] / "easy_agent" / "knowledges" / "scripts"

# 打开知识库和文档同步，两个脚本才会走到 init_database
KNOWLEDGE = {
    "enabled": True,
    "base_url": "http://ragflow.invalid",
    "api_key": "test-key",
    "source_sync": {"enabled": True},
}

# 两个脚本都在 init_database 之后才建 RagflowClient，在这里停下：再往后要连 RAGFlow /
# 研报 MySQL，LocalOriginalStore 还会在 cwd 下建 data/knowledge_originals。
_STOP_AT_RAGFLOW = """\
import runpy, sys
import easy_agent.knowledges.ragflow as ragflow


class ReachedRagflow(Exception):
    pass


def stop(*args, **kwargs):
    raise ReachedRagflow


ragflow.RagflowClient = stop
sys.argv = sys.argv[1:]
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except ReachedRagflow:
    print("reached RagflowClient")
"""


@pytest.mark.parametrize("script", ["migrate_knowledge_to_shared.py", "sync_source_docs.py"])
def test_knowledge_script_uses_the_configured_database(tmp_path, script):
    db_path = tmp_path / "configured.db"

    result = _run(
        tmp_path, "-c", _STOP_AT_RAGFLOW, str(SCRIPTS / script), db_path=db_path, knowledge=KNOWLEDGE
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert "reached RagflowClient" in result.stdout
    assert _names(db_path, "table") == FRESH_TABLES
    assert not (tmp_path / "data").exists()
