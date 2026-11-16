import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tools import fetch_assets as fetch


class FetchAssetsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.destination = self.root / 'destination'
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(contextlib.redirect_stderr(self.output))

    def item(self, name='image with spaces.png', data=b'complete image'):
        (self.source / name).write_bytes(data)
        return {'path': name, 'sha': fetch.blob_id(data)}

    def test_download_verify_and_resume_without_network(self):
        item = self.item()
        result = fetch.download_all([item], self.source.as_uri(), self.destination)
        self.assertEqual(0, result)
        self.assertEqual(b'complete image', (self.destination / item['path']).read_bytes())
        with patch.object(fetch, 'start_download', side_effect=AssertionError('Must skip verified file')):
            self.assertEqual(0, fetch.download_all([item], 'https://invalid.test', self.destination))
        self.assertIn('Already verified 1/1', self.output.getvalue())
        self.assertFalse(list(self.destination.glob('.natro-download-*')))

    def test_bad_checksum_is_never_published(self):
        item = self.item()
        (self.source / item['path']).write_bytes(b'corrupt')
        self.assertEqual(1, fetch.download_all([item], self.source.as_uri(), self.destination))
        self.assertFalse((self.destination / item['path']).exists())
        self.assertIn('Git blob checksum mismatch', self.output.getvalue())
        self.assertFalse(list(self.destination.glob('.natro-download-*')))

    def stalled_worker(self, item, base_url, destination):
        destination.mkdir(exist_ok=True)
        temporary = destination / '.natro-download-stalled'
        temporary.write_bytes(b'partial')
        process = subprocess.Popen(
            [sys.executable, '-c', 'import time\ntime.sleep(60)'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        self.process = process
        return fetch.Download(process, item, temporary, destination / item['path'], time.monotonic())

    def test_hard_deadline_kills_stalled_worker(self):
        item = self.item()
        started = time.monotonic()
        with patch.object(fetch, 'start_download', side_effect=self.stalled_worker):
            result = fetch.download_all([item], 'unused', self.destination, request_timeout=0.15)
        self.assertEqual(1, result)
        self.assertLess(time.monotonic() - started, 3)
        self.assertIsNotNone(self.process.poll())
        self.assertFalse(list(self.destination.iterdir()))
        self.assertIn('including DNS', self.output.getvalue())

    def test_interrupt_kills_worker_and_removes_partial(self):
        item = self.item()
        with patch.object(fetch, 'start_download', side_effect=self.stalled_worker), \
             patch.object(fetch.time, 'sleep', side_effect=KeyboardInterrupt):
            result = fetch.download_all([item], 'unused', self.destination)
        self.assertEqual(130, result)
        self.assertIsNotNone(self.process.poll())
        self.assertFalse(list(self.destination.iterdir()))

    def test_total_deadline_kills_active_worker(self):
        item = self.item()
        with patch.object(fetch, 'start_download', side_effect=self.stalled_worker):
            result = fetch.download_all([item], 'unused', self.destination,
                                        request_timeout=60, total_timeout=0.15)
        self.assertEqual(1, result)
        self.assertIsNotNone(self.process.poll())
        self.assertIn('Total download deadline', self.output.getvalue())
        self.assertFalse(list(self.destination.iterdir()))

    def test_modified_existing_file_is_preserved(self):
        item = self.item()
        self.destination.mkdir()
        target = self.destination / item['path']
        target.write_bytes(b'user modification')
        self.assertEqual(1, fetch.download_all([item], 'unused', self.destination))
        self.assertEqual(b'user modification', target.read_bytes())


if __name__ == '__main__':
    unittest.main()
