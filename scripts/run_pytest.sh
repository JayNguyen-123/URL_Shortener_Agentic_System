#!/usr/bin/env bash
# Portable pytest launcher.
#
# On a normal developer machine with internet access, `pip install -r
# requirements.txt` puts pytest and the app's dependencies in the same
# interpreter and plain `pytest` just works.
#
# In this sandbox's build environment, pytest and Flask/pydantic happened to
# be provisioned into two different Python 3.11 installs with no PyPI
# egress to unify them, so we bridge the two via PYTHONPATH. This script
# tries the simple path first and only falls back to the bridge if needed,
# so it behaves correctly on both a normal machine and this sandbox.
set -euo pipefail

if python3 -c "import pytest" >/dev/null 2>&1; then
    exec python3 -m pytest "$@"
fi

BRIDGE_PY="/root/.local/share/uv/tools/pytest/bin/python"
if [ -x "$BRIDGE_PY" ]; then
    export PYTHONPATH="/usr/local/lib/python3.11/dist-packages:/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}"
    exec "$BRIDGE_PY" -m pytest "$@"
fi

echo "pytest not found on this machine: pip install -r service/requirements.txt" >&2
exit 1
