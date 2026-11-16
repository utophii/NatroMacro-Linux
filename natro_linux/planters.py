"""Native planter scheduling and visual placement/harvest controllers."""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
from .game_data import game_data
from .engine import DetectionError
from .inventory import Inventory

NECTAR_COLORS = {'Comforting': 0x7E9EB3, 'Motivating': 0x937DB3,
                 'Satisfying': 0xB398A7, 'Refreshing': 0x78B375, 'Invigorating': 0xB35951}
CONSUMABLE = {'PaperPlanter', 'TicketPlanter'}


def nectar_percent(frame, nectar, offset=0):
    """Port of ba_GetNectarPercent with bounded full-height handling."""
    color = NECTAR_COLORS[nectar]
    rgb = [(color >> 16) & 255, (color >> 8) & 255, color & 255]
    mask = np.all(frame[max(0, offset+30):offset+151, :861, :3] == rgb, axis=2)
    yy, xx = np.nonzero(mask)
    if not len(yy):
        return 0.
    y, x = int(yy[0]), int(xx[0])
    pixels = 0
    while pixels < 38 and y+pixels < mask.shape[0] and mask[y+pixels, x]:
        pixels += 1
    return min(99.99, math.floor(pixels/38*100+.5))


def interval_seconds(row, projected=0., minimum=70., mode='auto', hours=2.):
    """Port of ba_updatePlanterData, preserving upstream coefficients."""
    _, nectar_bonus, growth_bonus, full_hours = row
    if mode == 'full':
        return math.floor(full_hours*3600)
    if mode == 'interval':
        return math.floor(min(full_hours, hours)*3600)
    if mode != 'auto':
        raise ValueError('Unknown planter harvest mode')
    bonus = nectar_bonus*growth_bonus
    time_to_cap = max(.25, max(0, (100-projected)/nectar_bonus)*.24/growth_bonus)
    if bonus < 1.2:
        automatic = min(time_to_cap, .5)
    elif minimum > projected and projected <= 90:
        automatic = ((minimum-projected+100/projected*bonus)/nectar_bonus*.24/growth_bonus
                     if projected > 0 else full_hours)
    else:
        automatic = time_to_cap
    return math.floor(min(full_hours, automatic+automatic/bonus, time_to_cap+time_to_cap/bonus)*3600)


@dataclass(frozen=True)
class Choice:
    slot: str
    field: str
    planter: str
    nectar: str
    seconds: int
    estimated_percent: float


class PlanterBook:
    """Write-ahead records survive stop, pause and process failure."""
    def __init__(self, state):
        self.state = state
        self.data = state.data.setdefault('planters', {'records': {}, 'last_fields': {}})
        if not isinstance(self.data, dict) or not isinstance(self.data.get('records'), dict) or not isinstance(self.data.get('last_fields'), dict):
            raise ValueError('Invalid planter state')
        if len(self.records) > 3:
            raise ValueError('Too many persisted planters')
        for slot, row in self.records.items():
            if slot not in ('1', '2', '3') or row.get('phase') not in ('placing', 'growing', 'harvesting', 'uncertain'):
                raise ValueError('Invalid planter record')
            if row.get('field') not in game_data()['rankings'] or row.get('planter') not in game_data()['planters']:
                raise ValueError('Unknown persisted planter')
            for name in ('seconds', 'estimated_percent', 'planted_at', 'due'):
                if not isinstance(row.get(name), (int, float)) or not math.isfinite(row[name]) or row[name] < 0:
                    raise ValueError(f'Invalid planter {name}')
        # An interrupted action must be reconciled before repeating it.
        changed = False
        for row in self.records.values():
            if row['phase'] in ('placing', 'harvesting'):
                row['phase'] = 'uncertain'
                changed = True
        if changed:
            state.save()

    @property
    def records(self):
        return self.data['records']

    def reserve(self, choice):
        from dataclasses import asdict
        if choice.slot in self.records or len(self.records) >= 3:
            raise ValueError('Planter slot is occupied')
        if any(r['field'] == choice.field or r['planter'] == choice.planter for r in self.records.values()):
            raise ValueError('Planter or field is occupied')
        row = asdict(choice)
        row.update(phase='placing', planted_at=self.state.clock(), due=0)
        self.records[choice.slot] = row
        self.state.save()

    def growing(self, slot):
        row = self.records[slot]
        now = self.state.clock()
        row.update(phase='growing', planted_at=now, due=now+row['seconds'])
        self.data['last_fields'][row['nectar']] = row['field']
        self.state.save()

    def phase(self, slot, phase):
        self.records[slot]['phase'] = phase
        self.state.save()

    def remove(self, slot, harvested=False):
        del self.records[slot]
        if harvested:
            self.state.increment('planters_harvested', save=False)
        self.state.save()

    def reconcile(self, slot, remaining_hours=None):
        if slot not in self.records:
            raise ValueError('No planter in this slot')
        if remaining_hours is None:
            # User-confirmed manual collection is not an observed macro harvest.
            self.remove(slot)
        else:
            from .config import number
            number('remaining_hours', remaining_hours, 0, 48)
            self.records[slot].update(phase='growing', due=self.state.clock()+remaining_hours*3600)
            self.state.save()


def choose(settings, book, percentages):
    """Choose a free field and planter using original field ranking tables."""
    records = book.records
    data = game_data()
    if settings.mode == 'off' or len(records) >= settings.max_active:
        return None
    used_fields = {r['field'] for r in records.values()}
    used_planters = {r['planter'] for r in records.values()}
    if settings.mode == 'manual':
        for index, entry in enumerate(settings.manual[:settings.max_active], 1):
            slot = str(index)
            if slot in records or entry['field'] in used_fields or entry['planter'] in used_planters:
                continue
            row = next(r for r in data['rankings'][entry['field']] if r[0] == entry['planter'])
            nectar = next(n for n, fields in data['nectar_fields'].items() if entry['field'] in fields)
            seconds = interval_seconds(row, mode='interval', hours=entry['hours'])
            return Choice(slot, entry['field'], entry['planter'], nectar, seconds, round(seconds*row[1]/864, 1))
        return None
    slot = next(str(i) for i in range(1, 4) if str(i) not in records)
    projected = {n: percentages.get(n, 0)+sum(r['estimated_percent'] for r in records.values()
                 if r['nectar'] == n) for n in data['nectars']}
    # Fill deficits in user priority order, then maintain the lowest nectar.
    priorities = sorted(enumerate(settings.priorities), key=lambda p:
                        (projected[p[1]['nectar']] >= p[1]['minimum'],
                         p[0] if projected[p[1]['nectar']] < p[1]['minimum'] else projected[p[1]['nectar']]))
    for _, priority in priorities:
        nectar = priority['nectar']
        fields = [f for f in data['nectar_fields'][nectar]
                  if f in settings.allowed_fields and f not in used_fields]
        last = book.data['last_fields'].get(nectar)
        if last in fields:
            index = fields.index(last)+1
            fields = fields[index:]+fields[:index]
        for field in fields:
            for row in data['rankings'][field]:
                if row[0] not in settings.allowed_planters or row[0] in used_planters:
                    continue
                seconds = interval_seconds(row, projected[nectar], priority['minimum'], settings.harvest, settings.hours)
                if settings.harvest == 'interval':
                    remaining = [r['due']-book.state.clock() for r in records.values()
                                 if r['phase'] == 'growing' and r['due'] > book.state.clock()]
                    if remaining:
                        seconds = max(1, math.floor(min(seconds, *remaining)))
                return Choice(slot, field, row[0], nectar, seconds, round(seconds*row[1]/864, 1))
    return None


class Planters:
    def __init__(self, engine):
        self.e = engine
        self.settings = engine.config.planters
        self.book = PlanterBook(engine.state)
        self.inventory = Inventory(engine)

    def travel(self, field):
        self.e.reset()
        self.e.convert()
        self.e.motion.run('gtp-'+field)
        self.e.at_hive = False
        self.e.motion.wait(.5)
        self.e.check_disconnected()

    def errors(self, frame):
        h, w = frame.shape[:2]
        region = (max(0, w-500), max(0, h-400), min(500, w), min(400, h))
        for key, message in (('legacy/3planters', 'В игре уже установлены три плантатора'),
                             ('legacy/planteralready', 'На поле уже есть плантатор'),
                             ('legacy/standing', 'Персонаж должен стоять на поле')):
            if self.e.vision.has(frame, key, 30, region):
                return message
        return None

    def confirm(self, name='yes'):
        frame = self.e.frame(fresh=True)
        h, w = frame.shape[:2]
        region = (max(0, w//2-250), max(0, h//2-100), min(w, 500), min(h, 250))
        matches = self.e.vision.find(frame, 'general/'+name, 5, region)
        if not matches:
            return False
        m = matches[0]
        self.e.click(m.x+m.width/2, m.y+m.height/2)
        self.e.move_pointer(350, self.e.config.ui_offset+100)
        return self.e.wait_for(lambda f: not self.e.vision.has(f, 'general/'+name, 5, region), 5)

    def place(self, choice):
        e = self.e
        e.status('Плантатор: установка', choice.planter+' / '+choice.field)
        self.travel(choice.field)
        position = self.inventory.find(choice.planter, 'up')
        if position is None:
            self.inventory.open()
            raise DetectionError('Плантатор отсутствует в инвентаре: '+choice.planter)
        self.book.reserve(choice)
        try:
            frame = e.frame()
            e.drag(position, (frame.shape[1]/2, frame.shape[0]/2))
            if not e.wait_for(lambda f: e.has('general/yes', f, 5), 5) or not self.confirm():
                raise DetectionError('Установка плантатора не подтверждена')
            for _ in range(10):
                e.motion.wait(.15)
                frame = e.frame(fresh=True)
                e.check_disconnected(frame)
                error = self.errors(frame)
                if error:
                    self.book.remove(choice.slot)
                    raise DetectionError(error)
            self.book.growing(choice.slot)
        except BaseException:
            if choice.slot in self.book.records and self.book.records[choice.slot]['phase'] == 'placing':
                self.book.phase(choice.slot, 'uncertain')
            raise
        finally:
            e.input.release_all()
        self.inventory.open()
        e.state.increment('planters_placed')

    def find_prompt(self):
        e = self.e
        if e.has('general/e_button', tolerance=10, top=True):
            return True
        # Port of nm_searchForE's expanding square, checking every half tile.
        deadline = e.motion.now()+90
        for side in range(1, 9):
            for _ in range(2):
                for _ in range(6*side):
                    if e.motion.now() >= deadline:
                        return False
                    e.motion.move(.5, 'w')
                    if e.has('general/e_button', e.frame(fresh=True), 10, True):
                        e.motion.wait(.3)
                        return e.has('general/e_button', e.frame(fresh=True), 10, True)
                e.motion.press('.', 2)
        return False

    def harvest(self, slot):
        e = self.e
        row = self.book.records[slot]
        e.status('Плантатор: сбор', row['planter']+' / '+row['field'])
        self.travel(row['field'])
        if not self.find_prompt():
            # Reusable planters in inventory prove that an old record is stale.
            if row['planter'] not in CONSUMABLE and self.inventory.find(row['planter'], 'up'):
                self.book.remove(slot)
                self.inventory.open()
                return
            self.inventory.open()
            raise DetectionError('Плантатор не найден на поле, запись сохранена')
        self.book.phase(slot, 'harvesting')
        try:
            e.motion.press('e')
            e.motion.wait(.5)
            if self.settings.harvest == 'full' and self.settings.mode == 'auto' and e.has('general/no', tolerance=5):
                if not self.confirm('no'):
                    raise DetectionError('Не удалось отменить сбор незрелого плантатора')
                row.update(phase='growing', due=e.state.clock()+600)
                e.state.save()
                return
            if e.has('general/yes', tolerance=5) and not self.confirm():
                raise DetectionError('Не удалось подтвердить сбор плантатора')
            if not e.wait_for(lambda f: not e.has('general/e_button', f, 10, True)
                              and not e.has('general/yes', f, 5), 5):
                raise DetectionError('Плантатор не подтвердил сбор')
            e.motion.wait(1)
            if self.settings.gather_loot:
                e.motion.move(7, 's', 'd')
                for _ in range(5):
                    for distance, key in ((9, 'w'), (1.5, 'a'), (9, 's'), (1.5, 'a')):
                        e.motion.move(distance, key)
            if row['planter'] not in CONSUMABLE:
                if self.inventory.find(row['planter'], 'up') is None:
                    raise DetectionError('Возврат плантатора в инвентарь не подтверждён')
                self.inventory.open()
            self.book.remove(slot, harvested=True)
        except BaseException:
            if slot in self.book.records and row['phase'] == 'harvesting':
                self.book.phase(slot, 'uncertain')
            raise
        finally:
            e.input.release_all()

    def tick(self):
        if self.settings.mode == 'off':
            return
        e = self.e
        task = 'planters'
        if not e.state.due(task, 0):
            return
        try:
            for slot, row in list(self.book.records.items()):
                if row['phase'] == 'growing' and row['due'] <= e.state.clock():
                    self.harvest(slot)
                elif row['phase'] == 'uncertain':
                    e.status('Плантатор: нужна сверка', row['planter']+' / '+row['field']+', слот '+slot)
            for _ in range(3):
                frame = e.frame(fresh=True)
                percentages = {n: nectar_percent(frame, n, e.config.ui_offset) for n in NECTAR_COLORS}
                choice = choose(self.settings, self.book, percentages)
                if choice is None:
                    break
                self.place(choice)
        except DetectionError as exc:
            from .engine import Disconnected
            if isinstance(exc, Disconnected):
                raise
            e.state.failed(task)
            e.status('Плантатор: повтор позже', str(exc))
