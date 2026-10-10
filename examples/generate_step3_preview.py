"""Generate the README preview using the real renderer and labeled sample values."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from inc.core import load_report, render_html
from inc.capture_updates import SCRIPT


def main():
    report = load_report(ROOT/'examples/sample.xml', ROOT)
    error = report['errors'][0]
    frames = []
    for index, frame in enumerate(error['stacks'][0]['frames']):
        frames.append({'file':frame['local_file'], 'line':int(frame['line']),
                       'function':frame['fn'], 'variables':
                       [{'name':'p', 'value':'0x1000', 'initialization':{'state':'defined'}}] if index==0
                       else [{'name':'i', 'value':'0'}]})
    snapshot = {'captured_at':'示例现场（演示数据，非实机采集）',
                'association':{'status':'verified','note':'Synthetic demo, not a live capture'},
                'valgrind_error':error['what'], 'frames':frames,
                'memory':{'explanation':'写入越过分配块末尾。',
                          'range_explanation':'内存块 4 字节，写入 4 字节；合法起始偏移 0，实际起始偏移 4，访问偏移 4～7。'}}
    payload = json.dumps({'live':False,'items':[{'id':'example','snapshot':snapshot}]},ensure_ascii=True)
    appendix = '<section style="padding:24px"><p id="capture-status"></p></section>' + SCRIPT
    appendix += '<script>window.aivCaptureUpdate('+payload+');document.getElementById("showBlame").checked=false;document.getElementById("showBlame").onchange();</script>'
    html = render_html(report, source_base=None).replace('</body>',appendix+'</body>')
    html = html.replace('<body>', '<body><p style="margin:0;padding:10px 30px;background:#fff2d7">步骤三效果示例 · 使用真实报告界面，变量及地址为演示数据</p>',1)
    destination = ROOT/'docs/images'
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'step3-preview.html').write_text(html,encoding='utf-8')


if __name__ == '__main__':
    main()
