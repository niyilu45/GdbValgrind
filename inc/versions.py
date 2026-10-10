"""Display versions of tools used by a CLI workflow, with bounded probes."""
import platform
import shutil
import subprocess
import sys


def print_tool_versions(feature, debug_enabled=False):
    print('[运行环境] 功能: ' + feature, flush=True)
    print('  Python: %s | %s' % (platform.python_version(), sys.executable), flush=True)
    names = []
    if feature in ('collect', 'analyze', 'debug', 'auto-values') or debug_enabled:
        names.append('valgrind')
    if feature in ('analyze', 'debug', 'auto-values') or debug_enabled:
        names.extend(('gdb', 'vgdb'))
    for name in names:
        path = shutil.which(name)
        if not path:
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
            print('    ' + version, flush=True)
        except subprocess.TimeoutExpired:
            print('    版本查询超时；未确认版本', flush=True)
        except OSError as exc:
            print('    版本查询失败：' + str(exc), flush=True)
    if not names:
        print('  此功能只需 Python 标准库，不调用 Valgrind / GDB / vgdb。', flush=True)

