"""Display versions of tools used by a CLI workflow, with bounded probes."""
import platform
import shutil
import subprocess
import sys
import sqlite3


def version_requirements(feature, debug_enabled=False):
    rows = ['Python 要求: 3.9 及以上']
    if feature in ('collect', 'analyze', 'debug', 'auto-values') or debug_enabled:
        rows.append('Valgrind 启动参数最低版本: 3.9.0（3.8.x 不兼容）')
    if feature in ('analyze', 'debug', 'auto-values') or debug_enabled:
        rows.append('GDB 适配目标版本: 10.1 / 10.2 / 13.1 / 15.2')
        rows.append('自动变量采集另需 GDB 内置 Python；版本号不代表已验证组合')
    return rows


def print_tool_versions(feature, debug_enabled=False):
    info = {'feature': feature, 'tools': ['Python ' + platform.python_version()],
            'requirements': version_requirements(feature, debug_enabled)}
    print('[运行环境] 功能: ' + feature, flush=True)
    print('  Python: %s | %s' % (platform.python_version(), sys.executable), flush=True)
    if feature in ('collect', 'analyze'):
        info['tools'].append('SQLite ' + sqlite3.sqlite_version)
        print('  SQLite: ' + sqlite3.sqlite_version + '（Python 内置，增量结果库）', flush=True)
    for row in info['requirements']:
        print('  ' + row, flush=True)
    names = []
    if feature in ('collect', 'analyze', 'debug', 'auto-values') or debug_enabled:
        names.append('valgrind')
    if feature in ('analyze', 'debug', 'auto-values') or debug_enabled:
        names.extend(('gdb', 'vgdb'))
    for name in names:
        path = shutil.which(name)
        if not path:
            info['tools'].append(name + ': 未找到')
            print('  %s: 未找到（PATH）' % name, flush=True)
            continue
        print('  %s: %s | 正在读取版本（最多 3 秒）' % (name, path), flush=True)
        command = [path, '--version']
        if name == 'gdb':
            command = [path, '-q', '-nx', '-nh', '--version']
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    timeout=3, text=True, errors='replace')
            lines = result.stdout.strip().splitlines()
            version = lines[0][:300] if lines else '没有版本输出'
            if result.returncode:
                version = '版本查询失败（退出码 %s）：%s' % (result.returncode, version)
        except subprocess.TimeoutExpired:
            version = '版本查询超时；未确认版本'
        except OSError as exc:
            version = '版本查询失败：' + str(exc)
        print('    ' + version, flush=True)
        info['tools'].append(name + ': ' + version)
    if not names:
        print('  此功能只需 Python 标准库，不调用 Valgrind / GDB / vgdb。', flush=True)
    return info

