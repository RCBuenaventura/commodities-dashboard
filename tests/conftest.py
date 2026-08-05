"""Shared test helpers.

The suite makes no network calls, ever. Sources take an injected `httpx.Client`, so
their transport is replaced with `httpx.MockTransport` here and nothing opens a
socket. See ``fixtures/README.md`` for where the payloads came from.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from dashboard.config import Instrument

if TYPE_CHECKING:
    from collections.abc import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def repo_root() -> Path:
    """The repository root, for tests that read the real `instruments.toml`."""
    return REPO_ROOT


def fixture_text(name: str) -> str:
    """Read a saved provider payload from ``tests/fixtures/``."""
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def fixture_json(name: str) -> Any:
    """Read and decode a saved JSON payload."""
    return json.loads(fixture_text(name))


def mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    """A client whose transport is a callable, so no request leaves the process."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def json_client(payload: Any, *, status: int = 200) -> httpx.Client:
    """A client that answers every request with the same JSON payload."""
    return mock_client(lambda _request: httpx.Response(status, json=payload))


def error_client(status: int) -> httpx.Client:
    """A client that answers every request with an HTTP error."""
    return mock_client(lambda _request: httpx.Response(status, json={"detail": "no"}))


def instrument(**overrides: Any) -> Instrument:
    """An `Instrument` with defaults, for tests that only care about one field."""
    fields: dict[str, Any] = {
        "id": "brent",
        "name": "Brent crude",
        "group": "crude",
        "source": "yahoo",
        "symbol": "BZ=F",
        "unit": "USD/bbl",
        "currency": "USD",
    }
    fields.update(overrides)
    return Instrument(**fields)
