"""
Hermetic unit tests for the unified name-matching contract in name -> ID
resolution (``resolve_id`` and ``SamplesResource._resolve_batch_ids``): a plain
string is a case-insensitive literal substring, a compiled ``re.Pattern`` is a
regex, and legacy raw-regex strings fall back with a DeprecationWarning.
"""

import re
import warnings
from contextlib import contextmanager
from types import SimpleNamespace

import pandas as pd
import pytest

from mascope_sdk._resolve import resolve_id
from mascope_sdk.resources.samples import SamplesResource


# A name full of regex metacharacters, as seen in real target-collection names.
METACHAR_NAME = "^Nitrate (Ur+ CIMS) m/z 40-600"

DATASETS = pd.DataFrame(
    {
        "dataset_id": ["ds-1", "ds-2", "ds-3", "ds-4"],
        "dataset_name": [
            "Blank 2026-01",
            "Uronium 2026-02",
            METACHAR_NAME,
            "Calibration",
        ],
    }
)


def _resolve(value, items=DATASETS):
    return resolve_id(
        value,
        items,
        id_column="dataset_id",
        name_column="dataset_name",
        entity_label="dataset",
    )


@contextmanager
def _no_warnings():
    """Fail the test if the matched path emits any warning (no shim expected)."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        yield


class TestResolveId:
    def test_plain_string_matches_as_case_insensitive_literal_substring(self):
        with _no_warnings():
            assert _resolve("uronium") == "ds-2"

    def test_plain_string_with_regex_metacharacters_matches_literally(self):
        # Previously this was fed to str.contains as a raw regex: "(Ur+ CIMS)"
        # became a group with a quantifier and the name never matched.
        with _no_warnings():
            assert _resolve(METACHAR_NAME) == "ds-3"

    def test_exact_id_match_wins(self):
        assert _resolve("ds-4") == "ds-4"

    def test_compiled_pattern_matches_as_regex(self):
        # Previously crashed inside pandas: "cannot set case for compiled regex".
        assert _resolve(re.compile(r"^Calib", re.IGNORECASE)) == "ds-4"

    def test_compiled_pattern_case_comes_from_flags(self):
        with pytest.raises(ValueError, match="No dataset matching"):
            _resolve(re.compile(r"^calib"))

    def test_no_match_error_lists_available_names(self):
        with pytest.raises(ValueError, match=re.escape("Calibration")):
            _resolve("does-not-exist")

    def test_multiple_match_error_lists_matched_names(self):
        with pytest.raises(ValueError, match="Multiple datasets matching '2026'"):
            _resolve("2026")

    def test_compiled_pattern_error_message_shows_pattern_text(self):
        with pytest.raises(ValueError, match=re.escape("'^zzz'")):
            _resolve(re.compile(r"^zzz"))

    def test_raw_regex_string_falls_back_with_deprecation_warning(self):
        # "Calib.*ion" only matches when treated as a regex; the legacy
        # behavior is kept behind a DeprecationWarning.
        with pytest.warns(DeprecationWarning, match="literal substrings"):
            assert _resolve("Calib.*ion") == "ds-4"

    def test_pre_escaped_string_resolves_via_fallback(self):
        # The peaky incident: callers that re.escape()d strings for the old
        # raw-regex contract must not silently match nothing.
        with pytest.warns(DeprecationWarning):
            assert _resolve(re.escape(METACHAR_NAME)) == "ds-3"

    def test_invalid_regex_string_is_just_an_unmatched_literal(self):
        # "(unclosed" is not a valid regex; the fallback must not raise
        # re.error, only the normal not-found ValueError.
        with pytest.raises(ValueError, match="No dataset matching"):
            _resolve("(unclosed")

    def test_empty_items_raises(self):
        with pytest.raises(ValueError, match="No datasets found"):
            _resolve("anything", items=None)


BATCHES = pd.DataFrame(
    {
        "sample_batch_id": ["b-1", "b-2", "b-3", "b-4"],
        "sample_batch_name": [
            "Blank 1",
            "Blank 2",
            "Sample (A)",
            METACHAR_NAME,
        ],
    }
)


@pytest.fixture
def samples_resource():
    client = SimpleNamespace(batches=SimpleNamespace(list=lambda dataset: BATCHES))
    return SamplesResource(client)


class TestResolveBatchIds:
    def test_plain_string_selects_all_literal_substring_matches(self, samples_resource):
        with _no_warnings():
            ids = samples_resource._resolve_batch_ids("blank", dataset="ds")
        assert ids == ["b-1", "b-2"]

    def test_plain_string_with_regex_metacharacters_matches_literally(
        self, samples_resource
    ):
        # "(A)" as a raw regex is a group matching "A"; as a literal it must
        # select only the batch actually named with parentheses.
        with _no_warnings():
            assert samples_resource._resolve_batch_ids("(A)", dataset="ds") == ["b-3"]
        with _no_warnings():
            ids = samples_resource._resolve_batch_ids(METACHAR_NAME, dataset="ds")
        assert ids == ["b-4"]

    def test_exact_id_match_wins(self, samples_resource):
        assert samples_resource._resolve_batch_ids("b-2", dataset="ds") == ["b-2"]

    def test_compiled_pattern_matches_as_regex(self, samples_resource):
        # Previously crashed inside pandas: "cannot set case for compiled regex".
        pattern = re.compile(r"blank [12]", re.IGNORECASE)
        assert samples_resource._resolve_batch_ids(pattern, dataset="ds") == [
            "b-1",
            "b-2",
        ]

    def test_raw_regex_string_falls_back_with_deprecation_warning(
        self, samples_resource
    ):
        with pytest.warns(DeprecationWarning, match="literal substrings"):
            ids = samples_resource._resolve_batch_ids("Blank 1|Blank 2", dataset="ds")
        assert ids == ["b-1", "b-2"]

    def test_pre_escaped_string_resolves_via_fallback(self, samples_resource):
        with pytest.warns(DeprecationWarning):
            ids = samples_resource._resolve_batch_ids(
                re.escape(METACHAR_NAME), dataset="ds"
            )
        assert ids == ["b-4"]

    def test_no_match_error_lists_available_batches(self, samples_resource):
        with pytest.raises(ValueError, match=re.escape("Sample (A)")):
            samples_resource._resolve_batch_ids("does-not-exist", dataset="ds")

    def test_compiled_pattern_error_message_shows_pattern_text(self, samples_resource):
        with pytest.raises(ValueError, match=re.escape("'^zzz'")):
            samples_resource._resolve_batch_ids(re.compile(r"^zzz"), dataset="ds")
