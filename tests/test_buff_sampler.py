import threading
import time
import unittest
from natro_linux.buff_sampler import BuffSampler
from natro_linux.config import Config
from natro_linux.motion import Motion, Buffs


class RecordingInput:
    def __init__(self):
        self.held = set()
        self.events = []

    def heartbeat(self):
        pass

    def key(self, name, down):
        self.events.append((time.monotonic(), name, down))
        if down:
            self.held.add(name)
        else:
            self.held.discard(name)

    def release_all(self):
        self.held.clear()


class BuffSamplerTests(unittest.TestCase):
    def test_slow_capture_does_not_extend_short_walk(self):
        entered, finish = threading.Event(), threading.Event()
        def capture():
            entered.set()
            finish.wait(2)
            return Buffs()
        sampler = BuffSampler(capture, Buffs())
        try:
            self.assertTrue(entered.wait(1))
            backend = RecordingInput()
            motion = Motion(backend, Config(base_speed=29))
            motion.buff_provider = sampler.get
            motion.move(1, 'w')
            duration = backend.events[-1][0]-backend.events[0][0]
            expected = (4-.125)/29
            self.assertGreaterEqual(duration, expected-.01)
            self.assertLess(duration, expected+.12)
            self.assertFalse(backend.held)
            self.assertFalse(finish.is_set())
        finally:
            finish.set()
            sampler.close()

    def test_stale_buffs_stop_movement_and_release_keys(self):
        now = [0.]
        finish = threading.Event()
        def capture():
            finish.wait(2)
            return Buffs()
        sampler = BuffSampler(capture, Buffs(), clock=lambda: now[0])
        try:
            now[0] = 3.
            backend = RecordingInput()
            motion = Motion(backend)
            motion.buff_provider = sampler.get
            with self.assertRaisesRegex(RuntimeError, 'устарели'):
                motion.move(1, 'w')
            self.assertFalse(backend.held)
        finally:
            finish.set()
            sampler.close()

    def test_initial_haste_is_used_on_first_short_step(self):
        backend = RecordingInput()
        motion = Motion(backend, Config(base_speed=29))
        motion.buff_provider = lambda: Buffs(haste=10)
        motion.move(2, 'w')
        duration = backend.events[-1][0]-backend.events[0][0]
        expected = (8-.125)/58
        self.assertLess(duration, expected+.08)
        self.assertGreaterEqual(duration, expected-.01)


if __name__ == '__main__':
    unittest.main()
