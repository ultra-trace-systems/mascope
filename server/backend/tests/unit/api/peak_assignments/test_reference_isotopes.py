"""Unit tests for expanding reference formulas into Stage A isotope rows.

Exercises the real target-ion / IsoSpec path (no DB): a bounded set of reference
formulas becomes matchable isotope rows carrying reference identities and no
curated target linkage.
"""

from types import SimpleNamespace

from mascope_backend.api.new.peak_assignments.service import (
    _build_reference_isotopes_df,
)
from mascope_reference import KnownComposition, KnownIdentity


def _mechanism(mech_id="m-deprot", notation="-H-", polarity="-"):
    return SimpleNamespace(
        ionization_mechanism_id=mech_id,
        ionization_mechanism=notation,
        ionization_mechanism_polarity=polarity,
    )


def _known(formula, mass, names):
    return KnownComposition(
        formula=formula,
        monoisotopic_mass=mass,
        identities=[
            KnownIdentity(
                name=n,
                source="pinene-tracers",
                license="custom",
                inchikey=None,
                source_native_id=n,
                xrefs={},
            )
            for n in names
        ],
    )


def test_expands_formula_into_reference_isotope_rows():
    known = [_known("C10H16O3", 184.1099, ["Pinonic acid", "Norpinonic acid"])]
    df = _build_reference_isotopes_df(
        known, [_mechanism()], resolution_type="HIGH", abundance_threshold=0.0
    )
    assert not df.empty
    # Shaped like the target known-isotope frame, plus the carried identities.
    for col in (
        "target_isotope_id",
        "target_ion_id",
        "mz",
        "relative_abundance",
        "resolution",
        "target_ion_formula",
        "ionization_mechanism_id",
        "target_compound_formula",
        "ionization_mechanism",
        "reference_identities",
    ):
        assert col in df.columns
    # Every reference row: no curated target, right formula/mechanism/resolution.
    assert df["target_compound_id"].isna().all()
    assert (df["target_compound_formula"] == "C10H16O3").all()
    assert (df["ionization_mechanism"] == "-H-").all()
    assert (df["resolution"] == "HIGH").all()
    # The one-to-many identities ride on each row.
    identities = df["reference_identities"].iloc[0]
    assert {i["name"] for i in identities} == {"Pinonic acid", "Norpinonic acid"}
    # Several isotopologues (M0 + 13C etc.), abundances are valid fractions.
    assert len(df) >= 2
    assert df["relative_abundance"].between(0.0, 1.0).all()


def test_resolution_and_abundance_filters():
    known = [_known("C9H14O4", 186.0892, ["Pinic acid"])]
    high = _build_reference_isotopes_df(
        known, [_mechanism()], resolution_type="HIGH", abundance_threshold=0.0
    )
    low = _build_reference_isotopes_df(
        known, [_mechanism()], resolution_type="LOW", abundance_threshold=0.0
    )
    assert not high.empty and not low.empty
    assert (high["resolution"] == "HIGH").all()
    assert (low["resolution"] == "LOW").all()
    # An abundance floor at half the base peak drops the isotope tail.
    floor = 0.5 * float(high["relative_abundance"].max())
    floored = _build_reference_isotopes_df(
        known, [_mechanism()], resolution_type="HIGH", abundance_threshold=floor
    )
    assert not floored.empty
    assert len(floored) < len(high)
    assert (floored["relative_abundance"] >= floor).all()


def test_empty_when_no_formulas():
    df = _build_reference_isotopes_df(
        [], [_mechanism()], resolution_type="HIGH", abundance_threshold=0.0
    )
    assert df.empty
