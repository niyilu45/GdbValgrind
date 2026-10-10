import shutil
import subprocess
import unittest
from inc.capture_updates import SCRIPT


class IncrementalCaptureTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'requires Node')
    def test_updates_keep_existing_expanded_nodes(self):
        setup = r'''
const assert=require('assert');
class Element {
 get textContent(){return (this.text||'')+this.children.map(n=>typeof n==='string'?n:n.textContent).join('')}
 set textContent(value){this.text=value;this.children=[]}
 constructor(){this.children=[];this.dataset={};this.style={};this.textContent='';}
 append(...nodes){this.children.push(...nodes)}
 before(node){this.previous=node}
 querySelector(){return this.children[1]}
 querySelectorAll(){return this.children}
 remove(){}
}
const area=new Element(),status=new Element();
const frame=new Element();frame.dataset.frameKey='0:0';frame.open=true;area.append(frame);
global.report={errors:[{id:'one',kind:'InvalidWrite',what:'Invalid write of size 4',stacks:[{frames:[{local_file:'/src/a.c',line:10,fn:'main'}]}]}]};
const issueList=new Element(),navigation=new Element(),issue=new Element();issue.dataset.errorId='one';issueList.append(issue);navigation.append(new Element(),new Element());
global.selected='one';global.detail=()=>{};global.nav=()=>{for(const node of navigation.children)node.textContent=matchingRows().length+'/'+report.errors.length};global.list=()=>nav();let filtered=true;global.matchingRows=()=>filtered?report.errors.map(e=>({e})):[];
global.window={};global.document={head:new Element(),getElementById:id=>id==='detail'?area:id==='list'?issueList:id==='nav'?navigation:status,createElement:()=>new Element(),createTextNode:text=>text};
global.setTimeout=()=>1;global.clearTimeout=()=>{};
'''
        checks = r'''
const snapshot={requested_error_id:'wrong',valgrind_error:'Invalid write of size 4',frames:[{file:'/src/a.c',line:10,function:'main',variables:[{name:'x',value:'42'}]}]};
const checkbox=status.previous.children[0];checkbox.checked=true;assert.strictEqual(matchingRows().length,0);
checkbox.onchange();assert.strictEqual(navigation.children[1].textContent,'0/1');
window.aivCaptureUpdate({live:true,items:[{id:'capture',snapshot}]});
assert.strictEqual(matchingRows().length,1);
const panel=frame.children[0];assert(panel.textContent.includes('实际值：42'));
const all=panel.children.find(n=>n.dataset?.captureAllVariables==='yes');assert(!all.open);all.open=true;
assert.strictEqual(frame.open,true);
assert(issue.children[0].textContent.includes('已有变量'));assert.strictEqual(navigation.children[1].textContent,'1/1');
assert(navigation.children.every(node=>!node.children.some(n=>n.dataset?.captureBadge==='yes')));
filtered=false;nav();assert.strictEqual(navigation.children[1].textContent,'0/1');filtered=true;list();assert.strictEqual(navigation.children[1].textContent,'1/1');
window.aivCaptureUpdate({live:true,items:[{id:'capture',snapshot}]});
assert.strictEqual(frame.children[0],panel);assert.strictEqual(frame.children.length,1);
assert.strictEqual(all.open,true);
const other={...snapshot,valgrind_error:'Invalid read of size 4'};
window.aivCaptureUpdate({live:false,items:[{id:'other',snapshot:other}]});
assert.strictEqual(frame.children.length,1);
assert(status.textContent.includes('1 个现场未能唯一匹配'));
'''
        script = SCRIPT.replace('<script>', '').replace('</script>', '')
        result = subprocess.run(['node','-e',setup+script+checks], capture_output=True, text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'requires Node')
    def test_source_alignment_and_ambiguous_candidates(self):
        setup = r'''
const assert=require('assert');
class Element {
 get textContent(){return (this.text||'')+this.children.map(n=>typeof n==='string'?n:n.textContent).join('')}
 set textContent(value){this.text=value;this.children=[]}
 constructor(){this.children=[];this.dataset={};this.style={};this.textContent=''}
 append(...nodes){this.children.push(...nodes)}
 before(node){this.previous=node}
 remove(){}
}
const nodes=Object.fromEntries(['capture-status','resetFilters','detail','list','nav'].map(id=>[id,new Element()]));
const error=(id,file,line=10)=>({id,kind:'InvalidWrite',what:'Invalid write of size 4',stacks:[{frames:[
 {fn:'system_wrapper'}, {dir:'/build/src',file,local_file:'/mapped/'+file,line,fn:'foo(int)'},
 {dir:'/build/src',file:'main.c',line:20,fn:'main()'}]}]});
global.report={errors:[error('one','a.c'),error('bad','bad.c'),error('amb1','same.c'),error('amb2','same.c'),error('optimized','opt.c')]};
global.selected='one';global.detail=()=>{};global.nav=()=>{};global.list=()=>{};
global.matchingRows=()=>report.errors.map(e=>({e}));
global.window={};global.document={head:new Element(),getElementById:id=>nodes[id],createElement:()=>new Element(),createTextNode:text=>text};
global.setTimeout=()=>1;global.clearTimeout=()=>{};
const frame=new Element();frame.dataset.frameKey='0:1';nodes.detail.append(frame);
'''
        checks = r'''
const runtime=(file,status='available',parent=20)=>({valgrind_error:'==123== Invalid write of size 4',frames:[
 {function:'wrapper'}, {file:'/build/src/./'+file,line:10,function:'foo',variables:[{name:'i',value:'5',status}]},
 {file:'/build/src/main.c',line:parent,function:'main',variables:[]}]});
window.aivCaptureUpdate({live:false,items:[
 {snapshot:runtime('a.c')}, {snapshot:runtime('bad.c','available',99)},
 {snapshot:runtime('same.c')}, {snapshot:runtime('opt.c','optimized_out')}]});
assert(frame.children[0].textContent.includes('实际值：5'));
const checkbox=nodes.resetFilters.previous.children[0];checkbox.checked=true;
assert.deepStrictEqual(matchingRows().map(row=>row.e.id),['one']);
assert(nodes['capture-status'].textContent.includes('已关联 2 个错误'));
assert(nodes['capture-status'].textContent.includes('多个候选 1'));
nodes.resetFilters.onclick();assert.strictEqual(checkbox.checked,false);
assert.strictEqual(matchingRows().length,5);
report.errors.push(error('structure','struct.c'));
// Reuse a previously unmatched error so its saved data can be inspected.
const struct=runtime('bad.c');struct.frames[1].variables=[{name:'request',type:'Request',status:'available',value:'raw struct',
 initialization:{status:'partially_undefined',raw_vbits:'ff',member_states:[]},
 member_details:{members:[{name:'length',type:'int',status:'available',value:'99',initialization:'undefined'},
 {name:'buffer',type:'char *',status:'available',value:'0x1234',initialization:'defined',note:'未读取指向的内容'},
 {name:'unknown',type:'int',status:'optimized_out',value:'unavailable',initialization:'unknown'}]}}];
selected='bad';frame.children=[];window.aivCaptureUpdate({live:false,items:[{snapshot:struct}]});
const text=frame.children[0].textContent;
assert(text.includes('request.length（int） = 99'));
assert(text.includes('未初始化，显示值不可靠'));
assert(text.includes('未读取指向的内容'));
assert(!text.includes('raw_vbits'));
const panel=frame.children[0],all=panel.children.find(n=>n.dataset?.captureAllVariables==='yes');
assert(!all.open);
assert(all.textContent.includes('request.buffer'));
const summary=panel.children.filter(n=>n!==all).map(n=>n.textContent).join('');
assert(summary.includes('request.length'));
assert(!summary.includes('request.buffer'));
const rows=all.children[1].children;
const member=name=>rows.find(n=>n.textContent.includes(name));
assert.strictEqual(member('request.length').dataset.variableState,'abnormal');
assert.strictEqual(member('request.buffer').dataset.variableState,'normal');
assert.strictEqual(member('request.unknown').dataset.variableState,'uncertain');
assert.strictEqual(member('request.length').style.color,'#9f3029');
assert.strictEqual(member('request.buffer').style.color,'#195943');
assert.strictEqual(member('request.unknown').style.color,'#805000');
assert(member('request.unknown').textContent.includes('【不确定】'));
'''
        script = SCRIPT.replace('<script>', '').replace('</script>', '')
        result = subprocess.run(['node', '-e', setup+script+checks], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
