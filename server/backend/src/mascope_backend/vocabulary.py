"""
PSI-MS controlled-vocabulary terms for what Mascope records of an acquisition.

The instrument model, ion source, mass analyser, detector and scan polarity,
each as the accession and name the PSI-MS ontology gives it
(https://github.com/HUPO-PSI/psi-ms-CV), so that an export says what a
sample was measured on in terms another tool can read, and not only in
Mascope's own words: an instrument's name is what its site calls it, its
class is ``"orbi"`` or ``"tof"``, a polarity is ``"+"`` or ``"-"``.

This module is the one place the terms are written down. The batch export
and the mzML export's ``instrumentConfiguration`` read them from here; an
``Instrument`` row is meant to take its term from here as well. Nothing
here touches the database or a file: it maps values already recorded to
terms, and a value it cannot map gives None, never a nearby term.

The accessions were checked against PSI-MS release :data:`PSI_MS_VERSION`.
OpenTFRaw's mzML writer embeds a model table of its own, and not all of it
agrees with the ontology: it writes ``MS:1003199`` for the Orbitrap
Exploris 120, an accession the ontology gives to "change log", and several
other accessions in its table are not instrument models either. The
ontology is what a reader of an export resolves an accession against, so
it is what this table follows.
"""

from typing import NamedTuple


#: The PSI-MS release every accession below was checked against.
PSI_MS_VERSION = "4.2.6"


class CvTerm(NamedTuple):
    """One term of the PSI-MS ontology: its accession and its name."""

    accession: str
    name: str


#: Every instrument model the ontology files under ``orbitrap instrument``
#: (``MS:1003953``: the Exactive and Exactive Plus) or under one of the four
#: classes beside it (ion trap, quadrupole, tribrid and Astral Orbitraps,
#: ``MS:1003768`` to ``MS:1003771``), by its PSI-MS name. Only Orbitraps: an
#: ``"orbi"`` file is analysed by an orbitrap, so a model listed here never
#: contradicts :data:`ANALYSERS`.
ORBITRAP_MODELS: tuple[CvTerm, ...] = (
    CvTerm("MS:1000449", "LTQ Orbitrap"),
    CvTerm("MS:1000555", "LTQ Orbitrap Discovery"),
    CvTerm("MS:1000556", "LTQ Orbitrap XL"),
    CvTerm("MS:1000639", "LTQ Orbitrap XL ETD"),
    CvTerm("MS:1000643", "MALDI LTQ Orbitrap"),
    CvTerm("MS:1000649", "Exactive"),
    CvTerm("MS:1001742", "LTQ Orbitrap Velos"),
    CvTerm("MS:1001910", "Orbitrap Elite"),
    CvTerm("MS:1001911", "Q Exactive"),
    CvTerm("MS:1002416", "Orbitrap Fusion"),
    CvTerm("MS:1002417", "Orbitrap Fusion ETD"),
    CvTerm("MS:1002523", "Q Exactive HF"),
    CvTerm("MS:1002526", "Exactive Plus"),
    CvTerm("MS:1002634", "Q Exactive Plus"),
    CvTerm("MS:1002732", "Orbitrap Fusion Lumos"),
    CvTerm("MS:1002835", "LTQ Orbitrap Classic"),
    CvTerm("MS:1002877", "Q Exactive HF-X"),
    CvTerm("MS:1002992", "Orbitrap Exploris GC-MS"),
    CvTerm("MS:1002993", "Q Exactive Focus"),
    CvTerm("MS:1002994", "Orbitrap Excedion Pro"),
    CvTerm("MS:1003028", "Orbitrap Exploris 480"),
    CvTerm("MS:1003029", "Orbitrap Eclipse"),
    CvTerm("MS:1003094", "Orbitrap Exploris 240"),
    CvTerm("MS:1003095", "Orbitrap Exploris 120"),
    CvTerm("MS:1003096", "Orbitrap Velos Pro"),
    CvTerm("MS:1003112", "Orbitrap ID-X"),
    CvTerm("MS:1003245", "Q Exactive UHMR"),
    CvTerm("MS:1003356", "Orbitrap Ascend"),
    CvTerm("MS:1003378", "Orbitrap Astral"),
    CvTerm("MS:1003395", "Q Exactive GC Orbitrap"),
    CvTerm("MS:1003411", "Orbitrap IQ-X"),
    CvTerm("MS:1003423", "Orbitrap Exploris GC 240"),
    CvTerm("MS:1003442", "Orbitrap Astral Zoom"),
    CvTerm("MS:1003496", "MALDI LTQ Orbitrap XL"),
    CvTerm("MS:1003497", "MALDI LTQ Orbitrap Discovery"),
    CvTerm("MS:1003499", "LTQ Orbitrap Velos/ETD"),
    CvTerm("MS:1003987", "Orbitrap Tribrid Apex"),
    CvTerm("MS:1003990", "Orbitrap Excedion"),
)

#: The ontology's parent of every Thermo model: what an mzML file of a
#: Thermo raw file says the instrument is when :func:`instrument_model_term`
#: does not know its model, since mzML asks for a model term and this one is
#: true of any Thermo instrument. Not a model of :data:`ORBITRAP_MODELS`: a
#: column or a field that wants the model stays empty instead.
THERMO_INSTRUMENT_MODEL = CvTerm(
    "MS:1000483", "Thermo Fisher Scientific instrument model"
)

#: Names the Thermo reader reports for a model that are not its PSI-MS name,
#: with the accession they stand for. The first two are the ontology's own
#: exact synonyms; the rest are OpenTFRaw's spellings of a listed model. A
#: family name alone ("Orbitrap Exploris") names no model and is not here.
MODEL_ALIASES: dict[str, str] = {
    "LTQ Orbitrap Elite": "MS:1001910",
    "LTQ Orbitrap Velos Pro": "MS:1003096",
    "LTQ Orbitrap Velos ETD": "MS:1003499",
    "Q Exactive GC": "MS:1003395",
    "Orbitrap Velos": "MS:1001742",
    "Orbitrap Discovery": "MS:1000555",
    "Orbitrap XL": "MS:1000556",
}

#: Ion source by instrument class. Every instrument Mascope reads ionizes by
#: chemical ionization - a reagent ion reacting with the neutral, which is
#: what an ionization mode's mechanisms describe - at atmospheric or reduced
#: pressure depending on the inlet, which nothing recorded says. So the
#: general term, which is true of both.
SOURCES: dict[str, CvTerm] = {
    "orbi": CvTerm("MS:1000071", "chemical ionization"),
    "tof": CvTerm("MS:1000071", "chemical ionization"),
}

#: Mass analyser by instrument class.
ANALYSERS: dict[str, CvTerm] = {
    "orbi": CvTerm("MS:1000484", "orbitrap"),
    "tof": CvTerm("MS:1000084", "time-of-flight"),
}

#: Detector by instrument class: an Orbitrap detects the image current of the
#: ions it traps, a TOF counts them on a microchannel plate.
DETECTORS: dict[str, CvTerm] = {
    "orbi": CvTerm("MS:1000624", "inductive detector"),
    "tof": CvTerm("MS:1000114", "microchannel plate detector"),
}

#: Scan polarity by Mascope's polarity sign.
POLARITIES: dict[str, CvTerm] = {
    "+": CvTerm("MS:1000130", "positive scan"),
    "-": CvTerm("MS:1000129", "negative scan"),
}


def _model_key(model: str) -> str:
    """A model name as it is looked up: case and spacing do not count."""
    return " ".join(model.split()).casefold()


_BY_ACCESSION = {term.accession: term for term in ORBITRAP_MODELS}
_MODELS_BY_NAME = {_model_key(term.name): term for term in ORBITRAP_MODELS} | {
    _model_key(alias): _BY_ACCESSION[accession]
    for alias, accession in MODEL_ALIASES.items()
}


def instrument_model_term(model: str | None) -> CvTerm | None:
    """
    The PSI-MS term of an instrument model, by the name the reader reports.

    :param model: The model as the raw file names it (the Thermo reader's
        ``instrument_details()["Model"]``), such as "Orbitrap Exploris 120".
    :return: Its term, or None for a model the table does not know: a TOF
        (the ontology has no Tofwerk models), a family name, an empty name.
    """
    if not model:
        return None
    return _MODELS_BY_NAME.get(_model_key(model))


def source_term(instrument_type: str | None) -> CvTerm | None:
    """
    The ion source term of an instrument class.

    :param instrument_type: ``"orbi"`` or ``"tof"``, as ``sample_file`` records it.
    :return: Its term, or None for a class this module does not know.
    """
    return SOURCES.get(instrument_type or "")


def analyser_term(instrument_type: str | None) -> CvTerm | None:
    """
    The mass analyser term of an instrument class.

    :param instrument_type: ``"orbi"`` or ``"tof"``, as ``sample_file`` records it.
    :return: Its term, or None for a class this module does not know.
    """
    return ANALYSERS.get(instrument_type or "")


def detector_term(instrument_type: str | None) -> CvTerm | None:
    """
    The detector term of an instrument class.

    :param instrument_type: ``"orbi"`` or ``"tof"``, as ``sample_file`` records it.
    :return: Its term, or None for a class this module does not know.
    """
    return DETECTORS.get(instrument_type or "")


def polarity_terms(polarity: str | None) -> tuple[CvTerm, ...]:
    """
    The scan polarity terms of a polarity.

    :param polarity: ``"+"``, ``"-"``, or ``"+-"`` for both, as a sample, a
        file or a batch records it.
    :return: One term per polarity, positive first; empty when the value is
        none of those.
    """
    if polarity not in ("+", "-", "+-"):
        return ()
    return tuple(POLARITIES[sign] for sign in polarity)
