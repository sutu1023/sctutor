import json

import httpx
import pytest
from conftest import FakeModels
from pydantic import ValidationError
from test_learning import learning_data as learning_data
from test_learning import question_data
from test_tutor import run_turn

from sctutor.app import create_app
from sctutor.config import UserError
from sctutor.knowledge import Knowledge
from sctutor.learning import report
from sctutor.policy import BLOCKED, Policy, PolicyInput
from sctutor.quiz import Quiz
from sctutor.store import Store


def test_policy_schema_and_literal_keywords(store):
    policy = Policy(store)
    assert policy.get()['max_level'] == 4 and policy.get()['homework_level'] == 3
    policy.save(PolicyInput(max_level=1, blocked_keywords=[' SECRET ', '[a-z]']))
    assert policy.assess('my secret')['blocked']
    assert not policy.assess('xyz')['blocked']  # Never regex-match a teacher string.
    assert Store(store.path.parent).query('SELECT version FROM teaching_policy')[0]['version'] == 1
    for data in [{'max_level':6}, {'max_level':True}, {'homework_level':4}, {'max_level':'2'},
                 {'blocked_keywords':[' ']}, {'blocked_keywords':['x'*121]},
                 {'blocked_keywords':['a']*51}, {'unknown':'instruction'}]:
        with pytest.raises(ValidationError):
            PolicyInput(**data)
    with pytest.raises(UserError):
        policy.save(PolicyInput(blocked_concepts=['not-approved']))


async def test_low_global_cap_applies_to_definition_and_upgrade(store, corpus):
    policy = Policy(store)
    policy.save(PolicyInput(max_level=1))
    class Definition(FakeModels):
        async def chat(self, system, payload, tid=None, purpose='generate'):
            if purpose == 'classify':
                return {'type':'definition','concept_ids':[]}
            return await super().chat(system, payload, tid, purpose)
    t, _, sid = await run_turn(store, Definition())
    assert t['level'] == 1 and t['status'] == 'completed'
    with pytest.raises(UserError):
        store.reserve_turn(sid, 'upgrade', '', t['id'])
    follow, _, _ = await run_turn(store, Definition(), 'followup', '我再想想', sid=sid)
    assert follow['level'] == 1


async def test_homework_cap_uses_evidence_not_model_exemption(store, learning_data):
    _, cs, model = learning_data
    store.execute('UPDATE concepts SET restricted=1 WHERE id=?', (cs['Copilot']['id'],))
    t, _, sid = await run_turn(store, model)
    assert t['max_level'] == 3
    for _ in range(2):
        t, _, _ = await run_turn(store, model, 'upgrade', '', t['id'], sid)
    assert t['level'] == 3
    with pytest.raises(UserError):
        store.reserve_turn(sid, 'upgrade', '', t['id'])
    assert t['candidates'] and t['concepts'] == []  # Invalid classifier is unknown, not inferred mastery.


async def test_policy_blocks_legacy_history_preview_and_context(store, corpus):
    _, model = corpus
    t, _, sid = await run_turn(store, model)
    for _ in range(2):
        t, _, _ = await run_turn(store, model, 'upgrade', '', t['id'], sid)
    pid = t['citations'][0]['id']
    assert store.citation(t['id'], pid)
    policy = Policy(store)
    policy.save(PolicyInput(max_level=2))
    hidden = store.turn(t['id'])
    assert hidden['answer'] == BLOCKED and hidden['policy_blocked']
    assert hidden['citations'] == hidden['concepts'] == hidden['candidates'] == []
    with pytest.raises(UserError):
        store.citation(t['id'], pid)
    assert all('L3' not in x['answer'] for x in store.context(sid, 'new'))
    # The actual source/answer is retained locally, not destroyed by policy edits.
    assert store.query('SELECT citations FROM turns WHERE id=?', (t['id'],))[0]['citations'] != '[]'
    store.execute('DELETE FROM turn_context')  # Legacy turns remain protected by citations.
    policy.save(PolicyInput(blocked_keywords=['copilot']))
    assert store.turn(t['id'])['policy_blocked']
    with pytest.raises(UserError):
        store.reserve_turn(sid, 'ask', 'COPILOT是什么', None)
    policy.save(PolicyInput())
    assert store.turn(t['id'])['citations']


async def test_verified_concept_association_and_followup_restore(store, learning_data):
    _, cs, _ = learning_data
    cid = cs['Copilot']['id']
    class Classified(FakeModels):
        async def chat(self, system, payload, tid=None, purpose='generate'):
            if purpose == 'classify':
                return {'type':'concept','concept_ids':[cid]}
            return await super().chat(system, payload, tid, purpose)
    t, _, sid = await run_turn(store, Classified())
    assert [x['id'] for x in t['concepts']] == [cid]
    follow, _, _ = await run_turn(store, Classified(), 'followup', '再思考一下', sid=sid)
    assert [x['id'] for x in follow['concepts']] == [cid]
    restored = Store(store.path.parent)
    assert restored.turn(follow['id'])['concepts'] == follow['concepts']
    assert 'probability' in follow['concepts'][0]


async def test_running_result_rechecks_policy_after_check(store, corpus):
    class ChangedDuringCheck(FakeModels):
        async def chat(self, system, payload, tid=None, purpose='generate'):
            result = await super().chat(system, payload, tid, purpose)
            if purpose == 'check':
                Policy(store).save(PolicyInput(blocked_keywords=['Copilot']))
            return result
    t, events, _ = await run_turn(store, ChangedDuringCheck())
    assert t['status'] == 'error'
    assert not any(e['event'] == 'answer' for e in events)
    assert '你能先说说' not in json.dumps(events, ensure_ascii=False)


async def test_keywords_cannot_be_bypassed_using_generic_followup(store, corpus):
    _, model = corpus
    t, _, sid = await run_turn(store, model, message='project-secret Copilot')
    t, _, _ = await run_turn(store, model, 'followup', '再说说', sid=sid)
    Policy(store).save(PolicyInput(blocked_keywords=['project-secret']))
    assert store.turn(t['id'])['policy_blocked']
    with pytest.raises(UserError):
        store.reserve_turn(sid, 'followup', '继续', None)


async def test_banned_concept_and_page_cannot_leak_via_report_or_quiz(settings, store, learning_data):
    k, cs, model = learning_data
    quiz = Quiz(store, model)
    question = quiz.add_question(question_data(cs['Copilot']))['id']
    quiz.review_question(question, 'approved')
    active = await quiz.start(1)
    iid = active['items'][0]['id']
    Policy(store).save(PolicyInput(blocked_concepts=[cs['Copilot']['id']]))
    assert quiz.view(active['id'])['items'][0]['available'] is False
    serialized = json.dumps(quiz.view(active['id']), ensure_ascii=False)
    assert '哪一个是课程' not in serialized and '参考答案' not in serialized
    with pytest.raises(UserError):
        await quiz.submit(iid, 'Copilot', 1, 'request')
    assert store.query('SELECT count(*) n FROM attempts')[0]['n'] == 0
    assert not quiz.questions()[0]['available']
    assert 'Copilot' not in json.dumps(report(store, k), ensure_ascii=False)
    app = create_app(settings, store, model)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
        await client.post('/api/auth/login', json={'account':'student'})
        await client.post(f'/api/quizzes/{active["id"]}/cancel')
        assert (await client.get(f'/api/knowledge/{cs["Copilot"]["id"]}/pages/{cs["Copilot"]["pages"][0]}')).status_code == 409
        assert (await client.post('/api/quizzes', json={'count':1})).status_code == 409
    # Shared teacher management can still inspect and remove the restriction.
    assert any(x['id'] == cs['Copilot']['id'] for x in Knowledge(store).concepts())
    Policy(store).save(PolicyInput())
    assert quiz.questions()[0]['available']


async def test_low_cap_hides_existing_l2_hint(store, learning_data):
    _, cs, model = learning_data
    quiz = Quiz(store, model)
    qid = quiz.add_question(question_data(cs['Copilot']))['id']
    quiz.review_question(qid, 'approved')
    active = await quiz.start(1)
    iid = active['items'][0]['id']
    store.execute("UPDATE quiz_items SET hint='secret L2 concept',attempts=1 WHERE id=?", (iid,))
    Policy(store).save(PolicyInput(max_level=1))
    assert 'secret' not in quiz.view(active['id'])['items'][0]['hint']
    assert '未开放' in await quiz.hint(question_data(cs['Copilot']), 'wrong')


async def test_teacher_only_management_and_public_capabilities(settings, store, corpus):
    _, model = corpus
    app = create_app(settings, store, model)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as c:
        assert (await c.get('/api/admin/overview')).status_code == 401
        await c.post('/api/auth/login', json={'account':'student'})
        for path in ('/api/admin/overview','/api/admin/policy'):
            assert (await c.get(path)).status_code == 403
        assert (await c.put('/api/admin/policy', json={'max_level':1})).status_code == 403
        assert (await c.get('/api/policy')).json()['l5'] is False
        await c.post('/api/auth/login', json={'account':'teacher'})
        assert (await c.put('/api/admin/policy', json={'max_level':2})).status_code == 200
        assert (await c.put('/api/admin/policy', json={'max_level':6})).status_code == 422
        assert (await c.put('/api/admin/policy', json={'max_level':5})).status_code == 200
        assert (await c.get('/api/policy')).json()['l5'] is False
        assert (await c.get('/api/admin/policy')).json()['max_level'] == 5
        assert (await c.get('/api/admin/overview')).status_code == 200
