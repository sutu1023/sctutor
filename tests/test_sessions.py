import sqlite3

import httpx
from conftest import FakeModels

from sctutor.app import create_app
from sctutor.auth import IDENTITY
from sctutor.config import UserError
from sctutor.store import Store


def test_legacy_migration_preserves_history_and_mastery(tmp_path):
    directory = tmp_path/'legacy'
    directory.mkdir()
    with sqlite3.connect(directory/'sctutor.sqlite3') as c:
        c.executescript("""CREATE TABLE sessions(id TEXT PRIMARY KEY,created REAL NOT NULL);
            INSERT INTO sessions VALUES ('legacy',1);""")
    store = Store(directory)
    assert store.sessions()[0]['id'] == 'legacy'
    assert store.history('legacy') == []
    token = IDENTITY.set('teacher')
    try:
        assert store.sessions() == []
        try:
            store.history('legacy')
            raise AssertionError('cross-account history allowed')
        except UserError:
            pass
    finally:
        IDENTITY.reset(token)
    # Repeated migration does not reassign a teacher session to student.
    token = IDENTITY.set('teacher')
    try:
        teacher = store.new_session()['id']
    finally:
        IDENTITY.reset(token)
    reopened = Store(directory)
    assert teacher not in {s['id'] for s in reopened.sessions()}


async def test_search_rename_delete_atomic_and_cleanup(settings, store):
    app = create_app(settings, store, FakeModels())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as c:
        await c.post('/api/auth/login', json={'account':'student'})
        sid = (await c.post('/api/sessions')).json()['id']
        other = (await c.post('/api/sessions')).json()['id']
        store.rename_session(sid, 'old')
        assert (await c.patch(f'/api/sessions/{sid}', json={'name':'  命名会话  '})).json()['name'] == '命名会话'
        assert (await c.get('/api/sessions', params={'search':'命名'})).json()[0]['id'] == sid
        assert (await c.patch(f'/api/sessions/{sid}', json={'name':' '})).status_code == 422
        assert (await c.patch(f'/api/sessions/{sid}', json={'name':'x'*121})).status_code == 422
        t = store.reserve_turn(sid, 'ask', 'unique-question', None)
        assert (await c.get('/api/sessions', params={'search':'unique-question'})).json()[0]['id'] == sid
        assert (await c.post('/api/sessions/delete', json={'ids':[other,sid]})).status_code == 409
        assert len(store.sessions()) == 2
        store.finish(t['id'], 'done', [], 'completed', 1)
        store.audit(t['id'], 1, {'text':'private-draft'}, {'passed':True})
        store.call(t['id'], 'check', 'fake', 1, None, 'ok')
        store.execute('INSERT INTO turn_details(turn_id,closed) VALUES (?,1)', (t['id'],))
        assert (await c.post('/api/sessions/delete', json={'ids':[other,'unknown']})).status_code == 409
        assert len(store.sessions()) == 2
        # Unrelated utility calls are kept.
        store.call(None, 'utility', 'fake', 1, None, 'ok')
        response = await c.post('/api/sessions/delete', json={'ids':[sid,other,sid]})
        assert response.status_code == 200
        for table in ['sessions','session_owners','turns','audits','turn_details']:
            assert not store.query(f'SELECT * FROM {table}')
        assert len(store.query('SELECT * FROM calls')) == 1
        assert (await c.get(f'/api/sessions/{sid}')).status_code == 409
        assert (await c.get('/api/sessions')).json() == []
        third = (await c.post('/api/sessions')).json()['id']
        assert (await c.delete(f'/api/sessions/{third}')).status_code == 200
        assert (await c.post('/api/sessions/delete', json={'ids':[]})).status_code == 422
