"""Assignment profiles: the reagent chemistry and the sampled matrix.

Two orthogonal axes, as designed in ``docs/dev/chemistry_profiles.md`` and
sequenced by ``docs/dev/assignment_quality_plan.md`` (step 1.1):

- a :class:`ReagentProfile` answers "how was this measured?" - the ion source's
  polarity, the element grid its analytes can plausibly be built from, the
  mechanism notations that identify it, and the secondary channels the source
  also produces;
- a :class:`ChemistryContext` answers "what was sampled?" - heteroatom caps and
  Van Krevelen ratio windows for the matrix.

They stay separate because Br- CIMS on ambient air and Br- CIMS in a chamber
share every reagent fact and differ only in matrix priors; flattening the two
axes would be five reagents times nine contexts of duplicated data.

The presets are ported from peaky's ``chem/profiles.py`` and ``chem/contexts.py``
(github.com/ultra-trace-systems/peaky), where they were fitted on real
Br-/uronium/NO3-/I- campaigns. Two things are deliberately *not* ported:

- peaky's context also carries a CHO(N) grid box (``grid_c_max``/``grid_o_max``)
  because its passes build the grid from the context. Here the grid belongs to
  the reagent profile and a context may only *narrow* it, so a context cannot
  silently widen a grid the reagent chemistry bounded;
- peaky's carbon-free allowlist and contaminant families, which belong with the
  reagent pre-pass and the series detection of later steps.

This module is pure data plus pure functions over it: no database, no I/O. The
backend resolves a sample onto these presets in
``mascope_backend.api.new.peak_assignments.profiles`` and snapshots the result
onto the run, and step 3.5 of the plan turns them into versioned database rows
seeded from exactly this data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping


#: Ratio-window keys. ``C`` is the carbon-equivalent count (C + Si: silicon is a
#: tetravalent backbone atom) and ``H`` the hydrogen-equivalent one
#: (H + F + Cl + Br + I: halogens are monovalent H-substituents). Without the
#: substitution a halogenated compound is rejected on a ratio it never violated -
#: trichloroacetic acid C2HCl3O2 reads H/C 0.5 and (H+X)/C 2.0.
RATIO_H_TO_C = "H/C"
RATIO_O_TO_C = "O/C"
RATIO_N_TO_C = "N/C"
RATIO_DBE_TO_C = "DBE/C"

#: The untargeted stage's m/z window per instrument class, in ppm. An Orbitrap
#: assigns at 0.2-0.3 ppm and a 10 ppm window there is nothing but candidate
#: space; a TOF at 5-15 ppm needs the room. Keyed by
#: ``mascope_file.name.get_instrument_type`` values.
INSTRUMENT_MZ_PRECISION_PPM: dict[str, float] = {"orbi": 3.0, "tof": 20.0}

#: The window an unrecognised instrument class falls back to.
DEFAULT_MZ_PRECISION_PPM = 10.0

_RANGE_TOKEN = re.compile(r"^(\^?\[?\d*[A-Z][a-z]?\]?)(\d+)-(\d+)$")


@dataclass(frozen=True)
class ReagentProfile:
    """The measurement chemistry of one ion source.

    :param name: Short key, the value a run config names (``"BR"``).
    :param label: Display label (``"Bromide CIMS"``).
    :param polarity: ``"+"`` or ``"-"``; ``""`` for the identity profile, which
        belongs to no polarity.
    :param element_ranges: The neutral element grid the untargeted stage
        enumerates, in the ``element_count_ranges`` grammar. A context may
        narrow it; nothing widens it.
    :param detection: Ionization-mechanism notations whose presence on a
        sample's mode identifies this profile. The fingerprint, in the
        notation the mechanism table stores.
    :param secondary_adducts: Channels the source also produces but that a
        mode is rarely configured with. Searched opportunistically by step 1.2
        of the assignment plan; carried here because the panel is a fact about
        the reagent chemistry, not about one deployment's mechanism table.
    :param default_context: The context this reagent is normally used with,
        applied when a run asks for ``auto``.
    :param label_isotope: For an isotope-labelled reagent, the custom element
        its label introduces (``"^N"`` for 15N-nitrate).
    :param label_purity: The labelled isotope's fraction (0.98 for a 98% 15N
        reagent), which is what makes the light isotopologue predictable.
    :param min_carbon: Carbon floor the resolved grid gets. 1 for an organic
        grid; 0 only for the identity profile, which must reproduce the
        engine's pre-profile behaviour exactly.
    :param mz_precision_ppm: A fixed untargeted m/z window in ppm, overriding
        the instrument-class default of
        :data:`INSTRUMENT_MZ_PRECISION_PPM`. Set only by the identity profile.
    :param aliases: Alternative names a run config may use.
    """

    name: str
    label: str
    polarity: str
    element_ranges: str
    detection: tuple[str, ...] = ()
    secondary_adducts: tuple[str, ...] = ()
    default_context: str = "none"
    label_isotope: str | None = None
    label_purity: float | None = None
    min_carbon: int = 1
    mz_precision_ppm: float | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChemistryContext:
    """The matrix prior: what the sample was drawn from.

    Ratio windows are evaluated on the effective counts described at
    :data:`RATIO_H_TO_C`, and only where the carbon-equivalent count reaches
    ``config.CONTEXT_RATIO_MIN_CARBON`` - below it the ratios say nothing (methane reads
    H/C 4.0, urea 4.0) and applying them would reject the small molecules a CIMS
    source is most sensitive to.

    :param name: Short key a run config names (``"ambient-air"``).
    :param label: Display label.
    :param description: What the context is for, shown where it is chosen.
    :param element_caps: Maximum count per element in a neutral. Narrows the
        reagent grid; an element capped at 0 leaves the grid entirely.
    :param h_to_c: (H+F+Cl+Br+I)/(C+Si) window, or None for no window.
    :param o_to_c: O/(C+Si) window.
    :param n_to_c: N/(C+Si) window.
    :param dbe_to_c: DBE/(C+Si) window.
    :param min_carbon_for: Minimum carbon scaffold an element needs in a
        neutral (``{"Br": 5}``): below it the formula is almost always a
        reagent-cluster alias. Graded rather than gated, so it is read by the
        plausibility layer of stage 2, not by this step's filter.
    """

    name: str
    label: str
    description: str = ""
    element_caps: Mapping[str, int] = field(default_factory=dict)
    h_to_c: tuple[float, float] | None = None
    o_to_c: tuple[float, float] | None = None
    n_to_c: tuple[float, float] | None = None
    dbe_to_c: tuple[float, float] | None = None
    min_carbon_for: Mapping[str, int] = field(default_factory=dict)

    def ratio_windows(self) -> dict[str, tuple[float, float]]:
        """The context's ratio windows keyed for the heuristic filter.

        :return: Window per ratio key, omitting the ones this context leaves
            open. An empty dict means the context constrains no ratio, which is
            what makes ``none`` an identity.
        """
        windows = {
            RATIO_H_TO_C: self.h_to_c,
            RATIO_O_TO_C: self.o_to_c,
            RATIO_N_TO_C: self.n_to_c,
            RATIO_DBE_TO_C: self.dbe_to_c,
        }
        return {key: window for key, window in windows.items() if window is not None}


# --- Chemistry contexts ------------------------------------------------------
# Ported from peaky's chem/contexts.py, where the windows were fitted on real
# campaigns. The reasoning behind the individual numbers lives there; what
# matters here is that a context only ever narrows.

AMBIENT_AIR = ChemistryContext(
    name="ambient-air",
    label="Ambient air",
    description=(
        "Outdoor / ambient air: VOC oxidation chemistry (OH/O3/NO3/Cl), "
        "organonitrates and organosulfates, a routine contaminant load."
    ),
    # H/C reaches 2.75, not 2.6: a saturated C3 polyol (glycerol C3H8O3,
    # propylene glycol C3H8O2) sits at 2.67 and is real atmospheric signal.
    h_to_c=(0.7, 2.75),
    o_to_c=(0.0, 1.5),
    n_to_c=(0.0, 0.4),
    dbe_to_c=(0.0, 0.75),
    # F, P and I are capped at 0 for one reason: none of them can be confirmed
    # by an isotope (19F, 31P and 127I are monoisotopic), so a covalent one in a
    # neutral is a mass coincidence until a curated reference list says
    # otherwise. Stage A's known set is how they reach an assignment.
    element_caps={
        "N": 3,
        "S": 1,
        "P": 0,
        "F": 0,
        "Si": 1,
        "Cl": 2,
        "Br": 2,
        "I": 0,
    },
    min_carbon_for={"Br": 5, "Cl": 5, "F": 3},
)

CHAMBER = ChemistryContext(
    name="chamber",
    label="Chamber",
    description=(
        "Smog or environmental chamber: a clean known precursor with a "
        "controlled oxidant, HOMs and accretion dimers, tight unsaturation."
    ),
    h_to_c=(0.9, 2.75),
    o_to_c=(0.0, 2.2),
    n_to_c=(0.0, 0.4),
    dbe_to_c=(0.0, 0.7),
    element_caps={"N": 2, "S": 1, "P": 0, "F": 0, "Si": 1, "I": 0},
    min_carbon_for={"Br": 5, "Cl": 5, "F": 3},
)

INDOOR_AIR = ChemistryContext(
    name="indoor-air",
    label="Indoor air",
    description=(
        "Indoor air: siloxanes (personal care, sealants), glycols, amines and "
        "phthalates are real signal here rather than background."
    ),
    h_to_c=(0.7, 2.75),
    o_to_c=(0.0, 1.5),
    n_to_c=(0.0, 0.5),
    dbe_to_c=(0.0, 0.9),
    element_caps={"N": 3, "S": 1, "P": 1, "F": 2, "Si": 6, "Cl": 2, "Br": 1},
    min_carbon_for={"Br": 5, "Cl": 4, "F": 2},
)

OBJECT_HEADSPACE = ChemistryContext(
    name="object-headspace",
    label="Object headspace",
    description=(
        "Headspace over an object or material: terpenes, esters, aldehydes "
        "and alcohols, with a broad H/C and sample-specific volatiles."
    ),
    h_to_c=(0.8, 2.6),
    o_to_c=(0.0, 1.3),
    n_to_c=(0.0, 0.5),
    dbe_to_c=(0.0, 1.0),
    element_caps={"N": 3, "S": 2, "P": 0, "F": 0, "Si": 2},
    min_carbon_for={"Br": 4, "Cl": 4, "F": 3},
)

COMBUSTION = ChemistryContext(
    name="combustion",
    label="Combustion",
    description="Combustion, soot precursors and biomass burning: PAHs, high DBE.",
    h_to_c=(0.2, 2.2),
    o_to_c=(0.0, 1.5),
    n_to_c=(0.0, 0.5),
    dbe_to_c=(0.0, 1.1),
    element_caps={"N": 4, "S": 1, "P": 0, "F": 0},
)

WATER = ChemistryContext(
    name="water",
    label="Water",
    description=(
        "Drinking water, disinfection by-products and wastewater: halogenated "
        "species are the analytes, not contaminants."
    ),
    h_to_c=(0.5, 2.2),
    o_to_c=(0.0, 1.5),
    n_to_c=(0.0, 0.5),
    dbe_to_c=(0.0, 0.9),
    element_caps={"N": 5, "S": 2, "P": 1, "F": 4, "Cl": 6, "Br": 4, "I": 2},
)

FOOD = ChemistryContext(
    name="food",
    label="Food and beverage",
    description="Food, beverage and fermentation: natural products and plasticisers.",
    h_to_c=(0.5, 2.6),
    o_to_c=(0.0, 1.3),
    n_to_c=(0.0, 0.5),
    dbe_to_c=(0.0, 1.0),
    element_caps={"N": 8, "S": 2, "P": 1},
)

URONIUM = ChemistryContext(
    name="uronium",
    label="Uronium",
    description=(
        "Urea-CIMS in positive mode: N-heavy chemistry, oxygenated VOC and "
        "amine or N-base analytes seen as [M+H]+ and [M+urea+H]+."
    ),
    # H/C spans aromatic N-heterocycles (~0.5) to saturated amino-alcohols
    # (~2.6); N/C to 0.6 admits the N-bases the source is selective for without
    # opening the polyamide corner.
    h_to_c=(0.4, 2.6),
    o_to_c=(0.0, 1.5),
    n_to_c=(0.0, 0.6),
    dbe_to_c=(0.0, 1.1),
    element_caps={
        "N": 5,
        "S": 2,
        "P": 1,
        "F": 0,
        "Si": 12,
        "Cl": 0,
        "Br": 0,
        "I": 0,
    },
    min_carbon_for={"Si": 2},
)

NO_CONTEXT = ChemistryContext(
    name="none",
    label="None",
    description=(
        "No matrix prior: the reagent grid stands as it is and only the "
        "universal structural rules apply. The identity context."
    ),
)

CHEMISTRY_CONTEXTS: dict[str, ChemistryContext] = {
    context.name: context
    for context in (
        AMBIENT_AIR,
        CHAMBER,
        INDOOR_AIR,
        OBJECT_HEADSPACE,
        COMBUSTION,
        WATER,
        FOOD,
        URONIUM,
        NO_CONTEXT,
    )
}

_CONTEXT_ALIASES: dict[str, str] = {
    "ambient": AMBIENT_AIR.name,
    "atmospheric": AMBIENT_AIR.name,
    "smog-chamber": CHAMBER.name,
    "flow-tube": CHAMBER.name,
    "indoor": INDOOR_AIR.name,
    "headspace": OBJECT_HEADSPACE.name,
    "biomass": COMBUSTION.name,
    "wastewater": WATER.name,
    "beverage": FOOD.name,
    "urea": URONIUM.name,
    "urea-cims": URONIUM.name,
}


# --- Reagent profiles --------------------------------------------------------

#: The identity profile. Its grid and window are the engine's historical
#: defaults, so a run that names it reproduces a pre-profile ledger: it is what
#: the plan's regression guard compares against, and what an operator selects to
#: opt a deployment out of profile-driven search entirely.
IDENTITY_PROFILE_NAME = "none"

NO_PROFILE = ReagentProfile(
    name=IDENTITY_PROFILE_NAME,
    label="None",
    polarity="",
    element_ranges="C0-100 H0-100 O0-100 N0-100",
    default_context=NO_CONTEXT.name,
    # Both are the engine's historical defaults, restated rather than imported:
    # this library knows nothing of the backend's config, and the point of the
    # identity profile is that it is pinned even if a default moves.
    min_carbon=0,
    mz_precision_ppm=10.0,
)

BR = ReagentProfile(
    name="BR",
    label="Bromide CIMS",
    polarity="-",
    element_ranges="C0-40 H0-80 N0-3 O0-18 S0-2 Cl0-2 Br0-2",
    detection=("+Br-",),
    # Carbonate and the dibromide cluster: channels a bromide source produces
    # that a mode is seldom configured with. 145 of the reference engine's main
    # peaks on the nitrate gate set are read through the carbonate channel.
    secondary_adducts=("+CO3-", "+Br2-"),
    default_context=AMBIENT_AIR.name,
    aliases=("br", "bromide", "br-cims"),
)

UR = ReagentProfile(
    name="UR",
    label="Uronium (urea) CIMS",
    polarity="+",
    element_ranges="C0-40 H0-90 N0-8 O0-15 S0-2",
    detection=("+(CH4N2O)H+",),
    # The two channels the gate set showed missing outright: 1,648 [M+NH4]+ and
    # 317 [M+Na]+ main peaks on one Orbitrap that the engine could otherwise
    # only read as heavier N- or Na-free neutrals.
    secondary_adducts=("+NH4+", "+Na+"),
    default_context=URONIUM.name,
    aliases=("ur", "uronium", "urea", "urea-cims"),
)

NO3 = ReagentProfile(
    name="NO3",
    label="Nitrate CIMS",
    polarity="-",
    element_ranges="C0-40 H0-60 N0-3 O0-25 S0-2",
    detection=("+NO3-",),
    default_context=AMBIENT_AIR.name,
    aliases=("no3", "nitrate", "nitrate-cims"),
)

NO3_15N = ReagentProfile(
    name="NO3_15N",
    label="15N-nitrate CIMS",
    polarity="-",
    element_ranges="C0-40 H0-60 N0-3 O0-25 S0-2",
    detection=("+^NO3-",),
    default_context=AMBIENT_AIR.name,
    label_isotope="^N",
    label_purity=0.98,
    aliases=("no3-15n", "15no3", "^no3", "15n-nitrate", "nitrate-15n"),
)

IODIDE = ReagentProfile(
    name="IODIDE",
    label="Iodide CIMS",
    polarity="-",
    # No iodine in the grid, for the reason the ambient context caps it at 0:
    # 127I is monoisotopic, so covalent iodine reaches a neutral through the
    # adduct or a curated list, never through a mass fit.
    element_ranges="C0-40 H0-80 N0-3 O0-20 S0-2 Cl0-1",
    detection=("+I-",),
    secondary_adducts=("+I2-",),
    default_context=AMBIENT_AIR.name,
    aliases=("i", "iodide", "iodide-cims"),
)

ESI_POS = ReagentProfile(
    name="ESI_POS",
    label="Positive ESI / APCI",
    polarity="+",
    # Wider than the CIMS grids and narrower than the historical default: with
    # no reagent chemistry to lean on, the bound is the small-molecule range
    # itself rather than a source's selectivity. No matrix prior either - the
    # sampled matrix of a generic positive-mode run is unknown.
    element_ranges="C0-60 H0-120 N0-6 O0-25 S0-3",
    default_context=NO_CONTEXT.name,
    aliases=("esi+", "esi-pos", "positive"),
)

ESI_NEG = ReagentProfile(
    name="ESI_NEG",
    label="Negative ESI / APCI",
    polarity="-",
    element_ranges="C0-60 H0-120 N0-6 O0-25 S0-3",
    default_context=NO_CONTEXT.name,
    aliases=("esi-", "esi-neg", "negative"),
)

REAGENT_PROFILES: dict[str, ReagentProfile] = {
    profile.name: profile
    for profile in (NO_PROFILE, BR, UR, NO3, NO3_15N, IODIDE, ESI_POS, ESI_NEG)
}

_PROFILE_ALIASES: dict[str, str] = {
    alias: profile.name
    for profile in REAGENT_PROFILES.values()
    for alias in (profile.name.lower(), *profile.aliases)
}

#: Fingerprint order. A mode carrying several diagnostic mechanisms resolves the
#: same way every time, and the labelled nitrate is tested before the unlabelled
#: one because a 15N deployment often keeps both mechanisms on the mode.
_DETECTION_ORDER: tuple[ReagentProfile, ...] = (UR, NO3_15N, NO3, BR, IODIDE)

#: The generic profile per polarity, used when no mechanism is diagnostic.
_ESI_BY_POLARITY: dict[str, ReagentProfile] = {"+": ESI_POS, "-": ESI_NEG}


def get_reagent_profile(name: str) -> ReagentProfile:
    """Look a reagent profile up by name or alias, case-insensitively.

    :param name: Profile name or alias.
    :raises KeyError: The name is not a known profile.
    :return: The profile.
    """
    key = _PROFILE_ALIASES.get((name or "").strip().lower())
    if key is None:
        raise KeyError(
            f"unknown reagent profile {name!r}; known: {sorted(REAGENT_PROFILES)}"
        )
    return REAGENT_PROFILES[key]


def get_chemistry_context(name: str) -> ChemistryContext:
    """Look a chemistry context up by name or alias, case-insensitively.

    :param name: Context name or alias.
    :raises KeyError: The name is not a known context.
    :return: The context.
    """
    key = (name or "").strip().lower()
    key = _CONTEXT_ALIASES.get(key, key)
    if key not in CHEMISTRY_CONTEXTS:
        raise KeyError(
            f"unknown chemistry context {name!r}; known: {sorted(CHEMISTRY_CONTEXTS)}"
        )
    return CHEMISTRY_CONTEXTS[key]


def detect_reagent_profile(
    mechanism_notations: list[str] | tuple[str, ...],
    polarity: str | None = None,
) -> ReagentProfile:
    """The profile a sample's ionization mechanisms identify.

    The mechanism panel is the fingerprint the deployment already maintains:
    a mode carrying ``+(CH4N2O)H+`` is a urea source whatever it is named, and
    one carrying ``+Br-`` is a bromide source. Only when nothing is diagnostic
    does the polarity decide, and then the answer is the generic ESI preset -
    a broad grid and no matrix prior, because nothing was learned.

    :param mechanism_notations: The mode's mechanism notations, in the form the
        mechanism table stores (``"+Br-"``, ``"+(CH4N2O)H+"``).
    :param polarity: The sample's polarity, ``"+"`` or ``"-"``. Only consulted
        when no mechanism is diagnostic.
    :return: The resolved profile; :data:`NO_PROFILE` when neither the
        mechanisms nor the polarity say anything, so an unrecognisable sample
        keeps the engine's historical behaviour rather than acquiring a grid
        nothing justifies.
    """
    seen = {notation.strip() for notation in mechanism_notations or ()}
    for profile in _DETECTION_ORDER:
        if seen.intersection(profile.detection):
            return profile
    return _ESI_BY_POLARITY.get((polarity or "").strip(), NO_PROFILE)


def parse_element_ranges(element_ranges: str) -> dict[str, tuple[int, int]]:
    """Parse an element-range string into per-element bounds.

    Accepts the same grammar the composition finder does - ``C0-40``,
    ``[13C]0-2``, ``^N0-1`` - and preserves the order the elements appear in.

    :param element_ranges: The range string.
    :return: Element symbol to (minimum, maximum) count.
    :raises ValueError: A token does not parse.
    """
    ranges: dict[str, tuple[int, int]] = {}
    for token in (element_ranges or "").split():
        match = _RANGE_TOKEN.fullmatch(token)
        if match is None:
            raise ValueError(f"invalid element count range token {token!r}")
        symbol, minimum, maximum = match.groups()
        ranges[symbol] = (int(minimum), int(maximum))
    return ranges


def format_element_ranges(ranges: Mapping[str, tuple[int, int]]) -> str:
    """Render per-element bounds back into the finder's range string.

    :param ranges: Element symbol to (minimum, maximum) count.
    :return: The range string, elements in the mapping's order.
    """
    return " ".join(f"{symbol}{lo}-{hi}" for symbol, (lo, hi) in ranges.items())


def resolve_element_ranges(
    profile: ReagentProfile,
    context: ChemistryContext,
    min_carbon: int | None = None,
) -> str:
    """The neutral grid a run searches: the reagent grid, narrowed by the matrix.

    The context can only take away. An element it caps below the profile's
    maximum is narrowed, an element it caps at zero leaves the grid entirely
    (which removes it from the enumeration's depth, the term the search cost is
    exponential in), and an element the context says nothing about keeps the
    profile's bound.

    Carbon is floored at ``min_carbon`` for an organic grid. Carbon-free
    formulas are the clearest wrong answers in the measured baseline - dozens
    per sample, siloxane and nitrate coincidences that a carbon-free box can
    always fit - and the inorganic species that are genuine (HNO3, H2SO4, HOBr)
    belong to Stage A's curated set, which this grid does not gate.

    :param profile: The reagent profile supplying the grid.
    :param context: The matrix context supplying the caps.
    :param min_carbon: Minimum carbon count, overriding the profile's own
        :attr:`ReagentProfile.min_carbon`; 0 leaves the grid's floor alone.
    :return: The resolved ``element_count_ranges`` string.
    """
    if min_carbon is None:
        min_carbon = profile.min_carbon
    ranges = parse_element_ranges(profile.element_ranges)
    resolved: dict[str, tuple[int, int]] = {}
    for symbol, (low, high) in ranges.items():
        cap = context.element_caps.get(symbol)
        if cap is not None:
            high = min(high, cap)
        if symbol == "C" and min_carbon > 0:
            low = max(low, min_carbon)
        if high <= 0 or high < low:
            # Capped out of existence. Dropped rather than emitted as a 0-0
            # range: the enumeration is a depth-first search whose depth is the
            # species count, so an element that cannot appear must leave the
            # grid, not merely be bounded to nothing.
            continue
        resolved[symbol] = (low, high)
    return format_element_ranges(resolved)


def resolve_mz_precision_ppm(
    profile: ReagentProfile, instrument_type: str | None
) -> float:
    """The untargeted stage's m/z window for this profile on this instrument.

    The window is a property of the instrument class rather than of the
    chemistry - an Orbitrap assigns at 0.2-0.3 ppm and a TOF at 5-15 - so a
    profile only names one when it deliberately pins it.

    :param profile: The resolved reagent profile.
    :param instrument_type: ``"orbi"``, ``"tof"``, or None/unknown.
    :return: The window in ppm.
    """
    if profile.mz_precision_ppm is not None:
        return profile.mz_precision_ppm
    return INSTRUMENT_MZ_PRECISION_PPM.get(
        (instrument_type or "").strip().lower(), DEFAULT_MZ_PRECISION_PPM
    )
