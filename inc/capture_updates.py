"""Attach replay variables to unambiguously matched primary stack frames."""

SCRIPT = r'''
<script>
// aiv-stack-captures-v9
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
 const stateStyles={normal:['正常','#195943','#edf5ef'],uncertain:['不确定','#805000','#fff3d6'],abnormal:['异常','#9f3029','#fff0ec']};
 function variableState(state,readStatus,complete){
  if(['undefined','partially_undefined','unaddressable','out_of_bounds'].includes(state))return 'abnormal';
  if(readStatus&&readStatus!=='available')return 'uncertain';
  return ['defined','in_bounds'].includes(state)&&complete!==false?'normal':'uncertain';
 }
 function colorState(node,state){
  const [label,color,background]=stateStyles[state];node.dataset.variableState=state;
  node.style.color=color;node.style.backgroundColor=background;
  node.textContent='【'+label+'】'+node.textContent;
 }
 function variableLines(v){
  const info=v.initialization||{},details=v.member_details;
  const lines=[];const overall=variableState(info.observed_status||info.status,v.status,info.complete);
  lines.push({text:'\n'+v.name+(v.type?'（'+v.type+'）':'')+' · '+(v.role==='argument'?'函数参数':'局部变量'),state:overall});
  if(v.status&&v.status!=='available'){lines.push({text:'无法读取：'+v.value,state:'uncertain'});return lines}
  lines.push({text:'检查结果：'+(initLabels[info.status]||'未提供初始化检查结果'),state:overall});
  if(info.reason)lines.push('检查范围说明：'+info.reason);
  const members=details?.members||info.member_states?.filter(m=>m.name!=='$self')||[];
  if(members.length){
   lines.push('成员信息（字段 → 实际值 → 初始化状态）：');
   for(const m of members){
    const name=v.name+(m.name.startsWith('[')?'':'.')+m.name;
    const init=typeof m.initialization==='string'?m.initialization:(details?'unknown':m.status);
    const value=m.value===undefined?'旧现场未单独保存成员值':String(m.value);
    lines.push({text:'  '+name+(m.type?'（'+m.type+'）':'')+' = '+value+'；'+(details&&m.status!=='available'?'无法读取':(initLabels[init]||'初始化状态无法确定')),state:variableState(init,details?m.status:undefined,info.complete)});
    if(m.note)lines.push('    '+m.note);
   }
   if(details?.note)lines.push('展示范围：'+details.note);
   if(!details)lines.push('原始结构体值：'+v.value);
  }else lines.push({text:'实际值：'+v.value,state:overall});
  return lines;
 }
 function paint(e){
  const saved=captures.get(e.id);if(!saved)return;
  for(const node of document.getElementById('detail').children){
   const frame=saved.frames.get(node.dataset?.frameKey);if(!frame)continue;
   let panel=[...node.children].find(n=>n.dataset?.captureVariables==='yes');
   if(panel?._captureSaved===saved)continue; // Preserve expanded sections and selection during polling.
   if(panel)panel.remove();
   panel=document.createElement('section');panel.dataset.captureVariables='yes';panel.style.cssText='margin:16px 0;padding:16px;border:1px solid var(--line);overflow-wrap:anywhere';node.append(panel);
   panel._captureSaved=saved;
   const add=(tag,text,parent=panel)=>{const element=document.createElement(tag);element.textContent=text;parent.append(element);return element};
   add('h3','步骤三变量分析').style.margin='0 0 6px';
   add('p',saved.snapshot.captured_at||'本次运行').className='meta';
   const legend=add('p','');legend.className='meta';
   for(const state of Object.keys(stateStyles)){const tag=add('span',' ',legend);colorState(tag,state);tag.style.marginRight='10px'}
   add('p','颜色仅表示已检查的初始化或索引状态；绿色不代表业务取值正确，也不证明指针指向的内存可访问。').className='meta';
   const lines=[];
   if(node.dataset.frameKey===saved.frames.keys().next().value){
    const memory=saved.snapshot.memory||{};
    if(memory.explanation)lines.push(memory.explanation);
    lines.push(memory.range_explanation||'当前诊断未提供足够的内存块边界，无法确定合法访问范围。');
   }
   if(lines.length)add('p',lines.join('\n')).style.whiteSpace='pre-wrap';
   add('h4','需要重点检查的变量');
   const warnings=[];
   const suspicious=s=>['undefined','partially_undefined','unaddressable'].includes(s);
   for(const v of frame.variables||[]){
    const info=v.initialization||{},details=v.member_details;
    const members=details?.members||info.member_states?.filter(m=>m.name!=='$self')||[];
    let found=false;
    for(const m of members){
     const state=typeof m.initialization==='string'?m.initialization:(details?'unknown':m.status);
     if(!suspicious(state))continue;
     const name=v.name+(m.name.startsWith('[')?'':'.')+m.name;
     warnings.push(name+'：'+initLabels[state]);found=true;
    }
    if(!found&&suspicious(info.observed_status||info.status))warnings.push(v.name+'：'+(initLabels[info.observed_status||info.status]||'需要检查'));
   }
   for(const level of frame.index_analysis?.levels||[])if(level.status==='out_of_bounds')warnings.push(level.expression+'：索引越界，实际 '+level.actual_index+'；合法范围 '+(level.bounds?level.bounds.join('～'):'未知'));
   if(warnings.length){
    const ul=add('ul','');for(const warning of [...new Set(warnings)])colorState(add('li',warning,ul),'abnormal');
    add('p','以上是需核查的现场异常，不代表已确认的错误原因。请结合报错行检查赋值与访问过程。').className='meta';
   }else colorState(add('p','本帧尚未定位到具体异常变量；不表示全部变量正常。可展开下方信息核查。'),'uncertain');
   const all=add('details','');all.dataset.captureAllVariables='yes';
   add('summary','完整变量信息（'+(frame.variables||[]).length+' 个变量，点击展开）',all);
   const values=[];for(const v of frame.variables||[])values.push(...variableLines(v));
   if(!values.length)values.push(frame.note||'此帧没有可读取的变量');
   const valueBlock=add('pre','',all);valueBlock.className='source';valueBlock.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;padding:12px';
   for(const row of values){const line=add('span',typeof row==='string'?row:row.text,valueBlock);line.style.display='block';if(row.state)colorState(line,row.state)}
   const analysis=[];
   if(frame.index_analysis?.levels?.length){
    analysis.push('嵌套索引逐级分析（源码现场推导）');
    for(const level of frame.index_analysis.levels){
     const bounds=level.bounds?level.bounds.join('～'):'未知';
     analysis.push({text:level.expression+'：实际索引 '+(level.actual_index??'未知')+'；合法范围 '+bounds+'；'+({in_bounds:'该级未越界',out_of_bounds:'该级越界',unknown:'无法判断'}[level.status]||level.status)+(level.note?'（'+level.note+'）':''),state:variableState(level.status)});
    }
    analysis.push(frame.index_analysis.note);
   }
   if(analysis.length){const section=add('details','');add('summary','索引分析依据（点击展开）',section);const block=add('pre','',section);block.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';for(const row of analysis){if(!row)continue;const line=add('span',typeof row==='string'?row:row.text,block);line.style.display='block';if(row.state)colorState(line,row.state)}}
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
 }
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
