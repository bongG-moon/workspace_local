# Third-party notices

## Noto Sans KR

Company Workspace includes **Noto Sans KR**, distributed under the **SIL Open Font License 1.1**, in two forms:

- The app web UI uses a variable WOFF font with all 11,172 modern Hangul syllables. Its license is [local_app/web/fonts/OFL.txt](local_app/web/fonts/OFL.txt); source and hashes are recorded in [SOURCE.json](local_app/web/fonts/SOURCE.json).
- The offline Company Agent user guide embeds a smaller character subset. Its license is [scripts/assets/manual-font/OFL.txt](scripts/assets/manual-font/OFL.txt), with [source/build notes](scripts/assets/manual-font/README.md) and [recorded metadata](scripts/assets/manual-font/manifest.json). The generated guide retains the license.

The font is served locally or embedded in the guide. It is not installed into Windows, and these assets do not require a Google Fonts request.

## Python as an external prerequisite

Starting with **0.12.10**, both the EXE and VBS releases require an **existing Python 3.11 or later installation**. They do not redistribute, download or install a Python runtime or Python installer. The EXE payload and its app cache contain application files only.

The app checks the existing installation before starting. It does not replace that installation or register a new Python on the system PATH. Licensing and incorporated-software notices for the separately installed Python are supplied with that installation.

Historical 0.12.9 release packages and their embedded-runtime notices remain unchanged.

## Microsoft WebView2

Starting with 0.18.0, the dedicated Windows window uses Microsoft WebView2.
Builds download the official `Microsoft.Web.WebView2` NuGet SDK, pinned by version
and SHA256 in [deploy/WebView2.lock.json](deploy/WebView2.lock.json). Releases
redistribute its Core and WinForms managed assemblies and x64 WebView2Loader,
with the original package LICENSE and NOTICE as `desktop/WebView2-LICENSE.txt`
and `desktop/WebView2-NOTICE.txt`. The build manifest records their hashes.

The WebView2 Evergreen Runtime is a separately installed prerequisite. Neither
it nor a runtime installer is redistributed, downloaded or installed by the app.
The app uses its own user-data folder, not the user's Edge or Chrome profile.

## External applications

Claude Code, Microsoft Office and other default applications used to open files
remain separately installed products. The current app does not require Company
Agent or an Edge/Chrome browser window. The external WebView2 Runtime remains
subject to its own Microsoft terms and the organization's installation policy.
