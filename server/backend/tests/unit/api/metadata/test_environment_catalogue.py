"""The environments Mascope ships are ones the registry would take.

A start creates them without going through the routes, so nothing else checks
that each names a shipped chemistry context by its own name and regimes a
reference list can declare, under a key the routes would accept.
"""

import re

import pytest

from mascope_backend.api.new.metadata.environment_catalogue import (
    SHIPPED_ENVIRONMENTS,
)
from mascope_backend.api.new.metadata.environment_schema import (
    ENVIRONMENT_KEY_PATTERN,
    EnvironmentCreate,
)
from mascope_backend.api.new.peak_assignments.config import named_chemistry_context
from mascope_reference.peaklist import REGIMES
from mascope_reference.seed import catalogue


@pytest.mark.parametrize("shipped", SHIPPED_ENVIRONMENTS, ids=lambda s: s.key)
def test_a_shipped_environment_is_one_the_registry_takes(shipped):
    assert re.fullmatch(ENVIRONMENT_KEY_PATTERN, shipped.key)
    assert len(shipped.environment_id) == 16
    if shipped.chemistry_context is not None:
        assert named_chemistry_context(shipped.chemistry_context) == (
            shipped.chemistry_context
        )
    assert set(shipped.regimes) <= set(REGIMES)
    body = EnvironmentCreate(
        key=shipped.key,
        label=shipped.label,
        chemistry_context=shipped.chemistry_context,
        regimes=list(shipped.regimes),
    )
    assert body.chemistry_context == shipped.chemistry_context
    assert body.regimes == list(shipped.regimes)


def test_the_shipped_set_has_one_row_per_key_and_id():
    keys = [shipped.key for shipped in SHIPPED_ENVIRONMENTS]
    ids = [shipped.environment_id for shipped in SHIPPED_ENVIRONMENTS]
    assert len(set(keys)) == len(keys)
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("shipped", SHIPPED_ENVIRONMENTS, ids=lambda s: s.key)
def test_every_regime_a_shipped_environment_names_selects_a_shipped_list(shipped):
    declared = {regime for pl in catalogue() for regime in pl.applies_to_contexts}
    assert set(shipped.regimes) <= declared
