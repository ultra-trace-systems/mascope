"""The MS2 routes answer from the file's MS2 store, as the raw read would.

The converter writes a store for a file whose census holds a fragmentation
stream (``mascope_signal.ms2_store``), and the routes read it instead of the
raw file. Pinned here on each shape of the hermetic MS2 fixture
(``tests/ms2_fixture.py``), as the converter stores it:

- what the store answers is what the raw read answers, bit for bit: the
  summary, every spectrum at every S/N floor, every fragment timeseries and
  every group's scans;
- it answers without opening the raw file, at any grouping tolerance asked;
- a file without a store answers as before, and says so;
- a window that cuts a group is read from the store where it can be, and its
  spectra from the raw file;
- the calibration factor is applied on read.

Neither needs a running stack or a site file.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
import zarr
from ms2_fixture import SHAPES, Ms2Acquisition, answers, converted, scan_time_s

import mascope_file.io as m_io
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.new.ms2 import service as ms2_service
from mascope_signal import ms2_store
from mascope_thermo.backend import (
    MS2_HCD_ENERGY,
    MS2_INJECTION_TIME,
    MS2_ISOLATION_WIDTH,
    OpenTFRawBackend,
)


ANSWERS = answers()


@pytest.fixture(scope="module", params=SHAPES)
def stored(request, tmp_path_factory):
    """Each stored, converted into a filestore of its own."""
    filestore = tmp_path_factory.mktemp(f"filestore_{request.param}")
    with converted(request.param, filestore) as done:
        shape = ANSWERS["shapes"][request.param]
        yield SimpleNamespace(
            filename=done.props.filename,
            sample_dir=done.sample_dir,
            props=done.props,
            shape=shape,
            # What its groups are, in the order the routes give them
            groups=shape["answers"]["groups"],
        )


def _sample(stored, t0=None, t1=None, polarity="+"):
    """A sample of the stored file: by default the whole file, as one
    acquisition item of its one polarity reads it."""
    return SimpleNamespace(
        filename=stored.filename,
        sample_item_name="MS2 fixture",
        t0=t0,
        t1=t1,
        polarity=polarity,
    )


async def _read(route, sample, **query) -> dict:
    with patch.object(ms2_service, "fetch_sample", AsyncMock(return_value=sample)):
        return await route("si-1", **query)


@contextmanager
def _without_store(stored):
    """The file as one converted before the store existed: its props record
    none."""
    recorded = m_io.read_props(stored.filename)["ms2_store"]
    m_io.update_props(stored.filename, {"ms2_store": {}})
    try:
        yield
    finally:
        m_io.update_props(stored.filename, {"ms2_store": recorded})


@contextmanager
def _raw_file_unread():
    """Fail any read that opens the raw file."""

    def refused(self):
        raise AssertionError("the raw file was opened")

    with patch.object(OpenTFRawBackend, "__enter__", refused):
        yield


@contextmanager
def _calibrated(stored, factor):
    props = m_io.read_props(stored.filename)
    m_io.update_props(
        stored.filename, {"mz_calibration": {"par": {"calibration_factor": factor}}}
    )
    try:
        yield
    finally:
        m_io.update_props(stored.filename, {"mz_calibration": props["mz_calibration"]})


async def _both(stored, route, sample=None, **query) -> tuple[dict, dict]:
    """``route`` answered from the store, and from the raw file."""
    sample = sample or _sample(stored)
    from_store = await _read(route, sample, **query)
    with _without_store(stored):
        from_raw = await _read(route, sample, **query)
    return from_store, from_raw


def _parent_peaks(stored) -> list[float]:
    return list(dict.fromkeys(g["parent_peak_mz"] for g in stored.groups))


def _keys(stored) -> list[str]:
    return [
        ms2_store.group_key(g["parent_peak_mz"], g["activation"]) for g in stored.groups
    ]


async def _floors(stored) -> list[float]:
    """Every S/N floor that keeps a different set of centroids: each
    centroid's own S/N, which is where the floor's comparison turns, and
    none and above them all."""
    spectra = (
        await _read(
            ms2_service.get_ms2_averaged_centroids,
            _sample(stored),
            noise_threshold=0.0,
            by_activation=True,
        )
    )["data"]
    turns = {sn for spectrum in spectra.values() for sn in spectrum["signal_to_noise"]}
    return [0.0, *sorted(turns), max(turns) * 2]


class TestTheConversionWritesIt:
    def test_it_sits_beside_the_peak_store(self, stored):
        held = {path.name for path in stored.sample_dir.iterdir()}

        assert {"ms2.zarr", "peak_timeseries.zarr"} <= held

    def test_the_props_record_it_and_each_group_by_its_one_key(self, stored):
        recorded = m_io.read_props(stored.filename)["ms2_store"]

        assert recorded == {
            "layout": "mascope.ms2_store",
            "version": 1,
            "parent_peak_tolerance": 0.001,
            "ppm": 1,
            "reader": "opentfraw",
            "groups": [
                {"polarity": "+", "key": key, "scan_count": g["scan_count"]}
                for key, g in zip(_keys(stored), stored.groups, strict=True)
            ],
        }
        assert stored.props.ms2_store == recorded

    def test_a_key_has_four_decimals_and_the_activation(self, stored):
        for key, group in zip(_keys(stored), stored.groups, strict=True):
            mz, activation = key.split("@")
            assert mz == f"{group['parent_peak_mz']:.4f}"
            assert activation == group["activation"]


class TestTheRoutesAnswerAsTheRawReadDoes:
    @pytest.mark.asyncio
    async def test_the_summary(self, stored):
        from_store, from_raw = await _both(stored, ms2_service.get_ms2_summary)

        assert (from_store["source"], from_raw["source"]) == ("store", "raw")
        assert from_store["data"] == from_raw["data"]
        assert (
            from_store["data"]["ms2_scan_count"]
            == (stored.shape["answers"]["ms2_scan_count"])
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("by_activation", [True, False])
    async def test_every_spectrum_at_every_floor(self, stored, by_activation):
        for floor in await _floors(stored):
            from_store, from_raw = await _both(
                stored,
                ms2_service.get_ms2_averaged_centroids,
                noise_threshold=floor,
                by_activation=by_activation,
            )

            assert (from_store["source"], from_raw["source"]) == ("store", "raw")
            assert from_store["data"] == from_raw["data"], floor
            assert from_store["results"] == from_raw["results"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("normalize_by", [None, "tic"])
    async def test_every_fragment_timeseries(self, stored, normalize_by):
        steps = [None, *dict.fromkeys(g["activation"] for g in stored.groups)]
        served = 0
        for parent_peak_mz in _parent_peaks(stored):
            for activation in steps:
                for floor in (0.0, 10.0):
                    from_store, from_raw = await _both(
                        stored,
                        ms2_service.get_ms2_timeseries,
                        parent_peak_mz=parent_peak_mz,
                        activation=activation,
                        noise_threshold=floor,
                        normalize_by=normalize_by,
                    )

                    assert (from_store["source"], from_raw["source"]) == (
                        "store",
                        "raw",
                    )
                    assert from_store["data"] == from_raw["data"]
                    served += bool(from_store["data"]["values"])
        # Every parent peak at every floor, over all of its activations
        assert served >= 2 * len(_parent_peaks(stored))

    @pytest.mark.asyncio
    async def test_every_groups_scans(self, stored):
        for key in _keys(stored):
            for floor in (0.0, 10.0, 1e9):
                from_store, from_raw = await _both(
                    stored,
                    ms2_service.get_ms2_scans,
                    group=key,
                    noise_threshold=floor,
                )

                assert (from_store["source"], from_raw["source"]) == ("store", "raw")
                assert from_store["data"] == from_raw["data"]


class TestTheStoreIsReadAlone:
    @pytest.mark.asyncio
    async def test_no_route_opens_the_raw_file(self, stored):
        sample = _sample(stored)
        with _raw_file_unread():
            responses = [
                await _read(ms2_service.get_ms2_summary, sample),
                await _read(ms2_service.get_ms2_averaged_centroids, sample),
                await _read(
                    ms2_service.get_ms2_averaged_centroids, sample, by_activation=True
                ),
                await _read(
                    ms2_service.get_ms2_timeseries,
                    sample,
                    parent_peak_mz=_parent_peaks(stored)[0],
                ),
                await _read(ms2_service.get_ms2_scans, sample, group=_keys(stored)[0]),
            ]

        assert {response["source"] for response in responses} == {"store"}

    @pytest.mark.asyncio
    async def test_another_tolerance_is_answered_from_the_stores_groups(self, stored):
        """Never by a raw read: the store's groups, and the tolerance they
        were made at."""
        sample = _sample(stored)
        asked = {"parent_peak_tolerance": 0.05}
        with _raw_file_unread():
            summary = await _read(ms2_service.get_ms2_summary, sample, **asked)
            spectra = await _read(
                ms2_service.get_ms2_averaged_centroids,
                sample,
                by_activation=True,
                **asked,
            )
            timeseries = await _read(
                ms2_service.get_ms2_timeseries,
                sample,
                parent_peak_mz=_parent_peaks(stored)[0],
                **asked,
            )
            scans = await _read(
                ms2_service.get_ms2_scans, sample, group=_keys(stored)[0], **asked
            )

        for response in (summary, spectra, timeseries, scans):
            assert response["source"] == "store"
            assert response["parent_peak_tolerance"] == 0.001
        assert summary["data"]["parent_peak_tolerance"] == 0.001
        assert (
            summary["data"]
            == (await _read(ms2_service.get_ms2_summary, sample))["data"]
        )

    @pytest.mark.asyncio
    async def test_a_raw_read_says_the_tolerance_it_was_asked(self, stored):
        with _without_store(stored):
            response = await _read(
                ms2_service.get_ms2_averaged_centroids,
                _sample(stored),
                parent_peak_tolerance=0.05,
            )

        assert response["source"] == "raw"
        assert response["parent_peak_tolerance"] == 0.05

    @pytest.mark.asyncio
    async def test_the_timeseries_takes_the_parent_peak_within_the_asked_tolerance(
        self, stored
    ):
        """The groups are the store's; which parent peak the asked m/z names
        is read at the tolerance asked, as the raw read reads it."""
        parent_peak_mz = _parent_peaks(stored)[0]
        sample = _sample(stored)

        near = await _read(
            ms2_service.get_ms2_timeseries,
            sample,
            parent_peak_mz=parent_peak_mz + 0.003,
            parent_peak_tolerance=0.005,
        )
        too_far = await _read(
            ms2_service.get_ms2_timeseries,
            sample,
            parent_peak_mz=parent_peak_mz + 0.003,
        )

        assert near["source"] == too_far["source"] == "store"
        assert near["data"]["values"]
        assert too_far["data"] == {"mz_values": [], "time": [], "values": []}


class TestAFileWithoutAStore:
    @pytest.mark.asyncio
    async def test_answers_from_the_raw_file_and_says_so(self, stored):
        sample = _sample(stored)
        with _without_store(stored):
            responses = [
                await _read(ms2_service.get_ms2_summary, sample),
                await _read(ms2_service.get_ms2_averaged_centroids, sample),
                await _read(
                    ms2_service.get_ms2_timeseries,
                    sample,
                    parent_peak_mz=_parent_peaks(stored)[0],
                ),
                await _read(ms2_service.get_ms2_scans, sample, group=_keys(stored)[0]),
            ]

        assert [r["source"] for r in responses] == ["raw"] * 4
        summary = responses[0]["data"]
        assert summary["ms2_scan_count"] == stored.shape["answers"]["ms2_scan_count"]
        assert [g["scan_count"] for g in summary["groups"]] == [
            g["scan_count"] for g in stored.groups
        ]

    @pytest.mark.asyncio
    async def test_nor_does_one_whose_store_is_of_another_version(self, stored):
        recorded = m_io.read_props(stored.filename)["ms2_store"]
        m_io.update_props(stored.filename, {"ms2_store": {**recorded, "version": 2}})
        try:
            response = await _read(ms2_service.get_ms2_summary, _sample(stored))
        finally:
            m_io.update_props(stored.filename, {"ms2_store": recorded})

        assert response["source"] == "raw"

    @pytest.mark.asyncio
    async def test_nor_one_whose_store_cannot_be_opened(self, stored):
        moved = stored.sample_dir / "ms2.zarr.away"
        (stored.sample_dir / "ms2.zarr").rename(moved)
        try:
            response = await _read(ms2_service.get_ms2_summary, _sample(stored))
        finally:
            moved.rename(stored.sample_dir / "ms2.zarr")

        assert response["source"] == "raw"

    @pytest.mark.asyncio
    async def test_nor_a_sample_with_no_polarity(self, stored):
        """The store's groups are per polarity; the raw read pools both."""
        sample = _sample(stored, polarity=None)
        responses = [
            await _read(ms2_service.get_ms2_summary, sample),
            await _read(ms2_service.get_ms2_averaged_centroids, sample),
            await _read(
                ms2_service.get_ms2_timeseries,
                sample,
                parent_peak_mz=_parent_peaks(stored)[0],
            ),
            await _read(ms2_service.get_ms2_scans, sample, group=_keys(stored)[0]),
        ]

        assert [response["source"] for response in responses] == ["raw"] * 4
        assert responses[3]["data"]["scans"]


def _cutting_window(stored) -> tuple[float, float]:
    """From the first scan to the middle one of the first group's scans."""
    scans = stored.groups[0]["scans"]
    return scan_time_s(1), scan_time_s(scans[len(scans) // 2])


class TestAWindowThatCutsAGroup:
    @pytest.mark.asyncio
    async def test_its_spectra_are_averaged_from_the_raw_file(self, stored):
        """The store holds each group's average over all of its scans."""
        sample = _sample(stored, *_cutting_window(stored))

        from_store, from_raw = await _both(
            stored,
            ms2_service.get_ms2_averaged_centroids,
            sample,
            by_activation=True,
        )

        assert (from_store["source"], from_raw["source"]) == ("raw", "raw")
        assert from_store["data"] == from_raw["data"]

    @pytest.mark.asyncio
    async def test_the_rest_is_read_from_the_store_over_its_own_scans(self, stored):
        sample = _sample(stored, *_cutting_window(stored))
        first = stored.groups[0]

        summary = await _both(stored, ms2_service.get_ms2_summary, sample)
        timeseries = await _both(
            stored,
            ms2_service.get_ms2_timeseries,
            sample,
            parent_peak_mz=first["parent_peak_mz"],
            activation=first["activation"],
        )
        scans = await _both(
            stored, ms2_service.get_ms2_scans, sample, group=_keys(stored)[0]
        )

        for from_store, from_raw in (summary, timeseries, scans):
            assert (from_store["source"], from_raw["source"]) == ("store", "raw")
            assert from_store["data"] == from_raw["data"]
        held = summary[0]["data"]["groups"][0]["scan_count"]
        assert 0 < held < first["scan_count"]
        assert [s["scan_number"] for s in scans[0]["data"]["scans"]] == (
            first["scans"][:held]
        )

    @pytest.mark.asyncio
    async def test_a_window_whole_groups_fall_in_is_read_from_the_store(self, stored):
        """Every group of the shape's, from its first scan to its last."""
        scans = [n for g in stored.groups for n in g["scans"]]
        sample = _sample(stored, scan_time_s(min(scans)), scan_time_s(max(scans)))

        response = await _read(
            ms2_service.get_ms2_averaged_centroids, sample, by_activation=True
        )

        assert response["source"] == "store"
        assert len(response["data"]) == len(stored.groups)


class TestTheScansOfAGroup:
    @pytest.mark.asyncio
    async def test_each_scan_is_the_readers_record_with_its_centroids(self, stored):
        fixture = ANSWERS
        for key, group in zip(_keys(stored), stored.groups, strict=True):
            served = (
                await _read(
                    ms2_service.get_ms2_scans,
                    _sample(stored),
                    group=key,
                    noise_threshold=0.0,
                )
            )["data"]

            assert served["group"] == key
            assert (served["polarity"], served["activation"]) == (
                "+",
                group["activation"],
            )
            assert served["parent_peak_mz"] == group["parent_peak_mz"]
            assert [s["scan_number"] for s in served["scans"]] == group["scans"]
            spectrum = fixture["spectra"][group["spectrum"]]
            for scan in served["scans"]:
                defined = stored.shape["scans"][scan["scan_number"] - 1]
                assert scan["time_s"] == pytest.approx(scan_time_s(scan["scan_number"]))
                assert scan["isolation_width"] == defined["width"]
                assert scan["nce"] == defined["nce"]
                assert scan["hcd_energy_v"] == [spectrum["volts"]]
                assert scan["sources"]["isolation_width"] == "trailer"
                assert scan["sources"]["nce"] == "filter"
                assert scan["centroids"]["mz"] == pytest.approx(
                    [c["mz"] for c in spectrum["centroids"]]
                )

    @pytest.mark.asyncio
    async def test_its_floor_leaves_out_the_weaker_centroids(self, stored):
        key = _keys(stored)[0]
        every = (
            await _read(
                ms2_service.get_ms2_scans, _sample(stored), group=key, noise_threshold=0
            )
        )["data"]["scans"][0]["centroids"]
        floor = sorted(every["signal_to_noise"])[1]

        kept = (
            await _read(
                ms2_service.get_ms2_scans,
                _sample(stored),
                group=key,
                noise_threshold=floor,
            )
        )["data"]["scans"][0]["centroids"]

        assert kept["signal_to_noise"] == [
            sn for sn in every["signal_to_noise"] if sn >= floor
        ]
        assert len(kept["mz"]) == len(every["mz"]) - 1

    @pytest.mark.asyncio
    async def test_a_key_spelt_as_the_centroid_route_spells_it_reads_the_group(
        self, stored
    ):
        group = stored.groups[0]
        spelt = ms2_service.ms2_group_key(group["parent_peak_mz"], group["activation"])

        served = (await _read(ms2_service.get_ms2_scans, _sample(stored), group=spelt))[
            "data"
        ]

        assert served["group"] == _keys(stored)[0]

    @pytest.mark.asyncio
    async def test_a_group_the_sample_does_not_hold_is_not_found(self, stored):
        refusals = await _refusals(
            stored, ms2_service.get_ms2_scans, group="100.0000@hcd30.00"
        )

        assert refusals[0] == refusals[1]
        assert refusals[0][0] == 404

    @pytest.mark.asyncio
    async def test_a_group_the_windows_scans_leave_out_is_not_found(self, stored):
        """The window holds fragment scans, none of this group's."""
        first, last = stored.groups[0], stored.groups[-1]
        if first is last:
            pytest.skip("one group: every fragment scan is its")
        sample = _sample(
            stored, scan_time_s(first["scans"][0]), scan_time_s(first["scans"][0])
        )

        refusals = await _refusals(
            stored, ms2_service.get_ms2_scans, sample, group=_keys(stored)[-1]
        )

        assert refusals[0] == refusals[1]
        assert refusals[0][0] == 404

    @pytest.mark.asyncio
    async def test_a_key_that_is_no_key_is_refused(self, stored):
        with pytest.raises(ApiException) as refused:
            await _read(ms2_service.get_ms2_scans, _sample(stored), group="hcd30.00")

        assert refused.value.status_code == 422


def _stored_mz(stored) -> tuple[np.ndarray, np.ndarray]:
    store = zarr.open_group(ms2_store.store_path(stored.filename), mode="r")
    return np.asarray(store["averaged/mz"][:]), np.asarray(store["centroids/mz"][:])


async def _refusals(stored, route, sample=None, **query) -> list[tuple[int, str]]:
    """How ``route`` refuses, from the store and from the raw file."""
    sample = sample or _sample(stored)
    refused = []
    for store in (True, False):
        with pytest.raises(ApiException) as refusal:
            if store:
                await _read(route, sample, **query)
            else:
                with _without_store(stored):
                    await _read(route, sample, **query)
        refused.append((refusal.value.status_code, refusal.value.user_message))
    return refused


class TestASampleWithNoMs2Scan:
    """Is refused as the raw read refuses it: the store is a faster read of
    the same answers, not other answers."""

    @pytest.fixture(params=["other polarity", "survey scan alone"])
    def sample(self, request, stored):
        if request.param == "other polarity":
            return _sample(stored, polarity="-")
        return _sample(stored, scan_time_s(1), scan_time_s(1))

    @pytest.mark.asyncio
    async def test_its_spectra(self, stored, sample):
        for by_activation in (True, False):
            refusals = await _refusals(
                stored,
                ms2_service.get_ms2_averaged_centroids,
                sample,
                by_activation=by_activation,
            )

            assert refusals[0] == refusals[1]
            assert refusals[0][0] == 400

    @pytest.mark.asyncio
    async def test_its_timeseries(self, stored, sample):
        refusals = await _refusals(
            stored,
            ms2_service.get_ms2_timeseries,
            sample,
            parent_peak_mz=_parent_peaks(stored)[0],
        )

        assert refusals[0] == refusals[1]

    @pytest.mark.asyncio
    async def test_its_scans(self, stored, sample):
        refusals = await _refusals(
            stored, ms2_service.get_ms2_scans, sample, group=_keys(stored)[0]
        )

        assert refusals[0] == refusals[1]

    @pytest.mark.asyncio
    async def test_but_its_summary_is_answered(self, stored, sample):
        if sample.polarity == "-":
            # No scan of that polarity at all: refused, by both
            refusals = await _refusals(stored, ms2_service.get_ms2_summary, sample)
            assert refusals[0] == refusals[1]
            return
        from_store, from_raw = await _both(stored, ms2_service.get_ms2_summary, sample)

        assert from_store["data"] == from_raw["data"]
        assert from_store["data"]["ms2_scan_count"] == 0


class TestTheCalibrationIsAppliedOnRead:
    @pytest.mark.asyncio
    async def test_every_mz_moves_with_it_and_the_store_does_not(self, stored):
        sample = _sample(stored)
        key = _keys(stored)[0]
        before = _stored_mz(stored)
        uncalibrated = await _read(
            ms2_service.get_ms2_averaged_centroids, sample, by_activation=True
        )
        uncalibrated_scans = await _read(ms2_service.get_ms2_scans, sample, group=key)
        uncalibrated_timeseries = await _read(
            ms2_service.get_ms2_timeseries,
            sample,
            parent_peak_mz=_parent_peaks(stored)[0],
        )
        with _calibrated(stored, 1.000002):
            calibrated, from_raw = await _both(
                stored, ms2_service.get_ms2_averaged_centroids, by_activation=True
            )
            scans, scans_raw = await _both(stored, ms2_service.get_ms2_scans, group=key)
            timeseries, timeseries_raw = await _both(
                stored,
                ms2_service.get_ms2_timeseries,
                parent_peak_mz=_parent_peaks(stored)[0],
            )

        assert calibrated["data"] == from_raw["data"]
        assert scans["data"] == scans_raw["data"]
        assert timeseries["data"] == timeseries_raw["data"]
        for spectrum_key, spectrum in calibrated["data"].items():
            assert spectrum["mz"] == pytest.approx(
                [mz * 1.000002 for mz in uncalibrated["data"][spectrum_key]["mz"]],
                rel=1e-12,
            )
        for scan, before_scan in zip(
            scans["data"]["scans"], uncalibrated_scans["data"]["scans"], strict=True
        ):
            assert scan["centroids"]["mz"] == pytest.approx(
                [mz * 1.000002 for mz in before_scan["centroids"]["mz"]], rel=1e-12
            )
        assert timeseries["data"]["mz_values"] == pytest.approx(
            [mz * 1.000002 for mz in uncalibrated_timeseries["data"]["mz_values"]],
            rel=1e-12,
        )
        after = _stored_mz(stored)
        assert all(np.array_equal(b, a) for b, a in zip(before, after, strict=True))


#: In the stepped shape: a scan of the first step of precursor A whose
#: trailer lacks its width, its energy and its injection time, and one of its
#: second step that stepped the energy within the scan.
LACKING_SCAN, STEPPED_WITHIN_SCAN = 2, 9


@pytest.fixture
def trailers_that_differ(tmp_path_factory):
    """The stepped shape, two of whose fragment scans' trailers are unlike
    the rest. For the test alone: its presentation and its filestore are
    patched in while it lasts."""
    scan_parameters = Ms2Acquisition.scan_parameters

    def differing(self, scan_number):
        trailer = scan_parameters(self, scan_number)
        if int(scan_number) == LACKING_SCAN:
            for label in (MS2_ISOLATION_WIDTH, MS2_HCD_ENERGY, MS2_INJECTION_TIME):
                trailer.pop(label)
        if int(scan_number) == STEPPED_WITHIN_SCAN:
            trailer[MS2_HCD_ENERGY] = "7.8,15.6,23.4"
        return trailer

    filestore = tmp_path_factory.mktemp("filestore_trailers")
    with (
        patch.object(Ms2Acquisition, "scan_parameters", differing),
        converted("stepped", filestore) as done,
    ):
        shape = ANSWERS["shapes"]["stepped"]
        yield SimpleNamespace(
            filename=done.props.filename,
            sample_dir=done.sample_dir,
            props=done.props,
            shape=shape,
            groups=shape["answers"]["groups"],
        )


class TestTrailersThatDiffer:
    """A scan whose trailer lacks what the others store, and one that
    stepped the energy within it, read from the store as from the raw file."""

    def test_the_fixture_holds_them(self, trailers_that_differ):
        (lacking,) = [
            g for g in trailers_that_differ.groups if LACKING_SCAN in g["scans"]
        ]
        (within,) = [
            g for g in trailers_that_differ.groups if STEPPED_WITHIN_SCAN in g["scans"]
        ]

        assert lacking is not within

    @pytest.mark.asyncio
    async def test_the_summary(self, trailers_that_differ):
        from_store, from_raw = await _both(
            trailers_that_differ, ms2_service.get_ms2_summary
        )

        assert (from_store["source"], from_raw["source"]) == ("store", "raw")
        assert from_store["data"] == from_raw["data"]
        widths = [g["isolation_widths"] for g in from_store["data"]["groups"]]
        assert [{"isolation_width": 1.2, "scan_count": 2}] in widths
        energies = [len(g["hcd_energy"]) for g in from_store["data"]["groups"]]
        assert 3 in energies

    @pytest.mark.asyncio
    async def test_the_scans(self, trailers_that_differ):
        for key in _keys(trailers_that_differ):
            from_store, from_raw = await _both(
                trailers_that_differ, ms2_service.get_ms2_scans, group=key
            )

            assert from_store["data"] == from_raw["data"]
        lacking = [
            scan
            for key in _keys(trailers_that_differ)
            for scan in (
                await _read(
                    ms2_service.get_ms2_scans,
                    _sample(trailers_that_differ),
                    group=key,
                )
            )["data"]["scans"]
            if scan["scan_number"] == LACKING_SCAN
        ]
        (scan,) = lacking
        assert scan["isolation_width"] is scan["hcd_energy_v"] is None
        assert scan["sources"]["isolation_width"] is None
        assert scan["sources"]["precursor_mz"] == "filter"


@pytest.fixture
def no_width_stored(tmp_path_factory):
    """The manual shape, none of whose fragment scans stores a width. For the
    test alone, as :func:`trailers_that_differ`."""
    scan_parameters = Ms2Acquisition.scan_parameters

    def without_width(self, scan_number):
        trailer = scan_parameters(self, scan_number)
        trailer.pop(MS2_ISOLATION_WIDTH, None)
        return trailer

    filestore = tmp_path_factory.mktemp("filestore_no_width")
    with (
        patch.object(Ms2Acquisition, "scan_parameters", without_width),
        converted("manual", filestore) as done,
    ):
        shape = ANSWERS["shapes"]["manual"]
        yield SimpleNamespace(
            filename=done.props.filename,
            sample_dir=done.sample_dir,
            props=done.props,
            shape=shape,
            groups=shape["answers"]["groups"],
        )


@pytest.mark.asyncio
async def test_a_file_no_scan_of_which_stores_a_width_has_no_summary(no_width_stored):
    """The width is what a client reads the isolation window off: refused, in
    one sentence, from the store as from the raw file. Its spectra are still
    served."""
    refusals = await _refusals(no_width_stored, ms2_service.get_ms2_summary)

    assert refusals[0] == refusals[1]
    assert refusals[0] == (
        400,
        "Failed to get ms2 summary. No MS2 scan stores an isolation width.",
    )
    spectra = await _read(
        ms2_service.get_ms2_averaged_centroids, _sample(no_width_stored)
    )
    assert spectra["source"] == "store"
    assert spectra["data"]
