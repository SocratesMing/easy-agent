"""Archive is metadata only; filtering happens before pagination."""
import pytest

from easy_agent.db.repositories.session_repository import SessionRepository
from easy_agent.models.db import SessionModel


def test_archive_preserves_metadata_and_later_saves(client, db, monkeypatch):
    sid = client.post('/agent/sessions', json={'title': '保留'}).json()['session_id']
    client.put(f'/agent/sessions/{sid}/pin')
    client.post(f'/agent/sessions/{sid}/messages', json={'role': 'user', 'content': '你好'})
    repo = SessionRepository(db)
    before = repo.get_session(sid)
    # Updating the flag must never read full history or touch runtime leases.
    monkeypatch.setattr(repo.__class__, 'get_owned_session', lambda *a: pytest.fail('full load'))
    for _ in range(2):
        r = client.put(f'/agent/sessions/{sid}/archive', json={'archived': True})
        assert r.status_code == 200 and r.json()['archived'] is True
    after = repo.get_session(sid)
    assert after.messages == before.messages
    assert (after.created_at, after.updated_at, after.pinned) == (before.created_at, before.updated_at, 1)
    repo.update_session(before)  # stale model cannot unarchive
    assert repo.get_session(sid).archived is True
    repo.add_message(sid, {'role': 'assistant', 'content': '后台完成', 'timestamp': '2099-01-01'})
    active_time = repo.get_session(sid).updated_at
    assert client.put(f'/agent/sessions/{sid}/archive', json={'archived': False}).status_code == 200
    assert repo.get_session(sid).updated_at == active_time


def test_archive_lists_search_counts_and_sort(client, db):
    repo = SessionRepository(db)
    for i in range(65):
        sid = f'archive-{i:03}'
        repo.create_session(SessionModel(session_id=sid, title=f'字面%_\\标题 {i}',
                                        username='testuser', created_at='2020', updated_at='2020'))
        repo.set_session_archived(sid, 'testuser', True)
    repo.toggle_session_pin('archive-064')
    repo.create_session(SessionModel(session_id='hidden', title='[定时任务]字面%_\\标题', username='testuser'))
    repo.set_session_archived('hidden', 'testuser', True)
    repo.create_session(SessionModel(session_id='other', title='字面%_\\标题', username='other'))
    params = {'archived': 'true', 'q': '  %_\\  ', 'limit': 20, 'offset': 40}
    rows = client.get('/agent/sessions', params=params).json()
    assert len(rows) == 20
    assert rows[0]['session_id'] == 'archive-040'
    assert rows[-1]['session_id'] == 'archive-059'
    assert all(s['archived'] is True for s in rows)
    assert client.get('/agent/sessions/count', params={'archived': True, 'q': '%_\\'}).json()['total_sessions'] == 65
    assert client.get('/agent/sessions/count', params={'archived': True, 'q': 'absent'}).json()['total_sessions'] == 0
    assert client.get('/agent/sessions/count').json()['total_sessions'] == 65
    assert client.get('/agent/sessions').json() == []
    client.put('/agent/sessions/archive-064/archive', json={'archived': False})
    assert client.get('/agent/sessions').json()[0]['pinned'] == 1


def test_archive_access_validation_and_read_by_id(client, db):
    repo = SessionRepository(db)
    repo.create_session(SessionModel(session_id='foreign', title='private', username='other'))
    for sid in ['foreign', 'missing']:
        assert client.put(f'/agent/sessions/{sid}/archive', json={'archived': True}).status_code == 404
    sid = client.post('/agent/sessions', json={}).json()['session_id']
    for invalid in [{}, {'archived': None}, {'archived': 'true'}, {'archived': 1}]:
        assert client.put(f'/agent/sessions/{sid}/archive', json=invalid).status_code == 422
    client.put(f'/agent/sessions/{sid}/archive', json={'archived': True})
    for suffix in ['', '/history']:
        assert client.get(f'/agent/sessions/{sid}{suffix}').json()['archived'] is True
    assert client.post(f'/agent/sessions/{sid}/messages', json={'role': 'user', 'content': '继续'}).status_code == 200
    assert client.get('/agent/sessions').json() == []
    assert repo.get_session(sid).archived is True
