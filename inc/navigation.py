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
        for entry in config['entries']:
            if entry['default']:
                bp = ReportLocation(self, entry['id'], entry['default'])
                self.breakpoints.append(bp)
                if entry['id'] == self.target:
                    self.active = bp
        gdb.events.exited.connect(self.on_exit)

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
        bp = ReportLocation(self, target, spec)
        self.breakpoints.append(bp)
        self.active = bp
        self.target = target
        gdb.write('[AiValgrind] 继续到报告第 %d 条的源码位置；其他断点、信号或内存错误仍可能提前暂停。\n' % entry['order'])
        gdb.execute('continue')

class ReportLocation(gdb.Breakpoint):
    def __init__(self, navigator, identity, spec):
        self.navigator = navigator
        self.identity = identity
        super().__init__(spec, internal=True)

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
