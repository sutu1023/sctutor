import json

import httpx
import pytest
from conftest import FakeModels

from sctutor.config import Settings, UserError
from sctutor.policy import Policy, PolicyInput
from sctutor.retrieval import Retriever
from sctutor.tutor import Tutor
from sctutor.websearch import WebSearch, public_url


@pytest.mark.parametrize('url', ['http://example.com', 'https://127.0.0.1', 'https://10.0.0.1',
    'https://localhost', 'https://name.local', 'javascript:alert(1)', 'https://a:b@example.org',
    'https://example.org:8000', 'https://[::1]', 'https://2130706433', 'https://example.org\n'])
def test_reject_private_or_unsafe_urls(url):
    assert not public_url(url)


async def test_bounded_tavily_and_safe_failures(settings, store):
    settings.tavily_api_key = 'private-key'
    calls = []
    def respond(req):
        calls.append(json.loads(req.content))
        assert req.headers['Authorization'] == 'Bearer private-key'
        return httpx.Response(200, json={'results': [
            {'url':'https://example.org/source', 'title':'课程外资料', 'content':'independent evidence'},
            {'url':'https://127.0.0.1/private', 'title':'bad', 'content':'SECRET'}]})
    web = WebSearch(settings, store, httpx.MockTransport(respond))
    sources = await web.search('necessary question', 'tid')
    assert len(sources) == 1 and sources[0]['type'] == 'web'
    assert calls == [{'query':'necessary question', 'max_results':5, 'search_depth':'basic',
                      'include_answer':False, 'include_raw_content':False}]
    assert store.query('SELECT outcome FROM calls')[0]['outcome'] == 'ok'
    for code, payload in [(401, {'secret':'not leak'}), (200, []), (200, {'results':'bad'})]:
        bad = WebSearch(settings, store, httpx.MockTransport(lambda req: httpx.Response(code, json=payload)))
        with pytest.raises(UserError) as exc:
            await bad.search('q', 'tid')
        assert 'not leak' not in str(exc.value)
    retry_calls = []
    def limited(req):
        retry_calls.append(1)
        return httpx.Response(429)
    with pytest.raises(UserError):
        await WebSearch(settings, store, httpx.MockTransport(limited)).search('q', 'tid')
    assert len(retry_calls) == 2


async def test_optional_search_citations_history_policy_and_quiz(settings, store, corpus):
    class Sources:
        calls = 0
        async def search(self, q, tid):
            self.calls += 1
            return [{'id':'web-test', 'type':'web', 'url':'https://example.org/source',
                     'title':'External', 'text':'evidence', 'retrieved':1}]
    web = Sources()
    model = FakeModels(no_evidence=True)
    tutor = Tutor(store, model, Retriever(store, model), web)
    sid = store.new_session()['id']
    async def turn(action='ask', parent=None):
        t = store.reserve_turn(sid, action, 'q' if action == 'ask' else '', parent)
        events = []
        async def emit(x):
            events.append(x)
        await tutor.run(t, emit)
        return store.turn(t['id'])
    assert (await turn())['status'] == 'safe' and web.calls == 0
    with pytest.raises(UserError):
        Policy(store).save(PolicyInput(web_search=True), Settings())
    settings.tavily_api_key = 'configured'
    Policy(store).save(PolicyInput(web_search=True), settings)
    t = await turn()
    assert t['status'] == 'completed' and t['citations'][0]['type'] == 'web'
    assert '外部搜索' in next(x[2] for x in model.chats if x[0] == 'generate')
    for _ in range(2):
        t = await turn('upgrade', t['id'])
    assert store.citation(t['id'], 'web-test')['text'] == 'evidence'
    Policy(store).save(PolicyInput())
    assert store.turn(t['id'])['policy_blocked']
    with pytest.raises(UserError):
        store.citation(t['id'], 'web-test')
    assert 'External' not in json.dumps(store.history(sid))
    with store.connect() as c:
        c.execute("INSERT INTO quizzes VALUES ('active',1,'active',NULL,'')")
        c.execute("INSERT INTO quiz_owners VALUES ('active','student')")
    with pytest.raises(UserError):
        store.reserve_turn(sid, 'ask', 'q', None)
