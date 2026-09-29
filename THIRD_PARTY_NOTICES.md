# Third-party notices

## Noto Sans KR

Company Workspace includes **Noto Sans KR**, distributed under the **SIL Open Font License 1.1**, in two forms:

- The app web UI uses a variable WOFF font with all 11,172 modern Hangul syllables. Its license is [local_app/web/fonts/OFL.txt](local_app/web/fonts/OFL.txt); source and hashes are recorded in [SOURCE.json](local_app/web/fonts/SOURCE.json).
- The offline Company Agent user guide embeds a smaller character subset. Its license is [scripts/assets/manual-font/OFL.txt](scripts/assets/manual-font/OFL.txt), with [source/build notes](scripts/assets/manual-font/README.md) and [recorded metadata](scripts/assets/manual-font/manifest.json). The generated guide retains the license.

The font is served locally or embedded in the guide. It is not installed into Windows, and these assets do not require a Google Fonts request.

## Python as an external prerequisite

Starting with **0.12.10**, both the EXE and VBS releases require an **existing Python 3.11 or later installation**. They do not redistribute, download or install a Python runtime or Python installer. The EXE payload and its app cache contain application files only.

The app checks the existing installation before starting. It does not replace that installation or register a new Python on the system PATH. Licensing and incorporated-software notices for the separately installed Python are supplied with that installation.

This notice describes the current 0.12.10 release. Historical 0.12.9 release packages and their embedded-runtime notices remain unchanged.

## External applications

Claude Code and Microsoft Edge are external dependencies and are not redistributed in this repository or these release packages. Company Agent core is resolved from a separate installation; only static onboarding and user-guide resources are included here. Microsoft Office and other default applications used to open files remain separately installed products.
