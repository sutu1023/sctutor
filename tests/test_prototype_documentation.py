from pathlib import Path


def test_alignment_artifacts_and_live_controls_are_documented():
    root=Path(__file__).resolve().parents[1]
    mapping=(root/'docs/prototype-alignment.md').read_text()
    readme=(root/'README.md').read_text()
    guide='\n'.join(p.read_text() for p in (root/'docs/handover').glob('*.md'))
    for section in ['§4.1','§4.2','§4.3','§4.4','§4.5','图5-1','图5-2','图5-3','图5-4','图5-5','图5-6','图5-7']:
        assert section in mapping
    for name in ['test_l5.py','test_websearch.py','test_course_files.py','test_reporting.py','prototype_smoke.py']:
        assert name in mapping and (root/'tests'/name).is_file()
    for text in ['TAVILY_API_KEY','完整阶梯','当前快照','上传本身']:
        assert text in guide
    assert 'docs/handover/README.md' in readme
    assert len(readme.splitlines()) < 90
    html=(root/'src/sctutor/static/index.html').read_text()
    assert 'preview.js' in html and 'materials.css' in html and 'data-level="5"' in html
