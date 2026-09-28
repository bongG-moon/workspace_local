# Deterministic wrapper fixture. Does not load or change real Claude settings.
$env:WORKSPACE_FAKE_WRAPPER = 'active'
& $env:WORKSPACE_FAKE_PYTHON -X utf8 (Join-Path $PSScriptRoot 'workspace_fake_cli.py') @args
exit $LASTEXITCODE
