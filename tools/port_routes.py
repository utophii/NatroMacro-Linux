#!/usr/bin/env python3
"""Translate the fixed upstream movement language into reviewable Python.

This is a build tool, not a general purpose AutoHotkey interpreter.
Unknown statements and expressions are errors. The resulting application
runs Python only and does not evaluate downloaded source at runtime.
"""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r'''(?P<space>[ \t\r]+)|(?P<comment>\;[^\n]*)|(?P<nl>\n)|(?P<string>"(?:`.|[^"\n])*"|'(?:`.|[^'\n])*')|(?P<number>\d+(?:\.\d+)?)|(?P<id>[A-Za-z_]\w*)|(?P<op>:=|\*\*|//|>=|<=|!=|==|&&|\|\||[+*/<>=!(),{}\[\]:%-])''')

class PortError(ValueError):
    pass

class Compiler:
    def __init__(self, source: str):
        self.tokens = []
        at = 0
        while at < len(source):
            m = TOKEN.match(source, at)
            if not m:
                raise PortError(f"Unknown token near {source[at:at+80]!r}")
            at = m.end()
            if m.lastgroup not in ('space', 'comment'):
                self.tokens.append((m.lastgroup, m.group()))
        self.tokens.append(('eof', 'EOF'))
        self.i = 0
        self.lines = []
        self.depth = 1
        self.loop_id = 0

    def peek(self, value=None):
        t = self.tokens[self.i]
        return t[1].lower() == value if value is not None else t

    def pop(self, expected=None):
        token = self.tokens[self.i]
        if expected is not None and token[1].lower() != expected:
            raise PortError(f"Expected {expected}, got {token}")
        self.i += 1
        return token

    def skip_nl(self):
        while self.peek('\n'):
            self.pop()

    def emit(self, text):
        self.lines.append('    ' * self.depth + text)

    def expr(self, minimum=0):
        kind, value = self.pop()
        if kind == 'number':
            left = value
        elif kind == 'string':
            left = repr(value[1:-1].replace('`n', '\n').replace('`t', '\t').replace('`"', '"').replace('``', '`'))
        elif value == '(':
            left = self.expr()
            self.pop(')')
        elif value == '[':
            items = self.arguments(']')
            left = '[' + ', '.join(items) + ']'
        elif value in ('-', '+', '!'):
            left = '(' + ('not ' if value == '!' else value) + self.expr(80) + ')'
        elif kind == 'id':
            name = value.lower()
            if self.peek('('):
                self.pop()
                args = self.arguments(')')
                if name not in ('sqrt', 'max', 'min', 'abs', 'floor', 'ceil', 'mod'):
                    raise PortError(f"Unknown expression function {name}")
                name = {'sqrt': 'math.sqrt', 'floor': 'math.floor', 'ceil': 'math.ceil', 'mod': 'ctx.mod'}.get(name, name)
                left = name + '(' + ', '.join(args) + ')'
            elif name in ('true', 'false'):
                left = str(name == 'true')
            else:
                left = f"v[{name!r}]"
        else:
            raise PortError(f"Unexpected expression token {kind} {value}")
        precedence = {'||': 10, '&&': 20, '=': 30, '==': 30, '!=': 30, '>': 30, '<': 30, '>=': 30, '<=': 30, '+': 50, '-': 50, '*': 60, '/': 60, '//': 60, '%': 60, '**': 70}
        while True:
            kind, op = self.peek()
            implicit = kind in ('string', 'id', 'number') or op in ('(', '[')
            if op.lower() in ('else', 'in'):
                implicit = False
            p = 40 if implicit else precedence.get(op, -1)
            if p < minimum:
                break
            if not implicit:
                self.pop()
            right = self.expr(p if op == '**' else p + 1)
            if implicit:
                left = f"(str({left}) + str({right}))"
            elif op == '=':
                left = f"ctx.eq({left}, {right})"
            else:
                left = f"({left} { {'&&': 'and', '||': 'or'}.get(op, op)} {right})"
        return left

    def arguments(self, closing):
        args = []
        if not self.peek(closing):
            while True:
                args.append(self.expr())
                if not self.peek(','):
                    break
                self.pop()
        self.pop(closing)
        return args

    def body(self):
        self.skip_nl()
        self.depth += 1
        before = len(self.lines)
        if self.peek('{'):
            self.pop()
            self.skip_nl()
            while not self.peek('}'):
                if self.peek('eof') or self.peek()[0] == 'eof':
                    raise PortError('Unclosed block')
                self.statement()
                self.skip_nl()
            self.pop('}')
        else:
            self.statement()
        if len(self.lines) == before:
            self.emit('pass')
        self.depth -= 1

    def statement(self):
        self.skip_nl()
        name = self.pop()[1].lower()
        if name == 'if':
            condition = self.expr()
            self.emit(f'if {condition}:')
            self.body()
            self.skip_nl()
            if self.peek('else'):
                self.pop()
                self.emit('else:')
                self.body()
        elif name == 'loop':
            count = self.expr()
            self.loop_id += 1
            index = self.loop_id
            self.emit(f'_saved_index_{index} = v.get("a_index", 0)')
            self.emit(f'for _index_{index} in range(1, ctx.loop_count({count}) + 1):')
            self.depth += 1
            self.emit(f'v["a_index"] = _index_{index}')
            self.depth -= 1
            self.body()
            self.emit(f'v["a_index"] = _saved_index_{index}')
        elif name == 'switch':
            expression = self.expr()
            self.skip_nl()
            self.pop('{')
            self.skip_nl()
            first = True
            while not self.peek('}'):
                label = self.pop()[1].lower()
                if label == 'case':
                    condition = self.expr()
                    self.emit(f'{"if" if first else "elif"} ctx.eq({expression}, {condition}):')
                elif label == 'default':
                    self.emit('else:' if not first else 'if True:')
                else:
                    raise PortError(f'Unknown switch label {label}')
                first = False
                self.pop(':')
                self.skip_nl()
                self.depth += 1
                while not any(self.peek(k) for k in ('case', 'default', '}')):
                    self.statement()
                    self.skip_nl()
                self.depth -= 1
            self.pop('}')
        elif self.peek(':='):
            self.pop()
            self.emit(f'v[{name!r}] = {self.expr()}')
        else:
            methods = {'send': 'send', 'sendinput': 'send', 'walk': 'walk', 'nm_walk': 'move', 'sleep': 'sleep_ms', 'hypersleep': 'sleep_ms', 'nm_gotoramp': 'goto_ramp', 'nm_gotocannon': 'goto_cannon', 'jump': 'jump', 'setkeydelay': 'set_key_delay'}
            if name not in methods:
                raise PortError(f'Unsupported statement {name}')
            if self.peek('('):
                self.pop()
                args = self.arguments(')')
            else:
                args = [self.expr()]
            self.emit(f'ctx.{methods[name]}({", ".join(args)})')
            if self.peek(','):
                self.pop()
                self.statement()

    def compile(self):
        self.skip_nl()
        while self.peek()[0] != 'eof':
            self.statement()
            self.skip_nl()
        return 'import math\n\n\ndef run(ctx):\n    v = ctx.vars\n' + '\n'.join(self.lines) + '\n'


def main():
    manifest = []
    for source_dir, target in [('paths', 'routes'), ('patterns', 'patterns')]:
        out = ROOT / 'natro_linux' / target
        out.mkdir(parents=True, exist_ok=True)
        (out / '__init__.py').write_text('"""Movement ported from NatroTeam/NatroMacro under GPL-3.0."""\n')
        for path in sorted((ROOT / 'upstream/NatroMacro' / source_dir).glob('*.ahk')):
            source = path.read_text(encoding='utf-8-sig')
            adaptations = []
            if path.stem == 'gtc-nightmm':
                # This helper is reimplemented with a monotonic clock in Motion.jump.
                marker = 'Jump(movements*) {'
                body = source[source.index(marker):].split('\n}', 1)[0] + '\n}'
                expected = ['GetSystemTimeAsFileTime', 'Sleep Max(1400-', 'nm_Walk(params*)']
                if not all(x in body for x in expected):
                    raise PortError('Moon-jump helper changed, review the native port')
                source = source.replace(body, '')
                adaptations.append('Jump helper uses Motion.jump and monotonic time')
            if path.stem == 'CornerXSnake':
                if ' down}}"' not in source:
                    raise PortError('CornerXSnake adaptation no longer applies')
                source = source.replace(' down}}"', ' down}"')
                adaptations.append('Removed extra literal brace after TCLRKey down in upstream')
            try:
                code = Compiler(source).compile()
                compile(code, str(path), 'exec')
            except Exception as exc:
                raise PortError(f'{path.name}: {exc}') from exc
            name = path.stem.replace('-', '_').lower()
            header = f'# Derived from NatroTeam/NatroMacro {source_dir}/{path.name}\n# Copyright Natro Team and contributors, GPL-3.0-only.\n# Generated by tools/port_routes.py. Do not edit by hand.\n'
            (out / f'{name}.py').write_text(header + code)
            manifest.append({'name': path.stem, 'kind': target, 'source': f'{source_dir}/{path.name}', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'module': f'natro_linux.{target}.{name}', 'adaptations': adaptations})
    (ROOT / 'natro_linux/data/movements.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Ported {len(manifest)} movement programs')

if __name__ == '__main__':
    main()
