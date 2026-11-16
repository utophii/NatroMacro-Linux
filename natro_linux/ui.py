from __future__ import annotations
from dataclasses import asdict
import json
from pathlib import Path
import signal
import sys
import time
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QRect, QPoint
from PySide6.QtGui import QImage, QPainter, QColor, QPixmap
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QTabWidget, QLabel, QPushButton, QComboBox, QDoubleSpinBox, QSpinBox,
    QCheckBox, QPlainTextEdit, QMessageBox, QFileDialog, QScrollArea, QGroupBox, QSplitter)
from .config import Config, Field, PlanterSettings, FIELDS, PATTERNS
from .engine import Engine
from .motion import Control
from .scheduler import COLLECTIONS
from .control import ControlServer


class Worker(QThread):
    event = Signal(dict)
    error = Signal(str)

    def __init__(self, config, control, parent=None):
        super().__init__(parent)
        self.config, self.control = config, control

    def run(self):
        try:
            Engine(self.config, self.control, self.event.emit).run()
        except Exception as exc:
            self.error.emit(str(exc))


class Probe(QThread):
    result = Signal(object)
    error = Signal(str)

    def __init__(self, config, mode='doctor', parent=None):
        super().__init__(parent)
        self.config, self.mode = config, mode

    def run(self):
        try:
            if self.mode == 'doctor':
                from .cli import doctor
                self.result.emit(doctor(self.config, True))
            else:
                from .platform import Niri, Capture
                niri = Niri()
                window = niri.select(self.config.app_id, self.config.window_id)
                niri.focus(window['id'])
                time.sleep(.5)
                pixels = Capture(niri, window['id'], self.config.capture_scale).frame()
                image = QImage(pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0], QImage.Format.Format_RGBA8888).copy()
                self.result.emit(image)
        except Exception as exc:
            self.error.emit(str(exc))


class ImageSelection(QWidget):
    selected = Signal(list)

    def __init__(self):
        super().__init__()
        self.image = QImage()
        self.start = None
        self.selection = QRect()
        self.setMinimumSize(480, 270)

    def set_image(self, image):
        self.image = image
        self.selection = QRect()
        self.update()

    def image_rect(self):
        if self.image.isNull():
            return QRect()
        size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRect((self.width()-size.width())//2, (self.height()-size.height())//2, size.width(), size.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#171b21'))
        if self.image.isNull():
            painter.setPen(QColor('#b5c0cf'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'Захвати окно Sober или открой снимок')
            return
        painter.drawImage(self.image_rect(), self.image)
        if not self.selection.isNull():
            painter.setPen(QColor('#ffcd57'))
            painter.drawRect(self.selection)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.image_rect().contains(event.position().toPoint()):
            self.start = event.position().toPoint()
            self.selection = QRect(self.start, self.start)

    def mouseMoveEvent(self, event):
        if self.start is not None:
            self.selection = QRect(self.start, event.position().toPoint()).normalized().intersected(self.image_rect())
            self.update()

    def mouseReleaseEvent(self, event):
        if self.start is None:
            return
        self.mouseMoveEvent(event)
        self.start = None
        box = self.image_rect()
        rect = self.selection
        if rect.width() < 2 or rect.height() < 2:
            return
        sx, sy = self.image.width()/box.width(), self.image.height()/box.height()
        self.selected.emit([round((rect.x()-box.x())*sx), round((rect.y()-box.y())*sy), round(rect.width()*sx), round(rect.height()*sy)])


def spin(value, lo, hi, decimals=0):
    w = QDoubleSpinBox() if decimals else QSpinBox()
    if decimals:
        w.setDecimals(decimals)
    w.setRange(lo, hi)
    w.setValue(value)
    return w


def combo(items, current):
    w = QComboBox()
    w.addItems(items)
    w.setCurrentText(current)
    return w


class FieldEditor(QGroupBox):
    def __init__(self, value, number):
        super().__init__(f'Поле {number}')
        self.setCheckable(True)
        self.setChecked(value is not None)
        value = value or Field()
        form = QFormLayout(self)
        self.name = combo(FIELDS, value.name)
        self.pattern = combo(PATTERNS, value.pattern)
        self.minutes = spin(value.minutes, .1, 1440, 1)
        self.size = spin(value.size, .1, 4, 2)
        self.repetitions = spin(value.repetitions, 1, 100)
        self.backpack = spin(value.backpack_limit, 1, 100)
        self.rotate = spin(value.rotate, -8, 8)
        self.return_method = combo(['reset','walk'], value.return_method)
        self.sprinkler_key = combo([''] + list('1234567890'), value.sprinkler_key)
        self.sprinkler_count = spin(value.sprinkler_count, 0, 4)
        self.invert_fb = QCheckBox('Вперёд / назад')
        self.invert_fb.setChecked(value.invert_fb)
        self.invert_lr = QCheckBox('Влево / вправо')
        self.invert_lr.setChecked(value.invert_lr)
        for label, widget in [('Поле', self.name), ('Паттерн', self.pattern), ('Минуты', self.minutes), ('Размер', self.size), ('Повторы', self.repetitions), ('Порог рюкзака, %', self.backpack), ('Поворот камеры', self.rotate), ('Возврат', self.return_method), ('Клавиша спринклера', self.sprinkler_key), ('Количество', self.sprinkler_count), ('Инверсия', self.invert_fb), ('', self.invert_lr)]:
            form.addRow(label, widget)

    def value(self):
        return Field(name=self.name.currentText(), pattern=self.pattern.currentText(), minutes=self.minutes.value(), size=self.size.value(), repetitions=self.repetitions.value(), backpack_limit=self.backpack.value(), rotate=self.rotate.value(), return_method=self.return_method.currentText(), sprinkler_key=self.sprinkler_key.currentText(), sprinkler_count=self.sprinkler_count.value(), invert_fb=self.invert_fb.isChecked(), invert_lr=self.invert_lr.isChecked())


class MainWindow(QMainWindow):
    def __init__(self, path, with_server=True):
        super().__init__()
        self.path = Path(path)
        self.config = Config.load(path)
        self.worker = None
        self.probe = None
        self.control = Control()
        self.last_event = {'action': 'Готово'}
        self.server = ControlServer(self.remote) if with_server else None
        self.closing = False
        self.setWindowTitle('Natro Linux • разработка')
        self.resize(1120, 820)
        self.root = QWidget()
        self.setCentralWidget(self.root)
        outer = QVBoxLayout(self.root)
        header = QHBoxLayout()
        title = QLabel('Natro Linux')
        title.setStyleSheet('font-size: 26px; font-weight: 650')
        header.addWidget(title)
        header.addStretch()
        self.status_label = QLabel('Остановлено')
        header.addWidget(self.status_label)
        outer.addLayout(header)
        notice = QLabel('Полный перенос в работе. Игровые проверки ещё не завершены. Список готовых и недоступных функций находится во вкладке «Перенос».')
        notice.setWordWrap(True)
        outer.addWidget(notice)
        buttons = QHBoxLayout()
        self.start_button = QPushButton('Запустить')
        self.pause_button = QPushButton('Пауза')
        self.stop_button = QPushButton('Остановить')
        self.save_button = QPushButton('Сохранить настройки')
        for button in (self.start_button, self.pause_button, self.stop_button, self.save_button):
            buttons.addWidget(button)
        self.start_button.clicked.connect(self.start)
        self.pause_button.clicked.connect(self.pause)
        self.stop_button.clicked.connect(lambda: self.control.stop())
        self.save_button.clicked.connect(self.save)
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        outer.addLayout(buttons)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        self.field_page()
        self.settings_page()
        self.collections_page()
        from .planter_ui import PlanterEditor
        self.planter_editor = PlanterEditor(self.config.planters)
        planter_scroll = QScrollArea()
        planter_scroll.setWidgetResizable(True)
        planter_scroll.setWidget(self.planter_editor)
        self.tabs.addTab(planter_scroll, 'Плантаторы')
        self.calibration_page()
        self.diagnostics_page()
        self.progress_page()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1000)
        self.tabs.addTab(self.log, 'Журнал')
        self.stats_label = QLabel('Циклы: 0 • сбор: 0 мин • конвертация: 0 мин')
        outer.addWidget(self.stats_label)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(100)

    def page(self, title):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.tabs.addTab(page, title)
        return page, layout

    def field_page(self):
        page, layout = self.page('Поля')
        row = QHBoxLayout()
        self.editors = []
        for i in range(3):
            value = self.config.fields[i] if i < len(self.config.fields) else None
            editor = FieldEditor(value, i+1)
            row.addWidget(editor)
            self.editors.append(editor)
        layout.addLayout(row)
        note = QLabel('Рюкзак распознаётся по цвету индикатора BSS. В калибровке можно задать область полосы вручную. Номер улья должен совпадать с занятым ульем в игре.')
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()

    def settings_page(self):
        page, layout = self.page('Настройки')
        form = QFormLayout()
        self.hive = spin(self.config.hive_slot, 1, 6)
        self.bees = spin(self.config.hive_bees, 1, 50)
        self.speed = spin(self.config.base_speed, 1, 200, 2)
        self.correction = spin(self.config.speed_correction, .1, 5, 3)
        self.method = combo(['Walk','Cannon'], self.config.move_method)
        self.scale = spin(self.config.capture_scale, .5, 4, 2)
        self.offset = spin(self.config.ui_offset, 0, 200)
        self.dynamic = QCheckBox('Учитывать баффы скорости по изображению')
        self.dynamic.setChecked(self.config.dynamic_speed)
        self.hasty = QCheckBox('Hasty Guard')
        self.hasty.setChecked(self.config.hasty_guard)
        self.gifted = QCheckBox('Gifted Hasty Bee')
        self.gifted.setChecked(self.config.gifted_hasty)
        for label, widget in [('Номер улья', self.hive), ('Пчёл', self.bees), ('Базовая скорость', self.speed), ('Поправка времени ходьбы', self.correction), ('Перемещение', self.method), ('Масштаб захвата', self.scale), ('Смещение UI по Y', self.offset), ('', self.dynamic), ('', self.hasty), ('', self.gifted)]:
            form.addRow(label, widget)
        layout.addLayout(form)
        layout.addWidget(QLabel('Горячая панель, формат JSON: [{"key": "2", "seconds": 600}]'))
        self.hotbar = QPlainTextEdit(json.dumps(self.config.hotbar, indent=2))
        self.hotbar.setMaximumHeight(100)
        layout.addWidget(self.hotbar)
        layout.addWidget(QLabel('Глобальные клавиши настраиваются в Niri. Пример: packaging/niri.kdl'))
        layout.addStretch()

    def collections_page(self):
        page, layout = self.page('Предметы')
        self.collection_checks = {}
        for name, task in COLLECTIONS.items():
            check = QCheckBox(f'{task.title}, каждые {task.seconds/3600:g} ч' + (' • только Cannon' if task.cannon_only else ''))
            check.setChecked(name in self.config.collections)
            self.collection_checks[name] = check
            layout.addWidget(check)
        layout.addWidget(QLabel('Memory Match'))
        self.memory_checks = {}
        for game in ('Normal', 'Mega', 'Night', 'Extreme', 'Winter'):
            check = QCheckBox(game)
            check.setChecked(game in self.config.memory_matches)
            self.memory_checks[game] = check
            layout.addWidget(check)
        layout.addWidget(QLabel('Задания и противники пока не подключены к игровому циклу.'))
        layout.addStretch()

    def calibration_page(self):
        page, layout = self.page('Калибровка')
        row = QHBoxLayout()
        grab = QPushButton('Захватить Sober')
        load = QPushButton('Открыть PNG')
        export = QPushButton('Сохранить PNG')
        clear = QPushButton('Убрать область рюкзака')
        for button in (grab, load, export, clear):
            row.addWidget(button)
        layout.addLayout(row)
        grab.clicked.connect(lambda: self.launch_probe('capture'))
        load.clicked.connect(self.open_image)
        export.clicked.connect(self.save_image)
        clear.clicked.connect(lambda: self.select_backpack(None))
        layout.addWidget(QLabel('Выдели внутреннюю область полосы рюкзака без текста и рамки. Координаты сохраняются относительно окна игры.'))
        self.image = ImageSelection()
        self.image.selected.connect(self.select_backpack)
        layout.addWidget(self.image, 1)
        self.region_label = QLabel(str(self.config.backpack_region or 'Область не выбрана'))
        layout.addWidget(self.region_label)

    def diagnostics_page(self):
        page, layout = self.page('Диагностика')
        button = QPushButton('Проверить окружение без игровых нажатий')
        button.clicked.connect(lambda: self.launch_probe('doctor'))
        layout.addWidget(button)
        self.diagnostics = QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        layout.addWidget(self.diagnostics)

    def progress_page(self):
        page, layout = self.page('Перенос')
        text = QPlainTextEdit()
        text.setReadOnly(True)
        status = Path(__file__).parent / 'data/status.json'
        if status.exists():
            data = json.loads(status.read_text())
            text.setPlainText('\n\n'.join(f'{entry["feature"]}\n{entry["status"]}' for entry in data))
        else:
            text.setPlainText('См. docs/STATUS.md. Полное соответствие NatroMacro ещё не достигнуто.')
        layout.addWidget(text)

    def collect_config(self):
        self.config.fields = [e.value() for e in self.editors if e.isChecked()]
        self.config.hive_slot, self.config.hive_bees = self.hive.value(), self.bees.value()
        self.config.base_speed, self.config.speed_correction = self.speed.value(), self.correction.value()
        self.config.move_method, self.config.capture_scale = self.method.currentText(), self.scale.value()
        self.config.ui_offset = self.offset.value()
        self.config.dynamic_speed = self.dynamic.isChecked()
        self.config.hasty_guard, self.config.gifted_hasty = self.hasty.isChecked(), self.gifted.isChecked()
        self.config.collections = [k for k, w in self.collection_checks.items() if w.isChecked()]
        self.config.memory_matches = [k for k, w in self.memory_checks.items() if w.isChecked()]
        self.config.planters = self.planter_editor.value()
        self.config.hotbar = json.loads(self.hotbar.toPlainText())
        self.config.validate()
        # The worker receives a separate snapshot, so editing UI cannot change it.
        data = asdict(self.config)
        data['fields'] = [Field(**f) for f in data['fields']]
        data['planters'] = PlanterSettings(**data['planters'])
        return Config(**data)

    def save(self):
        try:
            self.collect_config().save(self.path)
            self.status_label.setText('Настройки сохранены')
        except Exception as exc:
            self.show_error(str(exc))

    def start(self):
        if self.worker and self.worker.isRunning():
            return
        if self.probe and self.probe.isRunning():
            self.show_error('Дождись завершения диагностики')
            return
        try:
            config = self.collect_config()
            config.save(self.path)
            self.control = Control()
            self.worker = Worker(config, self.control, self)
            self.worker.event.connect(self.on_event)
            self.worker.error.connect(self.show_error)
            self.worker.finished.connect(self.finished)
            self.worker.start()
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.pause_button.setEnabled(True)
        except Exception as exc:
            self.show_error(str(exc))

    def pause(self):
        if self.control.paused.is_set():
            self.control.resume()
        else:
            self.control.pause()

    def remote(self, action):
        if action != 'status':
            getattr(self.control, action)()
        return dict(self.last_event)

    def refresh(self):
        self.pause_button.setText('Продолжить' if self.control.paused.is_set() else 'Пауза')
        if self.closing and not (self.worker and self.worker.isRunning()) and not (self.probe and self.probe.isRunning()):
            self.close()

    def finished(self):
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.status_label.setText('Остановлено')

    def on_event(self, event):
        self.last_event = event
        self.status_label.setText(event['action'])
        self.log.appendPlainText(f'{time.strftime("%H:%M:%S")}  {event["action"]}  {event.get("detail", "")}')
        stats = event.get('stats', {})
        self.stats_label.setText(f'Циклы: {stats.get("cycles", 0)} • сбор: {stats.get("gather_seconds", 0)/60:.1f} мин • конвертация: {stats.get("convert_seconds", 0)/60:.1f} мин')

    def show_error(self, message):
        self.log.appendPlainText('Ошибка: ' + message)
        self.status_label.setText('Ошибка')
        if not self.closing:
            QMessageBox.warning(self, 'Natro Linux', message)

    def launch_probe(self, mode):
        if self.worker and self.worker.isRunning():
            self.show_error('Останови макрос перед диагностикой')
            return
        if self.probe and self.probe.isRunning():
            return
        try:
            self.probe = Probe(self.collect_config(), mode, self)
            self.probe.result.connect(self.probe_result)
            self.probe.error.connect(self.show_error)
            self.probe.start()
        except Exception as exc:
            self.show_error(str(exc))

    def probe_result(self, result):
        if isinstance(result, QImage):
            self.image.set_image(result)
            self.showNormal()
            self.raise_()
            self.activateWindow()
        else:
            self.diagnostics.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))

    def select_backpack(self, region):
        self.config.backpack_region = region
        self.region_label.setText(str(region or 'Область не выбрана'))

    def open_image(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Снимок игры', '', 'Images (*.png *.ppm *.jpg)')
        if path:
            image = QImage(path)
            if image.isNull():
                self.show_error('Не удалось открыть изображение')
            else:
                self.image.set_image(image)

    def save_image(self):
        if self.image.image.isNull():
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Сохранить снимок', 'sober.png', 'PNG (*.png)')
        if path and not self.image.image.save(path):
            self.show_error('Не удалось сохранить изображение')

    def closeEvent(self, event):
        self.control.stop()
        if (self.worker and self.worker.isRunning()) or (self.probe and self.probe.isRunning()):
            self.closing = True
            self.status_label.setText('Остановка…')
            event.ignore()
            return
        if self.server:
            self.server.close()
            self.server = None
        event.accept()


def run_ui(path):
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyle('Fusion')
    window = MainWindow(path)
    signal.signal(signal.SIGINT, lambda *_: window.close())
    signal.signal(signal.SIGTERM, lambda *_: window.close())
    window.show()
    return app.exec()
