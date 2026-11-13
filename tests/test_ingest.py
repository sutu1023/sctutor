import pytest
from conftest import FakeModels, make_pptx

from sctutor.config import UserError
from sctutor.pptx import chunks, parse_pptx
from sctutor.retrieval import Retriever, ingest, tokenize


def test_presentation_order_notes_xml(tmp_path):
    path = make_pptx(tmp_path/'test.pptx', ('A & "B"', 'second'), [2, 1], 'notes & text')
    slides = parse_pptx(path)
    assert [s.text for s in slides] == ['second', 'A & "B"']
    assert [s.page for s in slides] == [1, 2]
    assert slides[0].notes == 'notes & text'
    assert slides[1].title == 'A & "B"'


def test_chunks_and_tokens():
    text = 'a'*2401
    parts = chunks(text)
    assert [len(x) for x in parts] == [1200, 1200, 301]
    assert parts[0][-150:] == parts[1][:150]
    assert chunks('  ') == []
    assert tokenize('工作者 Worker API') == ['worker', 'api', '工作', '作者']


async def test_incremental_cache_and_revision(store, corpus):
    folder, model = corpus
    initial = len(model.embed_calls)
    before = store.query('SELECT id FROM pages')
    assert len(before) == 3
    assert store.query('SELECT indexed FROM pages WHERE page=3')[0]['indexed'] == 0
    assert (await ingest(store, model, folder))[0]['status'] == 'unchanged'
    assert len(model.embed_calls) == initial
    make_pptx(folder/'sample.pptx', ('GitHub Copilot 编程助手', 'new text'))
    result = await ingest(store, model, folder)
    assert result[0]['status'] == 'indexed'
    assert len(model.embed_calls) == initial + 1
    assert model.embed_calls[-1] == ['new text']
    assert not {r['id'] for r in before} & {r['id'] for r in store.query('SELECT id FROM pages')}


async def test_corrupt_failed_update_deletion_and_atomicity(store, corpus):
    folder, model = corpus
    original = store.query('SELECT * FROM pages')
    (folder/'bad.pptx').write_text('not a pptx')
    assert any(r['status'] == 'error' for r in await ingest(store, model, folder))
    make_pptx(folder/'sample.pptx', ('entirely new',))
    model.fail = 'embed'
    results = await ingest(store, model, folder)
    assert all(r['status'] == 'error' for r in results)
    assert store.query('SELECT * FROM pages') == original
    (folder/'sample.pptx').unlink()
    await ingest(store, model, folder)
    assert not store.query('SELECT * FROM pages')
    with pytest.raises(UserError):
        await ingest(store, model, folder/'missing')


async def test_retrieval_and_model_change(store, corpus):
    folder, model = corpus
    results = await Retriever(store, model).search('Copilot')
    assert results[0]['page'] == 1
    assert all(r['file'] == 'sample.pptx' for r in results)
    assert len({r['id'] for r in results}) == len(results)
    before = len(model.embed_calls)
    assert await Retriever(store, model).search(' ') == []
    assert len(model.embed_calls) == before
    model.embedding_key = 'another-model'
    with pytest.raises(UserError, match='变更'):
        await Retriever(store, model).search('Copilot')
    model.embedding_key = 'fake/embedding'
    async def wrong(texts, tid=None):
        return [[1, 2, 3]]
    model.embed = wrong
    with pytest.raises(UserError, match='维度'):
        await Retriever(store, model).search('Copilot')


async def test_both_retrieval_branches_and_five_page_limit(store, tmp_path, monkeypatch):
    folder = tmp_path/'course'
    folder.mkdir()
    make_pptx(folder/'many.pptx', ['lexical question'] + [f'noise {i}' for i in range(25)] + ['semantic target'])
    model = FakeModels()
    async def embedding(texts, tid=None):
        return [[1, 0] if ('semantic target' in t or 'Instruct:' in t)
                else [0, 1] if 'lexical question' in t else [0.01, 1] for t in texts]
    model.embed = embedding
    await ingest(store, model, folder)
    results = await Retriever(store, model).search('lexical question')
    assert len(results) == 5
    assert {1, 27} <= {r['page'] for r in results}
    # Repeated chunks of one page must not create multiple page results.
    make_pptx(folder/'long.pptx', ('lexical question '*300,))
    await ingest(store, model, folder)
    results = await Retriever(store, model).search('lexical question')
    assert len({r['id'] for r in results}) == len(results)
