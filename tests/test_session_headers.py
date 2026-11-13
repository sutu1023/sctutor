import httpx

from sctutor.models import Models


async def test_persisted_conversation_id_across_turns_and_checks(settings, store):
    headers = []

    def respond(request):
        headers.append(request.headers)
        return httpx.Response(200, json={'choices':[{'message':{'content':'{"ok":true}'}}]})

    sid = store.new_session()['id']
    first = store.reserve_turn(sid, 'ask', 'question', None)
    model = Models(settings, store, httpx.MockTransport(respond))
    await model.chat('sys', {}, first['id'], 'generate')
    await model.chat('sys', {}, first['id'], 'check')
    store.finish(first['id'], 'checked', [], 'completed', 1)
    second = store.reserve_turn(sid, 'followup', 'followup', None)
    await model.chat('sys', {}, second['id'], 'generate')
    assert all(h['x-opencode-session'] == sid for h in headers)
    assert all(h['user-agent'] == 'SC-Tutor/0.1' for h in headers)


async def test_other_providers_not_given_opencode_session_header(settings, store):
    settings.llm_base_url = 'https://example.com/v1'
    def respond(request):
        assert 'x-opencode-session' not in request.headers
        assert request.headers['user-agent'] == 'SC-Tutor/0.1'
        return httpx.Response(200, json={'choices':[{'message':{'content':'{"ok":true}'}}]})
    await Models(settings, store, httpx.MockTransport(respond)).chat('sys', {})
