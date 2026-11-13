import json

from conftest import FakeModels

from sctutor.retrieval import Retriever
from sctutor.tutor import Tutor


async def test_empty_l1_citation_is_rejected_then_repaired(store, corpus):
    model = FakeModels()
    original = model.chat
    generations = 0

    async def chat(system, payload, tid=None, purpose='generate'):
        nonlocal generations
        response = await original(system, payload, tid, purpose)
        if purpose == 'generate':
            generations += 1
            assert '不能留空' in system
            if generations == 1:
                response['citation_ids'] = []
            else:
                assert '至少选一个' in payload['revision_feedback']
        return response

    model.chat = chat
    sid = store.new_session()['id']
    turn = store.reserve_turn(sid, 'ask', 'Copilot 是什么？', None)
    events = []
    async def emit(event):
        events.append(event)
    await Tutor(store, model, Retriever(store, model)).run(turn, emit)
    result = store.turn(turn['id'])
    assert result['level'] == 1 and result['status'] == 'completed'
    assert result['citations']
    assert len([e for e in events if e['event']=='answer']) == 1
    audits = store.query('SELECT verdict FROM audits ORDER BY id')
    assert len(audits) == 2
    assert json.loads(audits[0]['verdict'])['passed'] is False
