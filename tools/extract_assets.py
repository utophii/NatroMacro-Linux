#!/usr/bin/env python3
"""Extract upstream embedded PNGs and constant-color templates without AHK."""
import base64
import hashlib
import json
import re
import struct
from pathlib import Path
from PySide6.QtGui import QImage

ROOT = Path(__file__).resolve().parents[1]
ASSIGN = re.compile(r'(?P<target>(?:bitmaps|buff_characters)\s*(?:\[(?:"[^"]*"|\d+)\])+)[ \t]*:=[ \t]*Gdip_BitmapFromBase64\("(?P<data>[A-Za-z0-9+/=\s]+)"\)')
SOLID = re.compile(r'(?P<target>bitmaps\s*(?:\["[^"]*"\])+)[ \t]*:=[ \t]*Gdip_CreateBitmap\((?P<w>\d+),\s*(?P<h>\d+)\)[\s\S]{0,300}?Gdip_GraphicsClear\([^,]+,\s*(?P<color>0x[0-9a-fA-F]+)\)')

def key(target):
    return '/'.join(re.findall(r'\["?([^"\]]+)"?\]', target)).lower()


def clean_png(data):
    """Drop malformed sBIT metadata from derived copies, preserving image data.

    PNG sBIT lengths: https://www.w3.org/TR/png/#11sBIT
    Upstream BLove.PNG is RGBA but carries the three-byte RGB form.
    """
    if data[:8] != b'\x89PNG\r\n\x1a\n' or len(data) < 33:
        raise ValueError('Invalid PNG header')
    expected = {0: 1, 2: 3, 3: 3, 4: 2, 6: 4}.get(data[25])
    output, offset = [data[:8]], 8
    while offset < len(data):
        if offset+12 > len(data):
            raise ValueError('Truncated PNG chunk')
        size = struct.unpack('>I', data[offset:offset+4])[0]
        end = offset+size+12
        if end > len(data):
            raise ValueError('Truncated PNG chunk data')
        if data[offset+4:offset+8] != b'sBIT' or size == expected:
            output.append(data[offset:end])
        offset = end
    return b''.join(output)


def main():
    out = ROOT / 'natro_linux/data/images'
    out.mkdir(parents=True, exist_ok=True)
    manifest = {}
    sources = sorted((ROOT / 'upstream/NatroMacro/nm_image_assets').rglob('*.ahk'))
    sources += [ROOT / 'upstream/NatroMacro/lib/Walk.ahk']
    for source in sources:
        text = source.read_text(encoding='utf-8-sig')
        group = source.parent.name if source.name != 'Walk.ahk' else 'movement'
        for match in ASSIGN.finditer(text):
            name = key(match['target'])
            if match['target'].startswith('buff_characters'):
                name = 'buffdigit' + name
            data = clean_png(base64.b64decode(re.sub(r'\s+', '', match['data']), validate=True))
            image = QImage.fromData(data, 'PNG')
            if image.isNull():
                raise ValueError(f'Invalid image {group}/{name}')
            digest = hashlib.sha256(data).hexdigest()
            (out / f'{digest}.png').write_bytes(data)
            manifest[f'{group}/{name}'] = {'file': f'images/{digest}.png', 'width': image.width(), 'height': image.height(), 'source': str(source.relative_to(ROOT / 'upstream/NatroMacro'))}
        for match in SOLID.finditer(text):
            name = key(match['target'])
            manifest[f'{group}/{name}'] = {'color': int(match['color'], 16), 'width': int(match['w']), 'height': int(match['h']), 'source': str(source.relative_to(ROOT / 'upstream/NatroMacro'))}
    for source in sorted((ROOT / 'upstream/NatroMacro/nm_image_assets').rglob('*')):
        if not source.is_file() or source.suffix.lower() != '.png':
            continue
        original = source.read_bytes()
        data = clean_png(original)
        image = QImage.fromData(data, 'PNG')
        if image.isNull():
            raise ValueError(f'Invalid original PNG {source}')
        digest = hashlib.sha256(data).hexdigest()
        (out / f'{digest}.png').write_bytes(data)
        name = 'legacy/' + str(source.relative_to(ROOT / 'upstream/NatroMacro/nm_image_assets').with_suffix('')).lower()
        manifest[name] = {'file': f'images/{digest}.png', 'width': image.width(), 'height': image.height(), 'source': str(source.relative_to(ROOT / 'upstream/NatroMacro'))}
        if data != original:
            manifest[name]['adaptation'] = 'Removed invalid sBIT metadata, image data unchanged'
            manifest[name]['source_sha256'] = hashlib.sha256(original).hexdigest()
    (ROOT / 'natro_linux/data/images.json').write_text(json.dumps(manifest, indent=2) + '\n')
    used = {item['file'] for item in manifest.values() if 'file' in item}
    print(f'Extracted {len(manifest)} named images, {len(used)} unique PNGs')

if __name__ == '__main__':
    main()
