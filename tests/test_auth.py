import json

import httpx
import pytest
from conftest import FakeModels
from test_learning import learning_data as learning_data
from test_learning import question_data

from sctutor.app import create_app
from sctutor.auth import COOKIE, IDENTITY
from sctutor.quiz import Quiz
from sctutor.store import Store


async def test_demo_login_logout_and_admin_matrix(settings, store):
    app = create_app(settings, store, FakeModels())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as c:
        assert (await c.get('/api/sessions')).status_code == 401
        assert (await c.post('/api/auth/login', json={'account':'admin'})).status_code == 422
        assert (await c.post('/api/auth/login', json={'account':'student', 'role':'teacher'})).status_code == 422
        assert (await c.post('/api/auth/login', json={'account':'teacher'}, headers={'Origin':'https://evil.example'})).status_code == 403
        login = await c.post('/api/auth/login', json={'account':'student'})
        cookie = c.cookies.get(COOKIE)
        assert 'httponly' in login.headers['set-cookie'].lower()
        assert (await c.get('/api/auth/me')).json()['role'] == 'student'
        paths = [('GET','/api/admin/knowledge'), ('GET','/api/admin/pages'), ('GET','/api/admin/questions'),
                 ('GET','/api/admin/processing'), ('POST','/api/admin/pipeline'), ('POST','/api/admin/ingest'),
                 ('POST','/api/admin/enhance'), ('POST','/api/admin/extract'), ('POST','/api/admin/questions/generate'),
                 ('POST','/api/admin/questions'), ('PUT','/api/admin/questions/forged'),
                 ('POST','/api/admin/questions/forged/status'), ('POST','/api/admin/concepts/forged/review')]
        for method, path in paths:
            assert (await c.request(method, path, json={})).status_code == 403, path
        assert (await c.post('/api/auth/logout')).status_code == 200
        assert (await c.get('/api/sessions', headers={'Cookie': f'{COOKIE}={cookie}'})).status_code == 401
        await c.post('/api/auth/login', json={'account':'teacher'})
        for path in ['/api/admin/knowledge','/api/admin/pages','/api/admin/questions','/api/admin/processing']:
            assert (await c.get(path)).status_code == 200
        assert (await c.get('/api/auth/me')).json()['role'] == 'teacher'
        # Bearer tokens aren't persisted in plaintext.
        assert c.cookies.get(COOKIE) not in json.dumps(store.query('SELECT * FROM logins'))


async def test_account_isolation_all_learning_resources(settings, store, learning_data):
    _, cs, model = learning_data
    quiz = Quiz(store, model)
    qid = quiz.add_question(question_data(cs['Copilot']))['id']
    quiz.review_question(qid, 'approved')
    old = store.new_session()['id']
    turn = store.reserve_turn(old, 'ask', 'student-private', None)
    pid = cs['Copilot']['pages'][0]
    store.execute('UPDATE turns SET level=3 WHERE id=?', (turn['id'],))
    store.finish(turn['id'], 'student answer', [{'id':pid,'file':'course','page':1}], 'completed', 1)
    app = create_app(settings, store, model)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as student, httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as teacher:
        await student.post('/api/auth/login', json={'account':'student'})
        await teacher.post('/api/auth/login', json={'account':'teacher'})
        sq = (await student.post('/api/quizzes', json={'count':1})).json()
        assert (await teacher.get('/api/quizzes/active')).json() is None
        assert (await teacher.get('/api/report')).status_code == 200
        assert (await teacher.get('/api/sessions')).json() == []
        for method, path, body in [
            ('GET', f'/api/sessions/{old}', None),
            ('PATCH', f'/api/sessions/{old}', {'name':'stolen'}),
            ('DELETE', f'/api/sessions/{old}', None),
            ('POST', f'/api/sessions/{old}/turns', {'message':'steal'}),
            ('POST', f'/api/sessions/{old}/close/{turn["id"]}', None),
            ('GET', f'/api/turns/{turn["id"]}/citations/{pid}', None),
            ('GET', f'/api/turns/{turn["id"]}/citations/{pid}/image', None),
            ('GET', f'/api/quizzes/{sq["id"]}', None),
            ('POST', f'/api/quizzes/{sq["id"]}/finish', None),
            ('POST', f'/api/quizzes/{sq["id"]}/cancel', None),
            ('POST', f'/api/quiz-items/{sq["items"][0]["id"]}/answers', {'answer':'Copilot','attempt':1,'request_id':'crossuser123'}),
            ('GET', f'/api/quiz-items/{sq["items"][0]["id"]}/citations/{pid}', None),
        ]:
            response = await teacher.request(method, path, json=body)
            assert response.status_code == 409, (path, response.text)
            assert 'student answer' not in response.text
        # The old global unique quiz restriction is now per-account.
        tq = (await teacher.post('/api/quizzes', json={'count':1})).json()
        assert tq['id'] != sq['id']
        for client, active in [(student,sq),(teacher,tq)]:
            iid = active['items'][0]['id']
            assert (await client.post(f'/api/quiz-items/{iid}/answers', json={'answer':'Copilot','attempt':1,'request_id':iid})).status_code == 200
            assert (await client.post(f'/api/quizzes/{active["id"]}/finish')).status_code == 200
        sr = (await student.get('/api/reviews')).json()
        tr = (await teacher.get('/api/reviews')).json()
        assert sr and tr and sr[0]['id'] != tr[0]['id']
        assert (await teacher.post('/api/quizzes', json={'review_id':sr[0]['id']})).status_code == 409
        assert len(store.query('SELECT * FROM mastery')) == 1
        assert len(store.query('SELECT * FROM teacher_mastery')) == 1
        # A new student observation must not change teacher mastery/report.
        teacher_before = (await teacher.get('/api/report')).json()
        store.execute('UPDATE mastery SET probability=.01,count=3')
        teacher_after = (await teacher.get('/api/report')).json()
        # Wall-clock window endpoints change between requests; account evidence must not.
        for snapshot in (teacher_before, teacher_after):
            snapshot['summary'].pop('start')
            snapshot['summary'].pop('end')
        assert teacher_after == teacher_before
        assert next(x for x in (await student.get('/api/report')).json()['concepts'] if x['id'] == cs['Copilot']['id'])['observations'] == 3


async def test_student_cannot_trigger_shortage_generation(settings, store, learning_data):
    _, _, model = learning_data
    app = create_app(settings, store, model)
    async def forbidden(*args, **kwargs):
        pytest.fail('student triggered teacher generation')
    app.state.quiz.generate = forbidden
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as c:
        await c.post('/api/auth/login', json={'account':'student'})
        assert (await c.post('/api/quizzes', json={'count':3})).status_code == 409
    assert IDENTITY.get() == 'student'


async def test_two_active_quizzes_survive_service_restart(settings, store, learning_data):
    _, cs, model = learning_data
    q = Quiz(store, model)
    qid = q.add_question(question_data(cs['Copilot']))['id']
    q.review_question(qid, 'approved')
    app = create_app(settings, store, model)
    saved = {}
    for account in ['student', 'teacher']:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as c:
            await c.post('/api/auth/login', json={'account':account})
            active = (await c.post('/api/quizzes', json={'count':1})).json()
            saved[account] = (c.cookies.get(COOKIE), active['id'])
    reopened = Store(settings.data_dir)
    restarted = create_app(settings, reopened, model)
    for account, (cookie, active_id) in saved.items():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted), base_url='http://127.0.0.1', cookies={COOKIE:cookie}) as c:
            assert (await c.get('/api/auth/me')).json()['id'] == account
            active = (await c.get('/api/quizzes/active')).json()
            assert active['id'] == active_id and active['status'] == 'active'
            assert (await c.post('/api/quizzes', json={'count':1})).status_code == 409
            assert (await c.get('/api/report')).status_code == 409
            assert (await c.post(f'/api/quizzes/{active_id}/cancel')).status_code == 200
    assert len(reopened.query('SELECT * FROM quizzes')) == 2
