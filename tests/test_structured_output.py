import json

import httpx
import pytest

from sctutor.config import UserError
from sctutor.models import Models


async def test_truncated_json_is_never_accepted(settings, store):
    response = {'choices':[{'finish_reason':'length','message':{'content':'{"ok":true}'}}]}
    model = Models(settings, store, httpx.MockTransport(lambda r:httpx.Response(200,json=response)))
    with pytest.raises(UserError, match='截断'):
        await model.chat('only JSON', {})


async def test_provider_specific_options_not_sent_to_other_models(settings, store):
    settings.llm_model = 'another-model'
    settings.llm_profile = 'standard'

    def respond(request):
        payload = json.loads(request.content)
        assert 'thinking' not in payload
        assert 'response_format' not in payload
        return httpx.Response(200, json={'choices':[{'message':{'content':'{"ok":true}'}}]})

    result = await Models(settings, store, httpx.MockTransport(respond)).chat('only JSON', {})
    assert result == {'ok':True}
