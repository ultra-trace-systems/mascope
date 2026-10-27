"""The normalized reference MS/MS spectrum, and the rules a spectrum is admitted by.

Every spectral adapter (MassBank, MoNA, GNPS) emits :class:`SpectrumRecord`
instances, or a :class:`SkippedSpectrum` saying why a record of the dump is not
one, so that the three dump formats meet in one shape before anything is
stored. :func:`finalize_spectrum` then does what the formats share: it
canonicalizes the compound's formula as :mod:`mascope_reference.normalize` does
a compound's, keeps an InChIKey only when it is one, writes the adduct, the
polarity, the licence and the collision energy the same way whichever source
spelled them, and cleans the centroid list.

A spectrum is admitted only when it can answer the read path
(:mod:`mascope_reference.spectral_query`): an MS2 spectrum, measured rather
than predicted, with a precursor m/z and at least one peak. Every other record
of a dump is counted under its reason and left out, so a load says what it
dropped.

The licence is the record's own wherever the source states one per record, as
MassBank and some MoNA records do: libraries mix licences, non-commercial ones
among them, and the read path filters on this tag exactly as Stage A filters a
compound's. A record that states none carries its adapter's default, which
says so (``mona-unstated``) rather than assuming the library's terms.
"""

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel, Field

from mascope_reference.normalize import canonical_formula
from mascope_reference.schema import (
    ADDUCT_LENGTH,
    ENERGY_TEXT_LENGTH,
    INCHIKEY_LENGTH,
    LICENSE_LENGTH,
)
from mascope_tools.composition.mechanism_notation import (
    MechanismNotationError,
    standard_notation,
)


#: The kinds a collision energy is recorded in: normalized (a percentage of an
#: energy the instrument scales by m/z, Thermo's NCE) and absolute, in eV. A
#: source's volts are read as eV, which they are for a singly charged precursor.
NCE = "nce"
EV = "ev"

#: Why a record of a dump is not stored. Counted per load and reported.
NOT_MS2 = "not MS2"
PREDICTED = "predicted"
DEPRECATED = "deprecated"
LIBRARY_CLASS = "library class"
NO_PRECURSOR = "no precursor m/z"
NO_PEAKS = "no peaks"

#: A standard InChIKey: 14 letters, 10 letters, one letter.
_INCHIKEY = re.compile(r"[A-Z]{14}-[A-Z]{10}-[A-Z]")

#: A number, as the energy and precursor fields write one.
_NUMBER = r"\d+(?:\.\d+)?"


class SpectrumRecord(BaseModel):
    """One MS/MS spectrum as it appears in one source.

    Adapters fill the fields from the dump as it spells them;
    :func:`finalize_spectrum` makes them comparable across sources.
    """

    source: str = Field(description="Source name, e.g. 'massbank'. Set by the adapter.")
    source_native_id: str = Field(
        description="The source's id of the spectrum: an accession, a DB#, a SPECTRUMID."
    )
    name: str | None = Field(default=None, description="The compound's name.")
    formula: str | None = Field(
        default=None,
        description="The compound's neutral formula. Canonical Hill order after "
        "finalize_spectrum(), or None when it is not one Mascope can read.",
    )
    inchikey: str | None = Field(
        default=None, description="The compound's InChIKey, where the source gives one."
    )
    smiles: str | None = Field(default=None)
    inchi: str | None = Field(default=None)
    precursor_mz: float | None = Field(
        default=None, description="The m/z of the ion that was fragmented."
    )
    adduct: str | None = Field(
        default=None,
        description="The precursor ion, '[M+H]+'; Mascope's standard adduct "
        "notation where it can write it.",
    )
    polarity: str | None = Field(
        default=None, description="'positive' or 'negative'; None when unknown."
    )
    collision_energy: float | None = Field(
        default=None,
        description="The energy, where the source names one value and its unit.",
    )
    collision_energy_kind: str | None = Field(
        default=None, description="NCE or EV, set with collision_energy."
    )
    collision_energy_text: str | None = Field(
        default=None,
        description="The energy as the source wrote it: a ramp, steps, a bare number.",
    )
    peaks: list[tuple[float, float]] = Field(
        default_factory=list,
        description="The centroids as (m/z, intensity), ascending in m/z.",
    )
    license: str = Field(description="Per-record licence tag.")


@dataclass(frozen=True)
class SkippedSpectrum:
    """A record of a dump that is not stored, and why.

    :param reason: One of the reasons above, as a load reports it.
    """

    reason: str


def licence_tag(text: str | None) -> str | None:
    """The tag a stated licence is filtered by, or None when none is stated.

    The tag is the source's own words, spelled one way: blanks become hyphens
    and a Creative Commons name is upper case, so MassBank's ``CC BY-NC`` and a
    MoNA record's ``cc by-nc`` are both ``CC-BY-NC``. No version is added where
    the source names none: what a licence means is the source's to say, and a
    gate that wants both ``CC-BY`` and ``CC-BY-4.0`` names both.

    :param text: The licence as the record states it.
    :return: The tag, at most the column's width, or None.
    """
    words = " ".join((text or "").split())
    if not words:
        return None
    tag = words.replace(" ", "-")
    if tag[:2].upper() == "CC":
        tag = tag.upper()
    return tag[:LICENSE_LENGTH]


def polarity_of(text: str | None) -> str | None:
    """Read an ion mode, as any of the three sources writes it.

    :param text: ``POSITIVE``, ``Positive``, ``P``, ``negative``, ``N``, ...
    :return: ``positive``, ``negative``, or None for anything else.
    """
    value = (text or "").strip().lower()
    if value in ("positive", "pos", "p", "+"):
        return "positive"
    if value in ("negative", "neg", "n", "-"):
        return "negative"
    return None


def _adduct_charge_sign(adduct: str) -> str | None:
    """The sign a bracketed ion type's charge ends in, or None."""
    match = re.search(r"\]\d*([+-])[.*]?$", adduct)
    return match.group(1) if match else None


def adduct_notation(text: str | None) -> str | None:
    """Write a precursor ion type the way Mascope compares one.

    Mascope's standard adduct notation is used wherever it can say the ion (one
    molecule, one charge, terms all added or all removed), so a library's
    ``[M+H]+`` is the spelling a ledger row's mechanism has. Any other ion
    (``[2M+H]+``, ``[M+2H]2+``, ``[M+H-H2O]+``) is kept as written, blanks
    aside. A type without brackets is kept as written too: only its adapter
    knows the charge to bracket it with.

    :param text: The ion type as the source wrote it.
    :return: The ion type, or None when there is none.
    """
    value = "".join((text or "").split())
    if not value or value.upper() in ("N/A", "NA", "NONE", "UNKNOWN"):
        return None
    try:
        value = standard_notation(value)
    except MechanismNotationError:
        pass
    return value[:ADDUCT_LENGTH]


def collision_energy(text: str | None) -> tuple[float | None, str | None, str | None]:
    """Read a collision energy into a value, its kind and the source's words.

    A value is read only where the text names one energy and its unit: ``20
    eV``, ``30 V``, ``35 %``, ``10(NCE)``, ``NCE 35``, MassBank's ``40
    (nominal)``. A ramp (``Ramp 5-45 V``), steps (``20/35/50 NCE``) and a bare
    number (``35``, ``35 CID``) keep their words and no value, since a value of
    unknown kind could not be compared with a sample's.

    :param text: The energy as the source wrote it.
    :return: (value, kind, words); each None when absent.
    """
    words = " ".join((text or "").split())
    if not words or words.upper() in ("N/A", "NA", "NONE", "UNKNOWN"):
        return None, None, None
    words = words[:ENERGY_TEXT_LENGTH]
    lowered = words.lower()
    numbers = re.findall(_NUMBER, lowered)
    if len(numbers) != 1 or "ramp" in lowered:
        return None, None, words
    value = float(numbers[0])
    if "%" in lowered or re.search(r"(?<![a-z])nce(?![a-z])|nominal", lowered):
        return value, NCE, words
    # eV, or V not after another letter (kV is no collision energy).
    if re.search(r"(?<![a-z])e?v(?![a-z])", lowered):
        return value, EV, words
    return None, None, words


def precursor_mz(text: str | float | None) -> float | None:
    """Read a precursor m/z, or None when there is not exactly one.

    :param text: ``391.2474``, as MassBank, MoNA and GNPS write it; a list of
        several (``195.0877/197.0857``) is not one precursor.
    :return: The m/z, when it is a positive finite number.
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        value = float(text)
    else:
        words = text.strip()
        if not re.fullmatch(_NUMBER, words):
            return None
        value = float(words)
    return value if math.isfinite(value) and value > 0 else None


def inchikey(text: str | None) -> str | None:
    """Keep an InChIKey only when it is a standard one.

    The read path looks spectra up by this key, so a value that is not one -
    ``N/A``, a truncated key, an InChI in the wrong field - is dropped rather
    than stored for a lookup to miss.

    :param text: The field's value, possibly prefixed ``InChIKey=``.
    :return: The key, or None.
    """
    candidate = (text or "").strip()
    if candidate.upper().startswith("INCHIKEY="):
        candidate = candidate[len("INCHIKEY=") :].strip()
    if len(candidate) == INCHIKEY_LENGTH and _INCHIKEY.fullmatch(candidate):
        return candidate
    return None


def clean_peaks(peaks: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    """The centroids a scorer can use, in ascending m/z.

    A peak at a non-positive or non-finite m/z, or of no intensity, is dropped:
    MassBank's own exporter drops the m/z below 1 it once wrote, and GNPS lists
    zero-intensity peaks.

    :param peaks: (m/z, intensity) pairs.
    :return: The kept pairs, sorted by m/z.
    """
    kept = [
        (float(mz), float(intensity))
        for mz, intensity in peaks
        if math.isfinite(mz) and math.isfinite(intensity) and mz > 0 and intensity > 0
    ]
    return sorted(kept)


def finalize_spectrum(
    spectrum: SpectrumRecord,
) -> SpectrumRecord | SkippedSpectrum:
    """Make an adapter's spectrum comparable across sources, or say why not.

    :param spectrum: As the adapter read it.
    :return: The finalized spectrum, or the reason it is not stored.
    """
    mz = precursor_mz(spectrum.precursor_mz)
    if mz is None:
        return SkippedSpectrum(NO_PRECURSOR)
    peaks = clean_peaks(spectrum.peaks)
    if not peaks:
        return SkippedSpectrum(NO_PEAKS)
    adduct = adduct_notation(spectrum.adduct)
    polarity = spectrum.polarity
    if polarity is None and adduct is not None:
        # An ion type with a charge says the polarity a record left out.
        polarity = {"+": "positive", "-": "negative"}.get(
            _adduct_charge_sign(adduct) or ""
        )
    value, kind, words = collision_energy(spectrum.collision_energy_text)
    return spectrum.model_copy(
        update={
            "formula": canonical_formula(spectrum.formula or ""),
            "inchikey": inchikey(spectrum.inchikey),
            "adduct": adduct,
            "polarity": polarity,
            "collision_energy": value,
            "collision_energy_kind": kind,
            "collision_energy_text": words,
            "peaks": peaks,
        }
    )
