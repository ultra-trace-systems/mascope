"""An Orbitrap m/z calibration fit, run end to end on a synthetic sample file.

``OrbiCalibrationHandler.fit`` joins several sources: the calibration
collection's isotopes from the database, the sample's peak list and peak
timeseries from ``peak_timeseries.zarr``, its scan axis and TIC from
``signal.zarr``, the previous calibration factor from ``.props``, and the
resolution function from the instrument function the sample file row points
at. The unit tests in ``tests/unit/api/calibration/`` pin the steps one at a
time with the rest patched out; this runs them together. The sample is written
to a temporary filestore and its rows are seeded in the integration database,
so nothing depends on the machine's runtime database or filestore.

The file is built so the answer is known in advance. Every calibrant peak sits
``CALIBRANT_OFFSET_PPM`` above its target m/z, and one more sits
``OUTLIER_OFFSET_PPM`` above its own: inside the refine window, so it is
matched, but outside the error tolerance once the others are fitted, so the
subset search has to leave it out. Background peaks far from every target have
to be discarded by the refine window.

The peak timeseries are stored already computed. Computing a missing one from
the signal on demand belongs to ``mascope_signal`` and is covered by its own
tests (``libraries/signal/tests/test_load_peak_timeseries.py``).
"""

import json
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest
import pytest_asyncio
import xarray as xr
from sqlalchemy import delete

import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    OrbiCalibrationHandler,
    calibration_params_factory,
    get_calibration_handler,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
)
from mascope_backend.db import (
    InstrumentFunction,
    IonizationMechanism,
    SampleFile,
    TargetCollection,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    TargetIsotope,
    Workspace,
)
from mascope_backend.db.id import gen_id
from mascope_file.runtime import runtime as file_runtime


_INSTRUMENT = "OrbiCalFit"
#: Acquisition time carried in the file name; the filestore path derives from it.
_ACQUIRED = datetime(2026, 1, 1, 12, 0, 0)
_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: Orbitrap resolution function coefficient, R = a / sqrt(m/z): 120 000 at m/z 200.
_RESOLUTION_COEFFICIENT = float(120_000 * np.sqrt(200.0))

CALIBRANT_MZS = (150.0, 250.0, 350.0)
OUTLIER_MZ = 450.0
BACKGROUND_MZS = (120.0, 200.0, 300.0, 400.0, 500.0)
CALIBRANT_OFFSET_PPM = 3.0
OUTLIER_OFFSET_PPM = 15.0

N_SCANS = 10
PEAK_HEIGHT = 1e6
SIGNAL_TO_NOISE = 1000.0


def _shifted(mz: float, ppm: float) -> float:
    return mz * (1.0 + ppm * 1e-6)


def _background_peaks() -> list[tuple[float, float]]:
    return [(mz, PEAK_HEIGHT) for mz in BACKGROUND_MZS]


def _calibrant_peaks() -> list[tuple[float, float]]:
    """Calibrants at the planted offset, the outlier, and the background."""
    return [
        *((_shifted(mz, CALIBRANT_OFFSET_PPM), PEAK_HEIGHT) for mz in CALIBRANT_MZS),
        (_shifted(OUTLIER_MZ, OUTLIER_OFFSET_PPM), PEAK_HEIGHT),
        *_background_peaks(),
    ]


def _write_sample_stores(filename: str, peaks: list[tuple[float, float]]) -> None:
    """Write the ``.props``, peak store and signal store of an orbi_zarr sample.

    An orbi_zarr sample keeps no source ``.raw``, so its scan axis and TIC are
    read from ``signal.zarr`` and its instrument type from ``.props``. Every
    peak holds the same height in every scan and is marked computed, so
    ``load_peak_timeseries`` reads it back instead of recomputing it.

    :param filename: Sample file name, which decides its filestore path
    :type filename: str
    :param peaks: ``(m/z, height per scan)`` of every peak in the file
    :type peaks: list[tuple[float, float]]
    """
    sample_dir = m_name.parse_path_from_item_filename(filename)
    os.makedirs(sample_dir)
    with open(os.path.join(sample_dir, ".props"), "w") as f:
        json.dump({"instrument_type": "orbi", "mz_calibration": None}, f)

    peaks = sorted(peaks)
    mzs = np.array([mz for mz, _ in peaks])
    heights = np.array([height for _, height in peaks])
    n_peaks = mzs.size
    scan_times = np.arange(N_SCANS, dtype=float)
    per_scan = np.repeat(heights[:, np.newaxis], N_SCANS, axis=1)

    xr.Dataset(
        data_vars={
            "is_satellite": ("mz", np.zeros(n_peaks, dtype=bool)),
            "is_weak": ("mz", np.zeros(n_peaks, dtype=bool)),
            "is_timeseries_computed": ("mz", np.ones(n_peaks, dtype=bool)),
            "sparsity": ("mz", np.zeros(n_peaks)),
            "peak_areas": (("mz", "time"), per_scan),
            "peak_heights": (("mz", "time"), per_scan),
            "sum_peak_areas": ("mz", per_scan.sum(axis=1)),
            "sum_peak_heights": ("mz", per_scan.sum(axis=1)),
            "signal_to_noise": ("mz", np.full(n_peaks, SIGNAL_TO_NOISE)),
            "polarity": ("mz", np.full(n_peaks, "+", dtype="<U1")),
        },
        coords={
            "mz": mzs,
            "time": scan_times,
            "tof": ("mz", np.zeros(n_peaks)),
            "peak_id": ("mz", [f"peak_{i:04d}" for i in range(n_peaks)]),
        },
    ).to_zarr(os.path.join(sample_dir, "peak_timeseries.zarr"), mode="w")

    # All the handler reads of the signal is its scan axis and, summed over
    # m/z, the TIC per scan - one row per peak is enough for both.
    xr.Dataset(
        {"signal": (("mz", "time"), per_scan)},
        coords={"mz": mzs, "time": scan_times},
    ).to_zarr(os.path.join(sample_dir, "signal.zarr"), mode="w")


@pytest.fixture
def filestore(tmp_path, monkeypatch):
    """Resolve sample file paths inside an empty temporary filestore."""
    monkeypatch.setattr(
        file_runtime, "filestore", lambda *parts: os.path.join(tmp_path, *parts)
    )
    return tmp_path


@pytest_asyncio.fixture
async def calibration_collection(async_session_factory):
    """A calibration collection with one isotope per target, the outlier's included.

    Each target is its own compound with one ion and one isotope of full
    relative abundance, so every matched peak is its own abundance reference
    and the match score turns on the m/z error alone.
    """
    ids = SimpleNamespace(
        workspace=gen_id(),
        mechanism=gen_id(),
        collection=gen_id(),
        compounds=[],
        ions=[],
        isotopes=[],
    )
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids.workspace,
                workspace_name=f"Calibration fit WS {ids.workspace}",
                workspace_description="Calibration fit test workspace",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=ids.mechanism,
                ionization_mechanism_polarity="+",
                ionization_mechanism=f"[M+H]+ {ids.mechanism}",
            )
        )
        session.add(
            TargetCollection(
                target_collection_id=ids.collection,
                target_collection_name=f"Calibration fit {ids.collection}",
                workspace_id=ids.workspace,
            )
        )
        # Formulas are carried by the rows but never read by the fit: the
        # isotopes' m/z values are what it calibrates against.
        for mz in (*CALIBRANT_MZS, OUTLIER_MZ):
            compound_id, ion_id, isotope_id = gen_id(), gen_id(), gen_id()
            session.add(
                TargetCompound(
                    target_compound_id=compound_id,
                    target_compound_name=f"Calibrant {mz} {compound_id}",
                    target_compound_formula="C6H12O6",
                )
            )
            session.add(
                TargetCompoundInTargetCollection(
                    target_compound_id=compound_id,
                    target_collection_id=ids.collection,
                )
            )
            session.add(
                TargetIon(
                    target_ion_id=ion_id,
                    target_compound_id=compound_id,
                    ionization_mechanism_id=ids.mechanism,
                    target_ion_formula="C6H13O6+",
                )
            )
            session.add(
                TargetIsotope(
                    target_isotope_id=isotope_id,
                    target_ion_id=ion_id,
                    target_isotope_formula="C6H13O6+",
                    mz=mz,
                    relative_abundance=1.0,
                    resolution="HIGH",
                )
            )
            ids.compounds.append(compound_id)
            ids.ions.append(ion_id)
            ids.isotopes.append(isotope_id)
        await session.commit()

    yield ids

    async with async_session_factory() as session:
        await session.execute(
            delete(TargetIsotope).where(
                TargetIsotope.target_isotope_id.in_(ids.isotopes)
            )
        )
        await session.execute(
            delete(TargetIon).where(TargetIon.target_ion_id.in_(ids.ions))
        )
        await session.execute(
            delete(TargetCompoundInTargetCollection).where(
                TargetCompoundInTargetCollection.target_collection_id == ids.collection
            )
        )
        await session.execute(
            delete(TargetCompound).where(
                TargetCompound.target_compound_id.in_(ids.compounds)
            )
        )
        await session.execute(
            delete(TargetCollection).where(
                TargetCollection.target_collection_id == ids.collection
            )
        )
        await session.execute(
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id == ids.mechanism
            )
        )
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == ids.workspace)
        )
        await session.commit()


@pytest_asyncio.fixture
async def write_orbi_sample(filestore, async_session_factory):
    """Factory writing an orbi_zarr sample to the filestore and seeding its rows.

    The sample file row points at an instrument function, which is where the
    overlap filter reads the resolution function from; its peak shape is never
    read by the fit.
    """
    created: list[tuple[str, str]] = []

    async def _write(peaks: list[tuple[float, float]]) -> str:
        # Hex only: no letter of it can complete the file name's h/m/s time pattern.
        filename = (
            f"{_INSTRUMENT}_{_ACQUIRED:%Y.%m.%d_%Hh%Mm%Ss}_{uuid.uuid4().hex[:12]}"
        )
        _write_sample_stores(filename, peaks)

        sample_file_id, instrument_function_id = gen_id(), gen_id(32)
        async with async_session_factory() as session:
            session.add(
                InstrumentFunction(
                    instrument_function_id=instrument_function_id,
                    instrument=_INSTRUMENT,
                    method_file="calibration-fit.meth",
                    datetime_utc=_NOW,
                    peakshape={},
                    resolution_function=[_RESOLUTION_COEFFICIENT],
                )
            )
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    instrument_function_id=instrument_function_id,
                    filename=filename,
                    instrument=_INSTRUMENT,
                    instrument_type="orbi",
                    datetime=_ACQUIRED,
                    datetime_utc=_NOW,
                    length=float(N_SCANS),
                    range=[100.0, 600.0],
                    polarity="+",
                )
            )
            await session.commit()
        created.append((sample_file_id, instrument_function_id))
        return filename

    yield _write

    if created:
        sample_file_ids, instrument_function_ids = zip(*created)
        async with async_session_factory() as session:
            await session.execute(
                delete(SampleFile).where(SampleFile.sample_file_id.in_(sample_file_ids))
            )
            await session.execute(
                delete(InstrumentFunction).where(
                    InstrumentFunction.instrument_function_id.in_(
                        instrument_function_ids
                    )
                )
            )
            await session.commit()


def _handler(filename: str, collection: SimpleNamespace) -> OrbiCalibrationHandler:
    """The handler the pipeline builds for this file, with its default parameters."""
    params = CalibrationFitParams(
        calibration_collection_id=collection.collection,
        ionization_mechanism_ids=[collection.mechanism],
        polarity="+",
        **calibration_params_factory(filename).model_dump(),
    )
    handler = get_calibration_handler(filename, params, notification=None)
    assert isinstance(handler, OrbiCalibrationHandler)
    return handler


@pytest.mark.asyncio
async def test_fit_recovers_the_planted_offset_and_leaves_the_outlier_out(
    calibration_collection, write_orbi_sample
):
    """The one-point fit undoes the calibrants' offset, fitted on them alone."""
    filename = await write_orbi_sample(_calibrant_peaks())
    handler = _handler(filename, calibration_collection)
    # The outlier is only an outlier under these parameters: matched inside the
    # refine window, then off by more than the tolerance once the rest are fitted.
    assert OUTLIER_OFFSET_PPM < handler.params.refine_window
    assert handler.params.mz_error_tolerance < OUTLIER_OFFSET_PPM - CALIBRANT_OFFSET_PPM

    await handler.fit()

    assert handler.warning is None
    scaling = 1.0 / (1.0 + CALIBRANT_OFFSET_PPM * 1e-6)
    assert handler.fit_result["mode"] == "one-point"
    assert handler.fit_result["par"] == pytest.approx(
        {
            "old_factor": 1.0,
            "old_factor_scaling": scaling,
            "calibration_factor": scaling,
        },
        rel=0,
        abs=1e-12,
    )

    *calibrants, summary = handler.stats
    assert sorted(row["mz"] for row in calibrants) == pytest.approx(list(CALIBRANT_MZS))
    for row in calibrants:
        assert row["match_mz_error"] == pytest.approx(CALIBRANT_OFFSET_PPM, abs=1e-6)
        assert row["calibration_mz_error"] == pytest.approx(0.0, abs=1e-6)
    assert summary["match_mz_error"] == pytest.approx(CALIBRANT_OFFSET_PPM, abs=1e-6)
    assert summary["calibration_mz_error"] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.asyncio
async def test_a_file_without_calibrant_peaks_fits_nothing(
    calibration_collection, write_orbi_sample
):
    """No peak inside any target's refine window: a warning, no fit, no stats."""
    filename = await write_orbi_sample(_background_peaks())
    handler = _handler(filename, calibration_collection)

    await handler.fit()

    assert handler.warning == "No calibration peaks found"
    assert handler.fit_result is None
    assert handler.stats is None
