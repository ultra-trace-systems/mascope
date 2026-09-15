"""Tests: a list endpoint's ``sort`` resolves only against its explicit allowlist.

Each endpoint spells its sortable columns once, as a ``Literal`` alias that
types ``sort`` in its query-params model (a 422 for anything else) and that its
controller hands to ``order_by_column`` (``mascope_backend.api.lib.sorting``).
An unlisted name is refused rather than looked up on the model, so it can be
neither a 500 nor an order over a column the caller cannot read.
"""

from typing import get_args

import pytest
from fastapi.routing import iter_route_contexts
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import JSON, inspect, select
from sqlalchemy.dialects import postgresql

from mascope_backend.api.lib.sorting import order_by_column, order_distinct_on
from mascope_backend.api.models.attribute_templates.attribute_template_pydantic_model import (
    AttributeTemplateSortColumn,
    GetAttributeTemplatesQueryParams,
)
from mascope_backend.api.models.dataset.dataset_pydantic_model import (
    DatasetSortColumn,
    GetDatasetsQueryParams,
)
from mascope_backend.api.models.ionization_mechanisms.ionization_mechanism_pydantic_model import (
    GetIonizationMechanismsQueryParams,
    IonizationMechanismSortColumn,
)
from mascope_backend.api.models.match.collections.match_collection_pydantic_model import (
    GetMatchCollectionsQueryParams,
    MatchCollectionSortColumn,
)
from mascope_backend.api.models.match.compounds.match_compound_pydantic_model import (
    GetMatchCompoundsQueryParams,
    MatchCompoundSortColumn,
)
from mascope_backend.api.models.match.ions.match_ion_pydantic_model import (
    GetMatchIonsQueryParams,
    MatchIonSortColumn,
)
from mascope_backend.api.models.match.isotopes.match_isotopes_pydantic_model import (
    GetMatchesQueryParams,
    MatchIsotopeSortColumn,
)
from mascope_backend.api.models.match.samples.match_sample_pydantic_model import (
    GetMatchSamplesQueryParams,
    MatchSampleSortColumn,
)
from mascope_backend.api.models.match_rating.match_rating_pydantic_model import (
    GetMatchRatingsQueryParams,
    MatchRatingSortColumn,
)
from mascope_backend.api.models.sample.batches.sample_batch_pydantic_model import (
    GetSampleBatchesQueryParams,
    SampleBatchSortColumn,
)
from mascope_backend.api.models.sample.files.sample_file_pydantic_model import (
    GetRecentSampleFilesQueryParams,
    GetSampleFilesQueryParams,
    SampleFileSortColumn,
)
from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
    GetSampleItemsQueryParams,
    SampleItemSortColumn,
)
from mascope_backend.api.models.samples.sample_pydantic_model import (
    GetSamplesQueryParams,
    SampleSortColumn,
)
from mascope_backend.api.models.target.collections.target_collection_pydantic_model import (
    GetTargetCollectionsInSampleBatchQueryParams,
    GetTargetCollectionsQueryParams,
    TargetCollectionInSampleBatchSortColumn,
    TargetCollectionSortColumn,
)
from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    GetTargetCompoundInTargetCollectionQueryParams,
    GetTargetCompoundsQueryParams,
    TargetCompoundInTargetCollectionSortColumn,
    TargetCompoundSortColumn,
)
from mascope_backend.api.models.target.ions.target_ion_pydantic_model import (
    GetTargetIonsQueryParams,
    TargetIonSortColumn,
)
from mascope_backend.api.models.target.isotopes.target_isotope_pydantic_model import (
    GetTargetIsotopesQueryParams,
    TargetIsotopeSortColumn,
)
from mascope_backend.api.new.instrument_configs.schemas import (
    GetInstrumentConfigsQueryParams,
    InstrumentConfigSortColumn,
)
from mascope_backend.api.new.users.schemas import (
    GetUsersQueryParams,
    PublicUserSortColumn,
    UserSortColumn,
)
from mascope_backend.app.fast import fast
from mascope_backend.db import (
    AttributeTemplate,
    Dataset,
    InstrumentFunction,
    IonizationMechanism,
    MatchCollection,
    MatchCompound,
    MatchIon,
    MatchIsotope,
    MatchRating,
    MatchSample,
    Sample,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCollection,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    TargetIsotope,
    User,
)


#: (query-params model, the mapped class its controller orders, its alias)
ALLOWLISTS = [
    (
        GetAttributeTemplatesQueryParams,
        AttributeTemplate,
        AttributeTemplateSortColumn,
    ),
    (GetDatasetsQueryParams, Dataset, DatasetSortColumn),
    (
        GetIonizationMechanismsQueryParams,
        IonizationMechanism,
        IonizationMechanismSortColumn,
    ),
    (GetMatchCollectionsQueryParams, MatchCollection, MatchCollectionSortColumn),
    (GetMatchCompoundsQueryParams, MatchCompound, MatchCompoundSortColumn),
    (GetMatchIonsQueryParams, MatchIon, MatchIonSortColumn),
    (GetMatchesQueryParams, MatchIsotope, MatchIsotopeSortColumn),
    (GetMatchSamplesQueryParams, MatchSample, MatchSampleSortColumn),
    (GetMatchRatingsQueryParams, MatchRating, MatchRatingSortColumn),
    (GetSampleBatchesQueryParams, SampleBatch, SampleBatchSortColumn),
    (GetSampleFilesQueryParams, SampleFile, SampleFileSortColumn),
    (GetRecentSampleFilesQueryParams, SampleFile, SampleFileSortColumn),
    (GetSampleItemsQueryParams, SampleItem, SampleItemSortColumn),
    (GetSamplesQueryParams, Sample, SampleSortColumn),
    (
        GetTargetCollectionsQueryParams,
        TargetCollection,
        TargetCollectionSortColumn,
    ),
    (
        GetTargetCollectionsInSampleBatchQueryParams,
        TargetCollectionInSampleBatch,
        TargetCollectionInSampleBatchSortColumn,
    ),
    (GetTargetCompoundsQueryParams, TargetCompound, TargetCompoundSortColumn),
    (
        GetTargetCompoundInTargetCollectionQueryParams,
        TargetCompoundInTargetCollection,
        TargetCompoundInTargetCollectionSortColumn,
    ),
    (GetTargetIonsQueryParams, TargetIon, TargetIonSortColumn),
    (GetTargetIsotopesQueryParams, TargetIsotope, TargetIsotopeSortColumn),
    (
        GetInstrumentConfigsQueryParams,
        InstrumentFunction,
        InstrumentConfigSortColumn,
    ),
    (GetUsersQueryParams, User, UserSortColumn),
]

#: What the INJ-02 pentest control sends, plus names that are attributes of
#: every mapped class without being columns.
HOSTILE_SORTS = ["1' OR '1'='1", "__class__", "metadata", "registry", ""]


def _ids(allowlist):
    return allowlist[0].__name__


@pytest.mark.parametrize("allowlist", ALLOWLISTS, ids=_ids)
def test_every_allowlisted_name_is_an_orderable_column(allowlist):
    """A name that is not a scalar column would still be a 500 once ordered."""
    _params, model, sort_column = allowlist
    sortable = get_args(sort_column)
    columns = {attr.key: attr.columns[0] for attr in inspect(model).column_attrs}
    assert len(set(sortable)) == len(sortable)
    for name in sortable:
        assert name in columns, f"{model.__name__} has no column '{name}'"
        assert not isinstance(columns[name].type, JSON), (
            f"{model.__name__}.{name} is JSON, which Postgres cannot order"
        )


@pytest.mark.parametrize("allowlist", ALLOWLISTS, ids=_ids)
def test_query_params_accept_exactly_the_allowlist(allowlist):
    """Each allowlisted name validates, the default is one of them, and a
    hostile value or a column outside the list is refused.

    Validated against the field's own type, so a model with other required
    parameters cannot make a refusal pass for the wrong reason."""
    params, model, sort_column = allowlist
    sortable = get_args(sort_column)
    field = params.model_fields["sort"]
    sort_type = TypeAdapter(field.annotation)
    for name in sortable:
        assert sort_type.validate_python(name) == name
    assert field.default is None or field.default in sortable

    outside = [
        attr.key for attr in inspect(model).column_attrs if attr.key not in sortable
    ]
    for value in HOSTILE_SORTS + outside:
        with pytest.raises(ValidationError):
            sort_type.validate_python(value)


def test_users_cannot_be_sorted_by_what_a_caller_cannot_read():
    """/api/users is open to every active user. Ordering by a credential or an
    email would be an oracle over values no listing returns."""
    for column in ("hashed_password", "mfa_secret", "email", "is_superuser"):
        assert column not in get_args(UserSortColumn)
        with pytest.raises(ValidationError):
            GetUsersQueryParams(sort=column)


def test_callers_below_admin_sort_by_the_public_columns_only():
    """UserPublic returns id and username; that is all such a caller may order by."""
    assert get_args(PublicUserSortColumn) == ("id", "username")
    assert set(get_args(PublicUserSortColumn)) <= set(get_args(UserSortColumn))


def _sort_parameters():
    """Every ``sort`` query parameter the OpenAPI document declares."""
    paths = fast.openapi()["paths"]
    for route in iter_route_contexts(fast.routes):
        if not route.methods or not getattr(route, "include_in_schema", False):
            continue
        for method in route.methods:
            operation = paths[route.path_format][method.lower()]
            for parameter in operation.get("parameters", []):
                if parameter["name"] == "sort" and parameter["in"] == "query":
                    yield f"{method} {route.path_format}", parameter["schema"]


def test_every_sort_query_parameter_is_a_closed_set():
    """A route with a free-form ``sort`` fails here, not in a pentest."""
    open_ended = []
    checked = 0
    for operation, schema in _sort_parameters():
        checked += 1
        variants = schema.get("anyOf", [schema])
        if not any("enum" in variant for variant in variants) or any(
            variant.get("type") == "string" and "enum" not in variant
            for variant in variants
        ):
            open_ended.append(operation)
    assert checked >= len(ALLOWLISTS) - 1
    assert open_ended == []


@pytest.mark.parametrize("sort", ["hashed_password", *HOSTILE_SORTS])
def test_order_by_column_refuses_names_outside_the_allowlist(sort):
    """The controller-side check holds for a caller that skipped the query model,
    including for a real column of the model."""
    with pytest.raises(ValueError, match="Unsupported sort column") as excinfo:
        order_by_column(User, sort, "asc", UserSortColumn)
    # The refusal does not reflect what the caller sent.
    if sort:
        assert sort not in str(excinfo.value)


@pytest.mark.parametrize(
    ("order", "expected"),
    [("desc", "username DESC"), ("asc", "username ASC"), (None, "username ASC")],
)
def test_order_by_column_orders_the_named_column(order, expected):
    """Anything but "desc" sorts ascending."""
    clause = order_by_column(User, "username", order, UserSortColumn)
    compiled = str(clause.compile(dialect=postgresql.dialect()))
    assert compiled.endswith(expected)


def test_refusal_message_leaves_the_final_period_to_process_exception():
    """process_exception appends a period; a message ending in one reads ".."."""
    with pytest.raises(ValueError) as excinfo:
        order_by_column(User, "email", "asc", UserSortColumn)
    assert not str(excinfo.value).endswith(".")


def test_order_distinct_on_orders_outside_the_distinct_select():
    """Postgres needs DISTINCT ON keys to lead ORDER BY, so the requested order
    goes on an outer select that keeps the entity's name and the added columns."""
    inner = (
        select(TargetIon, IonizationMechanism.ionization_mechanism)
        .join(
            IonizationMechanism,
            IonizationMechanism.ionization_mechanism_id
            == TargetIon.ionization_mechanism_id,
        )
        .distinct(TargetIon.target_ion_id)
    )
    outer = order_distinct_on(
        inner, TargetIon, "target_ion_formula", "desc", TargetIonSortColumn
    )
    assert [column["name"] for column in outer.column_descriptions] == [
        "TargetIon",
        "ionization_mechanism",
    ]
    sql = str(outer.compile(dialect=postgresql.dialect()))
    subquery, outer_tail = sql.rsplit(")", 1)
    assert "DISTINCT ON" in subquery
    assert "ORDER BY" not in subquery
    assert outer_tail.strip().endswith("target_ion_formula DESC")


def test_order_distinct_on_refuses_before_building_a_select():
    with pytest.raises(ValueError, match="Unsupported sort column"):
        order_distinct_on(
            select(TargetIon).distinct(TargetIon.target_ion_id),
            TargetIon,
            "filter_params",
            "asc",
            TargetIonSortColumn,
        )
