// Run the exported report script against a small DOM test double, without a browser.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[2],'utf8');
const original=JSON.parse(html.match(/<script id="report-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
function setup(report, environment={}){
 const ids=new Map();
 class Element{
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.attrs={};this.value='';this._text='';}
  set id(value){ids.set(value,this);this._id=value;} get id(){return this._id;}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes);}
  setAttribute(k,v){this.attrs[k]=v;}
  scrollIntoView(){} focus(){} select(){}
 }
 for(const id of ['report-data','nav','search','fileFilter','resetFilters','listCount','list','detail','file','mode','notice']){const n=new Element('div');n.id=id;}
 ids.get('report-data').textContent=JSON.stringify(report);
 const context=vm.createContext({document:{getElementById:id=>ids.get(id),createElement:tag=>new Element(tag),createTextNode:text=>{const n=new Element('text');n.textContent=text;return n}},
  Option:function(text,value){const n=new Element('option');n.textContent=text;n.value=value;return n;},
  history:{replaceState(){throw Error('file URL history unavailable')}},location:{hash:''},innerWidth:1200,
  navigator:{},setInterval(){},setTimeout,clearTimeout,console,...environment});
 vm.runInContext(script,context);
 return {ids,run:s=>vm.runInContext(s,context)};
}
const {ids,run}=setup(original);
let opened=[];
const viewer=setup(original,{window:{open(...args){opened=args}}});
viewer.run('openSourceFile(report.errors[0].stacks[0].frames[0])');
assert.equal(opened[1],'_blank');assert(opened[2].includes('noopener'));
assert(opened[0].startsWith('/source/'));assert(opened[0].includes('.html#L'));
assert.equal(ids.get('list').children.filter(n=>n.dataset.errorId).length,2,'both sample errors visible');
ids.get('list').children[1].onclick();
assert(ids.get('detail').textContent.includes(original.errors[1].id),'history failure must not block switching');
ids.get('nav').children[1].onclick();
assert.equal(ids.get('list').children.filter(n=>n.dataset.errorId).length,1);
ids.get('resetFilters').onclick();
assert.equal(ids.get('list').children.filter(n=>n.dataset.errorId).length,2);
ids.get('search').value='/home/user/project/examples/demo.c';ids.get('search').oninput();
assert.equal(ids.get('list').children.filter(n=>n.dataset.errorId).length,2,'full path search');
ids.get('resetFilters').onclick();
ids.get('fileFilter').value='examples\\demo.c';ids.get('fileFilter').oninput();
assert.equal(ids.get('list').children.filter(n=>n.dataset.errorId).length,2,'normalized file path');
ids.get('fileFilter').value='nonexistent.c';ids.get('fileFilter').oninput();
assert.equal(ids.get('list').children.filter(n=>n.dataset.errorId).length,0);
ids.get('resetFilters').onclick();ids.get('list').children[0].onclick();
ids.get('startDebug').onclick();
assert.equal(run('shellQuote("/usr/bin/python3")'),'/usr/bin/python3');
assert.equal(run('shellQuote("--project-dir")'),'--project-dir');
assert.equal(run('shellQuote("/a b/demo")'),'"/a b/demo"');
assert.equal(run('shellQuote("\\u0027")'),'"\u0027"');
assert.equal(run('shellQuote("")'),'""');
for(const ch of ['$','`','"','\\'])assert.equal(run('shellQuote('+JSON.stringify(ch)+')'),'"\\'+ch+'"');
assert(run('document.getElementById("detail").children.length')>0);
const large=JSON.parse(JSON.stringify(original));
large.errors=Array.from({length:5000},(_,i)=>({...original.errors[i%2],id:'issue'+i}));
const big=setup(large);
assert.equal(big.ids.get('list').children.filter(n=>n.dataset.errorId).length,100,'initial list bounded');
big.ids.get('list').children.at(-1).onclick();
assert.equal(big.ids.get('list').children.filter(n=>n.dataset.errorId).length,200,'load more works');
console.log('Report UI: command quoting, two-error selection, filters, history failure, and 5000-entry list passed.');

async function testLive(){
 const timers=new Map();let timerId=0,requests=[],step=0;
 const liveReport={...original,live:true,collection_state:'running',errors:[]};
 const data=[{revision:0,report:liveReport},{revision:1,report:{...liveReport,errors:original.errors}},
             {revision:1},new Error('HTTP 503'),{revision:2,report:{...liveReport,errors:original.errors}}];
 const live=setup(liveReport,{AbortController,
  setTimeout(fn,delay){const id=++timerId;timers.set(id,{fn,delay});return id;},
  clearTimeout(id){timers.delete(id);},
  fetch:async(url,options)=>{requests.push(url);assert.equal(options.cache,'no-store');const next=data[step++];if(next instanceof Error)throw next;return {ok:true,json:async()=>next};}});
 await new Promise(resolve=>setImmediate(resolve));
 async function tick(){const timer=[...timers.entries()].find(([,t])=>t.delay===2000);assert(timer,'refresh must be rescheduled');timers.delete(timer[0]);await timer[1].fn();}
 assert.equal(live.ids.get('list').children.filter(n=>n.dataset.errorId).length,0);
 live.ids.get('fileFilter').value='examples/demo.c';live.ids.get('fileFilter').oninput();
 await tick();
 assert.equal(live.ids.get('list').children.filter(n=>n.dataset.errorId).length,2,'polling adds new errors without reload');
 assert.equal(live.ids.get('fileFilter').value,'examples/demo.c','keep filters');
 const selected=live.run('selected');await tick();assert.equal(live.run('selected'),selected);
 assert(live.ids.get('notice').textContent.includes('3'), 'unchanged revision still updates heartbeat');
 await tick();assert(live.ids.get('notice').textContent.includes('HTTP 503'),'display actual failure');
 await tick();assert.equal(live.run('selected'),selected,'recover after temporary failure');
 assert.equal(requests[1],'/api/live?revision=0');
 assert.equal(requests[4],'/api/live?revision=1');
 assert.equal([...timers.values()].filter(t=>t.delay===2000).length,1,'one polling loop');
 assert(ids.get('notice').textContent.includes('快照'),'static report explains lack of live updates');
 console.log('Live report: new errors, preserved filters, heartbeat, failure and recovery passed.');
}
testLive().catch(error=>{console.error(error);process.exitCode=1;});
const filteredReport=JSON.parse(JSON.stringify(original));
filteredReport.errors.push({...filteredReport.errors[0],id:'reachable',kind:'Leak_StillReachable',stacks:[{label:'stack',frames:[{file:'only.c',fn:'only'}]}]});
const check=setup(filteredReport);
check.ids.get('fileFilter').value='only.c';check.ids.get('fileFilter').oninput();
assert.equal(check.run('filtered.length'),1);
assert.equal(check.ids.get('nav').children[0].children[1].textContent,'1/3');
assert.equal(check.ids.get('nav').children.find(n=>n.children[0].textContent==='仍可访问').children[1].textContent,'1/1');
check.ids.get('fileFilter').value='missing.c';check.ids.get('fileFilter').oninput();
assert.equal(check.run('filtered.length'),0);
assert.equal(check.ids.get('nav').children.find(n=>n.children[0].textContent==='仍可访问').children[1].textContent,'0/1');
for(const label of ['非法写入','确定泄漏'])assert.equal(check.ids.get('nav').children.find(n=>n.children[0].textContent===label).className,'danger-kind');
check.ids.get('resetFilters').onclick();check.ids.get('list').children[0].onclick();
assert.equal(check.ids.get('detail').children[0].textContent,'查看代码文件');
assert.equal(check.ids.get('detail').children[0].disabled,false);
