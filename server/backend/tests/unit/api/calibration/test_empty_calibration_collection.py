"""Calibrating with nothing to calibrate against stops before it fits.

An ionization mode may name a calibration collection that holds no compounds,
or whose compounds have no isotope for the mode's mechanisms at the
instrument's resolution. Both used to run a fit anyway:

- the empty compound list was dropped from the isotope query as falsy, so the
  sample was fitted against every target isotope in the database - other
  collections, other workspaces - and an accidental fit could be applied;
- an empty isotope result built a column-less DataFrame and crashed the
  request with an AttributeError.

Both now stop where the no-peaks case stops: a warning on the handler, no fit
and no stats, so nothing is written and the caller reports the warning.

The database is not involved: the two target lookups are patched in the module
the handler imported them into.
"""

from unittest.mock import AsyncMock, patch

import pytest
from conftest import get_test_calibration_handler

from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    EMPTY_CALIBRATION_COLLECTION_WARNING,
    NO_CALIBRATION_ISOTOPES_WARNING,
    BaseCalibrationHandler,
)


_LIB = "mascope_backend.api.controllers.calibration.lib.calibration_mz_fit"

ISOTOPE_ROW = {
    "target_isotope_id": "iso-1",
    "target_ion_id": "ion-1",
    "mz": 100.0,
    "relative_abundance": 1.0,
    "resolution": "HIGH",
}


def _patch_targets(compound_ids, isotope_rows):
    """Patch the collection and isotope lookups the resolver calls.

    :return: The two context managers plus the isotope-query mock, so a test
        can assert on the arguments the query was given - or that it was never
        reached at all.
    """
    compounds = {
        "data": [{"target_compound_id": compound_id} for compound_id in compound_ids]
    }
    isotopes_mock = AsyncMock(return_value={"data": list(isotope_rows)})
    return (
        patch(
            f"{_LIB}.get_target_compound_in_target_collection",
            AsyncMock(return_value=compounds),
        ),
        patch(f"{_LIB}.get_target_isotopes", isotopes_mock),
        isotopes_mock,
    )


@pytest.mark.parametrize("filename", ["orbitrap", "tofwerk"])
class TestResolveCalibrationIsotopes:
    """The resolver decides whether there is anything to calibrate against."""

    @pytest.mark.asyncio
    async def test_empty_collection_never_queries_isotopes(self, filename):
        """No compounds: warn, and keep the empty list out of the query.

        The query is where the damage was done - an empty compound filter
        there selected the whole isotope table - so the guard sits in front
        of it.
        """
        handler = get_test_calibration_handler(filename, "+")
        compounds_patch, isotopes_patch, isotopes_mock = _patch_targets([], [])

        with compounds_patch, isotopes_patch:
            resolved = await handler._resolve_calibration_isotopes()

        assert resolved is None
        assert handler.warning == EMPTY_CALIBRATION_COLLECTION_WARNING
        assert handler.fit_result is None
        assert handler.stats is None
        assert isotopes_mock.await_count == 0

    @pytest.mark.asyncio
    async def test_collection_without_isotopes_warns_instead_of_crashing(
        self, filename
    ):
        """Compounds but no isotopes for this mode: a warning, not a 500.

        Matching used to build its DataFrame from the empty result and fail on
        the missing ``mz`` column.
        """
        handler = get_test_calibration_handler(filename, "+")
        compounds_patch, isotopes_patch, isotopes_mock = _patch_targets(
            ["compound-1"], []
        )

        with compounds_patch, isotopes_patch:
            resolved = await handler._resolve_calibration_isotopes()

        assert resolved is None
        assert handler.warning == NO_CALIBRATION_ISOTOPES_WARNING
        assert handler.fit_result is None
        assert handler.stats is None
        assert isotopes_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_populated_collection_resolves_its_own_isotopes(self, filename):
        """The guard only fires on emptiness, and only ever asks for the
        collection's own compounds."""
        handler = get_test_calibration_handler(filename, "+")
        compounds_patch, isotopes_patch, isotopes_mock = _patch_targets(
            ["compound-1"], [ISOTOPE_ROW]
        )

        with compounds_patch, isotopes_patch:
            resolved = await handler._resolve_calibration_isotopes()

        assert handler.warning is None
        assert list(resolved["target_isotope_id"]) == ["iso-1"]
        assert isotopes_mock.await_args.kwargs["target_compound_ids"] == ["compound-1"]


@pytest.mark.parametrize("filename", ["orbitrap", "tofwerk"])
@pytest.mark.asyncio
async def test_fit_stops_on_an_empty_collection(filename):
    """Both instrument handlers abandon the fit and report the warning."""
    handler = get_test_calibration_handler(filename, "+")
    compounds_patch, isotopes_patch, _ = _patch_targets([], [])
    match_mock = AsyncMock(return_value=(None, None))

    with (
        patch.object(BaseCalibrationHandler, "_has_peaks", True),
        patch.object(
            BaseCalibrationHandler, "_match_calibration_compounds", match_mock
        ),
        compounds_patch,
        isotopes_patch,
    ):
        await handler.fit()

    assert handler.warning == EMPTY_CALIBRATION_COLLECTION_WARNING
    assert handler.fit_result is None
    assert handler.stats is None
    # Nothing was matched, so nothing can be fitted or applied.
    assert match_mock.await_count == 0
