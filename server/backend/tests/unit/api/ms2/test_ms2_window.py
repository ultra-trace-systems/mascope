"""The isolation-window inventory: each MS2 group's window, and its precursor.

``GET /api/samples/{id}/ms2/window`` lists, per group, the sample's MS1
centroids within +/-(width / 2 + 5 mDa) of its parent key, with their
offsets, shares and the one that is the precursor, beside the precursor the
MS2 store located for the group (``mascope_thermo.thermo.locate_precursors``).
Pinned on each shape of the hermetic MS2 fixture (``tests/ms2_fixture.py``),
whose survey scans carry each precursor and its co-isolate:

- each window holds the precursor as ``is_parent`` with its known share, and
  the co-isolate beside it;
- a width asked for draws every window at it, and windows that then overlap
  list a centroid under each group;
- a group with no width lists its target alone, and says so;
- each group's precursor is the one the store located, followed down its
  energies, on the file's calibrated axis, and a file with no store answers
  the same from its raw file;
- a sample with no MS1 scans answers empty windows, and says why; one that
  holds no group, or of a file whose MS2 scans are not read, says that.

Neither needs a running stack or a site file.
"""

import contextlib
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from ms2_fixture import SHAPES, Ms2Acquisition, answers, converted, scan_time_s

import mascope_file.io as m_io
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.new.ms2 import service as ms2_service
from mascope_signal import ms2_store
from mascope_signal.compute import MS2_WINDOW_PAD
from mascope_thermo.backend import MS2_ISOLATION_WIDTH


ANSWERS = answers()
PRECURSORS = ANSWERS["precursors"]


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


def _sample(stored, t0=None, t1=None, polarity="+"):
    return SimpleNamespace(
        sample_file_id="sf-ms2-window",
        filename=stored.filename,
        sample_item_name="MS2 fixture",
        t0=t0,
        t1=t1,
        polarity=polarity,
    )


async def _window(sample, **query) -> dict:
    with patch.object(ms2_service, "fetch_sample", AsyncMock(return_value=sample)):
        return await ms2_service.get_ms2_window("si-1", **query)


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


def _survey(name: str) -> dict[str, dict]:
    """The survey centroids of precursor ``name``, by role."""
    return {c["role"]: c for c in ANSWERS["survey"] if c["of"] == name}


def _name(group: dict) -> str:
    return ANSWERS["spectra"][group["spectrum"]]["precursor"]


def _listed(window: dict) -> list[dict]:
    """A window's centroids, one record each."""
    columns = window["centroids"]
    return [dict(zip(columns, row)) for row in zip(*columns.values())]


def _at(window: dict, mz: float) -> dict:
    (centroid,) = [c for c in _listed(window) if c["mz"] == pytest.approx(mz)]
    return centroid


class TestEachWindow:
    @pytest.mark.asyncio
    async def test_holds_the_precursor_and_its_co_isolate(self, stored):
        """At the group's own width: the precursor, as ``is_parent``, and the
        co-isolate beside it, each at its known share and nothing else."""
        body = await _window(_sample(stored))

        assert body["source"] == "store"
        assert body["reason"] is None
        assert body["window_pad"] == MS2_WINDOW_PAD == 0.005
        assert [w["group"] for w in body["data"]] == [
            ms2_store.group_key(g["parent_peak_mz"], g["activation"])
            for g in stored.groups
        ]
        for window, group in zip(body["data"], stored.groups, strict=True):
            survey = _survey(_name(group))
            precursor = _at(window, survey["precursor"]["mz"])
            co_isolate = _at(window, survey["co_isolate"]["mz"])
            assert len(_listed(window)) == 2
            assert precursor["is_parent"] and not co_isolate["is_parent"]
            for centroid, known in (
                (precursor, "precursor"),
                (co_isolate, "co_isolate"),
            ):
                assert centroid["share"] == pytest.approx(survey[known]["window_share"])
                assert centroid["intensity"] == pytest.approx(
                    survey[known]["intensity"]
                )
                assert centroid["offset"] == pytest.approx(
                    centroid["mz"] - group["parent_peak_mz"]
                )
                assert centroid["resolution"] > 0
                assert centroid["signal_to_noise"] > 0
            assert window["note"] is None

    @pytest.mark.asyncio
    async def test_is_drawn_at_the_widest_width_of_its_group(self, stored):
        """A group isolated at two widths (the interleaved shape's B) is
        windowed at the wider."""
        body = await _window(_sample(stored))

        for window, group in zip(body["data"], stored.groups, strict=True):
            widths = [w["isolation_width"] for w in group["isolation_widths"]]
            assert window["width"] == max(widths)

    @pytest.mark.asyncio
    async def test_reaches_the_pad_past_its_edge(self, stored):
        """A width that puts the co-isolate just inside the pad lists it, one
        that puts it just outside does not."""
        group = stored.groups[0]
        survey = _survey(_name(group))
        reach = abs(survey["co_isolate"]["mz"] - group["parent_peak_mz"])
        inside = 2 * (reach - MS2_WINDOW_PAD) + 1e-4
        outside = 2 * (reach - MS2_WINDOW_PAD) - 1e-4

        (wide, *_) = (await _window(_sample(stored), width=inside))["data"]
        (narrow, *_) = (await _window(_sample(stored), width=outside))["data"]

        assert len(_listed(wide)) == 2
        assert [c["mz"] for c in _listed(narrow)] == [
            pytest.approx(survey["precursor"]["mz"])
        ]
        assert _listed(narrow)[0]["share"] == pytest.approx(1.0)


class TestAWidthAskedFor:
    @pytest.mark.asyncio
    async def test_overlapping_windows_list_a_centroid_under_each_group(self, stored):
        """At 70 Da, A's window and B's each hold both precursors and both
        co-isolates, and the file's own centroids between them."""
        body = await _window(_sample(stored), width=70.0)

        names = {_name(g) for g in stored.groups}
        if names != {"A", "B"}:
            pytest.skip(f"the {stored.name} shape takes one precursor alone")
        survey = [c["mz"] for c in ANSWERS["survey"]]
        for window, group in zip(body["data"], stored.groups, strict=True):
            listed = _listed(window)
            assert window["width"] == 70.0
            for mz in survey:
                _at(window, mz)
            assert len(listed) > len(survey)
            assert sum(c["share"] for c in listed) == pytest.approx(1.0)
            (parent,) = [c for c in listed if c["is_parent"]]
            assert parent["mz"] == pytest.approx(
                _survey(_name(group))["precursor"]["mz"]
            )
            assert all(abs(c["offset"]) <= 35.0 + MS2_WINDOW_PAD for c in listed)

    @pytest.mark.asyncio
    async def test_is_refused_unless_positive(self):
        from pydantic import ValidationError

        from mascope_backend.api.new.ms2.schema import GetMs2WindowQueryParams

        for width in (0.0, -1.0):
            with pytest.raises(ValidationError):
                GetMs2WindowQueryParams(width=width)
        assert GetMs2WindowQueryParams().width is None


@pytest.fixture(scope="module")
def no_width_stored(tmp_path_factory):
    """The manual shape, none of whose fragment scans stores a width: a file
    with no MS2 summary, as ``test_ms2_store`` converts it."""
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
    async def test_lists_its_target_alone_and_says_so(self, no_width_stored, store):
        with contextlib.ExitStack() as stack:
            if not store:
                stack.enter_context(_without_store(no_width_stored))
            body = await _window(_sample(no_width_stored))
            asked = await _window(_sample(no_width_stored), width=1.0)

        assert body["source"] == ("store" if store else "raw")
        assert len(body["data"]) == len(no_width_stored.groups)
        for window, group in zip(body["data"], no_width_stored.groups, strict=True):
            (target,) = _listed(window)
            assert target["mz"] == pytest.approx(
                _survey(_name(group))["precursor"]["mz"]
            )
            assert target["is_parent"] and target["share"] is None
            assert window["width"] is None
            assert window["note"] == ms2_service.NO_WIDTH_NOTE
            assert window["located_precursor_mz"] == pytest.approx(
                PRECURSORS[_name(group)]["mz"]
            )
        for window in asked["data"]:
            assert window["width"] == 1.0 and window["note"] is None
            assert len(_listed(window)) == 2


class TestTheLocatedPrecursor:
    @pytest.mark.asyncio
    async def test_is_the_stores_followed_down_the_energies(self, stored):
        body = await _window(_sample(stored))
        located = ms2_store.Ms2Store.open(stored.filename).precursors("+")
        lowest = {}
        for group in stored.groups:
            nce = ANSWERS["spectra"][group["spectrum"]]["nce"]
            name = _name(group)
            if name not in lowest or nce < lowest[name][0]:
                lowest[name] = (nce, group["activation"])

        for window, group in zip(body["data"], stored.groups, strict=True):
            spectrum = ANSWERS["spectra"][group["spectrum"]]
            ion = PRECURSORS[spectrum["precursor"]]
            (peak,) = [c for c in spectrum["centroids"] if c["role"] == "precursor"]
            assert window["located_precursor_mz"] == pytest.approx(ion["mz"])
            assert window["located_precursor_activation"] == lowest[_name(group)][1]
            assert window["precursor_mz"] == pytest.approx(ion["mz"])
            assert window["precursor_intensity"] == pytest.approx(peak["intensity"])
            stored_one = located[(group["parent_peak_mz"], group["activation"])]
            assert window["located_precursor_intensity"] == pytest.approx(
                stored_one["located_precursor_intensity"]
            )

    @pytest.mark.asyncio
    async def test_is_on_the_files_calibrated_axis(self, stored):
        with _calibrated(stored, 1.0):
            plain = (await _window(_sample(stored)))["data"]
        with _calibrated(stored, 1.00001):
            shifted = (await _window(_sample(stored)))["data"]

        for before, after in zip(plain, shifted, strict=True):
            for field in ("located_precursor_mz", "precursor_mz"):
                assert after[field] == pytest.approx(before[field] * 1.00001, abs=1e-9)
            assert (
                after["located_precursor_intensity"]
                == (before["located_precursor_intensity"])
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("factor", [None, 1.00003])
    async def test_a_file_with_no_store_answers_the_same_from_its_raw_file(
        self, stored, factor
    ):
        """Calibrated too: both locate on the instrument's axis, around the
        parent keys. 30 ppm is 5.9 mDa at m/z 195, past the 5 mDa search, so
        a raw path that located on the calibrated axis would find nothing."""
        with _calibrated(stored, factor) if factor else contextlib.nullcontext():
            from_store = await _window(_sample(stored))
            with _without_store(stored):
                from_raw = await _window(_sample(stored))

        assert from_raw["source"] == "raw"
        assert len(from_raw["data"]) == len(from_store["data"])
        for raw, store in zip(from_raw["data"], from_store["data"], strict=True):
            assert raw["group"] == store["group"]
            assert raw["width"] == store["width"]
            assert raw["centroids"] == store["centroids"]
            for field in ms2_store.PRECURSOR_FIELDS:
                assert raw[field] == pytest.approx(store[field]), field


@pytest.mark.asyncio
async def test_a_store_written_before_the_precursor_was_located_is_not_read(stored):
    """Its groups record no precursor: version 2 is read as no store, the
    raw file answers, and the backfill writes it again."""
    recorded = m_io.read_props(stored.filename)["ms2_store"]
    m_io.update_props(stored.filename, {"ms2_store": {**recorded, "version": 2}})
    try:
        body = await _window(_sample(stored))
    finally:
        m_io.update_props(stored.filename, {"ms2_store": recorded})

    assert recorded["version"] == ms2_store.VERSION >= 3
    assert body["source"] == "raw"
    assert all(w["located_precursor_mz"] is not None for w in body["data"])


class TestASampleWithout:
    @pytest.mark.asyncio
    async def test_ms1_scans_answers_empty_windows_and_says_why(self, stored):
        """The sample of an acquisition of fragmentation scans alone, as its
        census says: its window holds fragment scans and no survey scan. Its
        groups are listed with their precursors, and no MS1 centroid."""
        first_fragment = min(n for g in stored.groups for n in g["scans"])
        if first_fragment < max(
            n for n, s in enumerate(stored.shape["scans"], start=1) if "ms1" in s
        ):
            pytest.skip(f"the {stored.name} shape interleaves its survey scans")
        sample = _sample(stored, t0=scan_time_s(first_fragment))
        with patch.object(
            ms2_service, "holds_only_fragmentation_scans", AsyncMock(return_value=True)
        ):
            body = await _window(sample)

        assert body["reason"] == ms2_service.REASON_NO_MS1_SCANS == "no_ms1_scans"
        assert "no MS1 scans" in body["message"]
        assert len(body["data"]) == len(stored.groups)
        for window in body["data"]:
            assert _listed(window) == []
            assert window["located_precursor_mz"] is not None

    @pytest.mark.asyncio
    async def test_ms1_scans_of_an_ms1_file_is_the_requests_fault(self, stored):
        """A selection of a file whose census holds MS1 scans that finds none
        fails as it did, rather than answering empty."""
        first_fragment = min(n for g in stored.groups for n in g["scans"])
        if first_fragment < max(
            n for n, s in enumerate(stored.shape["scans"], start=1) if "ms1" in s
        ):
            pytest.skip(f"the {stored.name} shape interleaves its survey scans")
        sample = _sample(stored, t0=scan_time_s(first_fragment))
        with (
            patch.object(
                ms2_service,
                "holds_only_fragmentation_scans",
                AsyncMock(return_value=False),
            ),
            pytest.raises(ApiException) as failed,
        ):
            await _window(sample)
        assert failed.value.status_code == 400

    @pytest.mark.asyncio
    async def test_a_group_says_it_holds_none(self, stored):
        """A sample of the survey scans before any fragment scan."""
        first_fragment = min(n for g in stored.groups for n in g["scans"])
        sample = _sample(stored, t0=scan_time_s(1), t1=scan_time_s(first_fragment - 1))

        body = await _window(sample)

        assert body["reason"] == "none" and body["data"] == []

    @pytest.mark.asyncio
    async def test_a_raw_orbitrap_file_answers_unsupported(self, stored):
        with (
            _without_store(stored),
            patch.object(ms2_service, "_reads_raw_ms2", lambda filename: False),
        ):
            body = await _window(_sample(stored))

        assert body["reason"] == "unsupported"
        assert body["source"] is None and body["data"] == []
