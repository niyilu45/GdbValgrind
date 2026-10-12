"""Attach replay variables to unambiguously matched primary stack frames."""

SCRIPT = r'''
<script>
// aiv-stack-captures-v34
(()=>{
 let running=true,timer,activeReplay=null,loadedRevision=null,probeRevision=null;const captures=new Map(),readableIds=new Set(),processed=new Map();
 const status=document.getElementById('capture-status');
 const unmatchedDetails=document.createElement('details'),unmatchedTitle=document.createElement('summary'),unmatchedText=document.createElement('pre');
 unmatchedTitle.textContent='未关联原因与候选错误（点击展开）';unmatchedText.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';unmatchedDetails.append(unmatchedTitle,unmatchedText);status.before(unmatchedDetails);
 const normalize=s=>{const parts=[];for(const p of String(s||'').replace(/\\/g,'/').split('/')){if(p==='.')continue;if(p==='..'&&parts.length&&parts[parts.length-1]!=='..')parts.pop();else parts.push(p)}return parts.join('/')};
 const paths=f=>[f.local_file,f.file&&(/^(\/|[A-Za-z]:)/.test(f.file)?f.file:(f.dir?f.dir+'/':'')+f.file)].filter(Boolean).map(normalize);
 const index=new Map();for(const e of report.errors){const frames=(e.stacks[0]?.frames||[]).slice(0,16),depth=frames.findIndex(f=>Number(f.line)>0&&paths(f).length);if(depth<0)continue;for(const p of paths(frames[depth])){const key=depth+':'+p+':'+Number(frames[depth].line);if(!index.has(key))index.set(key,new Set());index.get(key).add(e)}}
 const message=s=>String(s||'').replace(/0x[0-9a-f]+/gi,'<address>').replace(/\s+/g,' ').trim().toLowerCase();
 function sameFrame(f,r){
  const fn=s=>String(s||'').replace(/\s+/g,' ').trim().replace(/\([^()]*\)$/,'').trim();
  return !!r.file&&paths(f).includes(normalize(r.file))&&Number(f.line)>0&&Number(f.line)===Number(r.line)&&(!f.fn||!r.function||fn(f.fn)===fn(r.function));
 }
 function match(e,snapshot,failures=[]){
  if(snapshot.association?.status!=='verified')return null;
  const diagnostic=String(snapshot.valgrind_error||'').split('\n').map(line=>message(line.replace(/==\d+==\s*/g,'')));
  if(!e.what||!diagnostic.includes(message(e.what))){if(failures.length<8)failures.push(e.id+'：错误描述不同（报告：'+e.what+'）');return null}
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
   if(!actual||!sameFrame(source[j].f,actual)){if(failures.length<8){const f=source[j].f;failures.push(e.id+'：帧 #'+source[j].i+' 不一致；报告 '+paths(f).join(' / ')+':'+f.line+' '+(f.fn||'')+'；现场 '+(actual?actual.file+':'+actual.line+' '+(actual.function||''):'缺少该帧'))}return null}
   result.set('0:'+source[j].i,actual);
  }
  return result;
 }
 const initLabels={defined:'已检查部分已初始化',undefined:'未初始化，显示值不可靠',partially_undefined:'部分未初始化，显示值不可靠',unaddressable:'内存不可访问',out_of_bounds:'索引越界，未读取越界元素',partial_check:'只检查了部分内容，其余未知',unknown:'初始化状态无法确定'};
 const stateStyles={normal:['已检查部分通过','#195943','#edf5ef'],uncertain:['不确定','#805000','#fff3d6'],abnormal:['异常','#9f3029','#fff0ec']};
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
 function parsedIndices(expression){
  const source=String(expression||'');if(source.length>4096||/["']/.test(source))return [];
  const rows=[];
  function scan(begin,end,depth=0){
   if(depth>16)return;
   let cursor=begin;
   while(cursor<end){
    const m=/[A-Za-z_$][\w$]*(?:(?:\s*(?:\.|->|::)\s*)[A-Za-z_$][\w$]*)*/.exec(source.slice(cursor,end));if(!m)break;
    const start=cursor+m.index;let pos=start+m[0].length;
    while(pos<end){
     while(pos<end&&/\s/.test(source[pos]))pos++;
     const member=/^(?:\.|->|::)\s*[A-Za-z_$][\w$]*/.exec(source.slice(pos,end));
     if(member){pos+=member[0].length;continue}
     if(source[pos]!=='[')break;
     const open=pos;let level=1;pos++;
     while(pos<end&&level){if(source[pos]==='[')level++;if(source[pos]===']')level--;pos++}
     if(level)break;
     scan(open+1,pos-1,depth+1);
     rows.push({expression:source.slice(start,pos),base_expression:source.slice(start,open).trim(),index_expression:source.slice(open+1,pos-1).trim()});
    }
    cursor=Math.max(pos,start+1);
   }
  }
  scan(0,source.length);return rows;
 }
 function indexLabel(access,owner,accesses,position){
  if(access.index_expression)return access;
  const key=s=>String(s||'').replace(/\s+/g,'');
  const all=parsedIndices(owner);
  // Legacy records used the complete expression for every access. Recover
  // labels only when the full traversal matches; partial captures stay unknown.
  if(accesses&&all.length===accesses.length&&accesses.every(a=>!a.index_expression&&(!a.expression||key(a.expression)===key(owner))))return {...access,...all[position]};
  const local=parsedIndices(access.expression||owner);
  const matching=local.filter(row=>key(row.expression)===key(access.expression||owner)&&(!access.base_expression||key(row.base_expression)===key(access.base_expression)));
  if(matching.length===1&&(local.length===1||!accesses||access.base_expression))return {...access,...matching[0]};
  return access;
 }
 function variableLines(v){
  const info=v.initialization||{},details=v.member_details;
  const lines=[];const indexAbnormal=(v.index_accesses||[]).some(a=>a.bounds&&a.actual_index!==null&&(a.actual_index<a.bounds[0]||a.actual_index>a.bounds[1]));
  const prefixAbnormal=(v.index_accesses||[]).some(a=>a.prefix_check?.ranges?.some(r=>['undefined','unaddressable','out_of_bounds'].includes(r.status)));
  const prefixUnknown=(v.index_accesses||[]).some(a=>a.prefix_check&&(!a.prefix_check.coverage_complete||a.prefix_check.ranges?.some(r=>['unknown','unchecked'].includes(r.status))));
  const valueState=variableState(info.observed_status||info.status,v.status,info.complete);
  const overall=indexAbnormal||prefixAbnormal||valueState==='abnormal'?'abnormal':prefixUnknown?'uncertain':valueState;
  lines.push({text:'\n'+v.name+(v.type?'（'+v.type+'）':'')+' · '+(v.role==='argument'?'函数参数':v.role==='source_expression'?'本行表达式逐项读取':'局部变量'),state:overall});
  if(v.macro_expansion)lines.push('当前源码位置的宏展开：'+v.macro_expansion);
  for(const [position,raw] of (v.index_accesses||[]).entries()){
   const access=indexLabel(raw,v.name,v.index_accesses,position);
   lines.push('下标 '+(access.index_expression||'无法唯一对应（旧现场缺少名称，请重新采集）')+' = '+(access.actual_index??'未知')+'；'+(access.base_expression||'该数组')+' 的下标范围 '+(access.bounds?access.bounds.join('～')+'（元素索引）':'未知（没有可确认的数组边界）'));
   if(access.prefix_check)lines.push({prefix:access.prefix_check,base:(access.base_expression||'该数组')+(access.prefix_check.member_path?'[*].'+access.prefix_check.member_path:''),index:access.actual_index,bounds:access.bounds});
  }
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
   add('p','变量查看器 v34 · '+(directChecks?'本行逐项检查 '+frame.line_checks.items.length+' 项'+(frame.line_checks.truncated?'（已达到 32 项上限）':''):'现场没有本行逐项检查记录；以下仅是作用域值的源码匹配，不代表已逐项检查')).className='meta';
   for(const note of [frame.note,...(frame.inspection_errors||[]),!frame.source_line?.text&&frame.source_line?.note].filter(Boolean))add('p','采集说明：'+note).className='meta';
   if(directChecks){const rows=frame.line_checks.items,read=rows.filter(v=>v.status==='available').length,unknown=rows.filter(v=>['unknown','partial_check'].includes(v.initialization?.status||'unknown')).length;
    add('p','读取成功 '+read+' / '+rows.length+' 项；未读到 '+(rows.length-read)+' 项；初始化检查不完整或未知 '+unknown+' 项。'+(frame.line_checks.truncated?'还有表达式未检查。':'')+'仅覆盖本行识别出的表达式，不代表查全所有变量，也不证明整次访问合法。').className='meta';}
   if(saved.replacedEmpty)add('p','首次现场没有读到本行变量，现展示后续首次读到值的完整现场；未混合多次停止的变量。').className='meta';
   if(['InvalidRead','InvalidWrite'].includes(e.kind))colorState(add('p','Valgrind 已确认本次'+(e.kind==='InvalidRead'?'读取':'写入')+'非法。变量已初始化不代表指针有效、索引合法或对象仍存活。'),'abnormal');
   add('p','所属报告错误：'+e.id+' · 采集现场：'+(saved.captureId||'旧数据未提供现场文件名')).className='meta';
   if(e.records>1)add('p','本项合并了 '+e.records+' 条原始错误记录；以下仅来自上面标明的一次现场，不合并不同现场的变量值。').className='meta';
   add('p','现场来源：'+(frame.function||'未知函数')+' · '+(frame.file||'未知文件')+':'+(frame.line||'?')+' · GDB 帧 #'+(frame.index??'?')).className='meta';
   add('p',saved.snapshot.captured_at||'本次运行').className='meta';
   const [si,fi]=node.dataset.frameKey.split(':').map(Number),recorded=e.stacks[si]?.frames[fi];
   const shownLine=recorded?.source?.find(row=>Number(row.number)===Number(recorded.line))?.text;
   const capturedLine=frame.source_line?.text;
   const header=frame.source_line?.control_header;
   const headerValid=header&&Number(header.start_line)<=Number(frame.line)&&Number(header.end_line)>=Number(frame.line)&&header.lines?.some(r=>Number(r.number)===Number(frame.line)&&r.text===capturedLine);
   const headerChanged=headerValid&&(recorded?.source||[]).some(r=>{const saved=header.lines.find(h=>Number(h.number)===Number(r.number));return saved&&saved.text.trim()!==r.text.trim()});
   const sourceChanged=headerChanged||(typeof shownLine==='string'&&typeof capturedLine==='string'&&shownLine.trim()!==capturedLine.trim());
   const identifiers=!sourceChanged?(headerValid?header.identifiers:frame.source_line?.identifiers)||'':'';
   // Preserve token boundaries: `return value` must not become `returnvalue`.
   // A member `ptr->value` is not the unrelated local variable `value`.
   const tokens=s=>String(s).match(/[A-Za-z_$][\w$]*|\d+|->|::|[^\s]/g)||[];
   const sourceTokens=s=>tokens(s.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g,' '));
   const code=sourceTokens(identifiers);
   const evidence=[code,...(headerValid?[header.text]:[capturedLine,shownLine]).filter(s=>typeof s==='string').map(sourceTokens)];
   // Require the name in both recorded association text and actual source.
   // Old/malformed metadata must never put a different line's locals here.
   const onLine=(name,leaf=false)=>{const needle=tokens(name);return needle.length>0&&evidence.every(parts=>parts.some((_,i)=>!['.','->','::'].includes(parts[i-1])&&needle.every((t,j)=>parts[i+j]===t)&&(!leaf||!['.','->','['].includes(parts[i+needle.length]))))};
   const relatedVariables=[];
   for(const original of (directChecks?frame.line_checks.items:frame.variables)||[]){
    if(!onLine(original.name))continue;
    const v={...original,initialization:{...original.initialization}};
    const members=v.member_details?.members||v.initialization.member_states?.filter(m=>m.name!=='$self');
    // Scalars (including scalar struct fields) have a $self initialization
    // entry. Filtering it produces [], which is truthy in JavaScript.
    const aggregate=!!v.member_details||(members?.length||0)>0||/^\s*[\{\[]/.test(String(v.value||''));
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
   if(headerValid){add('pre','采集时完整控制语句（第 '+header.start_line+'～'+header.end_line+' 行）：\n'+header.text).style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';add('p',header.note||'读取当前帧变量，不执行控制语句。').className='meta'}
   // Keep index-only findings in the same list, even when value reading failed.
   const expressionKey=name=>tokens(name).join(' ');
   for(const level of frame.index_analysis?.levels||[]){
    if(level.status!=='out_of_bounds'||!onLine(level.expression))continue;
    let variable=relatedVariables.find(v=>expressionKey(v.name)===expressionKey(level.expression));
    if(!variable){variable={name:level.expression,role:'source_expression',status:'unavailable',value:'未采集到此表达式的值',initialization:{status:'out_of_bounds'}};relatedVariables.push(variable)}
    variable.index_accesses=[...(variable.index_accesses||[])];
    if(!variable.index_accesses.some(a=>expressionKey(a.expression||variable.name)===expressionKey(level.expression)))variable.index_accesses.push(level);
   }
   add('h4',headerValid?'当前控制语句变量分析（含异常核查）':'本行变量分析（含异常核查）');
   const relevant=add('pre','');relevant.className='source';relevant.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;padding:12px';let relatedCount=0;
   for(const v of relatedVariables){
    if(!onLine(v.name))continue;
    const group=add('span','',relevant);group.dataset.variableGroup=v.name;group.style.cssText='display:block;padding:12px 0;border-bottom:1px solid var(--line);margin-bottom:8px';
    const details=v.member_details;
    const members=details?.members||v.initialization?.member_states?.filter(m=>m.name!=='$self');
    if(details||(members?.length||0)>0){
     let memberCount=0;
     for(const m of members||[]){const name=v.name+(m.name.startsWith('[')?'':'.')+m.name;if(!onLine(name))continue;const state=typeof m.initialization==='string'?m.initialization:(details?'unknown':m.status);const row=add('span',(relatedCount+1)+'. '+name+(m.type?'（'+m.type+'）':'')+' = '+(m.value??'旧现场未单独保存成员值')+'；'+(initLabels[state]||'初始化状态无法确定'),group);row.style.display='block';colorState(row,variableState(state,details?m.status:undefined,v.initialization?.complete));if(m.note){const note=add('span','说明：'+m.note,group);note.style.display='block'}memberCount++;relatedCount++}
     if(!memberCount){add('span',(relatedCount+1)+'. '+v.name+'：本行出现此对象，但没有可对应的成员值，不展开其他成员。\n',group);relatedCount++}
    }else{let first=true;for(const row of variableLines(v)){
     if(row.prefix){
      const p=row.prefix,box=add('details','',group);box.dataset.arrayPrefix='yes';
      if(!Array.isArray(p.requested)||p.requested[0]!==0||p.requested[1]!==row.index||(p.bounds&&row.bounds&&JSON.stringify(p.bounds)!==JSON.stringify(row.bounds))){
       box.open=true;add('summary',row.base+'：范围检查记录与当前下标不一致',box);
       colorState(add('p','当前下标 '+row.index+'，应检查 0～'+row.index+'；已保存范围 '+(p.requested||[]).join('～')+'。不采用这份范围结果，请重新执行步骤三。',box),'uncertain');continue;
      }
      add('summary',row.base+'：下标 '+p.requested.join('～')+' 范围检查（已检查 '+p.checked_elements+' 个元素；'+(p.coverage_complete?'范围扫描完成':'存在未检查范围')+'）',box);
      if(p.attempted_elements!==undefined)add('p','尝试检查 '+p.attempted_elements+' 项；成功核对 '+p.checked_elements+' 项；查询失败 '+(p.failed_elements||0)+' 项。',box);
      if(!p.coverage_complete)box.open=true;
      add('p','当前下标 '+row.index+'；应覆盖 0～'+row.index+'。下面每行是一段检查结果，不代表整个检查范围。',box);
      const labels={defined:'初始化及内存可访问性检查通过',undefined:'存在未初始化字节，值不可靠',unaddressable:'元素存储含不可访问内存',out_of_bounds:'超出数组边界，未读取',unknown:'布局或初始化状态无法确定',unchecked:'未检查，不能判断'};
      for(const r of p.ranges||[]){const line=add('p',r.start+'～'+r.end+'：'+(labels[r.status]||r.status),box);colorState(line,variableState(r.status))}
      if(p.reason)add('p',p.reason,box);add('p',p.note,box);continue;
     }
     const text=typeof row==='string'?row:row.text;const line=add('span',first?(relatedCount+1)+'. '+text.trimStart():text,group);first=false;line.style.display='block';if(row.state)colorState(line,row.state)
    }relatedCount++}
   }
   if(!relatedCount)relevant.textContent='未找到可与本行直接对应的已采集变量。宏、多行表达式、全局变量或缺少源码可能导致无法关联。';
   const legend=add('p','');legend.className='meta';
   for(const state of Object.keys(stateStyles)){const tag=add('span',' ',legend);colorState(tag,state);tag.style.marginRight='10px'}
   add('p','颜色仅表示已检查的初始化或索引状态；绿色不代表业务取值正确，也不证明指针指向的内存可访问。').className='meta';
   const lines=[];
   if(node.dataset.frameKey===saved.frames.keys().next().value){
    const memory=saved.snapshot.memory||{};
    if(memory.explanation)lines.push(memory.explanation);
    lines.push('本次访问的内存块参考（字节偏移，不是变量下标）');
    lines.push(memory.range_evidence_version===2?memory.range_explanation:(memory.range_explanation&&memory.block_bytes!==undefined?'旧现场未保存地址与内存块的对应依据，不采用旧的范围计算；请重新执行步骤三。':memory.range_explanation||'当前诊断未提供足够的内存块边界，无法确定合法访问范围。'));
    if(memory.range_evidence)lines.push('范围依据：'+memory.range_evidence);
   }
   if(lines.length)add('p',lines.join('\n')).style.whiteSpace='pre-wrap';
   const basis=add('details','');basis.dataset.captureEvidence='yes';
   add('summary','变量分析依据（点击展开）',basis);
   const proof=add('pre','',basis);proof.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';
   const evidenceLines=[
    '现场核对：'+(saved.snapshot.association?.note||'该现场已通过停止位置校验及报告匹配；旧数据未记录详细校验说明。'),
    '当前帧：#'+(frame.index??'?')+' '+(frame.function||'未知函数')+'；指令地址 '+(frame.pc||'未记录'),
    '变量来源：'+(directChecks?'在当前 GDB 帧逐项读取源码表达式。':'旧现场作用域变量与源码文本匹配，未逐项读取表达式。'),
    '初始化依据：有可用内存存储时读取 Valgrind 有效位；常量、寄存器和无法检查的对象按采集记录说明。未初始化值不可靠，初始化通过不证明访问合法。',
    '范围依据：固定数组下标来自调试类型；内存块字节范围来自对应地址诊断，不推断变量业务范围。',
    '匹配范围：'+(sourceChanged?'源码已变化，未展示变量值。':headerValid?'采集时的完整控制条件。':'当前源码行；仅文本对应，不证明该变量导致错误。')];
   for(const v of relatedVariables){const info=v.initialization||{},notes=[];
    if(info.address)notes.push('检查地址 '+info.address);
    if(info.checked_bytes!==undefined)notes.push('检查 '+info.checked_bytes+' / '+(info.size_bytes??'未知')+' 字节');
    if(info.complete!==undefined)notes.push(info.complete?'初始化检查范围完整':'初始化检查范围不完整');
    if(info.undefined_byte_offsets?.length)notes.push('未初始化字节偏移 '+info.undefined_byte_offsets.join(', '));
    if(info.unaddressable_byte_offsets?.length)notes.push('不可访问字节偏移 '+info.unaddressable_byte_offsets.join(', '));
    if(info.reason)notes.push(info.reason);
    evidenceLines.push(v.name+'（当前表达式自身，不是整个数组区间）：'+(notes.join('；')||'现场未提供逐字节检查依据，不能补推结论。'));
    for(const access of v.index_accesses||[]){
     const p=access.prefix_check,name=(access.base_expression||v.name)+(p?.member_path?'[*].'+p.member_path:'');
     if(!p){evidenceLines.push(name+' 数组区间：现场未保存区间检查依据，不能用当前元素的字节数推断整个区间已检查。');continue}
     if(!Array.isArray(p.requested)||p.requested[0]!==0||p.requested[1]!==access.actual_index||(p.bounds&&access.bounds&&JSON.stringify(p.bounds)!==JSON.stringify(access.bounds))){evidenceLines.push(name+' 数组区间：范围记录与当前下标不一致，不采用其检查依据。');continue}
     evidenceLines.push(name+' 数组区间 '+p.requested.join('～')+'：已检查 '+p.checked_elements+' 个元素；'+(p.coverage_complete?'范围扫描完成':'存在未检查范围'));
     if(p.evidence_version===1){
      evidenceLines.push('  每元素 '+(p.element_bytes??'未知')+' 字节；已分类检查 '+p.checked_bytes+' / '+(p.target_bytes??'未知')+' 字节；成功查询有效位 '+p.queried_bytes+' 字节（两者不相加）。');
      if(p.member_path)evidenceLines.push('  只查询成员 '+p.member_path+'；结构体步长 '+p.element_stride+' 字节。地址首尾之间的其他成员不在检查范围内。');
      if(p.checked_bytes>0)evidenceLines.push('  已检查地址 '+p.start_address+'～'+p.end_address+'；Valgrind get_vbits 成功查询 '+(p.queries||[]).length+' 次。');
     }else evidenceLines.push('  旧现场未保存区间字节数及地址依据；需要重新执行步骤三补齐，不从当前元素的检查信息推算。');
     if(p.reason)evidenceLines.push('  未完成原因：'+p.reason);
     for(const failure of p.query_errors||[])evidenceLines.push('  查询失败区间 '+failure.start+'～'+failure.end+'，地址 '+failure.address+'，长度 '+failure.bytes+' 字节：'+failure.reason+'；原始返回：'+(failure.response||'无返回内容'));
    }
   }
   proof.textContent=evidenceLines.join('\n');
   const analysis=[];
   if(frame.index_analysis?.levels?.length){
    analysis.push('嵌套索引逐级分析（源码现场推导）');
    for(const raw of frame.index_analysis.levels){
     const level=indexLabel(raw,raw.expression);
     if(!onLine(level.expression))continue;
     const bounds=level.bounds?level.bounds.join('～'):'未知';
     analysis.push({text:'下标 '+(level.index_expression||'无法唯一对应（旧现场缺少名称，请重新采集）')+' = '+(level.actual_index??'未知')+'；'+(level.base_expression||'该数组')+' 的下标范围 '+bounds+'；'+({in_bounds:'该下标在范围内',out_of_bounds:'该下标越界',unknown:'无法判断'}[level.status]||level.status)+(level.note?'（'+level.note+'）':''),state:variableState(level.status)});
    }
    analysis.push(frame.index_analysis.note);
   }
   if(analysis.length){const section=add('details','');add('summary','索引分析依据（点击展开）',section);const block=add('pre','',section);block.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';for(const row of analysis){if(!row)continue;const line=add('span',typeof row==='string'?row:row.text,block);line.style.display='block';if(row.state)colorState(line,row.state)}}
  }
 }
 const originalDetail=detail;detail=function(e){originalDetail(e);paint(e)};
 function entryHasVariables(saved){return [...saved.frames.values()].some(f=>(f.line_checks?.version===1?f.line_checks.items:f.variables||[]).some(v=>v.status==='available'||(!v.status&&v.value!==undefined&&v.value!==null)))}
 function hasVariables(id){return readableIds.has(id)}
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
  let unmatched=0,missing=0,ambiguous=0,unverified=0,changed=false;const diagnostics=[];
  const tally=result=>{unmatched+=result.unmatched||0;missing+=result.missing||0;ambiguous+=result.ambiguous||0;unverified+=result.unverified||0;if(result.note&&diagnostics.length<30)diagnostics.push(result.note)};
  if(data.replay_id&&data.replay_id!==activeReplay){
   activeReplay=data.replay_id;captures.clear();readableIds.clear();processed.clear();changed=true;
   for(const frame of document.getElementById('detail').children)for(const child of [...frame.children])if(child.dataset?.captureVariables==='yes')child.remove();
   for(const node of document.getElementById('list').children)for(const child of [...node.children])if(child.dataset?.captureBadge==='yes')child.remove();
  }
  for(const item of data.items){
   const cached=item.id&&item.revision&&processed.get(item.id);if(cached&&cached.revision===item.revision){tally(cached.result);continue}
   const finish=result=>{tally(result);if(item.id&&item.revision)processed.set(item.id,{revision:item.revision,result})};
   const snapshot=item.snapshot;if(!snapshot){finish({unmatched:1,missing:1,note:(item.id||'现场')+'：缺少现场数据'});continue}
   if(snapshot.association?.status!=='verified'){finish({unmatched:1,unverified:1,note:(item.id||'现场')+'：停止位置未核实；'+(snapshot.association?.note||'缺少校验信息')});continue}
   const candidates=new Set();for(const [depth,r] of (snapshot.frames||[]).entries())for(const e of index.get(depth+':'+normalize(r.file)+':'+Number(r.line))||[])candidates.add(e);
   const matches=[],failures=[];for(const e of candidates){const frames=match(e,snapshot,failures);if(frames)matches.push({e,frames})}
   if(matches.length!==1){finish({unmatched:1,ambiguous:matches.length>1?1:0,note:(item.id||'现场')+'：'+(matches.length>1?'多个候选 '+matches.slice(0,8).map(m=>m.e.id).join('、')+'；主错误栈无法唯一识别，不自动填入':failures.join('；')||'主错误栈首个有源码的帧未命中；检查源码路径、行号及帧层级')});continue}
   const {e,frames}=matches[0];const previous=captures.get(e.id);
   const readable=entry=>{const first=[...entry.frames.values()].find(f=>f.line_checks?.items?.length);const rows=first?first.line_checks.items:[...entry.frames.values()].flatMap(f=>f.variables||[]);return rows.some(v=>v.status==='available')};
   const candidate={snapshot,frames,captureId:item.id};
   if(!previous||(!readable(previous)&&readable(candidate))){candidate.replacedEmpty=!!previous;captures.set(e.id,candidate);if(entryHasVariables(candidate))readableIds.add(e.id);else readableIds.delete(e.id);changed=true}
   finish({});
  }
  const current=report.errors.find(e=>e.id===selected);if(current)paint(current);
  if(changed&&filter.checked)list();else markNavigation();
  running=data.live;
  loadedRevision=data.update_revision||null;
  status.textContent=(running?'采集中':'采集已结束')+' · 已关联 '+captures.size+' 个错误，其中 '+[...captures.keys()].filter(hasVariables).length+' 个已读出变量；'+unmatched+' 个现场未能唯一匹配（缺少现场数据 '+missing+'，停止位置未核实 '+unverified+'，多个候选 '+ambiguous+'，位置或错误描述不匹配 '+(unmatched-missing-ambiguous-unverified)+'）。原始现场保留在 captures 目录。'+(unverified?' 旧现场或未核实现场不会填入堆栈，请重新执行步骤三。':'');
  const kinds=new Map();for(const e of report.errors){if(!kinds.has(e.kind))kinds.set(e.kind,{total:0,read:0,empty:0});const group=kinds.get(e.kind);group.total++;if(hasVariables(e.id))group.read++;else if(captures.has(e.id))group.empty++}
  status.style.whiteSpace='pre-line';
  for(const [kind,g] of kinds)status.textContent+='\n'+kind+'：已读值 '+g.read+'/'+g.total+'，已关联但无可展示值 '+g.empty+'，尚未关联 '+(g.total-g.read-g.empty)+'。';
  status.textContent+='\n尚未关联不等于没有变量：可能未重现、停止位置未核实或堆栈/描述不匹配，不能仅凭报告推断原因。';
  if([...kinds.keys()].some(k=>k.startsWith('Leak_')))status.textContent+='\n泄漏/仍可访问记录保存的是历史分配栈；检测泄漏时该调用可能已经返回，不能把退出现场的变量当成分配时的值。';
  unmatchedDetails.hidden=!diagnostics.length;const diagnosticText='未关联依据（最多展示 30 个现场）：\n'+diagnostics.join('\n');if(unmatchedText.textContent!==diagnosticText)unmatchedText.textContent=diagnosticText;
 };
 window.aivCaptureCheck=data=>{probeRevision=data.revision};
 function loadUpdateScript(name,done){
  const script=document.createElement('script');script.src=name+'?t='+Date.now();let finished=false;
  const finish=ok=>{if(finished)return;finished=true;clearTimeout(deadline);script.remove();done(ok)};
  const deadline=setTimeout(()=>finish(false),10000);script.onload=()=>finish(true);script.onerror=()=>finish(false);document.head.append(script);
 }
 function poll(){
  if(!running)return;probeRevision=null;
  const next=()=>{if(running)timer=setTimeout(poll,2000)};
  loadUpdateScript('capture-progress.js',ok=>{
   if(ok&&probeRevision&&probeRevision===loadedRevision){next();return}
   loadUpdateScript('capture-updates.js',loaded=>{if(!loaded)status.textContent='无法加载 capture-updates.js，请将它与 full-report.html 放在同一目录。正在重试…';next()});
  });
 }
 poll();
})();
</script>
'''
