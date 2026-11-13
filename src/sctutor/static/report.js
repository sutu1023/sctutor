'use strict';
function renderLearningReport(data){
  const box=workspace(document.querySelector('[data-view=review].selected')?'复习':'学习报告',data.notice);box.classList.add('report-workspace');box.append(dateControls('report',showReport));
  const state={tab:document.querySelector('[data-view=review].selected')?'review':'overview',filter:'all',search:'',pages:{overview:0,review:0},selected:data.concepts[0]?.id},byId=new Map(data.concepts.map(x=>[x.id,x]));
  const mastery=x=>x.probability===null?'未评估':`掌握估计 ${Math.round(x.probability*100)}%`;
  const band=x=>x.probability===null?'unknown':x.mastered?'mastered':x.probability<.5?'weak':'developing';
  if(data.summary){const s=data.summary;box.append(el('p',`${new Date(s.start*1000).toLocaleDateString()} — ${new Date(s.end*1000).toLocaleDateString()} · 当前账号所选期间（掌握为当前快照）`,'muted'),metricCards([['已达掌握门槛',`${s.mastered} / ${s.total}`,'概率与独立证据双门槛'],['期间涉及知识点',s.involved.length,'包括期间追问'],['首次作答正确率',s.accuracy===null?'暂无证据':`${Math.round(s.accuracy*100)}%`,`${s.first_correct} / ${s.attempted} 次已交卷题目`],['建议复习',s.review_concepts,'未评估或仍需练习']]));}
  const tabs=el('div',undefined,'queue-tabs');tabs.setAttribute('role','group');tabs.setAttribute('aria-label','报告视图');
  const overviewTab=el('button','知识点掌握情况'),reviewTab=el('button','建议复习');overviewTab.id='report-overview-tab';reviewTab.id='report-review-tab';tabs.append(overviewTab,reviewTab);
  const toolbar=el('div',undefined,'list-toolbar'),search=el('input'),filter=el('select');search.type='search';search.id='report-search';search.placeholder='搜索知识点';search.setAttribute('aria-label','搜索知识点');filter.id='report-filter';filter.setAttribute('aria-label','掌握程度筛选');
  for(const [value,label] of [['all','全部知识点'],['unknown','未评估'],['weak','薄弱（低于50%）'],['developing','进展中'],['mastered','已达掌握门槛'],['asked','期间提问']])filter.append(new Option(label,value));toolbar.append(search,filter);
  const distribution=el('div',undefined,'mastery-distribution');distribution.setAttribute('aria-label','掌握程度分布');
  const bands=[['unknown','未评估'],['weak','薄弱'],['developing','进展中'],['mastered','已达门槛']];
  for(const [value,label] of bands){const count=data.concepts.filter(x=>band(x)===value).length,b=el('button',`${label} · ${count}`,`mastery-chip ${value}`);b.onclick=()=>{filter.value=value;filter.dispatchEvent(new Event('change'));};distribution.append(b);}
  const content=el('section');content.id='report-content';box.append(tabs,toolbar,distribution,content);
  const dialog=el('dialog',undefined,'concept-dialog');dialog.id='concept-dialog';box.append(dialog);
  function studyButton(x){const b=actionButton('开始此知识点小测',async()=>{try{await startQuiz({count:3,concept_id:x.id});}catch(e){const n=dialog.querySelector('.notice');if(dialog.open&&n)n.textContent=e.message;else pageNotice(e.message);}},'primary');b.disabled=x.restricted;b.title=x.restricted?'作业限制：不用于小测':'';return b;}
  function openConcept(x){
    if(!dialog.isConnected)return;
    state.selected=x.id;dialog.replaceChildren();const close=el('button','关闭详情');close.className='dialog-close';close.onclick=()=>dialog.close();
    const title=el('h2',x.name);title.id='concept-'+x.id;title.tabIndex=-1;dialog.setAttribute('aria-labelledby',title.id);
    dialog.append(close,title,el('p',`${mastery(x)} · ${x.observations} 次证据${x.asked_this_week?' · 期间提问':''}`,'muted'),el('p',x.mastered?'已达到自适应起点门槛':'尚未达到起点门槛；概率并非可靠教育诊断。','muted'));
    const progress=el('progress');progress.max=1;progress.value=x.probability || 0;progress.setAttribute('aria-label',x.probability===null?'尚无掌握证据':'掌握概率估计');dialog.append(progress);
    if(x.last_practiced)dialog.append(el('p','最近练习：'+new Date(x.last_practiced*1000).toLocaleString(),'muted'));
    dialog.append(el('p',x.reason,'prewrap'));const prereqs=data.relations.filter(r=>r.target===x.id).map(r=>byId.get(r.prerequisite)).filter(Boolean);
    if(prereqs.length){dialog.append(el('h3','前置知识点'));const list=el('div',undefined,'refs');for(const p of prereqs){const b=el('button',p.name+' · '+mastery(p));b.onclick=()=>openConcept(p);list.append(b);}dialog.append(list);}
    const refs=el('div',undefined,'refs');x.pages.forEach((pid,i)=>refs.append(actionButton(`复习依据 ${i+1}`,async()=>{await previewSource(`/api/knowledge/${x.id}/pages/${pid}`,x.name);dialog.close();})));dialog.append(refs);
    if(x.restricted)dialog.append(el('p','作业限制：不用于小测','notice'));
    const error=el('p',undefined,'notice');error.setAttribute('role','status');dialog.append(error,studyButton(x));
    if(!dialog.open)dialog.showModal();title.focus();
  }
  function filtered(){const term=state.search.toLocaleLowerCase();return data.concepts.filter(x=>(!term||x.name.toLocaleLowerCase().includes(term))&&(state.filter==='all'||state.filter==='asked'&&x.asked_this_week||band(x)===state.filter));}
  function pager(total,size,page,change){const max=Math.max(1,Math.ceil(total/size)),nav=el('div',undefined,'list-pager'),before=el('button','上一页'),after=el('button','下一页');before.disabled=page===0;after.disabled=page>=max-1;before.onclick=()=>change(page-1);after.onclick=()=>change(page+1);nav.append(before,el('span',`共 ${total} 项 · 第 ${page+1} / ${max} 页`),after);return nav;}
  function focusGraph(selected){
    const incoming=data.relations.filter(r=>r.target===selected.id),outgoing=data.relations.filter(r=>r.prerequisite===selected.id);
    const ids=new Set([selected.id,...incoming.slice(0,3).map(r=>r.prerequisite),...outgoing.slice(0,3).map(r=>r.target)]);
    const local={concepts:data.concepts.filter(x=>ids.has(x.id)),relations:[...incoming,...outgoing].filter(r=>ids.has(r.prerequisite)&&ids.has(r.target))};
    const panel=el('section',undefined,'card focus-graph');panel.append(el('h2','知识点掌握情况'),el('h3',selected.name),el('p',`${mastery(selected)} · ${selected.observations} 次证据${selected.asked_this_week?' · 期间提问':''}`,'muted'));
    panel.append(knowledgeGraph(local,openConcept));
    panel.append(el('p',`聚焦 ${local.concepts.length} 个节点 · 箭头：前置 → 后续 · 描边：期间提问`,'muted'));
    const legend=el('details');legend.append(el('summary','图例与掌握说明'),el('p','灰：未评估，红：低于50%，黄：50–80%，蓝：≥80%。自适应起点仍需至少两次独立首次答对，概率不是可靠教育诊断。','muted'));panel.append(legend);
    if(incoming.length>3||outgoing.length>3)panel.append(el('p','每侧先显示3个关联点，完整关系可在下方列表或知识点详情中查看。','muted'));
    const relations=el('details',undefined,'focus-relations');relations.open=window.matchMedia('(max-width:650px)').matches;relations.append(el('summary',`前置 ${incoming.length} 项 / 后续 ${outgoing.length} 项 · 关系清单`));
    for(const [label,edges,field] of [['前置',incoming,'prerequisite'],['后续',outgoing,'target']]){const line=el('p',`${label}：${edges.length} 项`);relations.append(line);for(const r of edges.slice(0,3)){const x=byId.get(r[field]);if(x){const b=el('button',x.name);b.onclick=()=>openConcept(x);relations.append(b);}}}
    panel.append(relations);const detail=el('button','查看知识点详情 / 复习依据');detail.onclick=()=>openConcept(selected);panel.append(detail);return panel;
  }
  function allRelations(){const details=el('details',undefined,'all-relations');details.append(el('summary',`全部已审核前置关系 · ${data.relations.length} 项`));const list=el('div');let page=0;const draw=()=>{list.replaceChildren();for(const r of data.relations.slice(page*6,page*6+6)){const line=el('p',`${byId.get(r.prerequisite)?.name || '未知'} → ${byId.get(r.target)?.name || '未知'}`,'graph-edge');list.append(line);}if(!data.relations.length)list.append(el('p','尚无已审核关系。','muted'));list.append(pager(data.relations.length,6,page,next=>{page=next;draw();}));};details.append(list);details.ontoggle=()=>{if(details.open)draw();};return details;}
  function render(){
    const scroll=box.scrollTop,rows=filtered(),size=state.tab==='overview'?6:5,total=Math.max(1,Math.ceil(rows.length/size));state.pages[state.tab]=Math.min(state.pages[state.tab],total-1);
    overviewTab.classList.toggle('selected',state.tab==='overview');reviewTab.classList.toggle('selected',state.tab==='review');overviewTab.setAttribute('aria-pressed',String(state.tab==='overview'));reviewTab.setAttribute('aria-pressed',String(state.tab==='review'));
    content.replaceChildren();
    if(!rows.length){content.append(el('p',data.concepts.length?'没有匹配知识点，请调整关键词或筛选条件。':'尚无审核通过的知识点，请教师先处理并审核课程内容。','empty-state'));return;}
    if(!rows.some(x=>x.id===state.selected))state.selected=rows[0].id;
    const layout=el('div',undefined,state.tab==='overview'?'compact-report-layout':'review-plan-layout'),list=el('section',undefined,'concept-list');list.id=state.tab==='overview'?'concept-list':'review-plan-list';list.append(el('h2',state.tab==='overview'?'知识点 · 点击聚焦':'建议复习'));
    for(const x of rows.slice(state.pages[state.tab]*size,(state.pages[state.tab]+1)*size)){
      const card=el('article',undefined,'card concept-row '+(state.selected===x.id?'selected':''));card.dataset.conceptId=x.id;
      const title=el('button',x.name,'concept-title');title.title=x.name;title.onclick=()=>{state.selected=x.id;if(state.tab==='overview')render();else openConcept(x);};
      card.append(title,el('span',mastery(x)+(x.asked_this_week?' · 期间提问':''),'muted'));
      if(state.tab==='review'){const reason=el('p',x.reason,'compact-reason');reason.title=x.reason;card.append(reason);const actions=el('div',undefined,'row-actions'),details=el('button','查看详情 / 复习依据');details.onclick=()=>openConcept(x);actions.append(details,studyButton(x));card.append(actions);}
      list.append(card);
    }
    list.append(pager(rows.length,size,state.pages[state.tab],page=>{state.pages[state.tab]=page;render();}));layout.append(list);
    if(state.tab==='overview')layout.append(focusGraph(byId.get(state.selected)));
    content.append(layout);if(state.tab==='overview')content.append(allRelations());box.scrollTop=scroll;
  }
  overviewTab.onclick=()=>{state.tab='overview';render();};reviewTab.onclick=()=>{state.tab='review';render();};search.oninput=()=>{state.search=search.value.trim();state.pages={overview:0,review:0};render();};filter.onchange=()=>{state.filter=filter.value;state.pages={overview:0,review:0};render();};render();
}
