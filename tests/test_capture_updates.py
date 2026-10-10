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
 constructor(){this.children=[];this.dataset={};this.style={};this.textContent='';}
 append(...nodes){this.children.push(...nodes)}
 querySelector(){return this.children[1]}
 querySelectorAll(){return this.children}
 remove(){}
}
const area=new Element(),status=new Element();
const frame=new Element();frame.dataset.frameKey='0:0';frame.open=true;area.append(frame);
global.report={errors:[{id:'one',what:'Invalid write of size 4',stacks:[{frames:[{local_file:'/src/a.c',line:10,fn:'main'}]}]}]};
global.selected='one';global.detail=()=>{};
global.window={};global.document={head:new Element(),getElementById:id=>id==='detail'?area:status,createElement:()=>new Element()};
global.setTimeout=()=>1;global.clearTimeout=()=>{};
'''
        checks = r'''
const snapshot={requested_error_id:'wrong',valgrind_error:'Invalid write of size 4',frames:[{file:'/src/a.c',line:10,function:'main',variables:[{name:'x',value:'42'}]}]};
window.aivCaptureUpdate({live:true,items:[{id:'capture',snapshot}]});
const panel=frame.children[0];assert(panel.textContent.includes('x = 42'));
assert.strictEqual(frame.open,true);
window.aivCaptureUpdate({live:true,items:[{id:'capture',snapshot}]});
assert.strictEqual(frame.children[0],panel);assert.strictEqual(frame.children.length,1);
const other={...snapshot,valgrind_error:'Invalid read of size 4'};
window.aivCaptureUpdate({live:false,items:[{id:'other',snapshot:other}]});
assert.strictEqual(frame.children.length,1);
assert(status.textContent.includes('1 个现场未能唯一匹配'));
'''
        script = SCRIPT.replace('<script>', '').replace('</script>', '')
        result = subprocess.run(['node','-e',setup+script+checks], capture_output=True, text=True)
        self.assertEqual(result.returncode,0,result.stderr)
