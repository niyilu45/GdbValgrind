import http.client
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from inc import core as av
from inc import cli


def frame(ip="0x111", line=15, file="demo.c", fn="bad", directory="/old/project/src"):
    return f"<frame><ip>{ip}</ip><obj>/app/demo</obj><fn>{fn}</fn><dir>{directory}</dir><file>{file}</file><line>{line}</line></frame>"


def error(uid="0x1", kind="InvalidWrite", frames=None, extra="", what="Invalid write of size 4"):
    return f"<error><unique>{uid}</unique><kind>{kind}</kind><what>{what}</what><stack>{frames if frames is not None else frame()}</stack>{extra}</error>"


def xml(errors="", counts=""):
    return f'<?xml version="1.0"?><valgrindoutput><tool>memcheck</tool>{errors}<errorcounts>{counts}</errorcounts><status><state>FINISHED</state></status></valgrindoutput>'


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src/demo.c").write_text("\n".join(f"source line {n}" for n in range(1, 41)))
        self.path = self.root / "input.xml"

    def load(self, content, project=True):
        self.path.write_text(content, encoding="utf-8")
        return av.load_report(self.path, self.root if project else None)

    def test_dedup_addresses_counts_and_context(self):
        content = xml(error()+error("0x2", frames=frame("0x999")), '<pair><count>7</count><unique>0x1</unique></pair><pair><count>3</count><unique>0x2</unique></pair>')
        report = self.load(content)
        self.assertEqual(len(report["errors"]), 1)
        item = report["errors"][0]
        self.assertEqual((item["count"], item["records"]), (10, 2))
        self.assertEqual([l["number"] for l in item["stacks"][0]["frames"][0]["source"]], list(range(5, 26)))
        self.assertEqual(item["id"], self.load(content, False)["errors"][0]["id"])

    def test_duplicate_keys_serialize_once_and_keep_legacy_id(self):
        root = av.ET.fromstring(xml(''.join(error(str(i), frames=frame(ip=hex(i))) for i in range(500))))
        with patch.object(av.json, 'dumps', wraps=json.dumps) as dumps:
            report = av.report_from_root(root, self.path)
            self.assertEqual(dumps.call_count, 1)
        item = report['errors'][0]
        legacy = [item['kind'], av.normalized(item['what']),
                  [(av.diagnostic_label(s['label']), [av.frame_key(f) for f in s['frames']]) for s in item['stacks']]]
        self.assertEqual(item['id'], hashlib.sha256(json.dumps(legacy, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16])
        self.assertEqual(item['records'], 500)

    def test_distinct_callers_types_and_origins(self):
        self.assertEqual(len(self.load(xml(error()+error("2", frames=frame(line=16))+error("3", kind="InvalidRead")))["errors"]), 3)
        origin = '<origin><what>Uninitialised value was created by a stack allocation</what><stack>'+frame(line=8)+'</stack></origin>'
        report = self.load(xml(error(extra=origin)+error("2", extra=origin.replace("<line>8", "<line>9"))))
        self.assertEqual(len(report["errors"]), 2)
        self.assertEqual(len(report["errors"][0]["stacks"]), 2)

    def test_complete_source_shared_across_frames_and_unavailable_without_project(self):
        report = self.load(xml(error() + error('2', frames=frame(line=30))))
        self.assertEqual(len(report['source_files']), 1)
        frames = [e['stacks'][0]['frames'][0] for e in report['errors']]
        self.assertEqual(frames[0]['source_file_id'], frames[1]['source_file_id'])
        source = report['source_files'][frames[0]['source_file_id']]
        self.assertNotIn('lines', source)
        self.assertEqual(source['path'], str(self.root / 'src/demo.c'))
        self.assertEqual(self.load(xml(error()), project=False)['source_files'], {})

    def test_auxiliary_instance_values_merge_and_preserve_first(self):
        def auxiliary(address, offset, size, relation='after', line=8, state="alloc'd"):
            return '<auxwhat>Address %s is %s bytes %s a block of size %s %s</auxwhat><stack>%s</stack>' % (address, offset, relation, size, state, frame(line=line))
        first = error(extra=auxiliary('0x1234', '0', '16'))
        second = error('2', extra=auxiliary('0x5678', '8', '1,024'))
        report = self.load(xml(first + second))
        self.assertEqual(len(report['errors']), 1)
        item = report['errors'][0]
        self.assertEqual(item['records'], 2)
        self.assertEqual(item['count'], 2)
        self.assertIn('0x1234', item['stacks'][1]['label'])
        self.assertEqual(item['id'], self.load(xml(first))['errors'][0]['id'])
        self.assertEqual(item['id'], self.load(xml(second))['errors'][0]['id'])
        for extra in (auxiliary('0x5678', '8', '16', relation='before'),
                      auxiliary('0x5678', '8', '16', line=9),
                      auxiliary('0x5678', '8', '16', state="free'd")):
            self.assertEqual(len(self.load(xml(first + error('2', extra=extra)))['errors']), 2)
        self.assertEqual(len(self.load(xml(first + error('2', extra=auxiliary('0x5678', '8', '16'), what='Invalid write of size 8')))['errors']), 2)

    def test_live_partial_and_completed_dedup_agree(self):
        from inc.xmlstream import XMLStream
        stream = XMLStream()
        def record(uid, size):
            return error(uid, extra='<auxwhat>Address 0x123 is 0 bytes after a block of size %s alloc\'d</auxwhat><stack>%s</stack>' % (size, frame(line=8)))
        stream.feed(('<valgrindoutput>' + record('1', 16)).encode())
        first = av.report_from_root(stream.root, self.path, xml_complete=False)['errors'][0]
        stream.feed(record('2', 32).encode())
        live = av.report_from_root(stream.root, self.path, xml_complete=False)
        self.assertEqual(len(live['errors']), 1)
        self.assertEqual(live['errors'][0]['id'], first['id'])
        stream.feed(b'<errorcounts><pair><unique>1</unique><count>7</count></pair><pair><unique>2</unique><count>3</count></pair></errorcounts><status><state>FINISHED</state></status></valgrindoutput>')
        stream.finish()
        final = av.report_from_root(stream.root, self.path)
        self.assertEqual(len(final['errors']), 1)
        self.assertEqual(final['errors'][0]['count'], 10)
        self.assertEqual(final['errors'][0]['id'], first['id'])

    def test_unknown_locations_not_overmerged(self):
        self.assertEqual(len(self.load(xml(error(frames=frame(file="", line=0))+error("2", frames=frame(ip="0x222", file="", line=0))))["errors"]), 2)

    def test_uninitialized_named_library_frames_merge_but_origins_remain_distinct(self):
        first = error(kind='UninitCondition', what='Conditional jump depends on uninitialised value(s)',
                      frames=frame(file='', line=0, fn='library') + frame())
        second = first.replace('0x1</unique>', '0x2</unique>').replace('0x111', '0x222')
        report = self.load(xml(first + second))
        self.assertEqual(len(report['errors']), 1)
        self.assertEqual(report['errors'][0]['records'], 2)
        self.assertEqual(len(self.load(xml(first + second.replace('<line>15', '<line>16')))['errors']), 2)

    def test_uninitialized_source_identity_ignores_module_copy_and_formatting(self):
        first = error(kind='UninitValue', what='Use of uninitialised value of size 8')
        second = first.replace('0x1</unique>', '0x2</unique>').replace('/app/demo', '/other/demo').replace('of size', 'of  size').replace('/old/project/src', '/old/project/./src').replace('<line>15', '<line>015')
        report = self.load(xml(first + second))
        self.assertEqual(len(report['errors']), 1)
        self.assertEqual(report['errors'][0]['records'], 2)

    def test_html_omits_repeated_source_excerpts_without_changing_report(self):
        report = self.load(xml(error() + error('2', frames=frame(line=30))))
        html = av.render_html(report)
        payload = json.loads(html.split('<script id="report-data" type="application/json">')[1].split('</script>')[0])
        source_ref = payload['errors'][0]['stacks'][0]['frames'][0]['source_ref']
        self.assertEqual(payload['source_snippets'][source_ref], report['errors'][0]['stacks'][0]['frames'][0]['source'])
        self.assertIn('source', report['errors'][0]['stacks'][0]['frames'][0])
        self.assertEqual(len(payload['source_files']), 1)

    def test_leak_aggregate_and_repeated_unique(self):
        first = error(kind="Leak_DefinitelyLost", extra="<xwhat><leakedbytes>64</leakedbytes><leakedblocks>1</leakedblocks></xwhat>")
        second = error("2", kind="Leak_DefinitelyLost", what="128 bytes in 2 blocks", extra="<xwhat><leakedbytes>128</leakedbytes><leakedblocks>2</leakedblocks></xwhat>")
        item = self.load(xml(first+first+second))["errors"][0]
        self.assertEqual((item["records"], item["leaked_bytes"], item["leaked_blocks"]), (2, 192, 3))

    def test_source_boundaries_and_missing(self):
        for line, expected in [(1, list(range(1, 12))), (40, list(range(30, 41)))]:
            item = self.load(xml(error(frames=frame(line=line))))["errors"][0]
            self.assertEqual([r["number"] for r in item["stacks"][0]["frames"][0]["source"]], expected)
        f = self.load(xml(error(frames=frame(line=100))))["errors"][0]["stacks"][0]["frames"][0]
        self.assertNotIn("source", f)

    def test_ambiguous_and_outside_sources(self):
        (self.root / "other").mkdir()
        (self.root / "other/demo.c").write_text("other")
        sources = av.Sources(self.root)
        self.assertIsNone(sources.resolve({"dir": "/unknown", "file": "demo.c"}))
        with tempfile.TemporaryDirectory() as outside:
            path = Path(outside) / "secret.c"
            path.write_text("private")
            self.assertIsNone(sources.resolve({"dir": outside, "file": "secret.c"}))

    def test_invalid_xml_and_dtd(self):
        for value in ["<valgrindoutput><error></bad>", "<notvalgrind/>", '<!DOCTYPE valgrindoutput [<!ENTITY x "x">]><valgrindoutput/>']:
            with self.assertRaises(ValueError):
                self.load(value)

    def test_empty_and_incomplete(self):
        self.assertEqual(self.load(xml())["errors"], [])
        self.assertFalse(self.load("<valgrindoutput/>")["finished"])

    def test_html_injection_and_static_mode(self):
        report = self.load(xml(error(what="&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;")))
        html = av.render_html(report)
        self.assertNotIn("</script><script>alert(1)", html)
        payload = html.split('<script id="report-data" type="application/json">')[1].split("</script>")[0]
        self.assertEqual(json.loads(payload)["errors"][0]["what"], "</script><script>alert(1)</script>")
        self.assertEqual(json.loads(payload)["token"], "")

    def test_debug_selection_and_command_safety(self):
        item = self.load(xml(error()))["errors"][0]
        f = av.pick_frame(item)
        commands = av.debug_commands(f, 1234, "/tmp/test pipe/vgdb", "/usr/bin/vgdb", self.root)
        self.assertIn('--pid=1234', '\n'.join(commands))
        self.assertIn('break -source "/old/project/src/demo.c" -line 15', commands)
        self.assertEqual(commands[-1], "continue")
        self.assertTrue(any(c.startswith("set substitute-path") for c in commands))
        for selected in ("-1:0", "0:100", "bad", "0:0:0"):
            with self.assertRaises(ValueError):
                av.pick_frame(item, selected)
        with self.assertRaises(ValueError):
            av.breakpoint_command({"fn": "foo\nshell touch /tmp/oops"})

    def test_cli_and_no_overwrite(self):
        self.load(xml(error()))
        output = self.root / "out.html"
        self.assertEqual(cli.main(["report", str(self.path), "-p", str(self.root), "-o", str(output)]), 0)
        self.assertTrue(output.exists())
        self.assertEqual(cli.main(["report", str(self.path), "-o", str(self.path)]), 2)
        self.assertEqual(cli.main(["debug", str(self.path), "--error", "missing", "--", "./demo"]), 2)

    def test_launcher_commands_input_and_cleanup(self):
        binary = self.root / "program with spaces"
        binary.write_text("test executable")
        input_file = self.root / "input.txt"
        input_file.write_text("input replay")
        item = self.load(xml(error()))["errors"][0]
        args = SimpleNamespace(frame=None, command=[str(binary), "argument with spaces"],
                               cwd=self.root, project_dir=self.root, stop_on_error=False,
                               stdin_file=input_file)
        vg = MagicMock(pid=12345)
        vg.poll.return_value = 0
        debugger = MagicMock(pid=12346)
        debugger.poll.return_value = 0
        debugger.wait.return_value = 0
        captured = []

        def spawn(command, **kwargs):
            captured.append(command)
            if command[0].endswith("valgrind"):
                self.assertEqual(kwargs["stdin"].read(), b"input replay")
                self.assertTrue(kwargs["start_new_session"])
                return vg
            script = Path(command[command.index("-x") + 1]).read_text(encoding="utf-8")
            self.assertIn("--pid=12345", script)
            self.assertIn('break -source "/old/project/src/demo.c" -line 15', script)
            return debugger

        with patch.object(av, "check_debug_environment"), patch.object(av.sys.stdin, "isatty", return_value=True), patch.object(av.shutil, "which", side_effect=lambda n: "/usr/bin/"+n), patch.object(av.subprocess, "Popen", side_effect=spawn), patch.object(av.os, "killpg", create=True, side_effect=lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()) if sig == 0 else None) as kill:
            self.assertEqual(av.run_debug(item, args), 0)
            kill.assert_any_call(12345, av.signal.SIGTERM)
        self.assertEqual(captured[0][-2:], [str(binary), "argument with spaces"])
        self.assertEqual(captured[1][-2:], [str(binary), "argument with spaces"])

    def test_gdb_launch_failure_cleans_up_target(self):
        binary = self.root / "demo"
        binary.write_text("test")
        item = self.load(xml(error()))["errors"][0]
        args = SimpleNamespace(frame=None, command=[str(binary)], cwd=self.root,
                               project_dir=self.root, stop_on_error=False, stdin_file=None)
        vg = MagicMock(pid=12345)
        vg.poll.return_value = 0
        with patch.object(av, "check_debug_environment"), patch.object(av.sys.stdin, "isatty", return_value=True), patch.object(av.shutil, "which", side_effect=lambda n: "/usr/bin/"+n), patch.object(av.subprocess, "Popen", side_effect=[vg, OSError("gdb failed")]), patch.object(av.os, "killpg", create=True, side_effect=lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()) if sig == 0 else None) as kill:
            with self.assertRaisesRegex(OSError, "gdb failed"):
                av.run_debug(item, args)
            kill.assert_any_call(12345, av.signal.SIGTERM)
            vg.poll.assert_called()


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.report = {"errors": [{"id": "test", "stacks": [{"frames": [{"file": "demo.c", "line": "9"}]}]}]}
        self.server = av.DebugServer(self.report, 0, True)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, data=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        conn.request(method, path, body=json.dumps(data) if data is not None else None, headers=headers or {})
        response = conn.getresponse()
        result = response.status, response.read()
        conn.close()
        return result

    def auth(self):
        return {"Origin": self.server.origin, "X-Debug-Token": self.server.token}

    def test_authorization_and_session_serialization(self):
        self.assertEqual(self.request("POST", "/api/debug", {"id": "test"})[0], 403)
        headers = self.auth()
        headers["Origin"] = "https://evil.example"
        self.assertEqual(self.request("POST", "/api/debug", {"id": "test"}, headers)[0], 403)
        self.assertEqual(self.request("POST", "/api/debug", {"id": "missing"}, self.auth())[0], 400)
        self.assertEqual(self.request("POST", "/api/debug", {"id": "test", "frame": "0:0"}, self.auth())[0], 202)
        self.assertEqual(self.server.pending.get_nowait()[1], "0:0")
        self.assertEqual(self.request("POST", "/api/debug", {"id": "test"}, self.auth())[0], 409)
        self.assertEqual(self.request("GET", "/api/status", headers=self.auth())[0], 200)

    def test_rebinding_and_paths(self):
        self.assertEqual(self.request("GET", "/", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("GET", "/../../etc/passwd")[0], 404)
        self.assertEqual(self.request("GET", "/")[0], 200)


@unittest.skipUnless(sys.platform == "linux" and all(shutil.which(n) for n in ("cc", "valgrind", "vgdb", "gdb")), "需要 Linux、cc、valgrind、vgdb 和 gdb")
class LinuxIntegrationTests(unittest.TestCase):
    def test_real_report_and_gdb_breakpoint(self):
        with tempfile.TemporaryDirectory(prefix="aivalgrind-test-") as folder:
            root = Path(folder)
            source = Path(__file__).resolve().parents[1] / "examples/demo.c"
            binary, xml_path = root / "demo", root / "errors.xml"
            subprocess.run(["cc", "-g", "-O0", str(source), "-o", str(binary)], check=True, capture_output=True)
            subprocess.run(["valgrind", "--xml=yes", "--xml-file="+str(xml_path), "--leak-check=full", str(binary)], check=True, capture_output=True, timeout=30)
            report = av.load_report(xml_path, source.parent)
            error = next(e for e in report["errors"] if e["kind"] == "InvalidWrite")
            self.assertEqual(error["count"], 3)
            prefix = str(root / "vgdb")
            vg = subprocess.Popen(["valgrind", "--vgdb=yes", "--vgdb-error=0", "--vgdb-prefix="+prefix, str(binary)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                commands = av.debug_commands(av.pick_frame(error), vg.pid, prefix, shutil.which("vgdb"), source.parent)
                captures, collector = av.prepare_capture(root / "captures", error["id"])
                commands += ['printf "AIVALGRIND_BREAKPOINT_REACHED\\n"', 'bt',
                             "python exec(compile(open(" + repr(str(collector)) + ", encoding='utf-8').read(), 'capture', 'exec'))",
                             'delete breakpoints', 'monitor v.set vgdb-error 1', 'continue', 'continue', 'continue',
                             'monitor v.info last_error', 'quit']
                script = root / "test.gdb"
                script.write_text("\n".join(commands)+"\n")
                result = subprocess.run(["gdb", "-q", "-nx", "-batch", "-x", str(script), str(binary)], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                self.assertIn("AIVALGRIND_BREAKPOINT_REACHED", result.stdout)
                self.assertIn("invalid_write", result.stdout)
                self.assertIn("Invalid write of size 4", result.stdout+result.stderr)
                snapshots = list(captures.glob('error-*.json'))
                self.assertEqual(len(snapshots), 1, result.stdout+result.stderr)
                snapshot = json.loads(snapshots[0].read_text(encoding='utf-8'))
                self.assertEqual(snapshot['error_count'], 1)
                self.assertTrue(any(v['name'] == 'p' and v['status'] == 'available'
                                    for f in snapshot['frames'] for v in f.get('variables', [])))
            finally:
                vg.terminate()
                try:
                    vg.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    vg.kill()
                    vg.wait()


if __name__ == "__main__":
    unittest.main()
