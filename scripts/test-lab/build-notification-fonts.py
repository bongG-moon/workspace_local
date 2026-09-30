"""Reproduce native notification fonts offline; fontTools is build-time only.

Uses the already vendored, hash-verified Noto Sans KR WOFF. Static full-cmap
instances avoid variable-font support differences in .NET Framework GDI/GDI+.
The compressed assets are embedded in the desktop host and never installed.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path

import fontTools
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'local_app/web/fonts/NotoSansKR-Variable.woff'
SOURCE_INFO = SOURCE.with_name('SOURCE.json')
LICENSE = SOURCE.with_name('OFL.txt')
OUTPUT = ROOT / 'deploy/fonts'
FONTTOOLS_VERSION = '4.63.0'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def compressed(data):
    # GzipFile fixes the header OS byte across Python/platform versions. An
    # empty filename and mtime=0 keep source paths and build times out of it.
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode='wb', compresslevel=9, mtime=0, filename='') as stream:
        stream.write(data)
    return output.getvalue()


def build():
    if fontTools.__version__ != FONTTOOLS_VERSION:
        raise ValueError('Reproducible font generation requires fontTools==' + FONTTOOLS_VERSION)
    source_data = SOURCE.read_bytes()
    source_info = json.loads(SOURCE_INFO.read_text(encoding='utf-8'))
    if sha256(source_data) != source_info['fontSha256']:
        raise ValueError('Vendored Noto Sans KR does not match SOURCE.json.')
    source = TTFont(io.BytesIO(source_data), recalcTimestamp=False)
    cmap = source.getBestCmap()
    if (source['name'].getDebugName(16) != 'Noto Sans KR'
            or not set(range(0xAC00, 0xD7A4)).issubset(cmap)):
        raise ValueError('Expected Noto Sans KR with all modern Hangul syllables.')
    outputs = {'OFL.txt': LICENSE.read_bytes()}
    fonts = []
    for name, weight in (('Regular', 400), ('SemiBold', 600)):
        font = instantiateVariableFont(TTFont(io.BytesIO(source_data), recalcTimestamp=False),
            {'wght': weight}, inplace=True, updateFontNames=True)
        font.flavor = None
        if ('fvar' in font or set(font.getBestCmap()) != set(cmap)
                or font['OS/2'].usWeightClass != weight):
            raise ValueError('Static instance changed the required character coverage or weight.')
        buffer = io.BytesIO()
        font.save(buffer)
        ttf = buffer.getvalue()
        asset = compressed(ttf)
        filename = f'NotoSansKR-{name}.ttf.gz'
        outputs[filename] = asset
        fonts.append({'file': filename, 'weight': weight,
                      'family': font['name'].getDebugName(1),
                      'style': font['name'].getDebugName(2),
                      'postScriptName': font['name'].getDebugName(6),
                      'bytes': len(asset), 'sha256': sha256(asset),
                      'uncompressedBytes': len(ttf), 'uncompressedSha256': sha256(ttf)})
    manifest = {
        'schemaVersion': 1,
        'family': 'Noto Sans KR',
        'source': str(SOURCE.relative_to(ROOT)).replace('\\', '/'),
        'sourceSha256': sha256(source_data),
        'upstreamSource': source_info['source'],
        'upstreamSourceSha256': source_info['sourceSha256'],
        'sourceVersion': source['name'].getDebugName(5),
        'generator': 'scripts/test-lab/build-notification-fonts.py',
        'fontToolsVersion': FONTTOOLS_VERSION,
        'format': 'Static TrueType instances in deterministic gzip containers; no glyph subset',
        'coverage': {'sourceCmapRetained': True, 'codepoints': len(cmap),
                     'modernHangulSyllables': 11172, 'glyphs': len(source.getGlyphOrder())},
        'license': 'SIL Open Font License 1.1',
        'licenseFile': 'OFL.txt',
        'licenseSha256': sha256(outputs['OFL.txt']),
        'fonts': fonts,
    }
    outputs['SOURCE.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Rebuild in memory and compare existing assets.')
    args = parser.parse_args()
    outputs = build()
    if args.check:
        different = [name for name, data in outputs.items()
                     if not (OUTPUT / name).is_file() or (OUTPUT / name).read_bytes() != data]
        if different:
            raise SystemExit('Font assets differ: ' + ', '.join(different))
        print('Notification font assets reproduce exactly; full cmap and Hangul coverage verified.')
    else:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        for name, data in outputs.items():
            (OUTPUT / name).write_bytes(data)
        print(json.dumps({'directory': str(OUTPUT), 'bytes': {name: len(data) for name, data in outputs.items()}},
                         ensure_ascii=False))


if __name__ == '__main__':
    main()
