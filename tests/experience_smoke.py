"""P0/P1 acceptance in real local Chrome; isolated DB, deterministic models, no cloud."""
import asyncio
import json
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.request import urlopen

import uvicorn
from browser_path import chrome_path
from browser_smoke import Browser, LearningModels, free_port
from conftest import make_pptx
from websockets.sync.client import connect

from sctutor.app import create_app
from sctutor.config import Settings
from sctutor.knowledge import Knowledge, Review
from sctutor.retrieval import ingest
from sctutor.store import Store


class Models(LearningModels):
    hold=False

    async def chat(self,system,payload,tid=None,purpose='generate'):
        if purpose=='generate':
            while self.hold:
                await asyncio.sleep(.05)
            result=await super().chat(system,payload,tid,purpose)
            result['text']='先思考 `State`。\n- 第一点\n- 第二点\n```python\nx = "<img src=x onerror=alert(1)>"\n```\n<script>alert(2)</script>\n[危险](javascript:alert(3))'
            return result
        return await super().chat(system,payload,tid,purpose)


def click_text(b,selector,text):
    b.js(f"[...document.querySelectorAll({json.dumps(selector)})].find(x=>x.textContent==={json.dumps(text)}).click()")


def main():
    chrome=chrome_path()
    if not chrome.exists():
        raise SystemExit('blocked: Chrome unavailable')
    with TemporaryDirectory() as tmp:
        root=Path(tmp)
        course=root/'course'
        course.mkdir()
        make_pptx(course/'sample.pptx',('GitHub Copilot helper','Python concept'))
        settings=Settings(course_dir=course,data_dir=root/'data',llm_api_key='fake',embedding_api_key='fake',
                          llm_base_url='https://chat.example/v1',llm_model='fake-chat',
                          embedding_base_url='https://vector.example/v1',embedding_model='fake-vector')
        store=Store(settings.data_dir)
        models=Models()
        asyncio.run(ingest(store,models,course))
        app=create_app(settings,store,models)
        knowledge=Knowledge(store)
        pages=store.query('SELECT * FROM pages ORDER BY page')
        knowledge.add_extraction({'concepts':[{'name':'Copilot','page_ids':[pages[0]['id']]}],'relations':[]},[pages[0]['id']])
        concept=knowledge.concepts()[0]
        knowledge.review('concepts',concept['id'],Review(status='approved'))
        for i in range(4):
            q=app.state.quiz.add_question({'type':'choice','prompt':f'Practice {i}','options':['Copilot','Other'],'answer':'Copilot','rubric':'Identify the tool','hint':'Think of a helper','concept_ids':[concept['id']],'page_ids':[pages[0]['id']]})
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
                    with urlopen(f'http://127.0.0.1:{debug}/json',timeout=1) as response:
                        target=next(t for t in json.load(response) if t['type']=='page')
                    break
                except (OSError,StopIteration):
                    time.sleep(.05)
            if not target:
                raise RuntimeError('Chrome endpoint unavailable')
            with connect(target['webSocketDebuggerUrl']) as ws:
                b=Browser(ws)
                b.command('Runtime.enable')
                b.command('Page.enable')
                b.command('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
                b.command('Page.navigate',{'url':f'http://127.0.0.1:{port}'})
                b.wait("typeof loginAs==='function'")
                b.js("loginAs('student')")
                b.wait("identity?.id==='student' && !document.querySelector('#send').disabled")
                b.js("showView('quiz')")
                b.wait("document.querySelector('#today-tasks')")
                assert b.js("document.querySelector('#today-tasks').textContent.includes('暂无可用的到期复测')")
                click_text(b,'#workspace button','按薄弱知识点开始小测（3题）')
                b.wait("currentQuiz?.status==='active' && document.querySelectorAll('[id^=quiz-answer-]').length===3")
                qid=b.js('currentQuiz.id')
                b.js("window.second=currentQuiz.items[1].id;window.first=currentQuiz.items[0].id;const secondInput=document.querySelector('#quiz-answer-'+second);secondInput.value='Other';secondInput.dispatchEvent(new Event('input'));secondInput.focus();document.querySelector('#quiz-answer-'+first).value='Copilot';document.querySelector('#quiz-submit-'+first).click()")
                b.wait("currentQuiz.items[0].done && !quizBusy")
                assert b.js("document.querySelector('#quiz-answer-'+second).value==='Other' && document.activeElement.id==='quiz-answer-'+second")
                assert store.query('SELECT count(*) n FROM attempts')[0]['n']==1
                b.command('Page.reload')
                b.wait("typeof currentQuiz!=='undefined' && currentQuiz?.status==='active' && document.querySelectorAll('[id^=quiz-answer-]').length===2")
                assert b.js("document.querySelectorAll('[id^=quiz-answer-]')[0].value==='Other'")
                # Storage failure stays usable and does not claim successful persistence.
                b.js("window.storageSet=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k.startsWith('sctutor-quiz-draft'))throw Error('quota');return storageSet.call(this,k,v)};const field=document.querySelectorAll('[id^=quiz-answer-]')[1];field.value='Other';field.dispatchEvent(new Event('input'))")
                assert b.js("document.querySelector('#quiz-draft-status').textContent.includes('不可用')")
                b.js("renderQuiz(currentQuiz)")
                assert b.js("document.querySelectorAll('[id^=quiz-answer-]')[1].value==='Other'")
                assert b.js("document.querySelector('#quiz-draft-status').textContent.includes('不可用')")
                b.js("Storage.prototype.setItem=storageSet")
                # A failed submission preserves all input and consumes no attempt.
                b.js("window.realFetch=window.fetch;window.fetch=async(p,o)=>{if(String(p).includes('/answers'))throw Error('offline');return realFetch(p,o)};document.querySelectorAll('[id^=quiz-submit-]')[0].click()")
                b.wait("!quizBusy && document.querySelector('#workspace-notice').textContent.includes('offline')")
                assert store.query('SELECT count(*) n FROM attempts')[0]['n']==1
                assert b.js("document.querySelectorAll('[id^=quiz-answer-]')[0].value==='Other'")
                b.js("window.fetch=realFetch")
                # Escape does not cancel or clear drafts.
                click_text(b,'#workspace button','取消小测（不更新掌握状态）')
                b.wait("document.querySelector('.confirm-dialog[open]')")
                assert b.js("document.querySelector('.confirm-dialog').textContent.includes('不会更新掌握') && document.querySelector('#logout').disabled")
                b.command('Input.dispatchKeyEvent',{'type':'keyDown','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
                b.command('Input.dispatchKeyEvent',{'type':'keyUp','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
                b.wait("!document.querySelector('.confirm-dialog') && !quizBusy")
                assert store.query('SELECT status FROM quizzes WHERE id=?',(qid,))[0]['status']=='active'
                assert b.js("document.querySelectorAll('[id^=quiz-answer-]')[0].value==='Other'")
                # Account switch retains only that account's local draft, never loads it for teacher.
                b.js("document.querySelector('#logout').click()")
                b.wait('identity===null')
                b.js("loginAs('teacher')")
                b.wait("identity?.id==='teacher' && !document.querySelector('#send').disabled")
                assert b.js("currentQuiz===null && document.querySelectorAll('[id^=quiz-answer-]').length===0 && quizDrafts.memory.size===0")
                b.js("document.querySelector('#logout').click()")
                b.wait('identity===null')
                b.js("loginAs('student')")
                b.wait("currentQuiz?.id==="+json.dumps(qid))
                assert b.js("document.querySelectorAll('[id^=quiz-answer-]')[0].value==='Other'")
                click_text(b,'#workspace button','取消小测（不更新掌握状态）')
                b.wait("document.querySelector('.confirm-dialog[open]')")
                click_text(b,'.confirm-dialog button','确认取消小测')
                b.wait("!quizActive && !quizBusy && document.querySelector('#today-tasks')")
                assert store.query('SELECT status FROM quizzes WHERE id=?',(qid,))[0]['status']=='cancelled'
                assert not store.query('SELECT * FROM mastery')
                assert b.js("!Object.keys(localStorage).some(k=>k.includes("+json.dumps(qid)+"))")
                # No approved question: today-task entry is honest and does not generate paid content.
                store.execute("UPDATE questions SET status='pending'")
                before_questions=len(store.query('SELECT * FROM questions'))
                click_text(b,'#today-tasks button','练习此知识点')
                b.wait("document.querySelector('#workspace-notice').textContent.includes('审核题目')")
                assert not b.js('quizActive')
                assert len(store.query('SELECT * FROM questions'))==before_questions
                store.execute("UPDATE questions SET status='approved'")
                # Real due task from a saved, correctly completed quiz.
                b.js("startQuiz({count:1})")
                b.wait("currentQuiz?.status==='active'")
                b.js("document.querySelector('[id^=quiz-answer-]').value='Copilot';document.querySelector('[id^=quiz-submit-]').click()")
                b.wait("currentQuiz.items[0].done && !quizBusy")
                click_text(b,'#workspace button','交卷并更新掌握状态')
                b.wait("!quizActive && !quizBusy")
                store.execute("UPDATE reviews SET due=?",(time.time()-1,))
                b.js("showView('review')")
                b.wait("document.querySelector('#today-tasks')")
                assert b.js("document.querySelector('#today-tasks').textContent.includes('到期复测')")
                click_text(b,'#today-tasks button','开始最早到期复测')
                b.wait("quizActive && currentQuiz.status==='active'")
                b.js("showView('report')")
                b.wait("currentView==='quiz'")
                assert b.js("document.querySelector('#today-tasks')===null && document.querySelector('#report-content')===null")
                click_text(b,'#workspace button','取消小测（不更新掌握状态）')
                b.wait("document.querySelector('.confirm-dialog[open]')")
                click_text(b,'.confirm-dialog button','确认取消小测')
                b.wait('!quizActive && !quizBusy')
                # Safe code presentation and visible latest-turn actions.
                b.js("showView('tutor')")
                b.wait("currentView==='tutor' && !document.querySelector('#send').disabled")
                b.js("document.querySelector('#question').value='Copilot';document.querySelector('#send').click()")
                b.wait("turns.length===1 && !busy && !pendingRecovery")
                assert b.js("document.querySelector('.answer pre code').textContent.includes('<img') && !document.querySelector('.answer img,.answer script,.answer a')")
                assert b.js("document.querySelectorAll('.answer li').length===2 && document.querySelector('#messages #turn-feedback')!==null")
                b.js("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.copiedCode=text}}});document.querySelector('.code-toolbar button').click()")
                b.wait("window.copiedCode?.includes('<img')")
                # Preserve old-reading position, then explicitly jump to latest.
                b.js("window.realTurns=turns;turns=Array.from({length:20},(_,i)=>({...realTurns[0],id:'display-'+i,answer:'long line\\n'.repeat(25)}));render({bottom:true});document.querySelector('#messages').scrollTop=0;render()")
                assert b.js("document.querySelector('#messages').scrollTop===0 && !document.querySelector('#latest-answer').hidden")
                b.js("document.querySelector('#latest-answer').click()")
                assert b.js("document.querySelector('#messages').scrollTop>100")
                b.js("turns=realTurns;render({bottom:true})")
                # Truncated SSE after server completion recovers the answer, never auto repeats.
                before=len(store.query('SELECT * FROM turns'))
                b.js("window.realFetch=window.fetch;window.fetch=async(p,o)=>{const r=await realFetch(p,o);if(String(p).endsWith('/turns')&&o?.method==='POST'){await r.text();return new Response('event: status\\ndata: {\"stage\":\"check\"}\\n\\n',{status:200})}return r};document.querySelector('#question').value='second';document.querySelector('#send').click()")
                b.wait("turns.length===2 && !busy && !pendingRecovery")
                assert len(store.query('SELECT * FROM turns'))==before+1
                b.js("window.fetch=realFetch")
                # Network failure before POST: no automatic resend, explicit retry reuses request ID.
                before=len(store.query('SELECT * FROM turns'))
                b.js("window.fetch=async(p,o)=>{if(String(p).endsWith('/turns')&&o?.method==='POST')throw Error('offline before send');return realFetch(p,o)};document.querySelector('#question').value='third';document.querySelector('#send').click()")
                b.wait('!busy && pendingRecovery && recoveryRecord!==null')
                assert len(store.query('SELECT * FROM turns'))==before
                assert b.js("document.querySelector('#question').value==='third' && document.querySelector('#send').disabled")
                request_id=b.js('recoveryRecord.request_id')
                # Reload restores uncertainty without auto-sending; a failed status query cannot unlock retry.
                b.command('Page.reload')
                b.wait("typeof recoveryRecord!=='undefined' && recoveryRecord!==null && pendingRecovery && !busy")
                assert b.js('recoveryRecord.request_id')==request_id
                b.js("window.realFetch=window.fetch;window.retryPosts=0;window.fetch=async(p,o)=>{if(o?.method==='POST')retryPosts++;if(String(p).includes('/api/sessions/')&&!o?.method)throw Error('history offline');return realFetch(p,o)};document.querySelector('#retry-turn').click()")
                b.wait("document.querySelector('#notice').textContent.includes('无法核实')")
                assert b.js('retryPosts===0 && pendingRecovery')
                b.js("window.fetch=realFetch;document.querySelector('#retry-turn').click()")
                b.wait('turns.length===3 && !busy && !pendingRecovery')
                assert store.query('SELECT count(*) n FROM turn_requests WHERE request_id=?',(request_id,))[0]['n']==1
                # A 5xx is uncertain, not proof that the request never reserved a turn.
                b.js("window.fetch=async(p,o)=>{if(String(p).endsWith('/turns')&&o?.method==='POST')return new Response('{\"message\":\"temporary\"}',{status:503});return realFetch(p,o)};document.querySelector('#question').value='http uncertainty';document.querySelector('#send').click()")
                b.wait('!busy && pendingRecovery && recoveryRecord!==null')
                assert b.js('recoveryRecord.terminal===false')
                http_request=b.js('recoveryRecord.request_id')
                b.js("window.fetch=realFetch;document.querySelector('#retry-turn').click()")
                b.wait('turns.length===4 && !busy && !pendingRecovery')
                assert store.query('SELECT count(*) n FROM turn_requests WHERE request_id=?',(http_request,))[0]['n']==1
                # Stop is explicit; server records a terminal safe cancellation, not an unchecked draft.
                models.hold=True
                b.js("document.querySelector('#question').value='slow';document.querySelector('#send').click()")
                b.wait("busy && !document.querySelector('#stop-waiting').hidden")
                time.sleep(1.1)
                assert b.js("!document.querySelector('#wait-elapsed').textContent.includes(' 0 秒')")
                b.js("document.querySelector('#stop-waiting').click()")
                b.wait("document.querySelector('.confirm-dialog[open]')")
                assert b.js("document.querySelector('.confirm-dialog').textContent.includes('免计费')")
                click_text(b,'.confirm-dialog button','停止等待')
                b.wait('!busy')
                models.hold=False
                for _ in range(100):
                    if store.query('SELECT status FROM turns ORDER BY created DESC LIMIT 1')[0]['status']!='running':
                        break
                    time.sleep(.05)
                b.js('refreshTurnState()')
                b.wait("!pendingRecovery && ['error','completed'].includes(turns.at(-1)?.status)")
                assert b.js("document.querySelector('#question').value==='slow'")
                # A running record recovered from history blocks send until terminal.
                sid=b.js('session')
                turn=store.reserve_turn(sid,'ask','reserved-running',None,request_id='offline-running-test')
                b.js('refreshTurnState()')
                b.wait("pendingRecovery && document.querySelector('#send').disabled")
                store.finish(turn['id'],'safe',[],'error',1)
                b.js('refreshTurnState()')
                b.wait('!pendingRecovery')
                # Teacher structured fields: schema validation, preview, local queue preservation and versioning.
                b.js("document.querySelector('#logout').click()")
                b.wait('identity===null')
                b.js("loginAs('teacher')")
                b.wait("identity?.id==='teacher' && !document.querySelector('#send').disabled")
                b.js("showView('questions')")
                b.wait("document.querySelector('#review-status')")
                b.js("document.querySelector('#review-status').value='all';document.querySelector('#review-status').dispatchEvent(new Event('change'));document.querySelectorAll('.review-row details').forEach(x=>x.open=true)")
                b.wait("document.querySelectorAll('.question-editor').length>=2")
                b.js("window.row=document.querySelector('.review-row');window.oldQuestion=row.dataset.itemId;window.editor=row.querySelector('.question-editor');window.otherEditor=document.querySelectorAll('.question-editor')[1];otherEditor.querySelector('[aria-label=题干]').value='unsubmitted other draft';editor.querySelector('[aria-label=参考答案]').value='not an option'")
                click_text(b,'.review-row:first-child button','保存为新待审版本')
                b.wait("document.querySelector('[aria-invalid=true]')")
                assert b.js("editor.querySelector('[aria-label=参考答案]').value==='not an option' && otherEditor.querySelector('[aria-label=题干]').value==='unsubmitted other draft'")
                assert b.js("!editor.querySelector('.question-preview').textContent.includes('评分要点')")
                # Offline failure keeps a valid form; then four types save to new pending versions.
                b.js("editor.querySelector('[aria-label=参考答案]').value='Copilot';window.fetch=async(p,o)=>{if(o?.method==='PUT')throw Error('offline edit');return realFetch(p,o)}")
                click_text(b,'.review-row:first-child button','保存为新待审版本')
                b.wait("row.querySelector('.notice').textContent.includes('offline edit')")
                assert b.js("editor.querySelector('[aria-label=参考答案]').value==='Copilot'")
                b.js('window.fetch=realFetch')
                for kind in ['blank','short','code','choice']:
                    b.js(f"window.row=document.querySelector('.review-row');window.oldQuestion=row.dataset.itemId;window.editor=row.querySelector('.question-editor');editor.querySelector('[aria-label=题型]').value={json.dumps(kind)};editor.querySelector('[aria-label=题型]').dispatchEvent(new Event('change'));editor.querySelector('[aria-label=题干]').value='Updated {kind}';editor.querySelector('[aria-label=题干]').dispatchEvent(new Event('input'));editor.querySelector('[aria-label=参考答案]').value='Copilot'")
                    assert b.js("[...editor.querySelectorAll('.editor-diff')].some(x=>x.textContent.includes('题干'))")
                    before_q=len(store.query('SELECT * FROM questions'))
                    click_text(b,'.review-row:first-child button','保存为新待审版本')
                    b.wait("document.querySelector('#review-notice').textContent.includes('新版本已加入待审') && !document.querySelector('.review-row:first-child button').disabled")
                    assert len(store.query('SELECT * FROM questions'))==before_q+1
                    assert store.query('SELECT status FROM questions WHERE id=?',(b.js('oldQuestion'),))[0]['status']=='rejected'
                    newest=store.query('SELECT * FROM questions ORDER BY created DESC LIMIT 1')[0]
                    assert newest['status']=='pending'
                    assert json.loads(newest['data'])['type']==kind
                    assert b.js("otherEditor.isConnected && otherEditor.querySelector('[aria-label=题干]').value==='unsubmitted other draft'")
                # Narrow screen / keyboard and no viewport overflow.
                b.command('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                assert b.js('document.documentElement.scrollWidth<=window.innerWidth+1')
                b.command('Input.dispatchKeyEvent',{'type':'keyDown','key':'Tab','code':'Tab','windowsVirtualKeyCode':9})
                b.command('Input.dispatchKeyEvent',{'type':'keyUp','key':'Tab','code':'Tab','windowsVirtualKeyCode':9})
                assert b.js("document.activeElement!==document.body")
                b.js("showView('tutor')")
                b.wait("currentView==='tutor'")
                assert b.js('document.documentElement.scrollWidth<=window.innerWidth+1')
                assert not b.errors,b.errors
                print(json.dumps({'status':'passed','browser':'Chrome desktop/390px','cloud_calls':0,'checks':['cross-item and reload drafts','storage failure','submit failure','Esc and confirmed cancel','identity isolation','due review and quiz lockdown','safe code and copy','reading scroll','truncated SSE','explicit idempotent retry','stop waiting and history lock','four question types/version/validation/preview/other drafts','keyboard and viewport']},ensure_ascii=False))
        finally:
            models.hold=False
            if browser:
                browser.terminate()
                browser.wait(timeout=10)
            server.should_exit=True
            thread.join(timeout=10)


if __name__=='__main__':
    main()
