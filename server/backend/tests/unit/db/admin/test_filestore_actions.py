"""
Unit tests for the filestore sweep actions.

Both actions here glob a sample directory and delete what matches, and both
became sharper when zarr 2's ``ProcessSynchronizer`` was replaced by a
``fasteners`` lock: the side-car beside each store changed from a ``<var>.sync``
*directory* into a ``<var>.lock`` *file*, and ``shutil.rmtree`` raises
``NotADirectoryError`` on a file. ``delete_sum_signal``'s glob matches that lock,
so without the fix it dies on the first sample file it meets and leaves the
filestore half-cleaned.

Kept hermetic - the sample directories are built in a tmp_path and the database
lookup is mocked - so this runs on every CI job rather than only where a
database happens to be up.
"""

import os
from types import SimpleNamespace

import pytest

from mascope_backend.db.admin import filestore


@pytest.fixture
def filestore_with_samples(tmp_path, monkeypatch):
    """Build two sample directories and point the actions at them.

    Each holds a store, its lock side-car, and a leftover zarr 2 sync
    directory - the exact mix a filestore has after the upgrade.
    """
    samples = ["SampleA_1001.01.01_12h00m00s_One", "SampleB_1001.01.01_12h00m00s_Two"]
    paths = {}
    for name in samples:
        sample_dir = tmp_path / name
        for store in ("sum_signal", "sum_signal_a1b2c3d4", "peak_timeseries"):
            (sample_dir / f"{store}.zarr").mkdir(parents=True)
            (sample_dir / f"{store}.sync").mkdir(parents=True)
            (sample_dir / f"{store}.lock").write_text("")
        paths[name] = sample_dir

    async def fake_fetch_sample_files():
        return [SimpleNamespace(filename=name) for name in samples]

    monkeypatch.setattr(filestore, "fetch_sample_files", fake_fetch_sample_files)
    monkeypatch.setattr(
        filestore,
        "parse_path_from_item_filename",
        lambda name: str(paths[name]),
    )
    return paths


class TestDeleteSumSignal:
    """The sweep must survive the lock file its own glob now matches."""

    @pytest.mark.asyncio
    async def test_removes_stores_and_their_side_cars(self, filestore_with_samples):
        """Both sum-signal stores go, and so do their side-cars.

        Regression test: `sum_signal*` matches `sum_signal.lock`, and rmtree on
        a regular file raises NotADirectoryError. Nothing catches it, so the
        whole sweep used to abort on the first sample file.
        """
        await filestore.delete_sum_signal()

        for sample_dir in filestore_with_samples.values():
            remaining = sorted(os.listdir(sample_dir))
            assert not [n for n in remaining if n.startswith("sum_signal")], (
                f"sum_signal entries survived the sweep: {remaining}"
            )

    @pytest.mark.asyncio
    async def test_leaves_unrelated_stores_alone(self, filestore_with_samples):
        """Only sum_signal is swept; peak_timeseries must be untouched."""
        await filestore.delete_sum_signal()

        for sample_dir in filestore_with_samples.values():
            assert (sample_dir / "peak_timeseries.zarr").is_dir()
            assert (sample_dir / "peak_timeseries.lock").is_file()

    @pytest.mark.asyncio
    async def test_cached_only_spares_the_full_sum_signal(self, filestore_with_samples):
        """cached_only narrows the glob to the hashed caches."""
        await filestore.delete_sum_signal(cached_only=True)

        for sample_dir in filestore_with_samples.values():
            assert (sample_dir / "sum_signal.zarr").is_dir()
            assert not (sample_dir / "sum_signal_a1b2c3d4.zarr").exists()

    @pytest.mark.asyncio
    async def test_sweeps_every_sample_file(self, filestore_with_samples):
        """The sweep must not stop at the first sample file.

        This is the property the NotADirectoryError actually broke: the second
        sample was never reached.
        """
        await filestore.delete_sum_signal()

        assert len(filestore_with_samples) > 1
        for sample_dir in filestore_with_samples.values():
            assert not (sample_dir / "sum_signal.zarr").exists()


@pytest.fixture
def caches_of_every_vintage(tmp_path, monkeypatch):
    """A raw Orbitrap sample holding sum-signal caches of every vintage, beside
    a TOF sample whose caches are named as they always were.

    A raw Orbitrap cache is named after the reader and averaging that computed
    it, so the stale ones are: those from before the names carried it, the
    drawings the spectrum views once showed (``_recon``), and another reader's.
    """
    current = filestore.sum_signal_suffix("orbi_raw")
    stores = {
        "OrbiSample_1001.01.01_12h00m00s_One": {
            "current": [f"sum_signal{current}", f"sum_signal_a1b2c3d4e5f6{current}"],
            "stale": [
                "sum_signal",
                "sum_signal_a1b2c3d4e5f6",
                "sum_signal_a1b2c3d4e5f6_recon",
                "sum_signal_a1b2c3d4e5f6.otf0.0.0-g0",
            ],
        },
        "TofSample_1001.01.01_12h00m00s_Two": {
            "current": ["sum_signal", "sum_signal_a1b2c3d4e5f6"],
            "stale": [],
        },
    }
    types = {
        "OrbiSample_1001.01.01_12h00m00s_One": "orbi_raw",
        "TofSample_1001.01.01_12h00m00s_Two": "tof_h5",
    }
    paths = {}
    for name, kinds in stores.items():
        sample_dir = tmp_path / name
        for store in [*kinds["current"], *kinds["stale"], "peak_timeseries"]:
            (sample_dir / f"{store}.zarr").mkdir(parents=True)
            (sample_dir / f"{store}.lock").write_text("")
        paths[name] = sample_dir

    async def fake_fetch_sample_files():
        return [SimpleNamespace(filename=name) for name in stores]

    monkeypatch.setattr(filestore, "fetch_sample_files", fake_fetch_sample_files)
    monkeypatch.setattr(
        filestore, "parse_path_from_item_filename", lambda name: str(paths[name])
    )
    monkeypatch.setattr(filestore, "get_sample_file_type", lambda name: types[name])
    return {name: (paths[name], kinds) for name, kinds in stores.items()}


class TestDeleteStaleSumSignal:
    """The reclaim of raw Orbitrap caches the current reader no longer reads."""

    @pytest.mark.asyncio
    async def test_removes_what_the_current_reader_would_not_read(
        self, caches_of_every_vintage
    ):
        await filestore.delete_stale_sum_signal()

        sample_dir, kinds = caches_of_every_vintage[
            "OrbiSample_1001.01.01_12h00m00s_One"
        ]
        for store in kinds["stale"]:
            assert not (sample_dir / f"{store}.zarr").exists(), store
            assert not (sample_dir / f"{store}.lock").exists(), store
        for store in kinds["current"]:
            assert (sample_dir / f"{store}.zarr").is_dir(), store
            assert (sample_dir / f"{store}.lock").is_file(), store

    @pytest.mark.asyncio
    async def test_leaves_a_tof_file_alone(self, caches_of_every_vintage):
        """A TOF file's full sum signal carries its calibrated m/z axis."""
        await filestore.delete_stale_sum_signal()

        sample_dir, kinds = caches_of_every_vintage[
            "TofSample_1001.01.01_12h00m00s_Two"
        ]
        for store in kinds["current"]:
            assert (sample_dir / f"{store}.zarr").is_dir(), store

    @pytest.mark.asyncio
    async def test_leaves_other_stores_alone(self, caches_of_every_vintage):
        await filestore.delete_stale_sum_signal()

        for sample_dir, _ in caches_of_every_vintage.values():
            assert (sample_dir / "peak_timeseries.zarr").is_dir()
            assert (sample_dir / "peak_timeseries.lock").is_file()

    def test_action_is_registered(self):
        assert (
            filestore.ACTIONS["delete-stale-sum-signal"]
            is filestore.delete_stale_sum_signal
        )


class TestDeleteSyncDirs:
    """The one-shot reclaim of zarr 2's orphaned synchronizer directories."""

    @pytest.mark.asyncio
    async def test_removes_every_sync_directory(self, filestore_with_samples):
        await filestore.delete_sync_dirs()

        for sample_dir in filestore_with_samples.values():
            leftover = [n for n in os.listdir(sample_dir) if n.endswith(".sync")]
            assert leftover == [], f"sync directories survived: {leftover}"

    @pytest.mark.asyncio
    async def test_leaves_stores_and_locks_alone(self, filestore_with_samples):
        """Only *.sync goes. Deleting a live lock or a store would be data loss."""
        await filestore.delete_sync_dirs()

        for sample_dir in filestore_with_samples.values():
            for store in ("sum_signal", "sum_signal_a1b2c3d4", "peak_timeseries"):
                assert (sample_dir / f"{store}.zarr").is_dir()
                assert (sample_dir / f"{store}.lock").is_file()

    @pytest.mark.asyncio
    async def test_is_idempotent(self, filestore_with_samples):
        """Running it twice must not raise on the now-absent directories."""
        await filestore.delete_sync_dirs()
        await filestore.delete_sync_dirs()

        for sample_dir in filestore_with_samples.values():
            assert not [n for n in os.listdir(sample_dir) if n.endswith(".sync")]


def test_action_is_registered():
    """The action is only reachable through the ACTIONS registry."""
    assert filestore.ACTIONS["delete-sync-dirs"] is filestore.delete_sync_dirs
