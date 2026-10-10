"""Attach replay variables to unambiguously matched primary stack frames."""

SCRIPT = r'''
<script>
// aiv-stack-captures-v6
(()=>{
 let running=true,timer;const captures=new Map();
 const status=document.getElementById('capture-status');
 const normalize=s=>{const parts=[];for(const p of String(s||'').replace(/\\/g,'/').split('/')){if(p==='.')continue;if(p==='..'&&parts.length&&parts[parts.length-1]!=='..')parts.pop();else parts.push(p)}return parts.join('/')};
 const paths=f=>[f.local_file,f.file&&(/^(\/|[A-Za-z]:)/.test(f.file)?f.file:(f.dir?f.dir+'/':'')+f.file)].filter(Boolean).map(normalize);
 const index=new Map();for(const e of report.errors)for(const f of (e.stacks[0]?.frames||[]).slice(0,16))if(Number(f.line)>0)for(const p of paths(f)){const key=p+':'+Number(f.line);if(!index.has(key))index.set(key,new Set());index.get(key).add(e)}
 const message=s=>String(s||'').replace(/0x[0-9a-f]+/gi,'<address>').replace(/\s+/g,' ').trim().toLowerCase();
 function sameFrame(f,r){
  return !!r.file&&paths(f).includes(normalize(r.file))&&Number(f.line)>0&&Number(f.line)===Number(r.line);
 }
 function match(e,snapshot){
  if(!snapshot.valgrind_error||!message(snapshot.valgrind_error).includes(message(e.what))||!e.what)return null;
  const frames=e.stacks[0]?.frames||[],runtime=snapshot.frames||[];
  if(!frames.length||!runtime.length)return null;
  // Align source frames, ignoring unsymbolized wrappers. Never use the replay seed ID.
  const source=frames.slice(0,16).map((f,i)=>({f,i})).filter(x=>paths(x.f).length&&Number(x.f.line)>0);
  const actual=runtime.filter(r=>r.file&&Number(r.line)>0);
  if(!source.length||!actual.length)return null;
  const alignments=[];
  for(let start=0;start<actual.length;start++){
   if(!sameFrame(source[0].f,actual[start]))continue;
   const result=new Map();let valid=true;
   for(let j=0;j<source.length&&start+j<actual.length;j++){
    if(!sameFrame(source[j].f,actual[start+j])){valid=false;break}
    result.set('0:'+source[j].i,actual[start+j]);
   }
   if(valid&&result.size)alignments.push(result);
  }
  return alignments.length===1?alignments[0]:null;
 }
 const initLabels={defined:'已检查部分已初始化',undefined:'未初始化，显示值不可靠',partially_undefined:'部分未初始化，显示值不可靠',unaddressable:'内存不可访问',partial_check:'只检查了部分内容，其余未知',unknown:'初始化状态无法确定'};
 function variableLines(v){
  const info=v.initialization||{},details=v.member_details;
  const lines=['',v.name+(v.type?'（'+v.type+'）':'')+' · '+(v.role==='argument'?'函数参数':'局部变量')];
  if(v.status&&v.status!=='available')return lines.concat('无法读取：'+v.value);
  lines.push('检查结果：'+(initLabels[info.status]||'未提供初始化检查结果'));
  if(info.reason)lines.push('检查范围说明：'+info.reason);
  const members=details?.members||info.member_states?.filter(m=>m.name!=='$self')||[];
  if(members.length){
   lines.push('成员信息（字段 → 实际值 → 初始化状态）：');
   for(const m of members){
    const name=v.name+(m.name.startsWith('[')?'':'.')+m.name;
    const init=typeof m.initialization==='string'?m.initialization:(details?'unknown':m.status);
    const value=m.value===undefined?'旧现场未单独保存成员值':String(m.value);
    lines.push('  '+name+(m.type?'（'+m.type+'）':'')+' = '+value+'；'+(details&&m.status!=='available'?'无法读取':(initLabels[init]||'初始化状态无法确定')));
    if(m.note)lines.push('    '+m.note);
   }
   if(details?.note)lines.push('展示范围：'+details.note);
   if(!details)lines.push('原始结构体值：'+v.value);
   lines.push('定位提示：优先检查未初始化成员的赋值路径。发现未初始化不等于已证明本次错误使用了该成员；需结合报错行判断。');
  }else lines.push('实际值：'+v.value);
  return lines;
 }
 function paint(e){
  const saved=captures.get(e.id);if(!saved)return;
  for(const node of document.getElementById('detail').children){
   const frame=saved.frames.get(node.dataset?.frameKey);if(!frame)continue;
   let panel=[...node.children].find(n=>n.dataset?.captureVariables==='yes');
   if(!panel){panel=document.createElement('pre');panel.className='source';panel.dataset.captureVariables='yes';panel.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';node.append(panel)}
   const lines=['步骤三实际变量（'+(saved.snapshot.captured_at||'本次运行')+'）'];
   if(node.dataset.frameKey===saved.frames.keys().next().value){
    const memory=saved.snapshot.memory||{};
    if(memory.explanation)lines.push(memory.explanation);
    lines.push(memory.range_explanation||'当前诊断未提供足够的内存块边界，无法确定合法访问范围。');
   }
   for(const v of frame.variables||[])lines.push(...variableLines(v));
   if(frame.index_analysis?.levels?.length){
    lines.push('嵌套索引逐级分析（源码现场推导）');
    for(const level of frame.index_analysis.levels){
     const bounds=level.bounds?level.bounds.join('～'):'未知';
     lines.push(level.expression+'：实际索引 '+(level.actual_index??'未知')+'；合法范围 '+bounds+'；'+({in_bounds:'该级未越界',out_of_bounds:'该级越界',unknown:'无法判断'}[level.status]||level.status)+(level.note?'（'+level.note+'）':''));
    }
    lines.push(frame.index_analysis.note);
   }
   if(!(frame.variables||[]).length)lines.push(frame.note||'此帧没有可读取的变量');
   const text=lines.join('\n');if(panel.textContent!==text)panel.textContent=text;
  }
 }
 const originalDetail=detail;detail=function(e){originalDetail(e);paint(e)};
 function hasVariables(id){const saved=captures.get(id);return !!saved&&[...saved.frames.values()].some(f=>(f.variables||[]).some(v=>v.status==='available'||(!v.status&&v.value!==undefined&&v.value!==null)))}
 const filter=document.createElement('input');filter.type='checkbox';filter.id='captureVariableFilter';filter.style.width='auto';
 const label=document.createElement('label');label.htmlFor=filter.id;label.append(filter,document.createTextNode('仅显示已读出变量值的错误'));
 const reset=document.getElementById('resetFilters');reset.before(label);
 const originalMatching=matchingRows;matchingRows=function(...args){const rows=originalMatching(...args);return filter.checked?rows.filter(row=>hasVariables(row.e.id)):rows};
 filter.onchange=()=>list();
 const originalReset=reset.onclick;reset.onclick=function(...args){filter.checked=false;originalReset?.apply(this,args)};
 function badge(node,text){
  let mark=[...node.children].find(n=>n.dataset?.captureBadge==='yes');
  if(!mark){mark=document.createElement('span');mark.dataset.captureBadge='yes';mark.style.cssText='display:inline-block;font-size:12px;padding:2px 6px;border:1px solid #195943;border-radius:4px;color:#195943;background:#edf5ef;margin:4px';node.append(mark)}
  if(mark.textContent!==text)mark.textContent=text;
  mark.hidden=!text;
  mark.style.display=text?'inline-block':'none';
 }
 function markNavigation(){
  for(const node of document.getElementById('list').children){const id=node.dataset?.errorId;if(id&&captures.has(id))badge(node,hasVariables(id)?'步骤三 · 已有变量':'步骤三 · 未读到变量')}
  const counts=new Map();let total=0;
  for(const row of matchingRows()){if(hasVariables(row.e.id)){total++;counts.set(row.e.kind,(counts.get(row.e.kind)||0)+1)}}
  const kinds=['',...new Set(report.errors.map(e=>e.kind))];
  [...document.getElementById('nav').children].forEach((node,i)=>{const n=i===0?total:(counts.get(kinds[i])||0);badge(node,n?'变量 '+n:'')});
 }
 const originalNav=nav;nav=function(){originalNav();markNavigation()};
 const originalList=list;list=function(...args){originalList(...args);markNavigation()};
 window.aivCaptureUpdate=data=>{
  let unmatched=0,missing=0,ambiguous=0,changed=false;
  for(const item of data.items){
   const snapshot=item.snapshot;if(!snapshot){unmatched++;missing++;continue}
   const candidates=new Set();for(const r of snapshot.frames||[])for(const e of index.get(normalize(r.file)+':'+Number(r.line))||[])candidates.add(e);
   const matches=[];for(const e of candidates){const frames=match(e,snapshot);if(frames)matches.push({e,frames})}
   if(matches.length!==1){unmatched++;if(matches.length>1)ambiguous++;continue}
   const {e,frames}=matches[0];if(!captures.has(e.id)){captures.set(e.id,{snapshot,frames});changed=true}
  }
  const current=report.errors.find(e=>e.id===selected);if(current)paint(current);
  if(changed&&filter.checked)list();else markNavigation();
  running=data.live;
  status.textContent=(running?'采集中':'采集已结束')+' · 已关联 '+captures.size+' 个错误，其中 '+[...captures.keys()].filter(hasVariables).length+' 个已读出变量；'+unmatched+' 个现场未能唯一匹配（缺少现场数据 '+missing+'，多个候选 '+ambiguous+'，位置或错误描述不匹配 '+(unmatched-missing-ambiguous)+'）。原始现场保留在 captures 目录。';
 };
 function poll(){
  if(!running)return;
  const script=document.createElement('script');script.src='capture-updates.js?t='+Date.now();
  let finished=false;const finish=()=>{if(finished)return;finished=true;clearTimeout(timer);script.remove();if(running)timer=setTimeout(poll,2000)};
  script.onload=finish;script.onerror=()=>{status.textContent='无法加载 capture-updates.js，请将它与 full-report.html 放在同一目录。正在重试…';finish()};timer=setTimeout(finish,10000);document.head.append(script);
 }
 poll();
})();
</script>
'''
