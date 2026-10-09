"""
No route takes how a sample item's chemistry was decided.

``sample_item.bound_by`` and ``sample_item.method_binding_id`` are written by
auto-processing alone: every report on routing is a count over them, so a
request that could set one would be able to claim a rung for an item nobody
routed (``AcquisitionItemCreate``). They are read back through the item routes
and the sample view, which makes them fields a client holds and may well send
back with the rest of a row - so what keeps them out is no longer that nothing
knows their names.

Structural, like ``test_api_route_coverage.py``: it walks whatever routes the
app registers and every model a request to each is read into, so a route or a
request model added later is covered without anyone coming back here. What it
cannot see is a body a route takes as a bare ``dict``, which names no fields;
no route that writes a sample item takes one.
"""

from typing import get_args

from fastapi.routing import APIRoute, iter_route_contexts
from pydantic import BaseModel

from mascope_backend.app.fast import fast


#: The two fields, by the names a request would have to use.
PROVENANCE = {"bound_by", "method_binding_id"}


def _collect(annotation, names: set[str], seen: set[type]) -> None:
    """Add the fields of every model reachable from a parameter's type.

    :param annotation: The type, however it is wrapped: a model, a list of
        them, an optional one, a union.
    :param names: Where the field names and aliases go.
    :param seen: The models already walked, since a model can hold itself.
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return
        seen.add(annotation)
        for name, field in annotation.model_fields.items():
            names.add(name)
            names.update(
                alias
                for alias in (field.alias, field.validation_alias)
                if isinstance(alias, str)
            )
            _collect(field.annotation, names, seen)
        return
    for argument in get_args(annotation):
        _collect(argument, names, seen)


def _request_fields(dependant) -> set[str]:
    """Every name a request to a route can send a value under.

    Its path, query, header, cookie and body parameters, those of every
    dependency it resolves, and the fields of every model among them.
    """
    names: set[str] = set()
    seen: set[type] = set()
    pending = [dependant]
    while pending:
        current = pending.pop()
        pending.extend(current.dependencies)
        for parameter in (
            *current.path_params,
            *current.query_params,
            *current.header_params,
            *current.cookie_params,
            *current.body_params,
        ):
            names.update({parameter.name, parameter.alias})
            _collect(parameter.field_info.annotation, names, seen)
    return names


def _routes() -> dict[tuple[str, str], set[str]]:
    """``{(method, path): request field names}`` for every API route."""
    return {
        (method, context.path_format): _request_fields(context.dependant)
        for context in iter_route_contexts(fast.routes)
        if isinstance(context.route, APIRoute)
        for method in context.methods or ()
    }


def test_the_walk_reads_the_models_a_sample_item_request_is_read_into():
    """Guards the assertion below against passing on a walk that sees no
    fields: each of the four ways a route names an item's fields is found."""
    routes = _routes()

    # A model as the whole body.
    assert "ionization_mode_id" in routes[("POST", "/api/sample/items")]
    assert "ionization_mode_id" in routes[("POST", "/api/sample/items/process")]
    # A model inside the body's model.
    assert (
        "ionization_mode_id" in routes[("PATCH", "/api/sample/items/{sample_item_id}")]
    )
    # A list of models inside the body's model.
    assert (
        "ionization_mode_id"
        in routes[("POST", "/api/sample/batches/{sample_batch_id}/import")]
    )
    # A model as the query string.
    assert "sample_batch_id" in routes[("GET", "/api/sample/items")]


def test_no_route_takes_what_bound_an_item():
    taking = {
        f"{method} {path}": sorted(fields & PROVENANCE)
        for (method, path), fields in _routes().items()
        if fields & PROVENANCE
    }

    assert taking == {}
