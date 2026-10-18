"""The guard must inspect staged bytes, including files hidden by .gitignore."""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('publication', Path(__file__).parents[1] / 'check_publication.py')
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class PublicationTests(unittest.TestCase):
    def test_only_reviewed_screenshot_bytes_are_allowed(self):
        for name in ('docs/images/site.png', 'docs/images/annotator.png'):
            content = (guard.ROOT / name).read_bytes()
            self.assertEqual(list(guard.issues(name, '100644', content)), [])
            self.assertTrue(list(guard.issues(name, '100644', content + b'private metadata')))
            self.assertTrue(list(guard.issues('docs/images/other.png', '100644', content)))
            self.assertTrue(list(guard.issues(name, '120000', content)))

    def test_staged_secret_cannot_be_hidden_by_clean_working_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            file = root / 'config.txt'
            file.write_text('ghp_' + 'A' * 36)
            subprocess.run(['git', '-C', str(root), 'add', 'config.txt'], check=True)
            file.write_text('ordinary source text')
            previous = guard.ROOT
            try:
                guard.ROOT = root
                staged = list(guard.files(True))
                working = list(guard.files(False))
                self.assertIn('provider credential', list(guard.issues(*staged[0])))
                self.assertEqual(list(guard.issues(*working[0])), [])
            finally:
                guard.ROOT = previous

    def test_forced_private_path_and_symlink_are_rejected(self):
        self.assertIn('private or generated directory', list(guard.issues('data/notes.txt', '100644', b'notes')))
        self.assertIn('local credentials/configuration', list(guard.issues('.env', '100644', b'')))
        self.assertIn('symlink or submodule', list(guard.issues('reference.txt', '120000', b'target')))

    def test_binary_disguised_as_text_and_embedded_media_are_rejected(self):
        self.assertIn('binary content', list(guard.issues('notes.txt', '100644', b'a\x00b')))
        content = ('data:image/png;' + 'base64,' + 'A' * 150).encode()
        self.assertIn('embedded image or video', list(guard.issues('notes.txt', '100644', content)))
