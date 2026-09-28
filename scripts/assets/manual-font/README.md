# Noto Sans KR — offline guide subset

The guide embeds this WOFF directly. Opening one HTML file does not contact
Google Fonts or depend on an employee's installed fonts. `OFL.txt` is the upstream
license, also retained in the WOFF name table and each generated HTML header.
`manifest.json` records the source hash/version and included characters.

No font is installed in Windows. The subset preserves weights 400–700 and the
guide's characters; it is not a general-purpose Korean font package. The manual
builder rejects newly added characters that are missing from it.

To refresh, use an approved Noto Sans KR TTF on the build PC:

```powershell
python -X utf8 scripts/build-manual-font.py --font "<approved Noto Sans KR TTF>"
node scripts/build-manuals.mjs --modules "<approved node_modules>"
```

The full source font stays on the build PC. Employees receive the embedded subset
and its license, with no new dependencies, downloads, or model calls.

## Source used for the 2026-09-20 documentation refresh

The prior non-release source was not present in this checkout. The refreshed
subset uses the official Google Fonts **Version 2.004-H2** at commit
`4efc2774c63917927efe769ca845def6bd6debae`, not an assumed match to that old file.

- [Pinned TTF](https://github.com/google/fonts/blob/4efc2774c63917927efe769ca845def6bd6debae/ofl/notosanskr/NotoSansKR%5Bwght%5D.ttf)
- TTF SHA-256: `194018e6b2b293a7964f037b25c0249ce1418bc9ab3c971060a03aa57861e252`
- [Pinned OFL license](https://github.com/google/fonts/blob/4efc2774c63917927efe769ca845def6bd6debae/ofl/notosanskr/OFL.txt)

The builder checks typographic family ID 16 (or legacy family ID 1). This release
uses `Noto Sans KR Thin` in ID 1 and `Noto Sans KR` in ID 16; that does not change
the requested 400–700 weight range. After any font or text change, rebuild the
single user manual, then run glyph-coverage and browser presentation checks.
