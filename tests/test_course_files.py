import io
import json
from zipfile import ZipFile

import httpx
import pymupdf
import pytest
from conftest import make_pptx
from starlette.datastructures import UploadFile

from sctutor.app import create_app
from sctutor.auth import IDENTITY
from sctutor.config import UserError
from sctutor.course_files import Materials, upload_files, validate_pptx
from sctutor.policy import Policy, PolicyInput
from sctutor.rendering import Renderer
from sctutor.store import Store


def valid_pptx(path, texts=('alpha source', 'beta source', 'gamma source')):
    make_pptx(path, texts)
    with ZipFile(path, 'a') as z:
        z.writestr('[Content_Types].xml', '<Types><Override ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/></Types>')
    return path


async def test_safe_upload_duplicates_conflict_and_unsafe_zip(tmp_path):
    source = valid_pptx(tmp_path/'source.pptx')
    data = source.read_bytes()
    def file(name, body=data):
        return UploadFile(io.BytesIO(body), filename=name)
    root=tmp_path/'course'
    results=await upload_files([file('lesson.pptx'),file('lesson.pptx'),file('../escape.pptx'),file('not.txt'),file('broken.pptx',b'bad')],root)
    assert [x['status'] for x in results]==['uploaded','skipped','error','error','error']
    assert (root/'uploads/lesson.pptx').read_bytes()==data
    other=valid_pptx(tmp_path/'other.pptx',('different',)).read_bytes()
    result=await upload_files([file('lesson.pptx',other)],root)
    assert result[0]['status']=='error' and '同名' in result[0]['message']
    assert (root/'uploads/lesson.pptx').read_bytes()==data
    for name, payload in [('../evil','x'),('ppt/vbaProject.bin','macro'),('ppt/_rels/unsafe.rels','<Relationships><Relationship TargetMode="External" Target="http://private"/></Relationships>')]:
        bad=valid_pptx(tmp_path/'bad.pptx')
        with ZipFile(bad,'a') as z:
            z.writestr(name,payload)
        with pytest.raises(UserError):
            validate_pptx(bad)
    with pytest.raises(UserError):
        await upload_files([file(str(i)+'.pptx') for i in range(11)],root)
    large=await upload_files([file('large.pptx',b'x'*(51*1024*1024))],root)
    assert large[0]['status']=='error' and not (root/'uploads/large.pptx').exists()
    assert not list(root.rglob('.upload-*'))


async def test_upload_privileges_no_implicit_cloud(settings, store, corpus, tmp_path):
    folder,model=corpus
    settings.course_dir=folder
    source=valid_pptx(tmp_path/'new.pptx').read_bytes()
    app=create_app(settings,store,model)
    before=len(model.embed_calls)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as c:
        assert (await c.post('/api/admin/upload',files={'files':('new.pptx',source)})).status_code==401
        await c.post('/api/auth/login',json={'account':'student'})
        assert (await c.post('/api/admin/upload',files={'files':('new.pptx',source)})).status_code==403
        await c.post('/api/auth/login',json={'account':'teacher'})
        r=await c.post('/api/admin/upload',files={'files':('new.pptx',source)})
        assert r.status_code==200 and r.json()['files'][0]['status']=='uploaded'
        assert len(model.embed_calls)==before and len(store.query('SELECT * FROM documents'))==1
        async with app.state.pipeline.lock:
            assert (await c.post('/api/admin/upload',files={'files':('new.pptx',source)})).status_code==409
        r=await c.post('/api/admin/ingest')
        assert r.status_code==200 and len(store.query('SELECT * FROM documents'))==2


async def test_material_navigation_source_version_policy_and_pdf(store, corpus):
    folder,_=corpus
    source=folder/'sample.pptx'
    renderer=Renderer(store.path.parent)
    def export(copy,pdf):
        with pymupdf.open() as doc:
            for text in ['GitHub Copilot','Python','Empty']:
                doc.new_page().insert_text((72,72),text)
            doc.save(pdf)
    rendered=renderer.render(source,export)
    for p in store.query('SELECT * FROM pages'):
        store.execute('INSERT INTO page_assets VALUES (?,?,?,?)',(p['id'],str(rendered['directory']/f'page-{p["page"]}.png'),'','test'))
    pid=store.query('SELECT id FROM pages WHERE page=1')[0]['id']
    sid=store.new_session()['id']
    t=store.reserve_turn(sid,'ask','q',None)
    store.execute('UPDATE turns SET level=3 WHERE id=?',(t['id'],))
    store.finish(t['id'],'ok',[{'id':pid,'file':'sample.pptx','page':1}],'completed',1)
    m=Materials(store,renderer)
    origin='turn:'+t['id']
    assert m.page(pid,origin,2)['page']==2
    located=m.page(pid,origin,1,'GitHub Copilot')
    assert located['rectangles'] and '真实文字坐标' in located['location']
    assert m.pdf(pid,origin).name=='slides.pdf'
    with pytest.raises(UserError):
        m.page(pid,origin,4)
    with pytest.raises(UserError):
        m.page(pid,origin,1,'not in source')
    token=IDENTITY.set('teacher')
    try:
        with pytest.raises(UserError):
            m.page(pid,origin,2)
    finally:
        IDENTITY.reset(token)
    second=store.query('SELECT id FROM pages WHERE page=2')[0]['id']
    store.execute("INSERT INTO concepts VALUES ('restricted','作业',?,'approved',1,'sig')",(json.dumps([second]),))
    with pytest.raises(UserError):
        m.page(pid,origin,2)
    with pytest.raises(UserError):
        m.pdf(pid,origin)
    assert m.page(pid,origin,1)['id']==pid
    Policy(store).save(PolicyInput(max_level=2))
    with pytest.raises(UserError):
        m.page(pid,origin,1)
    Policy(store).save(PolicyInput())
    with store.connect() as c:
        c.execute("INSERT INTO quizzes VALUES ('active',1,'active',NULL,'')")
        c.execute("INSERT INTO quiz_owners VALUES ('active','student')")
    with pytest.raises(UserError):
        m.pdf(pid,origin)
    store.execute("UPDATE quizzes SET status='cancelled'")
    source.write_bytes(source.read_bytes()+b'changed')
    with pytest.raises(UserError,match='变化'):
        m.page(pid,origin,1)
    assert Store(store.path.parent).query('PRAGMA foreign_key_check')==[]


async def test_chunked_upload_limit_before_multipart_handler(settings,store,corpus):
    _,model=corpus
    app=create_app(settings,store,model)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as c:
        await c.post('/api/auth/login',json={'account':'teacher'})
        async def oversized():
            yield b'--boundary\r\nContent-Disposition: form-data; name="files"; filename="big.pptx"\r\n\r\n'
            for _ in range(102):
                yield b'x'*(1024*1024)
            yield b'\r\n--boundary--\r\n'
        result=await c.post('/api/admin/upload',content=oversized(),headers={'Content-Type':'multipart/form-data; boundary=boundary'})
        assert result.status_code==413
        assert not store.query("SELECT * FROM processing WHERE kind='upload'")


async def test_course_selection_scopes_retrieval_and_followups(store,corpus):
    from conftest import FakeModels

    from sctutor.retrieval import Retriever, ingest
    folder,_=corpus
    valid_pptx(folder/'second.pptx',('different course',))
    model=FakeModels()
    await ingest(store,model,folder)
    doc=store.query("SELECT id FROM documents WHERE path='second.pptx'")[0]['id']
    sid=store.new_session()['id']
    t=store.reserve_turn(sid,'ask','Copilot',None,course_id=doc)
    sources=await Retriever(store,model).search('Copilot',t['id'])
    assert sources and all(x['file']=='second.pptx' for x in sources)
    store.finish(t['id'],'ok',[],'completed',1)
    follow=store.reserve_turn(sid,'followup','continue',None)
    assert store.query('SELECT selected_doc FROM turn_options WHERE turn_id=?',(follow['id'],))[0]['selected_doc']==doc
    with pytest.raises(UserError):
        store.reserve_turn(sid,'ask','q',None,course_id='forged')


async def test_material_api_no_unbound_ids(settings,store,corpus):
    _,model=corpus
    pid=store.query('SELECT id FROM pages LIMIT 1')[0]['id']
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(settings,store,model)),base_url='http://127.0.0.1') as c:
        await c.post('/api/auth/login',json={'account':'student'})
        assert (await c.get('/api/courses')).json()[0]['pages']==3
        for suffix in ['', '/file', '/image']:
            r=await c.get(f'/api/materials/{pid}{suffix}',params={'origin':'turn:forged','page':1})
            assert r.status_code==409
