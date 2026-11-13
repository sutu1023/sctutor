"""Teacher uploads and source-bound learning materials; nothing is mounted publicly."""
import json
import os
import re
import stat
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

import pymupdf
from defusedxml import ElementTree as ET

from .config import UserError
from .policy import Policy
from .pptx import parse_pptx
from .rendering import digest

MIB = 1024 * 1024


def validate_pptx(path):
    try:
        with ZipFile(path) as z:
            entries = z.infolist()
            names = [x.filename for x in entries]
            if len(entries) > 10000 or sum(x.file_size for x in entries) > 200*MIB:
                raise UserError('PPTX解压规模超限。')
            if len(names) != len(set(names)):
                raise UserError('PPTX含重复压缩项。')
            for x in entries:
                p = PurePosixPath(x.filename)
                if p.is_absolute() or '..' in p.parts or '\\' in x.filename or ':' in x.filename or stat.S_ISLNK(x.external_attr >> 16):
                    raise UserError('PPTX包含不安全路径。')
                if x.flag_bits & 1 or (x.file_size > 10*MIB and x.file_size > max(1, x.compress_size)*1000):
                    raise UserError('PPTX加密或压缩比例异常。')
                if 'vbaproject' in x.filename.casefold():
                    raise UserError('不允许含宏的课件。')
            types = z.read('[Content_Types].xml')
            if b'application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml' not in types or b'macroEnabled' in types:
                raise UserError('不是标准无宏PPTX。')
            for name in names:
                if name.endswith('.rels'):
                    for rel in ET.fromstring(z.read(name)):
                        if rel.attrib.get('TargetMode', '').lower() == 'external':
                            raise UserError('上传课件含外部链接，拒绝导入。')
        if not parse_pptx(path):
            raise UserError('PPTX没有可解析页面。')
    except UserError:
        raise
    except Exception:
        raise UserError('课件损坏或不是可解析PPTX。') from None


async def upload_files(files, root):
    if not 1 <= len(files) <= 10:
        raise UserError('每次请选择1至10个PPTX文件。')
    root = Path(root).resolve()
    managed = root / 'uploads'
    if managed.is_symlink():
        raise UserError('上传目录不能为符号链接。')
    managed.mkdir(parents=True, exist_ok=True)
    if managed.resolve().parent != root:
        raise UserError('上传目录不可用。')
    results, total = [], 0
    for file in files:
        name = unicodedata.normalize('NFC', file.filename or '')
        result = {'file': name[:180], 'status': 'error'}
        try:
            if not re.fullmatch(r'[^/\\\x00-\x1f:]{1,175}\.pptx', name, re.I) or name.startswith('.'):
                raise UserError('仅允许安全文件名的.pptx课件。')
            with tempfile.NamedTemporaryFile(dir=managed, prefix='.upload-', delete=True) as temp:
                size = 0
                while chunk := await file.read(1024*1024):
                    size += len(chunk)
                    total += len(chunk)
                    if size > 50*MIB or total > 100*MIB:
                        raise UserError('上传超出单文件50MiB或总100MiB限制。')
                    temp.write(chunk)
                temp.flush()
                validate_pptx(temp.name)
                dest = managed/name
                if dest.is_symlink():
                    raise UserError('目标文件不安全。')
                if dest.exists():
                    if dest.is_file() and digest(dest) == digest(temp.name):
                        result.update(status='skipped', message='相同文件已存在，未重复写入。')
                    else:
                        raise UserError('同名文件内容不同，请改名后上传，不会覆盖旧文件。')
                else:
                    os.link(temp.name, dest)  # Exclusive atomic publication, never replace.
                    result.update(status='uploaded', message='上传成功，待教师显式同步；尚未调用云端。')
        except UserError as e:
            result['message'] = str(e)
        except Exception:
            result['message'] = '上传失败，未覆盖既有课件。'
        finally:
            await file.close()
        results.append(result)
    return results


class Materials:
    def __init__(self, store, renderer):
        self.store, self.renderer = store, renderer

    def catalog(self):
        self.store.require_study()
        return self.store.query('SELECT d.id,d.path file,COUNT(p.id) pages FROM documents d JOIN pages p ON p.doc_id=d.id GROUP BY d.id ORDER BY d.path')

    def anchor(self, pid, origin):
        self.store.require_study()
        if origin.startswith('turn:'):
            self.store.citation(origin[5:], pid)
        elif origin.startswith('concept:'):
            from .knowledge import Knowledge
            cid = origin[8:]
            if not any(x['id'] == cid and pid in x['pages'] for x in Knowledge(self.store).concepts(approved=True)):
                raise UserError('该页不属于审核通过的知识点。')
            Policy(self.store).require_pages([pid], concept_ids=[cid])
        else:
            raise UserError('缺少授权资料上下文，请先打开引用或审核知识点依据。')
        rows = self.store.query('SELECT p.*,d.path file,d.root,d.hash source_hash FROM pages p JOIN documents d ON p.doc_id=d.id WHERE p.id=?', (pid,))
        if not rows:
            raise UserError('课件版本已失效。')
        source = (Path(rows[0]['root'])/rows[0]['file']).resolve()
        if not source.is_relative_to(Path(rows[0]['root']).resolve()) or not source.is_file() or digest(source) != rows[0]['source_hash']:
            raise UserError('源课件已变化或不可用，请教师同步后重新提问。')
        return rows[0]

    def page(self, anchor, origin, number=None, snippet=''):
        a = self.anchor(anchor, origin)
        number = a['page'] if number is None else number
        rows = self.store.query('SELECT * FROM pages WHERE doc_id=? AND page=?', (a['doc_id'], number))
        if not rows:
            raise UserError('页码超出当前文档范围。')
        p = rows[0]
        state = Policy(self.store).assess(page_ids=[p['id']])
        # An explicitly quoted homework page stays readable, but does not grant neighbours.
        if state['blocked'] or state['restricted'] and p['id'] != anchor:
            raise UserError('该页受到作业或禁答策略限制。')
        assets = self.store.query('SELECT caption FROM page_assets WHERE page_id=?', (p['id'],))
        result = {k: p[k] for k in ('id', 'page', 'title', 'text', 'notes')}
        result.update(file=a['file'], caption=assets[0]['caption'] if assets else '',
                      pages=self.store.query('SELECT count(*) n FROM pages WHERE doc_id=?', (a['doc_id'],))[0]['n'],
                      anchor=anchor, origin=origin, rectangles=[], location='请选择真实原文片段进行定位。')
        snippet = snippet.strip()[:300]
        if snippet:
            if snippet not in p['text'] and snippet not in p['notes']:
                raise UserError('定位片段不在该页真实文字中。')
            result['snippet'] = snippet
            result['location'] = '已核对文字片段；原图无法定位。'
            try:
                image = self.renderer.asset(self.store, p['id'])
                with pymupdf.open(image.parent/'slides.pdf') as pdf:
                    page = pdf[p['page']-1]
                    result['rectangles'] = [[r.x0/page.rect.width, r.y0/page.rect.height, r.x1/page.rect.width, r.y1/page.rect.height] for r in page.search_for(snippet)[:20]]
                    if result['rectangles']:
                        result['location'] = '已根据PDF真实文字坐标定位。'
            except Exception:
                pass
        return result

    def image(self, anchor, origin, number):
        p = self.page(anchor, origin, number)
        return self.renderer.asset(self.store, p['id'])

    def pdf(self, anchor, origin):
        a = self.anchor(anchor, origin)
        ids = [x['id'] for x in self.store.query('SELECT id FROM pages WHERE doc_id=?', (a['doc_id'],))]
        for pid in ids:
            state = Policy(self.store).assess(page_ids=[pid])
            if state['blocked'] or state['restricted']:
                raise UserError('文档含禁答或作业限制页面，不能打开整份文件。')
        image = self.renderer.asset(self.store, anchor)
        path = (image.parent/'slides.pdf').resolve()
        try:
            manifest = json.loads((image.parent/'manifest.json').read_text())
            if manifest['source_hash'] != a['source_hash'] or manifest['pages'] != len(ids):
                raise ValueError()
            if not path.is_relative_to(self.renderer.root) or not path.is_file():
                raise ValueError()
        except Exception:
            raise UserError('完整PDF尚未生成或版本已失效，请教师处理原页。') from None
        return path
