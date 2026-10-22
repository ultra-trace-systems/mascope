"""
Tests: what Mascope records of an acquisition maps to PSI-MS terms.

The accessions are the PSI-MS ontology's (``mascope_backend.vocabulary``,
release ``PSI_MS_VERSION``); a value with no term maps to nothing rather
than to a nearby term.
"""

import re

import pytest

from mascope_backend import vocabulary
from mascope_backend.vocabulary import CvTerm


ACCESSION = re.compile(r"MS:\d{7}")


@pytest.mark.parametrize(
    ("model", "accession"),
    [
        ("Orbitrap Exploris 120", "MS:1003095"),
        ("Orbitrap Exploris 240", "MS:1003094"),
        ("Orbitrap Exploris 480", "MS:1003028"),
        ("Q Exactive", "MS:1001911"),
        ("Q Exactive HF-X", "MS:1002877"),
        ("Orbitrap Astral", "MS:1003378"),
        ("LTQ Orbitrap", "MS:1000449"),
        # Filed directly under orbitrap instrument, not under one of its
        # four sibling classes
        ("Exactive", "MS:1000649"),
        ("Exactive Plus", "MS:1002526"),
    ],
)
def test_a_model_maps_to_its_term_by_its_psi_ms_name(model, accession):
    assert vocabulary.instrument_model_term(model) == CvTerm(accession, model)


def test_the_exploris_120_is_not_given_opentfraws_accession():
    """OpenTFRaw's mzML writer writes MS:1003199 for this model, which the
    ontology names "change log"; the export must not repeat it."""
    term = vocabulary.instrument_model_term("Orbitrap Exploris 120")
    assert term.accession != "MS:1003199"


@pytest.mark.parametrize(
    "model",
    ["orbitrap exploris 120", "  Orbitrap   Exploris 120 ", "ORBITRAP EXPLORIS 120"],
)
def test_a_model_is_looked_up_regardless_of_case_and_spacing(model):
    assert vocabulary.instrument_model_term(model).accession == "MS:1003095"


#: Every name in ``MODEL_ALIASES``, written out with the PSI-MS name it must
#: give, so that dropping or misdirecting any alias fails a test.
ALIASES = {
    "LTQ Orbitrap Elite": "Orbitrap Elite",
    "LTQ Orbitrap Velos Pro": "Orbitrap Velos Pro",
    "LTQ Orbitrap Velos ETD": "LTQ Orbitrap Velos/ETD",
    "Q Exactive GC": "Q Exactive GC Orbitrap",
    "Orbitrap Velos": "LTQ Orbitrap Velos",
    "Orbitrap Discovery": "LTQ Orbitrap Discovery",
    "Orbitrap XL": "LTQ Orbitrap XL",
}


@pytest.mark.parametrize(("alias", "name"), ALIASES.items())
def test_a_readers_other_name_for_a_model_maps_to_the_models_term(alias, name):
    """The term keeps the ontology's name, whatever the reader called it."""
    term = vocabulary.instrument_model_term(alias)
    assert term.name == name
    assert term.accession == vocabulary.MODEL_ALIASES[alias]


def test_every_alias_is_tested():
    assert set(ALIASES) == set(vocabulary.MODEL_ALIASES)


@pytest.mark.parametrize(
    "model",
    [
        None,
        "",
        "Orbitrap Exploris",  # a family, not a model
        "Orbitrap Exploris 12",  # no prefix matching
        "LTQ",  # a Thermo model, but not an Orbitrap
        "instrument-x",  # a site's own name for an instrument
    ],
)
def test_a_model_the_table_does_not_know_maps_to_nothing(model):
    assert vocabulary.instrument_model_term(model) is None


def test_the_model_table_is_consistent():
    accessions = [term.accession for term in vocabulary.ORBITRAP_MODELS]
    names = [term.name.casefold() for term in vocabulary.ORBITRAP_MODELS]
    assert len(set(accessions)) == len(accessions)
    assert len(set(names)) == len(names)
    assert all(ACCESSION.fullmatch(accession) for accession in accessions)
    # Every alias stands for a listed model, and shadows no listed name
    assert set(vocabulary.MODEL_ALIASES.values()) <= set(accessions)
    assert not {alias.casefold() for alias in vocabulary.MODEL_ALIASES} & set(names)


@pytest.mark.parametrize(
    ("instrument_type", "source", "analyser", "detector"),
    [
        (
            "orbi",
            CvTerm("MS:1000071", "chemical ionization"),
            CvTerm("MS:1000484", "orbitrap"),
            CvTerm("MS:1000624", "inductive detector"),
        ),
        (
            "tof",
            CvTerm("MS:1000071", "chemical ionization"),
            CvTerm("MS:1000084", "time-of-flight"),
            CvTerm("MS:1000114", "microchannel plate detector"),
        ),
    ],
)
def test_an_instrument_class_maps_to_its_source_analyser_and_detector(
    instrument_type, source, analyser, detector
):
    assert vocabulary.source_term(instrument_type) == source
    assert vocabulary.analyser_term(instrument_type) == analyser
    assert vocabulary.detector_term(instrument_type) == detector


@pytest.mark.parametrize("instrument_type", [None, "", "quad"])
def test_an_unknown_instrument_class_maps_to_nothing(instrument_type):
    assert vocabulary.source_term(instrument_type) is None
    assert vocabulary.analyser_term(instrument_type) is None
    assert vocabulary.detector_term(instrument_type) is None


@pytest.mark.parametrize(
    ("polarity", "accessions"),
    [
        ("+", ("MS:1000130",)),
        ("-", ("MS:1000129",)),
        ("+-", ("MS:1000130", "MS:1000129")),
        (None, ()),
        ("", ()),
        ("neg", ()),
        ("++", ()),
    ],
)
def test_a_polarity_maps_to_one_term_per_sign(polarity, accessions):
    terms = vocabulary.polarity_terms(polarity)
    assert tuple(term.accession for term in terms) == accessions


def test_the_polarity_terms_carry_the_ontologys_names():
    assert vocabulary.polarity_terms("+-") == (
        CvTerm("MS:1000130", "positive scan"),
        CvTerm("MS:1000129", "negative scan"),
    )
