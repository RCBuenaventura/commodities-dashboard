"""EIA parsing, and the credential handling that surrounds it."""

from __future__ import annotations

from datetime import date

import pytest

from conftest import error_client, fixture_json, instrument, json_client
from dashboard.sources.base import FetchError
from dashboard.sources.eia import API_KEY_ENV, EIASource, parse_series

PROPANE = {
    "id": "propane",
    "source": "eia",
    "symbol": "petroleum/pri/spt/data:EER_EPLLPA_PF4_Y44MB_DPG",
    "unit": "USD/gal",
}


def test_parses_rows_oldest_first() -> None:
    quotes = parse_series(fixture_json("eia_propane.json"), instrument(**PROPANE))
    assert [q.observed for q in quotes] == [
        date(2026, 8, 4),
        date(2026, 8, 5),
        date(2026, 8, 7),
    ]


def test_null_values_are_skipped() -> None:
    quotes = parse_series(fixture_json("eia_propane.json"), instrument(**PROPANE))
    assert date(2026, 8, 6) not in {q.observed for q in quotes}


def test_numeric_strings_are_accepted() -> None:
    """Some EIA datasets return values as strings."""
    quotes = parse_series(fixture_json("eia_propane.json"), instrument(**PROPANE))
    assert next(q for q in quotes if q.observed == date(2026, 8, 5)).value == 0.8021


def test_api_error_payload_raises() -> None:
    with pytest.raises(FetchError, match="api_key"):
        parse_series(fixture_json("eia_bad_key.json"), instrument(**PROPANE))


@pytest.mark.parametrize(
    "payload",
    [{}, {"response": {}}, {"response": {"data": {}}}, {"response": {"data": []}}],
)
def test_malformed_payloads_raise(payload: object) -> None:
    with pytest.raises(FetchError):
        parse_series(payload, instrument(**PROPANE))


def test_missing_key_is_reported_not_silently_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A quietly-skipped source looks exactly like a price that did not move."""
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(FetchError, match=API_KEY_ENV):
        EIASource().fetch([instrument(**PROPANE)])


def test_key_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "not-a-real-key")
    source = EIASource(client=json_client(fixture_json("eia_propane.json")))
    assert len(source.fetch([instrument(**PROPANE)])) == 3


def test_symbol_must_carry_its_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "not-a-real-key")
    source = EIASource(client=json_client(fixture_json("eia_propane.json")))
    with pytest.raises(FetchError, match="no series returned data"):
        source.fetch([instrument(**{**PROPANE, "symbol": "EER_EPLLPA_PF4_Y44MB_DPG"})])


def test_http_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "not-a-real-key")
    with pytest.raises(FetchError):
        EIASource(client=error_client(503)).fetch([instrument(**PROPANE)])


def test_the_api_key_never_reaches_an_error_message() -> None:
    """The key travels as a query parameter, so it must not be echoed in messages."""
    secret = "super-secret-key-value"
    source = EIASource(api_key=secret, client=error_client(403))
    try:
        source.fetch([instrument(**PROPANE)])
    except FetchError as exc:
        assert secret not in str(exc)
    else:  # pragma: no cover - the 403 client always fails
        pytest.fail("expected a FetchError")
