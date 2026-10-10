#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility entry point. Core implementation lives in inc/."""
from pathlib import Path
import runpy

# Execute source directly before Python imports even inc/__init__.py.
runpy.run_path(str(Path(__file__).resolve().parent / 'inc' / 'bootstrap.py'))
from inc.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
