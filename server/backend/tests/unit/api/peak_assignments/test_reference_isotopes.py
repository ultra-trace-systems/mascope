"""Unit tests for expanding reference formulas into Stage A isotope rows.

Exercises the real target-ion / IsoSpec path (no DB): a bounded set of reference
formulas becomes matchable isotope rows carrying reference identities and no
curated target linkage. The cache tests below fake the DB edges and pin the
once-per-state property: the expansion is paid once per (reference state,
mechanism set, resolution, threshold, context ceiling, sample polarity), not
once per run.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    FileLines,
    ions_at_file,
    lines_at_file,
)
from mascope_backend.api.new.peak_assignments import sample_inputs
from mascope_backend.api.new.peak_assignments.engine import (
    listed_formula_identities,
    listed_ions,
)
from mascope_backend.api.new.peak_assignments.sample_inputs import (
    _build_reference_isotopes_df,
    _fetch_reference_known_isotopes,
)
from mascope_reference import KnownComposition, KnownIdentity
from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS, labelled_purity
from mascope_tools.composition.line_model import HeightResolution
from mascope_tools.composition.profiles import KNOWN_WINDOW_CEILING


#: An Orbitrap file with no resolution read: its fine lines as they are.
_FINE = FileLines("HIGH")


def _mechanism(mech_id="m-deprot", notation="[M-H]-", polarity="-"):
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
    df = _build_reference_isotopes_df(known, [_mechanism()])
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
    assert (df["ionization_mechanism"] == "[M-H]-").all()
    assert (df["resolution"] == "HIGH").all()
    # The one-to-many identities ride on each row.
    identities = df["reference_identities"].iloc[0]
    assert {i["name"] for i in identities} == {"Pinonic acid", "Norpinonic acid"}
    # Several isotopologues (M0 + 13C etc.), abundances are valid fractions.
    assert len(df) >= 2
    assert df["relative_abundance"].between(0.0, 1.0).all()


def test_a_files_lines_are_the_fine_lines_resolved_at_its_resolution():
    # The cache holds the fine lines; each run resolves them at its file's
    # R(m/z) and holds them to its abundance floor (MAS-977).
    known = [_known("C9H14O4", 186.0892, ["Pinic acid"])]
    fine = _build_reference_isotopes_df(known, [_mechanism()])
    high = lines_at_file(fine, FileLines("HIGH"))
    low = lines_at_file(fine, FileLines("LOW", lambda mz: 5000.0))
    assert not high.empty and not low.empty
    assert (high["resolution"] == "HIGH").all()
    assert (low["resolution"] == "LOW").all()
    assert len(high) == len(fine)
    # One peak of a 5000-resolution file holds the M+1 cluster: fewer lines,
    # the same total abundance.
    assert len(low) < len(high)
    assert low["relative_abundance"].sum() == pytest.approx(
        high["relative_abundance"].sum()
    )
    # An abundance floor at half the base peak drops the isotope tail.
    floor = 0.5 * float(high["relative_abundance"].max())
    floored = lines_at_file(fine, FileLines("HIGH"), floor)
    assert not floored.empty
    assert len(floored) < len(high)
    assert (floored["relative_abundance"] >= floor).all()


#: Formulas whose ions' brightest lines span the thresholds below: plain
#: organics, and the chlorine and bromine clusters whose brightest fine line
#: holds a share a merged line can pass.
_SPREAD = [
    ("C10H16O3", 184.1099),
    ("C6Cl6", 281.8131),
    ("CH2Br2", 171.8523),
    ("C2HBr3", 249.7628),
    ("C12H4Cl4O2", 319.8965),
    ("C30H50O", 426.3862),
]


@pytest.mark.parametrize(
    "file_lines",
    [
        FileLines("HIGH"),
        FileLines("LOW"),
        FileLines("LOW", lambda mz: 2000.0),
        FileLines("LOW", lambda mz: 15.0 * mz),
        FileLines("HIGH", HeightResolution(lambda mz: 40000.0)),
        FileLines("HIGH", HeightResolution(lambda mz: 1500.0)),
        FileLines("HIGH", lambda mz: float("nan")),
    ],
)
def test_the_lists_ions_are_the_ones_their_resolved_lines_name(file_lines):
    """What a reader that matches nothing reads of the reference lists, the
    neutrals they name and the ions they hold, is the same off their ions
    (``ions_at_file``) as off their lines resolved at the file
    (``lines_at_file``), whatever the file and the threshold (MAS-1274)."""
    known = [_known(formula, mass, [formula]) for formula, mass in _SPREAD]
    fine = _build_reference_isotopes_df(
        known, [_mechanism(), _mechanism("m-br", "[M+Br]-")]
    )
    merged_over = 0
    for threshold in (0.0, 1e-4, 0.01, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7):
        lines = lines_at_file(fine, file_lines, threshold)
        ions = ions_at_file(fine, file_lines, threshold)
        expected = lines.drop_duplicates("target_ion_id")
        assert ions["target_ion_id"].tolist() == expected["target_ion_id"].tolist()
        for column in ions.columns:
            assert ions[column].tolist() == expected[column].tolist(), column
        assert listed_formula_identities(ions) == listed_formula_identities(lines)
        assert listed_ions(ions) == listed_ions(lines)
        brightest = fine.groupby("target_ion_id")["relative_abundance"].max()
        merged_over += int((brightest[ions["target_ion_id"]] < threshold).sum())
    if file_lines.resolution_type == "LOW":
        # A merged line kept an ion no fine line of which clears the threshold.
        assert merged_over


def test_empty_when_no_formulas():
    df = _build_reference_isotopes_df([], [_mechanism()])
    assert df.empty


def _orbi_sample():
    return SimpleNamespace(
        sample_item_name="Sample One",
        filename="orbi-sample.raw",
        polarity="-",
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
        sample_inputs._reference_isotope_cache.clear()
        yield
        sample_inputs._reference_isotope_cache.clear()
        patch.stopall()

    def _patches(self, fingerprints, built_df):
        patch(
            "mascope_backend.api.new.peak_assignments.sample_inputs.async_session",
            side_effect=lambda: _session_ctx(),
        ).start()
        fingerprint = patch(
            "mascope_backend.api.new.peak_assignments.sample_inputs.known_state_fingerprint",
            new_callable=AsyncMock,
            side_effect=fingerprints,
        ).start()
        known = patch(
            "mascope_backend.api.new.peak_assignments.sample_inputs.iter_known_compositions",
            new_callable=AsyncMock,
            return_value=[_known("C10H16O3", 184.1099, ["Pinonic acid"])],
        ).start()
        build = patch(
            "mascope_backend.api.new.peak_assignments.sample_inputs._build_reference_isotopes_df",
            return_value=built_df,
        ).start()
        return fingerprint, known, build

    @pytest.mark.asyncio
    async def test_same_state_expands_once(self):
        built = pd.DataFrame({"mz": [183.1026], "relative_abundance": [1.0]})
        _, known, build = self._patches([(("src-1", "t1"),)] * 2, built)

        first = await _fetch_reference_known_isotopes(
            _orbi_sample(), 0.0, [_mechanism()], known_window=None, file_lines=_FINE
        )
        second = await _fetch_reference_known_isotopes(
            _orbi_sample(), 0.0, [_mechanism()], known_window=None, file_lines=_FINE
        )

        build.assert_called_once()
        known.assert_awaited_once()
        pd.testing.assert_frame_equal(first, second)

    @pytest.mark.asyncio
    async def test_each_run_holds_the_cached_lines_to_its_own_floor(self):
        """The cache holds the fine lines whatever the floor, so the floor a
        run is handed is what keeps it at its own: two runs at two floors read
        two sets of lines off one build."""
        built = pd.DataFrame(
            {
                "target_ion_id": ["ion-1", "ion-1"],
                "target_isotope_id": ["iso-1", "iso-2"],
                "target_isotope_formula": ["C10H15O3-", "[13C]C9H15O3-"],
                "mz": [183.1026, 184.1060],
                "relative_abundance": [1.0, 0.11],
            }
        )
        _, _, build = self._patches([(("src-1", "t1"),)] * 2, built)

        async def fetch(floor):
            return await _fetch_reference_known_isotopes(
                _orbi_sample(),
                floor,
                [_mechanism()],
                known_window=None,
                file_lines=_FINE,
            )

        deep = await fetch(0.0)
        shallow = await fetch(0.5)

        build.assert_called_once()
        assert deep["target_isotope_formula"].tolist() == [
            "C10H15O3-",
            "[13C]C9H15O3-",
        ]
        assert shallow["target_isotope_formula"].tolist() == ["C10H15O3-"]

    @pytest.mark.asyncio
    async def test_a_run_under_another_purity_builds_its_own(self):
        """The lines of a labelled ion are built at the purity the run's setup
        carries, so a run under another purity is not served the frame one
        under the default left behind; one under the same purity is."""
        built = pd.DataFrame({"mz": [183.1026], "relative_abundance": [1.0]})
        _, _, build = self._patches([(("src-1", "t1"),)] * 4, built)

        async def fetch():
            await _fetch_reference_known_isotopes(
                _orbi_sample(), 0.0, [_mechanism()], known_window=None, file_lines=_FINE
            )

        await fetch()
        with labelled_purity(0.95):
            await fetch()
            await fetch()
        with labelled_purity(CUSTOM_ELEMENTS["^N"].default_purity):
            await fetch()

        assert build.call_count == 2

    @pytest.mark.asyncio
    async def test_resynced_source_invalidates(self):
        built = pd.DataFrame({"mz": [183.1026], "relative_abundance": [1.0]})
        _, _, build = self._patches([(("src-1", "t1"),), (("src-1", "t2"),)], built)

        await _fetch_reference_known_isotopes(
            _orbi_sample(), 0.0, [_mechanism()], known_window=None, file_lines=_FINE
        )
        await _fetch_reference_known_isotopes(
            _orbi_sample(), 0.0, [_mechanism()], known_window=None, file_lines=_FINE
        )

        assert build.call_count == 2

    @pytest.mark.asyncio
    async def test_caller_mutation_does_not_corrupt_the_cache(self):
        built = pd.DataFrame({"mz": [183.1026], "relative_abundance": [1.0]})
        self._patches([(("src-1", "t1"),)] * 2, built)

        first = await _fetch_reference_known_isotopes(
            _orbi_sample(), 0.0, [_mechanism()], known_window=None, file_lines=_FINE
        )
        first["mz"] = -1.0
        first["extra"] = True
        second = await _fetch_reference_known_isotopes(
            _orbi_sample(), 0.0, [_mechanism()], known_window=None, file_lines=_FINE
        )

        assert "extra" not in second.columns
        assert second["mz"].iloc[0] == 183.1026

    @pytest.mark.asyncio
    async def test_no_active_sources_short_circuits(self):
        _, known, build = self._patches([()], pd.DataFrame())

        df = await _fetch_reference_known_isotopes(
            _orbi_sample(), 0.0, [_mechanism()], known_window=None, file_lines=_FINE
        )

        assert df.empty
        known.assert_not_awaited()
        build.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_reader_that_matches_nothing_reads_the_ions_alone(self):
        """``lines=False`` gives the cached frame's ions, one row each, with
        none of their lines resolved (``ions_at_file``, MAS-1274)."""
        built = pd.DataFrame(
            {
                "target_ion_id": ["ion-1", "ion-1"],
                "target_isotope_id": ["iso-1", "iso-2"],
                "target_isotope_formula": ["C10H15O3-", "[13C]C9H15O3-"],
                "mz": [183.1026, 184.1060],
                "relative_abundance": [0.89, 0.11],
                "target_compound_formula": ["C10H16O3", "C10H16O3"],
            }
        )
        self._patches([(("src-1", "t1"),)] * 2, built)
        resolving = FileLines("LOW", lambda mz: 1000.0)

        ions = await _fetch_reference_known_isotopes(
            _orbi_sample(),
            0.0,
            [_mechanism()],
            known_window=None,
            file_lines=resolving,
            lines=False,
        )
        lines = await _fetch_reference_known_isotopes(
            _orbi_sample(), 0.0, [_mechanism()], known_window=None, file_lines=resolving
        )

        assert ions.to_dict("records") == [
            {"target_ion_id": "ion-1", "target_compound_formula": "C10H16O3"}
        ]
        # The default reads the lines, resolved at the file.
        assert lines["mz"].tolist() == pytest.approx([183.1026, 184.1060])
        assert (lines["resolution"] == "LOW").all()

    @pytest.mark.asyncio
    async def test_no_mechanisms_touches_nothing(self):
        fingerprint, known, build = self._patches([(("src-1", "t1"),)], pd.DataFrame())

        df = await _fetch_reference_known_isotopes(
            _orbi_sample(), 0.0, [], known_window=None, file_lines=_FINE
        )

        assert df.empty
        fingerprint.assert_not_awaited()
        known.assert_not_awaited()
        build.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_ceiling_and_the_samples_polarity_reach_the_known_set(self):
        # Each source is matched inside its own window under the context's
        # ceiling, and only when it is detected in the sample's polarity.
        built = pd.DataFrame({"mz": [183.1026], "relative_abundance": [1.0]})
        _, known, _ = self._patches([(("src-1", "t1"),)], built)

        await _fetch_reference_known_isotopes(
            _orbi_sample(),
            0.0,
            [_mechanism()],
            known_window=KNOWN_WINDOW_CEILING,
            file_lines=_FINE,
        )

        assert known.await_args.kwargs["ceiling"] == KNOWN_WINDOW_CEILING
        assert known.await_args.kwargs["polarity"] == "negative"

    @pytest.mark.asyncio
    async def test_another_ceiling_or_polarity_rebuilds_rather_than_reusing(self):
        # Same reference state: a frame built under one context's ceiling, or
        # for one polarity, holds formulas another run may not match.
        built = pd.DataFrame({"mz": [183.1026], "relative_abundance": [1.0]})
        _, known, build = self._patches([(("src-1", "t1"),)] * 4, built)
        positive = SimpleNamespace(
            sample_item_name="Sample Two", filename="orbi-sample.raw", polarity="+"
        )

        for sample, ceiling in [
            (_orbi_sample(), KNOWN_WINDOW_CEILING),
            (_orbi_sample(), None),
            (positive, None),
            (_orbi_sample(), KNOWN_WINDOW_CEILING),
        ]:
            await _fetch_reference_known_isotopes(
                sample, 0.0, [_mechanism()], known_window=ceiling, file_lines=_FINE
            )

        # Three distinct keys; the fourth call is the first one again.
        assert build.call_count == 3
        assert [call.kwargs["polarity"] for call in known.await_args_list] == [
            "negative",
            "negative",
            "positive",
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("lines", [True, False])
async def test_the_known_set_hands_the_reference_lists_whether_to_resolve(lines):
    """The target library's lines are resolved either way; the reference
    lists' only where the reader asks for lines (MAS-1274)."""
    target = pd.DataFrame(
        {"target_ion_id": ["t-1"], "mz": [181.0707], "relative_abundance": [1.0]}
    )
    with (
        patch.object(
            sample_inputs,
            "_fetch_known_target_isotopes",
            new_callable=AsyncMock,
            return_value=target,
        ) as library,
        patch.object(
            sample_inputs,
            "_fetch_reference_known_isotopes",
            new_callable=AsyncMock,
            return_value=pd.DataFrame(),
        ) as reference,
    ):
        known = await sample_inputs._fetch_known_isotopes(
            _orbi_sample(),
            1e-4,
            ["m-deprot"],
            [_mechanism()],
            known_window=None,
            resolution=None,
            lines=lines,
        )

    library.assert_awaited_once()
    assert reference.await_args.kwargs["lines"] is lines
    assert known["target_ion_id"].tolist() == ["t-1"]
