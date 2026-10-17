"""knowledge schema 初始化测试（schema 注册制）。

Database.init_tables() 通过 db.schema_registry 调用领域模块注册的建表函数，
因此本测试需先 import 领域 schema 模块完成注册。
"""

import easy_agent.knowledges.core.schema  # noqa: F401  （导入即注册）

def test_knowledge_tables_created_on_init(db):
    """v2（knowledges）幂等建表，无迁移跟踪表，核心业务表齐备即可。"""
    with db.get_connection() as conn:
        cur = conn.cursor()
        db._execute(cur, "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'knowledge_%'")
        names = {r[0] if not isinstance(r, dict) else r["name"] for r in cur.fetchall()}
    assert {
        "knowledge_bases",
        "knowledge_folders",
        "knowledge_documents",
        "knowledge_document_objects",
        "knowledge_operations",
        "knowledge_audit_events",
    } <= names
