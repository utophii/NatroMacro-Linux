from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox, QLabel, QGridLayout)
from .config import PlanterSettings, FIELDS
from .game_data import game_data


def select(values, current):
    box = QComboBox()
    for key, label in values:
        box.addItem(label, key)
    box.setCurrentIndex(max(0, box.findData(current)))
    return box


def hours(value):
    box = QDoubleSpinBox()
    box.setRange(.25, 48)
    box.setDecimals(2)
    box.setValue(value)
    box.setSuffix(' ч')
    return box


class PlanterEditor(QWidget):
    def __init__(self, settings):
        super().__init__()
        data = game_data()
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.mode = select([('off', 'Выключены'), ('manual', 'Заданные поля'), ('auto', 'По нектару')], settings.mode)
        self.limit = QSpinBox()
        self.limit.setRange(1, 3)
        self.limit.setValue(settings.max_active)
        self.loot = QCheckBox('Собирать выпавшие предметы')
        self.loot.setChecked(settings.gather_loot)
        form.addRow('Режим', self.mode)
        form.addRow('Максимум одновременно', self.limit)
        form.addRow(self.loot)
        layout.addLayout(form)
        note = QLabel('Укажи только доступные тебе плантаторы. Таймеры сохраняются между запусками. '
                      'Уже установленные вручную плантаторы пока не импортируются автоматически.')
        note.setWordWrap(True)
        layout.addWidget(note)
        manual = QGroupBox('Заданные поля, повтор после сбора')
        ml = QVBoxLayout(manual)
        self.manual = []
        for i in range(3):
            value = settings.manual[i] if i < len(settings.manual) else {}
            row = QHBoxLayout()
            enabled = QCheckBox(str(i+1))
            enabled.setChecked(bool(value))
            planter = select([(p, p) for p in data['planters']], value.get('planter', 'PlasticPlanter'))
            field = select([(f, f) for f in FIELDS], value.get('field', FIELDS[i]))
            duration = hours(value.get('hours', 2))
            for widget in (enabled, planter, field, duration):
                row.addWidget(widget)
            ml.addLayout(row)
            self.manual.append((enabled, planter, field, duration))
        layout.addWidget(manual)
        automatic = QGroupBox('Выбор по нектару')
        al = QVBoxLayout(automatic)
        af = QFormLayout()
        self.harvest = select([('auto', 'По запасу нектара'), ('full', 'Полное созревание'), ('interval', 'Через интервал')], settings.harvest)
        self.duration = hours(settings.hours)
        af.addRow('Сбор', self.harvest)
        af.addRow('Интервал', self.duration)
        al.addLayout(af)
        al.addWidget(QLabel('Доступные плантаторы'))
        grid = QGridLayout()
        self.owned = {}
        for i, name in enumerate(data['planters']):
            check = QCheckBox(name)
            check.setChecked(name in settings.allowed_planters)
            grid.addWidget(check, i//3, i%3)
            self.owned[name] = check
        al.addLayout(grid)
        al.addWidget(QLabel('Разрешённые поля'))
        grid = QGridLayout()
        self.fields = {}
        for i, name in enumerate(FIELDS):
            check = QCheckBox(name)
            check.setChecked(name in settings.allowed_fields)
            grid.addWidget(check, i//4, i%4)
            self.fields[name] = check
        al.addLayout(grid)
        al.addWidget(QLabel('Приоритеты нектара, сверху вниз, и минимальный запас в процентах'))
        self.priorities = []
        for i in range(5):
            value = settings.priorities[i] if i < len(settings.priorities) else {'nectar': '', 'minimum': 70}
            row = QHBoxLayout()
            nectar = select([('', 'Не использовать')]+[(n, n) for n in data['nectars']], value['nectar'])
            minimum = QDoubleSpinBox()
            minimum.setRange(0, 100)
            minimum.setValue(value['minimum'])
            minimum.setSuffix(' %')
            row.addWidget(nectar)
            row.addWidget(minimum)
            al.addLayout(row)
            self.priorities.append((nectar, minimum))
        layout.addWidget(automatic)
        def refresh_mode():
            manual.setEnabled(self.mode.currentData() == 'manual')
            automatic.setEnabled(self.mode.currentData() == 'auto')
        self.mode.currentIndexChanged.connect(refresh_mode)
        refresh_mode()
        layout.addStretch()

    def value(self):
        settings = PlanterSettings(
            mode=self.mode.currentData(), max_active=self.limit.value(), gather_loot=self.loot.isChecked(),
            harvest=self.harvest.currentData(), hours=self.duration.value(),
            manual=[{'planter': p.currentData(), 'field': f.currentData(), 'hours': h.value()}
                    for enabled, p, f, h in self.manual if enabled.isChecked()],
            allowed_planters=[name for name, w in self.owned.items() if w.isChecked()],
            allowed_fields=[name for name, w in self.fields.items() if w.isChecked()],
            priorities=[{'nectar': n.currentData(), 'minimum': v.value()}
                        for n, v in self.priorities if n.currentData()],
        )
        settings.validate()
        return settings
