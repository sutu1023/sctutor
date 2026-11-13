'use strict';
async function showDashboard(){
  if(identity?.role!=='teacher')throw Error('仅教师可查看班级概览。');
  const version=viewVersion,account=identityEpoch,range=reportRanges.dashboard;
  const [data,policy,knowledge]=await Promise.all(['/api/admin/overview'+range,'/api/admin/policy','/api/admin/knowledge'].map(async path=>(await api(path)).json()));
  if(currentView!=='dashboard'||version!==viewVersion||account!==identityEpoch||range!==reportRanges.dashboard)return;
  const box=workspace(document.querySelector('[data-view=policy].selected')?'辅导策略':'班级概览',data.notice);
  box.append(dateControls('dashboard',showDashboard));
  box.append(el('p',`${new Date(data.start*1000).toLocaleString()} — ${new Date(data.end*1000).toLocaleString()}`,'muted'));
  const pct=x=>x===null?'暂无数据':`${(x*100).toFixed(1)}%`;
  box.append(metricCards([['提问量',data.questions,`未结束 ${data.unfinished} · 无完成回应 ${data.failed_questions}`],['平均结束级别',data.average_end_level===null?'暂无数据':`L${data.average_end_level.toFixed(1)}`,`${data.ended} 个明确结束的问题`],['泄题检查退回率',pct(data.rejection_rate),`${data.rejected} / ${data.checks} 次有效检查 · 调用失败 ${data.check_errors}`],['问到L4的比例',pct(data.l4_rate),`${data.l4_questions} / ${data.questions} 个独立问题`]]));
  const layout=el('div',undefined,'dashboard-layout'), stats=el('section'), form=el('section',undefined,'card policy-panel');layout.append(stats,form);box.append(layout);
  stats.append(el('h2','卡住最多的知识点'),el('p','统计到达L3及以上且尚未明确结束的问题；无知识点关联的旧问题不猜测归因。','muted'));
  if(!data.difficulties.length)stats.append(el('p','目前没有可归因的疑难记录。','empty-state'));
  for(const x of data.difficulties){const card=el('article',undefined,'card');card.append(el('h3',x.name),el('p',`${x.count} 个未解决问题`));for(const p of x.citations)card.append(el('p',`${p.file} · 第${p.page}页`,'muted'));stats.append(card);}
  stats.append(el('p',`${data.unattributed} 个问题暂无审核知识点关联。`,'muted'),el('h2','泄题检查记录'),el('p','仅展示程序类别，不返回原始问题、回答、草稿或自由文本检查原因。','muted'));
  const log=el('div',undefined,'check-log');log.id='teaching-check-log';if(!data.recent_checks.length)log.append(el('p','暂无检查记录。','empty-state'));
  for(const x of data.recent_checks){const row=el('div',undefined,'check-row');row.append(el('time',new Date(x.created*1000).toLocaleString()),el('span',`L${x.level}`,'badge'),el('span',x.category));log.append(row);}stats.append(log);
  form.append(el('h2','辅导策略'),el('p','仅限本机演示。策略即时影响新请求、运行中结果、历史和预览。L5须完成本问题L1–L4且非作业/禁答；Tavily只在课件不足时检索，必要问题外发且可能收费。','muted'));
  const field=(title,input)=>{const label=el('label',title,'field-label');label.append(input);form.append(label);return input;};
  const levels=(n,current)=>{const s=el('select');for(let i=1;i<=n;i++)s.append(new Option(`L${i}`,i));s.value=current;return s;};
  const max=field('最高提示级别',levels(5,policy.max_level));max.id='policy-max-level';
  const full=el('input'),web=el('input');full.type=web.type='checkbox';full.id='policy-full-answer';web.id='policy-web-search';full.checked=policy.allow_full_answer;web.checked=policy.web_search;
  field('允许给出完整解答（还需最高设L5、已完成完整阶梯）',full);field('联网搜索（需本地TAVILY_API_KEY）',web);
  const homework=field('作业相关问题最高到（实际不高于全局上限）',levels(3,policy.homework_level));homework.id='policy-homework-level';
  const keywords=el('textarea');keywords.rows=4;keywords.maxLength=6100;keywords.value=policy.blocked_keywords.join('\n');keywords.id='policy-keywords';field('禁答关键词（每行一条，最多50条，每条1–120字符；字面匹配）',keywords);
  const blocked=new Set(policy.blocked_concepts), list=el('fieldset');list.append(el('legend','禁答范围 · 知识点（现行已审核）'));
  for(const x of knowledge.concepts.filter(x=>x.status==='approved'&&x.available)){const label=el('label',undefined,'option-label'),input=el('input');input.type='checkbox';input.value=x.id;input.checked=blocked.has(x.id);label.append(input,document.createTextNode(x.name));list.append(label);}form.append(list);
  form.append(el('p','作业知识点标记在“课件管理”中设置，并继续排除小测。禁答会阻止依据页复习和相关新测验；活动题受限后可取消。已加载到浏览器或已复制的内容无法远程收回。','muted'));
  const result=el('p',undefined,'notice');result.id='policy-result';form.append(result,actionButton('保存策略',async()=>{const saved=await jsonApi('/api/admin/policy',{max_level:Number(max.value),allow_full_answer:full.checked,web_search:web.checked,homework_level:Number(homework.value),blocked_keywords:keywords.value.split('\n').map(x=>x.trim()).filter(Boolean),blocked_concepts:[...list.querySelectorAll('input:checked')].map(x=>x.value)},'PUT');activeMaxLevel=saved.max_level;activeL5=saved.allow_full_answer&&saved.max_level===5;clearPreview();if(session&&!quizActive){turns=(await(await api('/api/sessions/'+session)).json()).turns;render();}result.textContent=`策略已保存 · 版本 ${saved.version}。新旧访问均受服务端策略约束。`;},'primary'));
}
