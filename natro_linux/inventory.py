"""Native ports of nm_OpenMenu and nm_InventorySearch."""
from __future__ import annotations
from .engine import DetectionError


class Inventory:
    TABS = {'itemmenu':30, 'questlog':85, 'beemenu':140, 'badgelist':195, 'settingsmenu':250, 'shopmenu':305}

    def __init__(self, engine):
        self.engine = engine

    def active(self, frame):
        offset = self.engine.config.ui_offset
        region = (0, offset+72, min(350, frame.shape[1]), 80)
        for name in self.TABS:
            if self.engine.vision.has(frame, 'general/'+name, 4, region):
                return name
        return None

    def open(self, tab=None):
        if tab is not None and tab not in self.TABS:
            raise ValueError('Unknown game tab')
        e = self.engine
        for _ in range(10):
            active = self.active(e.frame(fresh=True))
            if active == tab:
                return
            target = active or tab
            e.click(self.TABS[target], e.config.ui_offset+120)
            e.move_pointer(350, e.config.ui_offset+100)
            e.motion.wait(.5)
        raise DetectionError(f'Не удалось переключить вкладку игры: {tab}')

    def find(self, name, direction='down', max_scrolls=70):
        e = self.engine
        key = 'inventory/' + name.lower().replace(' ', '')
        if key not in e.vision.templates.index:
            raise ValueError(f'Нет шаблона предмета {name}')
        self.open('itemmenu')
        step = 1 if direction == 'down' else -1
        e.move_pointer(30, e.config.ui_offset+200)
        # First inspect the visible area, then return to the start and scan.
        for attempt in range(max_scrolls):
            frame = e.frame(fresh=True)
            start = e.config.ui_offset+150
            height = frame.shape[0]-start
            if height <= 80:
                raise DetectionError('Недостаточно места для инвентаря')
            region = (0, start, min(306, frame.shape[1]), height)
            found = e.vision.find(frame, key, 10, region)
            if found:
                # Upstream templates identify the item label, icon is at x=30.
                return (30, found[0].y+40)
            e.move_pointer(30, e.config.ui_offset+200)
            if attempt == 0:
                for _ in range(10):
                    e.input.scroll(-step*10)
                    e.motion.wait(.05)
            else:
                e.input.scroll(step)
            e.motion.wait(.5)
        return None

    def use(self, name, confirm=False):
        e = self.engine
        position = self.find(name)
        if position is None:
            self.open()
            raise DetectionError(f'Предмет не найден: {name}')
        frame = e.frame()
        e.drag(position, (frame.shape[1]/2, frame.shape[0]/2))
        e.motion.wait(.3)
        if confirm:
            if not e.wait_for(lambda f: e.has('general/yes', f, 5), 5):
                raise DetectionError(f'Подтверждение использования {name} не найдено')
            match = e.vision.find(e.frame(fresh=True), 'general/yes', 5)[0]
            e.click(match.x+match.width/2, match.y+match.height/2)
            if not e.wait_for(lambda f: not e.has('general/yes', f, 5), 5):
                raise DetectionError('Диалог подтверждения не закрылся')
        self.open()
