"""
Unit tests for the report of peak stores the files have outgrown.

The script picks the Orbitrap files the old first-scan rule could misjudge,
opens each, and counts what it finds per instrument. Each is tested here
with stand-ins; the check itself, against a scripted acquisition, is in
``tests/unit/libraries/signal/test_peaks_redetected_on_reprocess.py``, beside
the re-processing it asks for.
"""

import asyncio

import pytest

from mascope_backend.db.scripts import report_stale_peak_stores as script


def _stream(ms_order, polarity):
    return {"signature": {"ms_order": ms_order, "polarity": polarity}}


MS1_NEG = _stream(1, "-")
MS1_POS = _stream(1, "+")
MS2_NEG = _stream(2, "-")


# --- which files are opened ---------------------------------------------------


class TestClassify:
    def test_a_file_with_no_props(self):
        assert script.classify(None) == script.NO_PROPS

    @pytest.mark.parametrize("census", [None, [], [{"key": "no signature"}]])
    def test_a_file_with_no_census_is_opened(self, census):
        """What it holds is not known without opening it."""
        props = {} if census is None else {"scan_streams": census}

        assert script.classify(props) == script.NO_CENSUS

    def test_a_file_of_ms1_scans_in_one_polarity_is_not(self):
        """Its first scan was compared with scans of its own MS order and
        polarity under the old rule as well."""
        props = {"scan_streams": [MS1_NEG, MS1_NEG]}

        assert script.classify(props) == script.MS1_ONE_POLARITY

    @pytest.mark.parametrize(
        "census",
        [[MS1_NEG, MS2_NEG], [MS1_NEG, MS1_POS], [MS2_NEG]],
        ids=["fragmentation", "both-polarities", "fragmentation-alone"],
    )
    def test_a_file_the_old_rule_could_misjudge_is_opened(self, census):
        assert script.classify({"scan_streams": census}) == script.CENSUS


# --- the survey ---------------------------------------------------------------


def _survey(props_of, check_of):
    candidates = [
        {"sample_file_id": f"id-{name}", "filename": name, "instrument": instrument}
        for name, instrument in [
            ("short", "A"),
            ("fine", "A"),
            ("plain", "A"),
            ("zarr", "A"),
            ("broken", "B"),
            ("unknown", "B"),
        ]
    ]
    checked = []

    def check(filename):
        checked.append(filename)
        return check_of(filename)

    def read_props(filename):
        assert filename != "zarr", "read the props of a file with no raw"
        return props_of[filename]

    found, counts = asyncio.run(
        script.survey(
            candidates, lambda filename: filename != "zarr", read_props, check
        )
    )
    return found, counts, checked


def test_the_survey_lists_the_outgrown_files_and_counts_every_file():
    props_of = {
        "short": {"scan_streams": [MS1_NEG, MS2_NEG]},
        "fine": {"scan_streams": [MS1_NEG, MS1_POS]},
        "plain": {"scan_streams": [MS1_NEG]},
        "broken": {"scan_streams": [MS1_NEG, MS2_NEG]},
        "unknown": {},
    }

    def check_of(filename):
        if filename == "broken":
            raise OSError("the raw file is gone")
        return {
            "short": (script.OUTGROWN, 126, 127),
            "fine": (script.CURRENT, 0, 0),
            "unknown": (script.NO_STORE, 0, 0),
        }[filename]

    found, counts, checked = _survey(props_of, check_of)

    assert found == [
        {
            "sample_file_id": "id-short",
            "filename": "short",
            "instrument": "A",
            "stored": 126,
            "reads": 127,
        }
    ]
    # Neither a single-polarity MS1 file nor one that keeps no raw file is
    # opened; one that cannot be read is counted and does not end the run
    assert checked == ["short", "fine", "broken", "unknown"]
    assert counts["A"] == {
        script.OUTGROWN: 1,
        script.CURRENT: 1,
        script.MS1_ONE_POLARITY: 1,
        script.NO_RAW: 1,
    }
    assert counts["B"] == {
        script.UNREADABLE: 1,
        script.NO_CENSUS: 1,
        script.NO_STORE: 1,
    }


def test_the_counts_line_names_every_class():
    line = script._counts_line(
        {
            script.OUTGROWN: 2,
            script.CURRENT: 5,
            script.PER_STREAM: 1,
            script.NO_STORE: 1,
            script.UNREADABLE: 1,
            script.NO_CENSUS: 3,
            script.MS1_ONE_POLARITY: 40,
            script.NO_RAW: 6,
            script.NO_PROPS: 4,
        }
    )

    assert line == (
        "2 outgrown their store, 5 read back their store, 1 per stream, "
        "1 with no store, 1 unreadable (10 candidates, 3 of them with no "
        "census); 40 MS1 files of one polarity not opened, 6 with no raw file "
        "kept, 4 with no props"
    )
