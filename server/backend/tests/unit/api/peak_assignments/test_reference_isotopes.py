"""Unit tests for expanding reference formulas into Stage A isotope rows.

Exercises the real target-ion / IsoSpec path (no DB): a bounded set of reference
formulas becomes matchable isotope rows carrying reference identities and no
curated target linkage. The cache tests below fake the DB edges and pin the
once-per-state property: the expansion is paid once per (reference state,
mechanism set, resolution, threshold), not once per run.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

import mascope_backend.api.new.peak_assignments.service as service
from mascope_backend.api.new.peak_assignments.service import (
    _build_reference_isotopes_df,
    _fetch_reference_known_isotopes,
)
from mascope_reference import KnownComposition, KnownIdentity


_MOD = "mascope_backend.api.new.peak_assignments.service"


def _mechanism(mech_id="m-deprot", notation="-H-", polarity="-"):
    return SimpleNamespace(
        ionization_mechanism_id=mech_id,
        ionization_mechanism=notation,
        ionization_mechanism_polarity=polarity,
    )


def _known(formula, mass, names):
    return KnownComposition(
        formula=formula,
        monoisotopic_mass=mass,
        identities=[
            KnownIdentity(
                name=n,
                source="pinene-tracers",
                license="custom",
                inchikey=None,
                source_native_id=n,
                xrefs={},
            )
            for n in names
        ],
    )


def test_expands_formula_into_reference_isotope_rows():
    known = [_known("C10H16O3", 184.1099, ["Pinonic acid", "Norpinonic acid"])]
    df = _build_reference_isotopes_df(
        known, [_mechanism()], resolution_type="HIGH", abundance_threshold=0.0
    )
    assert not df.empty
    # Shaped like the target known-isotope frame, plus the carried identities.
    for col in (
        "target_isotope_id",
        "target_ion_id",
        "mz",
        "relative_abundance",
        "resolution",
        "target_ion_formula",
        "ionization_mechanism_id",
        "target_compound_formula",
        "ionization_mechanism",
        "reference_identities",
    ):
        assert col in df.columns
    # Every reference row: no curated target, right formula/mechanism/resolution.
    assert df["target_compound_id"].isna().all()
    assert (df["target_compound_formula"] == "C10H16O3").all()
    assert (df["ionization_mechanism"] == "-H-").all()
    assert (df["resolution"] == "HIGH").all()
    # The one-to-many identities ride on each row.
    identities = df["reference_identities"].iloc[0]
    assert {i["name"] for i in identities} == {"Pinonic acid", "Norpinonic acid"}
    # Several isotopologues (M0 + 13C etc.), abundances are valid fractions.
    assert len(df) >= 2
    assert df["relative_abundance"].between(0.0, 1.0).all()


def test_resolution_and_abundance_filters():
    known = [_known("C9H14O4", 186.0892, ["Pinic acid"])]
    high = _build_reference_isotopes_df(
        known, [_mechanism()], resolution_type="HIGH", abundance_threshold=0.0
    )
    low = _build_reference_isotopes_df(
        known, [_mechanism()], resolution_type="LOW", abundance_threshold=0.0
    )
    assert not high.empty and not low.empty
    assert (high["resolution"] == "HIGH").all()
    assert (low["resolution"] == "LOW").all()
    # An abundance floor at half the base peak drops the isotope tail.
    floor = 0.5 * float(high["relative_abundance"].max())
    floored = _build_reference_isotopes_df(
        known, [_mechanism()], resolution_type="HIGH", abundance_threshold=floor
    )
    assert not floored.empty
    assert len(floored) < len(high)
    assert (floored["relative_abundance"] >= floor).all()


def test_empty_when_no_formulas():
    df = _build_reference_isotopes_df(
        [], [_mechanism()], resolution_type="HIGH", abundance_threshold=0.0
    )
    assert df.empty


def _orbi_sample():
    return SimpleNamespace(
        sample_item_name="Sample One",
        filename="orbi-sample.raw",
    )


def _session_ctx():
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


class TestReferenceExpansionCache:
    """The expansion must be paid once per state, and invalidate on re-sync."""

    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        service._reference_isotope_cache.clear()
        yield
        service._reference_isotope_cache.clear()
        patch.stopall()

    def _patches(self, fingerprints, built_df):
        patch(f"{_MOD}.async_session", side_effect=lambda: _session_ctx()).start()
        fingerprint = patch(
            f"{_MOD}.known_state_fingerprint",
            new_callable=AsyncMock,
            side_effect=fingerprints,
        ).start()
        known = patch(
            f"{_MOD}.iter_known_compositions",
            new_callable=AsyncMock,
            return_value=[_known("C10H16O3", 184.1099, ["Pinonic acid"])],
        ).start()
        build = patch(
            f"{_MOD}._build_reference_isotopes_df", return_value=built_df
        ).start()
        return fingerprint, known, build

    @pytest.mark.asyncio
    async def test_same_state_expands_once(self):
        built = pd.DataFrame({"mz": [183.1026], "relative_abundance": [1.0]})
        _, known, build = self._patches([(("src-1", "t1"),)] * 2, built)

        first = await _fetch_reference_known_isotopes(
            _orbi_sample(), 0.0, [_mechanism()]
        )
        second = await _fetch_reference_known_isotopes(
            _orbi_sample(), 0.0, [_mechanism()]
        )

        build.assert_called_once()
        known.assert_awaited_once()
        pd.testing.assert_frame_equal(first, second)

    @pytest.mark.asyncio
    async def test_resynced_source_invalidates(self):
        built = pd.DataFrame({"mz": [183.1026], "relative_abundance": [1.0]})
        _, _, build = self._patches([(("src-1", "t1"),), (("src-1", "t2"),)], built)

        await _fetch_reference_known_isotopes(_orbi_sample(), 0.0, [_mechanism()])
        await _fetch_reference_known_isotopes(_orbi_sample(), 0.0, [_mechanism()])

        assert build.call_count == 2

    @pytest.mark.asyncio
    async def test_caller_mutation_does_not_corrupt_the_cache(self):
        built = pd.DataFrame({"mz": [183.1026], "relative_abundance": [1.0]})
        self._patches([(("src-1", "t1"),)] * 2, built)

        first = await _fetch_reference_known_isotopes(
            _orbi_sample(), 0.0, [_mechanism()]
        )
        first["mz"] = -1.0
        first["extra"] = True
        second = await _fetch_reference_known_isotopes(
            _orbi_sample(), 0.0, [_mechanism()]
        )

        assert "extra" not in second.columns
        assert second["mz"].iloc[0] == 183.1026

    @pytest.mark.asyncio
    async def test_no_active_sources_short_circuits(self):
        _, known, build = self._patches([()], pd.DataFrame())

        df = await _fetch_reference_known_isotopes(_orbi_sample(), 0.0, [_mechanism()])

        assert df.empty
        known.assert_not_awaited()
        build.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_mechanisms_touches_nothing(self):
        fingerprint, known, build = self._patches([(("src-1", "t1"),)], pd.DataFrame())

        df = await _fetch_reference_known_isotopes(_orbi_sample(), 0.0, [])

        assert df.empty
        fingerprint.assert_not_awaited()
        known.assert_not_awaited()
        build.assert_not_called()
