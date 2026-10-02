"""
Unit tests: the bounds the method binding report reads from the environment.

The report refuses a bound it cannot read, where ``prune_peak_assignment_runs``
falls back to its default - a nightly prune that keeps running on the
documented policy beats one that stops, while a report's bounds are part of
what it claims about itself. A run that quietly read 20,000 files after being
asked for 100,000 is a wrong report rather than a slow one, and the operator
who typed it would have no way to tell.

No database: this is the one piece of the script that decides something before
anything is read.
"""

import pytest

from mascope_backend.db.scripts.report_method_binding_disagreements import (
    _DEFAULT_FILES,
    _int_env,
)


FILES = "BINDING_REPORT_FILES"


def test_an_unset_bound_is_its_default(monkeypatch):
    monkeypatch.delenv(FILES, raising=False)
    assert _int_env(FILES, _DEFAULT_FILES, 1) == _DEFAULT_FILES


def test_an_empty_bound_is_its_default(monkeypatch):
    # What an unset variable looks like once a shell has expanded it.
    monkeypatch.setenv(FILES, "")
    assert _int_env(FILES, _DEFAULT_FILES, 1) == _DEFAULT_FILES


def test_a_bound_that_is_read_is_used(monkeypatch):
    monkeypatch.setenv(FILES, "100000")
    assert _int_env(FILES, _DEFAULT_FILES, 1) == 100000


def test_a_bound_that_is_not_a_number_is_refused(monkeypatch):
    # A letter O for a zero, which is how this is mistyped.
    monkeypatch.setenv(FILES, "2O000")
    with pytest.raises(ValueError) as excinfo:
        _int_env(FILES, _DEFAULT_FILES, 1)
    # The variable by name and the default it would otherwise have used: the
    # operator sees what they typed, not an inner parameter name.
    assert FILES in str(excinfo.value)
    assert str(_DEFAULT_FILES) in str(excinfo.value)


def test_a_bound_of_zero_is_refused(monkeypatch):
    # Parseable, and an instruction to read nothing. Substituting the default
    # would report on 20,000 files to somebody who asked for none.
    monkeypatch.setenv(FILES, "0")
    with pytest.raises(ValueError, match=FILES):
        _int_env(FILES, _DEFAULT_FILES, 1)
