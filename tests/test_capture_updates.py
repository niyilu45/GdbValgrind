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
 append(...nodes){for(const node of nodes){if(node.parentNode)node.parentNode.children=node.parentNode.children.filter(n=>n!==node);if(typeof node!=='string')node.parentNode=this;this.children.push(node)}}
 get lastElementChild(){return this.children[this.children.length-1]}
 before(node){this.previous=node}
 querySelector(){return this.children[1]}
 querySelectorAll(){return this.children}
 remove(){}
}
const area=new Element(),status=new Element();
const frame=new Element();frame.dataset.frameKey='0:0';frame.open=true;area.append(frame);
let loaded=false;const source=new Element();source.textContent='source code';
frame.ontoggle=()=>{if(frame.open&&!loaded){loaded=true;frame.append(source)}};
global.report={errors:[{id:'one',kind:'InvalidWrite',what:'Invalid write of size 4',stacks:[{frames:[{local_file:'/src/a.c',line:10,fn:'main'}]}]}]};
const issueList=new Element(),navigation=new Element(),issue=new Element();issue.dataset.errorId='one';issueList.append(issue);navigation.append(new Element(),new Element());
global.savedFilters={variables:true};global.selected='one';global.detail=()=>{};global.nav=()=>{for(const node of navigation.children)node.textContent=matchingRows().length+'/'+report.errors.length};global.list=()=>nav();let filtered=true;global.matchingRows=()=>filtered?report.errors.map(e=>({e})):[];
global.window={};global.document={head:new Element(),getElementById:id=>id==='detail'?area:id==='list'?issueList:id==='nav'?navigation:status,createElement:()=>new Element(),createTextNode:text=>text};
global.setTimeout=()=>1;global.clearTimeout=()=>{};
'''
        checks = r'''
const snapshot={association:{status:'verified'},requested_error_id:'wrong',valgrind_error:'Invalid write of size 4',frames:[{file:'/src/a.c',line:10,function:'main',source_line:{text:'use(x);',identifiers:'use(x);'},variables:[{name:'x',value:'42'}]}]};
const checkbox=status.previous.children[0];assert.strictEqual(checkbox.checked,true);assert.strictEqual(matchingRows().length,0);
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
frame.ontoggle();assert.strictEqual(frame.children[0],source);assert.strictEqual(frame.lastElementChild,panel);
frame.open=false;frame.ontoggle();frame.open=true;frame.ontoggle();
assert.strictEqual(frame.children.length,2);assert.strictEqual(frame.lastElementChild,panel);assert(all.open);
window.aivCaptureUpdate({live:false,items:[{snapshot}]});assert.strictEqual(frame.lastElementChild,panel);
'''
        script = SCRIPT.replace('<script>', '').replace('</script>', '')
        result = subprocess.run(['node','-e',setup+script+checks], capture_output=True, text=True, encoding='utf-8')
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
const runtime=(file,status='available',parent=20)=>({association:{status:'verified'},valgrind_error:'==123== Invalid write of size 4',frames:[
 {function:'wrapper'}, {file:'/build/src/./'+file,line:10,function:'foo',source_line:{text:'use(i);',identifiers:'use(i);'},variables:[{name:'i',value:'5',status}]},
 {file:'/build/src/main.c',line:parent,function:'main',variables:[]}]});
const short=runtime('bad.c');short.frames.pop();
const legacy=runtime('bad.c');delete legacy.association;
const shifted=runtime('bad.c');shifted.frames.shift();
const wrongWidth=runtime('bad.c');wrongWidth.valgrind_error='Invalid write of size 40';
const unverified=runtime('bad.c');unverified.association={status:'unverified'};
const wrongFunction=runtime('bad.c');wrongFunction.frames[1].function='another_function';
const caller=runtime('bad.c');caller.frames.unshift({file:'/build/src/actual-error.c',line:1,function:'actual_error'});
window.aivCaptureUpdate({live:true,items:[{snapshot:short},{snapshot:wrongFunction},{snapshot:caller},{snapshot:wrongWidth},{snapshot:unverified},{snapshot:legacy},{snapshot:shifted}]});
assert(nodes['capture-status'].textContent.includes('已关联 0 个错误'));
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
struct.frames[1].source_line={text:'use(request.length);',identifiers:'use(request.length);'};
struct.frames[1].variables.push({name:'unrelated',status:'available',value:'456',initialization:{status:'undefined'}});
struct.frames[2].variables=[{name:'parentOnly',status:'available',value:'parentB'}];
struct.frames[2].source_line={text:'use(parentOnly);',identifiers:'use(parentOnly);'};
selected='bad';frame.children=[];window.aivCaptureUpdate({live:false,items:[{id:'session-B/error-1',snapshot:struct}]});
const text=frame.children[0].textContent;
assert(text.includes('request.length（int） = 99'));
assert(text.includes('未初始化，显示值不可靠'));
assert(!text.includes('request.buffer'));
assert(!text.includes('raw_vbits'));
const panel=frame.children[0],all=panel.children.find(n=>n.dataset?.captureAllVariables==='yes');
assert(!all.open);
assert(!all.textContent.includes('request.buffer'));
const summary=panel.children.filter(n=>n!==all).map(n=>n.textContent).join('');
assert(summary.includes('request.length'));
assert(!summary.includes('request.buffer'));
assert(!summary.includes('unrelated'));
assert(!all.textContent.includes('unrelated'));
const rows=all.children[1].children;
const member=name=>rows.find(n=>n.textContent.includes(name));
assert.strictEqual(member('request.length').dataset.variableState,'abnormal');
assert.strictEqual(member('request.length').style.color,'#9f3029');
// Two errors share a caller location: only the selected error's snapshot may
// supply either frame, even when the replay seed ID is identical.
const parentFrame=new Element();parentFrame.dataset.frameKey='0:2';nodes.detail.append(parentFrame);
window.aivCaptureUpdate({live:false,items:[]});
assert(parentFrame.textContent.includes('parentB'));assert(!frame.textContent.includes('parentB'));
selected='one';frame.children=[];parentFrame.children=[];
detail(report.errors[0]);
assert(frame.textContent.includes('实际值：5'));assert(!frame.textContent.includes('request.length'));
assert(!parentFrame.textContent.includes('parentB'));
selected='bad';frame.children=[];parentFrame.children=[];detail(report.errors[1]);
assert(frame.textContent.includes('session-B/error-1'));assert(parentFrame.textContent.includes('parentB'));
// Keywords/whitespace must not hide locals; members must not impersonate locals.
function lineSummary(source, variables, levels=[], identifiers=source, checked=null){
 const sample=runtime('bad.c');sample.frames[1].source_line={text:source,identifiers};
 sample.frames[1].variables=variables;sample.frames[1].index_analysis={levels};
 if(checked)sample.frames[1].line_checks={version:1,items:checked};
 frame.children=[];parentFrame.children=[];
 window.aivCaptureUpdate({live:false,replay_id:source,items:[{snapshot:sample}]});
 return frame.children[0].textContent;
}
const scalar=(name,value)=>({name,value,status:'available',initialization:{status:'undefined'}});
let lineText=lineSummary('return value;', [scalar('value','731')]);
assert(lineText.includes('实际值：731'));assert(lineText.includes('value：未初始化'));
lineText=lineSummary('int value = ptr -> length + ns::count;', [scalar('value','732'),scalar('length','WRONG_LENGTH'),scalar('count','WRONG_COUNT')]);
assert(lineText.includes('实际值：732'));assert(!lineText.includes('WRONG_LENGTH'));assert(!lineText.includes('WRONG_COUNT'));
assert(!lineText.includes('length：未初始化'));assert(!lineText.includes('count：未初始化'));
lineText=lineSummary('return longer[index];', [scalar('index','733'),scalar('long','WRONG_PREFIX')]);
assert(lineText.includes('实际值：733'));assert(!lineText.includes('WRONG_PREFIX'));
lineText=lineSummary('return table [ 1 ].length;', [{name:'table',status:'available',member_details:{members:[
 {name:'[1].length',value:'734',status:'available',initialization:'undefined'},
 {name:'[10].length',value:'WRONG_ELEMENT',status:'available',initialization:'undefined'}]}}]);
assert(lineText.includes('table[1].length = 734'));assert(!lineText.includes('WRONG_ELEMENT'));
lineText=lineSummary('return item.used;', [{name:'item',status:'available',initialization:{status:'partially_undefined',member_states:[
 {name:'used',status:'defined'},{name:'unrelated',status:'undefined'}]}}]);
assert(lineText.includes('item.used'));assert(!lineText.includes('item.unrelated'));
lineText=lineSummary('return item.child.used;', [{name:'item',status:'available',value:'{child={used=1,secret=99}}',member_details:{members:[
 {name:'child',status:'available',value:'{used=1,secret=99}',initialization:'partially_undefined'},
 {name:'child.used',status:'available',value:'1',initialization:'defined'},
 {name:'child.secret',status:'available',value:'99',initialization:'undefined'}]}}]);
assert(lineText.includes('item.child.used = 1'));assert(!lineText.includes('secret'));
lineText=lineSummary('return legacy.used;', [{name:'legacy',status:'available',value:'{used=1,SECRET_MEMBER=999}',initialization:{status:'partially_undefined'}}]);
assert(!lineText.includes('SECRET_MEMBER'));assert(!lineText.includes('legacy：部分未初始化'));
lineText=lineSummary('', [scalar('NO_SOURCE_VARIABLE','999')]);
assert(!lineText.includes('NO_SOURCE_VARIABLE'));
lineText=lineSummary('use(ptr->length);', [scalar('unrelated','WRONG_FRAME_VALUE')], [], 'use(ptr->length);',
 [{name:'ptr->length',role:'source_expression',status:'available',value:'123',initialization:{status:'undefined'}}]);
assert(lineText.includes('本行逐项检查 1 项'));assert(lineText.includes('实际值：123'));
assert(lineText.includes('ptr->length：未初始化'));assert(!lineText.includes('unrelated'));assert(!lineText.includes('WRONG_FRAME_VALUE'));
lineText=lineSummary('return other_table[i];', [],[{expression:'table[i]',status:'out_of_bounds',actual_index:99,bounds:[0,2]}]);
assert(!lineText.includes('table[i]：索引越界'));
lineText=lineSummary('return expected;', [scalar('wrong','WRONG_LINE')], [], 'return wrong;');
assert(!lineText.includes('WRONG_LINE'));assert(!lineText.includes('wrong：未初始化'));
assert(lineText.includes('变量查看器 v18'));
frame.children=[];parentFrame.children=[];
window.aivCaptureUpdate({live:false,replay_id:'restore-struct',items:[{snapshot:struct}]});
// A deduplicated report keeps one complete capture, never a mix of two stops.
const first=runtime('bad.c'),second=runtime('bad.c');
first.frames[1].variables[0].value='FIRST_STOP';second.frames[1].variables[0].value='SECOND_STOP';
report.errors[1].records=2;frame.children=[];parentFrame.children=[];
window.aivCaptureUpdate({live:false,replay_id:'dedup-test',items:[{id:'first',snapshot:first},{id:'second',snapshot:second}]});
assert(frame.textContent.includes('FIRST_STOP'));assert(!frame.textContent.includes('SECOND_STOP'));
frame.children=[];parentFrame.children=[];
window.aivCaptureUpdate({live:false,replay_id:'restore-again',items:[{snapshot:struct}]});
// Changed source text disables line-level claims instead of guessing.
report.errors[1].stacks[0].frames[1].source=[{number:10,text:'different_statement();'}];
frame.children=[];detail(report.errors[1]);
const changedPanel=frame.children[0],changedAll=changedPanel.children.find(n=>n.dataset?.captureAllVariables==='yes');
assert(changedPanel.textContent.includes('源码行不同'));
assert(!changedPanel.children.filter(n=>n!==changedAll).map(n=>n.textContent).join('').includes('request.length = 99'));
window.aivCaptureUpdate({live:true,report_key:'different-report',items:[{snapshot:struct}]});
assert(nodes['capture-status'].textContent.includes('另一份步骤二报告'));
window.aivCaptureUpdate({live:false,replay_id:'new-replay',items:[]});
assert(nodes['capture-status'].textContent.includes('已关联 0 个错误'));
'''
        script = SCRIPT.replace('<script>', '').replace('</script>', '')
        result = subprocess.run(['node', '-e', setup+script+checks], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
