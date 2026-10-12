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
global.scheduled=[];global.setTimeout=(fn,ms)=>{scheduled.push({fn,ms});return scheduled.length};global.clearTimeout=()=>{};
'''
        checks = r'''
const snapshot={association:{status:'verified'},requested_error_id:'wrong',valgrind_error:'Invalid write of size 4',frames:[{file:'/src/a.c',line:10,function:'main',source_line:{text:'use(x);',identifiers:'use(x);'},variables:[{name:'x',value:'42'}]}]};
const checkbox=status.previous.children[0];assert.strictEqual(checkbox.checked,true);assert.strictEqual(matchingRows().length,0);
checkbox.onchange();assert.strictEqual(navigation.children[1].textContent,'0/1');
window.aivCaptureUpdate({live:true,items:[{id:'capture',snapshot}]});
assert.strictEqual(matchingRows().length,1);
const panel=frame.children[0];assert(panel.textContent.includes('实际值：42'));
const all=panel.children.find(n=>n.dataset?.captureEvidence==='yes');assert(!all.open);all.open=true;
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
assert(status.textContent.includes('InvalidWrite：已读值 1/1'));
frame.ontoggle();assert.strictEqual(frame.children[0],source);assert.strictEqual(frame.lastElementChild,panel);
frame.open=false;frame.ontoggle();frame.open=true;frame.ontoggle();
assert.strictEqual(frame.children.length,2);assert.strictEqual(frame.lastElementChild,panel);assert(all.open);
window.aivCaptureUpdate({live:false,items:[{snapshot}]});assert.strictEqual(frame.lastElementChild,panel);
// Repeated filters and an unchanged sidecar must not rescan saved frame values.
window.aivCaptureUpdate({live:true,items:[{id:'cached',revision:'r1',snapshot}]});
const originalFrames=snapshot.frames;let frameReads=0;
Object.defineProperty(snapshot,'frames',{get(){frameReads++;return originalFrames}});
for(let i=0;i<20;i++){checkbox.checked=!checkbox.checked;checkbox.onchange();window.aivCaptureUpdate({live:true,items:[{id:'cached',revision:'r1',snapshot}]})}
assert.strictEqual(frameReads,0);
window.aivCaptureUpdate({live:true,items:[{id:'cached',revision:'r2',snapshot}]});assert(frameReads>0);
window.aivCaptureUpdate({live:true,update_revision:'stable',items:[{id:'cached',revision:'r2',snapshot}]});
const probe=document.head.children.find(n=>n.src?.startsWith('capture-progress.js'));
window.aivCaptureCheck({revision:'stable'});probe.onload();
assert(!document.head.children.some(n=>n.src?.startsWith('capture-updates.js')));
scheduled.filter(t=>t.ms===2000).at(-1).fn();
const nextProbe=document.head.children.at(-1);window.aivCaptureCheck({revision:'changed'});nextProbe.onload();
assert(document.head.children.at(-1).src.startsWith('capture-updates.js'));
'''
        script = SCRIPT.replace('<script>', '').replace('</script>', '')
        result = subprocess.run(['node'], input=setup+script+checks, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'requires Node')
    def test_source_alignment_and_ambiguous_candidates(self):
        setup = r'''
const assert=require('assert');
class Element {
 get textContent(){return (this.text||'')+this.children.map(n=>typeof n==='string'?n:n.textContent).join('')}
 set textContent(value){this.text=value;this.children=[]}
 constructor(){this.children=[];this.dataset={};this.style={};this.textContent=''}
 append(...nodes){this.children.push(...nodes);for(const n of nodes)if(typeof n==='object')n.parentNode=this}
 before(node){this.previous=node}
 remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(n=>n!==this)}
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
assert(nodes['capture-status'].previous.textContent.includes('amb1、amb2'));
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
const panel=frame.children[0],all=panel.children.find(n=>n.dataset?.captureEvidence==='yes');
assert(!all.open);
assert(!all.textContent.includes('request.buffer'));
const summary=panel.children.filter(n=>n!==all).map(n=>n.textContent).join('');
assert(summary.includes('request.length'));
assert(!summary.includes('request.buffer'));
assert(!summary.includes('unrelated'));
assert(!all.textContent.includes('unrelated'));
const rows=panel.children.flatMap(n=>n.children||[]).flatMap(n=>n.children||[]);
const member=name=>rows.find(n=>n.textContent.includes(name));
assert.strictEqual(member('request.length').dataset.variableState,'abnormal');
assert.strictEqual(member('request.length').style.color,'#9f3029');
assert(!text.includes('本行变量明细'));
assert(text.includes('变量分析依据（点击展开）'));
assert(text.includes('现场核对：'));
assert.strictEqual(text.split('= 99').length-1,1);
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
function lineSummary(source, variables, levels=[], identifiers=source, checked=null, memory={},header=null){
 const sample=runtime('bad.c');sample.frames[1].source_line={text:source,identifiers};
 sample.frames[1].variables=variables;sample.frames[1].index_analysis={levels};sample.memory=memory;if(header)sample.frames[1].source_line.control_header=header;
 if(checked)sample.frames[1].line_checks={version:1,items:checked};
 frame.children=[];parentFrame.children=[];
 window.aivCaptureUpdate({live:false,replay_id:source,items:[{snapshot:sample}]});
 return frame.children[0].textContent;
}
const scalar=(name,value)=>({name,value,status:'available',initialization:{status:'undefined'}});
const header={start_line:9,end_line:11,text:'for (int i=0;\n i < limit;\n ++i)',identifiers:'for (int i=0;\n i < limit;\n ++i)',lines:[{number:9,text:'for (int i=0;'},{number:10,text:' i < limit;'},{number:11,text:' ++i)'}]};
const headerText=lineSummary('for (int i=0;',[],[],'for (int i=0;',[scalar('i','2'),scalar('limit','8'),scalar('body_only','WRONG_BODY')],{}, {...header,start_line:10,end_line:12,lines:header.lines.map(r=>({...r,number:r.number+1}))});
assert(headerText.includes('当前控制语句变量分析'));assert(headerText.includes('实际值：2'));assert(headerText.includes('实际值：8'));assert(!headerText.includes('WRONG_BODY'));
report.errors[1].kind='InvalidWrite';
// Real initialization results include $self for scalars and scalar members.
const selfValue={name:'count',status:'available',value:'41',initialization:{status:'defined',complete:true,address:'0x1234',checked_bytes:4,size_bytes:4,member_states:[{name:'$self',status:'defined'}]}};
const fieldValue={name:'rxvec->abc',status:'available',value:'73',initialization:{status:'undefined',complete:true,member_states:[{name:'$self',status:'undefined'}]}};
let actualLine=lineSummary('use(count, rxvec->abc);', [], [], 'use(count, rxvec->abc);', [selfValue,fieldValue]);
assert(actualLine.includes('实际值：41'));assert(actualLine.includes('实际值：73'));
assert(actualLine.includes('【已检查部分通过】1. count'));assert(actualLine.includes('【异常】2. rxvec->abc'));
assert(actualLine.includes('检查结果：未初始化'));assert(!actualLine.includes('未采集到可直接对应'));
assert(actualLine.includes('检查地址 0x1234'));assert(actualLine.includes('检查 4 / 4 字节'));
assert(actualLine.includes('本行变量分析（含异常核查）'));
assert(!actualLine.includes('本行需要核查的异常变量'));
assert(!actualLine.includes('初始化状态无法确定'));
assert(actualLine.includes('Valgrind 已确认本次写入非法'));
assert(!actualLine.includes('【正常】'));
actualLine=lineSummary('use(data[options.index]);', [], [], 'use(data[options.index]);', [{name:'data[options.index]',status:'unavailable',value:'boundary unknown',initialization:{status:'unknown'},index_accesses:[{expression:'data[options.index]',base_expression:'data',index_expression:'options.index',actual_index:7,bounds:null}]}]);
assert(actualLine.includes('下标 options.index = 7；data 的下标范围 未知'));
assert(!actualLine.includes('第 1 级索引'));
assert(!actualLine.includes('访问 data[options.index]'));
actualLine=lineSummary('use(buffer[idx]);',[],[],'use(buffer[idx]);',[{name:'buffer[idx]',status:'available',value:'7',initialization:{status:'defined'},index_accesses:[{expression:'buffer[idx]',base_expression:'buffer',index_expression:'idx',actual_index:20,bounds:[0,99],prefix_check:{requested:[0,20],checked_elements:21,coverage_complete:true,note:'只检查元素自身存储',ranges:[{start:0,end:15,status:'defined'},{start:16,end:18,status:'undefined'},{start:19,end:20,status:'unaddressable'}]}}]}]);
assert(actualLine.includes('0～15：初始化及内存可访问性检查通过'));
assert(actualLine.includes('16～18：存在未初始化字节'));
assert(actualLine.includes('19～20：元素存储含不可访问内存'));
assert(actualLine.includes('已检查 21 个元素'));
const descendants=n=>[n,...(n.children||[]).flatMap(descendants)];
const prefixDetails=descendants(frame.children[0]).find(n=>n.dataset.arrayPrefix==='yes');
assert(prefixDetails);assert(!prefixDetails.open);
const prefixRow=(name,index,requested,ranges,checked=27,complete=true)=>({name,status:'available',value:'7',initialization:{status:'defined'},index_accesses:[{expression:name,base_expression:name.split('[')[0],index_expression:'idx',actual_index:index,bounds:[0,287],prefix_check:{requested,bounds:[0,287],checked_elements:checked,coverage_complete:complete,note:'range test',ranges}}]});
actualLine=lineSummary('use(items[idx]);',[],[],'use(items[idx]);',[prefixRow('items[idx]',26,[0,26],[{start:0,end:0,status:'defined'},{start:1,end:26,status:'undefined'}])]);
assert(actualLine.includes('应覆盖 0～26'));assert(actualLine.includes('已检查 27 个元素'));
assert(actualLine.includes('0～0：初始化及内存可访问性检查通过'));assert(actualLine.includes('1～26：存在未初始化字节'));
assert(actualLine.includes('旧现场未保存区间字节数及地址依据'));
const evidenced=prefixRow('proofArray[idx]',26,[0,26],[{start:0,end:26,status:'defined'}]);
evidenced.initialization={status:'defined',address:'0x168',checked_bytes:4,size_bytes:4,complete:true};
Object.assign(evidenced.index_accesses[0].prefix_check,{evidence_version:1,element_bytes:4,checked_bytes:108,target_bytes:108,queried_bytes:108,start_address:'0x100',end_address:'0x16b',queries:[{address:'0x100',bytes:108}]});
actualLine=lineSummary('use(proofArray[idx]);',[],[],'use(proofArray[idx]);',[evidenced]);
assert(actualLine.includes('当前表达式自身，不是整个数组区间'));
assert(actualLine.includes('检查 4 / 4 字节'));
assert(actualLine.includes('数组区间 0～26：已检查 27 个元素'));
assert(actualLine.includes('已分类检查 108 / 108 字节'));
assert(actualLine.includes('已检查地址 0x100～0x16b'));
const memberEvidence=prefixRow('records[idx].member',26,[0,26],[{start:0,end:26,status:'defined'}]);
Object.assign(memberEvidence.index_accesses[0].prefix_check,{member_path:'member',element_stride:16,evidence_version:1,element_bytes:4,checked_bytes:108,target_bytes:108,queried_bytes:108,start_address:'0x104',end_address:'0x2a7',queries:[]});
actualLine=lineSummary('use(records[idx].member);',[],[],'use(records[idx].member);',[memberEvidence]);
assert(actualLine.includes('records[*].member：下标 0～26'));
assert(actualLine.includes('只查询成员 member；结构体步长 16 字节'));
assert(actualLine.includes('地址首尾之间的其他成员不在检查范围内'));
memberEvidence.index_accesses[0].bounds=null;
Object.assign(memberEvidence.index_accesses[0].prefix_check,{bounds:null,range_basis:'index_probe'});
actualLine=lineSummary('consume(records[idx].member);',[],[],'consume(records[idx].member);',[memberEvidence]);
assert(actualLine.includes('仅为按索引推导的待检查范围，分配长度和合法数组边界未知'));
assert(actualLine.includes('未确认分配长度；可访问不等于未越过原对象边界'));
const failedMember=prefixRow('retry[idx].member',26,[0,26],[{start:0,end:0,status:'defined'},{start:1,end:1,status:'unknown'},{start:2,end:26,status:'defined'}],26,false);
Object.assign(failedMember.index_accesses[0].prefix_check,{member_path:'member',attempted_elements:27,failed_elements:1,query_errors:[{start:1,end:1,address:'0x108',bytes:4,reason:'format mismatch',response:'unavailable'}]});
actualLine=lineSummary('use(retry[idx].member);',[],[],'use(retry[idx].member);',[failedMember]);
assert(actualLine.includes('尝试检查 27 项；成功核对 26 项；查询失败 1 项'));
assert(actualLine.includes('2～26：初始化及内存可访问性检查通过'));
assert(actualLine.includes('查询失败区间 1～1，地址 0x108'));
assert(actualLine.includes('原始返回：unavailable'));
actualLine=lineSummary('use(stale[idx]);',[],[],'use(stale[idx]);',[prefixRow('stale[idx]',26,[0,0],[{start:0,end:0,status:'defined'}],1)]);
assert(actualLine.includes('范围检查记录与当前下标不一致'));assert(!actualLine.includes('0～0：初始化及内存可访问性检查通过'));
actualLine=lineSummary('use(part[idx]);',[],[],'use(part[idx]);',[prefixRow('part[idx]',26,[0,26],[{start:0,end:0,status:'defined'},{start:1,end:26,status:'unchecked'}],1,false)]);
assert(actualLine.includes('1～26：未检查'));
assert(descendants(frame.children[0]).find(n=>n.dataset.arrayPrefix==='yes').open);
function legacyIndices(name, accesses){
 const source='consume('+name+');';
 return lineSummary(source,[],[],source,[{name,status:'unavailable',value:'boundary unknown',initialization:{status:'unknown'},index_accesses:accesses}]);
}
actualLine=legacyIndices('data[ctx->index]',[{expression:'data[ctx->index]',actual_index:7,bounds:null}]);
assert(actualLine.includes('下标 ctx->index = 7；data 的下标范围 未知'));
assert(!actualLine.includes('名称未记录'));assert(!actualLine.includes('访问 data'));
actualLine=legacyIndices('matrix[row][col]',[2,7].map(actual_index=>({expression:'matrix[row][col]',actual_index,bounds:[0,3]})));
assert(actualLine.includes('下标 row = 2；matrix 的下标范围 0～3'));
assert(actualLine.includes('下标 col = 7；matrix[row] 的下标范围 0～3'));
actualLine=legacyIndices('data[indices[k]]',[1,9].map(actual_index=>({expression:'data[indices[k]]',actual_index,bounds:null})));
assert(actualLine.includes('下标 k = 1；indices 的下标范围'));
assert(actualLine.includes('下标 indices[k] = 9；data 的下标范围'));
actualLine=legacyIndices('objects[i].items[j]',[2,8].map(actual_index=>({expression:'objects[i].items[j]',actual_index,bounds:null})));
assert(actualLine.includes('下标 i = 2；objects 的下标范围'));
assert(actualLine.includes('下标 j = 8；objects[i].items 的下标范围'));
actualLine=legacyIndices('partial[row][col]',[{expression:'partial[row][col]',actual_index:7,bounds:null}]);
assert(actualLine.includes('无法唯一对应'));assert(!actualLine.includes('下标 col = 7'));assert(!actualLine.includes('下标 row = 7'));
actualLine=lineSummary('use(array[index]);',[],[{expression:'array[index]',actual_index:9,bounds:[0,3],status:'out_of_bounds'}]);
assert(actualLine.includes('下标 index = 9；array 的下标范围'));
assert(!actualLine.includes('array[index]：下标'));assert(!actualLine.includes('名称未记录'));
lineSummary('use(count, rxvec->abc);', [], [], 'use(count, rxvec->abc);', [selfValue,fieldValue]);
const variableArea=frame.children[0].children.find(n=>n.children?.some(c=>c.dataset?.variableGroup));
assert.strictEqual(variableArea.children.filter(n=>n.dataset?.variableGroup).length,2);
assert(variableArea.children.every(n=>n.style.cssText.includes('border-bottom')));
let rangeText=lineSummary('use(count);',[],[],'use(count);',[selfValue],{block_bytes:288,range_explanation:'LEGACY_RANGE_0_287'});
assert(!rangeText.includes('LEGACY_RANGE_0_287'));assert(rangeText.includes('旧现场未保存地址与内存块'));
rangeText=lineSummary('use( count );',[],[],'use( count );',[selfValue],{block_bytes:288,range_evidence_version:2,range_explanation:'BLOCK_REFERENCE',range_evidence:'Address 0x300 is 0 bytes after a block of size 288'});
assert(rangeText.includes('BLOCK_REFERENCE'));assert(rangeText.includes('字节偏移，不是变量下标'));assert(rangeText.includes('范围依据：Address 0x300'));
actualLine=lineSummary('return count;', [{...selfValue,initialization:{status:'defined',member_states:[]}}]);
assert(actualLine.includes('实际值：41'));assert(actualLine.includes('1. count'));
let lineText=lineSummary('return value;', [scalar('value','731')]);
assert(lineText.includes('实际值：731'));assert(lineText.includes('检查结果：未初始化'));
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
assert(lineText.includes('本行表达式 1 个（不是数组元素个数）'));assert(lineText.includes('实际值：123'));
assert(lineText.includes('检查结果：未初始化'));assert(!lineText.includes('unrelated'));assert(!lineText.includes('WRONG_FRAME_VALUE'));
lineText=lineSummary('return table[i];', [],[{expression:'table[i]',base_expression:'table',index_expression:'i',status:'out_of_bounds',actual_index:99,bounds:[0,2]}]);
assert(lineText.includes('下标 i = 99；table 的下标范围 0～2'));
const mergedArea=frame.children[0].children.find(n=>n.children?.some(c=>c.dataset?.variableGroup));
assert.strictEqual(mergedArea.children.filter(n=>n.dataset?.variableGroup==='table[i]').length,1);
assert(mergedArea.textContent.includes('【异常】'));
lineText=lineSummary('return other_table[i];', [],[{expression:'table[i]',status:'out_of_bounds',actual_index:99,bounds:[0,2]}]);
assert(!lineText.includes('table[i]：索引越界'));
lineText=lineSummary('return expected;', [scalar('wrong','WRONG_LINE')], [], 'return wrong;');
assert(!lineText.includes('WRONG_LINE'));assert(!lineText.includes('wrong：未初始化'));
assert(lineText.includes('变量查看器 v36'));
frame.children=[];parentFrame.children=[];
window.aivCaptureUpdate({live:false,replay_id:'restore-struct',items:[{snapshot:struct}]});
// A deduplicated report keeps one complete capture, never a mix of two stops.
const first=runtime('bad.c'),second=runtime('bad.c');
first.frames[1].variables[0].value='FIRST_STOP';second.frames[1].variables[0].value='SECOND_STOP';
report.errors[1].records=2;frame.children=[];parentFrame.children=[];
window.aivCaptureUpdate({live:false,replay_id:'dedup-test',items:[{id:'first',snapshot:first},{id:'second',snapshot:second}]});
assert(frame.textContent.includes('FIRST_STOP'));assert(!frame.textContent.includes('SECOND_STOP'));
// A later readable stop replaces an empty stop as a whole, independent of report order.
const empty=runtime('bad.c');empty.frames[1].variables[0].status='unavailable';
empty.frames[1].variables[0].value='EMPTY_STOP';
frame.children=[];parentFrame.children=[];
window.aivCaptureUpdate({live:true,replay_id:'retry-test',items:[{id:'empty',snapshot:empty}]});
assert(frame.textContent.includes('EMPTY_STOP'));
report.errors.reverse();
window.aivCaptureUpdate({live:true,replay_id:'retry-test',items:[{id:'empty',snapshot:empty},{id:'success',snapshot:second}]});
assert(frame.textContent.includes('SECOND_STOP'));assert(!frame.textContent.includes('EMPTY_STOP'));
assert(frame.textContent.includes('未混合多次停止'));
report.errors.reverse();
frame.children=[];parentFrame.children=[];
window.aivCaptureUpdate({live:false,replay_id:'restore-again',items:[{snapshot:struct}]});
// Runtime categories are matched by diagnostic + stack, not a type allowlist.
const target=report.errors[1],savedKind=target.kind,savedWhat=target.what;
for(const [kind,what] of [['UninitCondition','Conditional jump or move depends on uninitialised value(s)'],['UninitValue','Use of uninitialised value of size 8'],['InvalidFree','Invalid free() / delete / delete[] / realloc()'],['SyscallParam','Syscall param write(buf) points to uninitialised byte(s)']]){
 target.kind=kind;target.what=what;const sample=runtime('bad.c');sample.valgrind_error=what;
 window.aivCaptureUpdate({live:false,replay_id:kind,items:[{id:kind,snapshot:sample}]});
 assert(nodes['capture-status'].textContent.includes(kind+'：已读值 1/1'));
}
target.kind=savedKind;target.what=savedWhat;
report.errors.push({id:'leak',kind:'Leak_DefinitelyLost',what:'leak',stacks:[]});
window.aivCaptureUpdate({live:false,replay_id:'restore-after-categories',items:[{snapshot:struct}]});
assert(nodes['capture-status'].textContent.includes('Leak_DefinitelyLost：已读值 0/1'));
assert(nodes['capture-status'].textContent.includes('历史分配栈'));report.errors.pop();
// Changed source text disables line-level claims instead of guessing.
report.errors[1].stacks[0].frames[1].source=[{number:10,text:'different_statement();'}];
frame.children=[];detail(report.errors[1]);
const changedPanel=frame.children[0],changedAll=changedPanel.children.find(n=>n.dataset?.captureEvidence==='yes');
assert(changedPanel.textContent.includes('源码行不同'));
assert(!changedPanel.children.filter(n=>n!==changedAll).map(n=>n.textContent).join('').includes('request.length = 99'));
window.aivCaptureUpdate({live:true,report_key:'different-report',items:[{snapshot:struct}]});
assert(nodes['capture-status'].textContent.includes('另一份步骤二报告'));
window.aivCaptureUpdate({live:false,replay_id:'new-replay',items:[]});
assert(nodes['capture-status'].textContent.includes('已关联 0 个错误'));
'''
        script = SCRIPT.replace('<script>', '').replace('</script>', '')
        result = subprocess.run(['node'], input=setup+script+checks, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
