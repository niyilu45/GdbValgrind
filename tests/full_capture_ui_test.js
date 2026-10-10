const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[2],'utf8'),ids=new Map();
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.style={};this.value='';this._text='';this.open=false}
 set id(id){this._id=id;ids.set(id,this)}get id(){return this._id}
 set textContent(value){this._text=String(value);this.replaceChildren()}
 get textContent(){return this._text+this.children.map(n=>n.textContent).join('')}
 append(...nodes){for(const n of nodes){n.remove();n.parentNode=this;this.children.push(n)}}
 remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(n=>n!==this);this.parentNode=null}
 replaceChildren(...nodes){for(const n of this.children)n.parentNode=null;this.children=[];this.append(...nodes)}
 before(n){if(this.parentNode){n.remove();const parent=this.parentNode;n.parentNode=parent;parent.children.splice(parent.children.indexOf(this),0,n)}}
 get lastElementChild(){return this.children.at(-1)}
 setAttribute(){}scrollIntoView(){}focus(){}select(){}
}
for(const id of ['report-data','nav','search','fileFilter','authorFilter','resetFilters','listCount','list','detail','file','mode','notice','capture-status']){const e=new Element('div');e.id=id}
ids.get('report-data').textContent=html.match(/<script id="report-data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const context=vm.createContext({document:{head:new Element('head'),getElementById:id=>ids.get(id),createElement:t=>new Element(t),createTextNode:t=>{const e=new Element('text');e.textContent=t;return e}},window:{},
 Option:function(t,v){const e=new Element('option');e.textContent=t;e.value=v;return e},location:{hash:'',pathname:'/full-report.html'},history:{replaceState(){}},innerWidth:1500,navigator:{},setTimeout(){},clearTimeout(){},setInterval(){},console});
for(const match of html.matchAll(/<script>([\s\S]*?)<\/script>/g))vm.runInContext(match[1],context);
vm.runInContext(fs.readFileSync(process.argv[3],'utf8'),context);
assert(!html.includes('LEGACY_WRONG_VARIABLE'));
const errors=vm.runInContext('report.errors',context);
for(const index of [0,1,0,1]){
 ids.get('list').children[index].onclick();
 const frames=ids.get('detail').children.filter(n=>n.dataset.frameKey);
 for(const frame of frames){
  const panel=frame.children.find(n=>n.dataset.captureVariables==='yes');
  const [si,fi]=frame.dataset.frameKey.split(':').map(Number);
  if(si!==0||!errors[index].stacks[si].frames[fi].file)continue;
  assert(panel,`missing panel ${index}/${si}/${fi}`);
  assert(panel.textContent.includes(`owner_${index}_${fi}`));
  assert(!panel.textContent.includes(`owner_${1-index}_`),'other error values leaked');
  frame.open=true;if(frame.ontoggle)frame.ontoggle();
  assert.strictEqual(frame.lastElementChild,panel,'source must precede variables');
 }
 ids.get('showBlame').checked=false;ids.get('showBlame').onchange();
 assert(!ids.get('detail').textContent.includes(`owner_${1-index}_`));
}
console.log('Full generated report + upgraded viewer + sidecar: ownership preserved');
