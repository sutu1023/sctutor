import json

import pytest
from test_learning import learning_data as learning_data
from test_learning import question_data

from sctutor.analytics import WEEK, overview, problems, processing_summary
from sctutor.auth import IDENTITY
from sctutor.knowledge import Review
from sctutor.learning import report
from sctutor.quiz import Quiz


def recorded(store, now, action='ask', sid=None, level=1, closed=False, concepts=(), status='completed'):
    sid = sid or store.new_session()['id']
    # Direct fixture deliberately includes secrets; the aggregate must never return them.
    turn = store.reserve_turn(sid, action, 'PRIVATE_QUESTION<script>bad()</script>', None)
    store.execute('UPDATE turns SET created=?,level=? WHERE id=?', (now, level, turn['id']))
    store.finish(turn['id'], 'PRIVATE_ANSWER', [], status, 1)
    store.execute("INSERT INTO turn_context VALUES (?,?, '[]',0)", (turn['id'], json.dumps(list(concepts))))
    if closed:
        store.close_question(sid, turn['id'])
    return turn, sid


def add_audit(store, turn, at, verdict):
    store.audit(turn['id'], 1, {'text':'PRIVATE_DRAFT'}, verdict)
    store.execute('UPDATE audit_times SET created=? WHERE audit_id=(SELECT max(id) FROM audits)', (at,))


async def test_weekly_problem_grouping_denominators_and_privacy(store, learning_data):
    _, cs, _ = learning_data
    now = 2000000000
    cid = cs['Copilot']['id']
    # Old ask + in-window followup is not an in-window new problem.
    old, sid = recorded(store, now-WEEK-1)
    recorded(store, now-2, 'followup', sid, level=4)
    # Inclusive boundary, one new problem despite a followup, ended at L3.
    a, sid = recorded(store, now-WEEK, concepts=[cid])
    b, _ = recorded(store, now-100, 'followup', sid, 3, closed=True, concepts=[cid])
    c, _ = recorded(store, now-20, level=4, concepts=[cid])
    recorded(store, now-10, status='error')
    # Teacher activity is never included in student aggregates.
    token = IDENTITY.set('teacher')
    try:
        recorded(store, now-5, level=4, closed=True, concepts=[cid])
    finally:
        IDENTITY.reset(token)
    add_audit(store, a, now-WEEK, {'passed':True,'level_ok':True,'supported':True,'reason':'PRIVATE_REASON'})
    add_audit(store, b, now-50, {'passed':False,'level_ok':False,'supported':True,'reason':'PRIVATE_REASON'})
    add_audit(store, c, now-20, {'passed':False,'reason':'检查服务调用失败，草稿未发送。'})
    add_audit(store, old, now-WEEK-1, {'passed':False,'level_ok':False,'supported':True,'reason':'PRIVATE_REASON'})
    data = overview(store, now)
    assert data['questions'] == 3 and data['ended'] == 1
    assert data['average_end_level'] == 3 and data['unfinished'] == 2
    assert data['l4_questions'] == 1 and data['l4_rate'] == pytest.approx(1/3)
    assert data['checks'] == 2 and data['rejected'] == 1 and data['rejection_rate'] == .5
    assert data['check_errors'] == 1
    assert data['difficulties'][0]['id'] == cid and data['difficulties'][0]['count'] == 1
    assert data['unattributed'] == 1
    text = json.dumps(data)
    for secret in ['PRIVATE_', a['id'], b['id'], c['id'], sid, '<script>']:
        assert secret not in text
    assert {x['category'] for x in data['recent_checks']} == {'检查通过','级别越界','检查失败'}


async def test_empty_statistics_and_bounded_safe_reasons(store, corpus):
    now = 2000000000
    empty = overview(store, now)
    assert empty['questions'] == 0 and empty['average_end_level'] is None
    assert empty['l4_rate'] is None and empty['rejection_rate'] is None
    t, _ = recorded(store, now-5)
    for i in range(25):
        add_audit(store, t, now-i, {'passed':False,'reason':'PRIVATE_ANSWER'})
    data = overview(store, now)
    assert len(data['recent_checks']) == 20
    assert data['rejection_rate'] == 1 and data['checks'] == 24  # end is exclusive
    assert all(r['category'] == '格式无效' for r in data['recent_checks'])
    # Legacy audit has no timestamp: report explicitly documents turn-time fallback.
    store.execute('DELETE FROM audit_times')
    assert overview(store, now)['checks'] == 25


async def test_report_graph_week_marks_and_independent_first_answers(store, learning_data):
    k, cs, model = learning_data
    now = 2000000000
    cid = cs['Copilot']['id']
    recorded(store, now-1, concepts=[cid])
    k.review('relations', k.relations()[0]['id'], Review(status='approved'))
    quiz = Quiz(store, model, clock=lambda: now-1)
    qid = quiz.add_question(question_data(cs['Copilot']))['id']
    quiz.review_question(qid, 'approved')
    q = await quiz.start(1)
    await quiz.submit(q['items'][0]['id'], 'Copilot', 1, 'first-answer')
    quiz.finish(q['id'])
    data = report(store, k, now)
    assert len(data['relations']) == 1
    assert next(x for x in data['concepts'] if x['id']==cid)['asked_this_week']
    assert data['summary']['involved'] == [cid]
    assert data['summary']['attempted'] == 1 and data['summary']['first_correct'] == 1
    assert data['summary']['accuracy'] == 1
    token = IDENTITY.set('teacher')
    try:
        own = report(store, k, now)
        assert own['summary']['involved'] == [] and own['summary']['accuracy'] is None
        assert all(x['probability'] is None for x in own['concepts'])
    finally:
        IDENTITY.reset(token)
    store.execute('DELETE FROM documents')
    data = report(store, k, now)
    assert data['concepts'] == data['relations'] == []


async def test_processing_summary_true_counts_and_legacy_unknown(store, corpus):
    data = processing_summary(store)
    assert len(data['files']) == 1 and data['pages'] == 3
    assert data['caption_candidates'] == 0
    assert data['latest_ingest']['files'][0]['change'] == 'added'
    assert data['latest_ingest']['files'][0]['pages'] == 3
    store.execute('DELETE FROM processing')
    assert processing_summary(store)['latest_ingest'] is None
    assert processing_summary(store)['pages'] == 3


async def test_weekly_graph_includes_followup_of_older_question(store, learning_data):
    k, cs, _ = learning_data
    now = 2000000000
    cid = cs['Copilot']['id']
    _, sid = recorded(store, now-WEEK-1, concepts=[cid])
    recorded(store, now-1, 'followup', sid, concepts=[cid])
    data = report(store, k, now)
    assert data['summary']['involved'] == [cid]
    assert next(x for x in data['concepts'] if x['id'] == cid)['asked_this_week']
    # A graph activity marker is not a second independent new question.
    assert overview(store, now)['questions'] == 0
    token = IDENTITY.set('teacher')
    try:
        assert report(store, k, now)['summary']['involved'] == []
    finally:
        IDENTITY.reset(token)


async def test_caption_summary_removes_all_unicode_whitespace(store, corpus):
    store.execute("UPDATE pages SET text=?,images=1 WHERE page=1", ('字'*29+'\u3000'*30,))
    store.execute("UPDATE pages SET text=?,images=1 WHERE page=2", ('字'*30+'\r\n\t\u00a0\u2003',))
    store.execute("UPDATE pages SET text=?,images=0 WHERE page=3", ('\u3000\r\n',))
    data = processing_summary(store)
    assert data['caption_candidates'] == 1
    assert data['files'][0]['caption_candidates'] == 1
    assert data['pages'] == 3


async def test_problem_unknown_orphan_not_relabelled_as_new(store, corpus):
    now = 2000000000
    t, sid = recorded(store, now-4)
    store.execute("UPDATE turns SET action='followup' WHERE id=?", (t['id'],))
    assert problems(store, 'student', now) == []
    # Reopening the same database preserves the new tables and existing learning rows.
    from sctutor.store import Store
    restored = Store(store.path.parent)
    assert restored.sessions()[0]['id'] == sid
