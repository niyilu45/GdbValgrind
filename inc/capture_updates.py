"""Attach replay variables to unambiguously matched primary stack frames."""

SCRIPT = r'''
<script>
// aiv-stack-captures-v2
(()=>{
 let running=true,timer;const captures=new Map();
 const status=document.getElementById('capture-status');
 const normalize=s=>String(s||'').replace(/\\/g,'/');
 const location=f=>normalize(f.local_file||((f.dir?f.dir+'/':'')+(f.file||'')))+':'+Number(f.line);
 const index=new Map();for(const e of report.errors){const f=e.stacks[0]?.frames[0];if(f){const key=location(f);if(!index.has(key))index.set(key,[]);index.get(key).push(e)}}
 const message=s=>String(s||'').replace(/0x[0-9a-f]+/gi,'<address>').replace(/\s+/g,' ').trim().toLowerCase();
 function sameFrame(f,r){
  const file=normalize(f.local_file||((f.dir?f.dir+'/':'')+(f.file||'')));
  return !!file&&file===normalize(r.file)&&Number(f.line)>0&&Number(f.line)===Number(r.line)&&(!f.fn||f.fn===r.function);
 }
 function match(e,snapshot){
  if(!snapshot.valgrind_error||!message(snapshot.valgrind_error).includes(message(e.what))||!e.what)return null;
  const frames=e.stacks[0]?.frames||[],runtime=snapshot.frames||[];
  if(!frames.length||!runtime.length)return null;
  // Require the full recorded primary stack (within the 16-frame capture limit).
  const n=Math.min(frames.length,16);
  if(runtime.length<n)return null;
  const result=new Map();
  for(let i=0;i<n;i++){if(!sameFrame(frames[i],runtime[i]))return null;result.set('0:'+i,runtime[i])}
  return result;
 }
 function paint(e){
  const saved=captures.get(e.id);if(!saved)return;
  for(const node of document.getElementById('detail').children){
   const frame=saved.frames.get(node.dataset?.frameKey);if(!frame)continue;
   let panel=[...node.children].find(n=>n.dataset?.captureVariables==='yes');
   if(!panel){panel=document.createElement('pre');panel.className='source';panel.dataset.captureVariables='yes';panel.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';node.append(panel)}
   const lines=['步骤三实际变量（'+(saved.snapshot.captured_at||'本次运行')+'）'];
   for(const v of frame.variables||[])lines.push(v.name+' = '+v.value+(v.initialization?'\n初始化信息：'+JSON.stringify(v.initialization):''));
   if(!(frame.variables||[]).length)lines.push(frame.note||'此帧没有可读取的变量');
   const text=lines.join('\n');if(panel.textContent!==text)panel.textContent=text;
  }
 }
 const originalDetail=detail;detail=function(e){originalDetail(e);paint(e)};
 window.aivCaptureUpdate=data=>{
  let unmatched=0;
  for(const item of data.items){
   const snapshot=item.snapshot;if(!snapshot){unmatched++;continue}
   const first=snapshot.frames?.[0],candidates=first?(index.get(normalize(first.file)+':'+Number(first.line))||[]):[];
   const matches=[];for(const e of candidates){const frames=match(e,snapshot);if(frames)matches.push({e,frames})}
   if(matches.length!==1){unmatched++;continue}
   const {e,frames}=matches[0];if(!captures.has(e.id))captures.set(e.id,{snapshot,frames});
  }
  const current=report.errors.find(e=>e.id===selected);if(current)paint(current);
  running=data.live;
  status.textContent=(running?'采集中':'采集已结束')+' · 已关联 '+captures.size+' 个错误；'+unmatched+' 个现场未能唯一匹配，未填入堆栈。原始现场保留在 captures 目录。';
 };
 function poll(){
  if(!running)return;
  const script=document.createElement('script');script.src='capture-updates.js?t='+Date.now();
  let finished=false;const finish=()=>{if(finished)return;finished=true;clearTimeout(timer);script.remove();if(running)timer=setTimeout(poll,2000)};
  script.onload=finish;script.onerror=finish;timer=setTimeout(finish,10000);document.head.append(script);
 }
 poll();
})();
</script>
'''
