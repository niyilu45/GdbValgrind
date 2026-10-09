"""Public AiValgrind library API (Python 3.9+). Importing starts no processes."""
from .api import DebugOptions, debug_error, export_report, serve_report
from .core import load_report, render_html
from .collect import collect_run

__all__ = ["DebugOptions", "load_report", "render_html", "export_report", "debug_error", "serve_report", "collect_run"]
