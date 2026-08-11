"""Source registry tests."""

import pytest

from mascope_reference.sources import available_sources, get_adapter


def test_registry_lists_expected_sources():
    assert set(available_sources()) == {
        "pubchem",
        "comptox",
        "chebi",
        "hmdb",
        "lipidmaps",
        "coconut",
        "norman",
        "custom",
    }


def test_get_adapter_returns_named_adapter():
    adapter = get_adapter("pubchem")
    assert adapter.name == "pubchem"
    assert adapter.license == "public-domain"


def test_unknown_source_raises_with_choices():
    with pytest.raises(KeyError) as excinfo:
        get_adapter("nope")
    assert "Available" in str(excinfo.value)
