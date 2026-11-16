from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import numpy as np
from PySide6.QtGui import QImage
from natro_linux.config import Config
from natro_linux.engine import Engine, DetectionError
from natro_linux.motion import Cancelled
from natro_linux.scheduler import State
from natro_linux.vision import rgba, Vision


class ClosedMenuRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.vision = Vision()
        self.frame = np.full((400, 900, 4), [45, 145, 15, 255], np.uint8)
        self.offset = 22
        for x in (150, 450):
            self.frame[22:58, x:x+294, :3] = [213, 223, 207]
            self.frame[25:53, x+60:x+288, :3] = 110
        self.word(self.offset+14)

    def word(self, y):
        template = self.vision.templates.get('offset/toppollen')
        ink = (template[:, :, 3] > 0) & (template[:, :, :3].max(2) < 100)
        area = self.frame[y:y+template.shape[0], 458:458+template.shape[1]]
        # Same glyph shape, different shading requires the Sober fallback.
        area[ink, :3] = 70

    def assert_hud(self):
        offset = self.vision.hud_offset(self.frame)
        self.assertIsNotNone(offset)
        self.assertLessEqual(abs(offset-self.offset), 1)

    def test_hud_without_any_selected_menu(self):
        self.assert_hud()

    def test_changing_menu_region_does_not_change_recognition(self):
        self.frame[90:180, :350, :3] = 0
        self.assert_hud()
        self.frame[90:180, :350, :3] = 255
        self.assert_hud()

    def test_pollen_label_alone_is_not_enough(self):
        self.frame[22:58, 150:444, :3] = [45, 145, 15]
        self.assertIsNone(self.vision.hud_offset(self.frame))

    def test_word_elsewhere_does_not_hide_valid_hud(self):
        self.frame[0:22, 450:510, :3] = 230
        self.word(1)
        self.assert_hud()

    def test_bright_panel_without_counter_is_rejected(self):
        self.frame[25:53, 210:438, :3] = 230
        self.assertIsNone(self.vision.hud_offset(self.frame))


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        state = State(Path(self.directory.name)/'state.json')
        self.engine = Engine(Config(), state=state)
        self.engine.control = Mock()
        self.engine.control.stopped.wait.return_value = False
        self.frame = np.full((400, 640, 4), 255, np.uint8)
        self.engine.capture = Mock()
        self.engine.capture.frame.return_value = self.frame

    def test_transient_frame_is_retried_before_success(self):
        self.engine.vision.hud_offset = Mock(side_effect=[None, 22])
        frame, offset = self.engine.preflight_frame()
        self.assertIs(self.frame, frame)
        self.assertEqual(22, offset)
        self.assertEqual(2, self.engine.capture.frame.call_count)
        self.assertFalse((Path(self.directory.name)/'unrecognized-bss.png').exists())

    def test_rejected_frame_is_saved_and_input_never_created(self):
        self.engine.niri = Mock()
        self.engine.niri.select.return_value = {'id': 1}
        self.engine.niri.output.return_value = ('DP-1', {})
        with patch('natro_linux.engine.Capture', return_value=self.engine.capture), \
             patch('natro_linux.engine.NativeInput') as backend:
            with self.assertRaisesRegex(DetectionError, 'unrecognized-bss.png'):
                self.engine.connect()
        backend.assert_not_called()
        image = QImage(str(Path(self.directory.name)/'unrecognized-bss.png'))
        self.assertFalse(image.isNull())
        np.testing.assert_array_equal(self.frame, rgba(image))
        self.assertEqual(3, self.engine.capture.frame.call_count)

    def test_stop_interrupts_retry(self):
        self.engine.control.stopped.wait.return_value = True
        with self.assertRaises(Cancelled):
            self.engine.preflight_frame()
        self.assertEqual(1, self.engine.capture.frame.call_count)
        self.assertFalse((Path(self.directory.name)/'unrecognized-bss.png').exists())

    def test_runtime_failure_releases_input_and_saves_last_frame(self):
        import json
        import time
        self.engine.cached_frame = self.frame
        self.engine.frame_time = time.monotonic()-.5
        self.engine.input = Mock()
        self.engine.emit = Mock()
        save = self.engine.save_diagnostic_frame
        def after_release(*args):
            self.engine.input.release_all.assert_called_once()
            return save(*args)
        with patch.object(self.engine, 'save_diagnostic_frame', side_effect=after_release):
            self.engine.report_failure(DetectionError('Hive not confirmed'))
        metadata = json.loads((Path(self.directory.name)/'last-error.json').read_text())
        self.assertEqual('Hive not confirmed', metadata['error'])
        self.assertGreaterEqual(metadata['frame_age_seconds'], .5)
        np.testing.assert_array_equal(self.frame, rgba(QImage(metadata['frame'])))
        self.engine.capture.frame.assert_not_called()
        self.assertIn('last-error.png', self.engine.emit.call_args.args[0]['detail'])

    def test_failure_without_frame_does_not_reuse_old_diagnostic(self):
        import json
        self.engine.emit = Mock()
        self.engine.report_failure(DetectionError('Capture unavailable'))
        metadata = json.loads((Path(self.directory.name)/'last-error.json').read_text())
        self.assertIsNone(metadata['frame'])
        self.assertIsNone(metadata['frame_age_seconds'])
        self.assertEqual('Capture unavailable', self.engine.emit.call_args.args[0]['detail'])


if __name__ == '__main__':
    unittest.main()
