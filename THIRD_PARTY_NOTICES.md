# Third-party notices

## Noto Sans KR

Company Workspace includes **Noto Sans KR**, distributed under the **SIL Open Font License 1.1**, in two forms:

- The app web UI uses a variable WOFF font with all 11,172 modern Hangul syllables. Its license is [local_app/web/fonts/OFL.txt](local_app/web/fonts/OFL.txt); source and hashes are recorded in [SOURCE.json](local_app/web/fonts/SOURCE.json).
- The offline Company Agent user guide embeds a smaller character subset. Its license is [scripts/assets/manual-font/OFL.txt](scripts/assets/manual-font/OFL.txt), with [source/build notes](scripts/assets/manual-font/README.md) and [recorded metadata](scripts/assets/manual-font/manifest.json). The generated guide retains the license.

The font is served locally or embedded in the guide. It is not installed into Windows, and these assets do not require a Google Fonts request.

## Python in the standalone EXE

The **EXE release** includes the official **Python 3.13.15 Windows x64 embeddable distribution**. The VBS release uses an existing Python installation and does not redistribute that runtime.

The upstream `LICENSE.txt`, containing Python's licensing terms and incorporated-software notices, is retained inside the EXE payload at `Company-Workspace/runtime/LICENSE.txt`. After the EXE prepares its private runtime cache, the same file is available under that application's `runtime` directory. The original license is not replaced by this summary.

[deploy/New-WorkspaceStandalone.ps1](deploy/New-WorkspaceStandalone.ps1) records the official download URL and pinned SHA-256. The extracted runtime also includes `SOURCE.json` with its version, source URL and checksum. The app does not register this Python in the system PATH or replace the user's Python installation.

Upstream references: [Python 3.13.15 distribution](https://www.python.org/downloads/release/python-31315/) and [embeddable distribution documentation](https://docs.python.org/3.13/using/windows.html#the-embeddable-package).

## External applications

Claude Code and Microsoft Edge are external dependencies and are not redistributed in this repository or these release packages. Company Agent core is resolved from a separate installation; only static onboarding and user-guide resources are included here. Microsoft Office and other default applications used to open files remain separately installed products.
