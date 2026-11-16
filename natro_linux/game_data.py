"""Tables extracted from the pinned NatroMacro source, see tools/extract_game_data.py."""
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def game_data():
    return json.loads((Path(__file__).parent / 'data/game.json').read_text())
