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


#: The variables naming every secret file the backend reads while it imports.
_SECRET_FILE_VARIABLES = (
    "JWT_SECRET_KEY_FILE",
    "MFA_ENCRYPTION_KEY_FILE",
    "POSTGRES_PASSWORD_FILE",
    "SERVER_OWNER_SECRET_KEY_FILE",
)


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    """
    Render once through the CLI, from a dev-shaped caller environment.

    Per-env cookie names and a release version are set, so either reaching the
    document fails a test below, and PYTHONOPTIMIZE=2, which would strip the
    docstrings the app needs to import. Every secret points at a file that does
    not exist, so the render succeeds only by reading none - as an image build,
    which has no secrets, must - and not because the machine running the tests
    happens to hold them.
    """
    tmp = tmp_path_factory.mktemp("openapi")
    output = tmp / "openapi.json"
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MASCOPE_ENV", "wt-caller")
        mp.setenv("MASCOPE_COOKIE_SCOPED", "1")
        mp.setenv("MASCOPE_VERSION", "v9.9.9")
        mp.setenv("PYTHONOPTIMIZE", "2")
        for variable in _SECRET_FILE_VARIABLES:
            mp.setenv(variable, str(tmp / "absent" / variable.lower()))
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
    document, text = rendered
    assert "wt-caller" not in text
    assert "v9.9.9" not in text
    # Operations take their descriptions from docstrings, so they are all there.
    assert document["paths"]["/api/workspaces"]["get"]["description"]


def test_errors_are_declared_in_the_shape_the_app_answers_them(rendered):
    """Every handler answers {"error", "detail"}; FastAPI's own 422 schema
    describes a validation body the app never sends."""
    document, text = rendered
    body = document["components"]["schemas"]["ApiErrorBody"]
    assert set(body["properties"]) == {"error", "detail"}
    responses = document["paths"]["/api/workspaces"]["get"]["responses"]
    for status in ("4XX", "5XX"):
        schema = responses[status]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/ApiErrorBody"}
    assert "422" not in responses
    assert "HTTPValidationError" not in text


def test_token_scheme_is_a_bearer_token_with_its_service_header(rendered):
    """Not the OAuth2 password flow fastapi-users declares, whose tokenUrl names
    no route: a token is generated in the web app, and the backend refuses one
    sent without the service it was generated for."""
    document, text = rendered
    schemes = document["components"]["securitySchemes"]
    assert schemes["APIToken"]["type"] == "http"
    assert schemes["APIToken"]["scheme"] == "bearer"
    assert schemes["ServiceName"]["in"] == "header"
    assert schemes["ServiceName"]["name"] == "X-Service-Name"
    assert "OAuth2PasswordBearer" not in text


def test_token_is_declared_only_where_the_backend_accepts_one(rendered):
    """Listing workspaces is marked token_access, for the SDK; the signed-in
    user's own account answers to the session cookie alone."""
    document, _ = rendered
    paths = document["paths"]
    assert paths["/api/workspaces"]["get"]["security"] == [
        {"APIKeyCookie": []},
        {"APIToken": [], "ServiceName": []},
    ]
    assert paths["/api/users/me"]["get"]["security"] == [{"APIKeyCookie": []}]


def test_refuses_a_home_without_the_config_layers(tmp_path):
    with pytest.raises(OpenApiRenderError, match="base.mascope.toml"):
        render(tmp_path / "openapi.json", config_home=tmp_path)


def test_refuses_a_directory_as_the_output_before_rendering(tmp_path):
    """Refused up front: the config layers are not even looked for."""
    with pytest.raises(OpenApiRenderError, match="is a directory"):
        render(tmp_path, config_home=tmp_path)
