from zipfile import ZipFile

import pymupdf
import pytest
from conftest import make_pptx

from sctutor.config import UserError
from sctutor.rendering import Renderer, check_safe, digest


def test_render_cache_source_untouched_and_asset_path(store, tmp_path):
    source = make_pptx(tmp_path / 'course.pptx')
    before = digest(source)
    renderer = Renderer(store.path.parent)
    calls = []
    def export(copy, pdf):
        calls.append(copy)
        assert copy != source and digest(copy) == before
        with pymupdf.open() as doc:
            for _ in range(2):
                doc.new_page()
            doc.save(pdf)
    result = renderer.render(source, export)
    assert result['pages'] == 2 and digest(source) == before
    assert (result['directory'] / 'page-2.png').is_file()
    assert renderer.render(source, export)['key'] == result['key']
    assert len(calls) == 1
    store.execute("INSERT INTO page_assets VALUES ('forged','/etc/passwd','','key')")
    with pytest.raises(UserError):
        renderer.asset(store, 'forged')


def test_bad_pdf_and_unsafe_external_link(tmp_path):
    source = make_pptx(tmp_path / 'course.pptx')
    def wrong_count(copy, pdf):
        with pymupdf.open() as doc:
            doc.new_page()
            doc.save(pdf)
    with pytest.raises(UserError, match='页数'):
        Renderer(tmp_path / 'data').render(source, wrong_count)
    with ZipFile(source, 'a') as z:
        z.writestr('ppt/slides/_rels/slide1.xml.rels', '<Relationships><Relationship Id="x" Type="test/oleObject" TargetMode="External" Target="https://evil.example"/></Relationships>')
    with pytest.raises(UserError, match='外部'):
        check_safe(source)


def test_powerpoint_failure_is_safe(tmp_path):
    source = make_pptx(tmp_path / 'course.pptx')
    def fail(copy, pdf):
        raise RuntimeError('SECRET_PROVIDER_BODY')
    with pytest.raises(UserError) as error:
        Renderer(tmp_path / 'data').render(source, fail)
    assert 'SECRET' not in str(error.value)
