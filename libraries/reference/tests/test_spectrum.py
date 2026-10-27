"""The rules a library spectrum is normalized and admitted by."""

import pytest

from mascope_reference.spectrum import (
    EV,
    NCE,
    NO_PEAKS,
    NO_PRECURSOR,
    SkippedSpectrum,
    SpectrumRecord,
    adduct_notation,
    clean_peaks,
    collision_energy,
    finalize_spectrum,
    inchikey,
    licence_tag,
    polarity_of,
    precursor_mz,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("CC BY", "CC-BY"),
        ("CC BY-NC-SA", "CC-BY-NC-SA"),
        ("cc  by-sa 4.0", "CC-BY-SA-4.0"),
        ("CC0", "CC0"),
        # Not Creative Commons: kept in its own case.
        ("dl-de/by-2-0", "dl-de/by-2-0"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_licence_tag_is_the_source_words_spelled_one_way(text, expected):
    assert licence_tag(text) == expected


def test_licence_tag_fits_its_column():
    assert len(licence_tag("x" * 200)) == 64


@pytest.mark.parametrize(
    "text, expected",
    [
        ("POSITIVE", "positive"),
        ("Positive", "positive"),
        ("P", "positive"),
        ("negative", "negative"),
        ("N", "negative"),
        ("N/A", None),
        (None, None),
    ],
)
def test_polarity_of_reads_every_source_spelling(text, expected):
    assert polarity_of(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("[M+H]+", "[M+H]+"),
        (" [M-H]- ", "[M-H]-"),
        # Mascope's standard notation puts the terms in order.
        ("[M+H+CH4N2O]+", "[M+CH4N2O+H]+"),
        # Ions the standard notation cannot say are kept as written.
        ("[2M+Na]+", "[2M+Na]+"),
        ("[M+2H]2+", "[M+2H]2+"),
        ("[M+H-H2O]+", "[M+H-H2O]+"),
        ("[M]+*", "[M]+*"),
        # A bare type is its adapter's to bracket.
        ("M+H", "M+H"),
        ("N/A", None),
        ("", None),
        (None, None),
    ],
)
def test_adduct_notation(text, expected):
    assert adduct_notation(text) == expected


@pytest.mark.parametrize(
    "text, value, kind",
    [
        ("20 eV", 20.0, EV),
        ("20eV", 20.0, EV),
        ("30 V", 30.0, EV),
        ("35 %", 35.0, NCE),
        ("35%", 35.0, NCE),
        ("10(NCE)", 10.0, NCE),
        ("NCE 35", 35.0, NCE),
        ("HCD 35 NCE", 35.0, NCE),
        ("40 (nominal)", 40.0, NCE),
        ("10% (nominal)", 10.0, NCE),
        # One value, but of no unit a sample's energy could be compared with.
        ("35", None, None),
        ("35 CID", None, None),
        ("120 HCD", None, None),
        ("3 kV", None, None),
        # Not one value.
        ("Ramp 5-45 V", None, None),
        # A ramp that names one end only is still no value.
        ("Ramped collision energies of 40 V", None, None),
        ("Ramp 20%-70% (nominal)", None, None),
        ("20/35/50 NCE", None, None),
        ("15,30,45 (NCE)", None, None),
        ("20-40eV", None, None),
    ],
)
def test_collision_energy_value_and_kind(text, value, kind):
    assert collision_energy(text) == (value, kind, text)


@pytest.mark.parametrize("text", ["", "N/A", None])
def test_collision_energy_absent(text):
    assert collision_energy(text) == (None, None, None)


def test_collision_energy_words_are_kept_whitespace_folded():
    assert collision_energy("  Ramp   5-45 V ")[2] == "Ramp 5-45 V"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("391.2474", 391.2474),
        (" 391.2474 ", 391.2474),
        (391.2474, 391.2474),
        ("195.0877/197.0857", None),
        ("0.0", None),
        (0.0, None),
        ("-1", None),
        (float("nan"), None),
        ("N/A", None),
        (None, None),
    ],
)
def test_precursor_mz_is_one_positive_number(text, expected):
    assert precursor_mz(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("RYYVLZVUVIJVGH-UHFFFAOYSA-N", "RYYVLZVUVIJVGH-UHFFFAOYSA-N"),
        ("InChIKey=RYYVLZVUVIJVGH-UHFFFAOYSA-N", "RYYVLZVUVIJVGH-UHFFFAOYSA-N"),
        ("RYYVLZVUVIJVGH-UHFFFAOYSA", None),
        ("ryyvlzvuvijvgh-uhfffaoysa-n", None),
        ("InChI=1S/C8H10N4O2/c1-10", None),
        ("N/A", None),
        (None, None),
    ],
)
def test_inchikey_is_kept_only_when_it_is_one(text, expected):
    assert inchikey(text) == expected


def test_clean_peaks_sorts_and_drops_what_no_scorer_can_use():
    peaks = [(138.07, 999.0), (0.5, 3.0), (83.06, 0.0), (-1.0, 5.0), (110.07, 150.0)]
    assert clean_peaks(peaks) == [(0.5, 3.0), (110.07, 150.0), (138.07, 999.0)]
    assert clean_peaks([(float("inf"), 1.0), (50.0, float("nan"))]) == []


def _spectrum(**fields) -> SpectrumRecord:
    values = {
        "source": "test",
        "source_native_id": "1",
        "formula": "C8H10N4O2",
        "precursor_mz": 195.0877,
        "peaks": [(138.0662, 999.0), (110.0713, 150.0)],
        "license": "CC0",
    }
    values.update(fields)
    return SpectrumRecord(**values)


def test_finalize_canonicalizes_what_the_formats_share():
    spectrum = finalize_spectrum(
        _spectrum(
            formula="O2C8H10N4",
            inchikey="InChIKey=RYYVLZVUVIJVGH-UHFFFAOYSA-N",
            adduct="[M+H+CH4N2O]+",
            polarity="positive",
            collision_energy_text="35 %",
            peaks=[(138.0662, 999.0), (83.0604, 0.0), (110.0713, 150.0)],
        )
    )
    assert spectrum.formula == "C8H10N4O2"
    assert spectrum.inchikey == "RYYVLZVUVIJVGH-UHFFFAOYSA-N"
    assert spectrum.adduct == "[M+CH4N2O+H]+"
    assert (spectrum.collision_energy, spectrum.collision_energy_kind) == (35.0, NCE)
    assert spectrum.collision_energy_text == "35 %"
    assert spectrum.peaks == [(110.0713, 150.0), (138.0662, 999.0)]


@pytest.mark.parametrize(
    "text, words", [("  Ramp   5-45 V ", "Ramp 5-45 V"), ("N/A", None), (None, None)]
)
def test_finalize_stores_the_energy_words_as_read(text, words):
    spectrum = finalize_spectrum(_spectrum(collision_energy_text=text))
    assert spectrum.collision_energy_text == words


def test_finalize_keeps_a_spectrum_whose_formula_is_unreadable():
    # The formula is identity metadata; the spectrum is still a spectrum.
    spectrum = finalize_spectrum(_spectrum(formula="[C5H14NO]+"))
    assert isinstance(spectrum, SpectrumRecord)
    assert spectrum.formula is None


@pytest.mark.parametrize(
    "adduct, polarity",
    [("[M+H]+", "positive"), ("[M-H]-", "negative"), ("[M+2H]2+", "positive")],
)
def test_finalize_reads_a_missing_polarity_off_the_ion(adduct, polarity):
    assert finalize_spectrum(_spectrum(adduct=adduct)).polarity == polarity


def test_finalize_does_not_overrule_a_stated_polarity():
    spectrum = finalize_spectrum(_spectrum(adduct="[M-H]-", polarity="positive"))
    assert spectrum.polarity == "positive"


def test_finalize_leaves_polarity_unknown_without_an_ion_charge():
    assert finalize_spectrum(_spectrum(adduct="M+H")).polarity is None


@pytest.mark.parametrize("mz", [None, 0.0, -3.0])
def test_finalize_skips_a_spectrum_without_a_precursor(mz):
    assert finalize_spectrum(_spectrum(precursor_mz=mz)) == SkippedSpectrum(
        NO_PRECURSOR
    )


def test_finalize_skips_a_spectrum_without_a_usable_peak():
    assert finalize_spectrum(_spectrum(peaks=[(138.0, 0.0)])) == SkippedSpectrum(
        NO_PEAKS
    )
