import asyncio

import httpx
import pytest

from sctutor.app import create_app
from sctutor.auth import IDENTITY
from sctutor.config import UserError
from sctutor.store import Store


def test_request_reservation_survives_completion_restart_and_delete(store):
    sid=store.new_session()['id']
    turn=store.reserve_turn(sid,'ask','question',None,request_id='stable-request')
    with pytest.raises(UserError,match='已受理'):
        store.reserve_turn(sid,'ask','changed body',None,request_id='stable-request')
    store.finish(turn['id'],'checked',[],'completed',1)
    reopened=Store(store.path.parent)
    assert reopened.history(sid)[0]['request_id']=='stable-request'
    with pytest.raises(UserError,match='已受理'):
        reopened.reserve_turn(sid,'ask','question',None,request_id='stable-request')
    # Ownership is checked before the token lookup; IDs cannot reveal another account.
    token=IDENTITY.set('teacher')
    try:
        with pytest.raises(UserError):
            reopened.reserve_turn(sid,'ask','question',None,request_id='stable-request')
        own=reopened.new_session()['id']
        other=reopened.reserve_turn(own,'ask','question',None,request_id='stable-request')
        reopened.finish(other['id'],'done',[],'completed',1)
    finally:
        IDENTITY.reset(token)
    reopened.delete_sessions([sid])
    assert not reopened.query('SELECT 1 FROM turn_requests WHERE session_id=?',(sid,))
    assert not reopened.query('PRAGMA foreign_key_check')


async def test_duplicate_http_request_does_not_invoke_model_again(settings,store,corpus):
    _,models=corpus
    app=create_app(settings,store,models)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
        await client.post('/api/auth/login',json={'account':'student'})
        sid=(await client.post('/api/sessions')).json()['id']
        body={'action':'ask','message':'Copilot','request_id':'stable-http-request'}
        assert (await client.post(f'/api/sessions/{sid}/turns',json=body)).status_code==200
        calls=len(models.chats)
        results=await asyncio.gather(*(client.post(f'/api/sessions/{sid}/turns',json=body) for _ in range(4)))
        assert all(r.status_code==409 for r in results)
        assert len(models.chats)==calls
        history=(await client.get(f'/api/sessions/{sid}')).json()['turns']
        assert len(history)==1 and history[0]['request_id']==body['request_id']
        assert (await client.post(f'/api/sessions/{sid}/turns',json={**body,'request_id':'x'})).status_code==422
