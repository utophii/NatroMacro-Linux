from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
import json
import ctypes
from pathlib import Path
import numpy as np
from PySide6.QtGui import QImage
from .config import DATA
from .motion import Buffs


def rgba(image: QImage):
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(image.constBits(), dtype=np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width()*4].reshape(image.height(), image.width(), 4).copy()


@dataclass(frozen=True)
class Match:
    x: int
    y: int
    width: int
    height: int


class Templates:
    def __init__(self):
        self.index = json.loads((DATA / 'images.json').read_text())

    @lru_cache(maxsize=1024)
    def get(self, name):
        item = self.index[name.lower()]
        if 'color' in item:
            color = item['color']
            return np.full((item['height'], item['width'], 4), [(color >> 16) & 255, (color >> 8) & 255, color & 255, (color >> 24) & 255], dtype=np.uint8)
        image = QImage(str(DATA / item['file']))
        if image.isNull():
            raise ValueError(f'Cannot load template {name}')
        return rgba(image)

    def group(self, prefix):
        return [k for k in self.index if k.startswith(prefix)]


@lru_cache(maxsize=1)
def native_matcher():
    path = Path(__file__).parent / 'bin/libnatrovision.so'
    if not path.exists():
        return None
    lib = ctypes.CDLL(str(path))
    u8 = ctypes.POINTER(ctypes.c_uint8)
    lib.natro_find.argtypes = [u8, ctypes.c_int, ctypes.c_int, u8, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    lib.natro_find.restype = ctypes.c_int
    return lib


def find_all(frame, template, tolerance=0, limit=1):
    """Masked per-channel matching including the last valid row and column.

    Transparent template pixels are ignored. Constant images use a summed
    area table. General images use anchor rejection before full comparison.
    """
    if frame.ndim != 3 or template.ndim != 3 or frame.shape[2] != 4 or template.shape[2] != 4:
        raise ValueError('Expected RGBA images')
    if not 0 <= tolerance <= 255 or not 1 <= limit <= 100000:
        raise ValueError('Invalid matching parameters')
    h, w = template.shape[:2]
    height, width = frame.shape[:2]
    if not h or not w or h > height or w > width:
        return []
    library = native_matcher()
    if library is not None:
        src = np.ascontiguousarray(frame, dtype=np.uint8)
        tmpl = np.ascontiguousarray(template, dtype=np.uint8)
        positions = np.empty((limit, 2), dtype=np.int32)
        u8 = ctypes.POINTER(ctypes.c_uint8)
        count = library.natro_find(src.ctypes.data_as(u8), width, height, tmpl.ctypes.data_as(u8), w, h, tolerance, limit, positions.ctypes.data_as(ctypes.POINTER(ctypes.c_int)))
        if count < 0:
            raise MemoryError('Native template matcher allocation failed')
        return [Match(int(x), int(y), w, h) for x, y in positions[:count]]
    mask = template[:, :, 3] > 0
    yy, xx = np.nonzero(mask)
    if not len(yy):
        return []
    values = template[yy, xx, :3].astype(np.int16)
    src = frame[:, :, :3]
    oh, ow = height - h + 1, width - w + 1
    if np.all(mask) and np.all(values == values[0]):
        good = np.all(np.abs(src.astype(np.int16) - values[0]) <= tolerance, axis=2)
        integral = np.pad(good.astype(np.int32), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        counts = integral[h:, w:] - integral[:-h, w:] - integral[h:, :-w] + integral[:-h, :-w]
        ys, xs = np.nonzero(counts == h * w)
        return [Match(int(x), int(y), w, h) for y, x in zip(ys[:limit], xs[:limit])]
    candidates = np.ones((oh, ow), dtype=bool)
    for index in np.unique(np.linspace(0, len(yy) - 1, min(12, len(yy)), dtype=int)):
        y, x = yy[index], xx[index]
        candidates &= np.all(np.abs(src[y:y+oh, x:x+ow].astype(np.int16) - values[index]) <= tolerance, axis=2)
        if not candidates.any():
            return []
    ys, xs = np.nonzero(candidates)
    result = []
    for offset in range(0, len(ys), 64):
        cy, cx = ys[offset:offset+64], xs[offset:offset+64]
        sample = src[cy[:, None] + yy, cx[:, None] + xx].astype(np.int16)
        ok = np.all(np.abs(sample - values) <= tolerance, axis=(1, 2))
        for y, x in zip(cy[ok], cx[ok]):
            result.append(Match(int(x), int(y), w, h))
            if len(result) >= limit:
                return result
    return result


def find_text(frame, template, threshold=.94):
    matches = find_text_candidates(frame, template, threshold)
    return matches[0] if matches else None


def find_text_candidates(frame, template, threshold=.94, limit=1):
    """Match dark glyphs with one-pixel edge tolerance for Sober font rendering.

    Both glyph coverage and background separation are required, so a solid
    dark rectangle cannot satisfy the transparent parts of a text template.
    Call on a bounded text region, not an entire game frame.
    """
    src = frame[:, :, :3].max(axis=2) < 100
    mask = (template[:, :, 3] > 0) & (template[:, :, :3].max(axis=2) < 100)
    h, w = mask.shape
    oh, ow = src.shape[0]-h+1, src.shape[1]-w+1
    ink = int(mask.sum())
    if oh <= 0 or ow <= 0 or not ink:
        return []

    def dilate(a):
        padded = np.pad(a, 1)
        return np.logical_or.reduce([padded[y:y+a.shape[0], x:x+a.shape[1]]
                                     for y in range(3) for x in range(3)])

    def overlaps(stencil, pixels):
        result = np.zeros((oh, ow), dtype=np.int32)
        for y, x in zip(*np.nonzero(stencil)):
            result += pixels[y:y+oh, x:x+ow]
        return result

    coverage = overlaps(mask, dilate(src)) / ink
    integral = np.pad(src.astype(np.int32), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    counts = integral[h:, w:]-integral[:-h, w:]-integral[h:, :-w]+integral[:-h, :-w]
    precision = overlaps(dilate(mask), src) / np.maximum(1, counts)
    scores = 2*coverage*precision / np.maximum(1e-9, coverage+precision)
    scores[(counts < ink*.6) | (counts > ink*1.8)] = 0
    results = []
    for _ in range(limit):
        y, x = np.unravel_index(scores.argmax(), scores.shape)
        score = float(scores[y, x])
        if score < threshold:
            break
        results.append((Match(int(x), int(y), w, h), score))
        # Collapse nearby alignments of the same word, retaining other rows.
        rx, ry = max(1, w//2), max(1, h//2)
        scores[max(0, y-ry):y+ry+1, max(0, x-rx):x+rx+1] = -1
    return results


class Vision:
    def __init__(self, templates=None):
        self.templates = templates or Templates()

    def find(self, frame, name, tolerance=8, region=None, limit=1):
        x = y = 0
        if region:
            x, y, w, h = map(int, region)
            if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > frame.shape[1] or y+h > frame.shape[0]:
                return []
            frame = frame[y:y+h, x:x+w]
        matches = find_all(frame, self.templates.get(name), tolerance, limit)
        return [Match(m.x+x, m.y+y, m.width, m.height) for m in matches]

    def has(self, frame, name, tolerance=8, region=None):
        return bool(self.find(frame, name, tolerance, region))

    def hud_offset(self, frame):
        # Port of GetYOffset in lib/Roblox.ahk, including the fill rejection.
        h, w = frame.shape[:2]
        region = (w//2, 0, min(60, w-w//2), min(200, h))
        for match in self.find(frame, 'offset/toppollen', 20, region, limit=20):
            patch = frame[match.y:match.y+match.height, match.x:match.x+match.width]
            if not self.has(patch, 'offset/toppollenfill', 20) and self.hud_panels(frame, match.y-14):
                return match.y-14
        # Sober can rasterize the same font at a fractional pixel position.
        x, y, width, height = region
        candidates = find_text_candidates(frame[y:y+height, x:x+width],
                                          self.templates.get('offset/toppollen'), .96, limit=8)
        for match, _ in candidates:
            offset = match.y+y-14
            if self.hud_panels(frame, offset):
                return offset
        return None

    @staticmethod
    def hud_panels(frame, offset):
        """Verify the paired Honey/Pollen panels independently of open menus.

        General menu templates identify the selected tab and are absent when
        every menu is closed. The HUD instead has two light label backgrounds
        beside contrasting counters, each in a 300-pixel-wide panel.
        """
        h, w = frame.shape[:2]
        if w < 600 or offset < 0 or offset+32 > h:
            return False
        for x in (w//2-300, w//2):
            for top, bottom in ((3, 9), (27, 32)):
                border = frame[offset+top:offset+bottom, x+5:x+53, :3].astype(np.int16)
                light = (border.min(2) > 160) & (border.max(2)-border.min(2) < 70)
                if np.mean(light) < .9:
                    return False
            counter = frame[offset+4:offset+30, x+65:x+280, :3]
            if np.mean(counter.min(2) < 160) < .5:
                return False
        return True

    def hive(self, frame):
        h, w = frame.shape[:2]
        area = frame[3*h//4:]
        needed = max(1, w*w//3200)
        return any(len(find_all(area, self.templates.get(name), 4, needed)) >= needed for name in self.templates.group('reset/hive/'))

    def empty_hive(self, frame, offset=0):
        """Recognize both lines of the empty-backpack hive instruction.

        These upstream templates contain sparse white strokes, not complete
        glyphs. Transparent pixels cannot be treated as text background.
        """
        height, width = frame.shape[:2]
        x, y = width//2-200, offset+36
        if x < 0 or y < 0 or y+80 > height:
            return False
        area = frame[y:y+80, x:x+400, :3].astype(np.int16)
        white = area.min(2) > 190
        padded = np.pad(white, 1)
        nearby = np.logical_or.reduce([padded[dy:dy+80, dx:dx+400]
                                       for dy in range(3) for dx in range(3)])
        blue = (area[:, :, 2]-area[:, :, 0] > 35) & (area[:, :, 1] > area[:, :, 0])
        def matches(name):
            template = self.templates.get(name)
            h, w = template.shape[:2]
            oh, ow = 81-h, 401-w
            mask_y, mask_x = np.nonzero(template[:, :, 3])
            hits = np.zeros((oh, ow), dtype=np.int32)
            for ty, tx in zip(mask_y, mask_x):
                hits += nearby[ty:ty+oh, tx:tx+ow]
            yy, xx = np.nonzero(hits >= len(mask_y)*.98)
            return [(int(cx), int(cy)) for cy, cx in zip(yy, xx)
                    if blue[cy:cy+h, cx:cx+w].mean() > .5
                    and .08 < white[cy:cy+h, cx:cx+w].mean() < .5]
        first = matches('general/colhey')
        if not first:
            return False
        second = matches('convert/collectpollen')
        return any(abs(ax-bx) <= 8 and 18 <= by-ay <= 35
                   for ax, ay in first for bx, by in second)

    def buffs(self, frame, offset=0):
        area = frame[offset+48:offset+78]
        if area.shape[0] != 30:
            raise ValueError('Buff region outside frame')
        result = Buffs()
        x = 0
        hastes = []
        for _ in range(3):
            found = self.find(area, 'movement/pbmhaste', 0, (x, 14, area.shape[1]-x, 16))
            if not found:
                break
            match = found[0]
            melody = self.has(area, 'movement/pbmmelody', 12, (match.x+2, 0, max(16, 2*match.y-24)-2, match.y))
            if not melody:
                hastes.append(match)
            x = match.x + max(1, 2*match.y-14)
            if x >= area.shape[1]:
                break
        if hastes:
            result.haste = 1
            result.coconut_haste = len(hastes) >= 2
            match = hastes[0]
            x1, y1 = match.x+2*match.y-44, max(0, match.y-18)
            for digit in range(9, 0, -1):
                if self.has(area, f'movement/buffdigit{digit}', 0, (x1, y1, 30, match.y-1-y1)):
                    result.haste = 10 if digit == 1 else digit
                    break
        for attribute, name, tolerance in [('haste_plus', 'pbmhasteplus', 0), ('oil', 'pbmoil', 4), ('smoothie', 'pbmsmoothie', 4)]:
            setattr(result, attribute, self.has(area, 'movement/' + name, tolerance, (0, 25, area.shape[1], 2)))
        result.bear = any(self.has(area, 'movement/pbmbear' + name, 8, (0, 25, area.shape[1], 2)) for name in ('brown','black','panda','polar','gummy','science','mother'))
        return result

    @staticmethod
    def backpack(frame, region, offset=0):
        if region is None:
            x, y = frame.shape[1]//2+62, offset+6
            if y >= frame.shape[0] or x >= frame.shape[1]:
                return None
            return backpack_color(frame[y,x,:3])
        x, y, w, h = region
        if x+w > frame.shape[1] or y+h > frame.shape[0]:
            raise ValueError('Backpack calibration does not fit the captured window')
        pixels = frame[y:y+h, x:x+w, :3].astype(np.int16)
        # User selects the interior of the progress bar, excluding text and border.
        colored = (pixels.max(2) - pixels.min(2) > 45) & (pixels.max(2) > 100)
        columns = np.mean(colored, axis=0) >= .5
        return float(np.mean(columns) * 100)


def backpack_color(rgb):
    """Port of background.ahk color thresholds, unknown colors stay unknown."""
    r,g,b = map(int,rgb)
    gb = g*256+b
    if (r,g,b) == (247,0,23) or (r>=224 and 0x1000 < gb <= 0x2427):
        return 100.
    bounds = [
        (65,66,0xFC85,0xFF80,5), (66,68,0xF984,0xFE85,10),
        (68,71,0xF582,0xFB84,15), (71,75,0xF080,0xF782,20),
        (75,79,0xEA7D,0xF280,25), (79,85,0xE37A,0xEC7D,30),
        (85,91,0xDA76,0xE57A,35), (91,98,0xD072,0xDC76,40),
        (98,105,0xC66D,0xD272,45), (105,114,0xBA68,0xC86D,50),
        (114,123,0xAD62,0xBC68,55), (123,133,0x9E5C,0xAF62,60),
        (133,144,0x8F55,0xA05C,65), (144,156,0x7E4E,0x9155,70),
        (156,169,0x6C46,0x804E,75), (169,182,0x5A3F,0x6E46,80),
        (182,196,0x4637,0x5D3F,85), (196,211,0x322E,0x4A37,90)]
    for low, high, gb_low, gb_high, value in bounds:
        if low < r <= high and gb_low < gb <= gb_high:
            return float(value)
    if 211 < r <= 247 and 0x0010 <= gb <= 0x342E:
        return 95.
    if 60 <= r <= 66 and 0xFC80 <= gb <= 0xFF90:
        return 0.
    return None
