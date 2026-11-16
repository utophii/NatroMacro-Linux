#!/usr/bin/env python3
"""Fetch pinned upstream PNGs with visible progress and hard request deadlines."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 2 * 1024 * 1024


def blob_id(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


@dataclass
class Download:
    process: subprocess.Popen
    item: dict
    temporary: Path
    target: Path
    started: float


def fetch_one(url, destination):
    # The parent enforces the deadline even while DNS or TLS is blocked.
    try:
        with urlopen(Request(url, headers={'User-Agent': 'natro-linux-port'}), timeout=8) as response:
            data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError('Response exceeds the image size limit')
        Path(destination).write_bytes(data)
        return 0
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        return 1


def start_download(item, base_url, destination):
    target = destination / item['path']
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.natro-download-', dir=target.parent)
    os.close(fd)
    temporary = Path(temporary)
    url = base_url + '/' + quote(item['path'], safe='/')
    try:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), '--fetch-one', url, str(temporary)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return Download(process, item, temporary, target, time.monotonic())


def discard(download):
    if download.process.poll() is None:
        download.process.kill()
    download.process.wait()
    if download.process.stderr:
        download.process.stderr.close()
    download.temporary.unlink(missing_ok=True)


def download_all(files, base_url, destination, *, request_timeout=8, total_timeout=120,
                 workers=8, max_errors=8):
    pending = []
    verified = 0
    print(f'Checking {len(files)} local PNG files...', flush=True)
    for item in files:
        target = destination / item['path']
        if target.exists():
            if blob_id(target.read_bytes()) != item['sha']:
                print(f'Existing modified file: {target}', file=sys.stderr, flush=True)
                return 1
            verified += 1
        else:
            pending.append(item)
    print(f'Already verified {verified}/{len(files)}. Remaining {len(pending)}. '
          f'Hard deadline per request: {request_timeout:g} seconds.', flush=True)
    iterator = iter(pending)
    active = []
    errors = 0
    started = time.monotonic()
    last_progress = started
    exhausted = False
    try:
        while active or not exhausted:
            now = time.monotonic()
            if now - started >= total_timeout:
                print('Total download deadline reached. Re-run to resume.', file=sys.stderr, flush=True)
                break
            while len(active) < workers and not exhausted and errors < max_errors:
                item = next(iterator, None)
                if item is None:
                    exhausted = True
                else:
                    active.append(start_download(item, base_url, destination))
            for download in active[:]:
                expired = time.monotonic() - download.started >= request_timeout
                result = download.process.poll()
                if result is None and not expired:
                    continue
                try:
                    if result is None:
                        download.process.kill()
                    _, stderr = download.process.communicate()
                    if result is None:
                        raise TimeoutError(f'request exceeded {request_timeout:g} seconds (including DNS)')
                    if result:
                        raise RuntimeError(stderr.decode(errors='replace').strip() or f'worker exited {result}')
                    data = download.temporary.read_bytes()
                    if blob_id(data) != download.item['sha']:
                        raise ValueError('Git blob checksum mismatch')
                    # Publish only a complete, verified file. Interrupted downloads remain resumable.
                    download.temporary.replace(download.target)
                    verified += 1
                    print(f'[{verified}/{len(files)}] {download.item["path"]}', flush=True)
                except (OSError, ValueError, RuntimeError) as exc:
                    errors += 1
                    print(f'ERROR {download.item["path"]}: {exc}', file=sys.stderr, flush=True)
                finally:
                    discard(download)
                    active.remove(download)
            if errors >= max_errors:
                print('Stopping after repeated failures. Re-run to resume.', file=sys.stderr, flush=True)
                break
            now = time.monotonic()
            if active and now - last_progress >= 1:
                print(f'Waiting for {len(active)} requests, {verified}/{len(files)}, {now-started:.0f}s', flush=True)
                last_progress = now
            if active:
                time.sleep(0.05)
    except KeyboardInterrupt:
        print('Cancelled. Keeping verified files and stopping active requests.', flush=True)
        return 130
    finally:
        for download in active:
            discard(download)
    if verified != len(files):
        print(f'Incomplete: {verified}/{len(files)} verified. Re-run to resume.', file=sys.stderr, flush=True)
        return 1
    print(f'Verified all {len(files)} PNG files.', flush=True)
    return 0


def main():
    if len(sys.argv) == 4 and sys.argv[1] == '--fetch-one':
        return fetch_one(sys.argv[2], sys.argv[3])
    manifest = json.loads((ROOT / 'upstream/manifest.json').read_text())
    files = [f for f in manifest['tree'] if f['type'] == 'blob' and f['path'].lower().endswith('.png')]
    base_url = 'https://raw.githubusercontent.com/NatroTeam/NatroMacro/' + manifest['sha']
    result = download_all(files, base_url, ROOT / 'upstream/NatroMacro')
    if result:
        return result
    print('Building image index...', flush=True)
    return subprocess.call([sys.executable, str(ROOT / 'tools/extract_assets.py')])


if __name__ == '__main__':
    raise SystemExit(main())
