"""Shared test helpers.

The suite makes no network calls, ever. Source tests parse saved payloads from
``tests/fixtures/``; nothing here opens a socket.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def repo_root() -> Path:
    """The repository root, for tests that read the real `instruments.toml`."""
    return REPO_ROOT


def fixture_text(name: str) -> str:
    """Read a recorded provider payload from ``tests/fixtures/``."""
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")
