'use strict';
// Deliberately small Markdown subset: text nodes only, no raw HTML or links.
function answerContent(text){
  const box=el('div',undefined,'answer'), lines=String(text || '').split('\n');
  function inline(node,value){
    const parts=value.split(/(`[^`\n]+`)/g);
    for(const part of parts)node.append(part.startsWith('`')&&part.endsWith('`')?el('code',part.slice(1,-1)):document.createTextNode(part));
  }
  for(let i=0;i<lines.length;){
    if(/^\s*```/.test(lines[i])){
      const language=lines[i++].replace(/^\s*```/,'').trim().slice(0,30), code=[];
      while(i<lines.length&&!/^\s*```\s*$/.test(lines[i]))code.push(lines[i++]);
      if(i<lines.length)i++;
      const section=el('section',undefined,'code-block'), bar=el('div',undefined,'code-toolbar'), status=el('span');status.setAttribute('role','status');
      const copy=el('button','复制代码');copy.type='button';copy.onclick=async()=>{try{await navigator.clipboard.writeText(code.join('\n'));status.textContent='已复制';}catch{status.textContent='复制失败，请选择代码手动复制。';}};
      const pre=el('pre'), content=el('code',code.join('\n'));pre.append(content);bar.append(el('span',language || '代码'),copy,status);section.append(bar,pre);box.append(section);continue;
    }
    if(!lines[i].trim()){i++;continue;}
    if(/^\s*(?:[-*]|\d+[.)])\s+/.test(lines[i])){
      const ordered=/^\s*\d+[.)]\s+/.test(lines[i]),list=el(ordered?'ol':'ul');
      const pattern=ordered?/^\s*\d+[.)]\s+/:/^\s*[-*]\s+/;
      while(i<lines.length&&pattern.test(lines[i])){const li=el('li');inline(li,lines[i++].replace(pattern,''));list.append(li);}box.append(list);continue;
    }
    const p=el('p');inline(p,lines[i++]);box.append(p);
  }
  return box;
}

function confirmAction(title,message,confirmLabel='确认'){
  return new Promise(resolve=>{
    const dialog=el('dialog',undefined,'confirm-dialog'),h=el('h2',title),p=el('p',message),back=el('button','返回继续'),ok=el('button',confirmLabel,'primary');
    h.id='confirm-action-title';dialog.setAttribute('aria-labelledby',h.id);p.id='confirm-action-description';dialog.setAttribute('aria-describedby',p.id);back.type=ok.type='button';
    let choice=false;back.onclick=()=>dialog.close();ok.onclick=()=>{choice=true;dialog.close();};dialog.onclose=()=>{dialog.remove();resolve(choice);};dialog.append(h,p,back,ok);document.body.append(dialog);dialog.showModal();back.focus();
  });
}

const quizDrafts={
  memory:new Map(),failed:false,
  key(qid,iid){return `sctutor-quiz-draft-v1:${identity.id}:${qid}:${iid}`;},
  read(qid,iid){const key=this.key(qid,iid);if(this.memory.has(key)){if(this.failed)this.warn();return this.memory.get(key);}try{const value=localStorage.getItem(key) || '';this.memory.set(key,value);return value;}catch{this.warn();return ''; }},
  save(qid,iid,value){const key=this.key(qid,iid);this.memory.set(key,value);try{localStorage.setItem(key,value);}catch{this.warn();}},
  remove(qid,iid){const key=this.key(qid,iid);this.memory.delete(key);try{localStorage.removeItem(key);}catch{this.warn();}},
  clear(q){for(const item of q.items)this.remove(q.id,item.id);},
  warn(){this.failed=true;const n=$('quiz-draft-status');if(n)n.textContent='本机草稿存储不可用：请勿刷新或离开；当前输入仍保留，可继续提交。清理失败时本设备可能残留旧草稿。';}
};
let quizBusy=false;
async function quizMutation(fn){
  if(quizBusy)return;
  quizBusy=true;controls();document.querySelectorAll('.quiz-mutation').forEach(n=>n.disabled=true);
  try{await fn();}finally{quizBusy=false;controls();document.querySelectorAll('.quiz-mutation').forEach(n=>n.disabled=false);}
}

async function todayTasks(box){
  const epoch=identityEpoch,version=viewVersion;
  const [tasks,data]=await Promise.all(['/api/reviews','/api/report'].map(async path=>(await api(path)).json()));
  if(epoch!==identityEpoch||version!==viewVersion||!box.isConnected||quizActive)return;
  const section=el('section',undefined,'card today-tasks');section.id='today-tasks';section.append(el('h2','今日学习'),el('p','先完成到期复测，再补齐薄弱前置知识；完成后查看学习报告。','muted'));
  const due=tasks.filter(x=>x.status==='pending'&&x.available&&x.due<=Date.now()/1000);
  if(due.length){section.append(el('p',`有 ${due.length} 项到期复测 · 根据已保存的复测计划`));section.append(actionButton('开始最早到期复测',()=>startQuiz({review_id:[...due].sort((a,b)=>a.due-b.due)[0].id,count:1}),'primary'));}
  else section.append(el('p','暂无可用的到期复测。','muted'));
  const eligible=data.concepts.filter(x=>!x.restricted&&!x.mastered),byId=new Map(eligible.map(x=>[x.id,x]));
  const first=eligible[0], prereq=first?.prerequisites?.map(x=>byId.get(x.id)).find(Boolean),target=prereq || first;
  if(target){section.append(el('h3',`${prereq?'先补前置：':'建议复习：'}${target.name}`),el('p',target.reason || '尚未评估，先复习依据，再通过小测提供证据。','muted'));
    section.append(actionButton('查看复习依据',async()=>{await showView('review');if(epoch!==identityEpoch||quizActive)return;const search=$('report-search');if(search){search.value=target.name;search.dispatchEvent(new Event('input'));const row=[...document.querySelectorAll('.concept-row')].find(n=>n.dataset.conceptId===target.id);row?.querySelector('.concept-title')?.click();}}),actionButton('练习此知识点',()=>startQuiz({concept_id:target.id,count:3})));}
  else section.append(el('p','暂无可用薄弱知识任务；可查看报告，或请教师先审核知识点和题目。','muted'));
  section.append(actionButton('查看学习报告',()=>showView('report')));box.insertBefore(section,box.children[3] || null);
}
