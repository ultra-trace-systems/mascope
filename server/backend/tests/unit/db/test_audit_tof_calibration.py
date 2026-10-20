"""
The TOF calibration audit script: what it reads, how it reproduces the node's
stored fit, and what it reports.

The refit itself is the node's handler and is tested beside it
(``tests/unit/api/calibration/test_tof_calibration_audit.py``). Here: the
configuration an operator types, which files a run reads, the mode's sets
from the database, one file's audit end to end on a sample file on disk, and
how files add up to a report. The database is the unit tests' own.
"""

import sys
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from test_utils import gen_test_id

import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration.lib import (
    tof_calibration_audit as audit,
)
from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    generate_target_ions_from_composition,
)
from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    TargetCompoundBase,
)
from mascope_backend.db import (
    Dataset,
    IonizationMechanism,
    IonizationMode,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCollection,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    TargetIsotope,
    Workspace,
)
from mascope_backend.db.scripts import audit_tof_calibration as script


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api" / "calibration"))
import tof_audit_support as support  # noqa: E402


@pytest.fixture
def filestore(tmp_path, monkeypatch):
    monkeypatch.setattr(m_name.runtime.meta, "filestore", str(tmp_path))
    monkeypatch.setattr(m_name, "_INSTRUMENT_TYPE", {})
    return tmp_path


@pytest.fixture
def no_env(monkeypatch):
    """No audit variable set but the instrument."""
    for name in script.os.environ.copy():
        if name.startswith("TOF_AUDIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("TOF_AUDIT_INSTRUMENT", "TOF H")
    return monkeypatch


def test_the_script_can_be_run():
    """``mascope db script run`` finds a script by its callable main()."""
    assert callable(script.main)


def test_it_declares_that_it_writes_nothing():
    """So the runner takes no backup before it, which it would on a site
    before every reading."""
    from mascope_cli.pg import script_writes_nothing

    assert script_writes_nothing(Path(script.__file__).read_text(encoding="utf-8"))


# --- Configuration ---


def test_the_instrument_is_required(no_env):
    no_env.delenv("TOF_AUDIT_INSTRUMENT")
    with pytest.raises(ValueError, match="TOF_AUDIT_INSTRUMENT"):
        script.read_config()


def test_the_defaults_are_the_last_seven_days_of_every_file(no_env):
    config = script.read_config(today=date(2026, 10, 20))
    assert (config.first_day, config.last_day) == (
        date(2026, 10, 14),
        date(2026, 10, 20),
    )
    assert config.per_day == 0
    assert config.models == (0,)
    assert config.band_edges == audit.DEFAULT_BAND_EDGES
    assert config.series == audit.DEFAULT_CLUSTER_SERIES
    assert config.families is None
    assert config.sets == {}
    assert not config.as_json


def test_everything_can_be_set(no_env):
    no_env.setenv("TOF_AUDIT_FROM", "2026-10-12")
    no_env.setenv("TOF_AUDIT_TO", "2026-10-18")
    no_env.setenv("TOF_AUDIT_PER_DAY", "40")
    no_env.setenv("TOF_AUDIT_MODE", "Propylamine")
    no_env.setenv(
        "TOF_AUDIT_SETS", '{"proposed": ["C13H25NO2"], "old": {"collection": "c1"}}'
    )
    no_env.setenv("TOF_AUDIT_BANDS", "40,100,200")
    no_env.setenv("TOF_AUDIT_FAMILIES", "[M+NO3]-, [M-H]-")
    no_env.setenv("TOF_AUDIT_SERIES", "none")
    no_env.setenv("TOF_AUDIT_FAMILY_SNR", "30")
    no_env.setenv("TOF_AUDIT_FAMILY_WINDOW_PPM", "20")
    no_env.setenv("TOF_AUDIT_FAMILY_DBE_MAX", "2")
    no_env.setenv("TOF_AUDIT_JSON", "1")

    config = script.read_config()

    assert (config.first_day, config.last_day) == (
        date(2026, 10, 12),
        date(2026, 10, 18),
    )
    assert config.per_day == 40
    assert config.mode == "Propylamine"
    assert config.sets == {"proposed": ["C13H25NO2"], "old": {"collection": "c1"}}
    assert config.band_edges == (40.0, 100.0, 200.0)
    assert config.families == ("[M+NO3]-", "[M-H]-")
    assert config.series == {}
    assert (config.family_snr, config.family_window_ppm, config.family_dbe_max) == (
        30.0,
        20.0,
        2,
    )
    assert config.as_json


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("TOF_AUDIT_TO", "2026-10-01", "after"),
        ("TOF_AUDIT_FROM", "12.10.2026", "not a date"),
        ("TOF_AUDIT_PER_DAY", "ten", "not a number"),
        ("TOF_AUDIT_PER_DAY", "-1", "below"),
        ("TOF_AUDIT_SETS", "{proposed", "not valid JSON"),
        ("TOF_AUDIT_SETS", '{"stored": ["C3H9N"]}', "reserved"),
        ("TOF_AUDIT_SETS", '{"collection": ["C3H9N"]}', "reserved"),
        ("TOF_AUDIT_SETS", '{"proposed": []}', "list of formulas"),
        ("TOF_AUDIT_SETS", '{"proposed": "C3H9N"}', "list of formulas"),
        ("TOF_AUDIT_SETS", '["C3H9N"]', "map set names"),
        ("TOF_AUDIT_MODELS", "2", "cannot be fitted"),
        ("TOF_AUDIT_MODELS", "zero", "not a list"),
        ("TOF_AUDIT_BANDS", "100,40", "rise"),
        ("TOF_AUDIT_BANDS", "40,a", "not a list"),
        ("TOF_AUDIT_SERIES", '{"x": {"core": "Br"}}', "series 'x'"),
        ("TOF_AUDIT_FAMILIES", "[M+NO3]-,[M-H]", "'\\[M-H\\]' must end"),
    ],
)
def test_what_cannot_be_read_stops_the_run(no_env, name, value, message):
    """A setting is part of what the report says about itself: one that
    cannot be read stops it rather than falling back to the default."""
    no_env.setenv("TOF_AUDIT_FROM", "2026-10-12")
    no_env.setenv(name, value)
    with pytest.raises(ValueError, match=message):
        script.read_config(today=date(2026, 10, 20))


def test_an_empty_setting_is_unset(no_env):
    """What an unset variable looks like once a shell has expanded it."""
    no_env.setenv("TOF_AUDIT_PER_DAY", "")
    no_env.setenv("TOF_AUDIT_SETS", " ")
    config = script.read_config()
    assert config.per_day == 0
    assert config.sets == {}


# --- Which files ---


def _row(day: int, hour: int, mode: str | None = "m1") -> script.FileRow:
    acquired = datetime(2026, 10, day, hour, tzinfo=UTC)
    return script.FileRow(f"sf{day}-{hour}", f"f{day}-{hour}", acquired, mode)


def test_a_sample_per_day_is_spread_over_the_day():
    rows = [_row(12, hour) for hour in range(10)] + [_row(13, 5)]
    sampled = script.sample_per_day(rows, 3)
    assert [(row.acquired.day, row.acquired.hour) for row in sampled] == [
        (12, 0),
        (12, 4),
        (12, 9),
        (13, 5),
    ]


def test_no_sample_is_every_file():
    rows = [_row(12, hour) for hour in range(10)]
    assert script.sample_per_day(rows, 0) == rows


@pytest_asyncio.fixture
async def site(async_session_factory):
    """An instrument's TOF files around a week, in a mode with a collection
    of three nitrate calibrants."""
    instrument = f"AuditTof{gen_test_id()[:6]}"
    workspace_id, dataset_id, batch_id = gen_test_id(), gen_test_id(), gen_test_id()
    collection_id, mode_id, other_mode_id = gen_test_id(), gen_test_id(), gen_test_id()
    added = []

    async with async_session_factory() as session:
        mechanism = (
            await session.execute(
                select(IonizationMechanism).where(
                    IonizationMechanism.ionization_mechanism == "[M]-."
                )
            )
        ).scalar_one_or_none()
        if mechanism is None:
            mechanism = IonizationMechanism(
                ionization_mechanism_id=gen_test_id(),
                ionization_mechanism="[M]-.",
                ionization_mechanism_polarity="-",
            )
            session.add(mechanism)
            added.append(mechanism)
        rows = [
            Workspace(
                workspace_id=workspace_id,
                workspace_name=f"audit {workspace_id}",
                workspace_status="active",
            ),
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name="audit",
                dataset_type="ACQUISITION",
                instrument=instrument,
            ),
            TargetCollection(
                target_collection_id=collection_id,
                target_collection_name=f"audit calibrants {collection_id}",
                target_collection_type="CALIBRATION",
            ),
            IonizationMode(
                ionization_mode_id=mode_id,
                ionization_mode_name=f"Nitrate {mode_id}",
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[mechanism.ionization_mechanism_id],
                calibration_collection_id=collection_id,
            ),
            IonizationMode(
                ionization_mode_id=other_mode_id,
                ionization_mode_name=f"Other {other_mode_id}",
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[mechanism.ionization_mechanism_id],
            ),
        ]
        for formula in ["NO3", "HN2O6", "H2N3O9"]:
            compound = TargetCompound(
                target_compound_id=gen_test_id(), target_compound_formula=formula
            )
            rows.append(compound)
            rows.append(
                TargetCompoundInTargetCollection(
                    target_compound_id=compound.target_compound_id,
                    target_collection_id=collection_id,
                )
            )
            ions, isotopes = generate_target_ions_from_composition(
                TargetCompoundBase(
                    target_compound_id=compound.target_compound_id,
                    target_compound_formula=formula,
                ),
                [mechanism],
            )
            rows.extend(ions)
            rows.extend(isotopes)
        session.add_all(rows)
        await session.flush()
        batch = SampleBatch(
            sample_batch_id=batch_id,
            dataset_id=dataset_id,
            sample_batch_name="audit",
            sample_batch_type="ACQUISITION",
            status="ready",
            polarity="-",
        )
        session.add(batch)
        await session.flush()

        files = {}
        for key, (day, instrument_type, mode, name) in {
            "before": (11, "tof", mode_id, instrument),
            "first": (12, "tof", mode_id, instrument),
            "no mode": (13, "tof", None, instrument),
            "second": (14, "tof", mode_id, instrument),
            "last": (18, "tof", mode_id, instrument),
            "after": (19, "tof", mode_id, instrument),
            "orbi": (14, "orbi", mode_id, instrument),
            "other mode": (15, "tof", other_mode_id, instrument),
            "other instrument": (15, "tof", mode_id, f"{instrument}B"),
        }.items():
            sample_file = SampleFile(
                sample_file_id=gen_test_id(),
                filename=f"{name}_2026.10.{day:02d}_12h00m00s_{key.replace(' ', '')}",
                instrument=name,
                instrument_type=instrument_type,
                datetime=datetime(2026, 10, day, 12),
                datetime_utc=datetime(2026, 10, day, 12, tzinfo=UTC),
                length=1.0,
                range=[10.0, 500.0],
                polarity="-",
            )
            session.add(sample_file)
            await session.flush()
            # Two items of one file, as a file in two batches has
            session.add_all(
                [
                    SampleItem(
                        sample_item_id=gen_test_id(),
                        sample_batch_id=batch_id,
                        sample_file_id=sample_file.sample_file_id,
                        sample_item_name=f"{key} a",
                        sample_item_type="ACQUISITION",
                        ionization_mode_id=None,
                    ),
                    SampleItem(
                        sample_item_id=gen_test_id(),
                        sample_batch_id=batch_id,
                        sample_file_id=sample_file.sample_file_id,
                        sample_item_name=f"{key} b",
                        sample_item_type="ACQUISITION",
                        ionization_mode_id=mode,
                    ),
                ]
            )
            files[key] = sample_file.sample_file_id
        await session.commit()

    yield {
        "instrument": instrument,
        "files": files,
        "mode_id": mode_id,
        "other_mode_id": other_mode_id,
        "collection_id": collection_id,
        "mechanism": mechanism,
    }

    async with async_session_factory() as session:
        await session.execute(
            delete(SampleItem).where(SampleItem.sample_batch_id == batch_id)
        )
        await session.execute(
            delete(SampleFile).where(
                SampleFile.instrument.in_([instrument, f"{instrument}B"])
            )
        )
        await session.execute(
            delete(SampleBatch).where(SampleBatch.sample_batch_id == batch_id)
        )
        compound_ids = select(
            TargetCompoundInTargetCollection.target_compound_id
        ).where(TargetCompoundInTargetCollection.target_collection_id == collection_id)
        ion_ids = select(TargetIon.target_ion_id).where(
            TargetIon.target_compound_id.in_(compound_ids)
        )
        await session.execute(
            delete(TargetIsotope).where(TargetIsotope.target_ion_id.in_(ion_ids))
        )
        await session.execute(
            delete(TargetIon).where(TargetIon.target_ion_id.in_(ion_ids))
        )
        ids = (await session.execute(compound_ids)).scalars().all()
        await session.execute(
            delete(TargetCompoundInTargetCollection).where(
                TargetCompoundInTargetCollection.target_collection_id == collection_id
            )
        )
        await session.execute(
            delete(TargetCompound).where(TargetCompound.target_compound_id.in_(ids))
        )
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id.in_([mode_id, other_mode_id])
            )
        )
        await session.execute(
            delete(TargetCollection).where(
                TargetCollection.target_collection_id == collection_id
            )
        )
        await session.execute(delete(Dataset).where(Dataset.dataset_id == dataset_id))
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == workspace_id)
        )
        for row in added:
            await session.delete(row)
        await session.commit()


def _config(instrument: str, **update) -> script.Config:
    values = {
        "instrument": instrument,
        "first_day": date(2026, 10, 12),
        "last_day": date(2026, 10, 18),
        "per_day": 0,
        "mode": None,
        "sets": {},
        "models": (0,),
        "band_edges": audit.DEFAULT_BAND_EDGES,
        "families": None,
        "series": audit.DEFAULT_CLUSTER_SERIES,
        "family_snr": audit.DEFAULT_FAMILY_SNR,
        "family_window_ppm": audit.DEFAULT_FAMILY_WINDOW_PPM,
        "family_dbe_max": audit.DEFAULT_FAMILY_DBE_MAX,
        "as_json": False,
    }
    values.update(update)
    return script.Config(**values)


@pytest.mark.asyncio
async def test_a_run_reads_the_instrument_tof_files_in_the_range(site):
    """Both ends of the range included, one row per file, with the mode its
    items name: not the files before or after, nor an Orbitrap file of the
    same instrument name, nor another instrument's."""
    rows = await script.select_files(_config(site["instrument"]))

    files = site["files"]
    assert [row.sample_file_id for row in rows] == [
        files["first"],
        files["no mode"],
        files["second"],
        files["other mode"],
        files["last"],
    ]
    assert [row.ionization_mode_id for row in rows] == [
        site["mode_id"],
        None,
        site["mode_id"],
        site["other_mode_id"],
        site["mode_id"],
    ]


@pytest.mark.asyncio
async def test_a_mode_is_audited_with_its_collection_and_the_named_sets(
    site, filestore
):
    from mascope_backend.db import async_session

    async with async_session() as session:
        mode = await session.get(IonizationMode, site["mode_id"])
        other = await session.get(IonizationMode, site["other_mode_id"])
    filename = support.write_file(filestore, support.calibrant_peaks())
    config = _config(
        site["instrument"],
        sets={
            "bromide": ["Br"],
            "by name": {"collection": f"audit calibrants {site['collection_id']}"},
            "missing": {"collection": "no such collection"},
            "unionised": ["C3H9N+"],
        },
    )

    prepared = await script.prepare_mode(mode, config, filename)

    assert set(prepared.sets) == {"collection", "bromide", "by name"}
    main = prepared.sets["collection"]
    main = main[main.relative_abundance >= 0.15]
    assert sorted(label.split()[0] for label in main.ion_label) == [
        "H2N3O9-",
        "HN2O6-",
        "NO3-",
    ]
    assert (
        prepared.sets["by name"].mz.tolist() == prepared.sets["collection"].mz.tolist()
    )
    assert any("missing" in note for note in prepared.notes)
    assert any("unionised" in note for note in prepared.notes)
    # [M]-. adds no adduct, so it has a family: the bare CHO radical anions
    assert [family.mechanism for family in prepared.families] == ["[M]-."]
    assert not prepared.series.empty

    without = await script.prepare_mode(other, _config(site["instrument"]), filename)
    assert without.sets == {}
    assert "no calibration collection" in without.notes[0]


@pytest.mark.asyncio
async def test_named_families_replace_the_mode_mechanisms(site, filestore):
    from mascope_backend.db import async_session

    async with async_session() as session:
        mode = await session.get(IonizationMode, site["mode_id"])
    filename = support.write_file(filestore, support.calibrant_peaks())
    config = _config(site["instrument"], families=("[M+NO3]-", "[M+H]+", "[2M-H]-"))

    prepared = await script.prepare_mode(mode, config, filename)

    # The positive one is of the other polarity; the dimer has no family
    assert [family.mechanism for family in prepared.families] == ["[M+NO3]-"]
    assert any("[2M-H]-" in note for note in prepared.notes)


# --- One file ---


def _mode_audit(sets=None, families=None, series=None) -> script.ModeAudit:
    mode = IonizationMode(
        ionization_mode_id="m1",
        ionization_mode_name="Nitrate",
        ionization_mode_polarity="-",
        ionization_mechanism_ids=["radical-"],
        calibration_collection_id="collection",
    )
    return script.ModeAudit(
        mode=mode,
        params=support.params(),
        sets=sets if sets is not None else {"collection": support.isotopes()},
        families=families or [],
        series=series if series is not None else audit.series_isotopes({}, "-"),
    )


async def _node_record(filestore, peaks, tolerance=15.0) -> dict:
    """The record the node stores for these peaks: its fit, and the quality
    block that says what it was fitted with."""
    support.write_file(filestore, peaks)
    refit = await audit.refit(
        audit.read_stored_peaks(support.FILENAME),
        support.params(mz_error_tolerance=tolerance),
        support.isotopes(),
        support.resolution,
    )
    return {
        **refit.fit,
        "quality": {
            "n_points": len(refit.retained),
            "mz_error_tolerance": tolerance,
            "refine_window": 100.0,
        },
        "status": "ok",
    }


async def _audit(filestore, peaks, record, mode_audit=None, config=None):
    filename = support.write_file(filestore, peaks, record=record)
    row = script.FileRow("sf1", filename, datetime(2026, 10, 20, 12, tzinfo=UTC), "m1")
    with patch.object(
        script,
        "read_instrument_functions",
        AsyncMock(return_value=({}, support.resolution)),
    ):
        return await script.audit_file(
            row, mode_audit or _mode_audit(), config or _config("AuditTof")
        )


@pytest.mark.asyncio
async def test_a_node_fit_is_reproduced_with_the_record_own_tolerance(filestore):
    """The node retries a fit at twice the tolerance; reproducing such a fit
    needs that tolerance, which the record keeps."""
    peaks = support.calibrant_peaks(off={125: 20.0})
    record = await _node_record(filestore, peaks, tolerance=30.0)
    assert record["quality"]["n_points"] == 5

    file = await _audit(filestore, peaks, record)

    assert file.reproduction["reproduced"]
    assert file.reproduction["difference_ppm"] <= script.REPRODUCED_PPM
    # The audit's own refit is at the node's first tolerance, which drops it
    assert len(file.retained["collection/mode0"]) == 4


@pytest.mark.asyncio
async def test_a_node_fit_is_reproduced_with_the_record_own_refine_window(filestore):
    """A stronger peak 50 ppm from a calibrant takes the match in a 100 ppm
    window, and the fit then drops it; in a 30 ppm window the calibrant's own
    peak is matched and kept."""
    # The calibrant's own peak 3 ppm off, so a fit with it and one without
    # it are told apart
    peaks = support.calibrant_peaks(off={125: 3.0})
    peaks.append({"mz": 124.984 * (1 + 50e-6), "area": 1000.0})
    support.write_file(filestore, peaks)
    narrow = await audit.refit(
        audit.read_stored_peaks(support.FILENAME),
        support.params(refine_window=30),
        support.isotopes(),
        support.resolution,
    )
    record = {
        **narrow.fit,
        "quality": {
            "n_points": len(narrow.retained),
            "mz_error_tolerance": 15.0,
            "refine_window": 30.0,
        },
    }
    assert record["quality"]["n_points"] == 5

    file = await _audit(filestore, peaks, record)

    assert file.reproduction["reproduced"]
    assert len(file.retained["collection/mode0"]) == 4


@pytest.mark.asyncio
async def test_a_record_the_set_cannot_have_made_is_said_so(filestore):
    """A record kept more points than the collection now offers: it was
    edited after the file was fitted."""
    peaks = support.calibrant_peaks(support.CALIBRANTS + support.MORE_CALIBRANTS)
    record = {
        **support.TRUE_FIT,
        "quality": {"n_points": 7, "mz_error_tolerance": 15.0},
    }
    record["par"] = [record["par"][0] * (1 + 1e-6), record["par"][1]]

    file = await _audit(filestore, peaks, record)

    assert not file.reproduction["reproduced"]
    assert "edited after the file was fitted" in file.reproduction["reason"]


@pytest.mark.asyncio
async def test_a_record_with_no_settings_is_reproduced_at_the_defaults(filestore):
    """``fit_quality`` writes None for a setting it was not given: the
    record is then fitted with the mode's own."""
    peaks = support.calibrant_peaks()
    record = await _node_record(filestore, peaks)
    record["quality"].update(mz_error_tolerance=None, refine_window=None)

    file = await _audit(filestore, peaks, record)

    assert file.reproduction["reproduced"]


async def _counted_refits(filestore, peaks, record, mode_audit=None):
    calls = []
    real = audit.refit

    async def counted(*args, **kwargs):
        calls.append(args[1])
        return await real(*args, **kwargs)

    with patch.object(audit, "refit", counted):
        file = await _audit(filestore, peaks, record, mode_audit)
    return file, calls


@pytest.mark.asyncio
async def test_a_record_fitted_with_the_mode_settings_is_not_refitted_twice(
    filestore,
):
    peaks = support.calibrant_peaks()
    record = await _node_record(filestore, peaks)

    file, calls = await _counted_refits(filestore, peaks, record)

    assert file.reproduction["reproduced"]
    assert len(calls) == 1

    record["quality"]["refine_window"] = 30.0
    file, calls = await _counted_refits(filestore, peaks, record)
    assert [call.refine_window for call in calls] == [100, 30]


@pytest.mark.asyncio
async def test_a_refit_that_raises_fails_alone(filestore):
    """A least-squares failure under one set costs that fit, not the file's
    other fits or its reproduction."""
    peaks = support.calibrant_peaks()
    record = await _node_record(filestore, peaks)
    nitrate = support.isotopes(["NO3", "HN2O6", "H2N3O9"])
    mode_audit = _mode_audit(
        sets={"collection": support.isotopes(), "nitrate": nitrate}
    )
    real = audit.refit

    async def failing(stored, params, isotopes, *args, **kwargs):
        if isotopes is nitrate:
            raise ValueError("least_squares did not converge")
        return await real(stored, params, isotopes, *args, **kwargs)

    with patch.object(audit, "refit", failing):
        file = await _audit(filestore, peaks, record, mode_audit)

    assert file.fits["nitrate/mode0"] is None
    assert file.warnings["nitrate/mode0"] == "the refit raised ValueError"
    assert file.fits["collection/mode0"] is not None
    assert file.reproduction["reproduced"]

    summary = _summary(mode_audit, ["stored", "collection/mode0", "nitrate/mode0"])
    script.summarise_file(summary, file, mode_audit, _config("AuditTof"))
    fits = script.summary_dict(summary)["fits"]
    assert fits["nitrate/mode0"]["failed"] == {"the refit raised ValueError": 1}
    assert fits["collection/mode0"]["fitted"] == 1


@pytest.mark.asyncio
async def test_a_reproduction_that_raises_says_so(filestore):
    peaks = support.calibrant_peaks()
    record = await _node_record(filestore, peaks)
    record["quality"]["refine_window"] = 30.0
    real = audit.refit

    async def failing(stored, params, *args, **kwargs):
        if params.refine_window == 30:
            raise ValueError("least_squares did not converge")
        return await real(stored, params, *args, **kwargs)

    with patch.object(audit, "refit", failing):
        file = await _audit(filestore, peaks, record)

    assert file.fits["collection/mode0"] is not None
    assert not file.reproduction["reproduced"]
    assert file.reproduction["reason"] == "no refit: the refit raised ValueError"


@pytest.mark.parametrize(
    "record",
    [
        support.STORED_FIT,
        {**support.STORED_FIT, "verified": True},
        {**support.STORED_FIT, "quality": {"n_points": 5}, "status": "unfitted"},
        {"mode": 2, "par": [1.0, 2.0, 0.5], "quality": {"n_points": 5}},
    ],
    ids=["converter", "legacy fit", "unfitted", "another model"],
)
@pytest.mark.asyncio
async def test_only_a_node_fit_is_reproduced(filestore, record):
    file = await _audit(filestore, support.calibrant_peaks(), record)
    assert file.reproduction is None


@pytest.mark.asyncio
async def test_a_file_is_measured_under_every_fit(filestore):
    peaks = support.calibrant_peaks()
    peaks.append({"mz": 96.9601, "area": 50.0})
    # Strong, but of the other polarity; and of the polarity, but weak
    peaks.append({"mz": 150.0, "snr": 100.0, "polarity": "+"})
    peaks.append({"mz": 151.0, "snr": 5.0})
    mode_audit = _mode_audit(
        sets={
            "collection": support.isotopes(),
            "nitrate": support.isotopes(["NO3", "HN2O6", "H2N3O9"]),
        },
        series=audit.series_isotopes(
            {"sulfate": {"core": "HSO4", "unit": "H2SO4", "n": 0, "charge": "-"}}, "-"
        ),
    )

    file = await _audit(filestore, peaks, support.STORED_FIT, mode_audit)

    assert set(file.fits) == {"stored", "collection/mode0", "nitrate/mode0"}
    assert file.fits["stored"] == support.STORED_FIT
    assert len(file.retained["collection/mode0"]) == 5
    assert len(file.retained["nitrate/mode0"]) == 3
    assert len(file.matched["nitrate"]) == 3
    assert set(file.set_readings) == {"collection", "nitrate"}
    assert set(file.set_readings["nitrate"]) == file.matched["nitrate"]
    assert any(label.startswith("HO4S-") for label in file.readings)
    # The calibrants and the series member, not the two others
    assert file.strong_tofs.size == len(peaks) - 2


# --- Adding up ---


def _summary(mode_audit, names, edges=audit.DEFAULT_BAND_EDGES):
    return script.ModeSummary(
        mode_name="Nitrate",
        polarity="-",
        fit_names=names,
        band_labels=audit.band_labels(edges),
        notes=[],
    )


@pytest.mark.asyncio
async def test_an_ion_a_fit_did_not_use_is_held_out(filestore):
    """The bromide lines are in the collection, not in the nitrate set: under
    the nitrate fit their errors are held out, under the collection's used."""
    mode_audit = _mode_audit(
        sets={
            "collection": support.isotopes(),
            "nitrate": support.isotopes(["NO3", "HN2O6", "H2N3O9"]),
        }
    )
    names = ["stored", "collection/mode0", "nitrate/mode0"]
    config = _config("AuditTof")
    file = await _audit(
        filestore, support.calibrant_peaks(), support.STORED_FIT, mode_audit
    )
    summary = _summary(mode_audit, names)

    script.summarise_file(summary, file, mode_audit, config)
    report = script.summary_dict(summary)

    def ion(fit, prefix):
        return next(
            i for i in report["fits"][fit]["ions"] if i["ion"].startswith(prefix)
        )

    bromide = ion("nitrate/mode0", "Br- 78")
    assert bromide["used"] is None
    assert bromide["held_out"]["n"] == 1
    assert bromide["calibrant"] is False
    assert ion("collection/mode0", "Br- 78")["used"]["n"] == 1
    assert ion("collection/mode0", "Br- 78")["presence"] == 1.0
    assert ion("collection/mode0", "Br- 78")["kept"] == 1.0
    # The record keeps no list of its points: everything is held out
    assert ion("stored", "NO3-")["used"] is None
    assert ion("stored", "NO3-")["held_out"]["median"] == pytest.approx(-6.0, abs=0.01)
    # On the true axis the refits read every line right
    assert abs(ion("nitrate/mode0", "Br- 78")["held_out"]["median"]) < 1e-3

    bands = {band["band"]: band for band in report["fits"]["nitrate/mode0"]["bands"]}
    assert bands["40-100"]["used"]["n"] == 1  # NO3-
    assert bands["40-100"]["held_out"]["n"] == 2  # both bromide lines
    assert bands["150-200"]["used"]["n"] == 1  # NO3-.(HNO3)2


def test_a_fit_is_scored_at_the_peaks_its_own_set_matched():
    """Two sets can match one label to different peaks: each set's fits are
    read at its own, and the record at the first set's."""
    label, mz = "L", 200.0
    first = support.tof_of(mz)
    other = support.tof_of(mz * (1 + 5e-6))
    file = script.FileAudit(
        row=script.FileRow("sf1", "f", datetime(2026, 10, 20, tzinfo=UTC), "m1"),
        fits={
            "stored": support.TRUE_FIT,
            "a/mode0": support.TRUE_FIT,
            "b/mode0": support.TRUE_FIT,
        },
        warnings={},
        retained={"a/mode0": frozenset({label}), "b/mode0": frozenset({label})},
        matched={"a": frozenset({label}), "b": frozenset({label})},
        readings={label: (mz, first)},
        set_readings={"a": {label: (mz, first)}, "b": {label: (mz, other)}},
        strong_tofs=np.array([]),
        estimated=0,
        reproduction=None,
    )
    mode_audit = _mode_audit(sets={})
    summary = _summary(mode_audit, ["stored", "a/mode0", "b/mode0"])

    script.summarise_file(summary, file, mode_audit, _config("AuditTof"))

    def used(name, bucket):
        return summary.ion_errors[(name, label)][bucket]

    assert used("a/mode0", "used") == [pytest.approx(0.0, abs=1e-6)]
    assert used("b/mode0", "used") == [pytest.approx(5.0, abs=0.01)]
    assert used("stored", "held_out") == [pytest.approx(0.0, abs=1e-6)]


@pytest.mark.asyncio
async def test_the_record_is_read_at_the_collection_peaks(filestore):
    """Where the sets matched a label to different peaks, the record and
    every other set's fits read it at the first set's, the collection's: the
    set the node fitted the record under."""
    nitrate = support.isotopes(["NO3", "HN2O6", "H2N3O9"])
    mode_audit = _mode_audit(
        sets={"collection": support.isotopes(), "nitrate": nitrate}
    )
    real = audit.refit

    async def shifted(stored, params, isotopes, *args, **kwargs):
        result = await real(stored, params, isotopes, *args, **kwargs)
        if isotopes is nitrate:
            result.readings = result.readings.assign(
                sample_peak_tof=result.readings.sample_peak_tof + 1.0
            )
        return result

    with patch.object(audit, "refit", shifted):
        file = await _audit(
            filestore, support.calibrant_peaks(), support.STORED_FIT, mode_audit
        )

    label = next(label for label in file.matched["nitrate"])
    assert file.readings[label] == file.set_readings["collection"][label]
    assert file.set_readings["nitrate"][label] != file.readings[label]


@pytest.mark.asyncio
async def test_a_set_name_may_hold_a_slash(filestore):
    mode_audit = _mode_audit(
        sets={"nitrate/v2": support.isotopes(["NO3", "HN2O6", "H2N3O9"])}
    )
    file = await _audit(
        filestore, support.calibrant_peaks(), support.STORED_FIT, mode_audit
    )
    summary = _summary(mode_audit, ["stored", "nitrate/v2/mode0"])

    script.summarise_file(summary, file, mode_audit, _config("AuditTof"))
    ions = script.summary_dict(summary)["fits"]["nitrate/v2/mode0"]["ions"]

    nitrate = next(ion for ion in ions if ion["ion"].startswith("NO3-"))
    assert nitrate["calibrant"] is True
    assert nitrate["presence"] == 1.0


@pytest.mark.asyncio
async def test_a_failed_refit_is_counted_with_its_reason(filestore):
    mode_audit = _mode_audit(sets={"collection": support.isotopes(["NO3"])})
    names = ["stored", "collection/mode0"]
    file = await _audit(
        filestore, support.calibrant_peaks(), support.STORED_FIT, mode_audit
    )
    summary = _summary(mode_audit, names)

    script.summarise_file(summary, file, mode_audit, _config("AuditTof"))
    report = script.summary_dict(summary)

    assert report["fits"]["collection/mode0"]["fitted"] == 0
    assert report["fits"]["collection/mode0"]["failed"] == {
        "Not enough calibration peaks": 1
    }
    assert report["fits"]["stored"]["fitted"] == 1


@pytest.mark.asyncio
async def test_family_offsets_are_read_per_band_under_each_fit(filestore):
    """Strong peaks on CHO members of a mechanism read 0 ppm on the true axis
    and the stored axis's 6 ppm low under the record."""
    mechanism = IonizationMechanism(
        ionization_mechanism_id="nitrate",
        ionization_mechanism="[M+NO3]-",
        ionization_mechanism_polarity="-",
    )
    family = audit.family_of(mechanism, audit.cho_neutral_masses(3))
    (shift,) = audit.mechanism_shifts(mechanism)
    members = [
        audit.composition_mass(audit.parse_composition(neutral)) + shift
        for neutral in ("C5H8O4", "C10H16O6", "C10H16O9")
    ]
    peaks = support.calibrant_peaks() + [{"mz": mz} for mz in members]
    mode_audit = _mode_audit(families=[family])
    names = ["stored", "collection/mode0"]
    file = await _audit(filestore, peaks, support.STORED_FIT, mode_audit)
    summary = _summary(mode_audit, names)

    script.summarise_file(summary, file, mode_audit, _config("AuditTof"))
    report = script.summary_dict(summary)

    def offsets(fit, band):
        bands = {b["band"]: b for b in report["fits"][fit]["bands"]}
        return bands[band]["families"]["[M+NO3]-"]

    assert offsets("collection/mode0", "150-200")["median"] == pytest.approx(
        0.0, abs=0.01
    )
    assert offsets("collection/mode0", "200-300")["n"] == 1
    assert offsets("collection/mode0", "300-500")["n"] == 1
    assert offsets("stored", "150-200")["median"] == pytest.approx(-6.0, abs=0.01)


def test_the_reproduction_adds_up_with_the_files_that_missed(filestore):
    summary = _summary(_mode_audit(), ["stored"])
    summary.reproduction = [
        {"sample_file_id": "a", "reproduced": True, "difference_ppm": 0.001},
        {
            "sample_file_id": "b",
            "reproduced": False,
            "difference_ppm": 0.4,
            "reason": "x",
        },
        {
            "sample_file_id": "c",
            "reproduced": False,
            "difference_ppm": None,
            "reason": "y",
        },
    ]
    reproduction = script.summary_dict(summary)["reproduction"]
    assert reproduction["checked"] == 3
    assert reproduction["reproduced"] == 1
    assert reproduction["worst_ppm"] == 0.4
    assert [entry["sample_file_id"] for entry in reproduction["mismatches"]] == [
        "b",
        "c",
    ]


@pytest.mark.asyncio
async def test_a_run_audits_each_mode_and_counts_what_it_could_not_read(
    site, filestore
):
    """The other mode has no collection; a file with no store on disk is
    counted, not fatal."""
    config = _config(site["instrument"], sets={"nitrate": ["NO3", "HN2O6", "H2N3O9"]})
    with patch.object(
        script,
        "read_instrument_functions",
        AsyncMock(return_value=({}, support.resolution)),
    ):
        report = await script.run_audit(config)

    by_mode = {mode["mode"].split()[0]: mode for mode in report["modes"]}
    assert by_mode["Nitrate"]["unreadable"] == {"FileNotFoundError": 3}
    assert by_mode["Other"]["unreadable"] == {"FileNotFoundError": 1}
    assert "collection/mode0" in by_mode["Nitrate"]["fits"]
    assert "collection/mode0" not in by_mode["Other"]["fits"]
    assert "nitrate/mode0" in by_mode["Other"]["fits"]
    assert report["files"] == []
    assert report["without_mode"] == 1

    only = await script.run_audit(
        _config(site["instrument"], mode=by_mode["Other"]["mode"])
    )
    assert [mode["mode"] for mode in only["modes"]] == [by_mode["Other"]["mode"]]


def test_a_record_that_is_not_a_fit_is_not_one():
    assert not script._node_record(None)
    assert not script._node_record({"mode": 0, "par": [1, 2]})
    assert script._node_record({"mode": 0, "par": [1, 2], "quality": {}})


@pytest.mark.asyncio
async def test_the_whole_result_can_be_printed_as_json(no_env, capsys):
    """One document on stdout, for a run whose log is read by people and
    whose numbers go into the plan's tables."""
    import json

    no_env.setenv("TOF_AUDIT_JSON", "1")
    result = {"instrument": "TOF H", "modes": [], "files": []}
    with (
        patch.object(script, "configure_database_engine", AsyncMock()),
        patch.object(script, "run_audit", AsyncMock(return_value=result)),
    ):
        await script.run()

    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1]) == result


@pytest.mark.asyncio
async def test_nothing_is_printed_unless_asked(no_env, capsys):
    with (
        patch.object(script, "configure_database_engine", AsyncMock()),
        patch.object(script, "run_audit", AsyncMock(return_value={"x": 1})),
    ):
        await script.run()

    assert '{"x": 1}' not in capsys.readouterr().out
