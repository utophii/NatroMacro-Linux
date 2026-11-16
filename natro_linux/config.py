from __future__ import annotations
from dataclasses import asdict, dataclass, field
import json
import math
import os
from pathlib import Path
import tempfile

DATA = Path(__file__).parent / 'data'
FIELDS = ('sunflower', 'dandelion', 'mushroom', 'blueflower', 'clover', 'spider', 'bamboo', 'strawberry', 'pineapple', 'stump', 'cactus', 'pumpkin', 'pinetree', 'rose', 'mountaintop', 'coconut', 'pepper')
PATTERNS = ('Auryn', 'CornerXSnake', 'Diamonds', 'Fork', 'Lines', 'Slimline', 'Snake', 'Squares', 'Stationary', 'SuperCat', 'XSnake', 'e_lol')


def config_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'natro-linux/config.json'


def state_path():
    return Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'natro-linux/state.json'


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def number(name, value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name}: expected {low}..{high}, got {value!r}')


@dataclass
class Field:
    name: str = 'pinetree'
    pattern: str = 'Snake'
    minutes: float = 10
    size: float = 1
    repetitions: int = 3
    invert_fb: bool = False
    invert_lr: bool = False
    rotate: int = 0
    return_method: str = 'reset'
    backpack_limit: float = 95
    sprinkler_key: str = '1'
    sprinkler_count: int = 1

    def validate(self):
        if self.name not in FIELDS or self.pattern not in PATTERNS:
            raise ValueError('Unknown field or pattern')
        number('minutes', self.minutes, 0.1, 1440)
        number('size', self.size, 0.1, 4)
        number('repetitions', self.repetitions, 1, 100)
        number('rotate', self.rotate, -8, 8)
        number('backpack_limit', self.backpack_limit, 1, 100)
        number('sprinkler_count', self.sprinkler_count, 0, 4)
        for key in ('repetitions', 'rotate', 'sprinkler_count'):
            if not isinstance(getattr(self, key), int):
                raise ValueError(f'{key} must be integer')
        if self.return_method not in ('walk', 'reset'):
            raise ValueError('return_method must be walk or reset')
        if self.sprinkler_key not in tuple('1234567890') + ('',):
            raise ValueError('Invalid sprinkler hotbar key')


@dataclass
class PlanterSettings:
    mode: str = 'off'
    manual: list[dict] = field(default_factory=list)
    allowed_planters: list[str] = field(default_factory=list)
    allowed_fields: list[str] = field(default_factory=lambda: list(FIELDS))
    priorities: list[dict] = field(default_factory=lambda: [
        {'nectar': name, 'minimum': 70} for name in
        ('Comforting', 'Refreshing', 'Satisfying', 'Motivating', 'Invigorating')])
    max_active: int = 3
    harvest: str = 'auto'
    hours: float = 2
    gather_loot: bool = True

    def validate(self):
        from .game_data import game_data
        data = game_data()
        if self.mode not in ('off', 'manual', 'auto') or self.harvest not in ('auto', 'full', 'interval'):
            raise ValueError('Unknown planter mode or harvest mode')
        if type(self.max_active) is not int or not 1 <= self.max_active <= 3:
            raise ValueError('max_active must be 1..3')
        number('planter hours', self.hours, .25, 48)
        if type(self.gather_loot) is not bool:
            raise ValueError('gather_loot must be boolean')
        for name, values, known in (('allowed_planters', self.allowed_planters, data['planters']),
                                    ('allowed_fields', self.allowed_fields, FIELDS)):
            if not isinstance(values, list) or any(v not in known for v in values) or len(set(values)) != len(values):
                raise ValueError(f'Invalid {name}')
        if not isinstance(self.manual, list) or len(self.manual) > 3:
            raise ValueError('Use at most three manual planters')
        for row in self.manual:
            if not isinstance(row, dict) or set(row) != {'field', 'planter', 'hours'}:
                raise ValueError('Manual planter needs field, planter, hours')
            if row['field'] not in FIELDS or row['planter'] not in data['planters']:
                raise ValueError('Unknown manual planter or field')
            number('manual planter hours', row['hours'], .25, 48)
        for key in ('field', 'planter'):
            if len({r[key] for r in self.manual}) != len(self.manual):
                raise ValueError(f'Manual planter {key} must be unique')
        if not isinstance(self.priorities, list) or len(self.priorities) > 5:
            raise ValueError('Use at most five nectar priorities')
        for row in self.priorities:
            if not isinstance(row, dict) or set(row) != {'nectar', 'minimum'} or row['nectar'] not in data['nectars']:
                raise ValueError('Priority needs nectar and minimum')
            number('nectar minimum', row['minimum'], 0, 100)
        if len({r['nectar'] for r in self.priorities}) != len(self.priorities):
            raise ValueError('Duplicate nectar priority')
        if self.mode == 'manual' and not self.manual:
            raise ValueError('Configure at least one manual planter')
        if self.mode == 'auto' and (not self.allowed_planters or not self.allowed_fields or not self.priorities):
            raise ValueError('Select owned planters, allowed fields and nectar priorities')


@dataclass
class Config:
    schema: int = 1
    app_id: str = 'org.vinegarhq.Sober'
    window_id: int | None = None
    hive_slot: int = 1
    hive_bees: int = 25
    base_speed: float = 28
    speed_correction: float = 1
    hasty_guard: bool = False
    gifted_hasty: bool = False
    dynamic_speed: bool = True
    move_method: str = 'Walk'
    key_delay_ms: float = 50
    capture_scale: float = 1
    ui_offset: int = 0
    convert_timeout: float = 300
    balloon_seconds: float = 0
    fields: list[Field] = field(default_factory=lambda: [Field()])
    # Region in captured window pixels, calibrated through the preview.
    backpack_region: list[int] | None = None
    collections: list[str] = field(default_factory=list)
    hotbar: list[dict] = field(default_factory=list)
    memory_matches: list[str] = field(default_factory=list)
    memory_match_ignore: list[str] = field(default_factory=list)
    planters: PlanterSettings = field(default_factory=PlanterSettings)
    server_url: str = ''
    reconnect: bool = False

    def validate(self):
        if self.schema != 1:
            raise ValueError('Unsupported config schema')
        if not isinstance(self.app_id, str) or not self.app_id:
            raise ValueError('app_id must be a non-empty string')
        number('hive_slot', self.hive_slot, 1, 6)
        number('hive_bees', self.hive_bees, 1, 50)
        number('base_speed', self.base_speed, 1, 200)
        number('speed_correction', self.speed_correction, .1, 5)
        number('key_delay_ms', self.key_delay_ms, 1, 1000)
        number('capture_scale', self.capture_scale, .5, 4)
        number('ui_offset', self.ui_offset, 0, 200)
        number('convert_timeout', self.convert_timeout, 10, 1800)
        number('balloon_seconds', self.balloon_seconds, 0, 1800)
        for key in ('hive_slot', 'hive_bees', 'ui_offset'):
            if not isinstance(getattr(self, key), int):
                raise ValueError(f'{key} must be integer')
        if self.window_id is not None and (type(self.window_id) is not int or self.window_id < 0):
            raise ValueError('window_id must be a non-negative integer')
        if self.move_method not in ('Walk', 'Cannon'):
            raise ValueError('move_method must be Walk or Cannon')
        if not self.fields or len(self.fields) > 3:
            raise ValueError('Choose one to three fields')
        for item in self.fields:
            item.validate()
        if self.backpack_region is not None:
            if len(self.backpack_region) != 4 or any(type(x) is not int or x < 0 for x in self.backpack_region) or min(self.backpack_region[2:]) == 0:
                raise ValueError('Invalid backpack region')
        for item in self.hotbar:
            if set(item) != {'key', 'seconds'} or item['key'] not in '1234567890' or len(item['key']) != 1:
                raise ValueError('Hotbar entries need key and seconds')
            number('hotbar seconds', item['seconds'], 1, 86400)
        if len(set(self.collections)) != len(self.collections):
            raise ValueError('Duplicate collection')
        if not isinstance(self.memory_matches, list) or any(g not in ('Normal', 'Mega', 'Night', 'Extreme', 'Winter') for g in self.memory_matches):
            raise ValueError('Unknown Memory Match game')
        if not isinstance(self.memory_match_ignore, list) or any(not isinstance(x, str) for x in self.memory_match_ignore):
            raise ValueError('memory_match_ignore must be a list of item names')
        if self.reconnect:
            validate_server_url(self.server_url)
        self.planters.validate()

    def save(self, path=None):
        self.validate()
        atomic_json(Path(path) if path else config_path(), asdict(self))

    @classmethod
    def load(cls, path=None):
        path = Path(path) if path else config_path()
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        data['fields'] = [Field(**f) for f in data.get('fields', [asdict(Field())])]
        data['planters'] = PlanterSettings(**data.get('planters', {}))
        config = cls(**data)
        config.validate()
        return config


def validate_server_url(url):
    from urllib.parse import urlparse, parse_qs
    p = urlparse(url)
    query = parse_qs(p.query)
    if p.scheme == 'roblox' and query.get('placeId', query.get('placeid')) == ['1537690962']:
        return
    if p.scheme == 'https' and p.hostname in ('roblox.com', 'www.roblox.com') and p.path.startswith('/games/1537690962'):
        return
    raise ValueError('Use a Bee Swarm Simulator URL with place ID 1537690962')
