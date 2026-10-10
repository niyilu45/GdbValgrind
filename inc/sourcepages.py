"""Separate, escaped source pages for offline reports and the local server."""
import html
import re
from pathlib import Path
from urllib.parse import quote


def source_page(file):
    def color(text):
        pattern = r'//.*|"(?:\\.|[^"\\])*"|\b(?:if|else|for|while|return|struct|class|const|static|void|int|char|sizeof|unsigned|break)\b|\b[0-9]+\b'
        result, end = [], 0
        for m in re.finditer(pattern, text):
            result.append(html.escape(text[end:m.start()]))
            cls = 'comment' if m[0].startswith('//') else 'string' if m[0].startswith('"') else 'number' if m[0].isdigit() else 'keyword'
            result.append('<span class="%s">%s</span>' % (cls, html.escape(m[0])))
            end = m.end()
        return ''.join(result) + html.escape(text[end:])
    rows = ''.join('<tr id="L{0}"><td class="num"><a href="#L{0}">{0}</a></td><td class="code">{1}</td></tr>'.format(i + 1, color(line)) for i, line in enumerate(file['lines']))
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + html.escape(file['path']) + '</title><style>body{margin:0;color:#1f2328;background:white;font:14px/1.5 system-ui}header{padding:16px 24px;background:#f6f8fa;border-bottom:1px solid #d1d9e0;overflow-wrap:anywhere}.file{margin:24px;border:1px solid #d1d9e0;border-radius:6px;overflow:auto}table{border-collapse:collapse;width:100%;font:12px/20px ui-monospace,Consolas,monospace}.num{width:1%;min-width:60px;padding:0 16px;text-align:right;user-select:none}.num a{color:#59636e;text-decoration:none}.code{white-space:pre;tab-size:4;padding-right:16px}td{vertical-align:top}tr:target{background:#fff8c5}.keyword{color:#cf222e}.string{color:#0a3069}.number{color:#0550ae}.comment{color:#6e7781}@media(max-width:600px){.file{margin:8px}}</style><header>' + html.escape(file['path']) + '<br>当前工程源码（文件修改后内容可能与错误发生时不同） · 点击行号定位</header><div class="file"><table>' + rows + '</table></div></html>'


def browser_report(report, source_base='/source/'):
    files = report.get('source_files', {})
    errors = []
    for error in report['errors']:
        item = {k: v for k, v in error.items() if k != 'unique_ids'}
        item['stacks'] = error['stacks']
        errors.append(item)
    return {**report, 'source_access': 'server' if source_base is not None else 'local', 'errors': errors, 'source_files': {key: {'path': f['path'], 'url': (source_base + key + '.html') if source_base is not None else 'file:///' + quote(f['path'].replace(chr(92), '/').lstrip('/'), safe='/:' )} for key, f in files.items()}}

