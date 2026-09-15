"""
Tests for `backend.reference_licenses`, the Stage A reference-licence gate.

The property that matters most here is the *default*: unset must mean no
gating. The reference mirror carries a per-record licence and peak assignment's
database stage can be restricted to a subset of them, but narrowing that set
makes assignment find less with nothing in the UI to say why - so it has to be
something an operator opts into, never something they inherit on upgrade.

The rest pins the normalization (the value is folded into a cache key and
recorded on every run, so it must not depend on typing order) and the refusal
of an empty list, which reads as "no restriction" but would block every record.
"""

import pytest
from pydantic import ValidationError

from mascope_runtime.config import BackendConfig


def _backend(**kwargs) -> BackendConfig:
    return BackendConfig(name="backend", **kwargs)


def test_unset_by_default_which_means_no_gating():
    """The whole point: a deployment that says nothing keeps matching
    everything, exactly as it did before the setting existed."""
    assert _backend().reference_licenses is None


def test_an_allowlist_is_kept():
    assert _backend(reference_licenses=["public-domain"]).reference_licenses == [
        "public-domain"
    ]


def test_entries_are_sorted_and_deduplicated():
    """The set is folded into the reference-isotope cache key and recorded on
    every run, so two operators who typed the same licences in a different
    order must get the same value."""
    config = _backend(
        reference_licenses=["public-domain", "CC0", "public-domain", "CC-BY-4.0"]
    )
    assert config.reference_licenses == ["CC-BY-4.0", "CC0", "public-domain"]


def test_surrounding_whitespace_is_stripped():
    config = _backend(reference_licenses=[" CC0 ", "public-domain"])
    assert config.reference_licenses == ["CC0", "public-domain"]


def test_an_empty_allowlist_is_refused():
    """`reference_licenses = []` reads as "no restriction" but would gate out
    every record, silently emptying the known set. Deleting the line is the
    documented way to disable gating."""
    with pytest.raises(ValidationError) as error:
        _backend(reference_licenses=[])
    assert "Remove the line" in str(error.value)


def test_a_list_of_only_blanks_is_refused_too():
    """Same failure, reached by a different route - it must not normalize down
    to an empty allowlist that then blocks everything."""
    with pytest.raises(ValidationError):
        _backend(reference_licenses=["", "   "])
