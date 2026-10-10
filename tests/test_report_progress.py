import io
import os
import unittest
import unicodedata
from unittest.mock import patch

from inc.terminal import ReportProgress


class ProgressTests(unittest.TestCase):
    def test_chinese_progress_never_wraps(self):
        for columns in (10, 40, 80):
            output = io.StringIO()
            output.isatty = lambda: True
            with patch('sys.stdout', output), patch('inc.terminal.shutil.get_terminal_size',
                                                   return_value=os.terminal_size((columns,24))):
                progress = ReportProgress('解析与准备报告')
                progress('源码与 blame | 1000 / 100000 帧位置')
                progress.render()
                progress('完成')
                progress.render()
            self.assertNotIn('\n', output.getvalue())
            for line in output.getvalue().split('\r')[1:]:
                width = sum(0 if unicodedata.combining(c) else
                            2 if unicodedata.east_asian_width(c) in ('W','F') else 1 for c in line)
                self.assertEqual(width, columns-1)

    def test_redirected_output_has_no_periodic_lines(self):
        output = io.StringIO()
        with patch('sys.stdout', output):
            progress = ReportProgress('生成报告')
            for _ in range(20):
                progress.render()
            self.assertEqual(output.getvalue(), '')
            progress.render(final=True)
        self.assertEqual(output.getvalue().count('\n'), 1)
