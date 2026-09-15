"""Tests: a list endpoint's ``sort`` resolves only against its explicit allowlist.

``sort`` used to reach ``getattr(Model, sort)`` unvalidated, so an unknown
name raised ``AttributeError`` - a 500 - and any mapped attribute, a password
hash included, was a column the caller could order by. Each endpoint now
declares its sortable columns as a tuple that types ``sort`` in its
query-params model (a 422 for anything else) and that ``order_by_column``
checks again in the controller (``mascope_backend.api.lib.sorting``).
"""

import pytest
from fastapi.routing import iter_route_contexts
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import JSON, inspect
from sqlalchemy.dialects import postgresql

from mascope_backend.api.lib.sorting import order_by_column
from mascope_backend.api.models.attribute_templates.attribute_template_pydantic_model import (
    ATTRIBUTE_TEMPLATE_SORT_COLUMNS,
    GetAttributeTemplatesQueryParams,
)
from mascope_backend.api.models.dataset.dataset_pydantic_model import (
    DATASET_SORT_COLUMNS,
    GetDatasetsQueryParams,
)
from mascope_backend.api.models.ionization_mechanisms.ionization_mechanism_pydantic_model import (
    IONIZATION_MECHANISM_SORT_COLUMNS,
    GetIonizationMechanismsQueryParams,
)
from mascope_backend.api.models.match.collections.match_collection_pydantic_model import (
    MATCH_COLLECTION_SORT_COLUMNS,
    GetMatchCollectionsQueryParams,
)
from mascope_backend.api.models.match.compounds.match_compound_pydantic_model import (
    MATCH_COMPOUND_SORT_COLUMNS,
    GetMatchCompoundsQueryParams,
)
from mascope_backend.api.models.match.ions.match_ion_pydantic_model import (
    MATCH_ION_SORT_COLUMNS,
    GetMatchIonsQueryParams,
)
from mascope_backend.api.models.match.isotopes.match_isotopes_pydantic_model import (
    MATCH_ISOTOPE_SORT_COLUMNS,
    GetMatchesQueryParams,
)
from mascope_backend.api.models.match.samples.match_sample_pydantic_model import (
    MATCH_SAMPLE_SORT_COLUMNS,
    GetMatchSamplesQueryParams,
)
from mascope_backend.api.models.match_rating.match_rating_pydantic_model import (
    MATCH_RATING_SORT_COLUMNS,
    GetMatchRatingsQueryParams,
)
from mascope_backend.api.models.sample.batches.sample_batch_pydantic_model import (
    SAMPLE_BATCH_SORT_COLUMNS,
    GetSampleBatchesQueryParams,
)
from mascope_backend.api.models.sample.files.sample_file_pydantic_model import (
    SAMPLE_FILE_SORT_COLUMNS,
    GetRecentSampleFilesQueryParams,
    GetSampleFilesQueryParams,
)
from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
    SAMPLE_ITEM_SORT_COLUMNS,
    GetSampleItemsQueryParams,
)
from mascope_backend.api.models.samples.sample_pydantic_model import (
    SAMPLE_SORT_COLUMNS,
    GetSamplesQueryParams,
)
from mascope_backend.api.models.target.collections.target_collection_pydantic_model import (
    TARGET_COLLECTION_IN_SAMPLE_BATCH_SORT_COLUMNS,
    TARGET_COLLECTION_SORT_COLUMNS,
    GetTargetCollectionsInSampleBatchQueryParams,
    GetTargetCollectionsQueryParams,
)
from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    TARGET_COMPOUND_IN_TARGET_COLLECTION_SORT_COLUMNS,
    TARGET_COMPOUND_SORT_COLUMNS,
    GetTargetCompoundInTargetCollectionQueryParams,
    GetTargetCompoundsQueryParams,
)
from mascope_backend.api.models.target.ions.target_ion_pydantic_model import (
    TARGET_ION_SORT_COLUMNS,
    GetTargetIonsQueryParams,
)
from mascope_backend.api.models.target.isotopes.target_isotope_pydantic_model import (
    TARGET_ISOTOPE_SORT_COLUMNS,
    GetTargetIsotopesQueryParams,
)
from mascope_backend.api.new.instrument_configs.schemas import (
    INSTRUMENT_CONFIG_SORT_COLUMNS,
    GetInstrumentConfigsQueryParams,
)
from mascope_backend.api.new.users.schemas import (
    USER_SORT_COLUMNS,
    GetUsersQueryParams,
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


#: (query-params model, the mapped class its controller orders, its allowlist)
ALLOWLISTS = [
    (
        GetAttributeTemplatesQueryParams,
        AttributeTemplate,
        ATTRIBUTE_TEMPLATE_SORT_COLUMNS,
    ),
    (GetDatasetsQueryParams, Dataset, DATASET_SORT_COLUMNS),
    (
        GetIonizationMechanismsQueryParams,
        IonizationMechanism,
        IONIZATION_MECHANISM_SORT_COLUMNS,
    ),
    (GetMatchCollectionsQueryParams, MatchCollection, MATCH_COLLECTION_SORT_COLUMNS),
    (GetMatchCompoundsQueryParams, MatchCompound, MATCH_COMPOUND_SORT_COLUMNS),
    (GetMatchIonsQueryParams, MatchIon, MATCH_ION_SORT_COLUMNS),
    (GetMatchesQueryParams, MatchIsotope, MATCH_ISOTOPE_SORT_COLUMNS),
    (GetMatchSamplesQueryParams, MatchSample, MATCH_SAMPLE_SORT_COLUMNS),
    (GetMatchRatingsQueryParams, MatchRating, MATCH_RATING_SORT_COLUMNS),
    (GetSampleBatchesQueryParams, SampleBatch, SAMPLE_BATCH_SORT_COLUMNS),
    (GetSampleFilesQueryParams, SampleFile, SAMPLE_FILE_SORT_COLUMNS),
    (GetRecentSampleFilesQueryParams, SampleFile, SAMPLE_FILE_SORT_COLUMNS),
    (GetSampleItemsQueryParams, SampleItem, SAMPLE_ITEM_SORT_COLUMNS),
    (GetSamplesQueryParams, Sample, SAMPLE_SORT_COLUMNS),
    (
        GetTargetCollectionsQueryParams,
        TargetCollection,
        TARGET_COLLECTION_SORT_COLUMNS,
    ),
    (
        GetTargetCollectionsInSampleBatchQueryParams,
        TargetCollectionInSampleBatch,
        TARGET_COLLECTION_IN_SAMPLE_BATCH_SORT_COLUMNS,
    ),
    (GetTargetCompoundsQueryParams, TargetCompound, TARGET_COMPOUND_SORT_COLUMNS),
    (
        GetTargetCompoundInTargetCollectionQueryParams,
        TargetCompoundInTargetCollection,
        TARGET_COMPOUND_IN_TARGET_COLLECTION_SORT_COLUMNS,
    ),
    (GetTargetIonsQueryParams, TargetIon, TARGET_ION_SORT_COLUMNS),
    (GetTargetIsotopesQueryParams, TargetIsotope, TARGET_ISOTOPE_SORT_COLUMNS),
    (
        GetInstrumentConfigsQueryParams,
        InstrumentFunction,
        INSTRUMENT_CONFIG_SORT_COLUMNS,
    ),
    (GetUsersQueryParams, User, USER_SORT_COLUMNS),
]

#: What the INJ-02 pentest control sends, plus names that are attributes of
#: every mapped class without being columns.
HOSTILE_SORTS = ["1' OR '1'='1", "__class__", "metadata", "registry", ""]


def _ids(allowlist):
    return allowlist[0].__name__


@pytest.mark.parametrize("allowlist", ALLOWLISTS, ids=_ids)
def test_every_allowlisted_name_is_an_orderable_column(allowlist):
    """A name that is not a scalar column would still be a 500 once ordered."""
    _params, model, sortable = allowlist
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
    params, model, sortable = allowlist
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
    """/api/users is open to every active user, who sees ids and usernames only.
    Ordering by a credential or an email would be an oracle over those values."""
    for column in ("hashed_password", "mfa_secret", "email", "is_superuser"):
        assert column not in USER_SORT_COLUMNS
        with pytest.raises(ValidationError):
            GetUsersQueryParams(sort=column)


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
    """A route that adds a free-form ``sort`` later fails here, not in a pentest."""
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
        order_by_column(User, sort, "asc", USER_SORT_COLUMNS)
    # The refusal does not reflect what the caller sent.
    if sort:
        assert sort not in str(excinfo.value)


@pytest.mark.parametrize(
    ("order", "expected"),
    [("desc", "username DESC"), ("asc", "username ASC"), (None, "username ASC")],
)
def test_order_by_column_orders_the_named_column(order, expected):
    """Anything but "desc" sorts ascending, as the controllers always have."""
    clause = order_by_column(User, "username", order, USER_SORT_COLUMNS)
    compiled = str(clause.compile(dialect=postgresql.dialect()))
    assert compiled.endswith(expected)
