# -*- coding: utf-8 -*-
"""Reusable functions; failures raise exceptions instead of exiting the host."""
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Optional, Sequence, Union

from . import core

PathLike = Union[str, Path]


@dataclass(frozen=True)
class DebugOptions:
    """Execution settings shared by direct debugging and the report server.

    command is an argument sequence, e.g. ['./app', '--input', 'file with spaces'].
    No shell parsing is performed. Paths are relative to the caller's directory;
    only the target executable and its arguments are interpreted in cwd.
    """
    command: Sequence[str]
    cwd: Optional[PathLike] = None
    stdin_file: Optional[PathLike] = None
    auto_values: bool = False
    capture_dir: PathLike = "captures"
    stop_on_error: bool = False

    def __post_init__(self):
        if isinstance(self.command, (str, bytes)) or not self.command:
            raise ValueError("command 必须是非空参数列表，例如 ['./app', 'arg1']")
        if any(not isinstance(arg, str) or '\0' in arg for arg in self.command):
            raise ValueError("command 的每个参数必须是无 NUL 字符的字符串")
        if not self.command[0]:
            raise ValueError("可执行程序名称不能为空")
        object.__setattr__(self, "command", tuple(self.command))


def _arguments(options, project_dir, frame=None, port=8765, open_browser=False):
    return SimpleNamespace(
        command=list(options.command) if options else [],
        cwd=options.cwd if options else None,
        stdin_file=options.stdin_file if options else None,
        auto_values=options.auto_values if options else False,
        capture_dir=options.capture_dir if options else "captures",
        stop_on_error=options.stop_on_error if options else False,
        project_dir=project_dir, frame=frame, port=port, open=open_browser,
    )


def export_report(xml_path: PathLike, output_path: PathLike = "report.html", *,
                  project_dir: Optional[PathLike] = None) -> dict:
    """Parse/deduplicate XML, save standalone HTML and return the report dictionary.

    Does not run the target. The output's parent directory must already exist.
    """
    if Path(xml_path).resolve() == Path(output_path).resolve():
        raise ValueError("输出文件不能覆盖输入 XML")
    report = core.load_report(xml_path, project_dir)
    core.save_report(report, output_path)
    return report


def debug_error(report: dict, error_id: str, options: DebugOptions, *,
                frame: Optional[str] = None) -> int:
    """Debug one selected report entry, returning GDB's exit code.

    Requires Linux and an interactive terminal. auto_values captures the first
    occurrence of each runtime diagnostic, not necessarily the selected old one.
    """
    error = next((item for item in report["errors"] if item["id"] == error_id), None)
    if error is None:
        raise ValueError("找不到错误 ID: " + error_id)
    args = _arguments(options, report.get("project") or None, frame)
    args.navigation_errors = report['errors']
    return core.run_debug(error, args)


def serve_report(report: dict, *, options: Optional[DebugOptions] = None,
                 port: int = 8765, open_browser: bool = False) -> None:
    """Block serving a loopback report until Ctrl+C; run in the main thread.

    With no options, this is read-only browsing and needs no GDB installation.
    Providing options enables the debug button and requires an interactive TTY.
    """
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port 必须在 0 到 65535 之间（0 为自动分配）")
    core.serve(report, _arguments(options, report.get("project") or None,
                                 port=port, open_browser=open_browser))
