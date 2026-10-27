"""Indexed read path over the mirrored MS/MS reference spectra.

The two ways a scorer (MAS-360's candidate ranking) asks the library for
spectra: :func:`by_inchikey`, every spectrum of one structure, and
:func:`by_precursor`, every spectrum whose precursor lies within a ppm window of
a measured one. Both read only the active version of each library, and both
filter on the licence each spectrum carries: libraries mix licences,
non-commercial ones among them, and what a deployment may match against is the
caller's to say, as it is for compounds in Stage A
(:func:`mascope_reference.known.iter_known_compositions`).

Each spectrum comes back as one :class:`SpectrumRecord` per library row:
the same molecule measured by two libraries is two spectra, at two energies
two more. Nothing here scores.
"""

from collections.abc import Collection
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_reference.query import _mass_bounds
from mascope_reference.schema import reference_source, reference_spectrum
from mascope_reference.scope import POLARITIES
from mascope_reference.spectrum import SpectrumRecord, inchikey


# The spectrum columns a query selects, in the order they are read back.
_SPECTRUM_COLUMNS = (
    reference_spectrum.c.source_native_id,
    reference_spectrum.c.name,
    reference_spectrum.c.formula,
    reference_spectrum.c.inchikey,
    reference_spectrum.c.smiles,
    reference_spectrum.c.inchi,
    reference_spectrum.c.precursor_mz,
    reference_spectrum.c.adduct,
    reference_spectrum.c.polarity,
    reference_spectrum.c.collision_energy,
    reference_spectrum.c.collision_energy_kind,
    reference_spectrum.c.collision_energy_text,
    reference_spectrum.c.peaks,
    reference_spectrum.c.license,
)

# A total order: (library, native id) names a spectrum within a load, and the
# row id breaks the tie a library that repeats an id would leave.
_STABLE_ORDER = (
    reference_source.c.name,
    reference_spectrum.c.source_native_id,
    reference_spectrum.c.reference_spectrum_id,
)


def _active_spectra(
    *, polarity: str | None, licenses: Collection[str] | None
) -> Select:
    """SELECT of every spectrum column + library name, active loads only.

    :raises ValueError: For a polarity no spectrum records.
    """
    stmt = (
        select(*_SPECTRUM_COLUMNS, reference_source.c.name.label("source_name"))
        .select_from(
            reference_spectrum.join(
                reference_source,
                reference_spectrum.c.reference_source_id
                == reference_source.c.reference_source_id,
            )
        )
        .where(reference_source.c.is_active.is_(True))
    )
    if polarity is not None:
        if polarity not in POLARITIES:
            raise ValueError(
                f"polarity '{polarity}' is not one of {', '.join(POLARITIES)}"
            )
        stmt = stmt.where(reference_spectrum.c.polarity == polarity)
    if licenses is not None:
        stmt = stmt.where(reference_spectrum.c.license.in_(list(licenses)))
    return stmt


def _row_to_spectrum(row: Any) -> SpectrumRecord:
    """Map a result row (spectrum columns + source_name) to a spectrum."""
    return SpectrumRecord(
        source=row.source_name,
        source_native_id=row.source_native_id,
        name=row.name,
        formula=row.formula,
        inchikey=row.inchikey,
        smiles=row.smiles,
        inchi=row.inchi,
        precursor_mz=row.precursor_mz,
        adduct=row.adduct,
        polarity=row.polarity,
        collision_energy=row.collision_energy,
        collision_energy_kind=row.collision_energy_kind,
        collision_energy_text=row.collision_energy_text,
        peaks=[(mz, intensity) for mz, intensity in row.peaks or ()],
        license=row.license,
    )


async def by_inchikey(
    session: AsyncSession,
    key: str,
    *,
    polarity: str | None = None,
    licenses: Collection[str] | None = None,
) -> list[SpectrumRecord]:
    """Every active reference spectrum of the structure an InChIKey names.

    :param session: Active async session.
    :param key: A standard InChIKey; anything else matches nothing.
    :param polarity: ``positive`` or ``negative`` to keep one; None keeps both.
    :param licenses: Keep only spectra under these licence tags; None keeps all.
    :return: The spectra, by library, then id.
    :raises ValueError: For a polarity no spectrum records.
    """
    # Built first, so a polarity that cannot be read is refused whatever the key.
    stmt = _active_spectra(polarity=polarity, licenses=licenses)
    wanted = inchikey(key)
    if wanted is None:
        return []
    stmt = stmt.where(reference_spectrum.c.inchikey == wanted).order_by(*_STABLE_ORDER)
    return [_row_to_spectrum(row) for row in (await session.execute(stmt)).all()]


async def by_precursor(
    session: AsyncSession,
    mz: float,
    ppm: float,
    *,
    polarity: str | None = None,
    licenses: Collection[str] | None = None,
) -> list[SpectrumRecord]:
    """Every active reference spectrum whose precursor is within ``ppm`` of ``mz``.

    The precursor is the ion's m/z, not the molecule's mass: a sample's MS2 scan
    is looked up by the m/z it isolated.

    :param session: Active async session.
    :param mz: The measured precursor m/z.
    :param ppm: Half-width of the window in parts per million.
    :param polarity: ``positive`` or ``negative`` to keep one; None keeps both.
    :param licenses: Keep only spectra under these licence tags; None keeps all.
    :return: The spectra, ascending in precursor m/z, then by library and id.
    :raises ValueError: For a polarity no spectrum records.
    """
    low, high = _mass_bounds(mz, ppm)
    stmt = (
        _active_spectra(polarity=polarity, licenses=licenses)
        .where(reference_spectrum.c.precursor_mz.between(low, high))
        .order_by(reference_spectrum.c.precursor_mz, *_STABLE_ORDER)
    )
    return [_row_to_spectrum(row) for row in (await session.execute(stmt)).all()]
