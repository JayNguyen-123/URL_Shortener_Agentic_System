"""Invokes the real pytest suite against a scenario workspace as a
subprocess and parses the result. The Testing Agent stage uses this so
"tests pass" is an actual pytest exit code, not a simulated result.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

RUN_PYTEST_SCRIPT = Path(__file__).parent.parent / "scripts" / "run_pytest.sh"

_SUMMARY_RE = re.compile(
    r"(?P<counts>(?:\d+ \w+(?:, )?)+) in (?P<seconds>[\d.]+)s"
)


def run_pytest(workspace: Path, target: str = "tests") -> dict:
    result = subprocess.run(
        ["bash", str(RUN_PYTEST_SCRIPT), "-q", target],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=120,
    )
    stdout = result.stdout
    summary_match = _SUMMARY_RE.search(stdout)
    summary_line = summary_match.group(0) if summary_match else stdout.strip().splitlines()[-1] if stdout.strip() else "no output"

    passed = failed = errors = 0
    if summary_match:
        for chunk in summary_match.group("counts").split(", "):
            m = re.match(r"(\d+) (\w+)", chunk)
            if not m:
                continue
            count, label = int(m.group(1)), m.group(2)
            if label.startswith("pass"):
                passed = count
            elif label.startswith("fail"):
                failed = count
            elif label.startswith("error"):
                errors = count

    return {
        "returncode": result.returncode,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "total": passed + failed + errors,
        "summary_line": summary_line,
        "stdout_tail": "\n".join(stdout.strip().splitlines()[-40:]),
        "stderr_tail": "\n".join(result.stderr.strip().splitlines()[-40:]) if result.stderr else "",
    }
