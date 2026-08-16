"""Manages the on-disk workspace each scenario run operates on.

Greenfield starts from an empty workspace (the Implementation Agent
scaffolds it). Brownfield and ambiguous-requirement scenarios start from a
seeded copy of an existing codebase (the "production system" they enhance),
so the Requirements/Design stages can perform genuine codebase reasoning
(reading real files) rather than operating on a description of code.
"""
from __future__ import annotations

import shutil
from pathlib import Path

BASELINE_TEMPLATE = Path(__file__).parent / "templates" / "baseline"
RUNS_ROOT = Path(__file__).parent.parent / "runs"


def workspace_path(scenario_name: str) -> Path:
    return RUNS_ROOT / scenario_name / "workspace"


def create_empty_workspace(scenario_name: str) -> Path:
    ws = workspace_path(scenario_name)
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def create_seeded_workspace(scenario_name: str, seed_dir: Path | None = None) -> Path:
    """Seed a workspace from an existing codebase: `seed_dir` if given
    (e.g. the completed greenfield workspace), otherwise the baseline
    template shipped with the orchestrator."""
    ws = workspace_path(scenario_name)
    if ws.exists():
        shutil.rmtree(ws)
    src = seed_dir or BASELINE_TEMPLATE
    shutil.copytree(src, ws, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.db", "*.db-*"))
    return ws


def app_dir(workspace: Path) -> Path:
    return workspace / "app"


def tests_dir(workspace: Path) -> Path:
    return workspace / "tests"
