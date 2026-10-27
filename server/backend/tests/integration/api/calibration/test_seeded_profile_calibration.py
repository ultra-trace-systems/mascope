"""A file of a seeded chemistry calibrates on the anchors Mascope seeds for it.

Nobody builds a collection here. A start seeds the chemistries, their
collections and the mode pointers on an empty database; the handler is given
what the pipeline gives it for a file bound to the mode - the mode's
calibration collection and mechanisms - and the fit resolves its anchors
through the one path it has (``_resolve_calibration_isotopes``), so what it
fits on is what the seed wrote.

An Orbitrap file of the nitrate chemistry and a TOF file of the bromide one,
each holding the lines of its reagent's cluster series and some background:
the Orbitrap's one-point fit undoes the offset its lines were planted at, and
the TOF's axis is fitted on three anchors from three ions. Each test also says
which ions it fitted, read off the reagent-ion library on its own, so a seed
that resolved to the wrong ions fails here rather than fitting on them.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
import pytest_asyncio
import xarray as xr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

import mascope_backend.db as db_module
import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    OrbiCalibrationHandler,
    TofCalibrationHandler,
    calibration_params_factory,
    get_calibration_handler,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
)
from mascope_backend.db import (
    InstrumentFunction,
    IonizationMechanism,
    IonizationMode,
    SampleFile,
    TargetCollection,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    TargetIsotope,
)
from mascope_backend.db.admin.ionization.ensure_system_modes import (
    ensure_system_ionization,
)
from mascope_backend.db.id import gen_id
from mascope_file.runtime import runtime as file_runtime
from mascope_tofwerk.calibration import tof_to_mass
from mascope_tools.composition.reagents import ion_mz, reagent_library


_NODE = "mascope_backend.api.controllers.calibration.lib.calibration_mz_fit"
_ACQUIRED = datetime(2026, 10, 27, 12, 0, 0)
_NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)
N_SCANS = 10
PEAK_HEIGHT = 1e6
#: Where an Orbitrap file's lines sit from their exact m/z.
OFFSET_PPM = 3.0
#: Far from every anchor of either chemistry: for the refine window to drop.
BACKGROUND_MZS = (90.0, 150.0, 300.0)

#: A TOF axis ``i = p1 * sqrt(m) + p2``: the one the lines were placed by, and
#: the one the file carries, which reads them a few ppm high.
TRUE_FIT = {"mode": 0, "par": [10850.0, -18500.0]}
STORED_FIT = {"mode": 0, "par": [10850.0 * (1 + 3e-6), -18500.0]}


def _series(profile: str) -> dict[str, float]:
    """The reagent's cluster series, ion by formula as the generator writes
    it, at the m/z of its main line, read off the library."""
    return {
        f"{ion.formula}-": ion_mz(ion.formula, ion.charge)
        for ion in reagent_library(profile)
        if ion.kind == "cluster" and ion.family == "reagent"
    }


@pytest_asyncio.fixture(scope="module")
async def seed_db_factory(async_engine_factory):
    """A database of its own, which a start seeds the way a fresh server's is."""
    engine = await async_engine_factory("integration_seeded_calibration")
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def seeded(seed_db_factory, bind_db, monkeypatch):
    """The application pointed at that database, freshly seeded.

    Requests ``bind_db`` so that it runs first: this rebinds what it bound.
    """
    async with seed_db_factory() as session:
        for model in (
            SampleFile,
            InstrumentFunction,
            TargetCompoundInTargetCollection,
            TargetCollection,
            TargetIsotope,
            TargetIon,
            TargetCompound,
            IonizationMode,
            IonizationMechanism,
        ):
            await session.execute(delete(model))
        await session.commit()
    monkeypatch.setattr(db_module, "ASYNC_SESSION_MAKER", seed_db_factory)
    await ensure_system_ionization()
    return seed_db_factory


@pytest.fixture
def filestore(tmp_path, monkeypatch):
    """Resolve sample file paths inside an empty temporary filestore."""
    monkeypatch.setattr(
        file_runtime, "filestore", lambda *parts: os.path.join(tmp_path, *parts)
    )
    monkeypatch.setattr(m_name, "_INSTRUMENT_TYPE", {})
    return tmp_path


async def _mode(factory, mode_id: str) -> IonizationMode:
    async with factory() as session:
        return await session.get(IonizationMode, mode_id)


async def _anchor_lines(factory, mode: IonizationMode, resolution: str) -> list:
    """The lines the fit can read off the mode's calibration collection: each
    isotope its ions have under the mode's mechanisms, as (m/z, relative
    abundance), above the fit's abundance floor."""
    async with factory() as session:
        rows = (
            await session.execute(
                select(TargetIsotope.mz, TargetIsotope.relative_abundance)
                .join(TargetIon, TargetIon.target_ion_id == TargetIsotope.target_ion_id)
                .join(
                    TargetCompoundInTargetCollection,
                    TargetCompoundInTargetCollection.target_compound_id
                    == TargetIon.target_compound_id,
                )
                .where(
                    TargetCompoundInTargetCollection.target_collection_id
                    == mode.calibration_collection_id,
                    TargetIon.ionization_mechanism_id.in_(
                        mode.ionization_mechanism_ids
                    ),
                    TargetIsotope.resolution == resolution,
                    TargetIsotope.relative_abundance >= 0.15,
                )
            )
        ).all()
    # An ion two of the compounds give, one through each mechanism, is one line.
    return sorted(set(rows))


async def _fitted_ions(factory, stats: list[dict]) -> set[str]:
    """The ions the fit's calibrant rows came from, by formula."""
    ion_ids = {row["target_ion_id"] for row in stats if row.get("target_ion_id")}
    async with factory() as session:
        return set(
            (
                await session.execute(
                    select(TargetIon.target_ion_formula).where(
                        TargetIon.target_ion_id.in_(ion_ids)
                    )
                )
            ).scalars()
        )


def _handler(filename: str, mode: IonizationMode):
    """The handler the pipeline builds for a file bound to the mode."""
    params = CalibrationFitParams(
        calibration_collection_id=mode.calibration_collection_id,
        ionization_mechanism_ids=mode.ionization_mechanism_ids,
        polarity=mode.ionization_mode_polarity,
        **calibration_params_factory(filename).model_dump(),
    )
    return get_calibration_handler(filename, params, notification=None)


def _write_stores(
    filename: str,
    instrument_type: str,
    mzs: np.ndarray,
    heights: np.ndarray,
    *,
    tof: np.ndarray | None = None,
    calibration: dict | None = None,
) -> None:
    """The ``.props``, peak store and signal store of a negative sample.

    Every peak holds the same height in every scan and is marked computed, so
    ``load_peak_timeseries`` reads it back instead of recomputing it.
    """
    sample_dir = m_name.parse_path_from_item_filename(filename)
    os.makedirs(sample_dir)
    with open(os.path.join(sample_dir, ".props"), "w") as f:
        json.dump(
            {
                "instrument_type": instrument_type,
                "polarity": "-",
                "mz_calibration": calibration,
            },
            f,
        )
    order = np.argsort(mzs)
    mzs, heights = mzs[order], heights[order]
    tof = np.zeros(mzs.size) if tof is None else tof[order]
    per_scan = np.repeat(heights[:, np.newaxis], N_SCANS, axis=1)
    times = np.arange(N_SCANS, dtype=float)
    xr.Dataset(
        data_vars={
            "is_satellite": ("mz", np.zeros(mzs.size, dtype=bool)),
            "is_weak": ("mz", np.zeros(mzs.size, dtype=bool)),
            "is_timeseries_computed": ("mz", np.ones(mzs.size, dtype=bool)),
            "sparsity": ("mz", np.zeros(mzs.size)),
            "peak_areas": (("mz", "time"), per_scan),
            "peak_heights": (("mz", "time"), per_scan),
            "sum_peak_areas": ("mz", per_scan.sum(axis=1)),
            "sum_peak_heights": ("mz", per_scan.sum(axis=1)),
            "signal_to_noise": ("mz", np.full(mzs.size, 1000.0)),
            "polarity": ("mz", np.full(mzs.size, "-", dtype="<U1")),
        },
        coords={
            "mz": mzs,
            "time": times,
            "tof": ("mz", tof),
            "peak_id": ("mz", [f"peak_{i:04d}" for i in range(mzs.size)]),
        },
    ).to_zarr(os.path.join(sample_dir, "peak_timeseries.zarr"), mode="w")
    xr.Dataset(
        {"signal": (("mz", "time"), per_scan)},
        coords={"mz": mzs, "time": times},
    ).to_zarr(os.path.join(sample_dir, "signal.zarr"), mode="w")


@pytest.mark.asyncio
async def test_an_orbitrap_file_calibrates_on_the_seeded_nitrate_anchors(
    seeded, filestore
):
    mode = await _mode(seeded, "sysNitrate")
    assert mode.calibration_collection_id == "sysNitrateCal"
    lines = await _anchor_lines(seeded, mode, "HIGH")
    mzs = np.array(
        [mz * (1 + OFFSET_PPM * 1e-6) for mz, _ in lines] + list(BACKGROUND_MZS)
    )
    heights = np.array(
        [PEAK_HEIGHT * abundance for _, abundance in lines]
        + [PEAK_HEIGHT] * len(BACKGROUND_MZS)
    )
    instrument = "SeedOrbi"
    filename = f"{instrument}_{_ACQUIRED:%Y.%m.%d_%Hh%Mm%Ss}_{uuid.uuid4().hex[:12]}"
    _write_stores(filename, "orbi", mzs, heights)
    instrument_function_id = gen_id(32)
    async with seeded() as session:
        # Where the overlap filter reads the resolution function from:
        # 120 000 at m/z 200.
        session.add(
            InstrumentFunction(
                instrument_function_id=instrument_function_id,
                instrument=instrument,
                method_file="seeded-profile.meth",
                datetime_utc=_NOW,
                peakshape={},
                resolution_function=[float(120_000 * np.sqrt(200.0))],
            )
        )
        session.add(
            SampleFile(
                sample_file_id=gen_id(),
                instrument_function_id=instrument_function_id,
                filename=filename,
                instrument=instrument,
                instrument_type="orbi",
                datetime=_ACQUIRED,
                datetime_utc=_NOW,
                length=float(N_SCANS),
                range=[50.0, 400.0],
                polarity="-",
            )
        )
        await session.commit()
    handler = _handler(filename, mode)
    assert isinstance(handler, OrbiCalibrationHandler)

    await handler.fit()

    assert handler.warning is None
    assert handler.fit_result["mode"] == "one-point"
    assert handler.fit_result["par"]["old_factor_scaling"] == pytest.approx(
        1.0 / (1.0 + OFFSET_PPM * 1e-6), rel=0, abs=1e-12
    )
    *calibrants, summary = handler.stats
    series = _series("NO3")
    assert await _fitted_ions(seeded, calibrants) == set(series)
    assert sorted(row["mz"] for row in calibrants) == pytest.approx(
        sorted(series.values()), abs=1e-4
    )
    assert summary["calibration_mz_error"] == pytest.approx(0.0, abs=1e-6)


def _tof_index(mz: float) -> float:
    p1, p2 = TRUE_FIT["par"]
    return p1 * np.sqrt(mz) + p2


@pytest.mark.asyncio
async def test_a_tof_file_calibrates_on_the_seeded_bromide_anchors(seeded, filestore):
    mode = await _mode(seeded, "sysBromide")
    assert mode.calibration_collection_id == "sysBromideCal"
    lines = await _anchor_lines(seeded, mode, "LOW")
    # Each line where the true axis puts it, read through the file's own.
    tof = np.array(
        [_tof_index(mz) for mz, _ in lines] + [_tof_index(m) for m in BACKGROUND_MZS]
    )
    mzs = np.asarray(tof_to_mass(tof, STORED_FIT["mode"], STORED_FIT["par"]))
    heights = np.array(
        [PEAK_HEIGHT * abundance for _, abundance in lines]
        + [PEAK_HEIGHT] * len(BACKGROUND_MZS)
    )
    filename = f"SeedTof_{_ACQUIRED:%Y.%m.%d_%Hh%Mm%Ss}_{uuid.uuid4().hex[:12]}"
    _write_stores(filename, "tof", mzs, heights, tof=tof, calibration=STORED_FIT)
    handler = _handler(filename, mode)
    assert isinstance(handler, TofCalibrationHandler)

    def resolution(mz):
        return np.full_like(np.asarray(mz, dtype=np.float64), 30000.0)

    with patch(
        f"{_NODE}.read_instrument_functions",
        AsyncMock(return_value=({}, resolution)),
    ):
        await handler.fit()

    assert handler.warning is None
    assert handler.error is None
    assert handler.fit_result is not None
    *calibrants, summary = handler.stats
    # The series' three ions, the TOF's three points at the least.
    assert await _fitted_ions(seeded, calibrants) == set(_series("BR"))
    assert len(calibrants) >= 3
    assert sorted(round(row["mz"], 4) for row in calibrants) == [
        round(mz, 4) for mz, _ in lines
    ]
    # The fit recovers the axis the lines were placed by.
    assert summary["calibration_mz_error"] == pytest.approx(0.0, abs=0.5)
