import json
from types import SimpleNamespace

import httpx
import pymupdf
import pytest
from conftest import FakeModels, make_pptx

from sctutor.config import UserError
from sctutor.models import Models
from sctutor.rendering import Renderer
from sctutor.retrieval import ingest, near_duplicate, prefer_body
from sctutor.vision import enhance


async def test_caption_cache_atomic_failure_and_indexing(store, tmp_path):
    course = tmp_path / 'course'
    course.mkdir()
    make_pptx(course / 'one.pptx', ('图',))
    class Vision(FakeModels):
        settings = SimpleNamespace(llm_base_url='https://example.com', llm_model='deepseek-v4.1-flash')
        calls = 0
        fail_caption = False
        async def chat(self, system, payload, tid=None, purpose='generate', image=None):
            if purpose == 'vision':
                self.calls += 1
                assert image.startswith(b'\x89PNG')
                if self.fail_caption:
                    return {'description': ''}
                return {'description':'图中展示编程助手的交互界面', 'uncertain':False}
            return await super().chat(system,payload,tid,purpose)
    model = Vision()
    await ingest(store, model, course)
    store.execute('UPDATE pages SET images=1')
    renderer = Renderer(store.path.parent)
    def export(copy, pdf):
        with pymupdf.open() as doc:
            doc.new_page()
            doc.save(pdf)
    class OfflineRenderer:
        def render(self, path):
            return renderer.render(path, export)
    result = await enhance(store, model, course, OfflineRenderer())
    assert result[0]['status'] == 'enhanced' and result[0]['described_pages'] == 1
    assert '机器生成' in store.query('SELECT caption FROM page_assets')[0]['caption']
    assert '交互界面' in store.query('SELECT text FROM chunks')[0]['text']
    embed_count = len(model.embed_calls)
    await enhance(store, model, course, OfflineRenderer())
    assert model.calls == 1 and len(model.embed_calls) == embed_count
    old_chunks = store.query('SELECT text FROM chunks')
    store.execute('DELETE FROM caption_cache')
    model.fail_caption = True
    assert (await enhance(store, model, course, OfflineRenderer()))[0]['status'] == 'blocked'
    assert store.query('SELECT text FROM chunks') == old_chunks


async def test_image_adapter_same_model_and_safe_limits(settings, store):
    seen = []
    def response(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"description":"visible", "uncertain":false}'}}]})
    model = Models(settings,store,httpx.MockTransport(response))
    await model.chat('vision',{'page':1},purpose='vision',image=b'\x89PNG\r\n\x1a\nFAKE')
    assert seen[0]['model'] == settings.llm_model
    content = seen[0]['messages'][1]['content']
    assert content[1]['image_url']['url'].startswith('data:image/png;base64,')
    assert 'FAKE' not in str(store.query('SELECT * FROM calls'))
    with pytest.raises(UserError):
        await model.chat('vision',{},image=b'not-an-image')


def test_near_duplicate_body_priority_and_nonduplicate():
    assert near_duplicate('ABC，课程内容与助手。','abc课程内容与助手')
    pages = [
        {'file':'summery/chapter.pptx','page':9,'id':'a','page_text':'相同的课程正文内容与知识点','text':'摘要'},
        {'file':'chapter.pptx','page':3,'id':'b','page_text':'相同的课程正文内容与知识点','text':'正文'},
        {'file':'other.pptx','page':1,'id':'c','page_text':'另一完全不同的主题','text':'其他'}]
    selected = prefer_body(pages)
    assert [p['id'] for p in selected] == ['b','c']
    assert selected[0]['page'] == 3 and 'page_text' not in selected[0]
