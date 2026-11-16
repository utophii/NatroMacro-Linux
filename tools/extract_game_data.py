#!/usr/bin/env python3
"""Extract literal planter and quest tables from the pinned upstream source."""
import ast
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'upstream/NatroMacro/submacros/natro_macro.ahk'


def literal(text, name, mapping=False):
    pattern = rf'^\s*{re.escape(name)}\s*:=\s*' + (r'Map\(' if mapping else r'\[')
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise ValueError(f'Missing upstream literal: {name}')
    opening, closing = ('(', ')') if mapping else ('[', ']')
    start = match.end() - 1
    depth, quoted, comment = 0, False, False
    output = []
    for char in text[start:]:
        if comment:
            if char == '\n':
                comment = False
                output.append(char)
            continue
        if char == '"':
            quoted = not quoted
        if not quoted and char == chr(59):
            comment = True
            continue
        if not quoted:
            if char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
        output.append(char)
        if not depth:
            value = ast.literal_eval('[' + ''.join(output)[1:-1] + ']')
            if mapping:
                if len(value) % 2 or len(set(value[::2])) != len(value) // 2:
                    raise ValueError(f'Invalid or duplicate map entries: {name}')
                return dict(zip(value[::2], value[1::2]))
            return value
    raise ValueError(f'Unterminated literal: {name}')


def extract(text):
    nectars = literal(text, 'nectarnames')
    planters = literal(text, 'planternames')
    fields = literal(text, 'fieldnames')
    nectar_fields = {n: [f.lower().replace(' ', '') for f in literal(text, n+'Fields')] for n in nectars}
    names = re.findall(r'^([A-Za-z]+)Planters\s*:=\s*\[', text, re.MULTILINE)
    tables = {n.lower(): literal(text, n+'Planters') for n in names}
    if set(tables) != set(fields):
        raise ValueError('Missing or unexpected field planter table')
    for rows in tables.values():
        if len(rows) != len(planters) or {r[0] for r in rows} != set(planters):
            raise ValueError('Incomplete planter rankings')
        if any(len(r) != 4 or any(type(v) not in (int, float) or v <= 0 for v in r[1:]) for r in rows):
            raise ValueError('Invalid planter coefficients')
    quests = {giver: literal(text, giver, mapping=True) for giver in ('PolarBear', 'BlackBear', 'BuckoBee', 'RileyBee')}
    for rows in quests.values():
        for objectives in rows.values():
            if any(len(row) != 3 or type(row[0]) is not int or row[0] < 1 or row[1] not in ('Collect', 'Kill', 'Get', 'Feed') for row in objectives):
                raise ValueError('Unknown quest objective')
    return {'nectars': nectars, 'planters': planters, 'nectar_fields': nectar_fields,
            'rankings': tables, 'quests': quests}


def main():
    data = extract(SOURCE.read_text(encoding='utf-8-sig'))
    data['source'] = str(SOURCE.relative_to(ROOT / 'upstream/NatroMacro'))
    data['source_sha256'] = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    data['upstream_commit'] = json.loads((ROOT / 'upstream/manifest.json').read_text())['sha']
    (ROOT / 'natro_linux/data/game.json').write_text(json.dumps(data, indent=2) + '\n')
    count = sum(len(q) for q in data['quests'].values())
    print(f'Extracted {len(data["rankings"])} field rankings, {len(data["planters"])} planters, {count} quests')


if __name__ == '__main__':
    main()
