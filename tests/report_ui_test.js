// Run the exported report script against a small DOM test double, without a browser.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[2],'utf8');
const original=JSON.parse(html.match(/<script id="report-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
function setup(report){
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
  navigator:{},setInterval(){},setTimeout,clearTimeout,console});
 vm.runInContext(script,context);
 return {ids,run:s=>vm.runInContext(s,context)};
}
const {ids,run}=setup(original);
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
