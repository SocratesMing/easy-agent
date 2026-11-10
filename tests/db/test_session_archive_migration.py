"""Run MySQL cases with EASY_TEST_MYSQL_HOST=127.0.0.1 (isolated database)."""
import os
import uuid

import pytest

from easy_agent.db.database import Database
from easy_agent.db.repositories.session_repository import SessionRepository
from easy_agent.db.schema import SCHEMA_VERSION
from easy_agent.models.db import SessionModel
from easy_agent.models.exceptions import NotFoundError


@pytest.fixture(params=['sqlite', 'mysql'])
def archive_db(request, tmp_path):
    admin = None
    if request.param == 'mysql':
        host = os.getenv('EASY_TEST_MYSQL_HOST')
        if not host:
            pytest.skip('EASY_TEST_MYSQL_HOST is not configured')
        import pymysql
        settings = dict(host=host, port=int(os.getenv('EASY_TEST_MYSQL_PORT', '3306')),
                        user=os.getenv('EASY_TEST_MYSQL_USER', 'root'),
                        password=os.getenv('EASY_TEST_MYSQL_PASSWORD', ''), charset='utf8mb4')
        admin = pymysql.connect(**settings, autocommit=True)
        name = 'test_archive_' + uuid.uuid4().hex
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4')
        db = Database({'type': 'mysql', 'mysql': {**settings, 'database': name}})
        assert db.db_type == 'mysql'
    else:
        db = Database({'type': 'sqlite', 'sqlite': {'path': str(tmp_path / 'old.db')}})
    try:
        # Pre-feature sessions table. Real init_tables must add all missing columns.
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('CREATE TABLE sessions (session_id VARCHAR(255) PRIMARY KEY, title TEXT NOT NULL, '
                        'messages TEXT NOT NULL, created_at VARCHAR(50) NOT NULL, updated_at VARCHAR(50) NOT NULL, '
                        "username VARCHAR(255) DEFAULT '')")
            db._execute(cur, 'INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)', ('old', '旧库', '', '2020', '2020', 'alice'))
        db.init_tables()
        yield db
    finally:
        if admin:
            db._pool.close()
            with admin.cursor() as cur:
                cur.execute(f'DROP DATABASE `{name}`')
            admin.close()


def test_upgrade_idempotent_archive_and_literal_search(archive_db):
    db = archive_db
    repo = SessionRepository(db)
    old = repo.get_session('old')
    assert old.archived is False
    repo.set_session_archived('old', 'alice', True)
    repo.set_session_archived('old', 'alice', True)  # MySQL changed rows == 0
    db.init_tables()  # re-start fast path leaves flag and timestamps intact
    assert repo.get_session('old').archived is True
    assert repo.get_session('old').updated_at == '2020'
    with db.get_connection() as conn:
        cur = conn.cursor()
        db._execute(cur, 'SELECT meta_value FROM schema_meta WHERE meta_key=?', ('schema_version',))
        row = cur.fetchone()
        assert int(row['meta_value']) == SCHEMA_VERSION
    for i in range(65):
        sid = f's{i:03}'
        repo.create_session(SessionModel(session_id=sid, title=f'百分%下划_反斜\\ {i}', username='alice', created_at='2020', updated_at='2020'))
        repo.set_session_archived(sid, 'alice', True)
    for q in ['%', '_', '\\', '  %下划_反斜\\  ']:
        assert repo.count_sessions('alice', True, q) == 65
        assert len(repo.list_sessions(limit=20, offset=40, username='alice', archived=True, q=q)) == 20
    assert repo.count_sessions('alice', False) == 0
    assert repo.count_sessions('alice', True) == 66
    for sid, owner in [('old', 'bob'), ('missing', 'alice')]:
        with pytest.raises(NotFoundError):
            repo.set_session_archived(sid, owner, False)
    repo.set_session_archived('old', 'alice', False)
    assert repo.get_session('old').updated_at == '2020'
