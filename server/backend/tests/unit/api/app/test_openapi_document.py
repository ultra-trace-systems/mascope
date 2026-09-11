"""Tests: the OpenAPI document production deployments publish with the docs.

Every deployment serves the document anonymously, so what it must not carry
matters as much as what it must: it has to describe a default production
deployment whatever runtime renders it, and nothing of the rendering machine -
its env, its secrets, its version - may end up in it.
"""

import json

import pytest
from typer.testing import CliRunner

from mascope_backend.main import backend_app
from mascope_backend.openapi import OpenApiRenderError, render


#: The value of a secret the caller's environment points at.
_CALLER_SECRET = "caller-secret-that-must-not-reach-the-document"


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    """
    Render once through the CLI, from a dev-shaped caller environment.

    Per-env cookie names, a secret and a release version are all set, so any of
    them leaking into the document fails a test below.
    """
    tmp = tmp_path_factory.mktemp("openapi")
    secret = tmp / "jwt_secret_key.txt"
    secret.write_text(_CALLER_SECRET + "\n", encoding="utf-8")
    output = tmp / "openapi.json"
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MASCOPE_ENV", "wt-caller")
        mp.setenv("MASCOPE_COOKIE_SCOPED", "1")
        mp.setenv("JWT_SECRET_KEY_FILE", str(secret))
        mp.setenv("MASCOPE_VERSION", "v9.9.9")
        result = CliRunner().invoke(backend_app, ["openapi", "--output", str(output)])
    assert result.exit_code == 0, result.output
    text = output.read_text(encoding="utf-8")
    return json.loads(text), text


def test_document_is_openapi_3_titled_for_mascope(rendered):
    document, _ = rendered
    assert document["openapi"].startswith("3.")
    assert document["info"]["title"] == "Mascope API"


def test_session_cookie_carries_its_prod_name(rendered):
    """Not the caller's per-env name: the document describes a deployment."""
    document, _ = rendered
    cookie = document["components"]["securitySchemes"]["APIKeyCookie"]
    assert cookie["name"] == "mascope_auth"


def test_every_path_is_under_the_proxied_api(rendered):
    """nginx proxies /api/ to the backend and nothing else, so a path outside
    it would be documented yet unreachable on a deployment."""
    document, _ = rendered
    outside = [path for path in document["paths"] if not path.startswith("/api/")]
    assert document["paths"]
    assert not outside, f"paths outside /api/: {outside}"


def test_nothing_of_the_rendering_machine_reaches_the_document(rendered):
    _, text = rendered
    assert _CALLER_SECRET not in text
    assert "wt-caller" not in text
    assert "v9.9.9" not in text


def test_refuses_a_home_without_the_config_layers(tmp_path):
    with pytest.raises(OpenApiRenderError, match="base.mascope.toml"):
        render(tmp_path / "openapi.json", config_home=tmp_path)
