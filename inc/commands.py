# -*- coding: utf-8 -*-
"""Resolve report debug commands once, on the host generating the report."""
import json
import ntpath
import posixpath
from pathlib import Path
import sys


def absolute_path(value, base):
    if ntpath.isabs(value) and ntpath.splitdrive(value)[0]:
        return ntpath.normpath(value)
    if posixpath.isabs(value):
        return posixpath.normpath(value)
    if posixpath.isabs(str(base)):
        return posixpath.normpath(posixpath.join(str(base), value))
    return str((Path(base) / value).resolve())


def command_metadata(root, xml_path, project=None):
    xml_path = Path(xml_path).resolve()
    project = str(Path(project).resolve()) if project else ''
    cwd = project or str(xml_path.parent)
    target, stdin_file, source = [], '', 'xml'
    executable = root.findtext('args/argv/exe', '')
    if executable:
        target = [absolute_path(executable, cwd)] + [n.text or '' for n in root.findall('args/argv/arg')]
    # Only our own adjacent collection metadata, bound to the exact XML name.
    metadata = xml_path.parent / 'run.json'
    if metadata.is_file():
        try:
            saved = json.loads(metadata.read_text(encoding='utf-8'))
            if not isinstance(saved, dict):
                saved = {}
            command = saved.get('command')
            if (saved.get('xml_file') == xml_path.name and saved.get('schema') == 1
                    and isinstance(command, list) and command and command[0] and all(isinstance(x, str) for x in command)
                    and isinstance(saved.get('cwd'), str) and saved['cwd']):
                cwd = absolute_path(saved['cwd'], xml_path.parent)
                target = [absolute_path(command[0], cwd)] + command[1:]
                stdin_file = saved.get('stdin_file') or ''
                if not isinstance(stdin_file, str):
                    stdin_file = ''
                if stdin_file:
                    stdin_file = absolute_path(stdin_file, cwd)
                source = 'collection'
        except (OSError, ValueError, TypeError):
            pass
    base = [str(Path(sys.executable).resolve()), str(Path(__file__).resolve().parents[1] / 'main.py'),
            '--action', 'debug', '--retPath', str(xml_path.parent)]
    if xml_path.name != 'errors.xml':
        base += ['--xml', str(xml_path)]  # Compatibility for previously exported reports.
    if stdin_file:
        base += ['--stdin-file', stdin_file]
    program_args = []
    if target:
        path_module = ntpath if ntpath.splitdrive(target[0])[0] else posixpath
        program_args = ['--progPath', path_module.dirname(target[0]), '--exeName', path_module.basename(target[0])]
        if len(target) > 1:
            program_args += ['--exeArgs'] + target[1:]
    return {'program_args': program_args, 'cwd': cwd, 'project': project, 'base_args': base, 'target_args': target, 'source': source,
            'ready': bool(target), 'note': ('已使用绝对程序路径和结果目录；运行目录与源码目录自动恢复或推导。' if target else
                '已带入结果目录；XML 未记录可执行程序，无法自动生成完整调试命令。'),
            'cwd_note': '' if source == 'collection' else '原 XML 未记录工作目录，使用工程目录（未提供时使用 XML 所在目录）。'}
