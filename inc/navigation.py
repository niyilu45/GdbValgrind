"""GDB commands for navigating report locations in an existing session."""

GDB_NAVIGATION_SCRIPT = r'''
import gdb
import json
from pathlib import Path

class ReportNavigation:
    def __init__(self, config):
        self.config = config
        self.entries = {e['id']: e for e in config['entries']}
        self.current = None
        self.seen = set()
        self.exited = False
        self.target = None if config['auto_values'] else config['initial']
        self.breakpoints = []
        self.active = None
        # Install only the requested location. Preinstalling the whole report
        # probes unavailable library symbols and slows every continue command.
        if self.target:
            self.active = self.create_location(self.entries[self.target])
        if self.target and self.active is None:
            raise gdb.GdbError('目标错误没有可解析的源码或符号断点，未继续程序。请检查可执行文件与报告是否一致、是否带调试符号，或使用 --frame 选择工程调用帧。')
        gdb.events.exited.connect(self.on_exit)

    def create_location(self, entry, spec=None):
        candidates = [spec] if spec else entry.get('candidates', [entry['default']])
        failures = []
        for candidate in candidates:
            if not candidate:
                continue
            try:
                bp = ReportLocation(self, entry['id'], candidate)
                if getattr(bp, 'pending', False):
                    bp.delete()
                    failures.append(candidate + '：符号尚未解析')
                    continue
                self.breakpoints.append(bp)
                if candidate != entry['default']:
                    gdb.write('[AiValgrind] 当前目标改在调用帧暂停：' + candidate + '（不代表已到达实际出错指令）\n')
                return bp
            except Exception as exc:
                failures.append(candidate + '：' + str(exc))
        gdb.write('[AiValgrind] 目标错误第 %d 条的 %d 个候选位置均无法解析，程序保持暂停。可能缺少调试符号、相关库尚未加载或可执行文件与报告不一致。\n' % (entry['order'], len(failures)))
        for failure in failures[:3]:
            gdb.write('  ' + failure + '\n')
        return None

    def on_exit(self, event):
        self.exited = True
        gdb.write('[AiValgrind] 程序已结束；如未到达目标位置，本次运行可能未经过该路径。再次使用 aiv-goto 将询问是否重新运行。\n')

    def restart(self, target, frame):
        answer = input('待调试内存错误位置位于当前调试位置之前，或程序已结束，需要重新运行程序，是否执行？[y/n] ').strip().lower()
        if answer != 'y':
            gdb.write('[AiValgrind] 已忽略，保留当前调试现场。\n')
            return
        Path(self.config['restart_file']).write_text(json.dumps({'id': target, 'frame': frame}), encoding='utf-8')
        gdb.execute('set confirm off')
        gdb.execute('quit')

    def navigate(self, text):
        args = gdb.string_to_argv(text)
        if not 1 <= len(args) <= 2 or args[0] not in self.entries:
            raise gdb.GdbError('用法：aiv-goto 错误ID [调用栈序号:帧序号]；请从同一报告复制命令。')
        target = args[0]
        frame = args[1] if len(args) == 2 else None
        entry = self.entries[target]
        spec = entry['frames'].get(frame) if frame else entry['default']
        if not spec:
            raise gdb.GdbError('此错误没有可用断点位置，或帧序号无效。')
        if self.exited or (self.current is not None and
                (target in self.seen or entry['order'] < self.entries[self.current]['order'])):
            return self.restart(target, frame)
        bp = self.create_location(entry, spec if frame else None)
        if bp is None:
            raise gdb.GdbError('目标断点未解析，保留当前现场且不继续。请核对源码/符号，或指定工程调用帧。')
        if self.active is not None:
            self.active.enabled = False
        self.active = bp
        self.target = target
        gdb.write('[AiValgrind] 继续到报告第 %d 条的源码位置；其他断点、信号或内存错误仍可能提前暂停。\n' % entry['order'])
        gdb.execute('continue')

class ReportLocation(gdb.Breakpoint):
    def __init__(self, navigator, identity, spec):
        self.navigator = navigator
        self.identity = identity
        # Pending creation prints missing-symbol messages even for internal
        # breakpoints. Fail directly and let navigation report one summary.
        pending = gdb.parameter('breakpoint pending')
        try:
            gdb.execute('set breakpoint pending off', to_string=True)
            super().__init__(spec, internal=True)
        finally:
            setting = 'auto' if pending is None else 'on' if pending else 'off'
            gdb.execute('set breakpoint pending ' + setting, to_string=True)

    def stop(self):
        nav = self.navigator
        nav.seen.add(self.identity)
        nav.current = self.identity
        stop = self is nav.active
        if stop:
            gdb.write('[AiValgrind] 已到达报告第 %d 条的源码位置（不代表已触发该内存错误）。\n' % nav.entries[self.identity]['order'])
        return stop

class ReportGoto(gdb.Command):
    def __init__(self, navigator):
        self.navigator = navigator
        super().__init__('aiv-goto', gdb.COMMAND_RUNNING)

    def invoke(self, arg, from_tty):
        self.dont_repeat()
        self.navigator.navigate(arg)

_aiv_navigation = ReportNavigation(_navigation_config)
_aiv_goto = ReportGoto(_aiv_navigation)
gdb.write('[AiValgrind] 可粘贴网页的 aiv-goto 命令继续调试其他错误；回到已到达的位置需确认重新运行。\n')
'''
