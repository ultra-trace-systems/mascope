"""
Sorting a list endpoint by a caller-named column.

A list endpoint's ``sort`` query parameter names the column to order by. Each
endpoint spells its sortable columns once, as a ``Literal`` alias next to its
query-params model (``UserSortColumn = Literal["id", "username", ...]``), and
that alias is the only thing a ``sort`` value is resolved against:

- the query-params model types ``sort`` with the alias, so request validation
  refuses any other value with a 422 and the OpenAPI document lists the
  accepted ones;
- the controller orders through :func:`order_by_column` (or
  :func:`order_distinct_on`) with the same alias, which checks the value again
  before touching the model, so a caller that reaches the controller without
  the query-params model still cannot name an attribute outside it.

The allowlist is explicit rather than "any mapped column": a column added to a
model - a credential, a secret, an internal flag - is not sortable, and so is
no ordering oracle over values the caller cannot read, until it is added to the
alias on purpose. Non-orderable columns (JSON) and non-column attributes
(relationships, methods, dunder names) stay out of reach.
"""

from typing import Any, get_args

from sqlalchemy import Select, inspect, select
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement


def sortable_columns(sort_column: Any) -> tuple[str, ...]:
    """
    The column names a ``Literal`` sort alias accepts.

    :param sort_column: An endpoint's alias, e.g. ``Literal["id", "username"]``.
    :return: Its values, in declaration order.
    """
    return get_args(sort_column)


def order_by_column(
    model: Any, sort: str | None, order: str | None, sort_column: Any
) -> ColumnElement:
    """
    The ORDER BY clause for ``sort`` on ``model``, if ``sort`` is sortable.

    :param model: The mapped class, or an alias of it, the column belongs to.
    :param sort: The column name the caller asked to sort by.
    :param order: ``"desc"`` for descending; anything else sorts ascending.
    :param sort_column: The endpoint's ``Literal`` alias of sortable columns.
    :raises ValueError: If ``sort`` is not one of the alias's values (a 400
        through ``process_exception``). The rejected value is not echoed back.
    :return: The ascending or descending clause for the column.
    """
    sortable = sortable_columns(sort_column)
    if sort not in sortable:
        # No trailing period: process_exception adds one.
        raise ValueError(
            f"Unsupported sort column. Sortable columns: {', '.join(sortable)}"
        )
    column = getattr(model, sort)
    return column.desc() if order == "desc" else column.asc()


def order_distinct_on(
    stmt: Select, model: type, sort: str | None, order: str | None, sort_column: Any
) -> Select:
    """
    Order a ``DISTINCT ON`` select of ``model`` by one of its sortable columns.

    Postgres requires the ``DISTINCT ON`` expressions to lead the ``ORDER BY``,
    so ordering such a select by any other column is an error. The select
    becomes a subquery - which keeps exactly the rows it picked - and an outer
    select orders them. The outer select exposes ``model`` under its class name
    and every other selected column under its own key, so result rows read the
    same as the inner select's.

    :param stmt: A select of ``model`` (plus any added columns) with ``DISTINCT ON``.
    :param model: The mapped class the select loads and ``sort`` names a column of.
    :param sort: The column name the caller asked to sort by.
    :param order: ``"desc"`` for descending; anything else sorts ascending.
    :param sort_column: The endpoint's ``Literal`` alias of sortable columns.
    :raises ValueError: As :func:`order_by_column`, before any select is built.
    :return: The ordered outer select.
    """
    order_by_column(model, sort, order, sort_column)
    inner = stmt.subquery()
    entity = aliased(model, inner, name=model.__name__)
    model_columns = {column.name for column in inspect(model).columns}
    added = [column for column in inner.c if column.name not in model_columns]
    return select(entity, *added).order_by(
        order_by_column(entity, sort, order, sort_column)
    )
