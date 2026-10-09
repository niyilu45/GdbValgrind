# -*- coding: utf-8 -*-
"""Self-contained offline report template."""

HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AiValgrind · 内存问题报告</title>
<style>
:root{color-scheme:light;--ink:#202b29;--muted:#586863;--line:#d9e0dc;--paper:#fff;--bg:#f3f5f1;--accent:#195943;--selected:#e4efe7;--danger:#9f3029}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}button,input,select{font:inherit}button{cursor:pointer}button:disabled{cursor:not-allowed;opacity:.55}button:focus-visible,a:focus-visible,summary:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid #247958;outline-offset:3px}a{color:var(--accent)}
header{padding:22px 30px;border-bottom:1px solid var(--line);background:var(--paper);display:flex;align-items:center;justify-content:space-between;gap:20px}h1{font-size:23px;margin:0;font-weight:650}h1 small{font-size:14px;color:var(--muted);font-weight:400;margin-left:16px}header p{margin:4px 0 0;color:var(--muted);font-size:13px;overflow-wrap:anywhere}.mode{color:var(--accent);white-space:nowrap}.layout{display:grid;grid-template-columns:220px 320px minmax(0,1fr);min-height:calc(100vh - 103px)}
nav{padding:24px 16px;border-right:1px solid var(--line)}nav h2{font-size:13px;color:var(--muted);font-weight:500;margin:0 10px 12px}nav button{border:0;background:transparent;width:100%;padding:10px 12px;text-align:left;display:flex;justify-content:space-between;gap:8px;color:var(--ink);border-radius:5px;font-size:13px}nav button:hover,.issue:hover{background:#ecf0e9}nav button[aria-current=true]{background:var(--accent);color:white}nav button.danger-kind{color:var(--danger);font-weight:600}nav button.danger-kind[aria-current=true]{background:#fce2d7;color:var(--danger)}nav button span:first-child{overflow-wrap:anywhere}nav .note{margin:28px 10px;font-size:12px;color:var(--muted)}
.issues{border-right:1px solid var(--line);background:#fafbf8;min-width:0}.search{padding:22px 18px 14px;border-bottom:1px solid var(--line)}label{display:block;font-size:13px;margin-bottom:6px;color:var(--muted)}input{width:100%;border:1px solid #a5b4ac;border-radius:4px;padding:9px 10px;background:white;color:var(--ink)}#listCount{font-size:12px;color:var(--muted);margin:10px 0 0}.issue{display:block;width:100%;border:0;border-bottom:1px solid var(--line);background:transparent;padding:18px;text-align:left;color:var(--ink)}.issue[aria-current=true]{background:var(--selected);box-shadow:inset 0 0 0 1px #8fac99}.issue strong{display:block;font-size:14px;margin:5px 0}.issue small{display:block;font:12px/1.6 ui-monospace,Consolas,monospace;overflow-wrap:anywhere;color:var(--muted)}.issue .type{display:flex;justify-content:space-between;gap:12px;font-size:11px;color:var(--accent)}
main{padding:28px 32px 60px;min-width:0;background:var(--paper)}h2{font-size:24px;line-height:1.35;margin:12px 0;overflow-wrap:anywhere}h3{font-size:16px;margin:30px 0 12px}.meta{font-size:12px;color:var(--muted);overflow-wrap:anywhere}.kind{color:var(--danger);font-size:13px;font-weight:600}.summary{margin:10px 0 20px;color:var(--muted)}.debug-box{padding:18px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.debug-box p{font-size:13px;color:var(--muted);margin:8px 0}.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap}select{max-width:100%;min-width:0;flex:1;padding:9px;border:1px solid #a5b4ac;border-radius:4px;background:white;color:var(--ink)}.primary{border:0;border-radius:4px;background:var(--accent);color:white;padding:11px 18px;white-space:nowrap}.primary:hover{background:#104330}.secondary{border:1px solid #a5b4ac;border-radius:4px;background:white;color:var(--ink);padding:8px 12px}textarea{width:100%;resize:vertical;min-height:88px;padding:12px;font:12px/1.7 ui-monospace,Consolas,monospace;background:var(--bg);color:var(--ink);border:1px solid var(--line);margin:10px 0}details{border-bottom:1px solid var(--line)}summary{cursor:pointer;padding:13px 0;font:13px/1.5 ui-monospace,Consolas,monospace;overflow-wrap:anywhere}summary small{font-size:12px;color:var(--muted)}.source{overflow:auto;background:#f5f7f2;padding:10px 0;margin:0 0 16px;border:1px solid var(--line);font:12px/1.8 ui-monospace,Consolas,monospace;tab-size:4}.line{display:block;white-space:pre;min-width:max-content;padding-right:18px}.line b{display:inline-block;width:58px;padding-right:14px;text-align:right;font-weight:400;color:var(--muted);user-select:none}.line.hit{background:#fce2d7;color:#762c20}.line.hit b{color:#762c20;font-weight:700}.empty{padding:40px 20px;color:var(--muted);text-align:center}.notice{background:#fff2d7;color:#694910;padding:10px 18px;font-size:13px}.status{margin:12px 0;color:var(--accent);font-size:13px;min-height:22px}.mobile-link{display:none}.stack-label{overflow-wrap:anywhere}code{font-family:ui-monospace,Consolas,monospace}
@media(min-width:1100px){nav{position:sticky;top:0;height:100vh;overflow:auto}.issues{max-height:calc(100vh - 100px);overflow:auto;position:sticky;top:0}}
@media(max-width:1099px){.layout{grid-template-columns:190px minmax(0,1fr)}.issues{border-right:0}main{grid-column:1/-1;border-top:1px solid var(--line)}#list{max-height:350px;overflow:auto}.mobile-link{display:block;margin:10px 0;font-size:13px}}
@media(max-width:600px){header{padding:18px;display:block}h1 small{display:block;margin:3px 0}.mode{margin-top:8px;font-size:13px}.layout{display:block}nav{border:0;padding:16px}#nav{display:flex;gap:6px;overflow:auto}nav button{width:auto;flex-shrink:0;gap:12px}nav .note{display:none}nav h2{margin-left:0}.search{padding-top:5px}main{padding:22px 18px 40px}h2{font-size:21px}.controls{align-items:stretch;flex-direction:column}select{width:100%;flex:auto}}
</style></head><body>
<header><div><h1>AiValgrind <small>内存问题报告</small></h1><p id="file"></p></div><div class="mode" id="mode"></div></header>
<div id="notice"></div><div class="layout"><nav aria-label="内存问题类型"><h2>问题类型</h2><div id="nav"></div><p class="note">相同错误类型及调用路径已合并。次数来自 XML 中的 errorcounts；缺失时按记录计数。</p></nav>
<section class="issues" aria-label="错误列表"><div class="search"><label for="search">搜索错误、函数或文件</label><input id="search" type="search" placeholder="例如 InvalidRead、main.c"><label for="fileFilter">筛选源文件（文件名或路径）</label><input id="fileFilter" type="search" placeholder="例如 src/main.c"><button id="resetFilters" class="secondary">清除全部筛选</button><p id="listCount" aria-live="polite"></p></div><div id="list"></div></section>
<main id="detail" tabindex="-1"><div class="empty">选择一条错误，查看调用栈与源码。</div></main></div>
<script id="report-data" type="application/json">__REPORT_DATA__</script>
<script>
'use strict';
let report=JSON.parse(document.getElementById('report-data').textContent);
let countPrefix=report.counts_complete===false?'至少 ':'';
const $=id=>document.getElementById(id);
const labels={InvalidRead:'非法读取',InvalidWrite:'非法写入',InvalidFree:'非法释放',MismatchedFree:'释放方式不匹配',UninitCondition:'未初始化条件',UninitValue:'未初始化值',Overlap:'内存区域重叠',SyscallParam:'系统调用参数',Leak_DefinitelyLost:'确定泄漏',Leak_IndirectlyLost:'间接泄漏',Leak_PossiblyLost:'可能泄漏',Leak_StillReachable:'仍可访问',FishyValue:'可疑参数值'};
let kind='',selected=null,busy=false,visibleLimit=100;
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n};
const typeName=k=>labels[k]||k;
const position=f=>f.file?f.file+(f.line?':'+f.line:''):(f.fn||f.obj||'无符号位置');
const shellQuote=s=>/^[A-Za-z0-9_@%+=:,./-]+$/.test(s)?s:'"'+s.replace(/[\\"$`]/g,'\\$&')+'"';
const normalize=s=>String(s||'').replace(/\\/g,'/').toLowerCase();
let indexed=[],kindCounts=new Map();
function indexReport(){indexed=report.errors.map((e,index)=>{const frames=e.stacks.flatMap(s=>s.frames);const files=frames.flatMap(f=>[f.file,f.local_file,f.file?(f.dir?f.dir.replace(/[\\/]$/,'')+'/':'')+f.file:'']).filter(Boolean);return {e,index,files:files.map(normalize),text:normalize([e.kind,typeName(e.kind),e.what,e.id,...files,...frames.flatMap(f=>[f.fn,f.obj])].join(' '))}});kindCounts=new Map();for(const e of report.errors)kindCounts.set(e.kind,(kindCounts.get(e.kind)||0)+1)}
indexReport();
let filtered=[];
function selectError(e){selected=e.id;for(const b of $('list').children)if(b.dataset.errorId)b.setAttribute('aria-current',String(b.dataset.errorId===selected));detail(e);try{history.replaceState(null,'','#'+e.id)}catch{}if(innerWidth<1100)$('detail').scrollIntoView({behavior:'auto'})}
function nav(){
 $('nav').replaceChildren();
 const kinds=['',...new Set(report.errors.map(e=>e.kind))];
 for(const k of kinds){const b=el('button');if(['InvalidRead','InvalidWrite','Leak_DefinitelyLost'].includes(k))b.className='danger-kind';b.append(el('span',k?typeName(k):'全部问题'),el('span',String(k?kindCounts.get(k):report.errors.length)));b.setAttribute('aria-current',String(k===kind));b.onclick=()=>{kind=k;nav();list()};$('nav').append(b)}
}
function openSourceFile(frame){
 const file=report.source_files?.[frame.source_file_id];if(!file)return;
 const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const color=line=>{let end=0,out='';const tokens=/\/\/.*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b(?:if|else|for|while|return|struct|class|const|static|void|int|char|float|double|unsigned|sizeof|switch|case|break|include|define|nullptr|NULL|true|false)\b|\b(?:0x[\da-fA-F]+|\d+)\b/g;for(const m of line.matchAll(tokens)){out+=escape(line.slice(end,m.index));const cls=m[0].startsWith('//')?'comment':/^["']/.test(m[0])?'string':/^\d/.test(m[0])?'number':'keyword';out+='<span class="'+cls+'">'+escape(m[0])+'</span>';end=m.index+m[0].length}return out+escape(line.slice(end))};
 const rows=file.lines.map((text,i)=>'<tr id="L'+(i+1)+'"'+(i+1===Number(frame.line)?' class="hit"':'')+'><td class="num"><a href="#L'+(i+1)+'">'+(i+1)+'</a></td><td class="code">'+color(text)+'</td></tr>').join('');
 const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escape(file.path)+' · 源码</title><style>body{margin:0;color:#1f2328;background:#fff;font:14px/1.5 system-ui,sans-serif}header{padding:16px 24px;background:#f6f8fa;border-bottom:1px solid #d1d9e0;overflow-wrap:anywhere}header p{margin:6px 0;color:#59636e;font-size:12px}.file{margin:24px;border:1px solid #d1d9e0;border-radius:6px;overflow:auto}table{border-collapse:collapse;width:100%;font:12px/20px ui-monospace,SFMono-Regular,Consolas,monospace}td{vertical-align:top}.num{width:1%;min-width:64px;text-align:right;padding:0 16px;user-select:none}.num a{color:#59636e;text-decoration:none}.num a:hover{text-decoration:underline}.code{white-space:pre;padding:0 16px 0 0;tab-size:4}.hit,:target{background:#fff8c5}.keyword{color:#cf222e}.string{color:#0a3069}.number{color:#0550ae}.comment{color:#6e7781}@media(max-width:600px){.file{margin:8px}header{padding:12px}}</style><header><strong>'+escape(file.path)+'</strong><p>'+file.lines.length+' 行 · 报告生成时的源码快照 · 高亮行 '+escape(frame.line||'未知')+' · 点击行号定位</p></header><div class="file"><table>'+rows+'</table></div></html>';
 const url=URL.createObjectURL(new Blob([html],{type:'text/html;charset=utf-8'}));
 window.open(url+'#L'+Number(frame.line),'_blank','noopener,noreferrer');setTimeout(()=>URL.revokeObjectURL(url),60000);
}
function list(reset=true){
 if(reset){visibleLimit=100;const query=normalize($('search').value.trim()),file=normalize($('fileFilter').value.trim());filtered=indexed.filter(row=>(!kind||row.e.kind===kind)&&row.text.includes(query)&&(!file||row.files.some(path=>path.includes(file))));}
 const errors=filtered.map(row=>row.e);
 $('listCount').textContent='共 '+report.errors.length+' 个错误位置 · 筛选后 '+errors.length+' 个 · 已显示 '+Math.min(visibleLimit,errors.length)+' 个 · '+countPrefix+errors.reduce((n,e)=>n+e.count,0)+' 次记录';$('list').replaceChildren();
 for(const {e,index} of filtered.slice(0,visibleLimit)){const b=el('button',undefined,'issue');b.dataset.errorId=e.id;b.setAttribute('aria-current',String(e.id===selected));const t=el('span',undefined,'type');t.append(el('span','第 '+(index+1)+' 条 · '+e.kind),el('span','× '+countPrefix+e.count));b.append(t,el('strong',e.what));const frames=e.stacks[0]?.frames||[];const f=frames.find(f=>f.local_file)||frames.find(f=>f.file)||frames[0];b.append(el('small',f?position(f):'无调用栈'));b.onclick=()=>selectError(e);$('list').append(b)}
 if(errors.length>visibleLimit){const more=el('button','显示后 100 条','secondary');more.onclick=()=>{visibleLimit+=100;list(false)};$('list').append(more)}
 if(!errors.length)$('list').append(el('div',report.errors.length?'没有匹配项，试试其他类型或关键词。':'XML 中没有内存错误。','empty'));
 if(selected&&!errors.some(e=>e.id===selected)){selected=null;$('detail').replaceChildren(el('div','选择当前列表中的错误以查看详情。','empty'))}
}
function detail(e){
 const main=$('detail');main.replaceChildren();main.append(el('div',typeName(e.kind)+' / '+e.kind,'kind'),el('h2',e.what),el('div','错误 ID '+e.id,'meta'));
 main.append(el('p',countPrefix+e.count+' 次记录 · 合并 '+e.records+' 条 XML 错误'+(e.kind.startsWith('Leak_')?' · '+e.leaked_bytes+' 字节 / '+e.leaked_blocks+' 个块':''),'summary'));
 const box=el('section',undefined,'debug-box');const label=el('label',report.auto_values?'自动采集位置':'复现断点');label.htmlFor='frame';box.append(label);const controls=el('div',undefined,'controls'),select=el('select');select.id='frame';select.append(new Option(report.auto_values?'在实际内存错误处暂停并提取变量':'自动选择错误栈中的工程源码位置',''));select.disabled=!!report.auto_values;
 e.stacks.forEach((s,si)=>s.frames.forEach((f,fi)=>{if((f.file&&Number(f.line)>0)||f.fn)select.append(new Option(si+':'+fi+' · '+position(f)+' · '+(f.fn||''),si+':'+fi))}));
 const start=el('button',report.token?(report.auto_values?'启动并采集变量':'在终端启动 GDB'):'生成调试命令','primary');start.id='startDebug';start.disabled=busy;controls.append(select,start);box.append(controls);
 box.append(el('p',report.auto_values?'重新运行到实际内存错误，自动保存当前线程的参数、局部变量和内存诊断。同一问题只保留首次现场，结果保存在服务端采集目录；实际错误可能不同于选中的历史记录。':(e.kind.startsWith('Leak_')?'将重新运行程序，在分配调用路径设置断点。泄漏通常在退出时检测，不能从 XML 恢复旧现场。':'将重新运行程序并在源码位置设置断点。相同行可能在错误发生前多次执行，请在 GDB 中继续或设置条件断点。')));
 if(report.token)box.append(el('p','GDB 在启动本服务的 SSH 终端中交互。退出 GDB 后可调试下一条错误。'));
 const status=el('div','','status');status.id='status';status.setAttribute('role','status');box.append(status);
 box.append(el('p','启动命令：粘贴到 Linux / SSH 终端的普通 Shell 中执行，不要粘贴到 (gdb)。'));
 const command=el('textarea');command.readOnly=true;command.setAttribute('aria-label','终端调试命令');command.hidden=true;const copy=el('button','复制命令','secondary');copy.hidden=true;
 const metadata=report.debug_command;
 const updateCommand=()=>{if(!metadata?.ready){command.value='';return}const args=[...metadata.base_args,'--error',e.id,...(select.value?['--frame',select.value]:[]),'--',...metadata.target_args];command.value=args.map(shellQuote).join(' ');};
 const resumeLabel=el('p','已有调试会话：复制下面的命令到当前 (gdb) 提示符中执行。');
 const resume=el('textarea');resume.readOnly=true;resume.setAttribute('aria-label','当前 GDB 会话继续命令');
 const updateResume=()=>{resume.value='aiv-goto '+e.id+(select.value?' '+select.value:'')};updateResume();
 const copyResume=el('button','复制继续命令','secondary');
 copyResume.onclick=async()=>{try{await navigator.clipboard.writeText(resume.value);status.textContent='继续命令已复制，请粘贴到当前 (gdb) 提示符。'}catch{resume.focus();resume.select();status.textContent='请按 Ctrl+C 复制继续命令。'}};
 select.onchange=()=>{updateCommand();updateResume()};
 copy.onclick=async()=>{try{await navigator.clipboard.writeText(command.value);status.textContent='命令已复制，工程目录和原程序参数已自动带入。'}catch{command.focus();command.select();status.textContent='请按 Ctrl+C 复制选中的命令。'}};
 start.onclick=async()=>{
  if(!report.token){updateCommand();command.hidden=!metadata?.ready;copy.hidden=!metadata?.ready;status.textContent=metadata?(metadata.note+' '+metadata.cwd_note):'此报告没有调试路径信息，请用新版脚本重新生成报告。';return}
  start.disabled=true;status.textContent='正在提交调试…';
  try{const r=await fetch('/api/debug',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Token':report.token},body:JSON.stringify({id:e.id,frame:select.value||null})});const data=await r.json();status.textContent=data.message;busy=r.ok||r.status===409;start.disabled=busy}catch{status.textContent='服务连接失败，请检查 SSH 转发和服务进程。';start.disabled=false}
 };
 box.append(command,copy,resumeLabel,resume,copyResume,el('p','继续命令定位到所选源码位置。已到达的位置或报告顺序早于当前位置时，会询问是否重跑：y 重新运行，n 忽略。报告顺序不保证本次执行顺序；其他断点或信号可能提前暂停。命令仅适用于新版脚本启动的同一报告会话。'));main.append(box);
 if(!e.stacks.length)main.append(el('p','此错误没有调用栈；建议使用 -g 编译后重新采集。'));
 e.stacks.forEach((s,si)=>{main.append(el('h3',s.label,'stack-label'));s.frames.forEach((f,fi)=>{const d=el('details');d.open=!!f.source&&fi===s.frames.findIndex(x=>x.source);const summary=el('summary');summary.append(el('span','#'+si+':'+fi+'  '+(f.fn||'未知函数')+'  '),el('small',position(f)));d.append(summary);if(report.source_files?.[f.source_file_id]){const view=el('button','查看代码文件 ↗','secondary');view.onclick=()=>openSourceFile(f);d.append(view)}if(f.obj)d.append(el('p',f.obj,'meta'));if(f.source){let loaded=false;const loadSource=()=>{if(loaded||!d.open)return;loaded=true;const pre=el('pre',undefined,'source');for(const row of f.source){const line=el('span',undefined,'line'+(row.number===Number(f.line)?' hit':''));line.append(el('b',String(row.number)),document.createTextNode(row.text));pre.append(line)}d.append(pre)};d.ontoggle=loadSource;loadSource()}else d.append(el('p',f.source_note||'无源码信息','meta'));main.append(d)})});
}
$('file').textContent=report.xml;
$('mode').textContent=report.token?(report.auto_values?'本地服务 · 自动采集首次错误现场':'本地服务 · 可联合调试'):'离线报告 · 可导出调试命令';
if(!report.live){$('notice').append(el('div','当前是报告快照，不会实时更新。查看采集进度请打开 collect / analyze 终端打印的 HTTP 实时报告链接，并保持采集运行；直接打开 HTML 文件或使用 browse 仅查看快照。','notice'))}
if(!report.finished||report.fatal){const n=el('div',(report.fatal?'目标程序收到 '+report.fatal+'。 ':'')+(!report.finished?'报告不完整：仅恢复已完整写出的错误；次数为下限，尚未写出的错误及退出时泄漏信息可能缺失。':''),'notice');$('notice').append(n)}
$('search').oninput=()=>list();$('fileFilter').oninput=()=>list();
$('resetFilters').onclick=()=>{kind='';$('search').value='';$('fileFilter').value='';nav();list()};
nav();list();const first=report.errors.find(e=>e.id===location.hash.slice(1))||report.errors[0];if(first){selectError(first)}
if(report.token)setInterval(async()=>{try{const r=await fetch('/api/status',{headers:{'X-Debug-Token':report.token}});if(!r.ok)throw Error();const s=await r.json();busy=s.busy;if($('startDebug'))$('startDebug').disabled=busy;if($('status'))$('status').textContent=s.message}catch{if($('status'))$('status').textContent='服务已断开，请检查 SSH 连接。'}},2000);
if(report.live){
 let revision=-1,refreshTimer=null,refreshing=false,syncCount=0,lastChange='尚未收到新错误';
 const liveNotice=el('div','正在采集：约每 2 秒更新已完整写入的错误，次数暂为下限。','notice');$('notice').replaceChildren(liveNotice);
 const refreshButton=el('button','立即刷新','secondary');$('notice').append(refreshButton);
 $('mode').textContent='实时采集 · 网页只读';
 const refresh=async()=>{
  if(refreshing)return;refreshing=true;refreshButton.disabled=true;clearTimeout(refreshTimer);
  let timeout=null;
  try{
   const controller=new AbortController();timeout=setTimeout(()=>controller.abort(),5000);
   const response=await fetch('/api/live?revision='+revision,{signal:controller.signal,cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);
   const snapshot=await response.json();
   if(snapshot.report){
    const previous=selected?report.errors.find(e=>e.id===selected):null;
    const limit=visibleLimit;report=snapshot.report;countPrefix=report.counts_complete===false?'至少 ':'';indexReport();nav();list();
    if(limit>visibleLimit){visibleLimit=limit;list(false)}
    const current=selected?report.errors.find(e=>e.id===selected):null;
    if(current&&JSON.stringify(previous)!==JSON.stringify(current))detail(current);
    if(!selected&&filtered.length)selectError(filtered[0].e);
    lastChange=new Date().toLocaleTimeString();
   }
   revision=snapshot.revision;syncCount++;
   liveNotice.textContent=(report.collection_state==='running'?'采集中 · 连接正常':'采集已结束')+' · 最近检查 '+new Date().toLocaleTimeString()+'（第 '+syncCount+' 次） · 数据更新 '+lastChange+' · 全部 '+report.errors.length+' 个错误位置。相同错误已去重，重复次数可能到程序结束才更新；筛选可能隐藏新错误。';
  }catch(error){liveNotice.textContent='实时更新失败：'+(error.message||String(error))+'。请检查采集是否仍在运行、SSH 转发是否保持连接，以及是否打开了终端打印的实时链接。保留当前结果，2 秒后重试；最终结果在采集目录 report.html。'}
  finally{clearTimeout(timeout);refreshing=false;refreshButton.disabled=false;refreshTimer=setTimeout(refresh,2000)}
 };refreshButton.onclick=refresh;refresh();
}
</script></body></html>'''
