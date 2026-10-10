const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[2],'utf8'),ids=new Map(),requests=[];
let failDetail=false;
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.value='';this.dataset={};this.attrs={};this._text=''}
 set id(v){ids.set(v,this)}
 set textContent(v){this._text=String(v);this.children=[]}
 get textContent(){return this._text+this.children.map(n=>n.textContent).join('')}
 append(...n){this.children.push(...n)} prepend(...n){this.children.unshift(...n)}
 replaceChildren(...n){this.children=[];this._text='';this.append(...n)}
 after(n){this.afterNode=n} setAttribute(k,v){this.attrs[k]=v}
 scrollIntoView(){} focus(){} select(){}
}
for(const id of ['report-data','nav','search','fileFilter','authorFilter','resetFilters','listCount','list','detail','file','mode','notice']){const e=new Element('div');e.id=id}
ids.get('report-data').textContent=html.match(/<script id="report-data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const issue={id:'one',kind:'InvalidWrite',what:'write error',count:1,records:1,stacks:[{frames:[{file:'a.c',line:'1',source_ref:0},{file:'a.c',line:'2',source_ref:0}]}]};
const timers=[];
const context=vm.createContext({document:{getElementById:id=>ids.get(id),createElement:t=>new Element(t),createTextNode:t=>{const e=new Element('text');e.textContent=t;return e}},
 Option:function(t,v){const e=new Element('option');e.textContent=t;e.value=v;return e},URLSearchParams,AbortController,
 location:{hash:''},history:{replaceState(){}},innerWidth:1200,navigator:{},
 setTimeout(fn,ms){timers.push({fn,ms});return timers.length},clearTimeout(){},setInterval(){},
 fetch:async url=>{requests.push(url);if(failDetail&&url.startsWith('/api/issue?'))throw Error('test disconnect');return {ok:true,json:async()=>url.startsWith('/api/issue?')?{ready:true,report:{errors:[issue],source_files:{},source_snippets:[[{number:1,text:'int x;',blame:{author:'Alice'}}]]}}:{errors:[{id:issue.id,kind:issue.kind,what:issue.what,count:issue.count,seq:1}],total:201,kinds:{InvalidWrite:201},totals:{InvalidWrite:201},authors:[{author:'Alice',n:200},{author:'Bob',n:1}],pending:0,status:{state:'running'}}}},console});
vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
const settle=async()=>{for(let i=0;i<4;i++)await new Promise(r=>setImmediate(r))};
(async()=>{
 await settle();assert(requests.some(u=>u.startsWith('/api/issues?')));assert(ids.get('detail').textContent.includes('int x;'),'pooled source restored');
 assert.equal(ids.get('authorFilter').children[1].textContent,'Alice (200)');
 const firstRow=ids.get('list').children[0],firstOption=ids.get('authorFilter').children[1];
 await timers.find(t=>t.ms===2000).fn();await settle();
 assert.strictEqual(ids.get('list').children[0],firstRow,'unchanged polling preserves rows');
 assert.strictEqual(ids.get('authorFilter').children[1],firstOption,'unchanged polling preserves options');
 failDetail=true;firstRow.onclick();await settle();
 assert(ids.get('detail').textContent.includes('int x;'),'failed detail refresh preserves code');
 assert(ids.get('detailRetry'),'retry action is available');
 failDetail=false;ids.get('detailRetry').children.at(-1).onclick();await settle();
 const frames=ids.get('detail').children.filter(n=>n.dataset.frameKey);
 frames[0].open=false;frames[1].open=true;
 issue.count=2;firstRow.onclick();await settle();
 const updatedFrames=ids.get('detail').children.filter(n=>n.dataset.frameKey);
 assert.strictEqual(updatedFrames[1],frames[1],'unchanged parent code DOM is reused during refresh');
 assert.equal(updatedFrames[0].open,false,'manually collapsed frame stays collapsed');
 assert.equal(updatedFrames[1].open,true,'expanded parent frame survives refreshed detail');
 ids.get('showBlame').checked=false;ids.get('showBlame').onchange();
 assert.equal(ids.get('detail').children.filter(n=>n.dataset.frameKey)[1].open,true,'blame toggle preserves expansion');
 ids.get('list').afterNode.children[1].onclick();await settle();assert(requests.some(u=>u.includes('offset=100')));
 ids.get('authorFilter').value='author:Alice';ids.get('authorFilter').onchange();await settle();assert(requests.some(u=>u.includes('author=author%3AAlice')&&u.includes('offset=0')));
 assert(ids.get('nav').children[1].className==='danger-kind');
 assert(!requests.some(u=>u.startsWith('/api/live')),'no full snapshot polling');
 console.log('Paged UI: detail, pooled sources, author filter and pagination passed');
})().catch(e=>{console.error(e);process.exitCode=1});
