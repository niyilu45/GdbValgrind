"""Attach replay variables to unambiguously matched primary stack frames."""

SCRIPT = r'''
<script>
// aiv-stack-captures-v19
(()=>{
 let running=true,timer,activeReplay=null;const captures=new Map();
 const status=document.getElementById('capture-status');
 const normalize=s=>{const parts=[];for(const p of String(s||'').replace(/\\/g,'/').split('/')){if(p==='.')continue;if(p==='..'&&parts.length&&parts[parts.length-1]!=='..')parts.pop();else parts.push(p)}return parts.join('/')};
 const paths=f=>[f.local_file,f.file&&(/^(\/|[A-Za-z]:)/.test(f.file)?f.file:(f.dir?f.dir+'/':'')+f.file)].filter(Boolean).map(normalize);
 const index=new Map();for(const e of report.errors)for(const f of (e.stacks[0]?.frames||[]).slice(0,16))if(Number(f.line)>0)for(const p of paths(f)){const key=p+':'+Number(f.line);if(!index.has(key))index.set(key,new Set());index.get(key).add(e)}
 const message=s=>String(s||'').replace(/0x[0-9a-f]+/gi,'<address>').replace(/\s+/g,' ').trim().toLowerCase();
 function sameFrame(f,r){
  const fn=s=>String(s||'').replace(/\s+/g,' ').trim().replace(/\([^()]*\)$/,'').trim();
  return !!r.file&&paths(f).includes(normalize(r.file))&&Number(f.line)>0&&Number(f.line)===Number(r.line)&&(!f.fn||!r.function||fn(f.fn)===fn(r.function));
 }
 function match(e,snapshot){
  if(snapshot.association?.status!=='verified')return null;
  const diagnostic=String(snapshot.valgrind_error||'').split('\n').map(line=>message(line.replace(/==\d+==\s*/g,'')));
  if(!e.what||!diagnostic.includes(message(e.what)))return null;
  const frames=e.stacks[0]?.frames||[],runtime=snapshot.frames||[];
  if(!frames.length||!runtime.length)return null;
  // Align source frames, ignoring unsymbolized wrappers. Never use the replay seed ID.
  const source=frames.slice(0,16).map((f,i)=>({f,i})).filter(x=>paths(x.f).length&&Number(x.f.line)>0);
  if(!source.length)return null;
  // Never match a caller further down the runtime stack, or accept a short
  // prefix when the recorded caller chain is unavailable.
  const result=new Map();
  for(let j=0;j<source.length;j++){
   const actual=runtime[source[j].i];
   if(!actual||!sameFrame(source[j].f,actual))return null;
   result.set('0:'+source[j].i,actual);
  }
  return result;
 }
 const initLabels={defined:'已检查部分已初始化',undefined:'未初始化，显示值不可靠',partially_undefined:'部分未初始化，显示值不可靠',unaddressable:'内存不可访问',out_of_bounds:'索引越界，未读取越界元素',partial_check:'只检查了部分内容，其余未知',unknown:'初始化状态无法确定'};
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
  lines.push({text:'\n'+v.name+(v.type?'（'+v.type+'）':'')+' · '+(v.role==='argument'?'函数参数':v.role==='source_expression'?'本行表达式逐项读取':'局部变量'),state:overall});
  if(v.macro_expansion)lines.push('当前源码位置的宏展开：'+v.macro_expansion);
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
   // Collapsed source frames load their code on the first toggle, after this
   // panel was attached. Keep the same panel below that lazily inserted code.
   const loadSource=node.ontoggle;
   node.ontoggle=function(...args){
    if(loadSource)loadSource.apply(this,args);
    if(panel.parentNode===node&&node.lastElementChild!==panel)node.append(panel);
   };
   const add=(tag,text,parent=panel)=>{const element=document.createElement(tag);element.textContent=text;parent.append(element);return element};
   add('h3','步骤三变量分析').style.margin='0 0 6px';
   const directChecks=frame.line_checks?.version===1;
   add('p','变量查看器 v19 · '+(directChecks?'本行逐项检查 '+frame.line_checks.items.length+' 项'+(frame.line_checks.truncated?'（已达到 32 项上限）':''):'旧现场：没有本行逐项检查记录；以下仅是旧值的源码匹配，不代表已逐项检查')).className='meta';
   add('p','所属报告错误：'+e.id+' · 采集现场：'+(saved.captureId||'旧数据未提供现场文件名')).className='meta';
   if(e.records>1)add('p','本项合并了 '+e.records+' 条原始错误记录；以下仅来自上面标明的一次现场，不合并不同现场的变量值。').className='meta';
   add('p','现场来源：'+(frame.function||'未知函数')+' · '+(frame.file||'未知文件')+':'+(frame.line||'?')+' · GDB 帧 #'+(frame.index??'?')).className='meta';
   add('p',saved.snapshot.captured_at||'本次运行').className='meta';
   const [si,fi]=node.dataset.frameKey.split(':').map(Number),recorded=e.stacks[si]?.frames[fi];
   const shownLine=recorded?.source?.find(row=>Number(row.number)===Number(recorded.line))?.text;
   const capturedLine=frame.source_line?.text;
   const sourceChanged=typeof shownLine==='string'&&typeof capturedLine==='string'&&shownLine.trim()!==capturedLine.trim();
   const identifiers=!sourceChanged&&typeof frame.source_line?.identifiers==='string'?frame.source_line.identifiers:'';
   // Preserve token boundaries: `return value` must not become `returnvalue`.
   // A member `ptr->value` is not the unrelated local variable `value`.
   const tokens=s=>String(s).match(/[A-Za-z_$][\w$]*|\d+|->|::|[^\s]/g)||[];
   const sourceTokens=s=>tokens(s.replace(/\/\*.*?\*\/|\/\/.*$|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g,' '));
   const code=sourceTokens(identifiers);
   const evidence=[code,...[capturedLine,shownLine].filter(s=>typeof s==='string').map(sourceTokens)];
   // Require the name in both recorded association text and actual source.
   // Old/malformed metadata must never put a different line's locals here.
   const onLine=(name,leaf=false)=>{const needle=tokens(name);return needle.length>0&&evidence.every(parts=>parts.some((_,i)=>!['.','->','::'].includes(parts[i-1])&&needle.every((t,j)=>parts[i+j]===t)&&(!leaf||!['.','->','['].includes(parts[i+needle.length]))))};
   const relatedVariables=[];
   for(const original of (directChecks?frame.line_checks.items:frame.variables)||[]){
    if(!onLine(original.name))continue;
    const v={...original,initialization:{...original.initialization}};
    const members=v.member_details?.members||v.initialization.member_states?.filter(m=>m.name!=='$self');
    const aggregate=!!members||/^\s*[\{\[]/.test(String(v.value||''));
    if(aggregate){
     const selected=(members||[]).filter(m=>onLine(v.name+(m.name.startsWith('[')?'':'.')+m.name,true));
     v.member_details={members:selected.map(m=>({...m,...(/^\s*[\{\[]/.test(String(m.value||''))?{value:'对象值未展开，避免混入未匹配的成员',initialization:'unknown'}:{initialization:m.initialization||(original.member_details?'unknown':m.status)}),status:original.member_details?m.status:'available'}))};
     v.initialization={status:'unknown',complete:original.initialization?.complete};
     v.value='未采集到可直接对应的成员值，不展示整个对象的值';
    }
    relatedVariables.push(v);
   }
   add('p',sourceChanged?'报告源码与步骤三采集时的源码行不同，本区域不展示变量值。':identifiers?'只显示当前源码行能对应的变量或具体成员；文本出现不证明实际读取或导致错误。':'此现场没有可核对的源码行，本区域不展示变量值。').className='meta';
   if(capturedLine)add('pre','采集时源码：'+capturedLine).style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';
   add('h4','本行源码中出现的变量');
   const relevant=add('pre','');relevant.className='source';relevant.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;padding:12px';let relatedCount=0;
   for(const v of relatedVariables){
    if(!onLine(v.name))continue;
    const details=v.member_details;
    const members=details?.members||v.initialization?.member_states?.filter(m=>m.name!=='$self');
    if(members){
     let memberCount=0;
     for(const m of members){const name=v.name+(m.name.startsWith('[')?'':'.')+m.name;if(!onLine(name))continue;const state=typeof m.initialization==='string'?m.initialization:(details?'unknown':m.status);const row=add('span',name+' = '+(m.value??'旧现场未单独保存成员值')+'；'+(initLabels[state]||'初始化状态无法确定'),relevant);row.style.display='block';colorState(row,variableState(state,details?m.status:undefined,v.initialization?.complete));memberCount++;relatedCount++}
     if(!memberCount){add('span',v.name+'：本行出现此对象，但没有可对应的成员值，不展开其他成员。\n',relevant);relatedCount++}
    }else{for(const row of variableLines(v)){const line=add('span',typeof row==='string'?row:row.text,relevant);line.style.display='block';if(row.state)colorState(line,row.state)}relatedCount++}
   }
   if(!relatedCount)relevant.textContent='未找到可与本行直接对应的已采集变量。宏、多行表达式、全局变量或缺少源码可能导致无法关联。';
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
   add('h4','本行需要核查的异常变量');
   const warnings=[];
   const suspicious=s=>['undefined','partially_undefined','unaddressable','out_of_bounds'].includes(s);
   for(const v of relatedVariables){
    const info=v.initialization||{},details=v.member_details;
    const members=details?.members||info.member_states?.filter(m=>m.name!=='$self')||[];
    let found=false;
    for(const m of members){
     const state=typeof m.initialization==='string'?m.initialization:(details?'unknown':m.status);
     if(!suspicious(state))continue;
     const name=v.name+(m.name.startsWith('[')?'':'.')+m.name;
     if(onLine(name))warnings.push(name+'：'+initLabels[state]);found=true;
    }
    if(!members.length&&!found&&onLine(v.name)&&suspicious(info.observed_status||info.status))warnings.push(v.name+'：'+(initLabels[info.observed_status||info.status]||'需要检查'));
   }
   for(const level of frame.index_analysis?.levels||[])if(level.status==='out_of_bounds'&&onLine(level.expression))warnings.push(level.expression+'：索引越界，实际 '+level.actual_index+'；合法范围 '+(level.bounds?level.bounds.join('～'):'未知'));
   if(warnings.length){
    const ul=add('ul','');for(const warning of [...new Set(warnings)])colorState(add('li',warning,ul),'abnormal');
    add('p','以上是需核查的现场异常，不代表已确认的错误原因。请结合报错行检查赋值与访问过程。').className='meta';
   }else colorState(add('p','本帧尚未定位到具体异常变量；不表示全部变量正常。可展开下方信息核查。'),'uncertain');
   const all=add('details','');all.dataset.captureAllVariables='yes';
   add('summary','本行变量明细（不包含其他局部变量或未匹配的成员）',all);
   const values=[];for(const v of relatedVariables)values.push(...variableLines(v));
   if(!values.length)values.push('没有可与当前源码行对应的变量。原始采集数据保留在 captures 目录。');
   const valueBlock=add('pre','',all);valueBlock.className='source';valueBlock.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;padding:12px';
   for(const row of values){const line=add('span',typeof row==='string'?row:row.text,valueBlock);line.style.display='block';if(row.state)colorState(line,row.state)}
   const analysis=[];
   if(frame.index_analysis?.levels?.length){
    analysis.push('嵌套索引逐级分析（源码现场推导）');
    for(const level of frame.index_analysis.levels){
     if(!onLine(level.expression))continue;
     const bounds=level.bounds?level.bounds.join('～'):'未知';
     analysis.push({text:level.expression+'：实际索引 '+(level.actual_index??'未知')+'；合法范围 '+bounds+'；'+({in_bounds:'该级未越界',out_of_bounds:'该级越界',unknown:'无法判断'}[level.status]||level.status)+(level.note?'（'+level.note+'）':''),state:variableState(level.status)});
    }
    analysis.push(frame.index_analysis.note);
   }
   if(analysis.length){const section=add('details','');add('summary','索引分析依据（点击展开）',section);const block=add('pre','',section);block.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';for(const row of analysis){if(!row)continue;const line=add('span',typeof row==='string'?row:row.text,block);line.style.display='block';if(row.state)colorState(line,row.state)}}
  }
 }
 const originalDetail=detail;detail=function(e){originalDetail(e);paint(e)};
 function hasVariables(id){const saved=captures.get(id);return !!saved&&[...saved.frames.values()].some(f=>(f.line_checks?.version===1?f.line_checks.items:f.variables||[]).some(v=>v.status==='available'||(!v.status&&v.value!==undefined&&v.value!==null)))}
 const filter=document.createElement('input');filter.type='checkbox';filter.id='captureVariableFilter';filter.style.width='auto';
 if(typeof savedFilters!=='undefined')filter.checked=!!savedFilters.variables;
 const label=document.createElement('label');label.htmlFor=filter.id;label.append(filter,document.createTextNode('仅显示已读出变量值的错误'));
 const reset=document.getElementById('resetFilters');reset.before(label);
 const originalMatching=matchingRows;matchingRows=function(...args){const rows=originalMatching(...args);return filter.checked?rows.filter(row=>hasVariables(row.e.id)):rows};
 filter.onchange=()=>{if(typeof savedFilters!=='undefined')savedFilters.variables=!!filter.checked;list()};
 const originalReset=reset.onclick;reset.onclick=function(...args){filter.checked=false;originalReset?.apply(this,args)};
 if(filter.checked)list();
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
  if(data.report_key&&data.report_key!=='__CAPTURE_REPORT_KEY__'){
   running=false;status.textContent='变量数据属于另一份步骤二报告，已停止关联。请重新打开本次生成的 full-report.html。';return;
  }
  let unmatched=0,missing=0,ambiguous=0,unverified=0,changed=false;
  if(data.replay_id&&data.replay_id!==activeReplay){
   activeReplay=data.replay_id;captures.clear();changed=true;
   for(const frame of document.getElementById('detail').children)for(const child of [...frame.children])if(child.dataset?.captureVariables==='yes')child.remove();
   for(const node of document.getElementById('list').children)for(const child of [...node.children])if(child.dataset?.captureBadge==='yes')child.remove();
  }
  for(const item of data.items){
   const snapshot=item.snapshot;if(!snapshot){unmatched++;missing++;continue}
   if(snapshot.association?.status!=='verified'){unmatched++;unverified++;continue}
   const candidates=new Set();for(const r of snapshot.frames||[])for(const e of index.get(normalize(r.file)+':'+Number(r.line))||[])candidates.add(e);
   const matches=[];for(const e of candidates){const frames=match(e,snapshot);if(frames)matches.push({e,frames})}
   if(matches.length!==1){unmatched++;if(matches.length>1)ambiguous++;continue}
   const {e,frames}=matches[0];if(!captures.has(e.id)){captures.set(e.id,{snapshot,frames,captureId:item.id});changed=true}
  }
  const current=report.errors.find(e=>e.id===selected);if(current)paint(current);
  if(changed&&filter.checked)list();else markNavigation();
  running=data.live;
  status.textContent=(running?'采集中':'采集已结束')+' · 已关联 '+captures.size+' 个错误，其中 '+[...captures.keys()].filter(hasVariables).length+' 个已读出变量；'+unmatched+' 个现场未能唯一匹配（缺少现场数据 '+missing+'，停止位置未核实 '+unverified+'，多个候选 '+ambiguous+'，位置或错误描述不匹配 '+(unmatched-missing-ambiguous-unverified)+'）。原始现场保留在 captures 目录。'+(unverified?' 旧现场或未核实现场不会填入堆栈，请重新执行步骤三。':'');
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
