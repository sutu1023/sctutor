import asyncio
import json

import httpx
import pytest
from test_learning import learning_data as learning_data
from test_learning import question_data

from sctutor.app import create_app
from sctutor.config import UserError
from sctutor.knowledge import Review
from sctutor.processing import Options


@pytest.fixture
async def pipeline(settings, store, learning_data, monkeypatch):
    k, cs, model = learning_data
    calls = []
    verdict = dict(passed=True, supported=True, answer_ok=True, rubric_ok=True,
                   restricted=False, uncertain=False, reason='依据明确')
    original = model.chat
    async def chat(system, payload, tid=None, purpose='generate'):
        calls.append(purpose)
        if purpose == 'knowledge':
            return {'concepts':[], 'relations':[]}
        if purpose == 'questions':
            concept = {**payload['concept'], 'pages':[p['id'] for p in payload['sources']]}
            return question_data(concept)
        if purpose == 'auto-review':
            return dict(verdict)
        return await original(system, payload, tid, purpose)
    model.chat = chat
    async def no_ingest(*args):
        return [{'status':'unchanged'}]
    async def no_enhance(*args):
        return [{'status':'enhanced'}]
    monkeypatch.setattr('sctutor.processing.ingest', no_ingest)
    monkeypatch.setattr('sctutor.processing.enhance', no_enhance)
    p = create_app(settings, store, model).state.pipeline
    return p, k, cs, calls, verdict


@pytest.mark.parametrize('generate,review', [(False,False),(True,False),(False,True),(True,True)])
async def test_all_four_options_and_repeat_cache(store, pipeline, generate, review):
    p, _, cs, calls, _ = pipeline
    existing = p.quiz.add_question(question_data(cs['Copilot']))['id']
    options = Options(generate_questions=generate, review_questions=review)
    await p.start(options)
    await p.task
    assert p.jobs()[0]['status'] == 'completed'
    questions = p.quiz.questions()
    assert len(questions) == (3 if generate else 1)
    assert all(q['status'] == ('approved' if review else 'pending') for q in questions)
    assert ('questions' in calls) == generate
    # Review-only must handle the existing pending question.
    assert next(q for q in questions if q['id'] == existing)['status'] == ('approved' if review else 'pending')
    before = list(calls)
    await p.start(options)
    await p.task
    assert calls == before
    assert len(p.quiz.questions()) == len(questions)
    assert not p.lock.locked()


@pytest.mark.parametrize('field,value', [('passed',False),('supported',False),('answer_ok',False),('rubric_ok',False),('restricted',True),('uncertain',True)])
async def test_unsafe_question_verdict_never_approved(pipeline, field, value):
    p, _, cs, _, verdict = pipeline
    verdict[field] = value
    qid = p.quiz.add_question(question_data(cs['Copilot']))['id']
    result = await p.auto_review('questions', p.current('questions', qid))
    assert result['status'] == 'manual'
    assert p.current('questions', qid)['status'] == 'pending'


async def test_auto_review_rejected_restricted_invalid_and_changed_sources(store, pipeline):
    p, k, cs, calls, _ = pipeline
    qid = p.quiz.add_question(question_data(cs['Copilot']))['id']
    p.quiz.review_question(qid, 'rejected')
    assert (await p.auto_review('questions', p.current('questions', qid)))['status'] == 'skipped'
    assert not calls
    k.review('concepts', cs['Copilot']['id'], Review(status='approved', restricted=True))
    assert (await p.auto_review('questions', {**p.current('questions', qid), 'status':'pending'}))['status'] == 'blocked'
    k.review('concepts', cs['Copilot']['id'], Review(status='approved', restricted=False))
    qid = p.quiz.add_question(question_data(cs['Copilot']))['id']
    async def invalid(*args, **kwargs):
        return {'passed':'true'}
    p.models.chat = invalid
    assert (await p.auto_review('questions', p.current('questions', qid)))['status'] == 'blocked'
    assert p.current('questions', qid)['status'] == 'pending'
    async def changes(*args, **kwargs):
        p.quiz.review_question(qid, 'rejected')
        return dict(passed=True,supported=True,answer_ok=True,rubric_ok=True,restricted=False,uncertain=False,reason='ok')
    p.models.chat = changes
    assert (await p.auto_review('questions', p.current('questions', qid)))['status'] == 'blocked'
    assert p.current('questions', qid)['status'] == 'rejected'
    assert store.query('SELECT * FROM auto_reviews')


async def test_knowledge_review_cycle_restriction_and_dependency(store, pipeline):
    p, k, cs, _, verdict = pipeline
    c = cs['Copilot']
    k.review('concepts', c['id'], Review(status='pending'))
    verdict['restricted'] = True
    assert (await p.auto_review('concepts', p.current('concepts', c['id'])))['status'] == 'manual'
    assert p.current('concepts', c['id'])['restricted']
    alias = k.aliases()[0]
    assert (await p.auto_review('aliases', alias))['status'] == 'blocked'
    k.review('concepts', c['id'], Review(status='approved', restricted=False))
    verdict['restricted'] = False
    relation = k.relations()[0]
    assert (await p.auto_review('relations', relation))['status'] == 'approved'
    store.execute("INSERT INTO relations VALUES ('cycle',?,?,?,'pending')", (cs['Copilot']['id'], cs['Python']['id'], json.dumps(cs['Python']['pages'])))
    assert (await p.auto_review('relations', p.current('relations', 'cycle')))['status'] == 'blocked'
    assert p.current('relations', 'cycle')['status'] == 'pending'


async def test_text_pipeline_skips_office_explicitly(pipeline, monkeypatch):
    p, _, _, _, _ = pipeline
    p.settings.enable_enhancement = False

    async def forbidden(*args):
        raise AssertionError('Office must not be invoked')

    monkeypatch.setattr('sctutor.processing.enhance', forbidden)
    await p.start(Options())
    await p.task
    assert p.jobs()[0]['status'] == 'completed'
    assert any(e.get('status') == 'skipped' and e['stage'] == 'enhance'
               for e in p.jobs()[0]['detail'])
    p.settings.enable_enhancement = True

    async def failed(*args):
        return [{'status': 'blocked'}]

    monkeypatch.setattr('sctutor.processing.enhance', failed)
    await p.start(Options())
    await p.task
    assert p.jobs()[0]['status'] == 'blocked'


async def test_pipeline_conflict_failure_and_restart(pipeline, monkeypatch):
    p, _, _, _, _ = pipeline
    gate = asyncio.Event()
    async def blocked(*args):
        await gate.wait()
        return [{'status':'error'}]
    monkeypatch.setattr('sctutor.processing.ingest', blocked)
    await p.start(Options())
    with pytest.raises(UserError, match='正在处理'):
        await p.start(Options())
    gate.set()
    await p.task
    assert p.jobs()[0]['status'] == 'blocked'
    assert not p.lock.locked()
    p.store.execute("UPDATE pipeline_jobs SET status='running'")
    p.recover()
    assert p.jobs()[0]['status'] == 'blocked'
    assert '中断' in p.jobs()[0]['detail'][0]['message']


async def test_pipeline_http_options_status_and_authorization(settings, store, pipeline):
    p, _, _, _, _ = pipeline
    app = create_app(settings, store, p.models)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as c:
        assert (await c.post('/api/admin/pipeline', json={})).status_code == 401
        await c.post('/api/auth/login', json={'account':'student'})
        assert (await c.post('/api/admin/pipeline', json={})).status_code == 403
        await c.post('/api/auth/login', json={'account':'teacher'})
        assert (await c.post('/api/admin/pipeline', json={'generate_questions':'yes'})).status_code == 422
        started = await c.post('/api/admin/pipeline', json={'generate_questions':True,'review_questions':True})
        assert started.status_code == 202
        await app.state.pipeline.task
        status = (await c.get('/api/admin/processing')).json()
        assert status['jobs'][0]['options'] == {'generate_questions':True,'review_questions':True}
        assert status['jobs'][0]['status'] == 'completed'
        assert not status['running']


async def test_cached_negative_review_stays_manual(pipeline):
    p, _, cs, calls, verdict = pipeline
    verdict['uncertain'] = True
    qid = p.quiz.add_question(question_data(cs['Copilot']))['id']
    item = p.current('questions', qid)
    assert (await p.auto_review('questions', item))['status'] == 'manual'
    before = list(calls)
    assert (await p.auto_review('questions', item))['status'] == 'manual'
    assert calls == before
    assert p.current('questions', qid)['status'] == 'pending'


async def test_suspected_assignment_retained_for_manual(pipeline):
    p, k, cs, calls, _ = pipeline
    k.review('concepts', cs['Copilot']['id'], Review(status='pending', name='作业提交任务'))
    result = await p.auto_review('concepts', p.current('concepts', cs['Copilot']['id']))
    assert result['status'] == 'manual'
    assert not calls
    assert p.current('concepts', cs['Copilot']['id'])['restricted']
