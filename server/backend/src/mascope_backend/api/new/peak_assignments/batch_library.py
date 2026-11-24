"""What a batch's assignment reads besides its samples: the library and the
chemistry the samples are read with.

A pass records the library it read and folds every sample again when it has
changed (``batch_stages.fold_reasons``); the batch search keys what it
remembers of a search on both (``batch_untargeted.search_key``). Below both,
so each reads them the same way.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from mascope_backend.db import (
    IonizationMechanism,
    IonizationMode,
    ReferenceSource,
    SampleItem,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _digest(rows: Iterable[tuple]) -> str:
    text = json.dumps(
        sorted([["" if v is None else str(v) for v in row] for row in rows])
    )
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def library_read(session, sample_batch_id: str) -> dict:
    """What a fold of the batch reads besides the samples: each target
    collection the batch names, as a digest of its compounds and their ions
    (a collection keeps no modification time), and each active reference
    list, by its load.

    Plain JSON values, so a pass's record compares equal to a read of the
    same library.
    """
    rows = (
        await session.execute(
            select(
                TargetCollectionInSampleBatch.target_collection_id,
                TargetCompound.target_compound_id,
                TargetCompound.target_compound_formula,
                TargetIon.target_ion_id,
                TargetIon.target_ion_formula,
                TargetIon.ionization_mechanism_id,
            )
            .select_from(TargetCollectionInSampleBatch)
            .outerjoin(
                TargetCompoundInTargetCollection,
                TargetCompoundInTargetCollection.target_collection_id
                == TargetCollectionInSampleBatch.target_collection_id,
            )
            .outerjoin(
                TargetCompound,
                TargetCompound.target_compound_id
                == TargetCompoundInTargetCollection.target_compound_id,
            )
            .outerjoin(
                TargetIon,
                TargetIon.target_compound_id == TargetCompound.target_compound_id,
            )
            .where(TargetCollectionInSampleBatch.sample_batch_id == sample_batch_id)
        )
    ).all()
    by_collection: dict[str, list[tuple]] = {}
    for row in rows:
        by_collection.setdefault(row.target_collection_id, []).append(tuple(row[1:]))
    sources = (
        await session.execute(
            select(
                ReferenceSource.reference_source_id,
                ReferenceSource.name,
                ReferenceSource.version,
                ReferenceSource.ingested_at,
            ).where(
                ReferenceSource.is_active.is_(True),
                ReferenceSource.kind == "compound",
            )
        )
    ).all()
    return {
        "target_collections": {
            collection: _digest(members)
            for collection, members in sorted(by_collection.items())
        },
        "reference_lists": {
            str(source.reference_source_id): {
                "name": source.name,
                "version": source.version,
                "ingested_at": _iso(source.ingested_at),
            }
            for source in sources
        },
    }


async def chemistry_read(session, sample_batch_id: str) -> str:
    """The ionization chemistry the batch's samples are read with, as one
    digest: every ionization mechanism a sample of any mode can be read
    through, and the modes the batch's samples are in, with the mechanisms
    each names. An edited mechanism or mode changes it.

    A mechanism row is read whole rather than by the modes' ids, since a
    search opens secondary channels by notation, outside its mode's own.
    """
    mechanisms = (
        await session.execute(
            select(
                IonizationMechanism.ionization_mechanism_id,
                IonizationMechanism.ionization_mechanism_polarity,
                IonizationMechanism.ionization_mechanism,
                IonizationMechanism.operation,
                IonizationMechanism.species,
                IonizationMechanism.species_charge,
                IonizationMechanism.multimer_n,
                IonizationMechanism.neutral_loss,
            )
        )
    ).all()
    modes = (
        await session.execute(
            select(
                IonizationMode.ionization_mode_id,
                IonizationMode.ionization_mode_polarity,
                IonizationMode.instrument,
                IonizationMode.version,
                IonizationMode.ionization_mechanism_ids,
            ).where(
                IonizationMode.ionization_mode_id.in_(
                    select(SampleItem.ionization_mode_id).where(
                        SampleItem.sample_batch_id == sample_batch_id
                    )
                )
            )
        )
    ).all()
    return _digest(
        [("mechanism", *row) for row in mechanisms]
        + [
            ("mode", *row[:4], json.dumps(sorted(row.ionization_mechanism_ids or [])))
            for row in modes
        ]
    )
