"""
Hermetic unit tests for ``ChemInfoResource.query_by_mz``: the body it sends to
``POST /api/cheminfo/mz/query``. They stub the HTTP layer and need no running
stack.

``isotopologues`` defaults to True and always goes out, so ``False`` reaches
the server too; ``known_only`` goes out only when asked for. A server that
predates either ignores it.
"""

from typing import Any

import pytest

from mascope_sdk.resources.cheminfo import ChemInfoResource


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeServer:
    def __init__(self):
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.rows: list[dict] | None = [{"target_compound_formula": "CH2O2"}]

    def http_post(self, url, path, access_token, data=None, **kwargs):
        self.posts.append((path, dict(data or {})))
        return _Response({"status": "success", "data": self.rows})


class _StubClient:
    url = "http://testserver"
    access_token = "token"
    _timeout = (1, 5)
    _verify_ssl = False
    _service_name = "mascope_sdk"


@pytest.fixture()
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr("mascope_sdk.resources._base.http_post", fake.http_post)
    return fake


@pytest.fixture()
def cheminfo(server):
    return ChemInfoResource(_StubClient())


def test_default_query_asks_for_isotopologues(server, cheminfo):
    results = cheminfo.query_by_mz(126.9223, ["mech-br"], mz_tolerance=5.0)

    assert results == [{"target_compound_formula": "CH2O2"}]
    [(path, body)] = server.posts
    assert path == "cheminfo/mz/query"
    assert body == {
        "mz": 126.9223,
        "mz_precision": 5.0,
        "formula_ranges": "C0-100 H0-100 O0-100 N0-100",
        "ionization_mechanism_ids": ["mech-br"],
        "limit": 20,
        "isotopologues": True,
    }


def test_isotopologues_false_is_sent(server, cheminfo):
    cheminfo.query_by_mz(126.9223, ["mech-br"], isotopologues=False)

    [(_, body)] = server.posts
    assert body["isotopologues"] is False
    assert "known_only" not in body


def test_known_only_is_sent_when_asked_for(server, cheminfo):
    cheminfo.query_by_mz(126.9223, ["mech-br"], known_only=True)

    [(_, body)] = server.posts
    assert body["known_only"] is True
    assert body["isotopologues"] is True


def test_flags_are_keyword_only(cheminfo):
    with pytest.raises(TypeError):
        cheminfo.query_by_mz(126.9223, ["mech-br"], "C0-10", 5.0, 20, False)


def test_no_data_answers_an_empty_list(server, cheminfo):
    server.rows = None

    assert cheminfo.query_by_mz(126.9223, ["mech-br"]) == []
