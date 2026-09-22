def test_knowledge_tables_created_on_init(db):
    with db.get_connection() as conn:
        cur = conn.cursor()
        db._execute(cur, "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'knowledge_%'")
        names = {r[0] if not isinstance(r, dict) else r["name"] for r in cur.fetchall()}
    assert "knowledge_schema_migrations" in names
