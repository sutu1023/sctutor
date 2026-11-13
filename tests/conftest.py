from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

from sctutor.config import Settings
from sctutor.store import Store


def make_pptx(path, texts=("alpha", "beta"), order=None, notes=None):
    order = order or list(range(1, len(texts)+1))
    with ZipFile(path, 'w') as z:
        z.writestr('ppt/presentation.xml',
                   '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   '<p:sldIdLst>' + ''.join(f'<p:sldId id="{i+256}" r:id="rId{i}"/>' for i in order)
                   + '</p:sldIdLst></p:presentation>')
        z.writestr('ppt/_rels/presentation.xml.rels', '<Relationships>' + ''.join(
            f'<Relationship Id="rId{i}" Type="slide" Target="slides/slide{i}.xml"/>'
            for i in range(1, len(texts)+1)) + '</Relationships>')
        for i, text in enumerate(texts, 1):
            xml = ('<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                   'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                   '<p:sp><p:ph type="title"/><a:p><a:r><a:t>' + escape(text)
                   + '</a:t></a:r></a:p></p:sp></p:sld>')
            z.writestr(f'ppt/slides/slide{i}.xml', xml)
            if notes:
                z.writestr(f'ppt/slides/_rels/slide{i}.xml.rels',
                           f'<Relationships><Relationship Id="n" Type="test/notesSlide" '
                           f'Target="../notesSlides/notesSlide{i}.xml"/></Relationships>')
                z.writestr(f'ppt/notesSlides/notesSlide{i}.xml',
                           xml.replace(escape(text), escape(notes)))
    return path


class FakeModels:
    embedding_key = 'fake/embedding'

    def __init__(self, rejects=0, no_evidence=False, invalid_ids=False, fail=None):
        self.rejects, self.no_evidence, self.invalid_ids, self.fail = rejects, no_evidence, invalid_ids, fail
        self.embed_calls, self.chats, self.checks = [], [], 0

    async def embed(self, texts, tid=None):
        self.embed_calls.append(texts)
        if self.fail == 'embed':
            raise TimeoutError('private payload should not escape')
        return [[1.0, 0.25] for _ in texts]

    async def chat(self, system, payload, tid=None, purpose='generate'):
        self.chats.append((purpose, payload, system))
        if self.fail == purpose:
            raise TimeoutError('private payload should not escape')
        if purpose == 'relevance':
            return {'relevant_ids': [] if self.no_evidence else [payload['sources'][0]['id']]}
        if purpose == 'generate':
            return {'text': '你能先说说自己的思路吗？' if self.rejects == 0 else 'SECRET_UNCHECKED_ANSWER',
                    'citation_ids': ['forged'] if self.invalid_ids else [payload['sources'][0]['id']]}
        if purpose == 'check':
            self.checks += 1
            ok = self.checks > self.rejects
            return {'passed': ok, 'level_ok': ok, 'supported': ok, 'reason': 'ok' if ok else '超出级别'}
        return {'ok': True}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'data')


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path/'data', llm_api_key='test-secret', embedding_api_key='test-secret',
                    llm_base_url='https://opencode.ai/inference/go/openai/v1',
                    llm_model='deepseek-v4.1-flash', llm_profile='deepseek-json',
                    embedding_base_url='https://api.siliconflow.cn/v1',
                    embedding_model='Qwen/Qwen3-Embedding-8B')


@pytest.fixture
async def corpus(tmp_path, store):
    from sctutor.retrieval import ingest
    folder = tmp_path/'course'
    folder.mkdir()
    make_pptx(folder/'sample.pptx', ('GitHub Copilot 编程助手', 'Python 编程语言', ''))
    model = FakeModels()
    await ingest(store, model, folder)
    return folder, model
