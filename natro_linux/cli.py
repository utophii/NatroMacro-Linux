from __future__ import annotations
import argparse
from dataclasses import asdict
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import sys
from .config import Config, DATA, config_path
from .motion import Control, Motion
from .platform import Niri, NativeInput, TraceInput, PlatformError, GeometryUnavailable


def doctor(config, check_input=False):
    checks = []
    def check(name, fn):
        try:
            result = fn()
            checks.append({'name': name, 'ok': True, 'detail': result})
            return result
        except Exception as exc:
            checks.append({'name': name, 'ok': False, 'detail': str(exc)})
    check('Wayland session', lambda: os.environ['WAYLAND_DISPLAY'])
    for binary in ('niri', 'grim', 'flatpak'):
        check(binary, lambda b=binary: shutil.which(b) or (_ for _ in ()).throw(RuntimeError('Not installed')))
    for module in ('PySide6', 'numpy'):
        check(module, lambda m=module: bool(importlib.util.find_spec(m)) or (_ for _ in ()).throw(RuntimeError('Not installed')))
    check('Input helper', lambda: str(next((Path(__file__).parent/'bin').glob('natro-input'))))
    niri = Niri()
    window = check('Sober window', lambda: {k: v for k, v in niri.select(config.app_id, config.window_id).items() if k in ('id','app_id','is_focused','layout','workspace_id')})
    if window:
        def geometry_info():
            try:
                return asdict(niri.geometry(window))
            except GeometryUnavailable:
                return 'Coordinates absent in Niri IPC, direct window capture will be used'
        check('Window geometry', geometry_info)
    if check_input:
        def probe():
            # Create and immediately destroy virtual devices without sending keys.
            backend = NativeInput()
            backend.close()
            return 'Virtual keyboard and pointer available'
        check('Wayland input protocols', probe)
    return checks


def parser():
    p = argparse.ArgumentParser(prog='natro-linux', description='Natro Macro native Linux port, development build')
    p.add_argument('--config', type=Path, default=config_path())
    sub = p.add_subparsers(dest='command')
    sub.add_parser('ui')
    sub.add_parser('init')
    d = sub.add_parser('doctor')
    d.add_argument('--check-input', action='store_true')
    sub.add_parser('run')
    for name in ('stop','pause','resume','status'):
        sub.add_parser(name)
    sub.add_parser('list')
    t = sub.add_parser('trace')
    t.add_argument('name')
    t.add_argument('--pattern', action='store_true')
    t.add_argument('--events', action='store_true')
    snapshot = sub.add_parser('snapshot')
    snapshot.add_argument('path', type=Path)
    snapshot.add_argument('--focus', action='store_true', help='Focus Sober before capturing')
    inspect = sub.add_parser('inspect', help='Recognize a saved BSS screenshot without game input')
    inspect.add_argument('path', type=Path)
    sub.add_parser('planters', help='Show saved planter records')
    reconcile = sub.add_parser('planter-reconcile', help='Correct a planter record after checking it in game, close the app first')
    reconcile.add_argument('slot', choices=('1', '2', '3'))
    resolution = reconcile.add_mutually_exclusive_group(required=True)
    resolution.add_argument('--collected', action='store_true')
    resolution.add_argument('--remaining-hours', type=float)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in ('stop','pause','resume','status'):
            from .control import request
            print(json.dumps(request(args.command), ensure_ascii=False, indent=2))
            return 0
        config = Config.load(args.config)
        if args.command == 'init':
            if args.config.exists():
                raise ValueError('Config already exists, refusing to overwrite it')
            config.save(args.config)
            print(args.config)
        elif args.command == 'doctor':
            checks = doctor(config, args.check_input)
            print(json.dumps(checks, ensure_ascii=False, indent=2))
            return 0 if all(c['ok'] for c in checks) else 1
        elif args.command == 'list':
            print(json.dumps(json.loads((DATA/'movements.json').read_text()), indent=2))
        elif args.command == 'trace':
            backend = TraceInput()
            motion = Motion(backend, config)
            motion.run(args.name, 'patterns' if args.pattern else 'routes', config.fields[0] if args.pattern else None)
            result = {'name': args.name, 'seconds_at_configured_speed': round(backend.time, 3), 'events': backend.events if args.events else len(backend.events), 'held_keys': sorted(backend.held), 'note': 'Trace only, cannon detection and game state are not simulated'}
            print(json.dumps(result, indent=2))
        elif args.command == 'planters':
            from .scheduler import State
            print(json.dumps(State().data.get('planters', {}), ensure_ascii=False, indent=2))
        elif args.command == 'planter-reconcile':
            from .control import acquire_instance_lock
            from .scheduler import State
            from .planters import PlanterBook
            with acquire_instance_lock():
                book = PlanterBook(State())
                book.reconcile(args.slot, args.remaining_hours)
                print(json.dumps(book.data, ensure_ascii=False, indent=2))
        elif args.command == 'inspect':
            from PySide6.QtGui import QImage
            from .vision import Vision, rgba
            from .quests import visible_quests
            from .planters import NECTAR_COLORS, nectar_percent
            image = QImage(str(args.path))
            if image.isNull():
                raise ValueError('Could not decode screenshot')
            frame = rgba(image)
            vision = Vision()
            offset = vision.hud_offset(frame)
            result = {'bss_recognized': offset is not None, 'ui_offset': offset,
                      'width': image.width(), 'height': image.height()}
            if offset is not None:
                result.update(backpack_percent=vision.backpack(frame, config.backpack_region, offset),
                              buffs=asdict(vision.buffs(frame, offset)),
                              nectars={n: nectar_percent(frame, n, offset) for n in NECTAR_COLORS},
                              visible_quests=[q.as_dict() for q in visible_quests(frame, vision, offset)])
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if offset is not None else 1
        elif args.command == 'snapshot':
            import time
            from PySide6.QtGui import QImage
            from .platform import Capture
            niri = Niri()
            window = niri.select(config.app_id, config.window_id)
            if args.focus:
                niri.focus(window['id'])
                time.sleep(.4)
            capture = Capture(niri, window['id'], config.capture_scale)
            pixels = capture.frame()
            image = QImage(pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0], QImage.Format.Format_RGBA8888)
            if not image.save(str(args.path)):
                raise ValueError('Could not save screenshot')
            print(f'{args.path} (capture: {capture.backend})')
        elif args.command == 'run':
            from .control import ControlServer
            from .engine import Engine
            control = Control()
            status = {'action': 'Starting'}
            def emit(event):
                status.update(event)
                print(json.dumps(event, ensure_ascii=False), flush=True)
            def handle(action):
                if action != 'status':
                    getattr(control, action)()
                return status
            server = ControlServer(handle)
            signal.signal(signal.SIGINT, lambda *_: control.stop())
            signal.signal(signal.SIGTERM, lambda *_: control.stop())
            try:
                Engine(config, control, emit).run()
            finally:
                server.close()
        else:
            from .ui import run_ui
            return run_ui(args.config)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f'natro-linux: {exc}', file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
