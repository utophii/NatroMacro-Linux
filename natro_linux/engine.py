from __future__ import annotations
from collections import deque
from dataclasses import asdict
import time
from .config import Config, validate_server_url, atomic_json
from .motion import Motion, Control, Cancelled, RestartCycle
from .platform import Niri, NativeInput, Capture, PlatformError, FocusLost, command
from .scheduler import COLLECTIONS, State
from .vision import Vision


class DetectionError(RuntimeError):
    pass


class Disconnected(DetectionError):
    pass


class Engine:
    def __init__(self, config: Config, control=None, emit=None, state=None):
        config.validate()
        unknown = set(config.collections) - COLLECTIONS.keys()
        if unknown:
            raise ValueError(f'Collection controllers not ported: {sorted(unknown)}')
        self.config = config
        self.control = control or Control()
        self.emit = emit or (lambda event: None)
        self.state = state or State()
        self.niri = Niri()
        self.vision = Vision()
        self.input = None
        self.motion = None
        self.capture = None
        self.window_id = None
        self.at_hive = False
        self.camera_down = False
        self.hotbar_last = {}
        self.frame_time = -1.
        self.cached_frame = None
        self.shape = None
        self.output = None
        self.buff_sampler = None

    def status(self, action, detail=''):
        self.emit({'time': time.time(), 'action': action, 'detail': detail, 'stats': dict(self.state.data['stats'])})

    def save_diagnostic_frame(self, frame, filename):
        if frame is None:
            return None
        from PySide6.QtGui import QImage
        path = self.state.path.parent / filename
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            image = QImage(frame.data, frame.shape[1], frame.shape[0], frame.strides[0], QImage.Format.Format_RGBA8888)
            return path if image.save(str(path)) else None
        except Exception:
            # Optional diagnostics must never replace the original failure.
            return None

    def report_failure(self, error):
        if self.input:
            try:
                self.input.release_all()
            except Exception:
                pass
        # Use the last captured game frame, never refocus or send input to obtain it.
        path = self.save_diagnostic_frame(self.cached_frame, 'last-error.png')
        age = max(0, time.monotonic()-self.frame_time) if self.cached_frame is not None else None
        try:
            atomic_json(self.state.path.parent / 'last-error.json', {
                'time': time.time(), 'error': str(error), 'window_id': self.window_id,
                'frame': str(path) if path else None, 'frame_age_seconds': age,
            })
        except Exception:
            pass
        detail = str(error)
        if path:
            detail += f' Последний кадр: {path}, давность {age:.1f} с.'
        self.status('Ошибка', detail)

    def preflight_frame(self):
        # Focusing a scrolling Niri workspace can briefly expose an unfinished
        # game frame. Retry before creating any input device.
        for attempt in range(3):
            frame = self.capture.frame()
            offset = self.vision.hud_offset(frame)
            if offset is not None:
                return frame, offset
            if attempt < 2 and self.control.stopped.wait(.3):
                raise Cancelled()
        path = self.save_diagnostic_frame(frame, 'unrecognized-bss.png')
        detail = f' Кадр для диагностики: {path}' if path else ''
        raise DetectionError('Интерфейс Bee Swarm Simulator не распознан. Игровые нажатия не отправлены.'+detail)

    def connect(self):
        self.status('Подключение', 'Sober / Niri')
        window = self.niri.select(self.config.app_id, self.config.window_id)
        self.window_id = window['id']
        self.niri.focus(self.window_id)
        if self.control.stopped.wait(.4):
            raise Cancelled()
        window = self.niri.require_focus(self.window_id)
        self.output, _ = self.niri.output(window)
        self.capture = Capture(self.niri, self.window_id, self.config.capture_scale)
        initial_frame, detected_offset = self.preflight_frame()
        self.config.ui_offset = detected_offset
        self.status('Распознано', f'BSS, смещение UI {detected_offset}, захват {self.capture.backend}')
        self.input = NativeInput(self.output)
        self.motion = Motion(self.input, self.config, self.control, lambda: self.niri.require_focus(self.window_id))
        self.motion.cannon_handler = self.goto_cannon
        if self.config.dynamic_speed:
            from .buff_sampler import BuffSampler
            # Separate capture state prevents concurrent geometry/cache mutation.
            buff_capture = Capture(self.niri, self.window_id, self.config.capture_scale)
            buff_vision = Vision()
            offset, shape, output = detected_offset, initial_frame.shape, self.output
            def read_buffs():
                pixels = buff_capture.frame()
                if pixels.shape != shape or buff_capture.output != output:
                    raise DetectionError('Окно изменилось при чтении баффов. Перезапусти макрос.')
                return buff_vision.buffs(pixels, offset)
            initial_buffs = buff_vision.buffs(initial_frame, offset)
            self.motion.buffs = initial_buffs
            self.buff_sampler = BuffSampler(read_buffs, initial_buffs)
            reported = asdict(initial_buffs)
            def current_buffs():
                nonlocal reported
                value = self.buff_sampler.get()
                values = asdict(value)
                if values != reported:
                    reported = values
                    active = ', '.join(f'{k}={v}' for k, v in values.items() if v) or 'нет'
                    self.status('Скорость', f'Расчётная {value.speed(self.config):g}, баффы: {active}')
                return value
            self.motion.buff_provider = current_buffs
            self.status('Скорость', f'Базовая {self.config.base_speed:g}, с распознанными баффами {initial_buffs.speed(self.config):g}')
        self.shape = None
        self.cached_frame = initial_frame
        self.frame_time = time.monotonic()
        self.shape = initial_frame.shape
        frame = initial_frame
        if frame.shape[0] < 400 or frame.shape[1] < 600:
            raise DetectionError('Окно игры слишком маленькое для шаблонов Natro')

    def frame(self, fresh=False):
        self.motion.checkpoint()
        if fresh or time.monotonic() - self.frame_time > .2:
            frame = self.capture.frame()
            if self.shape is not None and frame.shape != self.shape:
                raise DetectionError('Размер окна изменился. Перезапусти макрос после настройки окна.')
            if self.capture.output != self.output:
                raise DetectionError('Окно перемещено на другой монитор. Перезапусти макрос.')
            self.shape = frame.shape
            self.cached_frame = frame
            self.frame_time = time.monotonic()
        return self.cached_frame

    def top(self, frame):
        h, w = frame.shape[:2]
        return (max(0, w//2-200), self.config.ui_offset, min(400, w), min(160, h-self.config.ui_offset))

    def has(self, name, frame=None, tolerance=8, top=False):
        frame = self.frame() if frame is None else frame
        return self.vision.has(frame, name, tolerance, self.top(frame) if top else None)

    def wait_for(self, predicate, timeout, interval=.2):
        deadline = self.motion.now() + timeout
        while self.motion.now() < deadline:
            if predicate(self.frame(fresh=True)):
                return True
            self.motion.wait(interval)
        return False

    def check_disconnected(self, frame=None):
        if self.has('general/disconnected', frame, tolerance=15):
            raise Disconnected('Roblox disconnected')

    def move_pointer(self, x, y):
        self.motion.checkpoint()
        geo = self.capture.locate()
        local_x = geo.x - geo.output_x + x/self.config.capture_scale
        local_y = geo.y - geo.output_y + y/self.config.capture_scale
        self.input.absolute(local_x, local_y, geo.output_width, geo.output_height)

    def click(self, x, y):
        self.move_pointer(x, y)
        self.motion.press('lbutton')

    def drag(self, start, end):
        self.move_pointer(*start)
        self.motion.key('lbutton', True)
        try:
            for step in range(1, 21):
                self.move_pointer(*(start[i]+(end[i]-start[i])*step/20 for i in (0, 1)))
                self.motion.wait(.025)
        finally:
            self.input.key('lbutton', False)

    def dismiss_dialogs(self):
        from .inventory import Inventory
        Inventory(self).open()
        for _ in range(20):
            frame = self.frame(fresh=True)
            match = self.vision.find(frame, 'general/dialog', 10)
            if not match:
                break
            m = match[0]
            self.click(m.x + m.width/2, max(0, m.y-15))
            self.motion.wait(.15)
        frame = self.frame(fresh=True)
        for name in ('general/no', 'general/closegui'):
            found = self.vision.find(frame, name, 5)
            if found:
                m = found[0]
                self.click(m.x+m.width/2, m.y+m.height/2)
                self.motion.wait(.2)
        frame = self.frame(fresh=True)
        if self.has('general/shiftlock', frame, tolerance=8):
            self.motion.press('lshift')

    def reset(self):
        self.status('Возврат', 'Сброс персонажа и поиск улья')
        self.input.release_all()
        self.at_hive = False
        for attempt in range(3):
            self.check_disconnected()
            self.dismiss_dialogs()
            old_delay = self.motion.vars['a_keydelay']
            self.motion.set_key_delay(250 + self.config.key_delay_ms)
            try:
                self.motion.send('{esc}{r}{enter}')
            finally:
                self.motion.set_key_delay(old_delay)
            self.motion.wait(2)
            # Give the respawn animation time to finish, bounded to 12 seconds.
            self.wait_for(lambda f: not self.has('reset/emptyhealth', f), 12)
            self.motion.wait(1)
            for rotation in range(4):
                self.motion.wait(.25)
                frame = self.frame(fresh=True)
                self.check_disconnected(frame)
                if self.vision.hive(frame):
                    self.status('Улей', 'Ориентир камеры найден, проверка подсказки')
                    self.motion.press('.', 4)
                    if self.camera_down:
                        self.motion.press('pageup')
                        self.camera_down = False
                    self.motion.press('o', 5)
                    self.motion.wait(.5)
                    if self.confirm_hive():
                        self.at_hive = True
                        self.state.increment('resets')
                        return
                self.motion.press('.', 4)
                if rotation == 1:
                    self.motion.press('pageup' if self.camera_down else 'pagedown')
                    self.camera_down = not self.camera_down
            self.status('Повтор', f'Улей не распознан, попытка {attempt+1}/3')
        raise DetectionError('Не удалось подтвердить улей. Проверь номер улья, масштаб интерфейса и шаблоны в диагностике.')

    def confirm_hive(self):
        frame = self.frame(fresh=True)
        return self.hive_prompt(frame)

    def hive_prompt(self, frame):
        return (self.vision.empty_hive(frame, self.config.ui_offset)
                or self.has('convert/makehoney', frame, 8, True)
                or self.has('convert/collectpollen', frame, 8, True))

    def goto_cannon(self):
        self.status('Маршрут', 'Красная пушка')
        for _ in range(3):
            self.motion.send('{space down}{d down}')
            self.motion.sleep_ms(100)
            self.motion.send('{space up}')
            self.motion.move(2, 'd')
            self.motion.move(1.5, 'w', 'd')
            self.motion.key('d', True)
            try:
                found = self.wait_for(lambda f: self.has('general/redcannon', f, 8, True), 10, .05)
            finally:
                self.input.key('d', False)
            if found:
                for _ in range(10):
                    self.motion.wait(.5)
                    if self.has('general/redcannon', self.frame(fresh=True), 8, True):
                        return
                    self.motion.move(1.5, 'a')
            self.reset()
            self.motion.goto_ramp()
        raise DetectionError('Пушка не распознана после трёх попыток')

    def find_hive_slot(self):
        if not self.confirm_hive():
            self.motion.key('a', True)
            try:
                found = self.wait_for(self.hive_prompt, 15, .05)
            finally:
                self.input.key('a', False)
            if not found:
                return False
        for _ in range(10):
            self.motion.wait(.5)
            if self.confirm_hive():
                return True
            self.motion.move(1.5, 'd')
        return False

    def convert(self):
        self.status('Конвертация', 'Рюкзак')
        self.input.release_all()
        if not self.confirm_hive():
            raise DetectionError('Конвертация требует подтверждённого улья')
        if self.vision.empty_hive(self.frame(fresh=True), self.config.ui_offset):
            self.status('Конвертация', 'Рюкзак уже пуст')
            return
        started = self.motion.now()
        if self.has('convert/makehoney', tolerance=8, top=True):
            self.motion.press('e')
        self.motion.wait(1)
        deadline = started + self.config.convert_timeout
        empty_samples = 0
        while self.motion.now() < deadline:
            frame = self.frame(fresh=True)
            self.check_disconnected(frame)
            if self.vision.empty_hive(frame, self.config.ui_offset):
                break
            if not self.has('general/e_button', frame, 15, True):
                raise DetectionError('Персонаж ушёл от улья во время конвертации')
            pack = self.vision.backpack(frame, self.config.backpack_region, self.config.ui_offset)
            empty = pack is not None and pack <= 1
            # After conversion Roblox changes the prompt back to Make Honey.
            empty = empty or self.has('convert/makehoney', frame, 8, True)
            empty_samples = empty_samples + 1 if empty else 0
            if empty_samples >= 3:
                break
            self.motion.wait(.5)
        else:
            raise DetectionError('Истекло время конвертации. Проверь изображение игры.')
        if self.config.balloon_seconds:
            self.status('Конвертация', 'Шар, заданное время')
            self.motion.wait(self.config.balloon_seconds)
        self.state.increment('convert_seconds', round(self.motion.now()-started, 3))

    def sprinklers(self, field):
        if not field.sprinkler_key or not field.sprinkler_count:
            return
        self.status('Спринклеры', field.name)
        # Natro uses roughly 4.5 tiles between silver/gold/diamond placements.
        moves = [('w', 4.5), ('a', 4.5), ('s', 4.5)]
        applied = []
        for index in range(field.sprinkler_count):
            delay = 200
            for _ in range(3):
                self.motion.key('space', True)
                self.motion.sleep_ms(delay)
                self.motion.press(field.sprinkler_key)
                self.motion.key('space', False)
                self.motion.wait(.5)
                frame = self.frame(fresh=True)
                if self.has('sprinkler/standing', frame, 20):
                    delay = max(100, delay-50)
                elif self.has('sprinkler/thisclose', frame, 20):
                    delay = min(500, delay+50)
                else:
                    break
            else:
                raise DetectionError('Не удалось поставить спринклер')
            if index < field.sprinkler_count-1:
                key, distance = moves[index]
                self.motion.move(distance, key)
                applied.append((key, distance))
        opposite = {'w':'s', 'a':'d', 's':'w'}
        for key, distance in reversed(applied):
            self.motion.move(distance, opposite[key])

    def drift(self):
        deadline = self.motion.now() + 6
        try:
            while self.motion.now() < deadline:
                frame = self.frame(fresh=True)
                h, w = frame.shape[:2]
                matches = self.vision.find(frame, 'fdc/saturator', 30, limit=25)
                matches += self.vision.find(frame, 'fdc/saturatorws', 30, limit=25)
                if not matches:
                    return
                target = min(matches, key=lambda m: (m.x+m.width/2-w/2)**2 + (m.y+m.height/2-h/2)**2)
                x, y = target.x + target.width/2, target.y + target.height/2
                horizontal = 'a' if x < w/2.14 else 'd' if x > w/1.88 else None
                vertical = 'w' if y < h/2.14 else 's' if y > h/1.88 else None
                for key in ('w', 'a', 's', 'd'):
                    self.motion.key(key, key in (horizontal, vertical))
                if not horizontal and not vertical:
                    return
                self.motion.wait(.04)
        finally:
            for key in ('w', 'a', 's', 'd'):
                self.input.key(key, False)

    def use_hotbar(self):
        now = self.motion.now()
        for item in self.config.hotbar:
            key = item['key']
            if now - self.hotbar_last.get(key, -1e12) >= item['seconds']:
                self.motion.press(key)
                self.hotbar_last[key] = self.motion.now()

    def gather(self, field):
        self.status('Маршрут', field.name)
        self.at_hive = False
        self.motion.run('gtf-' + field.name)
        self.motion.wait(.5)
        self.sprinklers(field)
        if field.rotate:
            self.motion.press('.' if field.rotate > 0 else ',', abs(field.rotate))
        self.status('Сбор', field.name)
        start = self.motion.now()
        packs = deque(maxlen=6)
        self.motion.key('lbutton', True)
        try:
            while self.motion.now()-start < field.minutes*60:
                frame = self.frame(fresh=True)
                self.check_disconnected(frame)
                pack = self.vision.backpack(frame, self.config.backpack_region, self.config.ui_offset)
                if pack is not None:
                    packs.append(pack)
                    if len(packs) >= 3 and sum(packs)/len(packs) >= field.backpack_limit:
                        break
                self.use_hotbar()
                self.motion.run(field.pattern, 'patterns', field)
                self.drift()
        finally:
            self.input.release_all()
            self.state.increment('gather_seconds', round(self.motion.now()-start, 3))
        if field.rotate:
            self.motion.press(',' if field.rotate > 0 else '.', abs(field.rotate))
        if field.return_method == 'walk':
            self.status('Возврат', field.name)
            self.motion.run('wf-' + field.name)
            if not self.find_hive_slot():
                self.reset()
            else:
                self.at_hive = True
        else:
            self.reset()
        self.convert()
        self.state.increment('cycles')

    def collect(self, collection):
        if collection.cannon_only and self.config.move_method != 'Cannon':
            return
        for attempt in range(2):
            self.reset()
            self.convert()
            self.status('Предметы', collection.title)
            self.motion.run(collection.route)
            self.motion.wait(.5)
            self.check_disconnected()
            if not self.has('general/e_button', tolerance=30, top=True):
                continue
            self.motion.press('e')
            self.motion.wait(collection.settle)
            self.state.success(collection.name)
            self.status('Собрано', collection.title)
            return
        self.state.failed(collection.name)
        self.status('Не удалось собрать', collection.title + ', повтор через 5 минут')

    def reconnect(self):
        if not self.config.reconnect:
            raise Disconnected('Переподключение выключено')
        validate_server_url(self.config.server_url)
        self.status('Переподключение', 'Sober')
        if self.buff_sampler:
            self.buff_sampler.close()
            self.buff_sampler = None
        if self.input:
            self.input.close()
            self.input = None
        # Sober exposes its Roblox URI handler through Flatpak.
        import subprocess
        subprocess.Popen(['flatpak', 'run', 'org.vinegarhq.Sober', '--', self.config.server_url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(60):
            if self.control.stopped.wait(1):
                raise Cancelled()
            if self.niri.windows(self.config.app_id):
                self.connect()
                self.motion.wait(5)
                if not self.has('general/disconnected') and not self.has('reconnect/loading'):
                    return
                self.input.close()
                self.input = None
        raise Disconnected('Не удалось переподключиться за 60 секунд')

    def run_memory_matches(self):
        from .memory_match import MemoryMatch, GAMES
        for game in self.config.memory_matches:
            task = 'memorymatch-'+game
            if not self.state.due(task, GAMES[game][1]):
                continue
            if game == 'Night':
                frame = self.frame(fresh=True)
                if not any(self.has(name, frame, 5) for name in self.vision.templates.group('night/confirm_night/')):
                    continue
            try:
                MemoryMatch(self, game, self.config.memory_match_ignore).run()
            except (Cancelled, RestartCycle, Disconnected, FocusLost):
                raise
            except DetectionError as exc:
                self.state.failed(task)
                raise DetectionError(f'{game} Memory Match: {exc}. Выполнение остановлено, проверь открытую мини-игру.') from exc

    def run(self):
        try:
            self.connect()
            from .planters import Planters
            planters = Planters(self)
            index = 0
            while not self.control.stopped.is_set():
                try:
                    for name in self.config.collections:
                        task = COLLECTIONS[name]
                        if self.state.due(name, task.seconds):
                            self.collect(task)
                    self.run_memory_matches()
                    planters.tick()
                    self.reset()
                    self.convert()
                    self.gather(self.config.fields[index % len(self.config.fields)])
                    index += 1
                except RestartCycle:
                    self.status('Возобновление', 'Повтор цикла от улья')
                except Disconnected:
                    self.reconnect()
        except Cancelled:
            self.status('Остановлено')
        except Exception as exc:
            self.report_failure(exc)
            raise
        finally:
            try:
                if self.input:
                    self.input.close()
            finally:
                if self.buff_sampler:
                    self.buff_sampler.close()
                    self.buff_sampler = None
                self.state.save()
