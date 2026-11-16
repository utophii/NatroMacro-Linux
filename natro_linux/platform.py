from __future__ import annotations
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import select
import subprocess
import threading
import time


class PlatformError(RuntimeError):
    pass


class FocusLost(PlatformError):
    pass


class GeometryUnavailable(PlatformError):
    pass


def command(args, *, timeout=2, binary=False):
    try:
        result = subprocess.run(args, capture_output=True, text=not binary, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PlatformError(f'{args[0]}: {exc}') from exc
    if result.returncode:
        error = result.stderr.decode(errors='replace') if binary else result.stderr
        raise PlatformError(error.strip() or f'{args[0]} failed: {result.returncode}')
    return result.stdout


@dataclass(frozen=True)
class Geometry:
    output: str
    x: int
    y: int
    width: int
    height: int
    output_x: int
    output_y: int
    output_width: int
    output_height: int

    @property
    def grim_region(self):
        return f'{self.x},{self.y} {self.width}x{self.height}'


class Niri:
    def query(self, topic):
        return json.loads(command(['niri', 'msg', '--json', topic], timeout=1))

    def action(self, name, *args):
        command(['niri', 'msg', 'action', name, *map(str, args)], timeout=1)

    def windows(self, app_id):
        return [w for w in self.query('windows') if (w.get('app_id') or '').casefold() == app_id.casefold()]

    def select(self, app_id, window_id=None):
        candidates = self.windows(app_id)
        if window_id is not None:
            candidates = [w for w in candidates if w['id'] == window_id]
        if not candidates:
            raise PlatformError(f'Window not found: {app_id}. Open Bee Swarm Simulator in Sober.')
        if len(candidates) != 1:
            raise PlatformError('Multiple Sober windows. Select window_id in settings.')
        return candidates[0]

    def focus(self, window_id):
        self.action('focus-window', '--id', window_id)

    def require_focus(self, window_id):
        w = self.query('focused-window')
        if not w or w['id'] != window_id:
            raise FocusLost('Sober lost focus. Input was stopped. Return to the game and restart.')
        return w

    def output(self, window):
        workspace = next((w for w in self.query('workspaces') if w['id'] == window['workspace_id']), None)
        if not workspace or not workspace.get('is_active'):
            raise PlatformError('Sober workspace is not visible')
        output = self.query('outputs').get(workspace['output'])
        logical = output and output.get('logical')
        if not logical:
            raise PlatformError('Niri did not provide output geometry')
        return workspace['output'], logical

    def geometry(self, window):
        output_name, logical = self.output(window)
        layout = window.get('layout', {})
        position = layout.get('tile_pos_in_workspace_view')
        size = layout.get('window_size')
        offset = layout.get('window_offset_in_tile')
        if not logical or position is None or not size or offset is None:
            raise GeometryUnavailable('Niri does not report coordinates for this window. Using window capture.')
        x, y = [position[i] + offset[i] for i in range(2)]
        width, height = size
        if x < -.5 or y < -.5 or x + width > logical['width'] + .5 or y + height > logical['height'] + .5:
            raise PlatformError('Sober must be fully visible on one monitor')
        return Geometry(output_name, logical['x'] + round(x), logical['y'] + round(y), int(width), int(height), logical['x'], logical['y'], logical['width'], logical['height'])


class Capture:
    def __init__(self, niri, window_id, scale=1):
        self.niri, self.window_id, self.scale = niri, window_id, scale
        self.geometry = None
        self.output = None
        self.logical = None
        self.identifier = None
        self.backend = None
        self.latest = None

    def _window_image(self, window):
        from PySide6.QtGui import QImage
        if self.backend != 'niri':
            try:
                if self.identifier is None:
                    helper = Path(__file__).parent / 'bin/natro-toplevels'
                    entries = json.loads(command([str(helper)], timeout=1))
                    candidates = [e for e in entries if e['app_id'] == window['app_id']]
                    if len(candidates) > 1:
                        candidates = [e for e in candidates if e['title'] == window.get('title')]
                    if len(candidates) != 1:
                        raise PlatformError('Cannot uniquely identify the Sober toplevel')
                    self.identifier = candidates[0]['identifier']
                data = command(['grim', '-T', self.identifier, '-s', str(self.scale), '-t', 'ppm', '-'], binary=True, timeout=1.5)
                image = QImage.fromData(data)
                if image.isNull():
                    raise PlatformError('Invalid toplevel image')
                self.backend = 'toplevel'
                return image
            except PlatformError:
                self.backend = 'niri'
        # Niri 26.04 may not expose ext-image-copy-capture. The screenshot action
        # works without coordinates. It also updates the clipboard.
        import tempfile
        with tempfile.TemporaryDirectory(prefix='natro-capture-') as folder:
            path = Path(folder) / 'window.png'
            self.niri.action('screenshot-window', '--id', self.window_id, '--path', str(path), '--show-pointer', 'false')
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if path.exists():
                    image = QImage(str(path))
                    if not image.isNull():
                        size = window.get('layout', {}).get('window_size')
                        if size:
                            from PySide6.QtCore import Qt
                            image = image.scaled(round(size[0]*self.scale), round(size[1]*self.scale), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
                        return image
                time.sleep(.02)
        raise PlatformError('Niri did not finish saving the window screenshot')

    def frame(self):
        from PySide6.QtGui import QImage
        from .vision import rgba
        window = self.niri.require_focus(self.window_id)
        self.output, self.logical = self.niri.output(window)
        try:
            geo = self.niri.geometry(window)
        except GeometryUnavailable:
            geo = None
        if geo is not None:
            data = command(['grim', '-s', str(self.scale), '-g', geo.grim_region, '-t', 'ppm', '-'], binary=True, timeout=1.5)
            image = QImage.fromData(data)
            self.backend = 'region'
        else:
            image = self._window_image(window)
        if image.isNull():
            raise PlatformError('Capture returned an invalid image')
        self.niri.require_focus(self.window_id)
        self.geometry = geo
        self.latest = rgba(image)
        return self.latest

    def locate(self):
        """Locate a captured window on its output using two agreeing UI patches."""
        if self.geometry is not None:
            return self.geometry
        from PySide6.QtGui import QImage
        from .vision import rgba, find_all
        import numpy as np
        self.frame()
        data = command(['grim', '-s', str(self.scale), '-o', self.output, '-t', 'ppm', '-'], binary=True, timeout=1.5)
        image = QImage.fromData(data)
        if image.isNull():
            raise PlatformError('Cannot capture output for pointer calibration')
        output = rgba(image)
        frame = self.latest
        h, w = frame.shape[:2]
        # Full-output windows have a unique origin, independent of their content.
        if output.shape == frame.shape:
            x = y = 0
        else:
            votes = {}
            for px, py in [(8,8), (w//2-40,8), (w-88,8), (8,h-40), (w-88,h-40), (8,80), (w//2-40,80)]:
                if px < 0 or py < 0:
                    continue
                tile = frame[py:py+24, px:px+64].copy()
                if tile.shape[:2] != (24,64) or float(tile[:,:,:3].std()) < 8:
                    continue
                for match in find_all(output, tile, tolerance=4, limit=10):
                    ox, oy = match.x-px, match.y-py
                    if ox >= 0 and oy >= 0 and ox+w <= output.shape[1] and oy+h <= output.shape[0]:
                        votes[(ox,oy)] = votes.get((ox,oy), 0) + 1
            winners = sorted(votes, key=votes.get, reverse=True)
            if not winners or votes[winners[0]] < 2 or (len(winners)>1 and votes[winners[0]] == votes[winners[1]]):
                raise GeometryUnavailable('Cannot confirm window position for clicks. Make Sober fullscreen and retry.')
            x, y = winners[0]
        logical = self.logical
        self.geometry = Geometry(self.output, logical['x']+round(x/self.scale), logical['y']+round(y/self.scale), round(w/self.scale), round(h/self.scale), logical['x'], logical['y'], logical['width'], logical['height'])
        return self.geometry


KEYS = {'esc': 1, 'escape': 1, 'space': 57, 'enter': 28, 'return': 28, 'tab': 15, 'backspace': 14, 'shift': 42, 'lshift': 42, 'ctrl': 29, 'lctrl': 29, 'alt': 56, 'lalt': 56, ',': 51, 'comma': 51, '.': 52, 'period': 52, 'up': 103, 'down': 108, 'left': 105, 'right': 106, '/': 53, '-': 12, '=': 13}
KEYS.update(zip('1234567890', range(2, 12)))
KEYS.update(zip('qwertyuiop', range(16, 26)))
KEYS.update(zip('asdfghjkl', range(30, 39)))
KEYS.update(zip('zxcvbnm', range(44, 51)))
KEYS.update({f'f{i}': 58+i for i in range(1, 11)})
KEYS.update(f11=87, f12=88, pageup=104, pagedown=109)


class NativeInput:
    def __init__(self, output=None):
        executable = Path(__file__).parent / 'bin/natro-input'
        if not executable.is_file():
            raise PlatformError('Build the input helper with make -C native')
        env = os.environ.copy()
        if output:
            env['NATRO_OUTPUT'] = output
        self.lock = threading.RLock()
        self.proc = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, bufsize=0)
        self.held = set()
        try:
            if self._read(2) != b'READY\n':
                raise PlatformError('Input helper did not start')
        except Exception:
            self.proc.terminate()
            _, err = self.proc.communicate(timeout=2)
            raise PlatformError('Wayland input unavailable: ' + err.decode(errors='replace').strip())

    def _read(self, timeout):
        if not select.select([self.proc.stdout], [], [], timeout)[0]:
            raise PlatformError('Input helper timed out')
        return self.proc.stdout.readline()

    def _send(self, line):
        with self.lock:
            try:
                self.proc.stdin.write((line + '\n').encode())
                if self._read(1) != b'OK\n':
                    raise PlatformError('Input helper stopped or rejected a command')
            except (BrokenPipeError, OSError) as exc:
                raise PlatformError('Input helper disconnected') from exc

    def key(self, name, down):
        name = name.lower()
        if name in ('lbutton', 'rbutton', 'mbutton'):
            code = {'lbutton': 272, 'rbutton': 273, 'mbutton': 274}[name]
            self._send(f'BUTTON {code} {int(down)}')
        else:
            if name not in KEYS:
                raise ValueError(f'Unsupported key {name!r}')
            self._send(f'KEY {KEYS[name]} {int(down)}')
        self.held.add(name) if down else self.held.discard(name)

    def heartbeat(self):
        self._send('PING')

    def release_all(self):
        with self.lock:
            try:
                self._send('RELEASE')
            finally:
                self.held.clear()

    def absolute(self, x, y, width, height):
        if not (0 <= x <= width and 0 <= y <= height):
            raise ValueError('Pointer position outside output')
        self._send(f'ABS {int(x)} {int(y)} {int(width)} {int(height)}')

    def scroll(self, amount):
        self._send(f'SCROLL {int(amount)}')

    def close(self):
        with self.lock:
            if self.proc.poll() is None:
                try:
                    self.release_all()
                    self._send('QUIT')
                except PlatformError:
                    self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait()
            for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                pipe.close()


class TraceInput:
    def __init__(self):
        self.events = []
        self.held = set()
        self.time = 0.

    def key(self, name, down):
        if name not in KEYS and name not in ('lbutton', 'rbutton', 'mbutton'):
            raise ValueError(f'Unsupported key {name!r}')
        self.events.append((round(self.time, 6), name, bool(down)))
        self.held.add(name) if down else self.held.discard(name)

    def heartbeat(self):
        pass

    def release_all(self):
        for key in sorted(self.held.copy()):
            self.key(key, False)

    def scroll(self, amount):
        self.events.append((round(self.time, 6), 'scroll', amount))

    def close(self):
        self.release_all()
