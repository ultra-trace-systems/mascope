"""The reference lists' warm-up: their fine lines built after a worker starts.

With the shipped reference lists loaded, the first sample a backend worker
assigns pays for their expansion into the fine lines of every ion they name,
some 16 to 20 s for each mechanism list and polarity, once per worker and
again after every restart (MAS-1274). A worker therefore builds those entries
in the background once it has started (MAS-1277, MAS-1281): start-up does not
wait for it, ``/ready`` answers as before, and a sample that arrives first
waits for its own entry as it always has, never for a second copy of it.

The entries are the ones a run reads because they are made by a run's own
steps: for each ionization mode and polarity in use, its newest sample is
resolved as the ingest fold resolves it (``pipeline.resolve`` on the
:data:`~pipeline.INGEST_FOLD` path, at its setup's labelled purity), and the
lists are read through the mechanisms its Stage A and its list reads use, by
``sample_inputs.reference_fine_lines_built``, the cache's one way in. A file
whose spectrum opens another secondary channel than that sample's reads
another list, and builds its entry when it arrives, as before.

The lists change by a ``reference sync``, a seed or a list's activation: the
cache key carries ``known_state_fingerprint``, so the next read after a change
misses and builds anew. Nothing tells a worker of the change when it happens,
so nothing here warms again after one (mascope#3642).

Switched by ``backend.reference_warmup`` (on by default), and off where peak
assignment is off or no compound list is active.
"""

import asyncio
import time

from sqlalchemy import select

from mascope_backend.api.new.ionization.modes.versions import sample_label_purity
from mascope_backend.api.new.peak_assignments import pipeline, sample_inputs
from mascope_backend.api.new.peak_assignments.config import (
    PeakAssignmentConfig,
    peak_assignment_enabled,
)
from mascope_backend.db import Sample, async_session
from mascope_backend.runtime import runtime
from mascope_reference import known_state_fingerprint
from mascope_tools.composition.custom_elements import labelled_purity


#: The run id the warm-up's resolution is stamped with: it writes no row.
_WARMUP_RUN_ID = "reference-warmup"

#: This worker's warm-up task, kept so it is not collected while it runs and
#: so the worker's shutdown can cancel it.
_task: asyncio.Task | None = None


def reference_warmup_enabled() -> bool:
    """Whether a worker warms the reference lists after it starts.

    Read from ``reference_warmup`` in the runtime ``[backend]`` config (on by
    default), and only where peak assignment is on: with it off nothing reads
    the lists.
    """
    return peak_assignment_enabled() and bool(
        getattr(runtime.config, "reference_warmup", True)
    )


def start_reference_warmup() -> asyncio.Task | None:
    """Start this worker's warm-up in the background and return its task, or
    None where it is switched off. The caller does not wait for it."""
    global _task
    if not reference_warmup_enabled():
        return None
    _task = asyncio.create_task(warm_reference_lists(), name="reference-warmup")
    return _task


async def stop_reference_warmup() -> None:
    """Cancel the warm-up if it still runs, for a worker's shutdown."""
    global _task
    task, _task = _task, None
    if task is None or task.done():
        return
    task.cancel()
    # Waited for without raising its cancellation here, so a cancellation of
    # the shutdown itself still reaches the caller.
    await asyncio.wait({task})


async def warm_reference_lists() -> int:
    """Build the reference lists' cache entries the first samples would.

    Never raises: a failure is logged and leaves the lazy path as it is, so
    the worker serves whatever happens here.

    :return: The entries this call built.
    """
    try:
        return await _warm()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - the warm-up must never stop a worker
        runtime.logger.exception(
            "Reference lists warm-up failed; the first sample of each mode "
            "builds its entry as it arrives"
        )
        return 0


async def _warm() -> int:
    """:func:`warm_reference_lists` itself."""
    async with async_session() as session:
        fingerprint = await known_state_fingerprint(session)
    if not fingerprint:
        return 0
    samples = await _representative_samples()
    if not samples:
        return 0
    started = time.monotonic()
    runtime.logger.info(
        f"Reference lists warm-up started: {len(samples)} ionization mode and "
        "polarity pair(s) in use"
    )
    built = 0
    for sample in samples:
        if built >= sample_inputs._REFERENCE_ISOTOPE_CACHE_MAX:
            # More would only push out what was built first.
            break
        try:
            built += await _warm_sample(sample)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - one sample never ends the rest
            runtime.logger.warning(
                f"Reference lists warm-up skipped sample "
                f"'{sample.sample_item_name}': {error}"
            )
    runtime.logger.info(
        f"Reference lists warm-up done: {built} entr{'y' if built == 1 else 'ies'} "
        f"built in {time.monotonic() - started:.1f} s"
    )
    return built


async def _representative_samples() -> list[Sample]:
    """The newest sample with peaks of each ionization mode and polarity,
    the most recently acquired pair first."""
    async with async_session() as session:
        rows = (
            (
                await session.execute(
                    select(Sample)
                    .where(
                        Sample.ionization_mode_id.is_not(None),
                        Sample.instrument_function_id.is_not(None),
                    )
                    .distinct(Sample.ionization_mode_id, Sample.polarity)
                    .order_by(
                        Sample.ionization_mode_id,
                        Sample.polarity,
                        Sample.datetime_utc.desc().nulls_last(),
                    )
                )
            )
            .scalars()
            .all()
        )
    return sorted(
        rows,
        key=lambda sample: (sample.datetime_utc is not None, sample.datetime_utc),
        reverse=True,
    )


async def _warm_sample(sample: Sample) -> int:
    """Build the entries a fold of ``sample`` reads; return how many it built.

    Resolved as the ingest fold resolves a sample (``batch_fold``), at the
    labelled purity its runs read at (``pipeline.run_stages``): Stage A reads
    the lists through the searched mechanisms, the lists' own reads through
    the judged ones, which are the same list unless a batch decision gives
    the sample another channel.
    """
    with labelled_purity(await sample_label_purity(sample.sample_item_id)):
        ctx = pipeline.RunContext(
            path=pipeline.INGEST_FOLD,
            sample=sample,
            config=PeakAssignmentConfig(run_untargeted=False),
            run_id=_WARMUP_RUN_ID,
            strict_instrument=False,
        )
        await pipeline.resolve(ctx)
        known_window = ctx.resolved_profile.context.known_window
        built = 0
        seen: set[tuple[str, ...]] = set()
        for mechanisms in (ctx.searched_mechanisms, ctx.judged_mechanisms):
            ids = tuple(m.ionization_mechanism_id for m in mechanisms)
            if ids in seen:
                continue
            seen.add(ids)
            _, was_built = await sample_inputs.reference_fine_lines_built(
                sample, mechanisms, known_window
            )
            built += was_built
    return built
