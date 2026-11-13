"""Chrome/CDP acceptance of new prototype controls, isolated DB and no cloud calls."""
import asyncio
import base64
import json
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.request import urlopen

import pymupdf
import uvicorn
from browser_path import chrome_path
from browser_smoke import Browser, LearningModels, free_port
from test_course_files import valid_pptx
from websockets.sync.client import connect

from sctutor.app import create_app
from sctutor.config import Settings
from sctutor.knowledge import Knowledge, Review
from sctutor.retrieval import ingest
from sctutor.store import Store


class Models(LearningModels):
    async def chat(self, system, payload, tid=None, purpose='generate'):
        if purpose == 'relevance' and 'external-only' in payload['question']:
            return {'relevant_ids': []}
        return await super().chat(system,payload,tid,purpose)


class Search:
    async def search(self, question, tid):
        return [{'id':'web-'+tid, 'type':'web', 'url':'https://example.org/course',
                 'title':'外部来源示例', 'text':'仅用于离线搜索边界验证', 'retrieved':time.time()}]


def main():
    chrome=chrome_path()
    if not chrome.exists():
        raise SystemExit('blocked: Chrome unavailable')
    with TemporaryDirectory() as tmp:
        root=Path(tmp)
        course=root/'course'
        course.mkdir()
        source=valid_pptx(course/'lesson.pptx',('GitHub Copilot course source','Python second page','Third safe page'))
        settings=Settings(course_dir=course,data_dir=root/'data',llm_api_key='fake',embedding_api_key='fake',tavily_api_key='fake',
                          llm_base_url='https://chat.example/v1',llm_model='fake-chat',
                          embedding_base_url='https://vector.example/v1',embedding_model='fake-vector')
        store=Store(settings.data_dir)
        models=Models()
        asyncio.run(ingest(store,models,course))
        app=create_app(settings,store,models)
        app.state.tutor.websearch=Search()
        renderer=app.state.pipeline.renderer
        def exporter(copy,pdf):
            with pymupdf.open() as doc:
                for text in ['GitHub Copilot course source','Python second page','Third safe page']:
                    doc.new_page().insert_text((72,72),text)
                doc.save(pdf)
        rendered=renderer.render(source,exporter)
        pages=store.query('SELECT * FROM pages ORDER BY page')
        for p in pages:
            store.execute('INSERT INTO page_assets VALUES (?,?,?,?)',(p['id'],str(rendered['directory']/f'page-{p["page"]}.png'),'','smoke'))
        knowledge=Knowledge(store)
        knowledge.add_extraction({'concepts':[{'name':'Copilot','page_ids':[pages[0]['id']]}],'relations':[]},[pages[0]['id']])
        concept=knowledge.concepts()[0]
        knowledge.review('concepts',concept['id'],Review(status='approved'))
        q=app.state.quiz.add_question({'type':'choice','prompt':'Which course tool?','options':['Copilot','Other'],'answer':'Copilot','rubric':'tool','hint':'Think about tools','concept_ids':[concept['id']],'page_ids':[pages[0]['id']]})
        app.state.quiz.review_question(q['id'],'approved')
        port,debug=free_port(),free_port()
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
        thread=Thread(target=server.run,daemon=True)
        thread.start()
        browser=None
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            browser=subprocess.Popen([str(chrome),'--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-background-networking','--disable-extensions',f'--remote-debugging-port={debug}',f'--user-data-dir={root/"chrome"}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            target=None
            for _ in range(200):
                try:
                    with urlopen(f'http://127.0.0.1:{debug}/json',timeout=1) as r:
                        target=next(t for t in json.load(r) if t['type']=='page')
                    break
                except (OSError,StopIteration):
                    time.sleep(.05)
            if target is None:
                raise RuntimeError('Chrome debug endpoint missing')
            with connect(target['webSocketDebuggerUrl']) as ws:
                b=Browser(ws)
                b.command('Runtime.enable')
                b.command('Page.enable')
                b.command('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
                b.command('Page.navigate',{'url':f'http://127.0.0.1:{port}'})
                b.wait("typeof loginAs==='function' && document.querySelector('#login-teacher')")
                b.js("document.querySelector('#login-teacher').click()")
                b.wait("identity?.id==='teacher' && !document.querySelector('#send').disabled && document.querySelector('#course-navigation button')")
                assert b.js("[...document.querySelectorAll('#features button')].filter(x=>!x.hidden).slice(0,4).map(x=>x.textContent).join(',')==='班级概览,辅导策略,题目审核,课件管理'")
                b.js("(async()=>{await Promise.all(Array.from({length:6},()=>showView('tutor')));await showView('report');await showView('tutor');document.querySelector('#new-session').click();})()")
                assert store.query('SELECT count(*) n FROM sessions')[0]['n']==0
                assert b.js("session===null && turns.length===0")
                b.js("(async()=>{window.emptySession=(await jsonApi('/api/sessions',{})).id;await openSession(emptySession);for(let i=0;i<5;i++)await showView('tutor');})()")
                assert store.query('SELECT count(*) n FROM sessions')[0]['n']==1
                assert b.js("session===window.emptySession && turns.length===0")
                b.js("showView('policy')")
                b.wait("document.querySelector('#workspace h1')?.textContent==='辅导策略' && document.activeElement.id==='policy-max-level'")
                b.js("showView('questions')")
                b.wait("document.querySelector('#workspace h1')?.textContent==='题目审核' && document.querySelector('[data-review-kind=questions]').classList.contains('selected')")
                assert b.js("document.querySelector('#course-upload').hidden")
                b.js("showView('dashboard')")
                b.wait("document.querySelector('#policy-full-answer')")
                b.js("document.querySelector('#policy-max-level').value='5';document.querySelector('#policy-full-answer').checked=true;document.querySelector('#policy-web-search').checked=true;[...document.querySelectorAll('#workspace button')].find(x=>x.textContent==='保存策略').click()")
                b.wait("document.querySelector('#policy-result').textContent.includes('已保存')")
                b.js("showView('tutor')")
                b.wait("currentView==='tutor' && !quizActive")
                b.js("document.querySelector('#action').value='ask-l1';document.querySelector('#question').value='Copilot';document.querySelector('#send').click()")
                b.wait("turns.length===1 && !busy")
                assert store.query('SELECT count(*) n FROM sessions')[0]['n']==1
                for count in (2,3):
                    b.js("document.querySelector('#upgrade').click()")
                    b.wait(f"turns.length==={count} && !busy")
                b.js("[...document.querySelectorAll('.refs button')].at(-1).click()")
                b.wait("document.querySelector('#material-controls') && !document.querySelector('#preview-image').hidden")
                store.execute("INSERT INTO concepts VALUES ('nav-restricted','临时受限页',?,'approved',1,'nav-signature')",(json.dumps([pages[1]['id']]),))
                b.js("[...document.querySelectorAll('#material-controls button')].find(x=>x.textContent==='下一页').click()")
                b.wait("document.querySelector('#material-location').textContent.includes('限制')")
                assert b.js("document.querySelector('#preview-title').textContent.includes('第 1 / 3 页')")
                b.js("document.querySelector('[aria-label=课件页码]').value='4';[...document.querySelectorAll('#material-controls button')].find(x=>x.textContent==='跳页').click()")
                b.wait("document.querySelector('#material-location').textContent.includes('超出范围')")
                assert b.js("document.querySelector('#preview-title').textContent.includes('第 1 / 3 页')")
                store.execute("DELETE FROM concepts WHERE id='nav-restricted'")
                b.js("[...document.querySelectorAll('#material-controls button')].find(x=>x.textContent==='下一页').click()")
                b.wait("document.querySelector('#preview-title').textContent.includes('第 2 / 3 页')")
                b.js("[...document.querySelectorAll('#material-controls button')].find(x=>x.textContent==='上一页').click()")
                b.wait("document.querySelector('#preview-title').textContent.includes('第 1 / 3 页')")
                b.js("document.querySelector('[aria-label=定位片段]').value='GitHub Copilot';[...document.querySelectorAll('#material-controls button')].find(x=>x.textContent==='定位片段').click()")
                b.wait("document.querySelector('#preview-text mark') && document.querySelector('#material-location').textContent.includes('真实文字坐标')")
                b.js("[...document.querySelectorAll('#material-controls button')].find(x=>x.textContent==='放大原页').click()")
                b.wait("document.querySelector('#material-dialog[open] .location-rect')")
                b.js("document.querySelector('#material-dialog button').click();window.downloads=[];saveDownload=async(r,n)=>{const blob=await r.blob();downloads.push({name:n,size:blob.size,text:n.endsWith('.csv')?await blob.text():''});};[...document.querySelectorAll('#material-controls button')].find(x=>x.textContent.includes('打开文件')).click()")
                b.wait("downloads.some(x=>x.name.endsWith('.pdf')&&x.size>100)")
                for count in (4,5):
                    b.js("document.querySelector('#upgrade').click()")
                    b.wait(f"turns.length==={count} && !busy")
                assert b.js("turns.at(-1).level===5 && document.querySelector('[data-level=\"5\"]').classList.contains('active')")
                b.js("document.querySelector('#action').value='ask';document.querySelector('#question').value='external-only';document.querySelector('#send').click()")
                b.wait("turns.length===6 && !busy")
                assert b.js("document.querySelector('#messages').textContent.includes('外部来源示例') && turns.at(-1).citations[0].type==='web'")
                for count in (7,8):
                    b.js("document.querySelector('#upgrade').click()")
                    b.wait(f"turns.length==={count} && !busy")
                b.js("[...document.querySelectorAll('.refs button')].at(-1).click()")
                b.wait("document.querySelector('#preview-text a[rel=\"noopener noreferrer\"]')")
                b.js("showView('dashboard')")
                b.wait("document.querySelector('.date-controls')")
                b.js("document.querySelector('.date-controls select').value='30';document.querySelector('.date-controls button').click()")
                b.wait("reportRanges.dashboard.includes('start=') && document.querySelector('#policy-keywords')")
                assert b.js("document.querySelector('.date-controls select').value==='30'")
                b.js("document.querySelector('[aria-label=导出CSV]').click()")
                b.wait("downloads.some(x=>x.name==='sctutor-dashboard.csv'&&x.text.includes('questions'))")
                b.js("showView('admin')")
                b.wait("document.querySelector('#course-upload')")
                upload=base64.b64encode(valid_pptx(root/'new.pptx',('new course file',)).read_bytes()).decode()
                b.js("document.querySelector('#course-upload').open=true;const dt=new DataTransfer();dt.items.add(new File([Uint8Array.from(atob('"+upload+"'),c=>c.charCodeAt(0))],'new.pptx',{type:'application/vnd.openxmlformats-officedocument.presentationml.presentation'}));document.querySelector('#course-files').files=dt.files;[...document.querySelectorAll('#course-upload button')].find(x=>x.textContent==='上传课件').click()")
                b.wait("document.querySelector('#upload-result').textContent.includes('uploaded')")
                b.js("[...document.querySelectorAll('#course-upload button')].find(x=>x.textContent.includes('同步课程目录')).click()")
                b.wait("document.querySelector('#upload-result').textContent.includes('new.pptx') && document.querySelectorAll('#course-navigation button').length===3")
                b.js("document.querySelector('#logout').click()")
                b.wait("!identity")
                b.js("document.querySelector('#login-student').click()")
                b.wait("identity?.id==='student' && !document.querySelector('#send').disabled")
                assert b.js("[...document.querySelectorAll('#features button')].filter(x=>!x.hidden).map(x=>x.textContent).join(',')==='答疑,复习,小测,学习报告'")
                b.js("(async()=>{await showView('review');})()")
                b.wait("document.querySelector('#workspace h1')?.textContent==='复习' && document.querySelector('#review-plan-list')")
                b.js("(async()=>{for(let i=0;i<5;i++)await showView('tutor');})()")
                assert store.query("SELECT count(*) n FROM sessions s JOIN session_owners o ON o.session_id=s.id WHERE o.account='student'")[0]['n']==0
                assert b.js("session===null")
                b.js("showView('report')")
                b.wait("document.querySelector('#report-overview-tab') && document.querySelector('.date-controls')")
                b.js("document.querySelector('.date-controls select').value='custom';document.querySelector('.date-controls select').dispatchEvent(new Event('change'));document.querySelector('[aria-label=开始日期]').value='2026-01-01';document.querySelector('[aria-label=\"结束日期（包含）\"]').value='2026-12-31';document.querySelector('.date-controls button').click()")
                b.wait("reportRanges.report.includes('start=') && document.querySelector('#report-filter')")
                b.js("document.querySelector('[aria-label=导出CSV]').click()")
                b.wait("downloads.some(x=>x.name==='sctutor-report.csv'&&x.text.includes('Copilot'))")
                b.command('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                assert b.js("document.documentElement.scrollWidth<=innerWidth+1")
                b.js("showView('quiz')")
                b.wait("[...document.querySelectorAll('#workspace button')].some(x=>x.textContent.includes('按薄弱'))")
                b.js("[...document.querySelectorAll('#workspace button')].find(x=>x.textContent.includes('按薄弱')).click()")
                b.wait("quizActive && document.querySelector('#workspace h1').textContent.includes('进行中')")
                assert b.js("!document.querySelector('#material-controls') && !document.querySelector('#preview-text').textContent.includes('GitHub Copilot') && document.querySelectorAll('#course-navigation button').length===0")
                assert b.js("(async()=> (await fetch('/api/report/export')).status===409)()")
                assert not b.errors,b.errors
                Path('data').mkdir(exist_ok=True)
                Path('data/prototype-smoke.png').write_bytes(base64.b64decode(b.command('Page.captureScreenshot',{'format':'png'})['data']))
                result={'status':'passed','cloud_calls':False,'renderer':'synthetic source-matched PDF','scenarios':['L1–L5 and teacher switches','Tavily fixture and external links','neighbours/thumbnails/real coordinates/zoom/PDF/restricted-page-visible-error/retry','date presets/custom/CSV','teacher upload/local sync','student report/role isolation','390px/active quiz sealing'],'page_errors':b.errors}
                Path('data/prototype-smoke.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
                print(json.dumps(result,ensure_ascii=False,indent=2))
        finally:
            if browser:
                browser.terminate()
                browser.wait(timeout=10)
            server.should_exit=True
            thread.join(timeout=10)


if __name__=='__main__':
    main()
