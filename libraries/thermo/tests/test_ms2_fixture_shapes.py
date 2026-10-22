"""The reader answers the hermetic MS2 fixture's three shapes as its answers say.

The fixture (``server/backend/tests/ms2_fixture.py``) reads the committed MS1
file as three MS2 acquisitions - manual, interleaved data-dependent and
stepped-energy - and ``ms2_fixture.json`` beside it holds what a reader must
answer for each. These read every shape through the OpenTFRaw backend and the
reader functions every MS2 route is built on, and compare: the summary, the
scans' linkage, the averaged spectra, the per-scan spectra, and the census.

Every later phase tests on this fixture, so these are what make its answers
trustworthy: a reader change that moves one is caught here, and a fixture
change that leaves one stale is too.
"""

import numpy as np
import opentfraw
import pytest
from thermo_test_support import ms2_fixture

import mascope_thermo.thermo as m_thermo
from mascope_thermo.backend import open_backend
from mascope_thermo.streams import scan_streams


fixture = ms2_fixture()
ANSWERS = fixture.answers()


@pytest.fixture(params=fixture.SHAPES)
def shape(request):
    """Each shape, presented, as ``(its raw file, its definition)``."""
    with fixture.presented(request.param) as path:
        yield str(path), ANSWERS["shapes"][request.param]


def _scans_of(definition, kind):
    return [
        number
        for number, scan in enumerate(definition["scans"], start=1)
        if (kind in scan)
    ]


class TestTheSummary:
    def test_its_counts(self, shape):
        path, definition = shape
        expected = definition["answers"]

        summary = m_thermo.get_ms2_summary_metadata(path)

        assert summary["ms1_scan_count"] == expected["ms1_scan_count"]
        assert summary["ms2_scan_count"] == expected["ms2_scan_count"]
        assert (
            summary["ms1_scan_count"] + summary["ms2_scan_count"] == expected["scans"]
        )

    def test_its_groups(self, shape):
        path, definition = shape
        expected = definition["answers"]["groups"]

        summary = m_thermo.get_ms2_summary_metadata(path)

        assert [
            (
                g["parent_peak_mz"],
                g["activation"],
                g["hcd_energy"],
                g["isolation_width"],
                g["scan_count"],
            )
            for g in summary["groups"]
        ] == [
            (
                g["parent_peak_mz"],
                g["activation"],
                g["hcd_energy"],
                g["isolation_width"],
                g["scan_count"],
            )
            for g in expected
        ]
        assert summary["parent_peaks"] == list(
            dict.fromkeys(g["parent_peak_mz"] for g in expected)
        )

    def test_its_groups_span_their_scans(self, shape):
        path, definition = shape

        summary = m_thermo.get_ms2_summary_metadata(path)

        for group, expected in zip(
            summary["groups"], definition["answers"]["groups"], strict=True
        ):
            assert group["t_min"] == pytest.approx(
                fixture.scan_time_s(min(expected["scans"]))
            )
            assert group["t_max"] == pytest.approx(
                fixture.scan_time_s(max(expected["scans"]))
            )

    def test_its_isolation_width(self, shape):
        """One width where every fragment scan shares it; none where a group
        stores two, as the interleaved shape's does."""
        path, definition = shape

        summary = m_thermo.get_ms2_summary_metadata(path)

        assert summary["isolation_width"] == definition["answers"]["isolation_width"]


class TestTheTwoWidthGroup:
    def test_one_group_of_the_fixture_stores_two_widths(self):
        two_widths = [
            (name, group["spectrum"])
            for name, definition in ANSWERS["shapes"].items()
            for group in definition["answers"]["groups"]
            if len(group["isolation_widths"]) == 2
        ]

        assert two_widths == [("interleaved", "B@30")]

    def test_the_reader_reads_both(self):
        with (
            fixture.presented("interleaved") as path,
            open_backend(str(path)) as backend,
        ):
            _, widths, _ = backend.ms2_acquisition_info()
            events = backend.ms2_events_by_scan()

        b = ANSWERS["precursors"]["B"]["isolation_mz"]
        assert sorted({widths[n] for n, (mz, _) in events.items() if mz == b}) == [
            1.0,
            2.0,
        ]


class TestTheScans:
    def test_each_fragment_scan_names_its_precursor_and_activation(self, shape):
        path, definition = shape
        precursors = ANSWERS["precursors"]

        with open_backend(path) as backend:
            events = backend.ms2_events_by_scan()

        assert events == {
            number: (
                precursors[scan["precursor"]]["isolation_mz"],
                f"hcd{scan['nce']:.2f}",
            )
            for number, scan in enumerate(definition["scans"], start=1)
            if "precursor" in scan
        }

    def test_its_survey_and_master_scans(self, shape):
        """The manual shape records neither; the methods record their events,
        and the data-dependent one each scan's survey scan."""
        path, definition = shape

        with open_backend(path) as backend:
            rows = {row["scan"]: row for row in backend.scan_filters()}
            masters = {
                int(scan["scan_number"]): scan["master_scan_number"]
                for scan in backend._all_scans()
            }

        for number, scan in enumerate(definition["scans"], start=1):
            assert rows[number]["event"] == scan.get("event")
            assert masters[number] == scan.get("master")

    def test_its_trailer_holds_the_shapes_settings(self, shape):
        path, definition = shape
        settings = definition["fragment_trailer"]

        with open_backend(path) as backend:
            for number in _scans_of(definition, "precursor"):
                scan = definition["scans"][number - 1]
                precursor = ANSWERS["precursors"][scan["precursor"]]
                trailer = backend.scan_trailer(number)
                assert trailer["MS2 Isolation Width:"] == scan["width"]
                assert trailer["HCD Energy:"] == scan["nce"]
                assert trailer["Monoisotopic M/Z:"] == precursor["isolation_mz"]
                assert trailer["Charge State:"] == precursor["charge"]
                assert trailer["Micro Scan Count:"] == 1
                assert {label: trailer[label] for label in settings} == settings

    def test_its_trailer_records_its_linkage(self, shape):
        """As the scan index does: an event and a master scan only where the
        method recorded them, and 0 where it did not."""
        path, definition = shape

        with open_backend(path) as backend:
            for number, scan in enumerate(definition["scans"], start=1):
                trailer = backend.scan_trailer(number)
                assert trailer["Scan Event:"] == scan.get("event", 0)
                assert trailer["Scan Segment:"] == (1 if "event" in scan else 0)
                assert trailer["Master Scan Number:"] == scan.get("master", 0)
                assert trailer["Master Index:"] == scan.get("master", 0)

    def test_its_scan_table_names_the_precursor(self, shape):
        """The scan index's own MS2 fields, which a reader may read in place
        of the filter and the trailer, say what they do."""
        path, definition = shape

        with open_backend(path) as backend:
            rows = {int(s["scan_number"]): s for s in backend._all_scans()}

        for number in _scans_of(definition, "precursor"):
            scan = definition["scans"][number - 1]
            precursor = ANSWERS["precursors"][scan["precursor"]]
            row = rows[number]
            assert (
                row["precursor_mz"],
                row["isolation_target_mz"],
                row["isolation_width"],
                row["collision_energy"],
                row["collision_energy_is_nce"],
                row["activation"],
                row["charge"],
                row["ion_injection_time_ms"],
                row["scan_mode"],
            ) == (
                precursor["isolation_mz"],
                precursor["isolation_mz"],
                scan["width"],
                scan["nce"],
                True,
                "HCD",
                precursor["charge"],
                definition["fragment_trailer"]["Ion Injection Time (ms):"],
                "centroid",
            )

    def test_its_scan_range(self, shape):
        path, definition = shape

        with open_backend(path) as backend:
            stats = backend.scan_statistics(ms_type="Ms2")

        for number, row in stats.items():
            scan = definition["scans"][number - 1]
            low, high = definition["scan_ranges"][scan["precursor"]]
            assert (row["LowMass"], row["HighMass"]) == (low, high)
            assert row["IsCentroidScan"] is True
            assert row["MsType"] == "Ms2"
            centroids = ANSWERS["spectra"][f"{scan['precursor']}@{scan['nce']}"][
                "centroids"
            ]
            apex = max(centroids, key=lambda c: c["intensity"])
            assert (row["BasePeakMass"], row["BasePeakIntensity"]) == (
                apex["mz"],
                apex["intensity"],
            )
            assert row["PacketCount"] == len(centroids)

    def test_a_fragment_scans_trailer_has_a_survey_scans_labels(self, shape):
        """As in a real file, whose every scan has the one trailer header, so
        a table of the trailers lines up."""
        path, definition = shape

        with open_backend(path) as backend:
            survey = backend.scan_trailer(_scans_of(definition, "ms1")[0])
            for number in _scans_of(definition, "precursor"):
                assert list(backend.scan_trailer(number)) == list(survey)

    def test_its_survey_scans_are_the_files_own(self, shape):
        path, definition = shape
        # The file itself, as OpenTFRaw reads it without the backend
        source = opentfraw.RawFile(str(fixture.SOURCE_FILE))
        real = {n: np.asarray(source.centroid_labels(n)["mz"]) for n in (1, 2)}

        with open_backend(path) as backend:
            for number in _scans_of(definition, "ms1"):
                (scan,) = backend.centroids_per_scan(
                    t_min=fixture.scan_time_s(number),
                    t_max=fixture.scan_time_s(number),
                    ms_type="Ms",
                )
                stands_on = definition["scans"][number - 1]["ms1"]
                np.testing.assert_array_equal(scan["masses"], real[stands_on])


class TestTheSpectra:
    def test_each_step_averages_to_its_spectrum(self, shape):
        path, definition = shape

        by_step = m_thermo.get_ms2_centroids_by_parent(path)

        assert len(by_step) == len(definition["answers"]["groups"])
        for group in definition["answers"]["groups"]:
            masses, intensities, _, _ = by_step[
                (group["parent_peak_mz"], group["activation"])
            ]
            centroids = ANSWERS["spectra"][group["spectrum"]]["centroids"]
            np.testing.assert_allclose(masses, [c["mz"] for c in centroids], rtol=1e-9)
            np.testing.assert_allclose(
                intensities, [c["intensity"] for c in centroids], rtol=1e-9
            )

    def test_each_fragment_scan_carries_its_spectrum(self, shape):
        path, definition = shape

        for group in definition["answers"]["groups"]:
            per_scan, tics = m_thermo.get_ms2_centroids_per_scan_for_parent(
                path, group["parent_peak_mz"], activation=group["activation"]
            )
            centroids = ANSWERS["spectra"][group["spectrum"]]["centroids"]

            assert [s["timestamp"] for s in per_scan] == pytest.approx(
                [fixture.scan_time_s(n) for n in group["scans"]]
            )
            mz = np.array([c["mz"] for c in centroids])
            intensity = np.array([c["intensity"] for c in centroids])
            resolution = definition["fragment_trailer"]["FT Resolution:"]
            for scan, tic in zip(per_scan, tics, strict=True):
                np.testing.assert_array_equal(scan["masses"], mz)
                np.testing.assert_array_equal(scan["intensities"], intensity)
                np.testing.assert_array_equal(
                    scan["resolutions"], np.round(resolution * np.sqrt(200.0 / mz))
                )
                np.testing.assert_allclose(
                    scan["signal_to_noise"], intensity / ANSWERS["noise"]
                )
                assert tic == pytest.approx(intensity.sum())

    def test_a_fragment_scan_stores_no_profile(self, shape):
        """As OpenTFRaw answers for a centroid scan, so the averaged heights
        are the centroids' own and not a profile's re-centroided apex."""
        path, definition = shape

        with open_backend(path) as backend:
            masses, intensities, _ = backend.profile_per_scan(ms_type="Ms2")

        assert len(masses) == definition["answers"]["ms2_scan_count"]
        assert all(m.size == 0 for m in masses)
        assert all(i.size == 0 for i in intensities)

    def test_each_centroid_has_the_fixtures_noise(self, shape):
        path, definition = shape
        fragment_scans = set(_scans_of(definition, "precursor"))

        with open_backend(path) as backend:
            meta = list(backend.iter_centroids_meta())

        noises = {
            noise
            for number, _, scan in meta
            if number in fragment_scans
            for noise in scan["noises"]
        }
        assert noises == {ANSWERS["noise"]}


class TestTheChromatograms:
    """What reads a scan's peaks whole, as a chromatogram does, reads the
    shape too."""

    def test_of_a_fragment(self, shape):
        """Each precursor's m/z across every fragment scan: its intensity in
        the scans of its own steps, nothing in the other precursor's."""
        path, definition = shape
        fragment_scans = [
            definition["scans"][n - 1] for n in _scans_of(definition, "precursor")
        ]

        for name, precursor in ANSWERS["precursors"].items():
            with open_backend(path) as backend:
                heights, times = backend.xic([precursor["mz"]], ms_type="Ms2")

            expected = [
                sum(
                    c["intensity"]
                    for c in ANSWERS["spectra"][f"{scan['precursor']}@{scan['nce']}"][
                        "centroids"
                    ]
                    if c["role"] == "precursor" and scan["precursor"] == name
                )
                for scan in fragment_scans
            ]
            assert heights[0] == pytest.approx(expected)
            assert times == pytest.approx(
                [fixture.scan_time_s(n) for n in _scans_of(definition, "precursor")]
            )

    def test_of_a_survey_scan(self, shape):
        path, definition = shape
        source = opentfraw.RawFile(str(fixture.SOURCE_FILE))
        real = {n: source.scan(n) for n in (1, 2)}
        mz = float(real[2]["mz"][np.argmax(real[2]["intensity"])])

        with open_backend(path) as backend:
            heights, _ = backend.xic([mz], ppm=1, ms_type="Ms")

        expected = [
            np.asarray(real[scan["ms1"]]["intensity"])[
                np.abs(np.asarray(real[scan["ms1"]]["mz"]) - mz) <= mz * 1e-6
            ].sum()
            for scan in definition["scans"]
            if "ms1" in scan
        ]
        assert heights[0] == pytest.approx(expected)


class TestTheCensus:
    def test_it_shows_the_fragmentation_streams(self, shape):
        path, definition = shape

        with open_backend(path) as backend:
            streams = scan_streams(backend)

        assert [
            {
                "key": s["key"],
                "ms_order": s["signature"]["ms_order"],
                "scans": s["scans"],
                "blocks": s["blocks"],
                "filters": s["filters"],
                **({"scan_event": s["scan_event"]} if "scan_event" in s else {}),
            }
            for s in streams
        ] == definition["answers"]["streams"]


class TestPresentingAShape:
    def test_it_reads_through_opentfraw_whatever_the_deployment_chose(
        self, monkeypatch
    ):
        monkeypatch.setenv("MASCOPE_THERMO_BACKEND", "thermo")

        with fixture.presented("manual") as path:
            summary = m_thermo.get_ms2_summary_metadata(str(path))

        assert (
            summary["ms2_scan_count"]
            == (ANSWERS["shapes"]["manual"]["answers"]["ms2_scan_count"])
        )

    def test_an_unknown_shape_is_refused(self):
        with pytest.raises(ValueError, match="no MS2 fixture shape"):
            with fixture.presented("dia"):
                pass

    def test_a_scan_outside_the_shape_is_refused(self):
        """As the reader refuses one outside the file, rather than reading
        some other scan."""
        with (
            fixture.presented("manual") as path,
            open_backend(str(path)) as backend,
        ):
            with pytest.raises(IndexError):
                backend._raw.centroid_labels(0)

    def test_the_support_module_hands_out_one_fixture(self):
        assert ms2_fixture() is fixture
