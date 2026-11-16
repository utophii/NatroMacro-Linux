from dataclasses import asdict
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import numpy as np

from natro_linux.config import Config, PlanterSettings
from natro_linux.engine import DetectionError
from natro_linux.game_data import game_data
from natro_linux.motion import Cancelled
from natro_linux.planters import Choice, PlanterBook, Planters, choose, interval_seconds, nectar_percent
from natro_linux.quests import bar_state, visible_quests
from natro_linux.scheduler import State
from natro_linux.vision import Vision, find_text
from tools.extract_game_data import extract, SOURCE


class GameDataTests(unittest.TestCase):
    def test_tables_reproduce_pinned_source(self):
        data = extract(SOURCE.read_text(encoding='utf-8-sig'))
        for key, value in data.items():
            self.assertEqual(value, game_data()[key])
        self.assertEqual(17, len(data['rankings']))
        self.assertEqual(72, sum(len(q) for q in data['quests'].values()))
        templates = Vision().templates.index
        for quests in data['quests'].values():
            for name in quests:
                self.assertIn('legacy/'+name.lower(), templates)

    def test_nectar_height_and_full_bar_are_bounded(self):
        frame = np.zeros((180, 900, 4), dtype=np.uint8)
        frame[40:59, 10, :3] = [0x7e, 0x9e, 0xb3]
        self.assertEqual(50, nectar_percent(frame, 'Comforting'))
        frame[40:90, 10, :3] = [0x7e, 0x9e, 0xb3]
        self.assertEqual(99.99, nectar_percent(frame, 'Comforting'))
        self.assertEqual(0, nectar_percent(frame, 'Refreshing'))

    def test_unknown_or_clipped_quest_bar_is_not_complete(self):
        frame = np.zeros((300, 320, 4), dtype=np.uint8)
        self.assertEqual('unknown', bar_state(frame, 100, 1, 300))
        frame[115, 26, :3] = [108, 219, 97]
        self.assertEqual('complete', bar_state(frame, 100, 1, 300))
        self.assertEqual('unknown', bar_state(frame, 100, 1, 110))
        frame[115, 26, :3] = [110, 255, 96]
        self.assertEqual('incomplete', bar_state(frame, 100, 1, 300))

    def test_blank_frames_have_no_quests_or_hud(self):
        vision = Vision()
        for fill in (0, 255):
            frame = np.full((400, 640, 4), fill, dtype=np.uint8)
            self.assertIsNone(vision.hud_offset(frame))
            self.assertEqual([], visible_quests(frame, vision))
            self.assertIsNone(find_text(frame[:100, :60], vision.templates.get('offset/toppollen')))

    def test_text_matching_tolerates_font_shading(self):
        template = Vision().templates.get('offset/toppollen')
        h, w = template.shape[:2]
        frame = np.full((h+10, w+10, 4), 255, dtype=np.uint8)
        glyphs = (template[:, :, 3] > 0) & (template[:, :, :3].max(axis=2) < 100)
        area = frame[5:5+h, 5:5+w]
        area[glyphs, :3] = 70
        match = find_text(frame, template, .96)
        self.assertIsNotNone(match)
        self.assertLessEqual(abs(match[0].x-5), 1)
        self.assertLessEqual(abs(match[0].y-5), 1)

    def test_visible_quest_title_and_objective_order(self):
        vision = Vision()
        frame = np.full((400, 640, 4), 255, np.uint8)
        frame[180:210, :306, :3] = [229, 240, 247]
        for key, x in [('legacy/polar_bear', 10), ('legacy/mantis meatballs', 120)]:
            template = vision.templates.get(key)
            h, w = template.shape[:2]
            ink = (template[:, :, 3] > 0) & (template[:, :, :3].max(axis=2) < 100)
            area = frame[184:184+h, x:x+w]
            area[ink, :3] = template[ink, :3]
        frame[225, 26, :3] = [108, 219, 97]
        frame[275, 26, :3] = [244, 108, 85]
        quests = visible_quests(frame, vision)
        self.assertEqual(1, len(quests))
        self.assertEqual('Mantis Meatballs', quests[0].title)
        self.assertEqual([('Kill', 'incomplete'), ('Collect', 'complete')],
                         [(o.action, o.state) for o in quests[0].objectives])
        self.assertFalse(quests[0].complete)

    def test_malformed_png_metadata_removed_without_touching_pixels(self):
        from tools.extract_assets import clean_png, ROOT
        source = (ROOT/'upstream/NatroMacro/nm_image_assets/BLove.PNG').read_bytes()
        output = clean_png(source)
        self.assertNotEqual(source, output)
        def chunks(data):
            import struct
            result = []
            offset = 8
            while offset < len(data):
                size = struct.unpack('>I', data[offset:offset+4])[0]
                if data[offset+4:offset+8] != b'sBIT':
                    result.append(data[offset:offset+size+12])
                offset += size+12
            return result
        self.assertEqual(chunks(source), chunks(output))
        self.assertEqual(output, clean_png(output))


class PlanterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 100000.
        self.state = State(Path(self.directory.name)/'state.json', lambda: self.now)
        self.book = PlanterBook(self.state)

    def test_upstream_intervals(self):
        row = ['PlasticPlanter', 1, 1, 2]
        self.assertEqual(3600, interval_seconds(row, projected=0, minimum=70))
        self.assertEqual(7200, interval_seconds(row, mode='full'))
        self.assertEqual(1800, interval_seconds(row, mode='interval', hours=.5))
        self.assertEqual(7200, interval_seconds(row, mode='interval', hours=20))
        row = ['HydroponicPlanter', 1.4, 1.375, 8.73]
        self.assertEqual(31428, interval_seconds(row, projected=0, minimum=70))
        self.assertGreater(interval_seconds(row, projected=100, minimum=70), 0)

    def test_auto_priority_rotation_and_no_duplicate_planters(self):
        settings = PlanterSettings(mode='auto', allowed_planters=['PlasticPlanter', 'CandyPlanter'],
                                   priorities=[{'nectar': 'Comforting', 'minimum': 70}])
        settings.validate()
        first = choose(settings, self.book, {})
        self.assertEqual('dandelion', first.field)
        self.assertEqual('PlasticPlanter', first.planter)
        self.book.reserve(first)
        self.book.growing(first.slot)
        second = choose(settings, self.book, {})
        self.assertEqual('bamboo', second.field)
        self.assertEqual('CandyPlanter', second.planter)
        self.book.reserve(second)
        self.book.growing(second.slot)
        self.assertIsNone(choose(settings, self.book, {}))
        self.book.remove(first.slot, harvested=True)
        next_choice = choose(settings, self.book, {})
        self.assertEqual('dandelion', next_choice.field)
        self.assertEqual(1, self.state.data['stats']['planters_harvested'])

    def test_manual_slots_and_bounded_duration(self):
        settings = PlanterSettings(mode='manual', manual=[{'field': 'rose', 'planter': 'PlasticPlanter', 'hours': 40}])
        choice = choose(settings, self.book, {})
        self.assertEqual(7200, choice.seconds)
        self.book.reserve(choice)
        self.assertIsNone(choose(settings, self.book, {}))
        with self.assertRaises(ValueError):
            self.book.reserve(choice)

    def test_interrupted_action_is_not_success_after_restart(self):
        choice = Choice('1', 'rose', 'PlasticPlanter', 'Motivating', 3600, 4.2)
        self.book.reserve(choice)
        restarted = PlanterBook(State(self.state.path, lambda: self.now))
        self.assertEqual('uncertain', restarted.records['1']['phase'])
        self.assertEqual({}, restarted.state.data['stats'])
        self.assertEqual(0, restarted.records['1']['due'])

    def test_growing_time_survives_restart(self):
        choice = Choice('1', 'rose', 'PlasticPlanter', 'Motivating', 3600, 4.2)
        self.book.reserve(choice)
        self.book.growing('1')
        self.now += 100
        restarted = PlanterBook(State(self.state.path, lambda: self.now))
        self.assertEqual(103600, restarted.records['1']['due'])
        self.assertEqual('growing', restarted.records['1']['phase'])

    def test_manual_reconciliation_does_not_fake_harvest_statistics(self):
        choice = Choice('1', 'rose', 'PlasticPlanter', 'Motivating', 3600, 4.2)
        self.book.reserve(choice)
        self.book.phase('1', 'uncertain')
        self.book.reconcile('1', .5)
        self.assertEqual('growing', self.book.records['1']['phase'])
        self.assertEqual(101800, self.book.records['1']['due'])
        self.book.reconcile('1')
        self.assertEqual({}, self.book.records)
        self.assertNotIn('planters_harvested', self.state.data['stats'])

    def test_state_correction_uses_same_instance_lock(self):
        import os
        from unittest.mock import patch
        from natro_linux.control import acquire_instance_lock
        with patch.dict(os.environ, {'XDG_RUNTIME_DIR': self.directory.name}):
            with acquire_instance_lock():
                with self.assertRaises(RuntimeError):
                    acquire_instance_lock()
            with acquire_instance_lock():
                pass

    def test_planter_settings_roundtrip_and_validation(self):
        config = Config(planters=PlanterSettings(mode='manual', manual=[{'field': 'rose', 'planter': 'PlasticPlanter', 'hours': 1}]))
        path = Path(self.directory.name)/'config.json'
        config.save(path)
        self.assertEqual(asdict(config), asdict(Config.load(path)))
        config.planters.manual.append(dict(config.planters.manual[0]))
        with self.assertRaises(ValueError):
            config.validate()
        with self.assertRaises(ValueError):
            PlanterSettings(mode='auto').validate()

    def controller(self):
        e = SimpleNamespace(
            state=self.state, config=Config(), status=Mock(), motion=SimpleNamespace(wait=Mock()),
            frame=lambda **kw: np.zeros((700, 1000, 4), np.uint8), drag=Mock(),
            wait_for=lambda *args: True, check_disconnected=Mock(), input=SimpleNamespace(release_all=Mock()),
        )
        controller = Planters(e)
        controller.travel = Mock()
        controller.inventory.find = Mock(return_value=(30, 200))
        controller.inventory.open = Mock()
        controller.confirm = Mock(return_value=True)
        controller.errors = Mock(return_value=None)
        return controller

    def test_cancel_during_drag_preserves_uncertain_slot(self):
        controller = self.controller()
        controller.e.drag.side_effect = Cancelled
        choice = Choice('1', 'rose', 'PlasticPlanter', 'Motivating', 3600, 4.2)
        with self.assertRaises(Cancelled):
            controller.place(choice)
        self.assertEqual('uncertain', controller.book.records['1']['phase'])
        self.assertNotIn('planters_placed', self.state.data['stats'])
        controller.e.input.release_all.assert_called()

    def test_rejected_placement_does_not_start_timer(self):
        controller = self.controller()
        controller.errors.return_value = 'Already three planters'
        choice = Choice('1', 'rose', 'PlasticPlanter', 'Motivating', 3600, 4.2)
        with self.assertRaises(DetectionError):
            controller.place(choice)
        self.assertEqual({}, controller.book.records)
        self.assertNotIn('planters_placed', self.state.data['stats'])

    def test_confirmed_placement_records_timer(self):
        controller = self.controller()
        choice = Choice('1', 'rose', 'PlasticPlanter', 'Motivating', 3600, 4.2)
        controller.place(choice)
        self.assertEqual('growing', controller.book.records['1']['phase'])
        self.assertEqual(103600, controller.book.records['1']['due'])
        self.assertEqual(1, self.state.data['stats']['planters_placed'])


if __name__ == '__main__':
    unittest.main()
