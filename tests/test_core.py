import dataclasses
import json
import math
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from natro_linux.config import Config, Field, DATA
from natro_linux.motion import Motion, Control, Cancelled, RestartCycle, Buffs
from natro_linux.platform import TraceInput, FocusLost, Niri, PlatformError
from natro_linux.scheduler import State
from natro_linux.vision import find_all, Vision, Templates
from tools.port_routes import Compiler, PortError


class MovementTests(unittest.TestCase):
    def test_every_route_branch_releases_keys(self):
        entries = json.loads((DATA/'movements.json').read_text())
        self.assertEqual(91, sum(e['kind'] == 'routes' for e in entries))
        self.assertEqual(12, sum(e['kind'] == 'patterns' for e in entries))
        for entry in entries:
            for method in ('Walk', 'Cannon'):
                for hive in range(1, 7):
                    for bees in (20, 35):
                        with self.subTest(name=entry['name'], method=method, hive=hive, bees=bees):
                            backend = TraceInput()
                            motion = Motion(backend, Config(move_method=method, hive_slot=hive, hive_bees=bees))
                            motion.run(entry['name'], entry['kind'])
                            self.assertEqual(set(), backend.held)
                            self.assertTrue(math.isfinite(backend.time))
                            self.assertGreater(backend.time, 0)

    def test_cancellation_releases_keys(self):
        backend = TraceInput()
        motion = Motion(backend)
        def cancelled(_):
            raise Cancelled()
        motion.walk = cancelled
        with self.assertRaises(Cancelled):
            motion.run('gtf-dandelion')
        self.assertFalse(backend.held)

    def test_focus_loss_before_press(self):
        backend = TraceInput()
        def check():
            raise FocusLost('lost')
        motion = Motion(backend, focus_check=check)
        with self.assertRaises(FocusLost):
            motion.press('w')
        self.assertEqual([], backend.events)

    def test_stop_during_wait_is_prompt(self):
        class Backend:
            held = set()
            def heartbeat(self):
                pass
        control = Control()
        motion = Motion(Backend(), control=control)
        timer = threading.Timer(.05, control.stop)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(Cancelled):
                motion.wait(30)
        finally:
            timer.join()
        self.assertLess(time.monotonic()-started, .5)

    def test_pause_releases_then_restarts(self):
        backend = TraceInput()
        backend.key('w', True)
        control = Control()
        control.pause()
        motion = Motion(backend, control=control)
        timer = threading.Timer(.03, control.resume)
        timer.start()
        try:
            with self.assertRaises(RestartCycle):
                motion.checkpoint()
        finally:
            timer.join()
        self.assertFalse(backend.held)

    def test_original_speed_formula_and_cap(self):
        c = Config(base_speed=28, hasty_guard=True, gifted_hasty=True)
        buffs = Buffs(haste=10, coconut_haste=True, haste_plus=True, oil=True, smoothie=True, bear=True)
        self.assertAlmostEqual(buffs.speed(c), 42*1.1*1.15*2*2*1.2*1.25)
        self.assertAlmostEqual(buffs.speed(c, 10), 42*1.1*1.15*2*1.2*1.25)

    def test_walk_distance_in_studs(self):
        backend = TraceInput()
        Motion(backend, Config(base_speed=20)).move(10, 'w')
        self.assertAlmostEqual(backend.time, (40-.125)/20)

    def test_jump_waits_minimum_1400ms(self):
        backend = TraceInput()
        Motion(backend, Config(base_speed=20)).jump([1, 'w'])
        self.assertAlmostEqual(backend.time, 1.4)
        self.assertFalse(backend.held)

    def test_pattern_preserves_tool_and_inverts_direction(self):
        backend = TraceInput()
        backend.key('lbutton', True)
        motion = Motion(backend)
        field = Field(pattern='Slimline', invert_lr=True)
        motion.run('Slimline', 'patterns', field)
        self.assertEqual(backend.held, {'lbutton'})
        self.assertEqual(backend.events[1][1], 'd')

    def test_unknown_statement_is_rejected(self):
        with self.assertRaises(PortError):
            Compiler('Run("rm -rf /")').compile()
        with self.assertRaises(PortError):
            Compiler('Walk(Unknown(1))').compile()

    def test_case_insensitive_upstream_comparisons(self):
        self.assertTrue(Motion.eq('Cannon', 'cannon'))
        self.assertFalse(Motion.eq('Walk', 'cannon'))


class VisionTests(unittest.TestCase):
    def test_edges_alpha_and_tolerance(self):
        rng = np.random.default_rng(42)
        frame = rng.integers(0, 255, (20, 30, 4), dtype=np.uint8)
        template = rng.integers(0, 255, (3, 4, 4), dtype=np.uint8)
        template[:, :, 3] = 255
        template[0, 0, 3] = 0
        frame[-3:, -4:] = template
        frame[-3, -4, :3] = 0
        match = find_all(frame, template)[0]
        self.assertEqual((match.x, match.y), (26, 17))
        frame[-1, -1, 0] = int(frame[-1, -1, 0]) + 1
        self.assertEqual([], find_all(frame, template))
        self.assertTrue(find_all(frame, template, 1))

    def test_matcher_against_brute_force(self):
        rng = np.random.default_rng(5)
        for _ in range(20):
            frame = rng.integers(0, 6, (8, 9, 4), dtype=np.uint8)
            template = rng.integers(0, 6, (2, 3, 4), dtype=np.uint8)
            template[:, :, 3] = 255
            template[0, 0, 3] = 0
            frame[6:8, 6:9] = template
            mask = template[:, :, 3] != 0
            expected = []
            for y in range(7):
                for x in range(7):
                    if np.all(np.abs(frame[y:y+2, x:x+3, :3].astype(int)[mask] - template[:, :, :3].astype(int)[mask]) <= 1):
                        expected.append((x, y))
            self.assertEqual(expected, [(m.x,m.y) for m in find_all(frame, template, 1, 1000)])

    def test_solid_counts_all_positions(self):
        frame = np.full((6, 8, 4), [30, 40, 50, 255], dtype=np.uint8)
        template = frame[:2, :3].copy()
        self.assertEqual(30, len(find_all(frame, template, 0, 100)))
        self.assertEqual([], find_all(frame, np.zeros((2,2,4), dtype=np.uint8)))
        self.assertEqual([], find_all(template, frame))

    def test_all_assets_decode(self):
        templates = Templates()
        for name, entry in templates.index.items():
            with self.subTest(name=name):
                self.assertEqual((entry['height'], entry['width'], 4), templates.get(name).shape)

    def test_absent_buffs(self):
        self.assertEqual(Buffs(), Vision().buffs(np.zeros((100, 600, 4), dtype=np.uint8)))

    def test_backpack_region(self):
        frame = np.zeros((20, 100, 4), dtype=np.uint8)
        frame[3:7, 10:30, :3] = [20, 200, 40]
        self.assertEqual(50, Vision.backpack(frame, [10, 3, 40, 4]))
        self.assertIsNone(Vision.backpack(frame, None))
        with self.assertRaises(ValueError):
            Vision.backpack(frame, [99, 19, 3, 3])


class PersistenceTests(unittest.TestCase):
    def test_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'config.json'
            c = Config(fields=[Field(name='rose', pattern='Squares', rotate=-2)])
            c.save(path)
            self.assertEqual(c, Config.load(path))
            c.base_speed = float('nan')
            with self.assertRaises(ValueError):
                c.save(path)
            self.assertTrue(math.isfinite(Config.load(path).base_speed))

    def test_failed_collection_does_not_count_as_success(self):
        with tempfile.TemporaryDirectory() as d:
            now = [100000.]
            state = State(Path(d)/'state.json', lambda: now[0])
            self.assertTrue(state.due('clock', 3600))
            state.failed('clock')
            self.assertNotIn('clock', state.data['completed'])
            self.assertFalse(state.due('clock', 3600))
            now[0] += 301
            self.assertTrue(state.due('clock', 3600))
            state.success('clock')
            self.assertFalse(state.due('clock', 3600))
            now[0] += 3600
            self.assertTrue(state.due('clock', 3600))

    def test_niri_geometry_negative_origin_and_scale(self):
        niri = Niri()
        window = {'workspace_id': 4, 'layout': {'tile_pos_in_workspace_view': [10,20], 'window_offset_in_tile': [2,3], 'window_size': [800,600]}}
        values = {'workspaces':[{'id':4,'is_active':True,'output':'DP-1'}], 'outputs':{'DP-1':{'logical':{'x':-1920,'y':0,'width':1920,'height':1080,'scale':1.5}}}}
        niri.query = values.__getitem__
        geo = niri.geometry(window)
        self.assertEqual((-1908,23,800,600), (geo.x,geo.y,geo.width,geo.height))
        self.assertEqual('-1908,23 800x600',geo.grim_region)
        values['workspaces'][0]['is_active'] = False
        with self.assertRaises(PlatformError):
            niri.geometry(window)

if __name__ == '__main__':
    unittest.main()

class WindowCaptureRegressionTests(unittest.TestCase):
    def test_niri_2604_null_coordinates_use_window_capture(self):
        from PySide6.QtGui import QImage
        from natro_linux.platform import Capture, GeometryUnavailable
        niri = Niri()
        window = {'id':1781, 'app_id':'org.vinegarhq.Sober', 'workspace_id':148,
                  'layout':{'tile_pos_in_workspace_view':None, 'window_offset_in_tile':[0,0], 'window_size':[2560,1390]}}
        niri.require_focus = lambda _: window
        niri.output = lambda _: ('DP-1', {'x':0,'y':0,'width':2560,'height':1440,'scale':1})
        with self.assertRaises(GeometryUnavailable):
            niri.geometry(window)
        capture = Capture(niri, 1781)
        image = QImage(2560,1390,QImage.Format.Format_RGBA8888)
        image.fill(0xff123456)
        capture._window_image = lambda _: image
        frame = capture.frame()
        self.assertEqual((1390,2560,4), frame.shape)
        self.assertEqual('DP-1',capture.output)
        self.assertIsNone(capture.geometry)


class MemoryMatchTests(unittest.TestCase):
    def test_pair_strategy_avoids_self_and_matched(self):
        from natro_linux.memory_match import Memory
        m = Memory(4)
        a = np.full((50,50,4),[10,20,30,255],dtype=np.uint8)
        b = np.full((50,50,4),[200,200,200,255],dtype=np.uint8)
        m.images = {0:a,1:b,2:a.copy()}
        self.assertEqual((0,2),m.known_pair())
        self.assertEqual(2,m.partner(0))
        self.assertEqual(3,m.unknown())
        m.matched.update((0,2))
        self.assertIsNone(m.known_pair())
        self.assertIsNone(m.partner(1))

    def test_ignored_pairs_are_allowed_on_last_chance(self):
        from natro_linux.memory_match import Memory
        m = Memory(4,ignored=[0,1])
        image = np.full((50,50,4),[10,20,30,255],dtype=np.uint8)
        m.images = {0:image,1:image.copy()}
        self.assertIsNone(m.known_pair())
        self.assertEqual((0,1),m.known_pair(True))

    def test_original_grid_coordinates(self):
        from natro_linux.memory_match import MemoryMatch
        frame = np.zeros((1080,1920,4),dtype=np.uint8)
        normal = MemoryMatch(None,'Normal')
        wide = MemoryMatch(None,'Extreme')
        self.assertEqual((840,420),normal.coordinates(0,frame))
        self.assertEqual((1080,660),normal.coordinates(15,frame))
        self.assertEqual((1120,660),wide.coordinates(19,frame))
