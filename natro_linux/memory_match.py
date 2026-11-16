"""Memory Match strategy and native game controller derived from Natro."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .engine import DetectionError
from .vision import find_text


GAMES = {
    'Normal': ('gtc-normalmm', 7200, 16),
    'Mega': ('gtc-megamm', 14400, 16),
    'Night': ('gtc-nightmm', 28800, 16),
    'Extreme': ('gtc-extrememm', 28800, 20),
    'Winter': ('gtc-wintermm', 14400, 20),
}


def identical(left, right, tolerance=10):
    return left.shape == right.shape and bool(np.all(np.abs(left[:, :, :3].astype(np.int16)-right[:, :, :3].astype(np.int16)) <= tolerance))


def card_content(patch):
    if patch.shape != (50, 50, 4):
        return False
    rgb = patch[:, :, :3].astype(np.int16)
    background = np.median(rgb, axis=(0, 1))
    # Closed cards and empty matched slots are flat fills. A hover tint alone
    # must not be recorded as an item, nor should an animation mid-flip.
    return np.mean(np.max(np.abs(rgb-background), axis=2) > 25) >= .04


def cleared_card(patch):
    if patch.shape != (50, 50, 4) or card_content(patch):
        return False
    r, g, b = np.median(patch[:, :, :3], axis=(0, 1))
    return r > 200 and g > 165 and b < 140 and g-b > 60


class Memory:
    def __init__(self, size, ignored=None):
        self.size = size
        self.images = {}
        self.matched = set()
        self.ignored = set(ignored or ())

    def known_pair(self, include_ignored=False, exclude=()):
        for i in sorted(self.images):
            if i in exclude or i in self.matched or (i in self.ignored and not include_ignored):
                continue
            for j in sorted(self.images):
                if j in exclude or j <= i or j in self.matched or (j in self.ignored and not include_ignored):
                    continue
                if identical(self.images[i], self.images[j]):
                    return (i, j)
        return None

    def partner(self, index, include_ignored=False):
        if index not in self.images:
            return None
        for other, image in self.images.items():
            if other != index and other not in self.matched and (include_ignored or other not in self.ignored) and identical(self.images[index], image):
                return other
        return None

    def unknown(self, exclude=()):
        return next((i for i in range(self.size) if i not in self.images and i not in self.matched and i not in exclude), None)


class MemoryMatch:
    def __init__(self, engine, game, ignored_items=()):
        if game not in GAMES:
            raise ValueError('Unknown Memory Match game')
        self.engine, self.game = engine, game
        self.ignored_items = ignored_items
        self.tiles = GAMES[game][2]
        self.offset = 40 if self.tiles == 20 else 0
        self.memory = Memory(self.tiles)

    def title(self, frame):
        e = self.engine
        name = 'memorymatch/mmtitlewide' if self.tiles == 20 else 'memorymatch/mmtitle'
        h, w = frame.shape[:2]
        x, y = w//2-250-self.offset, h//2-210
        width = 500+2*self.offset
        if x < 0 or y < 0 or x+width > w or y+50 > h:
            return False
        region = (x, y, width, 50)
        if e.vision.has(frame, name, 8, region):
            return True
        if find_text(frame[y:y+50, x:x+width], e.vision.templates.get(name), .97) is None:
            return False
        # Validate the yellow board below the glyphs, including its width.
        for px in (w//2-150-self.offset, w//2+150+self.offset):
            patch = frame[h//2+185:h//2+195, px-5:px+5, :3].astype(np.int16)
            if patch.shape != (10, 10, 3):
                return False
            r, g, b = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
            if np.mean((r > 170) & (g > 130) & (b < 150) & (r > g) & (g-b > 50)) < .9:
                return False
        return True

    def coordinates(self, index, frame):
        h, w = frame.shape[:2]
        return w//2-120+80*(index//4)-self.offset, h//2-120+80*(index%4)

    def last_chance(self, frame):
        h, w = frame.shape[:2]
        return self.engine.vision.has(frame, 'memorymatch/chances1', 10, (w//2-275-self.offset, h//2-146, 100, 100))

    def reveal(self, index, partner=None):
        """Return True for a read item, None for unread, False for closed board."""
        e = self.engine
        frame = e.frame(fresh=True)
        if not self.title(frame):
            e.motion.wait(.2)
            frame = e.frame(fresh=True)
            if not self.title(frame):
                return False
        x, y = self.coordinates(index, frame)
        before = frame[y-25:y+25, x-25:x+25].copy()
        e.status('Memory Match', f'{self.game}: карточка {index+1}/{self.tiles}')
        e.move_pointer(x, y)
        e.motion.wait(.4)
        e.motion.key('lbutton', True)
        try:
            e.motion.wait(.1)
        finally:
            e.input.key('lbutton', False)
        e.move_pointer(frame.shape[1]//2, frame.shape[0]//2-190)
        e.motion.wait(.5)
        deadline = e.motion.now()+3
        previous = None
        while e.motion.now() < deadline:
            frame = e.frame(fresh=True)
            if not self.title(frame):
                e.motion.wait(.2)
                if not self.title(e.frame(fresh=True)):
                    return False
                continue
            patch = frame[y-25:y+25, x-25:x+25].copy()
            if card_content(patch) and not identical(patch, before):
                if previous is None or not identical(patch, previous):
                    previous = patch
                    e.motion.wait(.15)
                    continue
                self.memory.images[index] = patch
                for item in self.ignored_items:
                    for variant in (1, 2):
                        name = f'memorymatch/mm{item.lower()}{variant}'
                        if name in e.vision.templates.index and e.vision.has(self.memory.images[index], name, 10):
                            self.memory.ignored.add(index)
                return True
            # Matching items may disappear before the next screenshot. Require
            # both selected cards to become empty slots while the board remains.
            if partner in self.memory.images and cleared_card(patch) and not cleared_card(before):
                px, py = self.coordinates(partner, frame)
                if cleared_card(frame[py-25:py+25, px-25:px+25]):
                    self.memory.images[index] = self.memory.images[partner].copy()
                    return True
            previous = None
            e.motion.wait(.05)
        e.status('Memory Match', f'{self.game}: карточка {index+1} не прочитана, продолжаю')
        return None

    def solve(self):
        e = self.engine
        if not e.wait_for(self.title, 5):
            raise DetectionError('Окно Memory Match не распознано')
        unread = set()
        attempts = [0]*self.tiles
        # Unread cards do not necessarily consume a game chance. Allow extra
        # selections, while retaining a bound for a completely unresponsive UI.
        for _ in range(self.tiles*2):
            frame = e.frame(fresh=True)
            if not self.title(frame):
                return
            last = self.last_chance(frame)
            pair = self.memory.known_pair(last, exclude=unread)
            first = pair[0] if pair else self.memory.unknown(exclude=unread)
            if first is None:
                # Only ignored or unmatched known cards remain.
                pair = self.memory.known_pair(True, exclude=unread)
                if pair is None:
                    remaining = set(range(self.tiles))-self.memory.matched
                    if not remaining:
                        break
                    first = min(remaining, key=lambda i: (attempts[i], i))
                    unread.discard(first)
                else:
                    first = pair[0]
            attempts[first] += 1
            first_read = self.reveal(first)
            if first_read is False:
                return
            if first_read is None:
                unread.add(first)
            else:
                unread.discard(first)
            second = (pair[1] if pair else self.memory.partner(first, last)) if first_read else None
            if second is None:
                second = self.memory.unknown(unread | {first})
            if second is None:
                remaining = set(range(self.tiles))-self.memory.matched-{first}
                second = min(remaining, key=lambda i: (attempts[i], i)) if remaining else None
            if second is None:
                break
            attempts[second] += 1
            second_read = self.reveal(second, partner=first if first_read else None)
            if second_read is False:
                return
            if second_read is None:
                unread.add(second)
            else:
                unread.discard(second)
            if first_read and second_read and identical(self.memory.images[first], self.memory.images[second]):
                self.memory.matched.update((first, second))
            e.motion.wait(1)
            if (last and first_read and second_read) or len(self.memory.matched) == self.tiles:
                break
        if not e.wait_for(lambda f: not self.title(f), 15, .25):
            raise DetectionError('Memory Match не завершился после повторных попыток. Проверь, реагирует ли игра на нажатия.')

    def run(self):
        e = self.engine
        route, _, _ = GAMES[self.game]
        e.reset()
        e.convert()
        e.status('Memory Match', self.game)
        e.motion.run(route)
        if not e.wait_for(lambda f: e.has('general/e_button', f, 15, True), 5):
            raise DetectionError('Автомат Memory Match не найден')
        e.motion.press('e')
        e.motion.wait(1.5)
        self.solve()
        e.state.success('memorymatch-'+self.game)
        e.state.increment('memory_matches')
