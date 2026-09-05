"""Characterization tests for the MS2 extraction functions on the Thermo backend.

These pin the *contract* (shape + internal consistency) of
``get_ms2_summary_metadata``, ``get_ms2_centroids_by_parent`` and
``get_ms2_centroids_per_scan_for_parent`` so the OpenTFRaw migration can't
silently change it. Exact cross-backend agreement is enforced separately by
``test_backend_parity.py``, where the Thermo backend is the runtime reference --
so nothing here is hardcoded to a specific acquisition.

These functions need MS2 scans, and only some local files have them. The module
discovers the smallest ``.raw`` file in ``test_files/`` that contains MS2 scans
and **skips** entirely if there is none (e.g. on a fresh clone, where only the
MS1 KORBI files are committed).
"""

import numpy as np
import pytest
from conftest import POS_ORBI_FILE_PATH, TEST_FILES_DIR, read_or_xfail

import mascope_thermo.backend as m_backend
import mascope_thermo.thermo as m_thermo
from mascope_thermo.backend import open_backend


def _has_ms2(path: str) -> bool:
    """True if the file contains at least one MS2 scan (probed via the current
    backend, so discovery works without the Thermo DLLs)."""
    try:
        with open_backend(path) as be:
            be.scan_indices(ms_type="Ms2")
        return True
    except m_thermo.NoScansFoundError:
        return False
    except Exception:
        return False


def _first_ms2_file() -> str | None:
    """Smallest local .raw file with MS2 scans, or None. Smallest-first keeps the
    discovery probe cheap and avoids opening large files unnecessarily."""
    for path in sorted(TEST_FILES_DIR.glob("*.raw"), key=lambda p: p.stat().st_size):
        if _has_ms2(str(path)):
            return str(path)
    return None


MS2_FILE = _first_ms2_file()

# Run every test under each reader backend.
pytestmark = pytest.mark.usefixtures("backend")

# Applied to the classes that need an actual MS2 acquisition. The MS1-only
# regression test below runs unconditionally against the committed KORBI file.
requires_ms2 = pytest.mark.skipif(
    MS2_FILE is None, reason="no .raw file with MS2 scans in test_files/"
)


def test_ms2_summary_on_ms1_only_file_returns_empty():
    """An MS1-only file must return an empty-MS2 summary, not raise.

    Regression test: ``get_ms2_summary_metadata`` previously called
    ``len(ms2_selector.scan_indices_1based)``, which raises ``NoScansFoundError``
    when there are no MS2 scans -- making its own empty-MS2 return branch dead
    code. KORBI is committed and MS1-only.
    """
    meta = m_thermo.get_ms2_summary_metadata(POS_ORBI_FILE_PATH)
    assert meta["ms2_scan_count"] == 0
    assert meta["parent_peaks"] == []
    assert meta["groups"] == []
    assert meta["hcd_energy_map"] == {}
    assert meta["isolation_width"] is None
    assert meta["ms1_scan_count"] >= 0


# These depend on `backend` so they (a) recompute per backend and (b) run after
# the env var is set. Function-scoped for the same reason -- they can't be cached
# across backends. read_or_xfail keeps the not-yet-implemented opentfraw backend
# an xfail rather than a fixture-setup error.
@pytest.fixture
def summary(backend) -> dict:
    return read_or_xfail(m_thermo.get_ms2_summary_metadata, MS2_FILE)


@pytest.fixture
def by_parent(backend) -> dict:
    return read_or_xfail(m_thermo.get_ms2_centroids_by_parent, MS2_FILE)


@pytest.fixture
def num_scans(backend) -> int:
    return read_or_xfail(lambda: m_thermo.RawFileMetadataLegacy(MS2_FILE).num_of_scans)


@requires_ms2
class TestGetMs2SummaryMetadata:
    """``get_ms2_summary_metadata`` reports parent peaks, HCD energies, isolation
    width and the MS1/MS2 scan split -- all mutually consistent.
    """

    def test_parent_peaks_sorted_unique_positive(self, summary):
        parents = summary["parent_peaks"]
        assert len(parents) >= 1
        assert parents == sorted(parents)
        assert len(set(parents)) == len(parents)
        assert all(p > 0 for p in parents)

    def test_hcd_energy_map_keyed_by_parents(self, summary):
        assert sorted(summary["hcd_energy_map"]) == pytest.approx(
            summary["parent_peaks"]
        )
        for energies in summary["hcd_energy_map"].values():
            assert len(energies) >= 1
            assert all(np.isfinite(e) for e in energies)

    def test_isolation_width_positive(self, summary):
        assert isinstance(summary["isolation_width"], float)
        assert summary["isolation_width"] > 0

    def test_scan_counts_consistent(self, summary, num_scans):
        assert summary["ms2_scan_count"] > 0
        assert summary["ms1_scan_count"] >= 0
        # ms1 + ms2 is the number of *selected* scans: the file total, minus the
        # first scan when the bad-first-scan workaround drops it as a high-TIC
        # outlier (see thermo.py _bad_first_scan; applied by both backends).
        selected = summary["ms1_scan_count"] + summary["ms2_scan_count"]
        assert num_scans - 1 <= selected <= num_scans

    def test_parent_peak_tolerance_default(self, summary):
        assert summary["parent_peak_tolerance"] == 0.001


@requires_ms2
class TestGetMs2CentroidsByParent:
    """``get_ms2_centroids_by_parent`` returns averaged centroids per parent peak,
    each carrying per-peak resolution and S:N (the per-peak label data the
    reader backend must preserve).
    """

    def test_keys_match_summary_groups(self, by_parent, summary):
        expected = {(g["parent_peak_mz"], g["activation"]) for g in summary["groups"]}
        assert set(by_parent) == expected
        parents = sorted({mz for mz, _ in by_parent})
        assert parents == pytest.approx(summary["parent_peaks"])

    def test_centroid_arrays_well_formed(self, by_parent):
        for masses, intensities, resolutions, sn in by_parent.values():
            n = masses.size
            assert n > 0
            assert intensities.size == resolutions.size == sn.size == n
            assert np.all(np.isfinite(masses)) and np.all(masses > 0)
            assert np.all(intensities >= 0) and intensities.sum() > 0
            # Per-peak resolution / S:N must be present and finite.
            assert np.all(np.isfinite(resolutions)) and np.all(resolutions > 0)
            assert np.all(np.isfinite(sn))

    def test_average_flag_scales_intensity(self, by_parent):
        summed = m_thermo.get_ms2_centroids_by_parent(MS2_FILE, average=False)
        for parent, (_, avg_int, _, _) in by_parent.items():
            # Non-averaged intensities are scaled by the combined-scan count, so
            # they must be at least as large as the averaged ones.
            assert summed[parent][1].sum() >= avg_int.sum()

    def test_mz_range_filters_parents(self, by_parent):
        parents = sorted({mz for mz, _ in by_parent})
        if len(parents) < 2:
            pytest.skip("need >=2 parent peaks to exercise m/z filtering")
        threshold = (parents[0] + parents[-1]) / 2
        filtered = m_thermo.get_ms2_centroids_by_parent(MS2_FILE, mz_min=threshold)
        assert filtered, "expected at least one parent above the threshold"
        assert all(mz >= threshold for mz, _ in filtered)
        assert set(filtered).issubset(set(by_parent))


@requires_ms2
class TestGetMs2CentroidsPerScanForParent:
    """``get_ms2_centroids_per_scan_for_parent`` returns per-scan centroids + TICs
    for one parent, time-ordered.
    """

    def test_returns_scans_for_a_real_parent(self, summary):
        parent = summary["parent_peaks"][0]
        per_scan, tics = m_thermo.get_ms2_centroids_per_scan_for_parent(
            MS2_FILE, parent
        )
        assert len(per_scan) == len(tics) > 0
        for d in per_scan:
            assert set(d) == {
                "masses",
                "intensities",
                "resolutions",
                "signal_to_noise",
                "timestamp",
            }
            n = d["masses"].size
            assert d["intensities"].size == n
            assert d["resolutions"].size == n
            assert d["signal_to_noise"].size == n
        timestamps = [d["timestamp"] for d in per_scan]
        assert timestamps == sorted(timestamps)

    def test_per_parent_scan_counts_sum_to_total(self, summary):
        total = 0
        for parent in summary["parent_peaks"]:
            per_scan, _ = m_thermo.get_ms2_centroids_per_scan_for_parent(
                MS2_FILE, parent
            )
            total += len(per_scan)
        assert total == summary["ms2_scan_count"]

    def test_unknown_parent_returns_empty(self, summary):
        far_off = max(summary["parent_peaks"]) + 1000.0
        per_scan, tics = m_thermo.get_ms2_centroids_per_scan_for_parent(
            MS2_FILE, far_off
        )
        assert per_scan == []
        assert tics == []


class TestClusterScansByParent:
    """``_cluster_scans_by_parent`` groups on precursor *and* activation.

    Pure function over a ``{scan: (precursor_mz, activation)}`` mapping, so
    these run without an MS2 acquisition on disk.
    """

    def test_stepped_energy_splits_into_one_group_per_step(self):
        """A stepped-energy run is one precursor at several collision energies.

        Grouping on m/z alone would average the steps into a single spectrum
        whose collision energy is a number the instrument never used.
        """
        events = {
            1: (137.096, "hcd20.00"),
            2: (137.096, "hcd20.00"),
            3: (137.096, "hcd40.00"),
            4: (137.096, "hcd80.00"),
        }
        assert m_thermo._cluster_scans_by_parent(events) == {
            (137.096, "hcd20.00"): [1, 2],
            (137.096, "hcd40.00"): [3],
            (137.096, "hcd80.00"): [4],
        }

    def test_single_activation_yields_one_group_per_precursor(self):
        """The ordinary case is unchanged: one group per precursor."""
        events = {
            1: (200.5, "hcd25.00"),
            2: (300.5, "hcd25.00"),
            3: (200.5, "hcd25.00"),
        }
        assert m_thermo._cluster_scans_by_parent(events) == {
            (200.5, "hcd25.00"): [1, 3],
            (300.5, "hcd25.00"): [2],
        }

    def test_near_duplicate_precursors_merge_across_activations(self):
        """Clustering runs over all of a precursor's scans, so every step of one
        precursor shares one canonical m/z -- the steps must not drift apart."""
        events = {
            1: (137.0960, "hcd20.00"),
            2: (137.0965, "hcd40.00"),
        }
        groups = m_thermo._cluster_scans_by_parent(events, parent_peak_tolerance=0.001)
        assert len({mz for mz, _ in groups}) == 1
        assert {activation for _, activation in groups} == {"hcd20.00", "hcd40.00"}

    def test_distinct_precursors_stay_apart(self):
        events = {1: (137.0960, "hcd20.00"), 2: (137.5000, "hcd20.00")}
        groups = m_thermo._cluster_scans_by_parent(events, parent_peak_tolerance=0.001)
        assert len(groups) == 2

    def test_chained_activations_are_one_key(self):
        """A scan that chains two activations keeps them together in the key."""
        events = {1: (445.12, "cid30.00@hcd20.00"), 2: (445.12, "hcd20.00")}
        groups = m_thermo._cluster_scans_by_parent(events)
        assert set(groups) == {
            (445.12, "cid30.00@hcd20.00"),
            (445.12, "hcd20.00"),
        }

    def test_empty_input(self):
        assert m_thermo._cluster_scans_by_parent({}) == {}


class TestParseMs2Event:
    """The scan-filter parse both backends share."""

    @pytest.mark.parametrize(
        ("filter_string", "expected"),
        [
            (
                "FTMS + p NSI Full ms2 137.0960@hcd40.00 [40.0000-160.0419]",
                (137.0960, "hcd40.00"),
            ),
            (
                "FTMS + c NSI Full ms2 445.1200@cid30.00@hcd20.00 [50.0000-500.0000]",
                (445.1200, "cid30.00@hcd20.00"),
            ),
            ("FTMS + p NSI Full ms [120.0000-200.0000]", None),
            # The precursor is what has to be resolved; a scan must not go
            # missing over how the dissociation next to it is spelled.
            (
                "FTMS + p NSI Full ms2 137.0960@ETD50.00 [40.0000-160.0419]",
                (137.0960, "ETD50.00"),
            ),
        ],
    )
    def test_parse(self, filter_string, expected):
        assert m_backend._parse_ms2_event(filter_string) == expected


@requires_ms2
class TestMs2SummaryGroups:
    """``groups`` reports each (parent peak, activation) separately, and is
    internally consistent with the rest of the summary."""

    def test_groups_cover_every_ms2_scan_once(self, summary):
        assert (
            sum(g["scan_count"] for g in summary["groups"])
            <= (summary["ms2_scan_count"])
        )
        assert all(g["scan_count"] > 0 for g in summary["groups"])

    def test_group_parents_are_the_summary_parents(self, summary):
        assert {g["parent_peak_mz"] for g in summary["groups"]} == set(
            summary["parent_peaks"]
        )

    def test_group_energies_concatenate_into_the_hcd_map(self, summary):
        for parent, energies in summary["hcd_energy_map"].items():
            from_groups = [
                e
                for g in summary["groups"]
                if g["parent_peak_mz"] == parent
                for e in g["hcd_energy"]
            ]
            assert energies == from_groups

    def test_group_times_are_json_safe_and_ordered(self, summary):
        for g in summary["groups"]:
            # These go out over the API, so a numpy scalar here would fail to
            # serialize.
            assert type(g["t_min"]) is float
            assert type(g["t_max"]) is float
            assert g["t_min"] <= g["t_max"]
