"""Tests: how the OpenAPI document describes a sample file's scan table.

``GET /api/sample/files/{sample_file_id}/scans`` exists for API clients, and
a generated client is written from the document alone. So the document has to
say what the route takes: that an API token is accepted, which filters narrow
the table, and that ``fields`` repeats. ``/metadata`` gained
``include_centroids`` beside it, on by default, and the document says that too.
"""

from mascope_backend.app.fast import fast


def _query_parameters(path: str) -> dict[str, dict]:
    operation = fast.openapi()["paths"][path]["get"]
    return {
        parameter["name"]: parameter
        for parameter in operation["parameters"]
        if parameter["in"] == "query"
    }


def _types(schema: dict) -> set[str]:
    """The JSON types a parameter takes, apart from being left out."""
    options = schema.get("anyOf", [schema])
    return {option["type"] for option in options if option["type"] != "null"}


def test_the_scan_table_takes_an_api_token():
    operation = fast.openapi()["paths"]["/api/sample/files/{sample_file_id}/scans"][
        "get"
    ]

    assert {"APIToken": [], "ServiceName": []} in operation["security"]


def test_the_scan_table_documents_its_filters():
    parameters = _query_parameters("/api/sample/files/{sample_file_id}/scans")

    assert set(parameters) == {
        "fields",
        "first_scan",
        "last_scan",
        "t_min",
        "t_max",
        "polarity",
    }
    # Every filter narrows; none is needed to read the table.
    assert not any(parameter["required"] for parameter in parameters.values())
    assert all(parameter["description"] for parameter in parameters.values())
    assert _types(parameters["first_scan"]["schema"]) == {"integer"}
    assert _types(parameters["last_scan"]["schema"]) == {"integer"}
    assert _types(parameters["t_min"]["schema"]) == {"number"}
    assert _types(parameters["t_max"]["schema"]) == {"number"}


def test_fields_is_a_repeated_parameter():
    schema = _query_parameters("/api/sample/files/{sample_file_id}/scans")["fields"][
        "schema"
    ]

    [array] = [
        option for option in schema.get("anyOf", [schema]) if option["type"] == "array"
    ]
    assert array["items"] == {"type": "string"}


def test_metadata_includes_centroids_unless_told_otherwise():
    parameters = _query_parameters("/api/sample/files/{sample_file_id}/metadata")

    assert set(parameters) == {"include_centroids"}
    include_centroids = parameters["include_centroids"]
    assert include_centroids["required"] is False
    assert include_centroids["schema"]["type"] == "boolean"
    assert include_centroids["schema"]["default"] is True
