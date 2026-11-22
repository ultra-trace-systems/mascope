"""Stand-ins for what the stage list reads of a sample, for the tests of the
batch passes that measure a decision on a sample's members: the batch search's
propagation, the series detection, a pin and an import
(``pipeline.PROPAGATION``). The integration tests drive whole passes over the
test database with them; the unit tests measure one reading.

The stage list resolves each member's sample as a run does, so these stand in
for its peaks and its mechanisms, and for the seeded scorer, whose canned
pairings are turned into the scored frame Stage A's election reads
(``engine.invert_matches_to_peak_assignments``). Everything between them is
the stage list's own: the election, the judging and the level.

A sample's own run and its ingest fold read the batch's decisions too
(MAS-985): :func:`stub_known_set` stands in for the known set they propose,
and :func:`run_sample` and :func:`fold_sample` drive the two paths over the
test database, each kept as it keeps what it judged.
"""

from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.new.peak_assignments import (
    batch_fold,
    judging_loop,
    model_readers,
    pipeline,
    sample_inputs,
    sample_mechanisms,
    service,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.envelope_fit import RunEnvelopeFit
from mascope_backend.api.new.peak_assignments.fold_view import fold_run_id
from mascope_tools.composition.envelope_tolerance import (
    FORM_DEFAULT,
    EnvelopeLines,
    fit_envelope_tolerance,
)
from mascope_tools.composition.heuristic_filter import element_counts
from mascope_tools.composition.mass_accuracy import SampleMassAccuracy
from mascope_tools.composition.utils import to_hill_order


def mechanism(notation: str, mechanism_id: str) -> SimpleNamespace:
    """A mechanism row as the service reads one."""
    return SimpleNamespace(
        ionization_mechanism=notation,
        ionization_mechanism_id=mechanism_id,
        ionization_mechanism_polarity=notation[-1],
    )


def frame(peaks) -> pd.DataFrame:
    """A peak list from ``(sample_peak_id, mz, intensity)`` triples."""
    return pd.DataFrame(
        [
            {"sample_peak_id": peak, "mz": mz, "intensity": intensity}
            for peak, mz, intensity in peaks
        ]
    )


def protonated(formula: str) -> str:
    """The ``[M+H]+`` ion of a neutral, as the matcher names it."""
    counts = dict(element_counts(formula))
    counts["H"] = counts.get("H", 0) + 1
    return f"{to_hill_order(counts)}+"


#: The modules of a run that read each name :func:`stub_sample_reads` stands
#: in for, each patched with the one stand-in, as one in the service reached
#: every read of it when the service held them all.
_SAMPLE_READERS = {
    "load_sample_peaks": (sample_inputs, pipeline, model_readers),
    "fetch_sample_mechanisms": (sample_mechanisms, service, model_readers),
    "fetch_mechanisms_by_notation": (sample_mechanisms, service),
    "_resolution_of": (sample_inputs, model_readers),
    "_source_line_cap": (judging_loop,),
}


def _stand_in(monkeypatch, name, value) -> None:
    """Stand in for ``name`` in each module of a run that reads it."""
    for module in _SAMPLE_READERS[name]:
        monkeypatch.setattr(module, name, value)


def stub_sample_reads(
    monkeypatch,
    peaks_of: Callable[[str], pd.DataFrame],
    mechanisms,
) -> None:
    """The sample's peaks (``peaks_of(sample_item_id)``) and its mode's
    mechanisms; no secondary channel the deployment holds, no resolving power
    and no ion current read. Its batch's records, its setup and its named
    context are read from the test database, which holds none of them."""
    rows = list(mechanisms)
    _stand_in(
        monkeypatch,
        "load_sample_peaks",
        lambda sample: peaks_of(sample.sample_item_id),
    )
    _stand_in(
        monkeypatch,
        "fetch_sample_mechanisms",
        AsyncMock(return_value=([m.ionization_mechanism_id for m in rows], rows)),
    )
    _stand_in(monkeypatch, "fetch_mechanisms_by_notation", AsyncMock(return_value=[]))
    _stand_in(monkeypatch, "_resolution_of", AsyncMock(return_value=None))
    _stand_in(monkeypatch, "_source_line_cap", AsyncMock(return_value=None))


def seeded_frame(
    ion_by_seed: dict,
    fit_by_ion: dict,
    errors: dict,
    peaks: pd.DataFrame,
    ion_formula: Callable[[str, str], str],
) -> pd.DataFrame:
    """The scored frame a seeded pass gives for canned pairings: each seed's
    ion a line on every peak of ``peaks`` it paired to, the lightest its
    monoisotopic one, all at the ion's canned fit.

    :param ion_formula: The ion a seed's formula makes through its mechanism.
    """
    by_peak = {str(row.sample_peak_id): row for row in peaks.itertuples(index=False)}
    rows = []
    for (formula, mechanism_id), ion in ion_by_seed.items():
        fit = fit_by_ion.get(ion)
        if fit is None:
            continue
        paired = sorted(
            (peak for ion_id, peak in errors if ion_id == ion and peak in by_peak),
            key=lambda peak: by_peak[peak].mz,
        )
        for index, peak in enumerate(paired):
            observed = by_peak[peak]
            error = errors[(ion, peak)]
            rows.append(
                {
                    "target_isotope_id": f"{ion}#{index}",
                    "target_ion_id": ion,
                    "target_isotope_formula": None,
                    "mz": float(observed.mz),
                    "relative_abundance": 1.0 if index == 0 else 0.1,
                    "resolution": "HIGH",
                    "target_ion_formula": ion_formula(formula, mechanism_id),
                    "ionization_mechanism_id": mechanism_id,
                    "ionization_mechanism": None,
                    "target_compound_id": None,
                    "target_compound_formula": formula,
                    "sample_peak_id": peak,
                    "sample_peak_mz": float(observed.mz),
                    "sample_peak_intensity": float(observed.intensity),
                    "sample_peak_tof": None,
                    "match_score": float(fit),
                    "match_mz_error": error.get("mz_error_ppm"),
                    "match_abundance_error": error.get("abundance_error", 0.0),
                }
            )
    return pd.DataFrame(rows)


def stub_seeded_scorer(
    monkeypatch,
    seeded,
    peaks_of: Callable[[str], pd.DataFrame],
    ion_formula: Callable[[str, str], str] = lambda formula, _mech: protonated(formula),
    offsets: list | None = None,
) -> None:
    """The seeded scorer the stage list measures a given reading with, from a
    stand-in ``seeded(sample, seeds, params)`` that gives canned pairings: the
    frame Stage A's election reads is built from them (:func:`seeded_frame`).

    :param offsets: Where each measurement's model is appended, with the
        sample it measured, if given.
    """

    async def score(sample, seeds, params, **kwargs):
        if offsets is not None:
            offsets.append((sample, kwargs.get("model")))
        ion_by_seed, fit_by_ion, errors, _ = await seeded(sample, seeds, params)
        return (
            ion_by_seed,
            fit_by_ion,
            errors,
            seeded_frame(
                ion_by_seed,
                fit_by_ion,
                errors,
                peaks_of(sample.sample_item_id),
                ion_formula,
            ),
        )

    monkeypatch.setattr(pipeline, "score_seeds", score)


def stub_known_set(
    monkeypatch, rows_of: Callable[[str, str], list[dict]]
) -> list[set[str]]:
    """The known set a run or a fold proposes (``pipeline._stage_a_assignments``),
    canned: the rows ``rows_of(sample_item_id, run_id)`` gives, but for those
    on a peak the claim stage took, at a measurement of nothing fitted and
    today's line-share tolerance, with no frame to score again at a refit's
    model and no confidence curve.

    :return: The peaks each call was told the claim stage took, in order.
    """
    excluded: list[set[str]] = []

    async def stage_a(
        sample,
        config,
        match_params,
        mechanism_ids,
        mechanisms,
        run_id,
        excluded_peak_ids=None,
        **kwargs,
    ):
        taken = set(excluded_peak_ids or ())
        excluded.append(taken)
        rows = [
            row
            for row in rows_of(sample.sample_item_id, run_id)
            if str(row["sample_peak_id"]) not in taken
        ]
        return (
            rows,
            None,
            SampleMassAccuracy(),
            RunEnvelopeFit(fit_envelope_tolerance(FORM_DEFAULT, EnvelopeLines())),
            frozenset(),
            None,
            None,
        )

    monkeypatch.setattr(pipeline, "_stage_a_assignments", stage_a)
    return excluded


async def run_sample(
    sample_item_id: str, config: PeakAssignmentConfig | None = None
) -> pipeline.RunContext:
    """A sample assigned again: a run of it through the stage list
    (``pipeline.RUN``), kept as a run keeps its ledger - its rows written, the
    run completed and folded into the batch ledger. The known set alone, unless
    the config asks for the search.

    :return: The pass, the run's id its ``run_id``.
    """
    config = config or PeakAssignmentConfig(run_untargeted=False)
    run = await service._create_run(sample_item_id, config)
    return await pipeline.run_stages(
        pipeline.RunContext(
            path=pipeline.RUN,
            sample=await fetch_sample(sample_item_id),
            config=config,
            run_id=run.peak_assignment_run_id,
            keep=service.keep_run,
            run=run,
        )
    )


async def fold_sample(
    sample_item_id: str, config: PeakAssignmentConfig | None = None
) -> pipeline.RunContext:
    """A sample folded in without a run (``pipeline.INGEST_FOLD``): the known
    set alone, folded into its batch's ledger as the ingest fold folds it.

    :return: The pass.
    """
    return await pipeline.run_stages(
        pipeline.RunContext(
            path=pipeline.INGEST_FOLD,
            sample=await fetch_sample(sample_item_id),
            config=config or PeakAssignmentConfig(run_untargeted=False),
            run_id=fold_run_id(sample_item_id),
            keep=batch_fold.keep_fold,
            strict_instrument=False,
        )
    )


__all__ = [
    "fold_sample",
    "frame",
    "mechanism",
    "protonated",
    "run_sample",
    "seeded_frame",
    "stub_known_set",
    "stub_sample_reads",
    "stub_seeded_scorer",
]
