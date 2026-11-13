'use strict';
let previewEpoch=0, materialContext=null, selectedCourseId=null;
const reportRanges={report:'',dashboard:''}, reportPeriods={report:'7',dashboard:'7'};
function resetMaterialPreview(){previewEpoch++;materialContext=null;$('material-controls')?.remove();$('material-location')?.remove();$('material-dialog')?.remove();}
function previewValid(epoch,account,view){return epoch===previewEpoch&&account===identityEpoch&&view===viewVersion;}
async function openEvidence(path,title){
  const epoch=++previewEpoch,account=identityEpoch,view=viewVersion;
  const data=await(await api(path)).json();if(!previewValid(epoch,account,view))return false;
  document.body.classList.add('preview-open');$('preview-title').textContent=title;
  $('preview-text').textContent='正在核对来源版本与访问权限…';$('preview-image').hidden=true;
  $('material-controls')?.remove();$('material-location')?.remove();
  if(data.type==='web'){$('preview-text').textContent=data.text;return true;}
  const match=path.match(/^\/api\/(turns|knowledge)\/([^/]+)\/(?:citations|pages)\/([^/]+)$/);
  if(match&&!quizActive){materialContext={anchor:match[3],origin:(match[1]==='turns'?'turn:':'concept:')+match[2]};await loadMaterial(undefined,'',epoch);}
  else{materialContext=null;$('preview-text').textContent=[data.text,data.notes,data.caption].filter(Boolean).join('\n\n');showImage(path+'/image');}
  return previewValid(epoch,account,view);
}
async function loadMaterial(number,snippet='',parentEpoch=null){
  const context=materialContext;if(!context)return;
  const epoch=parentEpoch===null?++previewEpoch:parentEpoch,account=identityEpoch,view=viewVersion;
  const params=new URLSearchParams({origin:context.origin});if(number)params.set('page',number);if(snippet)params.set('snippet',snippet);
  const base='/api/materials/'+encodeURIComponent(context.anchor);
  let data;
  try{data=await(await api(base+'?'+params)).json();}
  catch(e){if(previewValid(epoch,account,view)){const error=$('material-location');if(error)error.textContent=e.message;else $('preview-text').textContent=e.message;}return;}
  if(!previewValid(epoch,account,view)||materialContext!==context)return;
  $('preview-title').textContent=`${data.file} · 第 ${data.page} / ${data.pages} 页`;
  const pre=$('preview-text');pre.replaceChildren();
  const text=[data.text,data.notes,data.caption].filter(Boolean).join('\n\n');
  const pos=data.snippet?text.indexOf(data.snippet):-1;
  if(pos>=0){pre.append(document.createTextNode(text.slice(0,pos)),el('mark',data.snippet),document.createTextNode(text.slice(pos+data.snippet.length)));}else pre.textContent=text;
  const imageParams=new URLSearchParams({origin:context.origin,page:data.page});showImage(base+'/image?'+imageParams);
  $('material-controls')?.remove();$('material-location')?.remove();
  const controls=el('section',undefined,'material-controls');controls.id='material-controls';
  const error=el('p',data.location,'muted');error.id='material-location';error.setAttribute('role','status');
  function button(label,fn){const b=el('button',label);b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){if(error.isConnected&&account===identityEpoch&&view===viewVersion&&materialContext===context)error.textContent=e.message;}finally{b.disabled=false;}};return b;}
  const row=el('div',undefined,'row-actions'),prev=button('上一页',()=>loadMaterial(data.page-1)),next=button('下一页',()=>loadMaterial(data.page+1));prev.disabled=data.page<=1;next.disabled=data.page>=data.pages;
  const page=el('input');page.type='number';page.min=1;page.max=data.pages;page.value=data.page;page.setAttribute('aria-label','课件页码');
  row.append(prev,next,page,button('跳页',()=>{const n=Number(page.value);if(!Number.isInteger(n)||n<1||n>data.pages)throw Error('页码超出范围。');return loadMaterial(n);}));controls.append(row);
  const thumbs=el('div',undefined,'material-thumbs');
  for(let n=Math.max(1,data.page-1);n<=Math.min(data.pages,data.page+1);n++){const b=button(`第${n}页`,()=>loadMaterial(n)),image=el('img');image.alt=`第${n}页缩略图`;image.loading='lazy';image.src=base+'/image?'+new URLSearchParams({origin:context.origin,page:n});image.onerror=()=>{image.remove();};b.prepend(image);thumbs.append(b);}controls.append(thumbs);
  const snippetInput=el('input');snippetInput.maxLength=300;snippetInput.value=snippet;snippetInput.placeholder='粘贴本页真实文字或代码片段';snippetInput.setAttribute('aria-label','定位片段');
  controls.append(snippetInput,button('定位片段',()=>loadMaterial(data.page,snippetInput.value)),button('放大原页',()=>{
    const dialog=el('dialog');dialog.id='material-dialog';const close=el('button','关闭放大');close.onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();
    const wrap=el('div',undefined,'zoom-image'),image=el('img');image.alt='课件原页放大';image.src=base+'/image?'+imageParams;wrap.append(image);
    for(const r of data.rectangles){const mark=el('span',undefined,'location-rect');mark.style.left=r[0]*100+'%';mark.style.top=r[1]*100+'%';mark.style.width=(r[2]-r[0])*100+'%';mark.style.height=(r[3]-r[1])*100+'%';wrap.append(mark);}
    dialog.append(close,el('p',data.location),wrap);document.body.append(dialog);dialog.showModal();
  }),button('打开文件',async()=>{const r=await api(base+'/file?'+new URLSearchParams({origin:context.origin}));if(!previewValid(epoch,account,view))return;await saveDownload(r,'course-slides.pdf');}));
  $('preview-text').before(controls,error);
}
async function saveDownload(response,name){const account=identityEpoch,blob=await response.blob();if(account!==identityEpoch)return;const url=URL.createObjectURL(blob),link=el('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function dateControls(kind,reload){
  const form=el('section',undefined,'date-controls'),period=el('select'),from=el('input'),to=el('input'),message=el('span',undefined,'notice');
  period.setAttribute('aria-label','统计日期范围');for(const [v,label] of [['7','最近7天'],['30','最近30天'],['custom','自定义日期']])period.append(new Option(label,v));
  for(const [input,label] of [[from,'开始日期'],[to,'结束日期（包含）']]){input.type='date';input.setAttribute('aria-label',label);}
  const date=x=>{const d=new Date(x);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;};
  const current=new URLSearchParams(reportRanges[kind]);if(current.has('start')){from.value=date(Number(current.get('start'))*1000);to.value=date(Number(current.get('end'))*1000-1);period.value=reportPeriods[kind];}else{from.value=date(Date.now()-6*86400000);to.value=date(Date.now());}
  const toggle=()=>{from.hidden=to.hidden=period.value!=='custom';};period.onchange=toggle;toggle();
  const apply=el('button','应用日期');apply.onclick=async()=>{apply.disabled=true;try{let start,end;if(period.value==='custom'){start=new Date(from.value+'T00:00:00');end=new Date(to.value+'T00:00:00');end.setDate(end.getDate()+1);}else{end=new Date();start=new Date(end.getTime()-Number(period.value)*86400000);}
    if(!Number.isFinite(+start)||!Number.isFinite(+end)||+start>=+end||+end-+start>366*86400000)throw Error('请选择先后有序且不超过366天的日期。');reportRanges[kind]='?'+new URLSearchParams({start:+start/1000,end:+end/1000});reportPeriods[kind]=period.value;await reload();}catch(e){message.textContent=e.message;}finally{apply.disabled=false;}};
  const download=el('button','导出');download.title='导出UTF-8 CSV';download.setAttribute('aria-label','导出CSV');download.onclick=async()=>{download.disabled=true;try{const path=kind==='report'?'/api/report/export':'/api/admin/overview/export';await saveDownload(await api(path+reportRanges[kind]),`sctutor-${kind}.csv`);}catch(e){message.textContent=e.message;}finally{download.disabled=false;}};
  form.append(period,from,to,apply,download,message);return form;
}
async function loadCourseNavigation(){
  const box=$('course-navigation');if(!box||quizActive)return;
  const account=identityEpoch,rows=await(await api('/api/courses')).json();if(account!==identityEpoch||quizActive)return;box.replaceChildren();if(!rows.some(x=>x.id===selectedCourseId))selectedCourseId=null;const all=el('button','全部课程（不限定文件）');all.onclick=()=>{selectedCourseId=null;loadCourseNavigation().catch(e=>note(e.message));note('新问题将检索全部已入库课程。');};box.append(all);
  for(const d of rows){const b=el('button',`${d.file} · ${d.pages}页`);b.title=d.file;b.classList.toggle('selected',selectedCourseId===d.id);b.onclick=()=>{selectedCourseId=d.id;note('已选择 '+d.file+'，新问题将仅检索此文件；追问沿用原问题范围。');loadCourseNavigation().catch(()=>{});const candidates=[...turns].reverse().filter(t=>t.level>=3&&t.status==='completed'&&!t.policy_blocked).flatMap(t=>t.citations.filter(c=>c.file===d.file).map(c=>[t,c]));if(candidates.length){const [t,c]=candidates[0];openEvidence(`/api/turns/${t.id}/citations/${c.id}`,c.file).catch(e=>note(e.message));}else note('已选择 '+d.file+'。新问题仅检索此文件；达到L3或主动审核知识复习后可打开正文。');};box.append(b);}
  if(!rows.length)box.append(el('p','尚无已入库课件，请教师上传并同步。','muted'));
}
