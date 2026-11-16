"""Read visible repeatable quests without sending input or guessing missing bars."""
from dataclasses import asdict, dataclass
import numpy as np
from .game_data import game_data
from .vision import find_text

GIVERS = {
    'PolarBear': ('polar_bear', 'polar_bear2', 'polar_bear3'),
    'BlackBear': tuple('black_bear'+suffix for suffix in ('', '2', '3', '4', '5', '6')),
    'BuckoBee': ('bucko', 'bucko2'),
    'RileyBee': ('riley', 'riley2'),
}


@dataclass(frozen=True)
class Objective:
    bar: int
    action: str
    target: str
    state: str


@dataclass(frozen=True)
class Quest:
    giver: str
    title: str
    confidence: float
    objectives: tuple[Objective, ...]

    @property
    def complete(self):
        return bool(self.objectives) and all(o.state == 'complete' for o in self.objectives)

    def as_dict(self):
        return {**asdict(self), 'complete': self.complete}


def bar_state(frame, bottom, bar, boundary):
    y = bottom+15+50*(bar-1)
    if y >= boundary or y < 0 or y >= frame.shape[0] or frame.shape[1] <= 26:
        return 'unknown'
    pixel = frame[y, 26, :3].astype(np.int16)
    # Explicit known colors only, other UI/background colors are not completion.
    for color, state in (((244, 108, 85), 'incomplete'), ((110, 255, 96), 'incomplete'),
                         ((108, 219, 97), 'complete')):
        if np.max(np.abs(pixel-color)) <= 2:
            return state
    return 'unknown'


def title_bands(frame, offset):
    if frame.shape[1] < 306:
        return []
    color = np.array([229, 240, 247])
    matching = np.all(np.abs(frame[:, :8, :3].astype(np.int16)-color) <= 2, axis=(1, 2))
    matching[:offset+150] = False
    edges = np.diff(np.pad(matching.astype(np.int8), (1, 1)))
    return [(int(start), int(end)) for start, end in zip(np.where(edges == 1)[0], np.where(edges == -1)[0])
            if 25 <= end-start <= 35]


def visible_quests(frame, vision, offset=0):
    data = game_data()['quests']
    result = []
    bands = title_bands(frame, offset)
    for band_index, (start, bottom) in enumerate(bands):
        area = frame[start:bottom, :306]
        giver_scores = []
        for giver, names in GIVERS.items():
            matches = [find_text(area, vision.templates.get('legacy/'+n), .90) for n in names]
            matches = [m for m in matches if m]
            if matches:
                m, score = max(matches, key=lambda value: value[1])
                giver_scores.append((score, giver, m.x+m.width))
        _, ink_x = np.nonzero(area[:, :, :3].max(axis=2) < 100)
        if not len(ink_x):
            continue
        right_edge = int(ink_x.max())
        candidates = []
        for giver_score, giver, giver_right in giver_scores:
            for title in data[giver]:
                key = 'legacy/'+title.lower()
                if key not in vision.templates.index:
                    continue
                template = vision.templates.get(key)
                match = find_text(area, template, .90)
                if match is None:
                    continue
                m, score = match
                mask = (template[:, :, 3] > 0) & (template[:, :, :3].max(axis=2) < 100)
                _, tx = np.nonzero(mask)
                if not len(tx) or abs(m.x+int(tx.max())-right_edge) > 3 or m.x < giver_right-5:
                    continue
                candidates.append((score*giver_score, giver, title))
        candidates.sort(reverse=True)
        if not candidates or (len(candidates) > 1 and candidates[0][0]-candidates[1][0] < .02):
            continue
        score, giver, title = candidates[0]
        boundary = bands[band_index+1][0] if band_index+1 < len(bands) else frame.shape[0]
        objectives = tuple(Objective(bar, action, target, bar_state(frame, bottom, bar, boundary))
                           for bar, action, target in data[giver][title])
        result.append(Quest(giver, title, round(score, 4), objectives))
    return result
