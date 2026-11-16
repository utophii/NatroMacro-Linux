from __future__ import annotations
from dataclasses import dataclass
import importlib
import json
import math
import re
import threading
import time
from .config import DATA, Config
from .platform import TraceInput


class Cancelled(Exception):
    pass


class RestartCycle(Exception):
    """Resume from a known location after releasing input during a pause."""


class Control:
    def __init__(self):
        self.stopped = threading.Event()
        self.paused = threading.Event()

    def stop(self):
        self.stopped.set()

    def pause(self):
        self.paused.set()

    def resume(self):
        self.paused.clear()


@dataclass
class Buffs:
    haste: int = 0
    coconut_haste: bool = False
    haste_plus: bool = False
    oil: bool = False
    smoothie: bool = False
    bear: bool = False

    def speed(self, config: Config, cap=0):
        # Port of lib/Walk.ahk, DetectMovespeed.
        return ((config.base_speed + 10 * self.coconut_haste + 4 * self.bear)
                * (1.1 if config.hasty_guard else 1)
                * (1.15 if config.gifted_hasty else 1)
                * (1 + max(0, self.haste - cap) * .1)
                * (2 if self.haste_plus else 1)
                * (1.2 if self.oil else 1)
                * (1.25 if self.smoothie else 1))


class Motion:
    def __init__(self, backend, config=None, control=None, focus_check=None, observer=None):
        self.backend = backend
        self.config = config or Config()
        self.control = control or Control()
        self.focus_check = focus_check
        self.observer = observer
        self.trace = isinstance(backend, TraceInput)
        self.buff_provider = None
        self.buffs = Buffs()
        self.last_focus = -math.inf
        self.last_buffs = -math.inf
        self.vars = {
            'fwdkey': 'w', 'backkey': 's', 'leftkey': 'a', 'rightkey': 'd',
            'tcfbkey': 'w', 'afcfbkey': 's', 'tclrkey': 'a', 'afclrkey': 'd',
            'rotleft': ',', 'rotright': '.', 'rotup': 'pageup', 'rotdown': 'pagedown',
            'zoomin': 'i', 'zoomout': 'o', 'sc_e': 'e', 'sc_r': 'r', 'sc_l': 'l',
            'sc_esc': 'esc', 'sc_enter': 'enter', 'sc_lshift': 'lshift', 'sc_space': 'space', 'sc_1': '1',
            'hiveslot': self.config.hive_slot, 'hivebees': self.config.hive_bees,
            'movemethod': self.config.move_method, 'size': 1., 'reps': 3, 'facingcorner': 0,
            'keydelay': self.config.key_delay_ms, 'a_keydelay': self.config.key_delay_ms, 'a_index': 0,
        }
        self.cannon_handler = None
        self._manifest = json.loads((DATA / 'movements.json').read_text())

    def now(self):
        return self.backend.time if self.trace else time.monotonic()

    def checkpoint(self):
        if self.control.stopped.is_set():
            raise Cancelled()
        if self.control.paused.is_set():
            self.backend.release_all()
            while self.control.paused.is_set():
                if self.control.stopped.wait(.1):
                    raise Cancelled()
                self.backend.heartbeat()
            raise RestartCycle()
        now = self.now()
        if self.focus_check and now - self.last_focus >= .1:
            self.focus_check()
            self.last_focus = now
        self.backend.heartbeat()

    def wait(self, seconds):
        if not math.isfinite(seconds) or seconds < 0 or seconds > 86400:
            raise ValueError(f'Invalid wait {seconds}')
        self.checkpoint()
        if self.trace:
            self.backend.time += seconds
            return
        end = time.monotonic() + seconds
        while True:
            self.checkpoint()
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            self.control.stopped.wait(min(.025, remaining))

    def sleep_ms(self, milliseconds):
        self.wait(max(0, milliseconds) / 1000)

    def set_key_delay(self, milliseconds):
        if not 0 <= milliseconds <= 5000:
            raise ValueError('Key delay outside supported range')
        self.vars['a_keydelay'] = milliseconds

    def key(self, name, down):
        self.checkpoint()
        self.backend.key(name.lower(), down)

    def press(self, name, count=1):
        for _ in range(self.loop_count(count)):
            self.key(name, True)
            try:
                self.sleep_ms(max(15, self.vars['a_keydelay']))
            finally:
                self.backend.key(name, False)
            self.sleep_ms(self.vars['a_keydelay'])

    def send(self, text):
        # Only the key notation present in upstream movement files is accepted.
        tokens = list(re.finditer(r'\{([^{}]+)\}', text))
        if ''.join(t.group() for t in tokens) != text:
            raise ValueError(f'Unsupported key sequence {text!r}')
        for token in tokens:
            parts = token[1].split()
            if len(parts) > 2:
                raise ValueError(f'Invalid key token {token[1]}')
            name = parts[0].lower()
            name = self.vars.get(name, name)
            mode = parts[1].lower() if len(parts) == 2 else '1'
            if mode in ('up', 'down'):
                self.key(name, mode == 'down')
            else:
                self.press(name, int(mode))

    def walk(self, tiles, haste_cap=0):
        if not math.isfinite(tiles) or not 0 <= tiles <= 2000:
            raise ValueError('Walk distance must be 0..2000 tiles')
        # Upstream Walk starts its distance accumulator at freq // 8.
        # This is 0.125 studs, not a 0.125-second delay.
        target = max(0., tiles * 4 - .125) * self.config.speed_correction
        if self.trace:
            self.wait(target / self.buffs.speed(self.config, haste_cap))
            return
        moved = 0.
        if self.buff_provider and self.config.dynamic_speed:
            self.buffs = self.buff_provider()
            self.last_buffs = self.now()
        previous = self.now()
        previous_speed = self.buffs.speed(self.config, haste_cap)
        while moved < target:
            self.checkpoint()
            if self.buff_provider and self.config.dynamic_speed and self.now() - self.last_buffs >= .25:
                self.buffs = self.buff_provider()
                self.last_buffs = self.now()
            current = self.now()
            speed = self.buffs.speed(self.config, haste_cap)
            moved += (previous_speed + speed) * .5 * (current - previous)
            previous, previous_speed = current, speed
            if moved < target:
                self.control.stopped.wait(min(.01, (target - moved) / speed))

    def move(self, tiles, *keys):
        keys = [k for k in keys if k]
        try:
            for key in keys:
                self.key(key, True)
            self.walk(tiles)
        finally:
            for key in keys:
                self.backend.key(key, False)

    def goto_ramp(self):
        self.move(5, self.vars['fwdkey'])
        self.move(9.2 * self.vars['hiveslot'] - 4, self.vars['rightkey'])

    def goto_cannon(self):
        if self.trace:
            self.backend.events.append((self.now(), 'goto_cannon', None))
            return
        if self.cannon_handler is None:
            raise RuntimeError('Cannon detection is required for this route')
        self.cannon_handler()

    def jump(self, *movements):
        started = self.now()
        self.key('space', True)
        self.sleep_ms(100)
        self.key('space', False)
        for args in movements:
            self.move(*args)
        self.wait(max(0, 1.4 - (self.now() - started)))

    def run(self, name, kind='routes', field=None):
        entry = next((r for r in self._manifest if r['name'].casefold() == name.casefold() and r['kind'] == kind), None)
        if not entry:
            raise ValueError(f'Unknown {kind}: {name}')
        old = self.vars.copy()
        if field:
            self.vars.update(size=field.size, reps=field.repetitions,
                tcfbkey='s' if field.invert_fb else 'w', afcfbkey='w' if field.invert_fb else 's',
                tclrkey='d' if field.invert_lr else 'a', afclrkey='a' if field.invert_lr else 'd')
        # Keep an already-held tool button while releasing all movement keys.
        keep = self.backend.held.copy()
        try:
            self.checkpoint()
            importlib.import_module(entry['module']).run(self)
        finally:
            for key in self.backend.held.copy() - keep:
                self.backend.key(key, False)
            self.vars = old

    @staticmethod
    def eq(a, b):
        return a.casefold() == b.casefold() if isinstance(a, str) and isinstance(b, str) else a == b

    @staticmethod
    def mod(a, b):
        return math.fmod(a, b)

    @staticmethod
    def loop_count(n):
        if not math.isfinite(n) or int(n) != n or not 0 <= n <= 10000:
            raise ValueError(f'Invalid loop count {n}')
        return int(n)
