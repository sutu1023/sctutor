'use strict';
let currentQuiz=null, currentView='tutor', viewVersion=0;
function pageNotice(message){let n=$('workspace-notice');if(!n){n=el('p',undefined,'notice');n.id='workspace-notice';$('workspace').prepend(n);}n.textContent=message;}
function actionButton(text, fn, cls){const b=el('button',text,cls);b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){pageNotice(e.message);}finally{b.disabled=false;}};return b;}
async function jsonApi(path, body, method='POST'){return (await api(path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();}
function workspace(title, subtitle){document.body.classList.add('study-workspace');$('workspace').className='';$('workspace').replaceChildren(el('h1',title),el('p',subtitle,'muted'));const n=el('p',undefined,'notice');n.id='workspace-notice';$('workspace').append(n);return $('workspace');}
function setQuiz(value){currentQuiz=value;quizActive=!!value && value.status==='active';controls();clearPreview();if(quizActive)$('course-navigation').replaceChildren();else loadCourseNavigation().catch(()=>{});}
async function showView(name){
  if(busy || pendingRecovery || quizBusy || !identity)return;
  if(['admin','dashboard','policy','questions'].includes(name) && identity.role!=='teacher')throw Error('仅教师可使用管理功能。');
  const entry=name;name=({review:'report',policy:'dashboard',questions:'admin'})[name]||name;
  const version=++viewVersion;currentView=name;
  clearTimeout(window.pipelineTimer);
  $('tutor-view').hidden=name!=='tutor';$('workspace').hidden=name==='tutor';clearPreview();
  if(name!=='tutor')$('workspace').replaceChildren(el('p','正在加载…','muted'));
  const active=await(await api('/api/quizzes/active')).json();
  if(version!==viewVersion)return;
  setQuiz(active);
  if(quizActive && !['admin','dashboard'].includes(name))name='quiz';
  currentView=name;document.body.classList.toggle('study-workspace',name!=='tutor');$('tutor-view').hidden=name!=='tutor';$('workspace').hidden=name==='tutor';clearPreview();
  document.querySelectorAll('[data-view]').forEach(n=>n.classList.toggle('selected',n.dataset.view===(quizActive&&name==='quiz'?'quiz':entry)));
  if(name==='report')await showReport();
  if(name==='quiz')await showQuizHome();
  if(name==='admin')await showAdmin();
  if(name==='dashboard')await showDashboard();
  if(version!==viewVersion)return;
  if(entry==='review'&&name==='report'){$('workspace').querySelector('h1').textContent='复习';$('report-review-tab')?.click();await todayTasks($('workspace'));}
  if(entry==='policy'&&name==='dashboard'){$('workspace').querySelector('h1').textContent='辅导策略';document.querySelector('.policy-panel')?.scrollIntoView({block:'start'});$('policy-max-level')?.focus();}
  if(entry==='questions'&&name==='admin'){$('workspace').querySelector('h1').textContent='题目审核';document.querySelector('[data-review-kind=questions]')?.click();$('course-upload').hidden=true;$('admin-tools').hidden=true;}
  if(name==='tutor'){render();await listSessions();}
}
async function startQuiz(body){const q=await jsonApi('/api/quizzes',body);setQuiz(q);currentView='quiz';$('tutor-view').hidden=true;$('workspace').hidden=false;renderQuiz(q);}
async function previewSource(path, title){return openEvidence(path,title);}
async function showReport(){
  const version=viewVersion,account=identityEpoch,range=reportRanges.report;
  const data=await(await api('/api/report'+range)).json();
  if(currentView!=='report'||version!==viewVersion||account!==identityEpoch||range!==reportRanges.report)return;
  renderLearningReport(data);
}
async function showQuizHome(){
  if(currentQuiz && currentQuiz.status==='active'){renderQuiz(currentQuiz);return;}
  const box=workspace('小测','只使用审核题；首次错误可获得受检概念提示再答一次。作答期间不看课件，完成后核对出处。');
  box.append(actionButton('按薄弱知识点开始小测（3题）',()=>startQuiz({count:3}),'primary'));
  await todayTasks(box);
  if(!box.isConnected)return;
  const tasks=await(await api('/api/reviews')).json();if(!box.isConnected)return;box.append(el('h2','复测安排'));
  if(!tasks.length)box.append(el('p','交卷后会显示1/3/7天复测或一周抽查安排。'));
  for(const r of tasks){const row=el('article',undefined,'card');row.append(el('p',`${new Date(r.due*1000).toLocaleString()} · 第${r.interval}天 · ${r.status==='completed'?'已完成':r.available?'待复测':'题目或来源不可用'}`));if(r.status==='pending' && r.available && r.due<=Date.now()/1000)row.append(actionButton('开始到期复测',()=>startQuiz({review_id:r.id,count:1})));box.append(row);}
}
function renderQuiz(q){
  const priorScroll=$('workspace').scrollTop,focused=document.activeElement?.id;
  currentQuiz=q;const active=q.status==='active', box=workspace('小测 · '+(active?'进行中':q.status==='completed'?'已交卷':'已取消'),'先凭记忆作答。模型批改需人工抽查；学生无法进入教师管理，作答期间禁止通用资料预览。');
  if(q.notice)box.append(el('p',q.notice,'notice'));
  const draftStatus=el('p','未提交内容仅作为本浏览器草稿保存；不是正式作答。同设备请保护自己的演示账号。','muted');draftStatus.id='quiz-draft-status';draftStatus.setAttribute('role','status');box.append(draftStatus);
  const done=q.items.filter(x=>x.done).length;
  box.append(metricCards([['作答进度',`${done} / ${q.items.length}`,'所有题终结后方可交卷'],['考查知识点',[...new Set(q.items.flatMap(x=>x.concepts))].join('、') || '来源不可用','作答期间不显示课件'],['复测规则','1 / 3 / 7 天','首次错误安排复测；首次答对7天抽查']]));
  const progress=el('progress');progress.max=q.items.length || 1;progress.value=done;progress.setAttribute('aria-label','小测完成进度');box.append(progress);
  for(const item of q.items){
    const card=el('article',undefined,'card');card.append(el('h2',`第 ${item.position+1} 题 · ${item.type}`),el('p',item.prompt,'prewrap'),el('p',`考查：${item.concepts.join('、')} · 已答 ${item.attempts} 次`,'muted'));
    if(!item.available){card.append(el('p','题目或来源已变化，请取消后重新开始。','notice'));box.append(card);continue;}
    if(item.done){quizDrafts.remove(q.id,item.id);card.append(el('p',item.first_correct?'首次答对':'首次答错，将安排复测'),el('h3','参考解答'),el('p',item.answer,'prewrap'),el('p',item.rubric,'prewrap'),el('p',item.feedback,'muted'));const refs=el('div',undefined,'refs');for(const c of item.citations)refs.append(actionButton(`${c.file} · 第${c.page}页`,()=>previewSource(`/api/quiz-items/${item.id}/citations/${c.id}`,`${c.file} · 第${c.page}页`)));card.append(refs);}
    else if(active){
      if(item.hint)card.append(el('p',item.hint,'notice'));
      let input;
      if(item.type==='choice'){input=el('select');input.append(new Option('请选择…',''));for(const opt of item.options)input.append(new Option(opt,opt));}
      else{input=el('textarea');input.maxLength=6000;input.rows=3;input.placeholder='先凭记忆写下你的答案';}
      input.id='quiz-answer-'+item.id;input.value=quizDrafts.read(q.id,item.id);input.oninput=()=>quizDrafts.save(q.id,item.id,input.value);
      input.setAttribute('aria-label',`第${item.position+1}题答案`);card.append(input);
      const submit=actionButton(item.attempts?'再试一次':'提交答案',()=>quizMutation(async()=>{if(!input.value.trim())throw Error('请输入答案。');const epoch=identityEpoch;quizDrafts.save(q.id,item.id,input.value);const next=await jsonApi(`/api/quiz-items/${item.id}/answers`,{answer:input.value,attempt:item.attempts+1,request_id:crypto.randomUUID()});if(epoch!==identityEpoch)return;currentQuiz=next;if(currentView==='quiz')renderQuiz(next);}), 'primary quiz-mutation');submit.id='quiz-submit-'+item.id;card.append(submit);
    }
    box.append(card);
  }
  if(active){const buttons=el('div',undefined,'actions');if(q.items.every(x=>x.done))buttons.append(actionButton('交卷并更新掌握状态',()=>quizMutation(async()=>{const epoch=identityEpoch,result=await(await api(`/api/quizzes/${q.id}/finish`,{method:'POST'})).json();if(epoch!==identityEpoch)return;quizDrafts.clear(q);setQuiz(result);renderQuiz(result);pageNotice('交卷已保存，掌握状态和复测计划已更新。');}),'primary quiz-mutation'));buttons.append(actionButton('取消小测（不更新掌握状态）',()=>quizMutation(async()=>{const epoch=identityEpoch;if(!await confirmAction('确认取消本次小测？','取消不会更新掌握状态。已提交的尝试不能继续本测验，未提交的本次草稿将清理；返回继续可保留作答。','确认取消小测'))return;if(epoch!==identityEpoch)return;const result=await(await api(`/api/quizzes/${q.id}/cancel`,{method:'POST'})).json();if(epoch!==identityEpoch)return;quizDrafts.clear(q);setQuiz(result);await showQuizHome();}),'quiz-mutation'));box.append(buttons);}
  else {quizDrafts.clear(q);box.append(actionButton('查看学习报告',()=>showView('report')),actionButton('查看复测安排',()=>showView('quiz')));if(q.status==='completed'){const summary=el('section',undefined,'card');summary.id='quiz-review-summary';summary.append(el('h2','本次交卷复测安排'),el('p','正在读取已保存计划…','muted'));box.append(summary);api('/api/reviews').then(r=>r.json()).then(tasks=>{if(!summary.isConnected)return;summary.replaceChildren(el('h2','本次交卷复测安排'));const ids=new Set(q.items.map(x=>x.id));const own=tasks.filter(x=>ids.has(x.origin_item));for(const t of own)summary.append(el('p',`${new Date(t.due*1000).toLocaleString()} · 第${t.interval}天 · ${t.available?'待复测':'题目或来源不可用'}`));if(!own.length)summary.append(el('p','本次为复测或无新计划；仅结清当前任务，不递归生成复测。'));}).catch(e=>{summary.replaceChildren(el('p',e.message,'notice'));});}}
  if(focused)$(focused)?.focus({preventScroll:true});
  box.scrollTop=priorScroll;
  if(quizBusy)box.querySelectorAll('.quiz-mutation').forEach(n=>n.disabled=true);
}
function metricCards(values){const box=el('div',undefined,'metric-grid');for(const [title,value,help] of values){const card=el('article',undefined,'metric');card.append(el('p',title,'muted'),el('strong',String(value)),el('small',help,'muted'));box.append(card);}return box;}
function knowledgeGraph(data,onSelect){
  const scroll=el('div',undefined,'graph-scroll'), ns='http://www.w3.org/2000/svg';
  const make=(tag,attrs={})=>{const x=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))x.setAttribute(k,String(v));return x;};
  const svg=make('svg',{role:'group','aria-label':'已审核知识点前置关系图'}), ids=new Set(data.concepts.map(x=>x.id)), depths=Object.fromEntries([...ids].map(id=>[id,0]));
  for(let i=0;i<ids.size;i++){let changed=false;for(const r of data.relations){if(ids.has(r.prerequisite)&&ids.has(r.target)&&depths[r.target]<depths[r.prerequisite]+1){depths[r.target]=Math.min(ids.size,depths[r.prerequisite]+1);changed=true;}}if(!changed)break;}
  const rows={},positions={};for(const x of data.concepts){const d=depths[x.id], col=rows[d] || 0;rows[d]=col+1;positions[x.id]={x:col*205+15,y:d*120+20};}
  const width=Math.max(410,...Object.values(rows).map(n=>n*205+20)),height=Math.max(160,...Object.values(depths).map(d=>(d+1)*120+20));svg.setAttribute('viewBox',`0 0 ${width} ${height}`);svg.setAttribute('width',width);svg.setAttribute('height',height);
  const defs=make('defs'), marker=make('marker',{id:'graph-arrow',markerWidth:8,markerHeight:8,refX:7,refY:4,orient:'auto'});marker.append(make('path',{d:'M0,0 L8,4 L0,8',fill:'#8996a6'}));defs.append(marker);svg.append(defs);
  for(const r of data.relations){const a=positions[r.prerequisite],b=positions[r.target];if(a&&b)svg.append(make('path',{d:`M${a.x+90},${a.y+72} C${a.x+90},${a.y+95} ${b.x+90},${b.y-20} ${b.x+90},${b.y}`,class:'graph-link','marker-end':'url(#graph-arrow)'}));}
  for(const x of data.concepts){const p=positions[x.id],g=make('g',{tabindex:0,role:'button','aria-label':x.name+'，'+(x.probability===null?'未评估':Math.round(x.probability*100)+'%'),class:'graph-node '+(x.probability===null?'unknown':x.probability<.5?'weak':x.probability<.8?'medium':'strong')+(x.asked_this_week?' asked':'')});g.append(make('rect',{x:p.x,y:p.y,width:180,height:72,rx:9}));const name=make('text',{x:p.x+90,y:p.y+23,'text-anchor':'middle'});for(let i=0;i<Math.min(x.name.length,36);i+=18){const line=make('tspan',{x:p.x+90,dy:i?16:0});line.textContent=x.name.slice(i,i+18)+(i===18&&x.name.length>36?'…':'');name.append(line);}const value=make('text',{x:p.x+90,y:p.y+61,'text-anchor':'middle',class:'graph-value'});value.textContent=x.probability===null?'未评估':`${Math.round(x.probability*100)}%`;g.append(name,value);const focus=()=>{if(onSelect){onSelect(x);return;}const card=$('concept-'+x.id);card?.scrollIntoView({behavior:'smooth',block:'nearest'});card?.focus();};g.onclick=focus;g.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();focus();}};svg.append(g);}
  scroll.append(svg);return scroll;
}
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>showView(b.dataset.view).catch(e=>{if(!$('workspace').hidden)pageNotice(e.message);else note(e.message);}));
window.addEventListener('quiz-resume',e=>{setQuiz(e.detail);currentView='quiz';document.querySelectorAll('[data-view]').forEach(n=>n.classList.toggle('selected',n.dataset.view==='quiz'));$('tutor-view').hidden=true;$('workspace').hidden=false;renderQuiz(e.detail);});
