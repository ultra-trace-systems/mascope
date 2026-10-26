"""A file's MS2 store over its life: converted, re-processed, deleted.

The converter writes the store of a raw file whose census holds a
fragmentation stream, whatever path the file takes after - an MS2-only part,
which has no MS1 scan to detect peaks in, included - and a store that cannot
be written costs no file. Re-processing writes it again from the raw file, and
deleting the file deletes it with its sample directory.

On the hermetic MS2 fixture (``tests/ms2_fixture.py``); no stack, no site
file.
"""

import asyncio
import os
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import ms2_fixture
import numpy as np
import pytest
import zarr
from ms2_fixture import answers, converted

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_thermo.processor as raw_processor
import mascope_thermo.thermo as m_thermo
from mascope_backend.api.controllers.sample.files.sample_files_controller import (
    delete_sample_file_from_filestore,
)
from mascope_signal import ms2_store
from mascope_signal.ms2_store import Ms2Store
from mascope_thermo.processor import RawProcessor


MS1_STREAM = {"signature": {"ms_order": 1}}
MS2_STREAM = {"signature": {"ms_order": 2}}


def _arrays(filename: str) -> dict[str, np.ndarray]:
    """Every array of a file's MS2 store, by its path."""
    root = zarr.open_group(ms2_store.store_path(filename), mode="r")
    return {
        f"{group}/{name}": np.asarray(array[:])
        for group, members in root.groups()
        for name, array in members.arrays()
    }


def _same_store(a: dict, b: dict) -> bool:
    return a.keys() == b.keys() and all(np.array_equal(a[k], b[k]) for k in a)


class TestTheConverterStep:
    def _processor(self):
        processor = RawProcessor(
            socket_client=None, file_queue=None, shutdown_event=None
        )
        processor.file_to_process = "ORBI-1_ms2.raw"
        return processor

    def _props(self, *streams):
        return SimpleNamespace(
            filename="ORBI-1_ms2", scan_streams=list(streams), ms2_store={}
        )

    def test_a_file_whose_census_holds_no_fragmentation_gets_none(self):
        props = self._props(MS1_STREAM)
        with patch.object(raw_processor, "write_ms2_store") as write:
            self._processor()._store_ms2(props)

        write.assert_not_called()
        assert props.ms2_store == {}

    def test_nor_does_one_with_no_census(self):
        props = self._props()
        with patch.object(raw_processor, "write_ms2_store") as write:
            self._processor()._store_ms2(props)

        write.assert_not_called()

    def test_one_whose_census_holds_it_gets_it_recorded(self):
        props = self._props(MS1_STREAM, MS2_STREAM)
        with patch.object(
            raw_processor, "write_ms2_store", return_value={"version": 1}
        ) as write:
            self._processor()._store_ms2(props)

        write.assert_called_once_with("ORBI-1_ms2")
        assert props.ms2_store == {"version": 1}

    def test_one_that_cannot_be_written_is_a_warning_and_no_store(self, caplog):
        props = self._props(MS2_STREAM)
        with patch.object(
            raw_processor, "write_ms2_store", side_effect=OSError("disk full")
        ):
            self._processor()._store_ms2(props)

        assert props.ms2_store == {}
        (warning,) = [r for r in caplog.records if r.levelname == "WARNING"]
        assert "Could not write the MS2 store of ORBI-1_ms2" in warning.getMessage()
        assert warning.exc_info is not None


class TestAConversion:
    def test_whose_store_cannot_be_written_still_stores_the_file(self, tmp_path):
        with (
            patch.object(
                raw_processor, "write_ms2_store", side_effect=OSError("disk full")
            ),
            converted("stepped", tmp_path) as done,
        ):
            stored = {path.name for path in done.sample_dir.iterdir()}
            props = m_io.read_props(done.props.filename)

        assert len(done.records) == 1
        assert "ms2.zarr" not in stored
        assert props["ms2_store"] == {}

    def test_of_an_ms2_only_part_writes_its_store_like_any_other(self, tmp_path):
        """The manual shape without its survey scans: fragmentation scans
        alone, stored without peaks, with its store."""
        fixture = answers()
        manual = fixture["shapes"]["manual"]
        manual["scans"] = [scan for scan in manual["scans"] if "ms1" not in scan]
        with (
            patch.object(ms2_fixture, "_answers", lambda: fixture),
            converted("manual", tmp_path) as done,
        ):
            ((_, instrument_function),) = done.records
            filename = done.props.filename
            raw = m_name.filename_to_datafile_path(filename)
            store = Ms2Store.open(filename)

            assert instrument_function is None
            assert done.props.ms2_store["groups"] == [
                {"polarity": "+", "key": "195.0877@hcd20.00", "scan_count": 6}
            ]
            assert store.summary(None, None, "+") == (
                m_thermo.get_ms2_summary_metadata(raw, polarity="+")
            )
            assert store.summary(None, None, "+")["ms1_scan_count"] == 0
            spectra = store.averaged_centroids(None, None, "+")
            raw_spectra = m_thermo.get_ms2_centroids_by_parent(raw, polarity="+")
            assert spectra.keys() == raw_spectra.keys()
            for key, spectrum in spectra.items():
                for stored_values, raw_values in zip(
                    spectrum, raw_spectra[key], strict=True
                ):
                    np.testing.assert_array_equal(stored_values, raw_values)


@pytest.fixture
def stepped(tmp_path):
    with converted("stepped", tmp_path) as done:
        yield done.props.filename


@contextmanager
def _census(filename: str, *streams):
    props = m_io.read_props(filename)
    m_io.update_props(filename, {"scan_streams": list(streams)})
    try:
        yield
    finally:
        m_io.update_props(filename, {"scan_streams": props["scan_streams"]})


class TestReprocessingRewritesIt:
    def test_from_the_raw_file(self, stepped):
        written = _arrays(stepped)
        root = zarr.open_group(ms2_store.store_path(stepped), mode="r+")
        root["averaged/mz"][:] = 0.0

        recorded = ms2_store.refresh_ms2_store(stepped)

        assert _same_store(_arrays(stepped), written)
        assert recorded == m_io.read_props(stepped)["ms2_store"]

    def test_for_a_file_converted_before_the_store_existed(self, stepped):
        recorded = m_io.read_props(stepped)["ms2_store"]
        written = _arrays(stepped)
        m_io.remove_path(ms2_store.store_path(stepped))
        m_io.update_props(stepped, {"ms2_store": {}})
        assert Ms2Store.open(stepped) is None

        assert ms2_store.refresh_ms2_store(stepped) == recorded

        assert m_io.read_props(stepped)["ms2_store"] == recorded
        assert _same_store(_arrays(stepped), written)

    def test_and_removes_it_where_the_census_holds_no_fragmentation(self, stepped):
        with _census(stepped, MS1_STREAM):
            assert ms2_store.refresh_ms2_store(stepped) == {}

            assert m_io.read_props(stepped)["ms2_store"] == {}
            assert not os.path.exists(ms2_store.store_path(stepped))
            assert Ms2Store.open(stepped) is None

    def test_and_removes_one_its_props_lost_track_of(self, stepped):
        """A rewrite that died between taking the name and recording it."""
        m_io.update_props(stepped, {"ms2_store": {}})

        with _census(stepped, MS1_STREAM):
            ms2_store.refresh_ms2_store(stepped)

        assert not os.path.exists(ms2_store.store_path(stepped))

    def test_and_touches_nothing_of_a_file_that_needs_none(self, stepped):
        """Nearly every file re-processed: no store, no lock file for one."""
        ms2_store.remove_ms2_store(stepped)
        directory = os.path.dirname(ms2_store.store_path(stepped))
        os.remove(os.path.join(directory, "ms2.lock"))
        held = set(os.listdir(directory))

        with _census(stepped, MS1_STREAM):
            assert ms2_store.refresh_ms2_store(stepped) == {}

        assert set(os.listdir(directory)) == held

    def test_a_rewrite_that_fails_leaves_the_store_it_had(self, stepped):
        recorded = m_io.read_props(stepped)["ms2_store"]
        written = _arrays(stepped)

        with (
            patch.object(ms2_store, "_write", side_effect=OSError("disk full")),
            pytest.raises(OSError),
        ):
            ms2_store.refresh_ms2_store(stepped)

        assert m_io.read_props(stepped)["ms2_store"] == recorded
        assert _same_store(_arrays(stepped), written)
        beside = os.listdir(os.path.dirname(ms2_store.store_path(stepped)))
        assert [name for name in beside if name.startswith("ms2.zarr.")] == []

    def test_a_rewrite_replaces_what_the_props_record(self, stepped):
        ms2_store.write_ms2_store(stepped, parent_peak_tolerance=0.002)

        assert m_io.read_props(stepped)["ms2_store"]["parent_peak_tolerance"] == 0.002
        assert Ms2Store.open(stepped).parent_peak_tolerance == 0.002


def test_deleting_the_file_deletes_its_store(tmp_path):
    with converted("interleaved", tmp_path) as done:
        path = ms2_store.store_path(done.props.filename)
        assert os.path.isdir(path)

        # Sync, and run here: the conversion runs its own event loop
        asyncio.run(delete_sample_file_from_filestore(done.props.filename))

        assert not os.path.exists(path)
        assert not done.sample_dir.exists()
