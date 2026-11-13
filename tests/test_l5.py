import json
import sqlite3

import pytest
from conftest import FakeModels

from sctutor.config import UserError
from sctutor.policy import Policy, PolicyInput
from sctutor.retrieval import Retriever
from sctutor.store import SCHEMA, Store
from sctutor.tutor import Tutor


async def test_complete_ladder_and_revoke(store, corpus):
    model = FakeModels()
    policy = Policy(store)
    policy.save(PolicyInput(max_level=5, allow_full_answer=True))
    sid = store.new_session()['id']
    tutor = Tutor(store, model, Retriever(store, model))
    async def run(t):
        async def emit(x):
            pass
        await tutor.run(t, emit)
        return store.turn(t['id'])
    t = await run(store.reserve_turn(sid, 'ask', 'Copilot?', None, True))
    assert t['level'] == 1
    for level in range(2, 6):
        parent = t['id']
        t = await run(store.reserve_turn(sid, 'upgrade', '', parent))
        assert t['level'] == level and t['status'] == 'completed'
        with pytest.raises(UserError):
            store.reserve_turn(sid, 'upgrade', '', parent)
    assert len([x for x in model.chats if x[0]=='check']) == 5
    policy.save(PolicyInput(max_level=5, allow_full_answer=False))
    assert store.turn(t['id'])['policy_blocked']
    with pytest.raises(UserError):
        store.citation(t['id'], t['citations'][0]['id'])
    assert store.query('SELECT answer FROM turns WHERE id=?', (t['id'],))[0]['answer']


async def test_l2_start_cannot_claim_full_ladder(store, corpus):
    Policy(store).save(PolicyInput(max_level=5, allow_full_answer=True))
    sid = store.new_session()['id']
    t = None
    for level in (2,3,4):
        t = store.reserve_turn(sid, 'ask' if level == 2 else 'upgrade', 'q' if level==2 else '', None if level==2 else t['id'])
        store.execute('UPDATE turns SET level=? WHERE id=?', (level,t['id']))
        store.finish(t['id'], 'ok', [], 'completed', 1)
    with pytest.raises(UserError, match='L1'):
        store.reserve_turn(sid, 'upgrade', '', t['id'])


async def test_restricted_evidence_never_l5(store, corpus):
    Policy(store).save(PolicyInput(max_level=5, allow_full_answer=True))
    pid = store.query('SELECT id FROM pages WHERE page=1')[0]['id']
    store.execute("INSERT INTO concepts VALUES ('restricted','作业',?,'approved',1,'sig')", (json.dumps([pid]),))
    sid = store.new_session()['id']
    t = store.reserve_turn(sid,'ask','q',None)
    store.finish(t['id'],'ok',[{'id':pid,'file':'sample.pptx','page':1}],'completed',1)
    store.execute('UPDATE turns SET level=3 WHERE id=?',(t['id'],))
    with pytest.raises(UserError):
        store.reserve_turn(sid,'upgrade','',t['id'])


def test_legacy_level_constraint_migration_with_children(tmp_path):
    data = tmp_path/'data'
    data.mkdir()
    db = data/'sctutor.sqlite3'
    c=sqlite3.connect(db)
    c.executescript(SCHEMA.replace('BETWEEN 1 AND 5','BETWEEN 1 AND 4'))
    c.execute("INSERT INTO sessions VALUES ('sid',1)")
    c.execute("INSERT INTO turns VALUES ('tid','sid',1,'ask','q',4,NULL,'completed','old','[]',1)")
    c.execute("INSERT INTO audits(turn_id,attempt,draft,verdict) VALUES ('tid',1,'{}','{}')")
    c.commit()
    c.close()
    store=Store(data)
    assert store.query('SELECT answer FROM turns')[0]['answer']=='old'
    assert len(store.query('SELECT * FROM audits'))==1
    assert store.query('PRAGMA foreign_key_check')==[]
    store.execute("UPDATE turns SET level=5 WHERE id='tid'")
    assert Store(data).query('SELECT level FROM turns')[0]['level']==5
