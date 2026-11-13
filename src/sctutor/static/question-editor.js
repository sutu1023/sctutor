'use strict';
function questionEditor(original,concepts,pages){
  const root=el('section',undefined,'question-editor'), fields={}, errors={},labels={type:'题型',prompt:'题干',options:'选项（每行一个）',answer:'参考答案',rubric:'评分要点',hint:'首次错误提示',concept_ids:'考查知识点',page_ids:'依据课件页'};
  function field(key,node){
    const label=el('label',undefined,'editor-field'),text=el('span',labels[key]),error=el('span',undefined,'field-error');
    node.setAttribute('aria-label',labels[key]);node.id='edit-'+crypto.randomUUID();error.id=node.id+'-error';node.setAttribute('aria-describedby',error.id);error.setAttribute('role','status');
    label.append(text,node,error);root.append(label);fields[key]=node;errors[key]=error;return node;
  }
  const type=field('type',el('select'));for(const [value,title] of [['choice','单选'],['blank','填空'],['short','简答'],['code','代码']])type.append(new Option(title,value));type.value=original.type;
  for(const [key,max] of [['prompt',4000],['options',6005],['answer',6000],['rubric',4000],['hint',1000]]){const node=field(key,el('textarea'));node.maxLength=max;node.rows=key==='prompt'||key==='options'?4:3;node.value=key==='options'?original.options.join('\n'):original[key];}
  for(const [key,items] of [['concept_ids',concepts.filter(x=>x.status==='approved'&&x.available!==false&&!x.restricted).map(x=>({id:x.id,name:x.name}))],['page_ids',pages.map(x=>({id:x.id,name:`${x.file} · 第${x.page}页`}))]]){
    const node=field(key,el('select'));node.multiple=true;node.size=4;
    const ids=new Set(items.map(x=>x.id));for(const id of original[key])if(!ids.has(id))items.push({id,name:'原关联已失效：'+id});
    for(const item of items){const opt=new Option(item.name,item.id);opt.selected=original[key].includes(item.id);node.append(opt);}
  }
  const help=el('p','保存将停用旧版并生成新待审版本，不会自动批准。知识点须已审核且非作业限制，依据页必须属于选中的知识点。','muted');root.append(help);
  const preview=el('details',undefined,'question-preview'),student=el('div');preview.append(el('summary','学生作答预览（不含答案和评分）'),student);
  const diff=el('details'),changes=el('div');diff.append(el('summary','与原版本的差异'),changes);root.append(preview,diff);
  function raw(){return {type:type.value,...Object.fromEntries(['prompt','answer','rubric','hint'].map(k=>[k,fields[k].value.trim()])),options:type.value==='choice'?fields.options.value.split('\n').map(x=>x.trim()).filter(Boolean):[],...Object.fromEntries(['concept_ids','page_ids'].map(k=>[k,[...fields[k].selectedOptions].map(x=>x.value)]))};}
  function update(){
    fields.options.closest('label').hidden=type.value!=='choice';
    const value=raw();student.replaceChildren(el('p',value.prompt,'prewrap'));if(value.type==='choice'){const list=el('ul');for(const opt of value.options)list.append(el('li',opt));student.append(list);}else student.append(el('p','学生在此输入答案','muted'));
    changes.replaceChildren();let count=0;for(const key of Object.keys(labels)){if(JSON.stringify(original[key])!==JSON.stringify(value[key])){count++;const row=el('div',undefined,'editor-diff');row.append(el('strong',labels[key]),el('pre','原：'+JSON.stringify(original[key])+'\n新：'+JSON.stringify(value[key])));changes.append(row);}}if(!count)changes.append(el('p','尚无修改。','muted'));
  }
  for(const node of Object.values(fields)){node.oninput=update;node.onchange=update;}update();
  function read(){
    const value=raw();let first=null;
    for(const [key,error] of Object.entries(errors)){error.textContent='';fields[key].removeAttribute('aria-invalid');}
    function invalid(key,text){errors[key].textContent=text;fields[key].setAttribute('aria-invalid','true');first ||= fields[key];}
    for(const [key,max] of [['prompt',4000],['answer',6000],['rubric',4000],['hint',1000]])if(!value[key]||value[key].length>max)invalid(key,`请填写1至${max}个字符。`);
    if(value.type==='choice'){
      if(value.options.length<2||value.options.length>6||new Set(value.options).size!==value.options.length||value.options.some(x=>x.length>1000))invalid('options','需2至6个互不相同的非空选项，每项不超过1000字。');
      if(!value.options.includes(value.answer))invalid('answer','单选题答案必须与一个选项完全一致。');
    }
    const validConcepts=concepts.filter(x=>x.status==='approved'&&x.available!==false&&!x.restricted);
    if(!value.concept_ids.length||value.concept_ids.length>5||value.concept_ids.some(id=>!validConcepts.some(x=>x.id===id)))invalid('concept_ids','请选择1至5个审核通过、来源现行且非作业限制的知识点。');
    const allowed=new Set(validConcepts.filter(x=>value.concept_ids.includes(x.id)).flatMap(x=>x.pages || []));
    if(!value.page_ids.length||value.page_ids.length>8||value.page_ids.some(id=>!pages.some(x=>x.id===id)||!allowed.has(id)))invalid('page_ids','请选择1至8个属于所选知识点的现行依据页。');
    if(first){first.focus();throw Error('请修正标出的题目字段；输入尚未保存。');}
    return value;
  }
  return {element:root,read};
}
