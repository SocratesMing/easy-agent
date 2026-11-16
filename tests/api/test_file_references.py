from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from easy_agent.models.api import ChatRequest, FileReference
from easy_agent.models.exceptions import BadRequestError, NotFoundError
from easy_agent.services.agents.chat_turn import require_turn_content, validate_file_references


class Repo:
    def __init__(self, username='alice'):
        self.session = SimpleNamespace(username=username, workspace_name='one')

    def get_session(self, session_id):
        return self.session if session_id == 's1' else None

    def get_owned_session_meta(self, session_id, username):
        session = self.get_session(session_id)
        if not session or session.username != username:
            raise NotFoundError('会话不存在')
        return {'workspace_name': session.workspace_name}


def test_reference_validation_and_reading(tmp_path, monkeypatch):
    monkeypatch.setattr('easy_agent.services.agents.chat_turn.Config.get_user_workspace_dir', lambda _: tmp_path)
    root = tmp_path / 'session' / 'one'
    (root / 'output').mkdir(parents=True)
    (root / 'output' / '报告.txt').write_text('session marker', encoding='utf-8')
    refs = [FileReference(path='output/报告.txt', type='file'), FileReference(path='output', type='directory')]
    assert validate_file_references(Repo(), 's1', 'alice', refs) == [
        {'path': 'output/报告.txt', 'type': 'file'}, {'path': 'output', 'type': 'directory'}
    ]
    assert (root / refs[0].path).read_text(encoding='utf-8') == 'session marker'
    assert [p.name for p in (root / refs[1].path).iterdir()] == ['报告.txt']
    require_turn_content('', None, None, refs)


def test_rejects_wrong_session_and_paths(tmp_path, monkeypatch):
    monkeypatch.setattr('easy_agent.services.agents.chat_turn.Config.get_user_workspace_dir', lambda _: tmp_path)
    root = tmp_path / 'session' / 'one'
    root.mkdir(parents=True)
    (tmp_path / 'outside').write_text('outside')
    (root / 'regular').write_text('data')
    (root / 'link').symlink_to(tmp_path / 'outside')
    with pytest.raises(NotFoundError):
        validate_file_references(Repo(), 's1', 'bob', [FileReference(path='link', type='file')])
    for path in ('../outside', '/outside', 'link'):
        with pytest.raises(BadRequestError):
            validate_file_references(Repo(), 's1', 'alice', [FileReference(path=path, type='file')])
    with pytest.raises(NotFoundError):
        validate_file_references(Repo(), 's1', 'alice', [FileReference(path='missing', type='file')])
    with pytest.raises(NotFoundError):
        validate_file_references(Repo(), 's1', 'alice', [FileReference(path='regular', type='directory')])


def test_reference_model_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ChatRequest(message='', file_references=[{'path': 'a', 'type': 'file', 'session_id': 'other'}])
    with pytest.raises(ValidationError):
        FileReference(path=' ', type='file')


def test_deleted_targets_and_references_without_session_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr('easy_agent.services.agents.chat_turn.Config.get_user_workspace_dir', lambda _: tmp_path)
    root = tmp_path / 'session' / 'one'
    root.mkdir(parents=True)
    target = root / 'removed.txt'
    target.write_text('removed', encoding='utf-8')
    reference = FileReference(path='removed.txt', type='file')
    validate_file_references(Repo(), 's1', 'alice', [reference, reference])
    target.unlink()
    with pytest.raises(NotFoundError):
        validate_file_references(Repo(), 's1', 'alice', [reference])
    with pytest.raises(BadRequestError):
        validate_file_references(Repo(), None, 'alice', [reference])
    with pytest.raises(NotFoundError):
        validate_file_references(Repo(), 'missing-session', 'alice', [reference])


def test_empty_reference_history_uses_same_short_body():
    from easy_agent.services.streaming.messages import build_context_messages, turn_message_text

    refs = [{'path': 'output', 'type': 'directory'}]
    body = turn_message_text('', file_references=refs)
    restored = build_context_messages([{'role': 'user', 'content': '', 'file_references': refs}], '下一轮')
    assert body == restored[0].content == '查看引用对象：output/'
