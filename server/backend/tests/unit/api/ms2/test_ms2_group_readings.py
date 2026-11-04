"""The readings route: an MS2 group's fragments as formulas, over named ions.

``GET /api/samples/{id}/ms2/readings?group=&ions=`` reads a group's window -
every activation of its parent peak in the sample - with the composition core,
against the ions a caller names. Pinned on each shape of the hermetic MS2
fixture (``tests/ms2_fixture.py``), whose spectra carry known answers:

- the target and its co-isolate named, every centroid reads as the fixture
  says: each fragment its formula and loss, the precursor and the co-isolate
  each its own ion's precursor, the harmonic ghost flagged and out of the TIC,
  the unexplained centroid unexplained;
- an ion no window holds is refused with the nearest key and its offset from
  it, and so is a window none of whose named ions is at its key;
- of two aliases named, the first is read and the other named as left out;
- the same request answers the same data twice, the second from the store's
  cache, and a change to any input - the spectra, the core's version, the
  S/N floor - is read again;
- each window is recalibrated once on its target's located precursor;
- a file with no store answers the same from its raw file, uncached.

Neither needs a running stack or a site file.
"""

import contextlib
import json
import os
import shutil
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
import zarr
from ms2_fixture import SHAPES, Ms2Acquisition, answers, converted, scan_time_s

import mascope_file.io as m_io
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.new.ms2 import readings as ms2_readings
from mascope_backend.api.new.ms2 import service as ms2_service
from mascope_signal import ms2_store
from mascope_thermo.backend import MS2_ISOLATION_WIDTH
from mascope_tools.composition import ms2 as core


ANSWERS = answers()
PRECURSORS = ANSWERS["precursors"]
#: An alias of precursor A's ion: N[15N] in the place of CHO, 0.44 mDa above
#: it, inside the reading tolerance (1 mDa at m/z 195).
A_ALIAS = "C7H10[15N]N5O+"


@pytest.fixture(scope="module", params=SHAPES)
def stored(request, tmp_path_factory):
    """Each shape, converted into a filestore of its own."""
    filestore = tmp_path_factory.mktemp(f"filestore_{request.param}")
    with converted(request.param, filestore) as done:
        shape = ANSWERS["shapes"][request.param]
        yield SimpleNamespace(
            name=request.param,
            filename=done.props.filename,
            shape=shape,
            groups=shape["answers"]["groups"],
        )


@pytest.fixture(autouse=True)
def _cold_cache(request):
    """Every test starts with nothing cached in its file's store."""
    if "stored" in request.fixturenames:
        filename = request.getfixturevalue("stored").filename
        shutil.rmtree(
            f"{ms2_store.store_path(filename)}/{ms2_store.READINGS_CACHE}",
            ignore_errors=True,
        )


def _sample(stored, t0=None, t1=None, polarity="+"):
    return SimpleNamespace(
        sample_file_id="sf-ms2-readings",
        filename=stored.filename,
        sample_item_name="MS2 fixture",
        t0=t0,
        t1=t1,
        polarity=polarity,
    )


async def _readings(sample, group: str, ions, **query) -> dict:
    if not isinstance(ions, str):
        ions = ",".join(ions)
    with patch.object(ms2_service, "fetch_sample", AsyncMock(return_value=sample)):
        return await ms2_service.get_ms2_readings(
            "si-1", group=group, ions=ions, **query
        )


async def _refused(sample, group: str, ions, **query) -> str:
    with pytest.raises(ApiException) as refused:
        await _readings(sample, group, ions, **query)
    assert refused.value.status_code == 422
    return refused.value.user_message


@contextmanager
def _without_store(stored):
    """The file as one converted before the store existed."""
    recorded = m_io.read_props(stored.filename)["ms2_store"]
    m_io.update_props(stored.filename, {"ms2_store": {}})
    try:
        yield
    finally:
        m_io.update_props(stored.filename, {"ms2_store": recorded})


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


def _key(group: dict) -> str:
    return ms2_store.group_key(group["parent_peak_mz"], group["activation"])


def _name(group: dict) -> str:
    return ANSWERS["spectra"][group["spectrum"]]["precursor"]


def _window_of(stored, group: dict) -> list[dict]:
    """The groups of ``group``'s parent peak: its window's spectra."""
    return [g for g in stored.groups if g["parent_peak_mz"] == group["parent_peak_mz"]]


def _ions(name: str) -> list[str]:
    """Precursor ``name``'s ion and its co-isolate's: its window's ions."""
    return [PRECURSORS[name]["formula"], PRECURSORS[name]["co_isolate"]["formula"]]


def _rows(spectrum: dict, mz: float) -> list[dict]:
    """A spectrum's rows of the peak at ``mz``."""
    return [r for r in spectrum["readings"] if r["mz"] == pytest.approx(mz, abs=1e-6)]


def _a(stored) -> dict:
    """The first group of precursor A in the shape."""
    return next(g for g in stored.groups if _name(g) == "A")


class TestTheKnownAnswers:
    @pytest.mark.asyncio
    async def test_every_centroid_reads_as_the_fixture_says(self, stored):
        """Each group, its precursor and co-isolate named: its window's every
        spectrum, and each of its centroids read as the fixture's answers."""
        for group in stored.groups:
            name = _name(group)
            body = await _readings(_sample(stored), _key(group), _ions(name))
            data = body["data"]

            assert body["source"] == "store" and body["reason"] is None
            assert body["core_version"] == core.CORE_VERSION
            assert data["group"] == _key(group)
            assert data["ions_source"] == "named"
            target, co_isolate = _ions(name)
            assert [(i["ion"], i["role"]) for i in data["ions"]] == [
                (target, "target"),
                (co_isolate, "co_isolate"),
            ]
            assert data["left_out"] == []
            window = _window_of(stored, group)
            assert [s["group"] for s in data["spectra"]] == [_key(g) for g in window]
            for spectrum, g in zip(data["spectra"], window, strict=True):
                known = ANSWERS["spectra"][g["spectrum"]]
                assert len({r["peak"] for r in spectrum["readings"]}) == len(
                    known["centroids"]
                )
                for centroid in known["centroids"]:
                    (row,) = _rows(spectrum, centroid["mz"])
                    assert row["intensity"] == pytest.approx(centroid["intensity"])
                    role = centroid["role"]
                    if role == "fragment":
                        assert row["role"] == "fragment"
                        assert row["window_ion"] == target
                        assert row["formula"] == centroid["formula"]
                        assert row["loss"] == centroid["loss"]
                        assert (row["rank"], row["n_candidates"]) == (1, 1)
                        assert row["n_parents"] == 1
                        assert abs(row["ppm"]) < 0.01
                    elif role in ("precursor", "co_isolate"):
                        ion = target if role == "precursor" else co_isolate
                        assert row["role"] == "precursor"
                        assert row["window_ion"] == row["formula"] == ion
                        assert row["loss"] is None and row["pieces"] == []
                    elif role == "harmonic_ghost":
                        assert row["role"] == "harmonic"
                        assert row["formula"] is None and row["rank"] == 0
                        assert row["note"].startswith("2f ghost of the line at")
                    else:
                        assert role == "unexplained"
                        assert row["role"] == "unexplained"
                        assert row["window_ion"] is None and row["ppm"] is None
                ghost = next(
                    c for c in known["centroids"] if c["role"] == "harmonic_ghost"
                )
                tic = sum(c["intensity"] for c in known["centroids"])
                assert spectrum["tic"] == pytest.approx(tic - ghost["intensity"])
                assert spectrum["n_peaks"] == len(known["centroids"]) - 1
                assert spectrum["n_harmonic"] == 1
                assert spectrum["harmonic_intensity"] == ghost["intensity"]
                assert spectrum["n_satellite"] == 0

    @pytest.mark.asyncio
    async def test_every_group_of_a_window_answers_its_one_reading(self, stored):
        """The stepped shape's four energies of A are one window: asked for
        by any of its groups, the same spectra and readings."""
        window = _window_of(stored, _a(stored))
        bodies = [
            (await _readings(_sample(stored), _key(g), _ions("A")))["data"]
            for g in window
        ]

        for data, group in zip(bodies, window, strict=True):
            assert data["group"] == _key(group)
            assert data["spectra"] == bodies[0]["spectra"]

    @pytest.mark.asyncio
    async def test_a_peak_of_a_spectrum_is_the_centroids_routes_at_its_floor(
        self, stored
    ):
        """``peak`` indexes the spectrum ``ms2/centroids`` serves at the same
        S/N floor; a floor above the ghost leaves it out of both."""
        group = _a(stored)
        floor = 1000.0
        with patch.object(
            ms2_service, "fetch_sample", AsyncMock(return_value=_sample(stored))
        ):
            centroids = await ms2_service.get_ms2_averaged_centroids(
                "si-1", noise_threshold=floor, by_activation=True
            )
        body = await _readings(
            _sample(stored), _key(group), _ions("A"), noise_threshold=floor
        )

        served = {
            ms2_store.group_key(s["parent_peak_mz"], s["activation"]): s
            for s in centroids["data"].values()
        }
        assert body["data"]["noise_threshold"] == floor
        for spectrum in body["data"]["spectra"]:
            mz = served[spectrum["group"]]["mz"]
            for row in spectrum["readings"]:
                assert row["mz"] == mz[row["peak"]]
                assert (
                    row["signal_to_noise"]
                    == served[spectrum["group"]]["signal_to_noise"][row["peak"]]
                )
            assert {r["peak"] for r in spectrum["readings"]} == set(range(len(mz)))
            assert all(r["role"] != "harmonic" for r in spectrum["readings"])

    @pytest.mark.asyncio
    async def test_every_row_is_the_cores_reading_of_the_served_spectra(self, stored):
        """The window's spectra as ``ms2/centroids`` serves them, read by the
        core as one window (``read_window`` at ``k_max`` 4, ``ladder_gate``) at
        the response's offset: the same rows, field by field."""
        group = _a(stored)
        with patch.object(
            ms2_service, "fetch_sample", AsyncMock(return_value=_sample(stored))
        ):
            centroids = await ms2_service.get_ms2_averaged_centroids(
                "si-1", by_activation=True
            )
        data = (await _readings(_sample(stored), _key(group), _ions("A")))["data"]
        spectra = {
            ms2_store.group_key(s["parent_peak_mz"], s["activation"]): {
                name: np.asarray(s[name])
                for name in ("mz", "intensity", "signal_to_noise", "resolution")
            }
            for s in centroids["data"].values()
            if s["parent_peak_mz"] == group["parent_peak_mz"]
        }
        rows = core.ladder_gate(
            core.read_window(
                spectra,
                _ions("A"),
                k_max=core.LADDER_K,
                offset_ppm=data["recalibration"]["offset_ppm"],
            )
        )

        def nothing(value):
            if isinstance(value, float) and value != value:
                return None
            return None if value == "" else value

        served = [(s["group"], r) for s in data["spectra"] for r in s["readings"]]
        assert len(served) == len(rows)
        for (key, row), r in zip(served, rows, strict=True):
            assert key == r.spectrum
            assert row == {
                "peak": r.peak,
                "mz": r.mz,
                "mz_cal": r.mz_cal,
                "intensity": r.intensity,
                "signal_to_noise": spectra[key]["signal_to_noise"][r.peak],
                "role": r.role,
                "window_ion": nothing(r.parent_ion),
                "formula": nothing(r.formula),
                "parity": nothing(r.parity),
                "dbe": nothing(r.dbe),
                "ppm": nothing(r.ppm),
                "loss": nothing(r.loss),
                "pieces": list(r.pieces),
                "unnamed_pieces": r.unnamed_pieces,
                "radical_loss": r.radical_loss,
                "rank": r.rank,
                "n_candidates": r.n_candidates,
                "n_parents": r.n_parents,
                "ladder_support": nothing(r.ladder_support),
                "note": nothing(r.flag_note),
            }
        assert data["k_max"] == core.LADDER_K == 4
        assert data["tolerance"] == {"ppm": 5.0, "mda": 0.3}


class TestRefused:
    @pytest.mark.asyncio
    async def test_an_ion_no_window_holds_with_the_nearest_key(self, stored):
        """Precursor B's ion named for A's group: it lies in no window of A,
        and the refusal names the key nearest it - B's, in a shape that takes
        B, else A's own - and how far the ion sits from it."""
        group = _a(stored)
        ion = PRECURSORS["B"]["formula"]
        mz = core.ion_mz(ion)
        nearest = min(stored.groups, key=lambda g: abs(g["parent_peak_mz"] - mz))
        offset = mz - nearest["parent_peak_mz"]
        half = max(w["isolation_width"] for w in group["isolation_widths"]) / 2

        message = await _refused(_sample(stored), _key(group), [*_ions("A"), ion])

        assert message.endswith(
            f"{ion} (m/z {mz:.5f}) lies in no isolation window of the group "
            f"{_key(group)} ({group['parent_peak_mz']:.4f} +/- {half + 0.005:.4f}). "
            f"The nearest key is {_key(nearest)}, {offset * 1e3:+.2f} mDa "
            f"({offset / nearest['parent_peak_mz'] * 1e6:+.1f} ppm) from it."
        )
        assert (_name(nearest) == "B") == (stored.name != "manual")

    @pytest.mark.asyncio
    async def test_a_window_none_of_whose_ions_is_at_its_key(self, stored):
        """The co-isolate alone: in the window, but 22 mDa from the key, so
        no named ion is the group's precursor."""
        group = _a(stored)
        co_isolate = PRECURSORS["A"]["co_isolate"]
        offset = (co_isolate["mz"] - group["parent_peak_mz"]) * 1e3

        message = await _refused(_sample(stored), _key(group), [co_isolate["formula"]])

        assert message.endswith(
            f"No named ion is the precursor of the group {_key(group)}: none lies "
            f"within max(10 ppm, 5 mDa) of its key {group['parent_peak_mz']:.4f}, "
            f"and the nearest, {co_isolate['formula']} (m/z "
            f"{co_isolate['mz']:.5f}), is {offset:+.2f} mDa "
            f"({offset * 1e-3 / group['parent_peak_mz'] * 1e6:+.1f} ppm) from it. "
            "Name the precursor's ion with the co-isolates."
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "ions, why",
        [
            ("C8H11N4O2", "is no ion formula"),
            ("C8H11N4O2+,C8H11N4O2+", "C8H11N4O2+ is named twice"),
            ("C8H11N4O2+,H11C8N4O2+", "C8H11N4O2+ is named twice"),
            ("C8H9N4O2-", "is an anion, and the sample's MS2 scans are of polarity"),
            (" , ", "No ion named"),
        ],
    )
    async def test_a_named_ion_that_is_no_ion_of_the_window(self, stored, ions, why):
        message = await _refused(_sample(stored), _key(_a(stored)), ions)

        assert why in message

    @pytest.mark.asyncio
    async def test_a_group_the_sample_does_not_hold(self, stored):
        with pytest.raises(ApiException) as missing:
            await _readings(_sample(stored), "195.0877@hcd99.00", _ions("A"))

        assert missing.value.status_code == 404
        assert "holds no MS2 group '195.0877@hcd99.00'" in missing.value.user_message

    @pytest.mark.asyncio
    async def test_a_key_that_is_no_group_key(self, stored):
        message = await _refused(_sample(stored), "hcd20.00", _ions("A"))

        assert "is no MS2 group key" in message


class TestAliases:
    def test_the_alias_is_inside_the_reading_tolerance(self):
        ion, alias = core.ion_mz(PRECURSORS["A"]["formula"]), core.ion_mz(A_ALIAS)

        assert (alias - ion) * 1e3 == pytest.approx(0.44, abs=0.01)
        assert abs(alias - ion) <= core.DEFAULT_TOLERANCE.window_da(alias)

    @pytest.mark.asyncio
    async def test_two_named_read_one_the_first(self, stored):
        group = _a(stored)
        target, co_isolate = _ions("A")

        body = await _readings(_sample(stored), _key(group), [target, A_ALIAS])
        plain = await _readings(_sample(stored), _key(group), [target])

        data = body["data"]
        assert [i["ion"] for i in data["ions"]] == [target]
        (left_out,) = data["left_out"]
        assert left_out["ion"] == A_ALIAS and left_out["alias_of"] == target
        assert left_out["offset_mda"] == pytest.approx(0.44, abs=0.01)
        assert left_out["note"].startswith(f"{A_ALIAS} left out: +0.44 mDa from")
        assert data["spectra"] == plain["data"]["spectra"]
        rows = [r for s in data["spectra"] for r in s["readings"]]
        assert {r["window_ion"] for r in rows} == {target, None}

    @pytest.mark.asyncio
    async def test_named_first_the_alias_is_the_one_read(self, stored):
        group = _a(stored)
        target, co_isolate = _ions("A")

        data = (
            await _readings(_sample(stored), _key(group), [A_ALIAS, co_isolate, target])
        )["data"]

        assert [(i["ion"], i["role"]) for i in data["ions"]] == [
            (A_ALIAS, "target"),
            (co_isolate, "co_isolate"),
        ]
        assert [(o["ion"], o["alias_of"]) for o in data["left_out"]] == [
            (target, A_ALIAS)
        ]


class TestTheCache:
    @pytest.mark.asyncio
    async def test_the_same_request_answers_the_same_data_twice(self, stored):
        group = _a(stored)

        first = await _readings(_sample(stored), _key(group), _ions("A"))
        second = await _readings(_sample(stored), _key(group), _ions("A"))

        assert (first["cached"], second["cached"]) == (False, True)
        assert json.dumps(first["data"]) == json.dumps(second["data"])
        assert first["message"] == second["message"]
        assert first["source"] == second["source"] == "store"

    @pytest.mark.asyncio
    async def test_is_the_stores_and_bounded(self, stored):
        group = _a(stored)
        store = ms2_store.Ms2Store.open(stored.filename)

        with patch.object(ms2_store, "READINGS_CACHE_ENTRIES", 2):
            for floor in (1.0, 2.0, 3.0):
                await _readings(
                    _sample(stored), _key(group), _ions("A"), noise_threshold=floor
                )
            again = [
                (
                    await _readings(
                        _sample(stored), _key(group), _ions("A"), noise_threshold=f
                    )
                )["cached"]
                for f in (3.0, 2.0)
            ]
        cache = store.root[ms2_store.READINGS_CACHE]

        assert again == [True, True]
        assert len(cache.attrs["order"]) == 2
        assert sorted(name for name, _ in cache.arrays()) == sorted(
            cache.attrs["order"]
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "change",
        ["ions", "order", "floor", "calibration", "core", "format"],
    )
    async def test_a_change_to_any_input_is_read_again(self, stored, change):
        group = _a(stored)
        target, co_isolate = _ions("A")
        ions, floor = [target, co_isolate], 10.0
        await _readings(_sample(stored), _key(group), ions, noise_threshold=floor)

        with contextlib.ExitStack() as stack:
            if change == "ions":
                ions = [target]
            elif change == "order":
                ions = [co_isolate, target]
            elif change == "floor":
                floor = 11.0
            elif change == "calibration":
                stack.enter_context(_calibrated(stored, 1.000002))
            elif change == "core":
                stack.enter_context(
                    patch.object(ms2_readings, "CORE_VERSION", core.CORE_VERSION + 1)
                )
            else:
                stack.enter_context(
                    patch.object(
                        ms2_readings,
                        "READINGS_FORMAT",
                        ms2_readings.READINGS_FORMAT + 1,
                    )
                )
            again = await _readings(
                _sample(stored), _key(group), ions, noise_threshold=floor
            )

        assert again["cached"] is False

    def test_an_entry_whose_write_did_not_finish_is_not_served(self, stored):
        """An entry the cache's order does not list - its write cut short
        before the order was written - is no entry, and is written anew."""
        store = ms2_store.Ms2Store.open(stored.filename)
        assert store.cache_readings("first", b'{"a": 1}')
        root = zarr.open_group(
            ms2_store.store_path(stored.filename), mode="r+", use_consolidated=False
        )
        cut = np.zeros(8, dtype=np.uint8)
        root[ms2_store.READINGS_CACHE].create_array(
            "cut", shape=cut.shape, dtype=cut.dtype
        )

        assert store.cached_readings("cut") is None
        assert store.cached_readings("first") == b'{"a": 1}'
        assert store.cache_readings("cut", b'{"b": 2}')
        assert store.cached_readings("cut") == b'{"b": 2}'
        assert store.root[ms2_store.READINGS_CACHE].attrs["order"] == ["first", "cut"]

    def test_an_entry_listed_with_no_bytes_is_written_once_more(self, stored):
        """An eviction cut short after its entry went and before the order
        was written: the order lists an entry the cache does not hold."""
        store = ms2_store.Ms2Store.open(stored.filename)
        assert store.cache_readings("first", b"[1]")
        cache = zarr.open_group(
            ms2_store.store_path(stored.filename), mode="r+", use_consolidated=False
        )[ms2_store.READINGS_CACHE]
        cache.attrs["order"] = ["first", "gone"]

        assert store.cached_readings("gone") is None
        assert store.cache_readings("gone", b"[2]")

        assert store.cached_readings("gone") == b"[2]"
        assert store.root[ms2_store.READINGS_CACHE].attrs["order"] == ["first", "gone"]

    def test_caching_one_key_twice_keeps_one_entry(self, stored):
        store = ms2_store.Ms2Store.open(stored.filename)

        assert store.cache_readings("key", b"[1]")
        assert store.cache_readings("key", b"[2]")

        assert store.cached_readings("key") == b"[1]"
        assert store.root[ms2_store.READINGS_CACHE].attrs["order"] == ["key"]

    def test_nothing_is_cached_in_a_store_removed_since_it_was_opened(
        self, stored, monkeypatch
    ):
        store = ms2_store.Ms2Store.open(stored.filename)
        gone = ms2_store.store_path(stored.filename).replace("ms2.zarr", "gone.zarr")
        monkeypatch.setattr(ms2_store, "store_path", lambda filename: gone)

        assert store.cache_readings("key", b"[1]") is False
        assert not os.path.exists(gone)

    def test_nothing_is_cached_while_another_writer_holds_the_store(
        self, stored, monkeypatch
    ):
        @contextmanager
        def held(path, timeout=None):
            assert timeout == ms2_store.READINGS_CACHE_LOCK_TIMEOUT
            raise TimeoutError(path)
            yield

        store = ms2_store.Ms2Store.open(stored.filename)
        monkeypatch.setattr(ms2_store.m_io, "zarr_write_lock", held)

        assert store.cache_readings("key", b"[1]") is False
        assert store.cached_readings("key") is None

    @pytest.mark.asyncio
    async def test_a_cache_that_cannot_be_written_serves_the_readings(self, stored):
        group = _a(stored)
        failing = patch.object(
            ms2_store.Ms2Store, "cache_readings", side_effect=OSError("read-only")
        )

        with failing:
            first = await _readings(_sample(stored), _key(group), _ions("A"))
            again = await _readings(_sample(stored), _key(group), _ions("A"))

        assert first["cached"] is again["cached"] is False
        assert first["data"] == again["data"]
        assert first["data"]["spectra"]

    @pytest.mark.asyncio
    async def test_a_file_with_no_store_answers_the_same_from_its_raw_file(
        self, stored
    ):
        group = _a(stored)
        from_store = await _readings(_sample(stored), _key(group), _ions("A"))
        with _without_store(stored):
            first = await _readings(_sample(stored), _key(group), _ions("A"))
            second = await _readings(_sample(stored), _key(group), _ions("A"))

        assert first["source"] == second["source"] == "raw"
        assert first["cached"] is second["cached"] is False
        assert first["data"] == second["data"] == from_store["data"]

    @pytest.mark.asyncio
    async def test_a_window_cut_by_the_sample_is_read_raw_and_cached(self, stored):
        """A sample of the first fragment scan of A alone: its spectrum is
        averaged from the raw file over that scan, under the store's group."""
        group = _a(stored)
        first = min(group["scans"])
        sample = _sample(stored, t0=scan_time_s(first), t1=scan_time_s(first))

        body = await _readings(sample, _key(group), _ions("A"))
        again = await _readings(sample, _key(group), _ions("A"))

        assert body["source"] == again["source"] == "raw"
        assert (body["cached"], again["cached"]) == (False, True)
        assert [s["group"] for s in body["data"]["spectra"]] == [_key(group)]


class TestTheRead:
    @pytest.mark.asyncio
    async def test_of_a_cut_window_reads_no_other_parent_peaks_scans(
        self, stored, monkeypatch
    ):
        """A sample of a fragment scan of A and the next one, of another
        group: read raw under the store's groups, and only A's scans."""
        group = _a(stored)
        first = min(group["scans"])
        sample = _sample(stored, t0=scan_time_s(first), t1=scan_time_s(first + 1))
        asked = []
        averaged = ms2_service.m_compute.get_orbi_ms2_centroids_of_scans

        async def recorded(filename, scans, ppm=1):
            asked.append(dict(scans))
            return await averaged(filename, scans, ppm=ppm)

        monkeypatch.setattr(
            ms2_service.m_compute, "get_orbi_ms2_centroids_of_scans", recorded
        )

        body = await _readings(sample, _key(group), _ions("A"))

        (scans,) = asked
        assert scans and {pp for pp, _ in scans} == {group["parent_peak_mz"]}
        assert body["source"] == "raw"
        # The interleaved shape's next scan is B's, which is not read
        assert any(
            first + 1 in g["scans"] and _name(g) == "B" for g in stored.groups
        ) == (stored.name == "interleaved")

    @pytest.mark.asyncio
    async def test_that_takes_too_long_answers_504(self, stored, monkeypatch):
        def slow(*args, **kwargs):
            time.sleep(0.5)
            return b"{}"

        monkeypatch.setattr(ms2_readings, "payload", slow)

        with pytest.raises(ApiException) as late:
            await _readings(_sample(stored), _key(_a(stored)), _ions("A"), timeout=0.05)

        assert late.value.status_code == 504


class TestTheRecalibration:
    @pytest.mark.asyncio
    async def test_is_one_offset_on_the_targets_located_precursor(self, stored):
        """The file calibrated 3 ppm up: the window's offset is +3 ppm, on
        the precursor located on that axis, and its readings are those of the
        uncalibrated file but for the m/z served."""
        group = _a(stored)
        plain = (await _readings(_sample(stored), _key(group), _ions("A")))["data"]
        with _calibrated(stored, 1.000003):
            shifted = (await _readings(_sample(stored), _key(group), _ions("A")))[
                "data"
            ]

        for data, factor in ((plain, 1.0), (shifted, 1.000003)):
            recalibration = data["recalibration"]
            assert recalibration["anchor_ion"] == PRECURSORS["A"]["formula"]
            assert recalibration["anchor_mz"] == pytest.approx(
                PRECURSORS["A"]["mz"] * factor, abs=1e-9
            )
            assert recalibration["offset_ppm"] == pytest.approx(
                (factor - 1) * 1e6, abs=0.01
            )
            assert recalibration["residual_ppm"] == pytest.approx(0.0, abs=1e-6)
            assert recalibration["note"] is None
        for before, after in zip(plain["spectra"], shifted["spectra"], strict=True):
            for b, a in zip(before["readings"], after["readings"], strict=True):
                assert a["mz"] == pytest.approx(b["mz"] * 1.000003, abs=1e-9)
                assert a["mz_cal"] == pytest.approx(b["mz_cal"], abs=1e-9)
                assert (a["role"], a["formula"], a["rank"]) == (
                    b["role"],
                    b["formula"],
                    b["rank"],
                )

    @pytest.mark.asyncio
    async def test_is_left_out_when_the_located_peak_is_not_the_targets(self, stored):
        """Calibrated 30 ppm up, the located precursor sits 30 ppm from A's
        ion: it is not taken for its precursor, the window is read with no
        offset, and the note gives the offset that would match it."""
        group = _a(stored)
        with _calibrated(stored, 1.00003):
            data = (await _readings(_sample(stored), _key(group), _ions("A")))["data"]

        recalibration = data["recalibration"]
        assert recalibration["offset_ppm"] == 0.0
        assert recalibration["anchor_mz"] is None
        assert "is +30.0 ppm from C8H11N4O2+" in recalibration["note"]
        assert recalibration["note"].endswith("An offset of +30.0 ppm would match it.")
        for spectrum in data["spectra"]:
            for row in spectrum["readings"]:
                assert row["mz_cal"] == row["mz"]


@pytest.fixture(scope="module")
def no_width_stored(tmp_path_factory):
    """The manual shape, none of whose fragment scans stores a width."""
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
            name="manual",
            filename=done.props.filename,
            shape=shape,
            groups=shape["answers"]["groups"],
        )


class TestAGroupWithNoWidth:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("store", [True, False], ids=["store", "raw"])
    async def test_holds_its_precursor_alone(self, no_width_stored, store):
        (group,) = no_width_stored.groups
        target, co_isolate = _ions("A")
        with contextlib.ExitStack() as stack:
            if not store:
                stack.enter_context(_without_store(no_width_stored))
            body = await _readings(_sample(no_width_stored), _key(group), [target])
            message = await _refused(
                _sample(no_width_stored), _key(group), [target, co_isolate]
            )

        assert body["data"]["width"] is None
        assert [i["ion"] for i in body["data"]["ions"]] == [target]
        assert f"{co_isolate} (m/z" in message
        assert "whose scans store no isolation width" in message


class TestASample:
    @pytest.mark.asyncio
    async def test_of_a_file_whose_ms2_scans_are_not_read_says_so(self, stored):
        with (
            _without_store(stored),
            patch.object(ms2_service, "_reads_raw_ms2", lambda filename: False),
        ):
            body = await _readings(_sample(stored), _key(_a(stored)), _ions("A"))

        assert body["reason"] == "unsupported"
        assert body["source"] is None and body["cached"] is False
        assert body["data"]["spectra"] == [] and body["data"]["ions"] == []
        assert body["data"]["group"] == _key(_a(stored))


# ------------------------------------------------------- the window rules ----
def _named(*ions: str) -> list[ms2_readings.NamedIon]:
    return ms2_readings.parse_ions(",".join(ions), "+")


def _group(mz: float, activation: str = "hcd20.00") -> dict:
    return {"parent_peak_mz": mz, "activation": activation}


class TestTheWindowRules:
    def test_an_ion_in_the_pad_past_the_edge_is_in_the_window(self):
        """+/-(width / 2 + 5 mDa): an ion 4.9 mDa past the edge is held, one
        5.1 mDa past it is not."""
        target = core.ion_mz("C8H11N4O2+")
        co = core.ion_mz("C10H11O4+")
        width = 2 * (abs(co - target) - 0.0049)

        held = ms2_readings.window_ions(
            _named("C8H11N4O2+", "C10H11O4+"),
            _group(target),
            width,
            target,
            [_group(target)],
        )
        with pytest.raises(ms2_readings.IonRefused, match="lies in no isolation"):
            ms2_readings.window_ions(
                _named("C8H11N4O2+", "C10H11O4+"),
                _group(target),
                width - 0.0004,
                target,
                [_group(target)],
            )

        assert [n.ion for n in held.ions] == ["C8H11N4O2+", "C10H11O4+"]

    def test_the_nearest_key_is_the_nearest_groups(self):
        groups = [_group(100.0), _group(163.123, "hcd30.00"), _group(195.0877)]

        with pytest.raises(ms2_readings.IonRefused) as refused:
            ms2_readings.window_ions(
                _named("C8H11N4O2+", "C10H15N2+"), groups[2], 1.0, None, groups
            )

        assert "The nearest key is 163.1230@hcd30.00, -0.03 mDa" in str(refused.value)

    def test_the_target_is_the_ion_at_the_key_nearest_the_located_precursor(self):
        """C8H9O2+ and C4H10[15N]O4+, two ions within 5 mDa of a key typed
        with two decimals, 2.2 mDa apart, past the reading tolerance: the
        target is the one nearest the located precursor, whichever is named
        first, and the other is read as a co-isolate."""
        first, second = "C8H9O2+", "C4H10[15N]O4+"
        a, b = core.ion_mz(first), core.ion_mz(second)
        assert 1.5e-3 < abs(a - b) < 5e-3
        key = round((a + b) / 2, 2)

        for located, target in ((a, first), (b, second), (None, None)):
            window = ms2_readings.window_ions(
                _named(first, second), _group(key), 1.0, located, [_group(key)]
            )
            if target is None:
                target = first if abs(a - key) <= abs(b - key) else second
            assert window.target.ion == target
            assert [n.ion for n in window.ions] == [
                target,
                second if target == first else first,
            ]

    def test_the_target_is_read_first_wherever_it_is_named(self):
        window = ms2_readings.window_ions(
            _named("C10H11O4+", "C8H11N4O2+"),
            _group(195.0877),
            1.0,
            core.ion_mz("C8H11N4O2+"),
            [_group(195.0877)],
        )

        assert ms2_readings.served_ions(window) == [
            {"ion": "C8H11N4O2+", "mz": core.ion_mz("C8H11N4O2+"), "role": "target"},
            {"ion": "C10H11O4+", "mz": core.ion_mz("C10H11O4+"), "role": "co_isolate"},
        ]

    def test_caret_labels_are_read_and_written_as_mascope_writes_them(self):
        (named,) = _named("C7H10^NN5O+")

        assert named.ion == A_ALIAS and named.mz == core.ion_mz(A_ALIAS)


class TestTheOffset:
    target = ms2_readings.NamedIon("C8H11N4O2+", core.ion_mz("C8H11N4O2+"))

    def test_is_measured_on_a_located_precursor_within_10_ppm(self):
        located = self.target.mz * (1 + 9.9e-6)

        recalibration = ms2_readings.recalibration(self.target, located)

        assert recalibration["offset_ppm"] == pytest.approx(9.9, abs=1e-6)
        assert recalibration["anchor_mz"] == located
        assert recalibration["anchor_ion"] == self.target.ion
        assert recalibration["note"] is None

    def test_is_none_beyond_it_and_says_what_would_match(self):
        located = self.target.mz * (1 + 10.1e-6)

        recalibration = ms2_readings.recalibration(self.target, located)

        assert recalibration["offset_ppm"] == 0.0
        assert recalibration["anchor_mz"] is None
        assert recalibration["note"].endswith("An offset of +10.1 ppm would match it.")

    def test_has_an_absolute_floor_at_low_mass(self):
        """At m/z 20, 10 ppm is 0.2 mDa: a precursor 0.29 mDa off is still the
        target's."""
        target = ms2_readings.NamedIon("H4O+", core.ion_mz("H4O+"))

        recalibration = ms2_readings.recalibration(target, target.mz + 0.00029)

        assert recalibration["note"] is None
        assert recalibration["offset_ppm"] > 10.0

    def test_is_none_with_no_located_precursor(self):
        recalibration = ms2_readings.recalibration(self.target, None)

        assert recalibration["offset_ppm"] == 0.0
        assert recalibration["note"].startswith("No precursor was located")


class TestTheCacheKey:
    @staticmethod
    def _spectra(mz=83.06) -> dict:
        return {
            "195.0877@hcd20.00": {
                name: np.array([mz if name == "mz" else 10.0])
                for name in ("mz", "intensity", "signal_to_noise", "resolution")
            }
        }

    def test_is_moved_by_every_input(self):
        named = _named("C8H11N4O2+")
        args = ("195.0877@hcd20.00", named, self._spectra(), 1.0, 195.0877, 10.0)
        changed = [
            ("195.0877@hcd10.00", named, self._spectra(), 1.0, 195.0877, 10.0),
            (args[0], _named("C8H11N4O2+", "C10H11O4+"), *args[2:]),
            (*args[:2], self._spectra(mz=83.061), *args[3:]),
            (*args[:3], 1.2, *args[4:]),
            (*args[:4], None, 10.0),
            (*args[:5], 3.0),
        ]

        keys = {ms2_readings.cache_key(*a) for a in changed}

        assert ms2_readings.cache_key(*args) == ms2_readings.cache_key(*args)
        assert ms2_readings.cache_key(*args) not in keys
        assert len(keys) == len(changed)


class TestTheLadder:
    """Glucosamine + [15N]H4+ (the core's hash-seed case): its fourth step
    C6H7O3+ is read, four pieces, only where the step before it, C6H9O4+, is
    read at another energy of the window."""

    ion = "C6H17N[15N]O5+"

    def _spectrum(self, *ions: str) -> dict:
        return {
            "mz": np.array([core.ion_mz(i) for i in ions]),
            "intensity": np.array([200.0, 100.0]),
            "signal_to_noise": np.array([50.0, 25.0]),
            "resolution": np.full(2, np.nan),
        }

    def _fourth(self, spectra: dict) -> list[dict]:
        target = ms2_readings.NamedIon(self.ion, core.ion_mz(self.ion))
        window = ms2_readings.WindowIons(target, (target,), ())
        read = ms2_readings.read_group(spectra, window, 0.0)
        return [r for s in read for r in s["readings"] if r["peak"] == 1]

    def test_on_the_step_before_it(self):
        rows = self._fourth(
            {
                "198.1102@hcd1.00": self._spectrum(self.ion, "C6H9O4+"),
                "198.1102@hcd10.00": self._spectrum(self.ion, "C6H7O3+"),
            }
        )

        assert [
            (r["formula"], len(r["pieces"]), r["ladder_support"]) for r in rows
        ] == [
            ("C6H9O4+", 3, None),
            ("C6H7O3+", 4, "H8[15N]NO"),
        ]

    def test_and_not_without_it(self):
        (row,) = self._fourth(
            {"198.1102@hcd10.00": self._spectrum(self.ion, "C6H7O3+")}
        )

        assert row["role"] == "unexplained" and row["formula"] is None


def test_a_spectrum_with_no_peak_above_the_floor_reads_as_nothing():
    target = ms2_readings.NamedIon("C8H11N4O2+", core.ion_mz("C8H11N4O2+"))
    window = ms2_readings.WindowIons(target, (target,), ())
    empty = {
        name: np.array([])
        for name in ("mz", "intensity", "signal_to_noise", "resolution")
    }

    (spectrum,) = ms2_readings.read_group({"195.0877@hcd20.00": empty}, window, 0.0)

    assert spectrum == {
        "group": "195.0877@hcd20.00",
        "tic": 0.0,
        "n_peaks": 0,
        "n_harmonic": 0,
        "harmonic_intensity": 0.0,
        "n_satellite": 0,
        "satellite_intensity": 0.0,
        "readings": [],
    }
