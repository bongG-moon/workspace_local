"""Build tiny, process-private Noto Sans KR faces for the fixed update card.

fontTools is only needed to regenerate assets. The runtime uses Win32 GDI and
never installs fonts or writes to the Windows font registry.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys

import fontTools
from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from local_app.update_progress import BRAND, LABEL, NATIVE_FONT_FACES, PHASES

SOURCE = ROOT / 'deploy/fonts'
OUTPUT = ROOT / 'local_app/native_fonts'
FONTTOOLS_VERSION = '4.63.0'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def required_characters():
    return ''.join(sorted(set(BRAND + LABEL + ''.join(text for phase in PHASES.values() for text in phase))))


def build():
    if fontTools.__version__ != FONTTOOLS_VERSION:
        raise ValueError('Reproducible generation requires fontTools==' + FONTTOOLS_VERSION)
    source_info = json.loads((SOURCE / 'SOURCE.json').read_text(encoding='utf-8'))
    sources = {item['weight']: item for item in source_info['fonts']}
    characters = required_characters()
    outputs, fonts = {'OFL.txt': (SOURCE / 'OFL.txt').read_bytes()}, []
    for weight, (filename, family) in NATIVE_FONT_FACES.items():
        source = sources[weight]
        data = (SOURCE / source['file']).read_bytes()
        if sha256(data) != source['sha256']:
            raise ValueError('Native notification font differs from its verified source manifest.')
        font = TTFont(io.BytesIO(gzip.decompress(data)), recalcTimestamp=False)
        if font['OS/2'].usWeightClass != weight or 'fvar' in font:
            raise ValueError('Expected a static font at the exact requested weight.')
        options = subset.Options()
        options.name_IDs = list(range(26))
        options.name_languages = ['*']
        options.recalc_timestamp = False
        subsetter = subset.Subsetter(options=options)
        subsetter.populate(text=characters)
        subsetter.subset(font)
        postscript = filename.removesuffix('.ttf')
        names = {1: family, 2: 'Regular', 3: postscript, 4: family, 6: postscript,
                 16: family, 17: 'Regular', 21: family, 22: 'Regular'}
        for record in font['name'].names:
            if record.nameID in names:
                record.string = names[record.nameID].encode(record.getEncoding())
        for name_id, value in names.items():
            font['name'].setName(value, name_id, 3, 1, 0x409)
        font.flavor = None
        if not set(map(ord, characters)).issubset(font.getBestCmap()):
            raise ValueError('A fixed update label is missing from the font subset.')
        result = io.BytesIO()
        font.save(result)
        asset = result.getvalue()
        outputs[filename] = asset
        fonts.append({'file': filename, 'weight': weight, 'family': family,
                      'bytes': len(asset), 'sha256': sha256(asset),
                      'source': 'deploy/fonts/' + source['file'], 'sourceSha256': source['sha256']})
    outputs['SOURCE.json'] = (json.dumps({
        'schemaVersion': 1, 'sourceFamily': 'Noto Sans KR',
        'generator': 'scripts/test-lab/build-update-fonts.py',
        'fontToolsVersion': FONTTOOLS_VERSION,
        'usage': 'Fixed update progress labels only; no user content',
        'characters': characters, 'codepoints': sorted(map(ord, characters)),
        'license': 'SIL Open Font License 1.1', 'licenseFile': 'OFL.txt',
        'licenseSha256': sha256(outputs['OFL.txt']), 'fonts': fonts,
    }, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    outputs = build()
    if args.check:
        different = [name for name, data in outputs.items()
                     if not (OUTPUT / name).is_file() or (OUTPUT / name).read_bytes() != data]
        if different:
            raise SystemExit('Update font assets differ: ' + ', '.join(different))
        print('Update fonts reproduce exactly; all fixed Korean labels and both weights are covered.')
    else:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        for name, data in outputs.items():
            (OUTPUT / name).write_bytes(data)
        print(json.dumps({name: len(data) for name, data in outputs.items()}))


if __name__ == '__main__':
    main()
