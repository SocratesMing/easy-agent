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
