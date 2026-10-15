"""An m/z calibration applied to the test sample by another process.

Run as a script by ``test_calibration_during_detection``, which detects the
sample's peaks in its own process meanwhile. The two share the filestore and
nothing else, as a backend worker and the file converter do: this process
reads the sample through the same scripted acquisition, finds it in the same
temporary filestore, and the locks between them are the real ones.

It names each lock it asks for on stdout before it waits for it, as
``LOCK <path>``, so that the test can tell an apply that waits for the
detection from one that goes ahead of it, and ``APPLIED`` once it is done.

Arguments: the filestore, the fit as JSON, and the scans and microscans of
the acquisition as Python literals.
"""

import ast
import json
import os
import sys
from contextlib import contextmanager
from unittest.mock import patch

from scripted_acquisition import SAMPLE_FILENAME, ScriptedAcquisition

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_thermo.streams as m_streams
import mascope_thermo.thermo as m_thermo
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    OrbiCalibrationHandler,
)


def main(filestore: str, fit: dict, scans: list, microscans: dict) -> None:
    def in_filestore(*parts):
        return os.path.join(filestore, *parts)

    acquisition = ScriptedAcquisition(scans, microscans)
    lock = m_io.zarr_write_lock

    @contextmanager
    def named(path, *args, **kwargs):
        print(f"LOCK {path}", flush=True)
        with lock(path, *args, **kwargs):
            yield

    with (
        patch("mascope_file.name.runtime") as name_runtime,
        patch("mascope_file.io.runtime") as io_runtime,
        patch("mascope_signal.compute.runtime"),
        patch.object(m_name, "get_sample_file_type", lambda _: "orbi_raw"),
        patch.object(m_thermo, "open_backend", lambda _: acquisition),
        patch.object(m_streams, "open_backend", lambda _: acquisition),
        patch.object(m_io, "zarr_write_lock", named),
    ):
        name_runtime.filestore = in_filestore
        io_runtime.filestore = in_filestore
        OrbiCalibrationHandler(SAMPLE_FILENAME)._guarded_apply(fit)
    print("APPLIED", flush=True)


if __name__ == "__main__":
    filestore, fit, scans, microscans = sys.argv[1:]
    main(
        filestore,
        json.loads(fit),
        ast.literal_eval(scans),
        ast.literal_eval(microscans),
    )
