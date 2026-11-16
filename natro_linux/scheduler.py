from __future__ import annotations
from dataclasses import dataclass
import json
import time
from pathlib import Path
from .config import atomic_json, state_path


@dataclass(frozen=True)
class Collection:
    name: str
    title: str
    seconds: int
    route: str
    settle: float = .5
    cannon_only: bool = False


# Cooldowns and routes come from the corresponding upstream nm_* functions.
COLLECTIONS = {
    c.name: c for c in (
        Collection('clock', 'Wealth Clock', 3600, 'gtc-clock'),
        Collection('honeydis', 'Honey Dispenser', 3600, 'gtc-honeydis'),
        Collection('treatdis', 'Treat Dispenser', 3600, 'gtc-treatdis'),
        Collection('blueberrydis', 'Blueberry Dispenser', 14400, 'gtc-blueberrydis'),
        Collection('strawberrydis', 'Strawberry Dispenser', 14400, 'gtc-strawberrydis'),
        Collection('coconutdis', 'Coconut Dispenser', 14400, 'gtc-coconutdis'),
        Collection('royaljellydis', 'Royal Jelly Dispenser', 79200, 'gtc-royaljellydis', 10, True),
    )
}


class State:
    def __init__(self, path=None, clock=time.time):
        self.path = Path(path) if path else state_path()
        self.clock = clock
        self.data = {'schema': 1, 'completed': {}, 'retry_after': {}, 'stats': {}}
        if self.path.exists():
            data = json.loads(self.path.read_text())
            if data.get('schema') != 1 or not all(isinstance(data.get(k), dict) for k in ('completed', 'retry_after', 'stats')):
                raise ValueError('Invalid persistent state file')
            self.data = data

    def due(self, name, seconds):
        now = self.clock()
        return now >= self.data['retry_after'].get(name, 0) and now - self.data['completed'].get(name, 0) >= seconds

    def success(self, name):
        self.data['completed'][name] = self.clock()
        self.data['retry_after'].pop(name, None)
        self.increment('collections', save=False)
        self.save()

    def failed(self, name, backoff=300):
        self.data['retry_after'][name] = self.clock() + backoff
        self.increment('collection_failures', save=False)
        self.save()

    def increment(self, name, amount=1, save=True):
        stats = self.data['stats']
        stats[name] = stats.get(name, 0) + amount
        if save:
            self.save()

    def save(self):
        atomic_json(self.path, self.data)
