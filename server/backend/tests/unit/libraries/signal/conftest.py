"""
Fixtures for the tests of ``mascope_signal.peak``.

They are backend tests although the module is a library's: peak detection
takes its peak ids from the backend (``mascope_backend.db.id``), and
importing that needs the backend's database secret, which the library suite
runs without. So a test that imports ``mascope_signal.peak`` cannot be
collected there, and lives here.

The filestore is a temporary directory, as in the signal library's own
tests: one sample, with a ``.props`` beside it and nothing else.
"""

import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from scripted_acquisition import SAMPLE_FILENAME


@pytest.fixture(scope="session")
def temp_filestore():
    temp_dir = tempfile.mkdtemp(prefix="mascope_peak_test_filestore_")
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def mock_runtime(temp_filestore):
    def mock_filestore(*args):
        return os.path.join(temp_filestore, *args)

    mock_logger = MagicMock()

    with (
        patch("mascope_file.name.runtime") as mock_name_runtime,
        patch("mascope_file.io.runtime") as mock_io_runtime,
        patch("mascope_signal.compute.runtime") as mock_compute_runtime,
    ):
        mock_name_runtime.filestore = mock_filestore
        mock_io_runtime.filestore = mock_filestore
        mock_io_runtime.logger = mock_logger
        mock_compute_runtime.logger = mock_logger

        yield mock_name_runtime


@pytest.fixture
def sample_file_path(temp_filestore):
    sample_path = os.path.join(
        temp_filestore,
        "OrbiTest",
        "1001.01.01",
        SAMPLE_FILENAME,
    )
    if os.path.exists(sample_path):
        shutil.rmtree(sample_path, ignore_errors=True)
    os.makedirs(sample_path, exist_ok=True)

    props_path = os.path.join(sample_path, ".props")
    with open(props_path, "w") as f:
        f.write('{"mz_calibration": null}')

    yield sample_path

    shutil.rmtree(sample_path, ignore_errors=True)
