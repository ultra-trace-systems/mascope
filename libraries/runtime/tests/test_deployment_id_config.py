"""
Tests for `backend.deployment_id`, the configured name of a deployment.

Unset is the ordinary case: the backend then generates an id and keeps it with
the data. What is pinned here is what an operator may write instead. The id is
copied into the provenance of every export the deployment makes, where nothing
can correct it afterwards, so a malformed one is refused when the config loads
- naming the setting - rather than shipped.
"""

import pytest
from pydantic import ValidationError

from mascope_runtime.config import DEPLOYMENT_ID_PATTERN, BackendConfig


def _backend(**kwargs) -> BackendConfig:
    return BackendConfig(name="backend", **kwargs)


def test_unset_by_default():
    """Unset means the backend's own generated id is used."""
    assert _backend().deployment_id is None


@pytest.mark.parametrize(
    "value", ["example-lab", "lab_2", "site.eu-1", "A", "K3J9xQ2mP0aB7cD1"]
)
def test_a_well_formed_id_is_kept(value):
    assert _backend(deployment_id=value).deployment_id == value


def test_surrounding_whitespace_is_stripped():
    assert _backend(deployment_id="  example-lab \n").deployment_id == "example-lab"


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_id_reads_as_unset(value):
    """`deployment_id = ""` is someone clearing the setting, not naming the
    deployment with nothing."""
    assert _backend(deployment_id=value).deployment_id is None


@pytest.mark.parametrize(
    "value",
    [
        "example lab",  # a space would need quoting in a file name or URL
        "lab:1",  # would be ambiguous as the prefix of another identifier
        "lab/1",
        'lab"1',
        "-lab",  # reads as an option on a command line
        ".lab",  # a hidden file name
        "l" * 65,
        "laboratoire-é",  # ASCII only
    ],
)
def test_a_malformed_id_is_refused_naming_the_setting(value):
    with pytest.raises(ValidationError) as error:
        _backend(deployment_id=value)
    assert "backend.deployment_id" in str(error.value)


def test_the_longest_id_allowed_is_64_characters():
    value = "l" * 64
    assert _backend(deployment_id=value).deployment_id == value


def test_a_generated_nanoid_satisfies_the_pattern():
    """The backend's generated ids (16 alphanumeric characters) are held to the
    same rule as configured ones when they are read back."""
    assert DEPLOYMENT_ID_PATTERN.fullmatch("I6EFYnLih5emE6Iw")
