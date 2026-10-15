"""Infer directories for the compact CLI without changing target arguments."""
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET


def project_for(executable):
    parent = Path(executable).resolve().parent
    for candidate in (parent, *parent.parents):
        if (candidate / '.git').exists():
            return candidate
    return parent


def configure_paths(args):
    if args.action == 'refresh-captures':
        return
    command = list(args.command)
    xml = getattr(args, 'xml', None)
    saved = {}
    if xml:
        try:
            data = json.loads((Path(xml).parent / 'run.json').read_text(encoding='utf-8'))
            if isinstance(data, dict) and data.get('schema') == 1 and data.get('xml_file') == Path(xml).name:
                saved = data
        except (OSError, ValueError):
            pass
    old_command = saved.get('command')
    old_cwd = saved.get('cwd')
    valid = (isinstance(old_command, list) and old_command and
             all(isinstance(x, str) for x in old_command) and old_command[0] and
             isinstance(old_cwd, str) and old_cwd)
    candidate = command[0] if command else (old_command[0] if valid else None)
    if valid and not command and args.action in ('analyze', 'debug'):
        command = list(old_command)
    if candidate is None and xml:
        try:
            with Path(xml).open('rb') as stream:
                for event, node in ET.iterparse(stream, events=('end',)):
                    if node.tag == 'args':
                        recorded = node.findtext('argv/exe', '')
                        if recorded and Path(recorded).is_absolute():
                            candidate = recorded
                        break
                    if node.tag == 'error':
                        break
        except (OSError, ET.ParseError):
            pass
    if candidate:
        path = Path(candidate)
        base = getattr(args, 'cwd', None)
        if not path.is_absolute():
            if base:
                path = Path(base) / path
            elif valid and candidate == old_command[0]:
                path = Path(old_cwd) / path
            else:
                local = Path.cwd() / path
                path = local if local.is_file() else Path(shutil.which(candidate) or local)
        if path.is_file():
            path = path.resolve()
            if command:
                command[0] = str(path)
            original = Path(old_command[0]) if valid else None
            if original is not None and not original.is_absolute():
                original = Path(old_cwd) / original
            matches = original is not None and original.resolve() == path
            if not getattr(args, 'cwd', None):
                args.cwd = Path(old_cwd) if matches else path.parent
            if not getattr(args, 'project_dir', None):
                stored_project = saved.get('project_dir') if matches else None
                args.project_dir = Path(stored_project) if isinstance(stored_project, str) and Path(stored_project).is_dir() else project_for(path)
            if matches and getattr(args, 'stdin_file', None) is None and getattr(args, 'cwd', None) == Path(old_cwd):
                stdin = saved.get('stdin_file')
                if isinstance(stdin, str) and stdin:
                    args.stdin_file = str(Path(stdin) if Path(stdin).is_absolute() else Path(old_cwd) / stdin)
    args.command = command
