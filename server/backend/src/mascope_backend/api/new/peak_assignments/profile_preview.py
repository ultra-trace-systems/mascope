"""What a run would search under, read before the run is launched.

A run resolves its chemistry when it starts (``profiles.resolve_profile``) and
records the answer on itself. A launcher that offers ``auto`` has to say what
``auto`` will mean before anything starts, and a batch launcher has to say it
for every sample the search may reach. The resolution reads a sample's
ionization mode and polarity and nothing else, so the preview asks for those
alone: three queries whatever the number of samples, since samples sharing a
mode and a polarity resolve alike.
"""

from __future__ import annotations

from sqlalchemy import func, select

from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.profiles import (
    SampleChemistry,
    preview_resolutions,
)
from mascope_backend.db import (
    IonizationMechanism,
    IonizationMode,
    SampleBatch,
    SampleItem,
    async_session,
)


async def preview_profiles(
    config: PeakAssignmentConfig,
    *,
    sample_item_id: str | None = None,
    sample_batch_id: str | None = None,
) -> list[dict]:
    """Resolve a run config's profile and context over one sample or a batch.

    The mechanisms are read the way a run reads them
    (``service.fetch_sample_mechanisms``): the mode's own, at the sample's
    polarity. A sample with no mode resolves on its polarity alone, as the
    fingerprint does when nothing is diagnostic.

    :param config: The run configuration to resolve.
    :param sample_item_id: The sample to resolve for.
    :param sample_batch_id: Or the batch whose samples to resolve for.
    :raises ValueError: Neither or both scopes were given.
    :raises NotFoundException: The sample or the batch does not exist. A
        superuser passes the access check whatever the id, so this is where a
        missing one is told apart from an empty batch.
    :return: One record per distinct resolution, the most samples first; empty
        for a batch that holds no sample.
    """
    if (sample_item_id is None) == (sample_batch_id is None):
        raise ValueError("Name exactly one of a sample and a batch.")
    scope = (
        SampleItem.sample_item_id == sample_item_id
        if sample_item_id is not None
        else SampleItem.sample_batch_id == sample_batch_id
    )
    async with async_session() as session:
        groups = (
            await session.execute(
                select(
                    SampleItem.ionization_mode_id,
                    SampleItem.polarity,
                    func.count(),
                )
                .where(scope)
                .group_by(SampleItem.ionization_mode_id, SampleItem.polarity)
            )
        ).all()
        if not groups:
            if sample_item_id is not None:
                raise NotFoundException(f"Sample with ID '{sample_item_id}' not found")
            if await session.get(SampleBatch, sample_batch_id) is None:
                raise NotFoundException(
                    f"Sample batch with ID '{sample_batch_id}' not found"
                )
            return []
        mode_ids = {mode_id for mode_id, _, _ in groups if mode_id is not None}
        modes = (
            {
                mode.ionization_mode_id: list(mode.ionization_mechanism_ids or [])
                for mode in (
                    await session.execute(
                        select(IonizationMode).where(
                            IonizationMode.ionization_mode_id.in_(mode_ids)
                        )
                    )
                ).scalars()
            }
            if mode_ids
            else {}
        )
        mechanism_ids = {
            mechanism_id
            for mechanism_ids in modes.values()
            for mechanism_id in mechanism_ids
        }
        mechanisms = (
            {
                mechanism.ionization_mechanism_id: (
                    mechanism.ionization_mechanism,
                    mechanism.ionization_mechanism_polarity,
                )
                for mechanism in (
                    await session.execute(
                        select(IonizationMechanism).where(
                            IonizationMechanism.ionization_mechanism_id.in_(
                                mechanism_ids
                            )
                        )
                    )
                ).scalars()
            }
            if mechanism_ids
            else {}
        )
    return preview_resolutions(
        config,
        [
            SampleChemistry(
                mechanism_notations=tuple(
                    mechanisms[mechanism_id][0]
                    for mechanism_id in modes.get(mode_id, ())
                    if mechanism_id in mechanisms
                    and mechanisms[mechanism_id][1] == polarity
                ),
                polarity=polarity,
                samples=int(count),
            )
            for mode_id, polarity, count in groups
        ],
    )
