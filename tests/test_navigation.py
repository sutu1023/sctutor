from html.parser import HTMLParser
from pathlib import Path


class Navigation(HTMLParser):
    def __init__(self):
        super().__init__()
        self.names={}
        self.entry=None
        self.hidden=set()

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='button' and 'data-view' in attrs:
            self.entry=attrs['data-view']
            self.names[self.entry]=''
            if 'hidden' in attrs:
                self.hidden.add(self.entry)

    def handle_data(self,data):
        if self.entry:
            self.names[self.entry]+=data

    def handle_endtag(self,tag):
        if tag=='button':
            self.entry=None


def test_navigation_names_match_prototype_by_role():
    root=Path(__file__).resolve().parents[1]
    html=(root/'src/sctutor/static/index.html').read_text()
    nav=Navigation()
    nav.feed(html)
    assert nav.names=={'dashboard':'班级概览','policy':'辅导策略','questions':'题目审核','admin':'课件管理',
                      'tutor':'答疑','review':'复习','quiz':'小测','report':'学习报告'}
    assert nav.hidden=={'dashboard','policy','questions','admin'}
    for old in ['引导答疑','学习报告 / 知识点','小测 / 间隔复测','教学概览 / 辅导策略','课件管理 / 审核']:
        assert old not in html
    assert '课件原页' in html
    for name in nav.names.values():
        assert name in (root/'docs/prototype-alignment.md').read_text()
