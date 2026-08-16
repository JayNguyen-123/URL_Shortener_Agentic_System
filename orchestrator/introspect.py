"""Generates API documentation from the *actual* registered Flask routes
in a scenario workspace (introspection), rather than from a hand-maintained
list -- so docs/scenario_reports output can't silently drift from what the
Implementation Agent really built.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_INTROSPECT_SCRIPT = '''
import sys, json, os
sys.path.insert(0, sys.argv[1])
from app.main import create_app
app = create_app(db_path=sys.argv[2])
rules = []
for rule in app.url_map.iter_rules():
    if rule.endpoint == "static":
        continue
    methods = sorted(m for m in rule.methods if m not in ("HEAD", "OPTIONS"))
    rules.append({"rule": str(rule), "methods": methods, "endpoint": rule.endpoint})
print(json.dumps(sorted(rules, key=lambda r: (r["rule"], r["methods"]))))
'''


def introspect_routes(workspace: Path) -> list[dict]:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    script_fd, script_path = tempfile.mkstemp(suffix=".py")
    with os.fdopen(script_fd, "w") as f:
        f.write(_INTROSPECT_SCRIPT)

    try:
        result = subprocess.run(
            [sys.executable, script_path, str(workspace), db_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"route introspection failed: {result.stderr}")
        return json.loads(result.stdout.strip().splitlines()[-1])
    finally:
        os.remove(script_path)
        for suffix in ("", "-wal", "-shm"):
            p = db_path + suffix
            if os.path.exists(p):
                os.remove(p)
