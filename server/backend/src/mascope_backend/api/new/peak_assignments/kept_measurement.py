"""What a pass of the batch stages measured of each sample's readings, kept for
the next time the pass judges that sample again (MAS-1073).

A pass judges a sample again after every stage that rewrites its members
(``batch_rejudge``): up to four times, each time proposing every reading the
sample's members carry, one given formula per member (``pipeline.PROPAGATION``).
Each reading's measurement - its ion's lines matched on the sample's peaks,
gated and fit-scored at the sample's recorded measurement model
(``seeded_scoring.score_seeds``) - is the same every time unless what it is
measured at changes. So the pass keeps each reading's scored lines, and a
later judging scores only the readings it has not measured at the same
inputs: the ones a stage wrote or changed. Every other reading keeps the lines
an earlier judging of the pass scored, and the election, the judging and the
caps then run over all of them as a run runs them.

That holds because the seeded measurement reads each ion alone: its lines are
paired to the sample's peaks one by one, gated by the ion's own heights, and
scored at a model the frame is never fitted on. So the frame a judging elects
from is the one a single scoring of every reading gives, row for row; the
pieces are put back in the order the scorer builds its frame, by formula.

What a reading is measured at, and so what keeps it:

- the sample's peaks and its match parameters, the recorded model it is scored
  at, and the purity its labelled ions are read at: one fingerprint per sample,
  and a sample whose fingerprint moved is measured again whole;
- for a reading the sample's lists hold, the peaks the claim stage took, which
  its ion is measured without: such a reading is kept per claim set, so a stage
  that claims another peak for the source measures those readings again.

A sample whose run recorded no measurement model (one from before the record,
MAS-1071) is read at a stand-in its refit stage refits on what it committed,
so nothing is kept for it: it is measured whole every time, as before, and the
pass says so (``batch_stages.judge_again``).

The pass keeps three more things of each sample, which every judging of it
read again before (MAS-1172), all three read only through
:func:`kept_resolution`, :func:`kept_lists` and :func:`envelopes_of`:

- the file's resolving power, read once per pass while the sample's peaks are
  the ones it was read with;
- what the sample's lists hold (``engine.listed_formula_identities``,
  ``engine.listed_ions``), read once per resolving power, mechanisms,
  envelope floor, known window and purity;
- each ion's predicted envelope at that resolving power
  (``envelope_memo``), predicted once for the whole pass.

The store lives as long as the pass; nothing is written.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

from mascope_backend.api.new.peak_assignments import sample_inputs
from mascope_backend.api.new.peak_assignments.envelope_memo import EnvelopeMemo
from mascope_backend.api.new.peak_assignments.seeded_scoring import scored_maps
from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS, label_purity


#: A seed: a formula and the mechanism its ion is made through.
Seed = tuple[str, str]

#: The scorer, ``seeded_scoring.score_seeds`` as the stage list calls it.
Scorer = Callable[..., Awaitable[tuple[dict, dict, dict, pd.DataFrame]]]

#: The peak list's columns the fingerprint reads.
_PEAK_COLUMNS = ("sample_peak_id", "mz", "intensity")


@dataclass
class _Sample:
    """One sample's kept readings: every row scored at ``fingerprint``, and
    each reading's rows and ion in it."""

    fingerprint: tuple
    frame: pd.DataFrame = field(default_factory=pd.DataFrame)
    rows: dict[tuple, np.ndarray] = field(default_factory=dict)
    ions: dict[tuple, Optional[str]] = field(default_factory=dict)


@dataclass
class _Reads:
    """What a pass read of one sample's inputs: its resolving power, read
    with the peaks ``peaks`` keys (:func:`_peaks_key`), and its lists by what
    they were read at."""

    peaks: Any
    resolution: Any
    lists: dict[tuple, tuple[frozenset, dict]] = field(default_factory=dict)


@dataclass
class KeptMeasurement:
    """The readings a pass measured, by sample, and what each judging of a
    sample scored and kept (``counts``, by sample, the last judging's); and
    what it read of each sample (``reads``) and predicted of its ions'
    envelopes (``envelopes``)."""

    samples: dict[str, _Sample] = field(default_factory=dict)
    counts: dict[str, Counter] = field(default_factory=dict)
    reads: dict[str, _Reads] = field(default_factory=dict)
    envelopes: dict[str, EnvelopeMemo] = field(default_factory=dict)

    def forget(self, sample_item_id: str, *, unrecorded: bool = False) -> None:
        """Keep nothing of a sample: one measured whole, as a sample whose run
        recorded no model is (``unrecorded``, counted)."""
        self.samples.pop(sample_item_id, None)
        if unrecorded:
            self.counts[sample_item_id] = Counter(samples_unrecorded=1)


def _peaks_key(peaks_df: Optional[pd.DataFrame]) -> Any:
    """The sample's peaks, as the fingerprint reads them."""
    if peaks_df is None or peaks_df.empty:
        return 0
    columns = [c for c in _PEAK_COLUMNS if c in peaks_df.columns]
    return (
        len(peaks_df),
        int(pd.util.hash_pandas_object(peaks_df[columns], index=False).sum()),
    )


def _purity() -> tuple:
    """The purity labelled ions are read at, set by the stage list."""
    return tuple(label_purity(symbol) for symbol in sorted(CUSTOM_ELEMENTS))


def fingerprint(
    peaks_df: Optional[pd.DataFrame], match_params: Any, model: Any
) -> tuple:
    """What every reading of a sample is measured at: its peaks, its match
    parameters, the model it is scored at and the purity its labelled ions
    are read at (``custom_elements.labelled_purity``, set by the stage list)."""
    dump = getattr(match_params, "model_dump_json", None)
    return (
        _peaks_key(peaks_df),
        dump() if dump is not None else repr(match_params),
        repr(model),
        _purity(),
    )


def envelopes_of(ctx: Any) -> Optional[EnvelopeMemo]:
    """The memo a stage list pass predicts its envelopes in
    (``envelope_memo.open_memo``): the sample's for the whole of the batch's
    pass (:attr:`KeptMeasurement.envelopes`), or None, which opens one for this
    pass alone."""
    kept: Optional[KeptMeasurement] = ctx.kept
    if kept is None:
        return None
    return kept.envelopes.setdefault(str(ctx.sample.sample_item_id), EnvelopeMemo())


async def kept_resolution(ctx: Any) -> Any:
    """The file's resolving power (``sample_inputs._resolution_of``), read
    once for the whole of the batch's pass while the sample's peaks are the
    ones it was read with: the same function every judging of the pass, so the
    envelopes predicted at it serve each (:func:`envelopes_of`). Peaks read
    again drop what was read with the old ones.

    :param ctx: The stage list's pass, its sample's peaks read.
    """
    kept: Optional[KeptMeasurement] = ctx.kept
    if kept is None:
        return await sample_inputs._resolution_of(ctx.sample)
    sample_item_id = str(ctx.sample.sample_item_id)
    peaks = _peaks_key(ctx.peaks_df)
    reads = kept.reads.get(sample_item_id)
    if reads is None or reads.peaks != peaks:
        resolution = await sample_inputs._resolution_of(ctx.sample)
        reads = _Reads(peaks=peaks, resolution=resolution)
        kept.reads[sample_item_id] = reads
        if sample_item_id in kept.envelopes:
            kept.envelopes[sample_item_id].clear()
    return reads.resolution


async def kept_lists(
    ctx: Any, reader: Callable[[], Awaitable[tuple[frozenset, dict]]]
) -> tuple[frozenset, dict]:
    """What the sample's lists hold, the neutrals they name and their ions
    (``pipeline._propose_given``), read once for the whole of the batch's pass
    at the same inputs: the resolving power the lists' lines are resolved at,
    the mode's mechanisms and the ones the lists are read through, the
    envelope floor, the known window and the purity.

    :param ctx: The stage list's pass, its chemistry resolved.
    :param reader: Reads the lists at the pass's inputs.
    """
    kept: Optional[KeptMeasurement] = ctx.kept
    reads = None if kept is None else kept.reads.get(str(ctx.sample.sample_item_id))
    if reads is None or reads.resolution is not ctx.resolution:
        return await reader()
    key = (
        ctx.match_params.isotope_abundance_threshold,
        tuple(ctx.mechanism_ids),
        tuple(m.ionization_mechanism_id for m in ctx.judged_mechanisms),
        ctx.resolved_profile.context.known_window,
        _purity(),
    )
    if key not in reads.lists:
        reads.lists[key] = await reader()
    return reads.lists[key]


def _key(seed: Seed, listed: bool, claimed: frozenset[str]) -> tuple:
    """A reading's key: its seed, and for one the lists hold, the claims its
    ion is measured without."""
    return (seed, claimed if listed else None)


def _order(key: tuple) -> tuple[str, str]:
    """The scorer builds its frame formula by formula
    (``seeded_scoring.build_seeded_isotopes_df``)."""
    formula, mechanism_id = key[0]
    return str(formula), str(mechanism_id)


async def score_kept(
    ctx: Any, scorer: Scorer, listed_seeds: Collection[Seed]
) -> tuple[dict, dict, dict, pd.DataFrame]:
    """The seeded measurement of a stage list pass's given readings
    (``seeded_scoring.score_seeds``), scoring only those the batch's pass has
    not measured at the same inputs (``RunContext.kept``).

    Without a store, every reading is scored, as a path with no pass around
    it scores them; and so is every reading of a sample whose model no
    record held: a stand-in its refit refits, at which nothing is kept
    (MAS-1071).

    :param ctx: The stage list's pass (``pipeline.RunContext``): its sample,
        its seeds and match parameters, the model they are scored at and
        whether a record held it, its peaks, its resolving power and the
        peaks its claim stage took.
    :param scorer: The scorer, called with the readings to measure.
    :param listed_seeds: The seeds the sample's lists hold.
    :return: What the scorer returns for every seed: the ion by seed, the fit
        by ion, the errors by pairing, and the scored frame.
    """
    kept: Optional[KeptMeasurement] = ctx.kept
    sample, seeds, match_params = ctx.sample, ctx.seeds, ctx.match_params
    listed = set(listed_seeds)

    async def measure(chosen: set[Seed]) -> tuple[dict, dict, dict, pd.DataFrame]:
        return await scorer(
            sample,
            chosen,
            match_params,
            model=ctx.model,
            resolution=ctx.resolution,
            claimed_peak_ids=ctx.claimed_peak_ids,
            listed_seeds={seed for seed in chosen if seed in listed},
        )

    if kept is None:
        return await measure(set(seeds))
    sample_item_id = str(sample.sample_item_id)
    if not ctx.model_recorded:
        kept.forget(sample_item_id, unrecorded=True)
        return await measure(set(seeds))
    now = fingerprint(ctx.peaks_df, match_params, ctx.model)
    entry = kept.samples.get(sample_item_id)
    if entry is None or entry.fingerprint != now:
        entry = _Sample(fingerprint=now)
        kept.samples[sample_item_id] = entry
    claimed = frozenset(str(p) for p in ctx.claimed_peak_ids)
    keys = {seed: _key(seed, seed in listed, claimed) for seed in seeds}
    missing = {seed for seed, key in keys.items() if key not in entry.ions}
    if missing:
        ion_by_seed, _fits, _errors, scored = await measure(missing)
        _keep(entry, {keys[seed]: ion_by_seed.get(seed) for seed in missing}, scored)
    kept.counts[sample_item_id] = Counter(
        readings_scored=len(missing), readings_kept=len(seeds) - len(missing)
    )
    return _assemble(entry, [keys[seed] for seed in seeds])


def _keep(
    entry: _Sample, ions: dict[tuple, Optional[str]], scored: pd.DataFrame
) -> None:
    """Add what one scoring measured to a sample's kept readings: each
    reading's rows, by its ion; a reading with none keeps none, as it scored
    none."""
    offset = len(entry.frame)
    if not scored.empty:
        scored = scored.reset_index(drop=True)
        entry.frame = (
            scored.copy()
            if entry.frame.empty
            else pd.concat([entry.frame, scored], ignore_index=True)
        )
        positions = (
            scored.groupby(scored["target_ion_id"].astype(str), sort=False).indices
            if "target_ion_id" in scored.columns
            else {}
        )
    else:
        positions = {}
    empty = np.array([], dtype=np.intp)
    for key, ion in ions.items():
        entry.ions[key] = ion
        found = positions.get(str(ion)) if ion is not None else None
        entry.rows[key] = empty if found is None else np.asarray(found) + offset


def _assemble(
    entry: _Sample, keys: list[tuple]
) -> tuple[dict, dict, dict, pd.DataFrame]:
    """The scorer's answer for ``keys``, put together from the kept rows."""
    ordered = sorted(keys, key=_order)
    ion_by_seed = {
        key[0]: entry.ions[key] for key in ordered if entry.ions[key] is not None
    }
    positions = [entry.rows[key] for key in ordered if len(entry.rows[key])]
    if not positions:
        return ion_by_seed, {}, {}, pd.DataFrame()
    frame = entry.frame.take(np.concatenate(positions)).reset_index(drop=True)
    fit_by_ion, errors = scored_maps(frame)
    return ion_by_seed, fit_by_ion, errors, frame


__all__ = [
    "KeptMeasurement",
    "envelopes_of",
    "fingerprint",
    "kept_lists",
    "kept_resolution",
    "score_kept",
]
