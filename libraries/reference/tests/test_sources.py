"""Source registry tests."""

import pytest

from mascope_reference.scope import MIRROR_WINDOW, UNBOUNDED
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
        "peaklist",
    }


def test_get_adapter_returns_named_adapter():
    adapter = get_adapter("pubchem")
    assert adapter.name == "pubchem"
    assert adapter.license == "public-domain"


def test_unknown_source_raises_with_choices():
    with pytest.raises(KeyError) as excinfo:
        get_adapter("nope")
    assert "Available" in str(excinfo.value)


#: The registered sources a person authors rather than downloads.
_LISTS = {"custom", "peaklist"}


@pytest.mark.parametrize("name", available_sources())
def test_a_database_loads_at_the_mirror_window_and_a_list_unbounded(name):
    # A cited or hand-authored list is its own bound; a database mirror holds
    # formulas no sample carries, so it loads at the window every source was
    # matched inside before a source carried its own.
    expected = UNBOUNDED if name in _LISTS else MIRROR_WINDOW
    assert get_adapter(name).known_window == expected
