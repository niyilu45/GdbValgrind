"""Paging controller layered over the existing report detail renderer."""
SCRIPT = r'''
if(report.paged){
 let offset=0,loading=false,requestId=0,detailId=0,detailReady=false,timer;
 $('mode').textContent='实时采集 · SQLite 分页';
 const banner=el('div','错误增量入库，源码与 blame 在后台加载。','notice');$('notice').replaceChildren(banner);
 const paging=el('div',undefined,'controls'),prev=el('button','上一页','secondary'),next=el('button','下一页','secondary');paging.append(prev,next);$('list').after(paging);
 async function loadDetail(id){
  const version=++detailId;selected=id;
  try{const response=await fetch('/api/issue?id='+encodeURIComponent(id),{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);const data=await response.json();if(version!==detailId||id!==selected)return;
   const value=restoreSources(data.report);report.source_files=value.source_files;report.source_access=value.source_access;detailReady=data.ready;detail(value.errors[0]);
   if(!data.ready)$('detail').prepend(el('p','源码和 blame 加载中；错误信息已可查看。','notice'));
  }catch(error){if(version===detailId)$('detail').replaceChildren(el('p','详情加载失败：'+error.message,'notice'))}
 }
 async function loadPage(reset=false){
  if(reset)offset=0;
  const version=++requestId,params=new URLSearchParams({offset:String(offset),kind,q:$('search').value.trim(),file:$('fileFilter').value.trim(),author:$('authorFilter').value});
  try{const response=await fetch('/api/issues?'+params,{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);const data=await response.json();if(version!==requestId)return;
   if(offset>=data.total&&offset>0){offset=Math.max(0,Math.floor((data.total-1)/100)*100);return loadPage()}
   const current=$('authorFilter').value;const options=[new Option('全部作者 ('+data.authors.reduce((n,a)=>n+a.n,0)+')','')];
   for(const a of data.authors)options.push(new Option((a.author||'无作者信息')+' ('+a.n+')',a.author?'author:'+a.author:'missing'));
   if(current&&!options.some(o=>o.value===current))options.push(new Option((current==='missing'?'无作者信息':current.slice(7))+' (0)',current));
   $('authorFilter').replaceChildren(...options);$('authorFilter').value=current;
   $('nav').replaceChildren();const total=Object.values(data.totals).reduce((a,b)=>a+b,0),matched=Object.values(data.kinds).reduce((a,b)=>a+b,0);
   for(const k of ['',...Object.keys(data.totals)]){const b=el('button');if(['InvalidRead','InvalidWrite','Leak_DefinitelyLost'].includes(k))b.className='danger-kind';b.append(el('span',k?typeName(k):'全部问题'),el('span',(k?(data.kinds[k]||0):matched)+'/'+(k?data.totals[k]:total),'type-count'));b.setAttribute('aria-current',String(kind===k));b.onclick=()=>{kind=k;loadPage(true)};$('nav').append(b)}
   $('list').replaceChildren();for(const e of data.errors){const b=el('button',undefined,'issue');b.dataset.errorId=e.id;b.setAttribute('aria-current',String(e.id===selected));b.append(el('small','第 '+e.seq+' 条 · '+typeName(e.kind)+' · '+e.count+' 次'),el('strong',e.what));b.onclick=()=>{detailReady=false;loadDetail(e.id)};$('list').append(b)}
   if(!data.errors.length)$('list').append(el('div','没有匹配的问题。','empty'));
   $('listCount').textContent='筛选后 '+data.total+' / 全部 '+total+' 个位置 · 本页 '+data.errors.length+' 条';prev.disabled=offset===0;next.disabled=offset+100>=data.total;
   banner.textContent='状态：'+({running:'采集中',finished:'已结束',interrupted:'已中断',failed:'失败'}[data.status.state]||'准备中')+' · 最近更新 '+new Date().toLocaleTimeString()+' · '+data.pending+' 条作者信息待查询；无作者信息含尚未完成的查询。'+(data.worker_error?' 源码后台查询失败：'+data.worker_error:'');
   if(selected&&!data.errors.some(e=>e.id===selected)){selected=null;detailId++;$('detail').replaceChildren(el('p','请选择当前页的问题。','empty'))}
   if(!selected&&data.errors.length){detailReady=false;loadDetail(data.errors[0].id)}else if(selected&&!detailReady)loadDetail(selected);
  }catch(error){banner.textContent='更新失败：'+error.message+'。保留当前页面；停止采集后请从 XML 导出报告。'}
 }
 function schedule(){clearTimeout(timer);timer=setTimeout(async()=>{await loadPage();schedule()},2000)}
 let inputTimer;const changed=()=>{clearTimeout(inputTimer);inputTimer=setTimeout(()=>loadPage(true),250)};
 $('search').oninput=changed;$('fileFilter').oninput=changed;$('authorFilter').onchange=()=>loadPage(true);
 $('resetFilters').onclick=()=>{kind='';$('search').value='';$('fileFilter').value='';$('authorFilter').value='';loadPage(true)};
 prev.onclick=()=>{offset=Math.max(0,offset-100);loadPage()};next.onclick=()=>{offset+=100;loadPage()};
 loadPage();schedule();
}
'''
