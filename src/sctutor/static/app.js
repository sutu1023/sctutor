'use strict';
const $ = id => document.getElementById(id);
let session = null, turns = [], busy = false, quizActive = false;
let pendingRecovery=false,recoveryRecord=null,turnController=null,waitTimer=null;
const recoveryKey=()=> 'sctutor-pending-turn-'+identity.id;
function saveRecovery(){try{if(recoveryRecord)sessionStorage.setItem(recoveryKey(),JSON.stringify(recoveryRecord));else sessionStorage.removeItem(recoveryKey());}catch{ /* Current input and in-memory recovery remain usable. */ }}
let activeMaxLevel = 4, activeL5 = false;
let identity = null, identityEpoch = 0, sessionSearchVersion = 0;
const selectedSessions = new Set();
const sessionKey = () => 'sctutor-session-' + identity.id;
const stages = {web:'正在进行教师允许的联网检索…',classify:'正在确定问题类型与起始级别…', retrieve:'正在检索课件…', relevance:'正在核对证据相关性…', generate:'草稿已生成，等待检查…', check:'独立检查完成…', finish:'正在保存本轮记录…'};
function el(tag, text, cls) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; }
async function api(path, options={}) {
  const epoch=identityEpoch, r=await fetch(path, options);
  if(epoch!==identityEpoch)throw Error('账号已切换，请重新操作。');
  if(!r.ok){ let body={}; try{body=await r.json();}catch{}
    if(r.status===401 && !path.startsWith('/api/auth/'))resetIdentity();
    const error=Error(typeof body.detail==='string'?body.detail:body.message || `请求失败（${r.status}），请检查输入或稍后重试。`);error.status=r.status;throw error; }
  return r;
}
function note(text){$('notice').textContent=text;}
function controls(){
  document.querySelector('[data-level="5"]').textContent=activeL5?'L5 完整解答':'L5 未开放';
  const locked=busy||pendingRecovery||turns.some(t=>t.status==='running');
  for(const id of ['send','action','new-session'])$(id).disabled=locked || quizActive;
  $('recover-turn').hidden=quizActive||!session||(!locked&&!recoveryRecord);$('recover-turn').disabled=busy;
  $('retry-turn').hidden=quizActive||!recoveryRecord||busy;$('retry-turn').disabled=turns.some(t=>t.status==='running');
  $('stop-waiting').hidden=!busy||!turnController;$('recovery-help').hidden=!busy&&!pendingRecovery&&!recoveryRecord;
  const last=turns.at(-1);
  $('upgrade').disabled=busy || quizActive || !last || last.closed || last.policy_blocked || last.status!=='completed' || last.level>=Math.min(activeMaxLevel,last.max_level || 4);
  if(last?.level===4&&last.max_level===5&&!last.l5_eligible)$('upgrade').disabled=true;
  $('upgrade').textContent=last?.level===4?'请求完整解答（L5）':'再给一点提示';
  $('upgrade').title=last?.l5_notice || '';
  $('understood').disabled=busy || quizActive || !last || last.closed || last.policy_blocked || last.status!=='completed';
  $('think').disabled=$('understood').disabled;
  const feedbackReason=locked?'请求状态尚未核实，请先等待或刷新本轮状态。':quizActive?'小测进行中，答疑暂不可用。':!last?'发送一个问题后，再选择如何继续。':last.closed?'本问题已结束，请开始新问题。':last.policy_blocked?'教师策略限制了本轮内容。':last.status!=='completed'?'本轮没有通过检查的回答，请先处理失败状态。':last.level>=Math.min(activeMaxLevel,last.max_level||4)?'已达到本题允许的最高级别。':last.level===4&&!last.l5_eligible?(last.l5_notice || '完整解答未开放或尚未走完完整阶梯。'):'继续思考保持级别；更多提示才逐级升级。';
  $('feedback-reason').textContent=feedbackReason;$('upgrade').title=feedbackReason;
  if(locked)for(const id of ['think','upgrade','understood'])$(id).disabled=true;
  $('level-description').textContent=last?`当前 L${last.level}：${['','先说说你的思路','提供概念提示','指向课件依据','提供部分步骤','教师授权的完整解答'][last.level]}`:'逐级提示：先思考，再按需要增加帮助。';
  document.querySelectorAll('#sessions button,#sessions input').forEach(b=>b.disabled=locked || quizActive);
  $('delete-selected').disabled=locked || quizActive || !selectedSessions.size;
  $('logout').disabled=busy||quizBusy;
}
function render({bottom=false}={}){
  const box=$('messages'),follow=bottom||box.scrollHeight-box.scrollTop-box.clientHeight<70,position=box.scrollTop,feedback=$('turn-feedback');
  const expanded=new Set([...box.querySelectorAll('.check-details[open]')].map(x=>x.dataset.turn));feedback.remove();box.replaceChildren();
  if(!turns.length){const n=el('div',undefined,'empty');n.append(el('h2','从一个真实困惑开始'),el('p','先说说你的理解，需要更多帮助时再逐级增加提示。'));box.append(n);}
  for(const t of turns){
    const wrap=el('article',undefined,'turn');
    wrap.append(el('div',t.action==='upgrade'?'再给一点提示':t.question,'question'));
    wrap.append(el('div',`L${t.level} · ${t.policy_blocked?'教师策略限制':t.status==='completed'?'泄题检查通过':t.status==='running'?'处理中':t.status==='safe'?'安全反馈':'请求未完成'}${t.elapsed!=null?' · '+t.elapsed.toFixed(1)+' 秒':''}`,'turn-meta'));
    const details=el('details',undefined,'check-details');details.dataset.turn=t.id;details.open=expanded.has(t.id);details.append(el('summary','查看检查与重写详情'),el('p',`${t.reason || ''} · 重写 ${t.rewrites || 0} 次${t.closed?' · 问题已结束':''}${t.policy_blocked?' · 教师策略限制':''}`,'muted'));wrap.append(details);
    wrap.append(answerContent(t.answer || '正在处理…'));
    if(t.l5_notice)wrap.append(el('p',t.l5_notice,'notice'));
    const refs=el('div',undefined,'refs');
    for(const c of t.citations){
      const title=c.type==='web'?`外部来源 · ${c.title} · ${new Date(c.retrieved*1000).toLocaleDateString()}`:`${c.file} · 第 ${c.page} 页`;
      const b=el('button',title+(t.level<3?'（L3 起可预览）':''));b.disabled=t.level<3;
      b.onclick=async()=>{try{const opened=await openEvidence(`/api/turns/${t.id}/citations/${c.id}`,title);if(opened&&c.type==='web'){const link=el('a','打开外部来源（非课件）');const u=new URL(c.url);if(u.protocol!=='https:'||u.username||u.password)throw Error('外部URL不安全');link.href=u.href;link.target='_blank';link.rel='noopener noreferrer';$('preview-text').append(document.createTextNode('\n'),link);}}catch(e){note(e.message);}};
      refs.append(b);
    }
    wrap.append(refs);box.append(wrap);
  }
  const level=turns.at(-1)?.level || 1;
  document.querySelectorAll('[data-level]').forEach(n=>n.classList.toggle('active',Number(n.dataset.level)===level));
  renderTurnContext(turns.at(-1));
  feedback.hidden=!turns.length;box.append(feedback);
  box.scrollTop=follow?box.scrollHeight:position;$('latest-answer').hidden=follow||!turns.length;controls();
}
async function listSessions(){
  const version=++sessionSearchVersion;
  const list=await(await api('/api/sessions?search='+encodeURIComponent($('session-search').value))).json();
  if(version!==sessionSearchVersion)return list;
  $('sessions').replaceChildren();
  for(const [i,s] of list.entries()){
    const row=el('div',undefined,'session-row'), check=el('input');check.type='checkbox';check.checked=selectedSessions.has(s.id);check.setAttribute('aria-label','选择会话 '+(s.name || s.id.slice(0,6)));
    check.onchange=()=>{if(check.checked)selectedSessions.add(s.id);else selectedSessions.delete(s.id);controls();};
    const title=s.name || `会话 ${list.length-i} · ${new Date(s.created*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}`;
    const b=el('button',title,s.id===session?'selected session-open':'session-open');b.title=title;
    b.onclick=()=>openSession(s.id).catch(e=>note(e.message));
    const rename=el('button','重命名'), remove=el('button','删除');rename.className='session-rename';remove.className='session-delete';
    rename.onclick=()=>sessionDialog('rename',[s.id],s.name || '');remove.onclick=()=>sessionDialog('delete',[s.id]);
    row.append(check,b,rename,remove);$('sessions').append(row);
  } controls(); return list;
}
async function openSession(id){
  if(busy || quizActive)return;
  const data=await(await api('/api/sessions/'+encodeURIComponent(id))).json();session=id;turns=data.turns;
  if(recoveryRecord&&recoveryRecord.sid!==id){recoveryRecord=null;saveRecovery();}
  localStorage.setItem(sessionKey(),id);$('action').value='ask';pendingRecovery=turns.some(t=>t.status==='running');clearPreview();render({bottom:true});await listSessions();
}
async function createSession(){
  if(busy||pendingRecovery||quizActive)return;
  recoveryRecord=null;saveRecovery();
  // An empty chat is a local draft. Persist exactly once, when its first question is sent.
  if(!session||!turns.length){clearPreview();render();return;}
  session=null;turns=[];localStorage.removeItem(sessionKey());$('action').value='ask';
  clearPreview();render();await listSessions();
}
function clearPreview(){if(typeof resetMaterialPreview==='function')resetMaterialPreview();document.body.classList.remove('preview-open');$('turn-context').replaceChildren();$('preview-title').textContent='尚未打开课件页';$('preview-text').textContent='点击 L3–L5 出处或主动复习知识点查看课件；作答期间仅可看已终结题的依据。';$('preview-image').hidden=true;$('preview-image').removeAttribute('src');}
function renderTurnContext(t){
  const box=$('turn-context');box.replaceChildren();
  if(!t || quizActive || t.policy_blocked || currentView!=='tutor')return;
  box.append(el('h2','这一轮涉及的知识点'));
  if(!t.concepts?.length)box.append(el('p','尚无可确认的审核知识点关联。','muted'));
  for(const x of t.concepts || []){const row=el('div',undefined,'context-concept');row.append(el('strong',x.name),el('span',x.probability===null?'未评估':`${Math.round(x.probability*100)}% · 模型估计`,'muted'));box.append(row);}
  box.append(el('h3','检索到的其他课件页'));
  for(const x of t.candidates || [])box.append(el('p',`${x.file} · 第${x.page}页`,'candidate-meta'));
  box.append(el('p','候选不等于已引用依据，仅显示元数据；正文仍受提示级别限制。','muted'));
}
function showImage(url){const image=$('preview-image'),epoch=previewEpoch,account=identityEpoch,probe=new Image();image.hidden=true;image.removeAttribute('src');probe.onload=()=>{if(epoch===previewEpoch&&account===identityEpoch){image.src=url;image.hidden=false;}};probe.onerror=()=>{};probe.src=url;}
async function refreshTurnState(){
  if(!session||busy||quizActive)return;
  const sid=session,epoch=identityEpoch;
  try{
    const data=await(await api('/api/sessions/'+sid)).json();if(epoch!==identityEpoch||sid!==session)return;
    turns=data.turns;const running=turns.some(t=>t.status==='running'),match=recoveryRecord&&turns.find(t=>t.request_id===recoveryRecord.request_id);
    pendingRecovery=running||!!(recoveryRecord&&!match&&!recoveryRecord.terminal);
    if(match?.status==='completed'){recoveryRecord=null;saveRecovery();note('本轮已经完成，已恢复受检回答；没有重复发送。');}
    else if(running)note('服务端仍在处理，不能重复发送；稍后可再次刷新状态。');
    else if(match){recoveryRecord.terminal=true;pendingRecovery=false;saveRecovery();note((match.answer || '本轮已结束但未完成有效回答。')+' 输入保留；可明确重试，新的调用可能计费。');}
    else if(recoveryRecord?.terminal)note((recoveryRecord.failure || '请求未获受理。')+' 输入保留，请修正问题后发送，或明确重试。');
    else if(recoveryRecord)note('尚未找到该请求的记录，结果未确定。可继续刷新，或明确重试同一请求ID以避免重复调用。');
    else note('状态已刷新。没有运行中的请求。');
    render();return true;
  }catch(e){pendingRecovery=true;note('无法核实本轮状态，请恢复网络后再刷新。'+e.message);controls();return false;}
}
async function send(body,retry=false){
  if(busy||quizActive||(!retry&&pendingRecovery))return;
  const epoch=identityEpoch;busy=true;controls();note('正在核对请求状态…');let done=false,success=false,httpRejected=false;
  const previous=retry?recoveryRecord:null;
  try{
    if(session){
      turns=(await(await api('/api/sessions/'+session)).json()).turns;
      if(turns.some(t=>t.status==='running')){pendingRecovery=true;throw Error('服务端仍在处理，请先刷新本轮状态。');}
      const accepted=previous&&turns.find(t=>t.request_id===previous.request_id);
      if(accepted?.status==='completed'){recoveryRecord=null;pendingRecovery=false;saveRecovery();note('本轮已经完成，已恢复结果；没有重复调用。');return;}
      if(accepted)previous.terminal=true;
    }
    if(body.action==='ask')clearPreview();
    if(!session){const fresh=await(await api('/api/sessions',{method:'POST'})).json();session=fresh.id;localStorage.setItem(sessionKey(),session);}
    // An uncertain retry reuses the same transactionally reserved request ID.
    recoveryRecord={sid:session,body,request_id:previous&&!previous.terminal?previous.request_id:crypto.randomUUID(),terminal:false};saveRecovery();pendingRecovery=true;
    turnController=new AbortController();const started=Date.now();$('wait-elapsed').textContent='已等待 0 秒';
    waitTimer=setInterval(()=>{$('wait-elapsed').textContent=`已等待 ${Math.floor((Date.now()-started)/1000)} 秒`;},1000);controls();
    let r;try{r=await api(`/api/sessions/${session}/turns`,{method:'POST',signal:turnController.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,request_id:recoveryRecord.request_id})});}catch(e){httpRejected=!!e.status&&e.status<500;if(recoveryRecord)recoveryRecord.failure=e.message;throw e;}
    const reader=r.body.getReader(),decoder=new TextDecoder();let buffer='';
    while(true){
      const {value,done:end}=await reader.read();buffer+=decoder.decode(value || new Uint8Array(),{stream:!end});let boundary;
      while((boundary=buffer.indexOf('\n\n'))!==-1){
        const block=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);let event='',data='';
        for(const line of block.split('\n')){if(line.startsWith('event: '))event=line.slice(7);if(line.startsWith('data: '))data+=line.slice(6);}
        if(!data)continue;const payload=JSON.parse(data);
        if(event==='status')note(stages[payload.stage] || '处理中…');
        if(event==='answer'){success=payload.status==='completed';note(success?'已通过检查。你可以继续思考，或再增加一级提示。':payload.answer);}
        if(event==='error')note(payload.message);if(event==='done')done=true;
      }
      if(end)break;
    }
    if(!done)throw Error('连接中断，请刷新本轮状态，不会自动重发。');
    if(success)$('question').value='';
  }catch(e){if(epoch===identityEpoch)note(e.name==='AbortError'?'已停止前端等待。供应商调用可能仍计费，请刷新本轮状态。':e.message);}
  finally{
    clearInterval(waitTimer);waitTimer=null;turnController=null;
    if(epoch===identityEpoch){
      busy=false;
      if(httpRejected&&recoveryRecord){recoveryRecord.terminal=true;saveRecovery();}
      if(success){recoveryRecord=null;saveRecovery();}
      // Fetch history even after success to reconcile publication/cancellation. Never resend here.
      await refreshTurnState();render();await listSessions().catch(()=>{});
    }
  }
}
$('question-form').onsubmit=e=>{e.preventDefault();const force=$('action').value==='ask-l1';send({action:force?'ask':$('action').value,message:$('question').value,force_l1:force,...(($('action').value==='ask'||force)&&selectedCourseId?{course_id:selectedCourseId}:{})});};
$('think').onclick=()=>{$('action').value='followup';$('question').focus();note('保持当前级别。请写下你的思路或下一处疑问，不会自动升级。');};
$('upgrade').onclick=()=>send({action:'upgrade',parent_turn_id:turns.at(-1)?.id});
$('understood').onclick=async()=>{try{await api(`/api/sessions/${session}/close/${turns.at(-1).id}`,{method:'POST'});await openSession(session);note('已记录理解自述，不作为掌握证据。');}catch(e){note(e.message);}};
$('latest-answer').onclick=()=>{$('messages').scrollTop=$('messages').scrollHeight;$('latest-answer').hidden=true;};
$('messages').onscroll=()=>{$('latest-answer').hidden=$('messages').scrollHeight-$('messages').scrollTop-$('messages').clientHeight<70||!turns.length;};
$('recover-turn').onclick=()=>refreshTurnState();
$('retry-turn').onclick=async()=>{const verified=await refreshTurnState();if(!verified||!recoveryRecord||turns.some(t=>t.status==='running'))return;await send(recoveryRecord.body,true);};
$('stop-waiting').onclick=async()=>{const controller=turnController;if(await confirmAction('停止等待？','这会关闭前端接收连接，不保证供应商已经停止处理或免计费。请随后刷新本轮状态。','停止等待')){if(controller===turnController)controller?.abort();}};
$('new-session').onclick=()=>createSession().catch(e=>note(e.message));
$('close-preview').onclick=clearPreview;
async function initialize(){
  try{
    const health=await(await api('/api/health')).json();
    const policy=await(await api('/api/policy')).json();activeMaxLevel=policy.max_level;activeL5=policy.l5;
    $('health').textContent=`${health.pages} 页 · ${health.configured.llm&&health.configured.embedding?'模型已配置':'待配置密钥'}`;
    const active=await(await api('/api/quizzes/active')).json();
    if(active){quizActive=true;clearPreview();controls();window.dispatchEvent(new CustomEvent('quiz-resume',{detail:active}));return;}
    await loadCourseNavigation();
    const list=await listSessions();const saved=localStorage.getItem(sessionKey());
    try{const record=JSON.parse(sessionStorage.getItem(recoveryKey()));if(record&&list.some(x=>x.id===record.sid)&&typeof record.request_id==='string'&&record.body)recoveryRecord=record;}catch{}
    if(list.length){await openSession(recoveryRecord?.sid || (list.some(s=>s.id===saved)?saved:list[0].id));if(recoveryRecord){$('question').value=recoveryRecord.body.message || '';await refreshTurnState();}}
    else{session=null;turns=[];localStorage.removeItem(sessionKey());clearPreview();render();}
  }catch(e){note(e.message);$('health').textContent='连接失败';}
}
function resetIdentity(){
  identityEpoch++;identity=null;session=null;turns=[];quizActive=false;busy=false;pendingRecovery=false;recoveryRecord=null;turnController?.abort();turnController=null;clearInterval(waitTimer);waitTimer=null;quizDrafts.memory.clear();quizDrafts.failed=false;quizBusy=false;
  document.querySelectorAll('.confirm-dialog').forEach(d=>d.close());$('wait-elapsed').textContent='';selectedSessions.clear();sessionSearchVersion++;
  currentQuiz=null;currentView='tutor';viewVersion++;adminQueueRefresh=null;clearTimeout(window.pipelineTimer);
  $('course-navigation').replaceChildren();selectedCourseId=null;reportRanges.report=reportRanges.dashboard='';reportPeriods.report=reportPeriods.dashboard='7';
  $('sessions').replaceChildren();$('workspace').replaceChildren();$('workspace').hidden=true;$('tutor-view').hidden=false;
  $('session-search').value='';$('question').value='';$('action').value='ask';$('session-notice').textContent='';
  if($('session-dialog').open)$('session-dialog').close();
  clearPreview();render();document.body.classList.remove('study-workspace','preview-open');document.body.classList.add('logged-out');$('login-screen').hidden=false;
}
async function enterIdentity(user){
  identityEpoch++;identity=user;$('identity-name').textContent=user.name;
  document.querySelectorAll('[data-view]').forEach(n=>n.classList.toggle('selected',n.dataset.view==='tutor'));
  for(const name of ['admin','dashboard','policy','questions'])document.querySelector(`[data-view=${name}]`).hidden=user.role!=='teacher';
  document.body.classList.remove('logged-out');$('login-screen').hidden=true;
  await initialize();
}
async function loginAs(account){
  $('login-notice').textContent='正在登录…';
  document.querySelectorAll('.login-options button').forEach(b=>b.disabled=true);
  try{const r=await api('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account})});await enterIdentity(await r.json());$('login-notice').textContent='';}
  catch(e){$('login-notice').textContent=e.message;}
  finally{document.querySelectorAll('.login-options button').forEach(b=>b.disabled=false);}
}
$('login-teacher').onclick=()=>loginAs('teacher');$('login-student').onclick=()=>loginAs('student');
$('logout').onclick=async()=>{try{await api('/api/auth/logout',{method:'POST'});}catch(e){note(e.message);return;}resetIdentity();};
let searchTimer;
$('session-search').oninput=()=>{selectedSessions.clear();clearTimeout(searchTimer);searchTimer=setTimeout(()=>listSessions().catch(e=>{$('session-notice').textContent=e.message;}),150);};
let dialogOperation=null;
function sessionDialog(kind,ids,name=''){
  dialogOperation={kind,ids};$('session-dialog-title').textContent=kind==='rename'?'重命名会话':`确认永久删除 ${ids.length} 个会话？`;
  $('session-name').hidden=kind!=='rename';$('session-name').value=name;
  $('session-dialog-notice').textContent=kind==='delete'?'会话及相关问答记录将被删除；测试、掌握和复习记录不受影响。':'';
  $('session-dialog').showModal();if(kind==='rename')$('session-name').focus();
}
$('delete-selected').onclick=()=>sessionDialog('delete',[...selectedSessions]);
$('session-cancel').onclick=()=>{$('session-dialog').close();dialogOperation=null;};
$('session-confirm').onclick=async()=>{
  if(!dialogOperation)return;const {kind,ids}=dialogOperation;$('session-confirm').disabled=true;
  try{
    if(kind==='rename'){const name=$('session-name').value.trim();if(!name)throw Error('请输入会话名称。');await api('/api/sessions/'+ids[0],{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});}
    else{await api('/api/sessions/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});ids.forEach(id=>selectedSessions.delete(id));
      if(ids.includes(session)){session=null;turns=[];clearPreview();render();localStorage.removeItem(sessionKey());const all=await(await api('/api/sessions')).json();if(all.length && !quizActive)await openSession(all[0].id);}
    }
    await listSessions();$('session-dialog').close();dialogOperation=null;$('session-notice').textContent=kind==='rename'?'已重命名。':'已删除选中的会话。';
  }catch(e){$('session-dialog-notice').textContent=e.message;}finally{$('session-confirm').disabled=false;}
};
(async()=>{try{const r=await fetch('/api/auth/me');if(r.ok)await enterIdentity(await r.json());}catch(e){$('login-notice').textContent=e.message;}})();
