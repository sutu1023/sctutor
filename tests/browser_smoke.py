"""Real Chrome smoke via CDP, synthetic PPTX and fake models; no cloud API calls."""
import asyncio
import base64
import json
import socket
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.request import urlopen

import pymupdf
import uvicorn
from browser_path import chrome_path
from conftest import FakeModels, make_pptx
from websockets.sync.client import connect

from sctutor.app import create_app
from sctutor.config import Settings
from sctutor.knowledge import Knowledge, Review
from sctutor.retrieval import ingest
from sctutor.store import Store


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Browser:
    def __init__(self, ws):
        self.ws, self.seq, self.errors = ws, 0, []

    def command(self, method, params=None):
        self.seq += 1
        self.ws.send(json.dumps({'id':self.seq, 'method':method, 'params':params or {}}))
        while True:
            response = json.loads(self.ws.recv(timeout=15))
            if response.get('method') == 'Runtime.exceptionThrown':
                self.errors.append(response['params'])
            if response.get('id') == self.seq:
                if 'error' in response:
                    raise RuntimeError(response['error'])
                return response.get('result', {})

    def js(self, expression):
        response = self.command('Runtime.evaluate', {'expression':expression,
                                'returnByValue':True, 'awaitPromise':True})
        if 'exceptionDetails' in response:
            raise RuntimeError(response['exceptionDetails'])
        return response.get('result', {}).get('value')

    def wait(self, expression):
        end = time.monotonic()+15
        while time.monotonic()<end:
            if self.js(f'Boolean({expression})'):
                return
            time.sleep(.05)
        raise AssertionError('Browser condition timed out: '+expression+'\n'+str(self.js("document.body.innerText"))+'\n'+str(self.errors))


class LearningModels(FakeModels):
    async def chat(self, system, payload, tid=None, purpose='generate'):
        if purpose == 'classify':
            return {'type':'concept', 'concept_ids':[x['id'] for x in payload['concepts']][:12]}
        if purpose == 'knowledge':
            return {'concepts':[{'name':'Copilot', 'page_ids':[payload['sources'][0]['id']], 'aliases':['微软补全插件']}], 'relations':[]}
        if purpose == 'questions':
            return {'type':payload['type'], 'prompt':'课件中的编程助手是哪一个？', 'options':['Copilot','其他'], 'answer':'Copilot', 'rubric':'识别课件工具', 'hint':'想想编程辅助工具的概念', 'concept_ids':[payload['concept']['id']], 'page_ids':[payload['sources'][0]['id']]}
        if purpose == 'auto-review':
            return {'passed':True,'supported':True,'answer_ok':True,'rubric_ok':True,'restricted':False,'uncertain':False,'reason':'模拟来源复核通过'}
        if purpose == 'quiz-hint':
            return {'text':'思考编程辅助工具的概念。'}
        if purpose == 'quiz-hint-check':
            return {'passed':True,'level_ok':True,'supported':True,'reason':'L2'}
        return await super().chat(system,payload,tid,purpose)


def main():
    chrome = chrome_path()
    if not chrome.exists():
        raise SystemExit('blocked: 未找到本机 Chrome，不自动下载浏览器。')
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        course = root/'course'
        course.mkdir()
        make_pptx(course/'example.pptx', ('GitHub Copilot 是编程助手',))
        settings = Settings(course_dir=course, data_dir=root/'data', llm_api_key='fake', embedding_api_key='fake',
                            llm_base_url='https://chat.example/v1', llm_model='fake-chat',
                            embedding_base_url='https://vector.example/v1', embedding_model='fake-vector')
        store = Store(settings.data_dir)
        models = LearningModels()
        asyncio.run(ingest(store, models, course))
        port, debug_port = free_port(), free_port()
        app = create_app(settings, store, models)
        renderer = app.state.pipeline.renderer
        def synthetic_export(copy, pdf):
            with pymupdf.open() as doc:
                doc.new_page()
                doc.save(pdf)
        class OfflineRenderer:
            def render(self, path):
                return renderer.render(path, synthetic_export)
        app.state.pipeline.renderer = OfflineRenderer()
        server = uvicorn.Server(uvicorn.Config(app,
                                host='127.0.0.1', port=port, log_level='error'))
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        browser = None
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            if not server.started:
                raise RuntimeError('test server did not start')
            browser = subprocess.Popen([str(chrome), '--headless=new', '--disable-gpu',
                         '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
                         '--disable-extensions', f'--remote-debugging-port={debug_port}',
                         f'--user-data-dir={root/"chrome"}', 'about:blank'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            target = None
            for _ in range(200):
                try:
                    with urlopen(f'http://127.0.0.1:{debug_port}/json', timeout=1) as r:
                        target = next(t for t in json.load(r) if t['type']=='page')
                    break
                except (OSError, StopIteration):
                    time.sleep(.05)
            if target is None:
                raise RuntimeError('Chrome debugging endpoint not available')
            with connect(target['webSocketDebuggerUrl']) as ws:
                b = Browser(ws)
                b.command('Runtime.enable')
                b.command('Page.enable')
                b.command('Emulation.setDeviceMetricsOverride',
                          {'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
                b.command('Page.navigate', {'url':f'http://127.0.0.1:{port}'})
                b.wait("typeof loginAs==='function' && document.querySelector('#login-teacher') !== null")
                b.js("document.querySelector('#login-teacher').click()")
                b.wait("identity?.id==='teacher' && !busy && !document.querySelector('#send').disabled")
                assert b.js("session===null && document.querySelectorAll('#sessions .session-open').length===0")
                b.js("document.querySelector('#question').value='Copilot 是什么？';document.querySelector('#send').click()")
                b.wait("document.querySelectorAll('.turn').length === 1 && !document.querySelector('#upgrade').disabled")
                assert b.js("document.querySelector('.turn-meta').textContent.includes('L1')")
                assert b.js("document.querySelector('.refs button').disabled")
                for count in (2, 3):
                    b.js("document.querySelector('#upgrade').click()")
                    b.wait(f"document.querySelectorAll('.turn').length === {count} && !document.querySelector('#send').disabled")
                assert b.js("[...document.querySelectorAll('.turn-meta')].at(-1).textContent.includes('L3')")
                b.js("[...document.querySelectorAll('.refs button')].at(-1).click()")
                b.wait("document.querySelector('#preview-text').textContent.includes('GitHub Copilot')")
                b.command('Page.reload')
                b.wait("document.querySelectorAll('.turn').length === 3")
                b.js("document.querySelector('#new-session').click()")
                b.wait("document.querySelectorAll('.turn').length === 0 && document.querySelectorAll('#sessions .session-open').length === 1")
                b.js("document.querySelector('[data-view=admin]').click()")
                b.wait("document.querySelector('#workspace h1')?.textContent.includes('课件管理') && document.querySelector('select[multiple]')")
                b.js("document.querySelector('select[multiple]').options[0].selected=true;[...document.querySelectorAll('#workspace button')].find(x=>x.textContent==='抽取为待审候选').click()")
                b.wait("[...document.querySelectorAll('#workspace .card h3')].some(x=>x.textContent==='Copilot')")
                b.js("[...document.querySelectorAll('#workspace .card')].find(x=>x.querySelector('h3')?.textContent==='Copilot').querySelector('button').click()")
                b.wait("[...document.querySelectorAll('#workspace select option')].some(x=>x.textContent==='Copilot')")
                b.js("[...document.querySelectorAll('#workspace button')].find(x=>x.textContent==='依据课件生成待审题').click()")
                b.wait("[...document.querySelectorAll('#workspace button')].some(x=>x.textContent==='确认')")
                b.js("[...document.querySelectorAll('#workspace button')].find(x=>x.textContent==='确认').click()")
                b.wait("document.querySelector('#review-list').textContent.includes('没有待审核')")
                assert b.js("document.querySelector('#review-status').value==='pending'")
                assert b.js("!document.querySelector('#auto-generate').checked && !document.querySelector('#auto-review').checked")
                b.js("document.querySelector('#auto-generate').checked=true;document.querySelector('#auto-review').checked=true;[...document.querySelectorAll('#workspace button')].find(x=>x.textContent==='一键处理课件').click()")
                b.wait("document.querySelector('#pipeline-progress').textContent.includes('completed')")
                assert b.js("document.querySelector('#processing-summary').textContent.includes('真实页数')")
                # Synthetic graph fixture exercises edges and hostile names without cloud calls.
                knowledge = Knowledge(store)
                page = store.query('SELECT id FROM pages')[0]['id']
                knowledge.add_extraction({'concepts':[{'name':'Copilot','page_ids':[page]},
                    {'name':'<img src=x onerror=window.XSS=1>','page_ids':[page]}],
                    'relations':[{'prerequisite':'<img src=x onerror=window.XSS=1>',
                                  'target':'Copilot','page_ids':[page]}]}, [page])
                for item in knowledge.concepts():
                    knowledge.review('concepts', item['id'], Review(status='approved'))
                for item in knowledge.relations():
                    knowledge.review('relations', item['id'], Review(status='approved'))
                # Volume fixtures: never approve production content, only this temporary DB.
                for start in (0,10):
                    knowledge.add_extraction({'concepts':[{'name':f'ZZ-知识点{i:02d}', 'page_ids':[page], 'aliases':[f'别名{i:02d}']} for i in range(start,start+10)]}, [page])
                for item in knowledge.concepts():
                    if item['name'].startswith('ZZ-知识点') and int(item['name'][-2:])<10:
                        knowledge.review('concepts', item['id'], Review(status='approved'))
                copilot=next(x for x in knowledge.concepts() if x['name']=='Copilot')
                for i in range(20):
                    app.state.quiz.add_question({'type':'choice','prompt':f'待审题目{i:02d}', 'options':['Copilot','其他'],'answer':'Copilot','rubric':'识别工具','hint':'思考工具概念','concept_ids':[copilot['id']], 'page_ids':[page]})
                b.js("showView('admin')")
                b.wait("document.querySelector('#review-list .review-row')")
                assert b.js("document.querySelectorAll('#review-list .review-row').length===6 && !document.querySelector('#admin-tools').open")
                assert b.js("document.querySelector('#review-next').getBoundingClientRect().bottom<=window.innerHeight")
                Path('data').mkdir(exist_ok=True)
                Path('data/browser-admin-queue.png').write_bytes(base64.b64decode(b.command('Page.captureScreenshot', {'format':'png'})['data']))
                b.js("window.uxHeading=document.querySelector('#workspace h1');window.uxSearch=document.querySelector('#review-search');window.uxOther=document.querySelectorAll('#review-list .review-row')[1];uxOther.querySelector('.review-detail').open=true;uxOther.querySelector('input').value='未提交的草稿';window.uxFirst=document.querySelector('#review-list .review-row').dataset.itemId;document.querySelector('#review-list .review-row .primary').click()")
                b.wait("!document.querySelector('#review-list [data-item-id=\"'+window.uxFirst+'\"]')")
                assert b.js("window.uxHeading===document.querySelector('#workspace h1') && window.uxSearch===document.querySelector('#review-search') && uxOther.isConnected && uxOther.querySelector('input').value==='未提交的草稿' && uxOther.querySelector('.review-detail').open")
                assert b.js("document.querySelector('#review-notice').textContent.includes('移出待审队列')")
                b.js("document.querySelector('[data-review-kind=aliases]').click();document.querySelector('#review-search').value='别名19';document.querySelector('#review-search').dispatchEvent(new Event('input'));document.querySelector('#review-list .primary').click()")
                b.wait("document.querySelector('#review-list .notice').textContent.includes('请先审核')")
                assert b.js("document.querySelectorAll('#review-list .review-row').length===1 && window.uxHeading===document.querySelector('#workspace h1')")
                b.js("document.querySelector('[data-review-kind=questions]').click();document.querySelector('#review-search').value='';document.querySelector('#review-search').dispatchEvent(new Event('input'))")
                assert b.js("document.querySelectorAll('#review-list .review-row').length===6 && !document.querySelector('#review-next').disabled")
                b.js("document.querySelector('#review-next').click();window.uxQuestion=document.querySelector('#review-list .review-row').dataset.itemId;document.querySelector('#review-list .primary').click()")
                b.wait("!document.querySelector('#review-list [data-item-id=\"'+window.uxQuestion+'\"]')")
                assert b.js("document.querySelector('.list-pager').textContent.includes('第 2 /') && window.uxHeading===document.querySelector('#workspace h1')")
                b.js("document.querySelector('#review-status').value='approved';document.querySelector('#review-status').dispatchEvent(new Event('change'));document.querySelector('#review-search').value='待审题目';document.querySelector('#review-search').dispatchEvent(new Event('input'))")
                assert b.js("document.querySelectorAll('#review-list .review-row').length===1 && document.querySelector('#review-list').textContent.includes('已批准')")
                b.js("document.querySelector('#review-status').value='pending';document.querySelector('#review-status').dispatchEvent(new Event('change'));document.querySelector('#review-search').value='';document.querySelector('#review-search').dispatchEvent(new Event('input'));while(!document.querySelector('#review-next').disabled)document.querySelector('#review-next').click();window.uxLast=document.querySelector('#review-list .review-row').dataset.itemId;document.querySelector('#review-list .primary').click()")
                b.wait("!document.querySelector('#review-list [data-item-id=\"'+window.uxLast+'\"]')")
                assert b.js("document.querySelector('.list-pager').textContent.includes('第 3 / 3') && document.querySelectorAll('#review-list .review-row').length===6")
                b.command('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                assert b.js("document.documentElement.scrollWidth<=window.innerWidth+1")
                b.command('Emulation.setDeviceMetricsOverride', {'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
                b.js("document.querySelector('[data-view=dashboard]').click()")
                b.wait("document.querySelector('#policy-max-level') && document.querySelector('#teaching-check-log')")
                assert b.js("document.querySelector('#workspace').textContent.includes('非真实班级')")
                assert b.js("document.querySelector('#workspace .metric strong').textContent==='0'")
                b.js("document.querySelector('#policy-max-level').value='1';[...document.querySelectorAll('#workspace button')].find(x=>x.textContent==='保存策略').click()")
                b.wait("document.querySelector('#policy-result').textContent.includes('版本 1')")
                b.js("document.querySelector('[data-view=tutor]').click()")
                b.wait("!document.querySelector('#tutor-view').hidden")
                b.js("document.querySelector('#question').value='Copilot';document.querySelector('#send').click()")
                b.wait("document.querySelectorAll('.turn').length===1 && !document.querySelector('#send').disabled")
                assert b.js("document.querySelector('#upgrade').disabled && !document.querySelector('#think').disabled")
                assert b.js("document.querySelector('#turn-context').textContent.includes('Copilot')")
                b.js("document.querySelector('#think').click()")
                assert b.js("document.querySelector('#action').value==='followup' && document.activeElement.id==='question'")
                b.js("document.querySelector('[data-view=dashboard]').click()")
                b.wait("document.querySelector('#policy-max-level')")
                b.js("document.querySelector('#policy-max-level').value='4';[...document.querySelectorAll('#workspace button')].find(x=>x.textContent==='保存策略').click()")
                b.wait("document.querySelector('#policy-result').textContent.includes('版本 2')")
                b.js("document.querySelector('#logout').click()")
                b.wait("document.body.classList.contains('logged-out')")
                b.js("document.querySelector('#login-student').click()")
                b.wait("document.querySelector('#identity-name').textContent==='学生演示' && !busy && !document.querySelector('#send').disabled")
                assert b.js("document.querySelector('[data-view=admin]').hidden && document.querySelector('[data-view=dashboard]').hidden && document.querySelectorAll('.turn').length===0")
                assert b.js("(async()=> (await fetch('/api/admin/pages')).status===403)()")
                b.js("document.querySelector('[data-view=quiz]').click()")
                b.wait("[...document.querySelectorAll('#workspace button')].some(x=>x.textContent.includes('按薄弱知识点开始'))")
                b.js("[...document.querySelectorAll('#workspace button')].find(x=>x.textContent.includes('按薄弱知识点开始')).click()")
                b.wait("document.querySelector('#workspace h1')?.textContent.includes('进行中')")
                assert b.js("!document.querySelector('#workspace').textContent.includes('参考解答')")
                b.js("document.querySelector('#workspace select').value='其他';[...document.querySelectorAll('#workspace button')].find(x=>x.textContent==='提交答案').click()")
                b.wait("[...document.querySelectorAll('#workspace button')].some(x=>x.textContent==='再试一次')")
                assert b.js("!document.querySelector('#workspace').textContent.includes('参考解答')")
                b.command('Page.reload')
                b.wait("[...document.querySelectorAll('#workspace button')].some(x=>x.textContent==='再试一次')")
                b.js("document.querySelector('#workspace select').value='Copilot';[...document.querySelectorAll('#workspace button')].find(x=>x.textContent==='再试一次').click()")
                b.wait("document.querySelector('#workspace').textContent.includes('参考解答')")
                while b.js("[...document.querySelectorAll('#workspace button')].some(x=>x.textContent==='提交答案')"):
                    remaining = b.js("[...document.querySelectorAll('#workspace button')].filter(x=>x.textContent==='提交答案').length")
                    b.js("document.querySelector('#workspace select').value='Copilot';[...document.querySelectorAll('#workspace button')].find(x=>x.textContent==='提交答案').click()")
                    b.wait(f"[...document.querySelectorAll('#workspace button')].filter(x=>x.textContent==='提交答案').length < {remaining}")
                b.wait("[...document.querySelectorAll('#workspace button')].some(x=>x.textContent==='交卷并更新掌握状态')")
                b.js("[...document.querySelectorAll('#workspace button')].find(x=>x.textContent==='交卷并更新掌握状态').click()")
                b.wait("document.querySelector('#workspace h1')?.textContent.includes('已交卷')")
                b.js("[...document.querySelectorAll('#workspace button')].find(x=>x.textContent==='查看学习报告').click()")
                b.wait("document.querySelector('.focus-graph') && document.querySelector('#report-filter')")
                assert b.js("document.querySelectorAll('.graph-node').length===2 && document.querySelectorAll('.graph-link').length===1")
                assert b.js("!document.querySelector('#workspace img[src=x]') && !window.XSS")
                Path('data/browser-report-overview.png').write_bytes(base64.b64decode(b.command('Page.captureScreenshot', {'format':'png'})['data']))
                assert b.js("document.querySelector('.metric-grid').textContent.includes('首次作答正确率')")
                b.js("document.querySelector('.graph-node').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))")
                assert b.js("document.activeElement.id.startsWith('concept-') && document.querySelector('#concept-dialog').open")
                b.js("document.querySelector('#concept-dialog .dialog-close').click();document.querySelector('#report-review-tab').click()")
                assert b.js("document.querySelectorAll('#review-plan-list .concept-row').length===5")
                Path('data/browser-report-plan.png').write_bytes(base64.b64decode(b.command('Page.captureScreenshot', {'format':'png'})['data']))
                assert b.js("document.querySelector('#report-content .list-pager').getBoundingClientRect().bottom<=window.innerHeight")
                assert b.js("document.querySelector('#report-content .list-pager').textContent.includes('第 1 /')")
                b.js("[...document.querySelectorAll('#report-content .list-pager button')].at(-1).click()")
                assert b.js("document.querySelector('#report-content .list-pager').textContent.includes('第 2 /')")
                b.js("document.querySelector('#report-search').value='Copilot';document.querySelector('#report-search').dispatchEvent(new Event('input'))")
                assert b.js("document.querySelectorAll('#review-plan-list .concept-row').length===1")
                b.js("document.querySelector('#report-search').value='不存在的知识';document.querySelector('#report-search').dispatchEvent(new Event('input'))")
                assert b.js("document.querySelector('#report-content').textContent.includes('没有匹配')")
                b.js("document.querySelector('#report-search').value='';document.querySelector('#report-search').dispatchEvent(new Event('input'));document.querySelector('#report-filter').value='mastered';document.querySelector('#report-filter').dispatchEvent(new Event('change'));document.querySelector('#report-overview-tab').click()")
                assert b.js("document.querySelectorAll('#concept-list .concept-row').length===1 && document.querySelector('#concept-list').textContent.includes('Copilot')")
                b.js("[...document.querySelectorAll('.focus-graph button')].find(x=>x.textContent==='查看知识点详情 / 复习依据').click();[...document.querySelectorAll('#concept-dialog button')].find(x=>x.textContent==='复习依据 1').click()")
                b.wait("!document.querySelector('#concept-dialog').open && document.querySelector('#preview-text').textContent.includes('GitHub Copilot')")
                b.js("[...document.querySelectorAll('.focus-graph button')].find(x=>x.textContent==='查看知识点详情 / 复习依据').click();document.querySelector('#concept-dialog .primary').click()")
                b.wait("document.querySelector('#workspace h1')?.textContent.includes('进行中')")
                assert b.js("!document.querySelector('dialog[open]') && !document.querySelector('#preview-text').textContent.includes('GitHub Copilot')")
                b.js("[...document.querySelectorAll('#workspace button')].find(x=>x.textContent==='取消小测（不更新掌握状态）').click()")
                b.wait("document.querySelector('.confirm-dialog[open]')")
                b.js("[...document.querySelectorAll('.confirm-dialog button')].find(x=>x.textContent==='确认取消小测').click()")
                b.wait("document.querySelector('#workspace h1')?.textContent==='小测'")
                b.js("document.querySelector('[data-view=quiz]').click()")
                b.wait("document.querySelector('#workspace').textContent.includes('第1天') && document.querySelector('#workspace').textContent.includes('第7天')")
                b.js("document.querySelector('[data-view=tutor]').click()")
                b.wait("!document.querySelector('#tutor-view').hidden && !quizActive")
                b.js("document.querySelector('#question').value='创建真实学生问题Copilot';document.querySelector('#send').click()")
                b.wait("!busy && document.querySelector('#sessions .session-rename')")
                b.js("document.querySelector('#sessions .session-rename').click();document.querySelector('#session-name').value='我的复习会话';document.querySelector('#session-confirm').click()")
                b.wait("document.querySelector('#sessions .session-open').textContent==='我的复习会话'")
                b.js("document.querySelector('#session-search').value='不存在的名字';document.querySelector('#session-search').dispatchEvent(new Event('input'))")
                b.wait("document.querySelectorAll('#sessions .session-open').length===0")
                b.js("document.querySelector('#session-search').value='';document.querySelector('#session-search').dispatchEvent(new Event('input'))")
                b.wait("document.querySelector('#sessions .session-delete')")
                b.js("document.querySelector('#sessions .session-delete').click();document.querySelector('#session-cancel').click()")
                assert b.js("document.querySelectorAll('#sessions .session-open').length===1")
                b.js("document.querySelector('#new-session').click()")
                b.wait("turns.length===0 && !busy && document.querySelectorAll('#sessions .session-open').length===1")
                b.js("document.querySelector('#question').value='另一个实际Copilot问题';document.querySelector('#send').click()")
                b.wait("!busy && document.querySelectorAll('#sessions .session-open').length===2")
                b.js("document.querySelectorAll('#sessions input').forEach(x=>x.click());document.querySelector('#delete-selected').click();document.querySelector('#session-confirm').click()")
                b.wait("document.querySelectorAll('#sessions .session-open').length===0 && !document.querySelector('#session-dialog').open")
                assert b.js("document.querySelectorAll('.turn').length===0 && document.querySelector('#preview-image').hidden")
                b.js("document.querySelector('#question').value='删除后继续问Copilot';document.querySelector('#send').click()")
                b.wait("document.querySelectorAll('.turn').length===1 && !document.querySelector('#send').disabled")
                b.js("document.querySelector('[data-view=report]').click()")
                b.wait("document.querySelector('.graph-node.asked')")
                assert b.js("document.querySelectorAll('.graph-node.asked').length===2")
                b.command('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                assert b.js("document.documentElement.scrollWidth<=window.innerWidth+1")
                b.js("document.querySelector('[data-view=tutor]').click()")
                b.wait("!document.querySelector('#tutor-view').hidden")
                b.js("document.querySelector('#sessions .session-delete').click();document.querySelector('#session-confirm').click()")
                b.wait("document.querySelectorAll('#sessions .session-open').length===0 && !document.querySelector('#session-dialog').open")
                b.command('Page.reload')
                b.wait("document.querySelector('#identity-name').textContent==='学生演示' && !busy && !document.querySelector('#send').disabled")
                assert b.js("session===null && document.querySelectorAll('#sessions .session-open').length===0")
                b.js("document.querySelector('#question').value='刷新后实际Copilot提问';document.querySelector('#send').click()")
                b.wait("!busy && document.querySelector('#sessions .session-open')")
                b.js("document.querySelector('#sessions .session-delete').click();document.querySelector('#session-confirm').click()")
                b.wait("document.querySelectorAll('#sessions .session-open').length===0 && !document.querySelector('#session-dialog').open")
                b.js("document.querySelector('[data-view=quiz]').click()")
                b.wait("document.querySelector('#workspace').textContent.includes('第7天')")
                assert not b.errors, b.errors
                b.js("document.querySelector('#logout').click()")
                b.wait("document.body.classList.contains('logged-out')")
                b.js("document.querySelector('#login-teacher').click()")
                b.wait("document.querySelector('#identity-name').textContent==='教师演示'")
                b.js("document.querySelector('[data-view=dashboard]').click()")
                b.wait("document.querySelector('#policy-keywords')")
                b.js("document.querySelector('#policy-keywords').value='Copilot';[...document.querySelectorAll('#workspace button')].find(x=>x.textContent==='保存策略').click()")
                b.wait("document.querySelector('#policy-result').textContent.includes('已保存')")
                b.js("document.querySelector('[data-view=tutor]').click()")
                b.wait("!document.querySelector('#tutor-view').hidden")
                assert b.js("[...document.querySelectorAll('.answer')].every(x=>x.textContent.includes('策略限制'))")
                assert b.js("document.querySelectorAll('.refs button').length===0 && !document.querySelector('#turn-context').textContent")
                assert b.js("document.documentElement.scrollWidth<=window.innerWidth+1")
                assert not b.errors, b.errors
                data = Path('data')
                data.mkdir(exist_ok=True)
                screenshot = b.command('Page.captureScreenshot', {'format':'png'})['data']
                (data/'browser-smoke.png').write_bytes(base64.b64decode(screenshot))
                report = {'status':'passed', 'cloud_calls':False,
                          'scenarios':['ask/SSE', 'L1 preview blocked', 'upgrade to L3',
                                       'preview', 'reload history', 'new session',
                                       'knowledge extraction/review', 'question generation/review',
                                       'first failure checked hint', 'quiz reload and retry',
                                       'finish/report', '1/3/7 review schedule', 'teacher/student demo login',
                                       'teacher pipeline with optional generation/review', 'student management API denied',
                                       'rename/search/cancel delete/batch delete', 'current session cleanup',
                                       'reload identity and deletion persistence', 'independent reviews preserved',
                                       'teacher overview empty metrics and policy persistence', 'global cap and think action',
                                       'DAG edges/accessibility and hostile names safely rendered', 'weekly concept markers',
                                       '390px responsive layout', 'policy tightening hides old history and context',
                                       '20+ item review queue filters and six-row pagination',
                                       'approval removes only current row and preserves other drafts/page/heading',
                                       'review failure keeps row and inline error; approved items remain searchable',
                                       'focused graph with modal detail and five-row review plan pagination/search'],
                          'renderer':'synthetic PDF export, not real PowerPoint', 'page_errors':b.errors}
                (data/'browser-smoke.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
                print(json.dumps(report, ensure_ascii=False, indent=2))
        finally:
            if browser is not None:
                browser.terminate()
                browser.wait(timeout=10)
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == '__main__':
    main()
