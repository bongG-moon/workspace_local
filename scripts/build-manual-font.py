"""Build the offline Noto Sans KR subset; build-PC fontTools only, no downloads."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'scripts/assets/manual-font'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font', required=True, type=Path)
    args = parser.parse_args()
    license_text = (ASSETS / 'OFL.txt').read_text(encoding='utf-8')
    names = ['README.md', 'ONBOARDING_COURSE.md', 'CLAUDE_CODE_BASICS.md',
             'USER_GUIDE.md', 'COMPANY_AGENT_HANDBOOK.md',
             'CLAUDE_CODE_COMMANDS.md', 'DESIGN_TERMS.md']
    files = [*(ROOT / 'docs' / name for name in names),
             ROOT / 'scripts/build-manuals.mjs',
             ROOT / 'company-agent-plugin/resources/onboarding-course.json']
    text = '\n'.join(p.read_text(encoding='utf-8') for p in files)
    text = re.sub(r'data:font/woff;base64,[A-Za-z0-9+/=]+', '', text)
    requested = {ord(c) for c in text if c.isprintable()} | set(range(32, 127))
    source = TTFont(args.font, recalcTimestamp=False)
    # Official variable releases can use "Noto Sans KR Thin" as the legacy
    # family (ID 1); ID 16 holds the typographic family independent of weight.
    source_family = source['name'].getDebugName(16) or source['name'].getDebugName(1)
    if source_family != 'Noto Sans KR':
        raise ValueError('Supply the approved Noto Sans KR font, not a fallback.')
    missing = requested - set(source.getBestCmap())
    if missing:
        raise ValueError('Font lacks guide characters: ' + repr(''.join(map(chr, sorted(missing)))))
    font = source
    options = subset.Options()
    options.name_IDs = ['*']
    options.name_legacy = True
    options.name_languages = ['*']
    options.recalc_timestamp = False
    worker = subset.Subsetter(options=options)
    worker.populate(unicodes=requested)
    worker.subset(font)
    # Subset first: instancing unused composite glyphs is both slow and fragile.
    font = instantiateVariableFont(font, {'wght': (400, 700)}, inplace=False)
    font['name'].setName(license_text, 13, 3, 1, 0x409)
    font.flavor = 'woff'  # Standard-library zlib, no additional Brotli dependency.
    font_path = ASSETS / 'NotoSansKR-guide.woff'
    font.save(font_path)
    data = font_path.read_bytes()
    manifest = {
        'family': 'Noto Sans KR', 'weight': [400, 700],
        'sourceFile': args.font.name,
        'sourceVersion': source['name'].getDebugName(5),
        'sourceSha256': hashlib.sha256(args.font.read_bytes()).hexdigest(),
        'license': 'SIL Open Font License 1.1',
        'licenseUrl': 'https://github.com/google/fonts/blob/main/ofl/notosanskr/OFL.txt',
        'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
        'characters': ''.join(map(chr, sorted(font.getBestCmap()))),
    }
    (ASSETS / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(f'Noto Sans KR: {len(manifest["characters"])} characters, {len(data)} bytes, weights 400-700')


if __name__ == '__main__':
    main()
