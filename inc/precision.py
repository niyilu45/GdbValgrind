"""Select diagnostics from the installed Valgrind, without running the target."""
import re
import subprocess


def select_precision(version, help_text, debugging=False):
    match = re.search(r'valgrind-(\d+)\.(\d+)(?:\.(\d+))?', version, re.I)
    profile = {'version': version.strip()[:300] or 'unknown', 'options': [], 'skipped': [],
               'register_precision': 'unknown', 'mode': 'diagnostic'}
    if not match:
        if debugging:
            profile['options'].append('--vgdb=yes')
        profile['skipped'].append('无法确认 Valgrind 版本，保留基础参数，不声称高精度已启用')
        return profile
    def supports(name, value=None):
        entry = re.search(r'(?m)^\s*' + re.escape(name) + r'(?=[=<\s]|$)[^\n]*(?:\n(?!\s*--)[^\n]*)*', help_text)
        return bool(entry and (value is None or value in entry.group()))
    def add(name, value, check_value=False):
        if supports(name, value if check_value else None):
            profile['options'].append(name+'='+value)
            return True
        profile['skipped'].append(name+'='+value+'：当前安装的帮助信息未确认支持')
        return False
    for name, value in (
        ('--read-var-info', 'yes'), ('--read-inline-info', 'yes'),
        ('--num-callers', '40'), ('--keep-debuginfo', 'yes'),
        ('--keep-stacktraces', 'alloc-and-free'), ('--freelist-vol', '100000000'),
        ('--expensive-definedness-checks', 'yes')):
        add(name, value, name in ('--keep-stacktraces', '--expensive-definedness-checks'))
    if debugging:
        profile['mode'] = 'debug'
        if add('--vgdb', 'full', True):
            profile['register_precision'] = 'allregs-at-each-insn'
        else:
            profile['options'].append('--vgdb=yes')
        for value in ('allregs-at-each-insn', 'allregs-at-mem-access'):
            if profile['register_precision'] == 'allregs-at-each-insn':
                break
            if supports('--vex-iropt-register-updates', value):
                profile['options'].append('--vex-iropt-register-updates='+value)
                # full already implies each instruction, never downgrade it.
                if profile['register_precision'] == 'unknown':
                    profile['register_precision'] = value
                break
        add('--vgdb-shadow-registers', 'yes')
    return profile


def probe_precision(path, debugging=False):
    outputs, failures = {}, []
    for arg in ('--version', '--help-debug'):
        try:
            result = subprocess.run([path, '--tool=memcheck', arg], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, errors='replace', timeout=5)
            outputs[arg] = result.stdout if result.returncode == 0 else ''
            if result.returncode:
                failures.append(arg+' 查询失败')
        except (OSError, subprocess.TimeoutExpired) as exc:
            outputs[arg] = ''
            failures.append(arg+' 查询失败：'+str(exc))
    profile = select_precision(outputs['--version'], outputs['--help-debug'], debugging)
    profile['skipped'].extend(failures)
    return profile


def precision_lines(profile):
    return ['Valgrind 实际版本：'+profile['version'],
            '实际增强参数：'+(' '.join(profile['options']) or '无'),
            '寄存器精度：'+profile['register_precision'],
            '高精度会降低运行速度；释放块保留预算最多 100 MB（启用时）。'] + profile['skipped']
