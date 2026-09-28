"""No-prompt smoke: initialize the installed CLI control channel, then close it.

No business files or prompts are sent to a model. Existing SessionStart hooks
may run, just as when the user launches Claude. Uses a dedicated empty cwd.
"""
from pathlib import Path
import json
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from local_app.bridge import ClaudeSession, resolve_cli, probe_cli

command = resolve_cli()
info = probe_cli(command)
with tempfile.TemporaryDirectory(prefix="company-workspace-handshake-") as cwd:
    events = []
    bridge = ClaudeSession(command, info, Path(cwd), lambda kind, data: events.append((kind, data)))
    start = time.monotonic()
    try:
        bridge.start()
        ready = bridge.ready.wait(45)
        result = {"version": info["version"], "initialized": ready and not bridge.initialization_error and not bridge.closed,
                  "elapsedMs": round((time.monotonic() - start) * 1000), "modelRequestsSent": 0,
                  "permissionMode": "inherited-from-cli", "error": bridge.initialization_error,
                  "eventKinds": [kind for kind, _ in events]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["initialized"]:
            sys.exit(1)
    finally:
        bridge.close()
