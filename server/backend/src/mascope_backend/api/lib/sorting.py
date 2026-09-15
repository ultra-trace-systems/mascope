"""
Sorting a list endpoint by a caller-named column.

A list endpoint's ``sort`` query parameter names the column to order by. Each
endpoint declares the columns it can be sorted by as an explicit tuple, and
that tuple is the only thing a ``sort`` value is resolved against:

- the endpoint's query-params model types ``sort`` as ``Literal[<tuple>]``, so
  request validation refuses any other value with a 422 and the OpenAPI
  document lists the accepted ones;
- the controller orders through :func:`order_by_column`, which checks the same
  tuple again before touching the model, so a caller that reaches the
  controller without the query-params model still cannot name an attribute
  outside it.

The allowlist is deliberately explicit rather than "any mapped column": a
column added to a model later - a credential, a secret, an internal flag - does
not become sortable, and so does not become an ordering oracle over values the
caller cannot read, until someone adds it to the tuple on purpose. It also
keeps non-orderable columns (JSON) and non-column attributes (relationships,
methods, dunder names) out of reach, which used to surface as 500s.
"""

from sqlalchemy.sql.elements import ColumnElement


def order_by_column(
    model: type, sort: str | None, order: str | None, sortable: tuple[str, ...]
) -> ColumnElement:
    """
    The ORDER BY clause for ``sort`` on ``model``, if ``sort`` is sortable.

    :param model: The mapped class (or view mapping) the column belongs to.
    :param sort: The column name the caller asked to sort by.
    :param order: ``"desc"`` for descending; anything else sorts ascending.
    :param sortable: The endpoint's sortable column names.
    :raises ValueError: If ``sort`` is not one of ``sortable`` (a 400 through
        ``process_exception``). The rejected value is not echoed back.
    :return: The ascending or descending clause for the column.
    """
    if sort not in sortable:
        raise ValueError(
            f"Unsupported sort column. Sortable columns: {', '.join(sortable)}."
        )
    column = getattr(model, sort)
    return column.desc() if order == "desc" else column.asc()
