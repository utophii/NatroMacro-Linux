from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import numpy as np
from natro_linux.config import Config
from natro_linux.engine import Engine
from natro_linux.scheduler import State
from natro_linux.vision import Vision


class HivePromptTests(unittest.TestCase):
    def setUp(self):
        self.vision = Vision()
        self.frame = np.full((400, 900, 4), [40, 130, 20, 255], np.uint8)
        self.frame[58:138, 250:650, :3] = [55, 110, 180]
        for key, x, y in [('general/colhey', 390, 65), ('convert/collectpollen', 388, 94)]:
            template = self.vision.templates.get(key)
            h, w = template.shape[:2]
            area = self.frame[y:y+h, x:x+w]
            # Upstream stores only sparse samples of the white strokes.
            ink = template[:, :, 3] > 0
            padded = np.pad(ink, 1)
            strokes = np.logical_or.reduce([padded[dy:dy+h, dx:dx+w]
                                             for dy, dx in ((1, 1), (1, 0), (0, 1))])
            area[strokes, :3] = 230

    def test_two_line_empty_hive_instruction(self):
        self.assertTrue(self.vision.empty_hive(self.frame, 22))

    def test_single_line_does_not_confirm_hive(self):
        self.frame[94:120, 250:650, :3] = [55, 110, 180]
        self.assertFalse(self.vision.empty_hive(self.frame, 22))

    def test_white_block_does_not_confirm_hive(self):
        self.frame[58:138, 250:650, :3] = 255
        self.assertFalse(self.vision.empty_hive(self.frame, 22))

    def engine(self, directory):
        e = Engine(Config(ui_offset=22), state=State(Path(directory)/'state.json'))
        e.frame = Mock(return_value=self.frame)
        e.input = Mock()
        e.motion = Mock()
        e.motion.vars = {'a_keydelay': 50}
        e.has = Mock(return_value=False)
        e.dismiss_dialogs = Mock()
        e.check_disconnected = Mock()
        e.wait_for = Mock(return_value=True)
        return e

    def test_empty_backpack_needs_no_e_button_or_conversion_key(self):
        with tempfile.TemporaryDirectory() as directory:
            e = self.engine(directory)
            e.convert()
            e.motion.press.assert_not_called()
            e.motion.wait.assert_not_called()

    def test_reset_accepts_empty_prompt_after_camera_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            e = self.engine(directory)
            e.vision.hive = Mock(return_value=True)
            e.reset()
            self.assertTrue(e.at_hive)
            self.assertEqual(1, e.state.data['stats']['resets'])
            e.vision.hive.assert_called_once()
            self.assertEqual(1, e.motion.send.call_count)


if __name__ == '__main__':
    unittest.main()
