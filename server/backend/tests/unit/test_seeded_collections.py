"""
Tests: the calibrant and diagnostic collections Mascope seeds per chemistry.

A seeded chemistry is usable on the day a server is installed only if it
carries the anchors its files are calibrated on, so they ship with it, read
off the reagent-ion library. What these pin is what that reading gives: which
chemistries carry collections, which ions each holds, that every compound
really becomes its ion under its mode's mechanisms - checked through the
target ion generator a server builds them with, not restated - and that what
a collection holds has a digest the seed can tell an edit by.
"""

import pytest

from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    generate_target_ions_from_composition,
)
from mascope_backend.api.models.target.collections.config import (
    target_collection_config,
)
from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    TargetCompoundBase,
)
from mascope_backend.db import IonizationMechanism, IonizationMode, TargetCollection
from mascope_backend.ionization_catalogue import (
    NAMED_BEFORE,
    SEEDED_BEFORE,
    SYSTEM_MODES,
    _background_compounds,
    _neutral,
    collection_digest,
    seeded_collection_ids,
    seeded_collections,
)
from mascope_reference.seed import catalogue
from mascope_tools.composition.mechanism_notation import parse_mechanism
from mascope_tools.composition.profiles import detect_reagent_profile
from mascope_tools.composition.reagents import (
    BACKGROUND_ANCHOR_LISTS,
    background_anchor_lists,
    ion_mz,
    reagent_library,
)
from mascope_tools.composition.utils import parse_composition, to_hill_notation


_MODES = {system_key: entry for system_key, *entry in SYSTEM_MODES}

#: Each chemistry's calibrants, as (the ion, the neutral its mode makes it of).
#: Written out, so that a change to the library reads as a change to the
#: anchors every server calibrates on rather than as a count moving.
CALIBRANTS = {
    "nitrate": [
        ("[NO3]-", "()"),
        ("[NO3+HNO3]-", "HNO3"),
        ("[NO3+2xHNO3]-", "H2N2O6"),
    ],
    "nitrate-15n": [
        ("[^NO3]-", "()"),
        ("[^NO3+H^NO3]-", "HO3^N"),
        ("[^NO3+2xH^NO3]-", "H2O6^N2"),
    ],
    "bromide": [("[Br]-", "()"), ("[Br2]-", "Br"), ("[Br3]-", "Br2")],
    "iodide": [("[I]-", "()"), ("[I2]-", "I"), ("[I3]-", "I2")],
    "uronium": [
        ("[CH4N2O+H]+", "()"),
        ("[(CH4N2O)2+H]+", "CH4N2O"),
        ("[(CH4N2O)3+H]+", "C2H8N4O2"),
    ],
    # The charge-transfer (EASY-IC) lock mass, an Orbitrap's one anchor.
    "ambient-negative": [("[C16H10]-", "C16H10")],
    "ambient-positive": [("[C16H10]+", "C16H10")],
}

#: The background tier each chemistry's calibrants carry behind its reagent's
#: series, as (the compound, its neutral), in the order of its reference
#: lists: the cyclic siloxanes, then caprolactam (decision MAS-811).
BACKGROUND = {
    "uronium": [
        ("Hexamethylcyclotrisiloxane (D3)", "C6H18O3Si3"),
        ("Octamethylcyclotetrasiloxane (D4)", "C8H24O4Si4"),
        ("Decamethylcyclopentasiloxane (D5)", "C10H30O5Si5"),
        ("Dodecamethylcyclohexasiloxane (D6)", "C12H36O6Si6"),
        ("Tetradecamethylcycloheptasiloxane (D7)", "C14H42O7Si7"),
        ("Hexadecamethylcyclooctasiloxane (D8)", "C16H48O8Si8"),
        ("Caprolactam", "C6H11NO"),
    ],
}

#: The lines a chemistry's reagent compounds give through a channel its mode
#: declares beside the reagent's own, which the library does not name. Under
#: uronium's ammonium adduct the series' rungs become NH4+, below every scan
#: window; [urea+NH4]+, the ion through which ambient ammonia is read, which
#: every uronium spectrum holds; and the dimer's ammonium cluster, which the
#: test spectra do not show. A line no peak holds is left out of a fit.
THROUGH_DECLARED_CHANNELS = {"uronium": {"H4N+", "CH8N3O+", "C2H12N5O2+"}}

#: The chemistries with no reagent of their own in the library, which ship
#: without collections until a further tier gives them anchors.
WITHOUT_A_REAGENT = {"deprotonation", "protonation", "ammonium", "ammonium-15n"}

#: The digest of what each collection seeds. A change to what one seeds - its
#: name, its description, or its compounds, which follow the reagent-ion
#: library - fails here: add the digest it replaces to SEEDED_BEFORE in
#: ``ionization_catalogue``, so that a server holding it brings the collection
#: up to date instead of reading it as the deployment's edit, then pin the new
#: one here.
SEEDED_DIGESTS = {
    "sysNitrateCal": "ee3445148ceefc0e",
    "sysNitrateDia": "b84802ff6f6582b4",
    "sysNitrate15NCal": "b6f293cb9961af9e",
    "sysNitrate15NDia": "d9eea58be142d003",
    "sysBromideCal": "e0568fd7e79243cd",
    "sysBromideDia": "4a139370a962997a",
    "sysIodideCal": "f4825b329b2990e3",
    "sysIodideDia": "e808f225d923c917",
    "sysAmbientNegCal": "6baa13c90da5dd82",
    "sysAmbientNegDia": "7542acc9f1fca4af",
    "sysUroniumCal": "12948e42e87394ca",
    "sysUroniumDia": "ec1418fcd33dc2ef",
    "sysAmbientPosCal": "c0da51cbf39db2e2",
    "sysAmbientPosDia": "acd8c07034a3ab89",
}

#: The one ion a seeded compound gives beside the library's: ``()`` read
#: through a proton transfer is the bare proton, at m/z 1, where no
#: acquisition reaches.
_PROTON = "H+"


def _by_key(mode_field: str) -> dict:
    return {
        collection.system_key: collection
        for collection in seeded_collections()
        if collection.mode_field == mode_field
    }


def _library(system_key: str):
    _, _, polarity, mechanisms = _MODES[system_key]
    profile = detect_reagent_profile(mechanisms, polarity, instrument_type="orbi")
    return reagent_library(profile.name)


def _mz(ion: str) -> float:
    """The m/z of a singly charged ion as the target ion generator writes it."""
    return ion_mz(ion[:-1], 1 if ion.endswith("+") else -1)


def _ion_formula(formula: str, charge: int) -> str:
    """An ion as the target ion generator writes it: Hill order, then the sign."""
    return to_hill_notation(dict(parse_composition(formula))) + (
        "+" if charge > 0 else "-"
    )


def _ions_through_mode(system_key: str, formula: str) -> set[str]:
    """What a compound becomes under its mode's mechanisms, as a server builds it."""
    _, _, _, mechanisms = _MODES[system_key]
    ions, _ = generate_target_ions_from_composition(
        TargetCompoundBase(target_compound_id="x", target_compound_formula=formula),
        [
            IonizationMechanism(
                ionization_mechanism_id=f"m{i}",
                ionization_mechanism=notation,
                ionization_mechanism_polarity=parse_mechanism(notation).polarity,
            )
            for i, notation in enumerate(mechanisms)
        ],
    )
    return {ion.target_ion_formula for ion in ions}


def _reagent_compounds(collection):
    """The compounds a collection holds for the reagent's own ions."""
    background = {name for name, _ in BACKGROUND.get(collection.system_key, [])}
    return [
        compound
        for compound in collection.compounds
        if collection.collection_type != "CALIBRANTS" or compound.name not in background
    ]


def _every_compound():
    return [
        pytest.param(
            collection, compound, id=f"{collection.collection_id}:{compound.name}"
        )
        for collection in seeded_collections()
        for compound in _reagent_compounds(collection)
    ]


def test_the_chemistries_with_a_reagent_carry_both_collections():
    for mode_field in ("calibration_collection_id", "diagnostic_collection_id"):
        assert set(_by_key(mode_field)) == set(CALIBRANTS)
    assert set(CALIBRANTS) | WITHOUT_A_REAGENT == set(_MODES)


@pytest.mark.parametrize("system_key", sorted(WITHOUT_A_REAGENT))
def test_a_chemistry_without_a_reagent_has_nothing_to_seed(system_key):
    assert not [ion for ion in _library(system_key)]


@pytest.mark.parametrize("system_key", sorted(CALIBRANTS))
def test_the_calibrants_are_the_reagents_own_cluster_series(system_key):
    """Then the background tier, where the chemistry has one."""
    collection = _by_key("calibration_collection_id")[system_key]

    assert [
        (compound.name, compound.formula) for compound in collection.compounds
    ] == CALIBRANTS[system_key] + BACKGROUND.get(system_key, [])
    assert collection.collection_type == "CALIBRANTS"


def test_the_background_tier_is_read_off_its_reference_lists():
    """As each list names its compounds, so a list's edit reaches the tier."""
    lists = {peak_list.id: peak_list for peak_list in catalogue()}
    for system_key, compounds in BACKGROUND.items():
        _, _, polarity, mechanisms = _MODES[system_key]
        profile = detect_reagent_profile(mechanisms, polarity, instrument_type="orbi")
        named = background_anchor_lists(profile.name)

        assert named
        assert compounds == [
            (species.name, species.formula)
            for list_id in named
            for species in lists[list_id].species
        ]
    assert set(BACKGROUND_ANCHOR_LISTS) == {
        detect_reagent_profile(_MODES[key][3], _MODES[key][2], "orbi").name
        for key in BACKGROUND
    }


@pytest.mark.parametrize(
    ("list_ids", "polarity", "message"),
    [
        (("no-such-list",), "+", "No shipped reference list"),
        (("cyclic-siloxanes",), "-", "would calibrate a mode of polarity -"),
    ],
    ids=["unknown", "other-polarity"],
)
def test_a_background_list_that_cannot_anchor_the_mode_is_refused(
    list_ids, polarity, message
):
    with pytest.raises(ValueError, match=message):
        _background_compounds(list_ids, polarity)


@pytest.mark.parametrize(
    "compound",
    [compound for compounds in BACKGROUND.values() for compound in compounds],
    ids=lambda compound: compound[0],
)
def test_a_background_compound_is_fitted_on_through_every_mechanism(compound):
    """Protonated and as its urea adduct, the two channels the demo's files
    hold each anchor in, and as its ammonium adduct, which the mode declares
    too and the fit takes where a file holds it."""
    name, formula = compound
    neutral = dict(parse_composition(formula))

    def ion(**added):
        counts = dict(neutral)
        for element, count in added.items():
            counts[element] = counts.get(element, 0) + count
        return to_hill_notation(counts) + "+"

    assert _ions_through_mode("uronium", formula) == {
        ion(H=1),
        ion(C=1, H=5, N=2, O=1),
        ion(H=4, N=1),
    }


def test_the_uronium_calibrants_reach_a_window_above_the_urea_series():
    """A scan window from m/z 122 to 740 holds no rung of the urea series
    but the trimer, which the demo's files of that window hold at noise: the
    calibrants have ions there that are not the series' (GitHub #2689)."""
    collection = _by_key("calibration_collection_id")["uronium"]
    series = {
        ion
        for compound in _reagent_compounds(collection)
        for ion in _ions_through_mode("uronium", compound.formula)
    }
    above = {
        ion
        for compound in collection.compounds
        for ion in _ions_through_mode("uronium", compound.formula)
        if ion not in series and 122 <= _mz(ion) <= 740
    }

    # Caprolactam's urea adduct and D3 to D6, protonated and urea-clustered:
    # the ions in every one of the demo's 81 files of that window.
    assert {
        "C7H16N3O2+",
        "C6H19O3Si3+",
        "C8H25O4Si4+",
        "C10H31O5Si5+",
        "C12H37O6Si6+",
        "C7H23N2O4Si3+",
        "C9H29N2O5Si4+",
        "C11H35N2O6Si5+",
        "C13H41N2O7Si6+",
    } <= above
    assert round(_mz("C7H16N3O2+"), 4) == 174.1237
    assert round(_mz("C6H19O3Si3+"), 4) == 223.0637


@pytest.mark.parametrize(
    "system_key", sorted(set(CALIBRANTS) - {"ambient-negative", "ambient-positive"})
)
def test_a_reagent_gives_a_tof_the_three_anchors_its_fit_needs(system_key):
    """The floor, not sufficiency: a TOF fit needs three points to run, and
    the series climbs by the reagent's own mass each rung. Every series is
    exactly three, at the low end of the range, so a TOF fit on one has no
    point to spare (GitHub #2689)."""
    labels = {
        compound.name
        for compound in _by_key("calibration_collection_id")[system_key].compounds
    }
    anchors = sorted(
        ion_mz(ion.formula, ion.charge)
        for ion in _library(system_key)
        if ion.label in labels
    )

    assert len(anchors) >= 3
    assert anchors[-1] - anchors[0] >= 100.0


@pytest.mark.parametrize("system_key", sorted(CALIBRANTS))
def test_the_diagnostics_hold_every_line_of_the_reagent(system_key):
    """The series and the rest of what the source makes of its reagent alone,
    and none of the air's ions, which are the discharge's."""
    collection = _by_key("diagnostic_collection_id")[system_key]
    library = {ion.label: ion for ion in _library(system_key)}
    names = [compound.name for compound in collection.compounds]

    assert collection.collection_type == "DIAGNOSTICS"
    assert {name for name, _ in CALIBRANTS[system_key]} <= set(names)
    assert {library[name].family for name in names} <= {"reagent", "calibrant"}
    assert [ion.label for ion in library.values() if ion.family != "air"] == names
    assert len(names) <= target_collection_config.DIAGNOSTICS_MAX_COMPOUNDS


@pytest.mark.parametrize(("collection", "compound"), _every_compound())
def test_every_compound_becomes_its_ion_under_its_mode(collection, compound):
    ion = next(
        ion for ion in _library(collection.system_key) if ion.label == compound.name
    )

    assert _ion_formula(ion.formula, ion.charge) in _ions_through_mode(
        collection.system_key, compound.formula
    )


@pytest.mark.parametrize(
    "collection",
    [c for c in seeded_collections() if c.collection_type == "CALIBRANTS"],
    ids=lambda collection: collection.collection_id,
)
def test_a_calibrant_gives_no_line_but_its_collections_anchors(collection):
    """Whatever else a compound becomes under the mode is fitted on too, so a
    reagent calibrant may hold no ion it does not mean but the ones a channel
    the mode declares beside the reagent's makes of it, named here."""
    library = {ion.label: ion for ion in _library(collection.system_key)}
    reagent = _reagent_compounds(collection)
    anchors = {
        _ion_formula(library[c.name].formula, library[c.name].charge) for c in reagent
    }
    declared = THROUGH_DECLARED_CHANNELS.get(collection.system_key, set())

    given = set()
    for compound in reagent:
        given |= _ions_through_mode(collection.system_key, compound.formula)
    assert given <= anchors | {_PROTON} | declared
    assert declared <= given


@pytest.mark.parametrize(
    "collection",
    [c for c in seeded_collections() if c.collection_type == "DIAGNOSTICS"],
    ids=lambda collection: collection.collection_id,
)
def test_a_diagnostic_gives_no_line_its_source_does_not_make(collection):
    """A hydrate's water also loses a proton under a deprotonating mode, and
    that hydroxide is the discharge's own ion, which the library names."""
    lines = {
        _ion_formula(ion.formula, ion.charge) for ion in _library(collection.system_key)
    }
    declared = THROUGH_DECLARED_CHANNELS.get(collection.system_key, set())

    for compound in collection.compounds:
        assert _ions_through_mode(collection.system_key, compound.formula) <= (
            lines | {_PROTON} | declared
        )


def test_the_ids_are_the_modes_own_fit_the_column_and_are_unique():
    column = TargetCollection.__table__.c.target_collection_id.type.length
    ids = [collection.collection_id for collection in seeded_collections()]

    assert len(ids) == len(set(ids)) == len(seeded_collection_ids())
    for collection in seeded_collections():
        mode_id = _MODES[collection.system_key][0]
        suffix = {"calibration_collection_id": "Cal", "diagnostic_collection_id": "Dia"}
        assert collection.collection_id == mode_id + suffix[collection.mode_field]
        assert len(collection.collection_id) <= column
        assert hasattr(IonizationMode, collection.mode_field)


def test_the_charge_transfer_modes_are_named_as_their_profile_is():
    """Read as an Orbitrap's, electron transfer is the EASY-IC source, and the
    mode says so: "ambient" is an APi-TOF's own ions with no reagent, which
    the collections of these modes would not fit."""
    for system_key in ("ambient-negative", "ambient-positive"):
        _, name, polarity, mechanisms = _MODES[system_key]
        profile = detect_reagent_profile(mechanisms, polarity, instrument_type="orbi")

        assert profile.name.startswith("EASYIC")
        assert name == profile.label


def test_a_name_seeded_before_is_no_chemistrys_name_now():
    """A current name in NAMED_BEFORE would rename one chemistry's mode to
    another's, or read the current name as the one to replace."""
    names = {name for _, name, *_ in _MODES.values()}

    assert set(NAMED_BEFORE) <= set(_MODES)
    for earlier in NAMED_BEFORE.values():
        assert not earlier & names


def test_the_names_say_whose_they_are():
    for collection in seeded_collections():
        mode_name = _MODES[collection.system_key][1]
        role = (
            "calibrants"
            if collection.mode_field.startswith("calibration")
            else ("reagent ions")
        )
        assert collection.name == f"{mode_name} {role}"
        assert collection.description.startswith("Shipped by Mascope")


def test_the_digests():
    """See SEEDED_DIGESTS for what to do when this fails."""
    assert {
        collection.collection_id: collection.digest
        for collection in seeded_collections()
    } == SEEDED_DIGESTS


def test_no_digest_seeded_before_is_one_seeded_now():
    """A digest in both would read an edit back to an older release as the
    seed's own, and bring it up to date over the deployment's edit."""
    assert not SEEDED_BEFORE & set(SEEDED_DIGESTS.values())


_BASE = dict(
    name="Bromide, negative calibrants",
    description="Shipped by Mascope",
    collection_type="CALIBRANTS",
    workspace_id=None,
    compounds=[("[Br]-", "()"), ("[Br2]-", "Br")],
)


@pytest.mark.parametrize(
    "edit",
    [
        {"name": "Our bromide calibrants"},
        {"description": "Ours now"},
        {"collection_type": "TARGETS"},
        {"workspace_id": "ws1"},
        {"compounds": [("[Br]-", "()")]},
        {
            "compounds": [
                ("[Br]-", "()"),
                ("[Br2]-", "Br"),
                ("Pinonic acid", "C10H16O3"),
            ]
        },
        {"compounds": [("[Br]-", "()"), ("[Br2]-", "Br2")]},
        {"compounds": [("[Br]-", "()"), ("Bromine", "Br")]},
    ],
    ids=[
        "renamed",
        "described",
        "retyped",
        "scoped",
        "compound-removed",
        "compound-added",
        "formula-edited",
        "compound-renamed",
    ],
)
def test_every_edit_a_person_can_make_changes_the_digest(edit):
    assert collection_digest(**{**_BASE, **edit}) != collection_digest(**_BASE)


def test_a_compound_reads_as_the_library_identifies_one():
    """Case and runs of spaces aside, and in any order: a compound the library
    already held, reused by the seed, is the seed's own."""
    reused = {**_BASE, "compounds": [("[br2]-", " Br "), ("[Br]-", "()")]}

    assert collection_digest(**reused) == collection_digest(**_BASE)


def test_an_ion_no_mechanism_of_the_mode_makes_is_refused():
    with pytest.raises(ValueError, match="makes the ion"):
        _neutral("Br", +1, ("[M+NO3]-", "[M-H]-"))


@pytest.mark.parametrize(
    ("formula", "charge", "mechanisms", "neutral"),
    [
        # The reagent ion itself is nothing at all added to.
        ("Br", -1, ("[M+Br]-", "[M-H]-"), "()"),
        # Which nothing removed from cannot be, nor nothing ionized.
        ("Br", -1, ("[M-H]-",), "BrH"),
        ("C16H10", +1, ("[M]+.",), "C16H10"),
        # The mode's first mechanism that makes the ion, in its order.
        ("HN2O6", -1, ("[M-H]-", "[M+NO3]-"), "H2N2O6"),
        ("HN2O6", -1, ("[M+NO3]-", "[M-H]-"), "HNO3"),
        # A mechanism whose moiety the ion does not hold is passed over.
        ("Br", -1, ("[M+NO3]-", "[M+Br]-"), "()"),
        # A mechanism of the other charge is passed over.
        ("CH5N2O", +1, ("[M-H]-", "[M+H]+"), "CH4N2O"),
    ],
)
def test_the_neutral(formula, charge, mechanisms, neutral):
    assert _neutral(formula, charge, mechanisms) == neutral
