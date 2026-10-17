# Update progress fonts

These two 11 KB static TrueType subsets contain only the fixed Korean update
messages and AX Workspace label from `local_app/update_progress.py`. They come
from the verified Noto Sans KR Regular (400) and SemiBold (600) notification
fonts in `deploy/fonts`. User-provided titles or other dynamic content must not
be rendered with these limited subsets.

The private family names prevent collisions with any installed Noto Sans KR
font. Win32 loads them from memory for the short-lived update coordinator only;
there is no font installation, registry change, browser, or extra process. The
font resources are released after the window and its font handles are destroyed.
If an asset is unavailable, the card falls back to Malgun Gothic.

Regenerate offline after changing fixed labels (fontTools 4.63.0 is build-time
only):

```powershell
python -B -X utf8 scripts/test-lab/build-update-fonts.py
python -B -X utf8 scripts/test-lab/build-update-fonts.py --check
```

`SOURCE.json` records source/output hashes, exact weights, and glyph coverage.
Original copyright and SIL Open Font License notices remain in the name tables
and the accompanying `OFL.txt`.
