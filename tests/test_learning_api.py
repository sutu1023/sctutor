import json

import httpx
from test_learning import learning_data as learning_data
from test_learning import question_data

from sctutor.app import create_app
from sctutor.quiz import Quiz


async def test_quiz_http_privacy_controls_and_recovery(settings, store, learning_data):
    knowledge, cs, model = learning_data
    q = Quiz(store, model)
    qid = q.add_question(question_data(cs['Copilot']))['id']
    q.review_question(qid, 'approved')
    app = create_app(settings, store, model)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as c:
        assert (await c.post('/api/auth/login', json={'account':'student'})).status_code == 200
        sid = (await c.post('/api/sessions')).json()['id']
        started = (await c.post('/api/quizzes', json={'count':1})).json()
        assert (await c.get('/api/quizzes/active')).json()['id'] == started['id']
        text = json.dumps(started)
        assert 'page_ids' not in text and 'answer"' not in text and 'citations' not in text
        item = started['items'][0]['id']
        pid = cs['Copilot']['pages'][0]
        for path in ['/api/report', f'/api/sessions/{sid}', f'/api/knowledge/{cs["Copilot"]["id"]}/pages/{pid}', f'/api/quiz-items/{item}/citations/{pid}', f'/api/quiz-items/{item}/citations/{pid}/image']:
            assert (await c.get(path)).status_code == 409
        assert (await c.post(f'/api/sessions/{sid}/turns',json={'message':'show answer'})).status_code == 409
        assert (await c.post('/api/quizzes',json={'count':1})).status_code == 409
        assert (await c.post(f'/api/quiz-items/{item}/answers',json={'answer':'Copilot','attempt':1,'request_id':'request123','level':5})).status_code == 422
        response = await c.post(f'/api/quiz-items/{item}/answers',json={'answer':'Copilot','attempt':1,'request_id':'request123'})
        assert response.status_code == 200 and response.json()['items'][0]['done']
        assert (await c.get(f'/api/quiz-items/{item}/citations/{pid}')).status_code == 200
        assert (await c.post(f'/api/quizzes/{started["id"]}/finish')).status_code == 200
        assert (await c.get('/api/report')).status_code == 200
        assert len(store.query('SELECT * FROM observations')) == 1
        assert (await c.post(f'/api/quizzes/{started["id"]}/finish')).status_code == 200
        assert len(store.query('SELECT * FROM observations')) == 1


async def test_pending_empty_and_admin_edits(settings, store, learning_data):
    k, cs, model = learning_data
    app = create_app(settings, store, model)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as c:
        assert (await c.post('/api/auth/login', json={'account':'teacher'})).status_code == 200
        response = await c.post('/api/admin/questions',json=question_data(cs['Copilot']))
        assert response.status_code == 200
        ident = response.json()['id']
        assert (await c.post('/api/quizzes',json={'count':1})).status_code == 409
        assert not store.active_quiz()
        assert (await c.post(f'/api/admin/questions/{ident}/status',json={'status':'approved'})).status_code == 200
        updated = await c.put(f'/api/admin/questions/{ident}',json={**question_data(cs['Copilot']), 'prompt':'修改后的题干'})
        assert updated.json()['status'] == 'pending'
        old = (await c.get('/api/admin/questions')).json()
        assert next(q for q in old if q['id']==ident)['status'] == 'rejected'
        assert (await c.post('/api/admin/extract',json={'page_ids':['forged']})).status_code == 409
