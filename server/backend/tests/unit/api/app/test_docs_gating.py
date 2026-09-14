"""Tests: the interactive API docs / OpenAPI schema are dev-only.

A deployment publishes a static copy of the document with its user docs
(``/docs/openapi.json``), so ``/docs``, ``/redoc`` and ``/openapi.json`` stay off
the backend outside dev: they would only add a second, live copy of the schema
and an interactive console to a backend reached directly.
"""

from mascope_backend.app.fast import _docs_kwargs


def test_docs_enabled_in_dev():
    kwargs = _docs_kwargs("dev")
    assert kwargs == {
        "docs_url": "/docs",
        "redoc_url": "/redoc",
        "openapi_url": "/openapi.json",
    }


def test_docs_disabled_in_prod():
    kwargs = _docs_kwargs("prod")
    assert kwargs == {
        "docs_url": None,
        "redoc_url": None,
        "openapi_url": None,
    }
