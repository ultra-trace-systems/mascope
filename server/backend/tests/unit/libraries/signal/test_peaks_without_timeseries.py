"""A time-ranged listing says which peaks it left out, and how to get them.

A listing of a sample's peaks over a time range is aggregated from each
peak's per-scan timeseries, and peak detection allocates every peak with
none: a timeseries is computed when something first asks for it. Matching
asks for the peaks it matches, so the ones a ranged listing leaves out are
mostly the unmatched - on a measured single-stream file, two of its three
strongest peaks.

Nothing in the rows of a short listing shows that it is short. The warning
is all there is, so it has to reach the reader in every answer, the empty one
included, name a remedy that works, and be readable without picking it out of
a sentence that also quotes a name the user chose.
"""

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest
from scripted_acquisition import SAMPLE_FILENAME

import mascope_signal.compute as m_compute
from mascope_backend.api.controllers.samples import samples_controller
from mascope_backend.api.controllers.samples.lib.samples_peaks import extract_peaks


def _window() -> tuple[float, float]:
    """The sample's acquisition window: every scan of the polarity."""
    scans = np.sort(m_compute.get_scan_timestamps(SAMPLE_FILENAME, polarity="-"))
    return float(scans.min()), float(scans.max())


def _whole():
    """The listing without a time range: every peak the sample lists."""
    t0, t1 = _window()
    return extract_peaks(SAMPLE_FILENAME, "-", t0, t1)


def _ranged():
    """The listing over a time range - here the whole window, so that it is
    short of a peak for want of a timeseries and for no other reason."""
    t0, t1 = _window()
    return extract_peaks(SAMPLE_FILENAME, "-", t0, t1, t_min=t0, t_max=t1)


def _ask_for_timeseries(mzs) -> None:
    """What the timeseries route does for a peak: compute it, and store it."""
    asyncio.run(m_compute.load_peak_timeseries(SAMPLE_FILENAME, list(mzs))).compute()


def _answer(monkeypatch, *, ranged: bool = True, name: str = "Scripted") -> dict:
    """The peaks controller's answer for the scripted sample."""
    t0, t1 = _window()
    sample = SimpleNamespace(
        sample_item_id="sample-1",
        sample_item_name=name,
        sample_file_id="file-1",
        filename=SAMPLE_FILENAME,
        polarity="-",
        t0=t0,
        t1=t1,
        instrument="OrbiTest",
        instrument_type="orbitrap",
    )

    async def _fetch_sample(_sample_item_id):
        return sample

    async def _no_stream_rows(_sample_file_id, segments):
        # The listing names the sample's segments, and reads what the file's
        # stream rows say of them: none here
        return segments

    monkeypatch.setattr(samples_controller, "fetch_sample", _fetch_sample)
    monkeypatch.setattr(samples_controller, "with_stream_rows", _no_stream_rows)
    time_range = {"t_min": t0, "t_max": t1} if ranged else {}
    return asyncio.run(
        samples_controller.get_sample_peaks("sample-1", matches=False, **time_range)
    )


def test_freshly_detected_peaks_are_all_left_out_of_a_ranged_listing(composite):
    """Detection computes no timeseries, so before anything asks for one a
    ranged listing is empty - and says how many peaks that cost it."""
    whole = _whole()
    listed = _ranged()

    assert whole.count > 0
    assert listed.peak_ids == []
    (warning,) = listed.warnings
    assert warning.startswith(f"{whole.count} peak(s) were excluded")


def test_asking_for_a_peaks_timeseries_brings_it_into_the_ranged_listing(composite):
    """The remedy the warning names: a peak asked for is listed from then on,
    the count falls by as many, and with every peak asked for the listing is
    whole and warns of nothing."""
    whole = _whole()
    asked = whole.mz_values[:2]

    _ask_for_timeseries(asked)
    listed = _ranged()

    assert listed.mz_values == pytest.approx(asked)
    (warning,) = listed.warnings
    assert warning.startswith(f"{whole.count - 2} peak(s) were excluded")

    _ask_for_timeseries(whole.mz_values)
    listed = _ranged()

    assert listed.peak_ids == whole.peak_ids
    assert listed.warnings == []


def test_a_listing_from_time_zero_names_exactly_the_peaks_with_a_timeseries(
    composite,
):
    """How a client learns which peaks still need one, the API having no
    other way to say it: a ranged listing from time zero, nothing aggregated.
    The SDK's ``compute_peak_timeseries`` asks exactly this, so that it does
    not request again what is already computed."""
    whole = _whole()
    asked = whole.mz_values[1:3]
    _ask_for_timeseries(asked)
    t0, t1 = _window()

    listed = extract_peaks(
        SAMPLE_FILENAME,
        "-",
        t0,
        t1,
        areas=False,
        heights=False,
        average=False,
        t_min=0.0,
    )

    assert listed.mz_values == pytest.approx(asked)
    assert listed.peak_ids == whole.peak_ids[1:3]
    assert listed.areas is None and listed.heights is None


def test_the_warning_does_not_send_its_reader_to_peak_detection(composite):
    """It used to end "Re-run peak detection to include them", which leaves
    every peak out again: detection is what allocates them with none."""
    (warning,) = _ranged().warnings

    assert "peak detection" not in warning.lower()
    assert "computed when it is first requested" in warning


def test_the_warning_names_the_remedy_in_the_apis_terms(composite):
    """The route, which every client has, and no client's name for it: an SDK
    older than its wrapper would be sent to a method it does not have, and a
    client that is no SDK to one that was never its own."""
    (warning,) = _ranged().warnings

    assert "POST /api/samples/{sample_item_id}/peaks/timeseries" in warning
    assert "SDK" not in warning
    assert "compute_peak_timeseries" not in warning


def test_an_answer_with_every_peak_left_out_still_carries_the_warning(
    composite, monkeypatch
):
    """The empty answer is the one that most needs it: "no peaks found" alone
    reads as a sample that has none."""
    answer = _answer(monkeypatch)

    assert answer["results"] == 0
    assert answer["data"]["peak_id"] == []
    assert answer["message"].startswith("No peaks found in sample 'Scripted'")
    assert f" Warning: {_whole().count} peak(s) were excluded" in answer["message"]
    assert answer["warnings"] == _ranged().warnings


def test_an_answer_short_of_some_peaks_carries_it_beside_the_count(
    composite, monkeypatch
):
    whole = _whole()
    _ask_for_timeseries(whole.mz_values[:2])

    answer = _answer(monkeypatch)

    assert answer["results"] == 2
    assert answer["message"].startswith("Successfully loaded 2 peaks")
    assert f" Warning: {whole.count - 2} peak(s) were excluded" in answer["message"]
    (warning,) = answer["warnings"]
    assert warning.startswith(f"{whole.count - 2} peak(s) were excluded")


def test_an_answer_without_a_time_range_warns_of_nothing(composite, monkeypatch):
    """Read off the stored sums, which every peak has: nothing is left out."""
    answer = _answer(monkeypatch, ranged=False)

    assert answer["results"] == _whole().count
    assert "Warning:" not in answer["message"]
    assert answer["warnings"] == []


def test_the_warnings_are_listed_apart_from_the_sentence_that_quotes_the_name(
    composite, monkeypatch
):
    """A sample may be named for the marker. Its message then holds the marker
    with nothing left out - which is why the warnings are also a list of their
    own, the same whatever the sample is called."""
    whole = _answer(monkeypatch, ranged=False, name="Warning: blank 3")

    assert "'Warning: blank 3'" in whole["message"]
    assert whole["warnings"] == []

    short = _answer(monkeypatch, name="Warning: blank 3")

    assert short["warnings"] == _ranged().warnings
