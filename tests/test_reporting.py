import csv
import io

import httpx
import pytest
from test_analytics import recorded
from test_learning import learning_data as learning_data

from sctutor.analytics import overview
from sctutor.app import create_app
from sctutor.config import UserError
from sctutor.reporting import Window, csv_text


@pytest.mark.parametrize('start,end',[(None,2),(2,None),(3,2),(1,1),(-1,2),(0,367*86400),(float('nan'),2),(1,float('inf'))])
def test_invalid_window(start,end):
    with pytest.raises(UserError):
        Window.make(start,end)


def test_csv_formula_and_control_characters():
    result=list(csv.reader(io.StringIO(csv_text([['=cmd', '+SUM(1)', '-malicious', '@x', '\t=1', 'normal,quoted', 'null\x00']]).lstrip('\ufeff'))))
    assert all(x.startswith("'") for x in result[0][:5])
    assert result[0][-2:]==['normal,quoted','null']


async def test_half_open_period_boundaries_and_l5_reached_l4(store,corpus):
    for stamp in (99,100,199,200):
        recorded(store,stamp,level=5 if stamp==199 else 1)
    result=overview(store,now=200,start=100)
    assert result['questions']==2 and result['l4_questions']==1 and result['l4_rate']==.5
    assert overview(store,now=300,start=200)['questions']==1


async def test_date_apis_csv_roles_and_active_quiz(settings,store,learning_data):
    _,cs,model=learning_data
    recorded(store,150,concepts=[cs['Copilot']['id']])
    app=create_app(settings,store,model)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as c:
        assert (await c.get('/api/report/export')).status_code==401
        await c.post('/api/auth/login',json={'account':'student'})
        params={'start':100,'end':200}
        report=(await c.get('/api/report',params=params)).json()
        assert report['summary']['involved']==[cs['Copilot']['id']]
        assert '当前快照' in report['notice']
        r=await c.get('/api/report/export',params=params)
        assert r.status_code==200 and 'text/csv' in r.headers['content-type']
        assert 'Copilot' in r.text and '150' not in r.text  # no raw turn rows
        assert (await c.get('/api/report',params={'start':300,'end':100})).status_code==409
        assert (await c.get('/api/report',params={'start':100})).status_code==409
        assert (await c.get('/api/admin/overview/export')).status_code==403
        await c.post('/api/auth/login',json={'account':'teacher'})
        summary=(await c.get('/api/admin/overview',params=params)).json()
        assert summary['questions']==1 and summary['start']==100 and summary['end']==200
        r=await c.get('/api/admin/overview/export',params=params)
        assert r.status_code==200 and 'questions,1' in r.text
        assert 'q,' not in r.text and 'turn_id' not in r.text and 'session_id' not in r.text
        assert (await c.get('/api/report',params=params)).json()['summary']['involved']==[]
        await c.post('/api/auth/login',json={'account':'student'})
        with store.connect() as db:
            db.execute("INSERT INTO quizzes VALUES ('active',1,'active',NULL,'')")
            db.execute("INSERT INTO quiz_owners VALUES ('active','student')")
        assert (await c.get('/api/report/export',params=params)).status_code==409
        assert (await c.get('/api/courses')).status_code==409
