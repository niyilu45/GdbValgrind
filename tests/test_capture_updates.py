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
global.window={};global.document={head:new Element(),getElementById:id=>id==='capture-items'?area:status,createElement:()=>new Element()};
global.setTimeout=()=>1;global.clearTimeout=()=>{};
'''
        checks = r'''
window.aivCaptureUpdate({live:true,items:[{id:'one',text:'x=1'}]});
const original=area.children[0];original.open=true;
window.aivCaptureUpdate({live:true,items:[{id:'one',text:'x=2'},{id:'two',text:'y=3'}]});
assert.strictEqual(area.children[0],original);
assert.strictEqual(original.open,true);
assert.strictEqual(original.querySelector().textContent,'x=2');
assert.strictEqual(area.children.length,2);
window.aivCaptureUpdate({live:false,items:[{id:'one',text:'x=2'},{id:'two',text:'y=3'}]});
assert.strictEqual(area.children.length,2);
'''
        script = SCRIPT.replace('<script>', '').replace('</script>', '')
        result = subprocess.run(['node','-e',setup+script+checks], capture_output=True, text=True)
        self.assertEqual(result.returncode,0,result.stderr)
