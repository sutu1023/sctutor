'use strict';
let adminQueueRefresh=null;
const reviewNames={concepts:'知识点',aliases:'术语别名',relations:'前置关系',questions:'题库'};
const reviewStatuses={pending:'待审核',approved:'已批准',rejected:'已驳回'};

async function showAdmin(){
  if(identity?.role!=='teacher')throw Error('仅教师可使用管理功能。');
  clearTimeout(window.pipelineTimer);
  const version=viewVersion;
  const [pages,data,questions]=await Promise.all(['/api/admin/pages','/api/admin/knowledge','/api/admin/questions'].map(async path=>(await api(path)).json()));
  if(currentView!=='admin'||version!==viewVersion)return;
  const box=workspace('课件管理','默认只看待审核内容。展开单条核对来源，批准后自动移出待审队列；已批准内容可切换状态查看。');
  const uploads=el('details',undefined,'card upload-panel');uploads.id='course-upload';uploads.append(el('summary','上传课件 / 同步课程目录'),el('p','仅上传PPTX到本地受管目录；上传不收费、不自动入库。同步会调用嵌入API，原页/知识另由处理流程完成。本机网盘文件夹须已配置为课程目录。每次≤10个、单50MiB/总100MiB，同名不同内容拒绝覆盖。','muted'));
  const files=el('input');files.type='file';files.multiple=true;files.accept='.pptx';files.id='course-files';files.setAttribute('aria-label','选择PPTX课件');const uploadResult=el('p',undefined,'notice');uploadResult.id='upload-result';
  uploads.append(files,actionButton('上传课件',async()=>{if(!files.files.length||files.files.length>10)throw Error('请选择1至10个PPTX。');const body=new FormData();for(const f of files.files)body.append('files',f);const result=await(await api('/api/admin/upload',{method:'POST',body})).json();if(version!==viewVersion||!uploads.isConnected)return;uploadResult.textContent=result.files.map(x=>`${x.file} · ${x.status} · ${x.message}`).join('\n');if(result.files.every(x=>x.status!=='error'))files.value='';}),actionButton('同步课程目录',async()=>{const result=await(await api('/api/admin/ingest',{method:'POST'})).json();if(version!==viewVersion||!uploads.isConnected)return;uploadResult.textContent=JSON.stringify(result,null,2);await refreshPipeline();await loadCourseNavigation();}),uploadResult);box.append(uploads);
  const queue=reviewQueue(data,questions,pages,version);box.append(queue.element);adminQueueRefresh=queue.refresh;
  const tools=el('details',undefined,'card admin-tools');tools.id='admin-tools';tools.append(el('summary','课件处理与生成 · 按需展开'));
  const summary=el('section',undefined,'processing-summary');summary.id='processing-summary';tools.append(summary);
  const automation=el('article',undefined,'card');automation.append(el('h2','一键课件处理与审核'),el('p','入库 → 可选原页/图片增强 → 知识抽取 → 独立复核与发布。默认文本模式跳过增强；原页需macOS PowerPoint并显式配置开启，容器不可用。模型处理可能收费，自动审核并非质量保证，异常或疑似作业留人工。','muted'));
  const generate=el('input'),review=el('input');generate.type=review.type='checkbox';generate.id='auto-generate';review.id='auto-review';
  for(const [input,text] of [[generate,'自动生成题目'],[review,'自动审核题目（通过后发布）']]){const line=el('label',undefined,'option-label');line.append(input,document.createTextNode(text));automation.append(line);}
  automation.append(actionButton('一键处理课件',async()=>{await jsonApi('/api/admin/pipeline',{generate_questions:generate.checked,review_questions:review.checked});pageNotice('已启动处理；自动审核不保证绝对正确。');await refreshPipeline();},'primary'));
  const progress=el('div');progress.id='pipeline-progress';automation.append(progress);tools.append(automation);
  const actions=el('div',undefined,'actions');
  actions.append(actionButton('增量入库',async()=>{await api('/api/admin/ingest',{method:'POST'});await refreshPipeline();pageNotice('增量处理完成，详情见处理概况。');}),actionButton('PowerPoint原页 + 少字页图片说明',async()=>{pageNotice('正在处理图片，可能需要macOS自动化授权。');await api('/api/admin/enhance',{method:'POST'});await refreshPipeline();pageNotice('原页与图片处理已完成。');}));tools.append(actions);
  const extraction=el('details',undefined,'card');extraction.append(el('summary','按页抽取知识点（每批最多8页）'));
  const select=el('select');select.multiple=true;select.size=5;select.setAttribute('aria-label','抽取来源页');for(const p of pages)select.append(new Option(`${p.file} · 第${p.page}页 · ${p.title}`,p.id));
  extraction.append(select,actionButton('抽取为待审候选',async()=>{const ids=[...select.selectedOptions].map(x=>x.value);if(!ids.length||ids.length>8)throw Error('请选择1至8页。');await jsonApi('/api/admin/extract',{page_ids:ids});await queue.refresh();queue.select('concepts','pending');pageNotice('候选已加入待审队列，未自动批准。');},'primary'));tools.append(extraction);
  const creator=el('details',undefined,'card');creator.append(el('summary','生成待审题（每次1题）'));
  const conceptSelect=el('select'),typeSelect=el('select');conceptSelect.setAttribute('aria-label','出题知识点');
  queue.updateCreator=()=>{const old=conceptSelect.value;conceptSelect.replaceChildren();for(const x of queue.data.concepts.filter(x=>x.status==='approved'&&x.available&&!x.restricted))conceptSelect.append(new Option(x.name,x.id));if([...conceptSelect.options].some(x=>x.value===old))conceptSelect.value=old;};queue.updateCreator();
  for(const [value,name] of [['choice','单选'],['blank','填空'],['short','简答'],['code','代码']])typeSelect.append(new Option(name,value));
  creator.append(conceptSelect,typeSelect,actionButton('依据课件生成待审题',async()=>{if(!conceptSelect.value)throw Error('请先审核非作业限制知识点。');await jsonApi('/api/admin/questions/generate',{concept_id:conceptSelect.value,type:typeSelect.value});await queue.refresh();queue.select('questions','pending');pageNotice('已生成待审题，审核后才能使用。');}));tools.append(creator);box.append(tools);
  await refreshPipeline();
}

function reviewQueue(data,questions,pages,version){
  const state={kind:'concepts',status:'pending',search:'',page:0},size=6;
  const collection={...data,questions}, pageMap=new Map(pages.map(x=>[x.id,x]));
  const root=el('section',undefined,'review-hub');root.id='review-hub';
  const tabs=el('div',undefined,'queue-tabs');tabs.setAttribute('role','group');tabs.setAttribute('aria-label','审核分类');
  const bar=el('div',undefined,'list-toolbar'), status=el('select'),search=el('input');status.id='review-status';status.setAttribute('aria-label','审核状态');
  for(const [value,name] of [['pending','待审核'],['approved','已批准'],['rejected','已驳回'],['all','全部状态']])status.append(new Option(name,value));
  search.type='search';search.id='review-search';search.placeholder='搜索名称、题干或关联知识点';search.setAttribute('aria-label','搜索审核条目');
  const notice=el('p',undefined,'notice');notice.id='review-notice';notice.setAttribute('role','status');
  const list=el('div',undefined,'review-list');list.id='review-list';
  const pager=el('div',undefined,'list-pager'),previous=el('button','上一页'),next=el('button','下一页'),count=el('span');previous.id='review-prev';next.id='review-next';pager.append(previous,count,next);
  const validView=()=>root.isConnected&&currentView==='admin'&&identity?.role==='teacher'&&version===viewVersion;
  const title=(kind,x)=>kind==='questions'?x.data.prompt:kind==='relations'?`${collection.concepts.find(c=>c.id===x.prerequisite)?.name || '失效知识点'} → ${collection.concepts.find(c=>c.id===x.target)?.name || '失效知识点'}`:x.name;
  const pendingCount=kind=>collection[kind].filter(x=>x.status==='pending').length;
  const tabButtons={};for(const kind of Object.keys(reviewNames)){const b=el('button');b.dataset.reviewKind=kind;b.onclick=()=>{state.kind=kind;state.page=0;renderList();};tabButtons[kind]=b;tabs.append(b);}
  function row(kind,x){
    const card=el('article',undefined,'card review-row');card.dataset.key=kind+':'+x.id;card.dataset.itemId=x.id;
    const head=el('div',undefined,'review-row-head'),heading=el('h3',title(kind,x));heading.title=title(kind,x);
    const badge=el('span',reviewStatuses[x.status] || x.status,'badge');head.append(heading,badge);card.append(head);
    const meta=kind==='questions'?`${x.data.type} · v${x.version} · ${x.available?'来源现行':'来源不可用'}`:x.available===false?'来源已失效':kind==='aliases'?`对应：${collection.concepts.find(c=>c.id===x.concept_id)?.name || x.canonical || '未知'}`:`${x.pages?.length || 0} 个依据页${x.restricted?' · 作业限制':''}`;
    card.append(el('p',meta,'muted'));
    const details=el('details',undefined,'review-detail');details.append(el('summary','查看详情 / 修改'));
    let nameInput=null,restricted=null;
    if(kind!=='questions'&&kind!=='relations'){nameInput=el('input');nameInput.value=x.name;nameInput.maxLength=120;nameInput.setAttribute('aria-label','审核名称');details.append(nameInput);}
    if(kind==='concepts'){const label=el('label',undefined,'option-label');restricted=el('input');restricted.type='checkbox';restricted.checked=x.restricted;label.append(restricted,document.createTextNode('作业限制 / 不用于小测'));details.append(label);}
    const source=el('details',undefined,'source-detail');source.append(el('summary','核对来源'));
    source.ontoggle=()=>{if(!source.open||source.dataset.loaded)return;source.dataset.loaded='true';for(const pid of kind==='questions'?x.data.page_ids:x.pages || []){const p=pageMap.get(pid);source.append(el('p',p?`${p.file} · 第${p.page}页`:'来源失效'),el('pre',p?[p.text,p.notes,p.caption].filter(Boolean).join('\n'):'不可用'));}};
    if(kind==='aliases'){const concept=collection.concepts.find(c=>c.id===x.concept_id);source.ontoggle=()=>{if(!source.open||source.dataset.loaded)return;source.dataset.loaded='true';for(const pid of concept?.pages || []){const p=pageMap.get(pid);source.append(el('p',p?`${p.file} · 第${p.page}页`:'来源失效'),el('pre',p?[p.text,p.notes,p.caption].filter(Boolean).join('\n'):'不可用'));}};}
    if(kind==='questions')details.append(el('p',x.data.prompt,'prewrap'),el('pre',`参考答案：${x.data.answer}\n评分：${x.data.rubric}\n提示：${x.data.hint}`));
    details.append(source);card.append(details);
    const message=el('p',undefined,'notice');message.setAttribute('role','status');
    const buttons=el('div',undefined,'row-actions');
    function operation(label,fn,cls){const b=el('button',label,cls);b.onclick=async()=>{card.querySelectorAll('button').forEach(n=>n.disabled=true);message.textContent='正在保存…';try{await fn();}catch(e){if(validView())message.textContent=e.message;}finally{card.querySelectorAll('button').forEach(n=>n.disabled=false);}};return b;}
    async function save(newStatus){
      const body={status:newStatus};if(nameInput)body.name=nameInput.value;if(restricted)body.restricted=restricted.checked;
      const path=kind==='questions'?`/api/admin/questions/${x.id}/status`:`/api/admin/${kind}/${x.id}/review`;
      const result=await jsonApi(path,kind==='questions'?{status:newStatus}:body);
      if(!validView())return;
      Object.assign(collection[kind].find(item=>item.id===x.id) || x,body,{status:result.status});
      // Only this item is replaced/removed. Other open rows and drafts stay mounted.
      cache.delete(card.dataset.key);notice.textContent=`${reviewNames[kind]}已${newStatus==='approved'?'批准 / 保存':newStatus==='rejected'?'驳回':'退回待审'}${state.status==='pending'&&newStatus!=='pending'?'，已移出待审队列':''}。可切换状态查看。`;
      renderList();queue.updateCreator?.();
    }
    const choices=kind==='questions'?[['确认','approved'],['退回待审','pending'],['驳回','rejected']]:[['确认','approved'],['修改后待审','pending'],['驳回','rejected']];
    for(const [label,value] of choices)buttons.append(operation(label,()=>save(value),value==='approved'?'primary':undefined));
    if(kind==='questions'){
      const editorDetails=el('details'),editor=questionEditor(x.data,collection.concepts,pages);editorDetails.append(el('summary','修改题目（新版本重新审核）'),editor.element,operation('保存为新待审版本',async()=>{const raw=editor.read(),result=await jsonApi(`/api/admin/questions/${x.id}`,raw,'PUT');if(!validView())return;x.status='rejected';collection.questions.push({id:result.id,version:result.version,status:result.status,data:raw,available:true});cache.delete(card.dataset.key);notice.textContent='新版本已加入待审，旧版本已停用。';renderList();}));details.append(editorDetails);
    }
    card.append(buttons,message);return card;
  }
  const cache=new Map();
  function renderList(){
    const scroll=$('workspace').scrollTop;
    for(const kind of Object.keys(reviewNames)){tabButtons[kind].textContent=`${reviewNames[kind]} · ${pendingCount(kind)} 待审`;tabButtons[kind].classList.toggle('selected',state.kind===kind);tabButtons[kind].setAttribute('aria-pressed',String(state.kind===kind));}
    const term=state.search.toLocaleLowerCase();const rows=collection[state.kind].filter(x=>(state.status==='all'||x.status===state.status)&&(!term||(title(state.kind,x)+' '+(x.canonical || '')+' '+collection.concepts.filter(c=>c.id===x.concept_id || x.data?.concept_ids?.includes(c.id)).map(c=>c.name).join(' ')).toLocaleLowerCase().includes(term)));
    const total=Math.max(1,Math.ceil(rows.length/size));state.page=Math.min(state.page,total-1);
    const visible=rows.slice(state.page*size,(state.page+1)*size), desired=[];
    for(const x of visible){const key=state.kind+':'+x.id;if(!cache.has(key))cache.set(key,row(state.kind,x));desired.push(cache.get(key));}
    if(!rows.length){const empty=el('p',state.search?'没有匹配条目，试试其他关键词或状态。':'当前分类没有'+(reviewStatuses[state.status] || '可显示')+'条目。','empty-state');desired.push(empty);}
    const wanted=new Set(desired);for(const child of [...list.children])if(!wanted.has(child))child.remove();
    desired.forEach((node,i)=>{if(list.children[i]!==node)list.insertBefore(node,list.children[i] || null);});
    count.textContent=`共 ${rows.length} 项 · 第 ${state.page+1} / ${total} 页 · 每页 ${size} 项`;previous.disabled=state.page===0;next.disabled=state.page>=total-1;
    $('workspace').scrollTop=scroll;
  }
  status.onchange=()=>{state.status=status.value;state.page=0;renderList();};search.oninput=()=>{state.search=search.value.trim();state.page=0;renderList();};previous.onclick=()=>{state.page--;renderList();};next.onclick=()=>{state.page++;renderList();};
  const queue={element:root,data:collection,updateCreator:null,select(kind,value){state.kind=kind;state.status=value;status.value=value;state.search='';search.value='';state.page=0;renderList();},async refresh(){
    const [fresh,qs]=await Promise.all(['/api/admin/knowledge','/api/admin/questions'].map(async path=>(await api(path)).json()));if(!validView())return;
    for(const [kind,rows] of Object.entries({...fresh,questions:qs})){const before=new Map(collection[kind].map(x=>[x.id,x]));collection[kind]=rows.map(x=>{const old=before.get(x.id);if(old&&JSON.stringify(old)===JSON.stringify(x))return old;cache.delete(kind+':'+x.id);return x;});}renderList();queue.updateCreator?.();
  }};
  const refresh=actionButton('刷新队列',()=>queue.refresh());bar.append(search,status,refresh);root.append(el('h2','审核工作台'),tabs,bar,notice,list,pager);renderList();return queue;
}

function renderProcessingSummary(data){
  const box=$('processing-summary');if(!box||!data)return;box.replaceChildren(el('h2','课件与审核概况'),metricCards([['已入库课件',data.files.length,'配置本地目录'],['真实页数',data.pages,'现行版本'],['少字含图页',data.caption_candidates,'正文不足30字'],['待审知识项',Object.values(data.pending).reduce((a,b)=>a+b,0),'知识点 / 别名 / 关系']]));
  const latest=data.latest_ingest,details=el('details');details.append(el('summary',latest?'最近增量处理 · '+new Date(latest.created*1000).toLocaleString():'现行文件（暂无可追溯增量记录）'));
  const labels={added:'新增',modified:'更新',unchanged:'未变化',deleted:'已删除',indexed:'已入库',error:'失败'};
  for(const x of latest?.files || data.files){const row=el('div',undefined,'file-status');row.append(el('span',x.file),el('span',`${labels[x.change || x.status] || '现行'} · ${x.pages ?? '未知'}页`));if(x.message)row.append(el('p',x.message,'notice'));details.append(row);}box.append(details,el('p',`待审：知识点 ${data.pending.concepts} / 别名 ${data.pending.aliases} / 关系 ${data.pending.relations}；旧批次缺失值为未知。`,'muted'));
}
async function refreshPipeline(){
  const status=await(await api('/api/admin/processing')).json(),box=$('pipeline-progress');
  if(box&&currentView==='admin'&&identity?.role==='teacher'){
    renderProcessingSummary(status.summary);
    const wasRunning=box.dataset.running==='true';box.dataset.running=String(status.running);
    const open=new Set([...box.querySelectorAll('details[open]')].map(x=>x.dataset.job));
    box.replaceChildren(el('p',status.running?'处理中，请勿重复启动。':'当前空闲','muted'));
    for(const job of status.jobs){const card=el('details');card.dataset.job=job.id;card.open=open.has(job.id);card.append(el('summary',`${new Date(job.created*1000).toLocaleString()} · ${job.status} · ${job.stage}`),el('pre',JSON.stringify({options:job.options,steps:job.detail},null,2)));box.append(card);}
    if(status.reviews.length){const log=el('details');log.append(el('summary',`自动审核记录 · ${status.reviews.length} 条（模型复核，非人工保证）`));for(const r of status.reviews)log.append(el('p',`${r.kind} · ${r.ident.slice(0,8)} · ${r.outcome}`),el('pre',r.verdict));box.append(log);}
    const completed=status.jobs.find(j=>j.status!=='running');
    if(!status.running&&completed&&(wasRunning||box.dataset.completedJob!==completed.id)){box.dataset.completedJob=completed.id;await adminQueueRefresh?.();}
    clearTimeout(window.pipelineTimer);if(status.running)window.pipelineTimer=setTimeout(()=>refreshPipeline().catch(e=>pageNotice(e.message)),1500);
  }
  return status;
}
