from pathlib import Path
import tempfile
import unittest
from inc.output import prepare_output, COLLECTION_FILES, register_capture, write_manifest


class OutputTests(unittest.TestCase):
    def test_reuse_preserves_unrelated_files_and_capture_notes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'notes.txt').write_text('keep')
            prepare_output(root, COLLECTION_FILES)
            (root / 'errors.xml').write_text('old')
            session = root / 'captures/session-test'
            session.mkdir(parents=True)
            register_capture(session)
            write_manifest(session, ['capture.py', 'error-0001.json'])
            (session / 'capture.py').write_text('old')
            (session / 'error-0001.json').write_text('old')
            (session / 'notes.txt').write_text('also keep')
            prepare_output(root, COLLECTION_FILES)
            self.assertFalse((root / 'errors.xml').exists())
            self.assertFalse((session / 'error-0001.json').exists())
            self.assertEqual((root / 'notes.txt').read_text(), 'keep')
            self.assertEqual((session / 'notes.txt').read_text(), 'also keep')

    def test_unknown_collision_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'report.html'
            path.write_text('user document')
            with self.assertRaises(ValueError):
                prepare_output(root, COLLECTION_FILES)
            self.assertEqual(path.read_text(), 'user document')

    def test_preserve_input_xml_and_metadata_when_analyzing_same_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prepare_output(root, COLLECTION_FILES)
            xml = root / 'errors.xml'
            xml.write_text('input')
            prepare_output(root, ['report.html', 'report.html.tmp'], [xml])
            self.assertEqual(xml.read_text(), 'input')

    def test_bad_manifest_aborts_before_any_deletion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'errors.xml').write_text('keep')
            write_manifest(root, ['errors.xml', '../outside'])
            with self.assertRaises(ValueError):
                prepare_output(root, COLLECTION_FILES)
            self.assertTrue((root / 'errors.xml').exists())
