"""Assignment profiles: the reagent chemistry and the sampled matrix.

Two orthogonal axes, as designed in ``docs/dev/chemistry_profiles.md`` and
sequenced by ``docs/dev/assignment_quality_plan.md`` (step 1.1):

- a :class:`ReagentProfile` answers "how was this measured?" - the ion source's
  polarity, the element grid its analytes can plausibly be built from, the
  mechanism notations that identify it, and the secondary channels the source
  also produces;
- a :class:`ChemistryContext` answers "what was sampled?" - heteroatom caps and
  Van Krevelen ratio windows for the matrix, and the ceiling on the formulas a
  reference source may bring into Stage A's known set.

They stay separate because Br- CIMS on ambient air and Br- CIMS in a chamber
share every reagent fact and differ only in matrix priors; flattening the two
axes would be five reagents times nine contexts of duplicated data.

The presets are ported from peaky's ``chem/profiles.py`` and ``chem/contexts.py``
(github.com/ultra-trace-systems/peaky), where they were fitted on real
Br-/uronium/NO3-/I- campaigns; the charge-transfer profiles followed in step
3.1, from peaky's EasyIC profile and a chemist's reading of a chamber dataset
measured on that source. Two things are deliberately *not* ported:

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

from mascope_tools.composition import formula_shape
from mascope_tools.composition.heuristic_filter import element_counts
from mascope_tools.composition.known_window import KnownWindow
from mascope_tools.composition.mechanism_notation import (
    MechanismNotationError,
    mechanism_key,
    standard_notation,
)


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
#: space. Keyed by ``mascope_file.name.get_instrument_type`` values.
#:
#: A TOF keeps the historical 10 ppm rather than the wider window its raw
#: accuracy suggests. Measured on the gate's three TOF sets, 20 ppm made the
#: committed mass error worse on all three (MAD 1.56 to 2.22, 0.94 to 1.81,
#: 0.73 to 1.19 ppm) and bought nothing: the multi-scheme TOF spreads 0.7-1.0
#: ppm, so 20 ppm is twenty sigma there and admits fits that are only noise.
#: The 10 ppm is a ceiling, not the window a sample with a measured width
#: searches: where Stage A fitted the sample's own width, the window is bounded
#: by it (:func:`fitted_window_ppm`, step 3.6).
INSTRUMENT_MZ_PRECISION_PPM: dict[str, float] = {"orbi": 3.0, "tof": 10.0}

#: The instrument classes whose untargeted window the sample's own fitted width
#: bounds (step 3.6 of the assignment plan). A TOF's 10 ppm is ten to fourteen
#: of its widths, so most of the window is candidate space no right formula
#: reaches. An Orbitrap's 3 ppm is already about ten of its 0.3 ppm, and a
#: tighter one there would cut the trend an uncorrected low-mass axis bends by
#: (``mass_accuracy.fit_mass_trend``), which the scorer reads and the window
#: does not.
FITTED_WINDOW_INSTRUMENTS: frozenset[str] = frozenset({"tof"})


def fitted_window_ppm(
    class_window_ppm: float, sigma_ppm: float, mu_ppm: float, sigmas: float
) -> float:
    """The untargeted window a sample's fitted width bounds.

    The window is symmetric about the peak and the sample's errors sit about
    its own centre, so it reaches the centre's distance from zero and then
    ``sigmas`` widths on: ``|mu| + sigmas * sigma``, never wider than the
    instrument class's window.

    :param class_window_ppm: The instrument class's window
        (:data:`INSTRUMENT_MZ_PRECISION_PPM`), the ceiling.
    :param sigma_ppm: The width the sample's fit is scored at
        (``mass_accuracy.scoring_sigma_ppm`` of its fitted width).
    :param mu_ppm: The centre it is scored at.
    :param sigmas: How many widths from the centre a right formula may sit.
    :return: The window in ppm.
    """
    return min(
        float(class_window_ppm), abs(float(mu_ppm)) + float(sigmas) * float(sigma_ppm)
    )


#: The window an unrecognised instrument class falls back to.
DEFAULT_MZ_PRECISION_PPM = 10.0

#: The width the fit score's mass term falls back to per instrument class, in
#: ppm - the Gaussian sigma of the mass likelihood, and NOT a second name for
#: the search window above. The window says how far from a peak a candidate may
#: sit; this says how well a hit has to agree once it is there, and on an
#: Orbitrap the two are an order of magnitude apart: 3 ppm of candidate space
#: around a peak whose error is 0.2-0.3 ppm.
#:
#: Used only where nothing has MEASURED the sample's own width - fewer than
#: eight known ions matched, which is the state of a bromide set whose curated
#: library holds two targets. A class statement is a poor substitute for a
#: measurement and a far better one than the match tolerance, which at 5 ppm
#: is five times an Orbitrap's real accuracy and lets a formula a whole ppm off
#: win a peak from one that is on it. The TOF number is the spread the gate
#: measured on its three TOF sets (0.7-2.2 ppm MAD).
INSTRUMENT_FALLBACK_SIGMA_PPM: dict[str, float] = {"orbi": 0.3, "tof": 3.0}

#: The width an unrecognised instrument class is judged at: the more forgiving
#: of the two, because an unknown instrument must not be held to an Orbitrap's.
DEFAULT_FALLBACK_SIGMA_PPM = 3.0

#: How far from its prediction a line may sit and still be claimed as an ion's,
#: per instrument class. The third of the three windows here and the widest: a
#: candidate is SEARCHED within `INSTRUMENT_MZ_PRECISION_PPM`, its lines are
#: MATCHED within this, and the agreement is then SCORED at the sample's own
#: fitted width or `INSTRUMENT_FALLBACK_SIGMA_PPM`. Matching generously and
#: scoring strictly is deliberate: a line 3 ppm out on an Orbitrap is this
#: ion's line, measured badly, and the score says so - refusing to pair it at
#: all would instead let the candidate off the hook, since a line nobody
#: matched is only charged for being absent.
#:
#: These are the targeted matcher's own class defaults, so a peak that an
#: untargeted candidate claims here would be claimed by the curated library
#: too, and the two stages of an engine cannot disagree about what a line is.
INSTRUMENT_MATCH_TOLERANCE_PPM: dict[str, float] = {"orbi": 5.0, "tof": 15.0}

#: The matching window for an unrecognised class: the wider of the two, so an
#: unknown instrument is not held to an Orbitrap's pairing.
DEFAULT_MATCH_TOLERANCE_PPM = 15.0

_RANGE_TOKEN = re.compile(r"^(\^?\[?\d*[A-Z][a-z]?\]?)(\d+)-(\d+)$")

#: A DOI, as a reference list's are checked (``mascope_reference.peaklist``).
_DOI = re.compile(r"10\.\d{4,9}/\S+")


@dataclass(frozen=True)
class Citation:
    """One work an entry of chemistry data rests on.

    The shape a reference list cites its publications in
    (``mascope_reference.peaklist.REFERENCE_KEYS``): the work as a reader cites
    it, and the DOI or, for a book, the ISBN that finds it.

    :param citation: The work, as a reader would cite it.
    :param doi: Its DOI.
    :param isbn: Its ISBN, for a book.
    """

    citation: str
    doi: str | None = None
    isbn: str | None = None

    def record(self) -> dict:
        """The citation as a run records it: its keys that hold a value."""
        return {
            key: value
            for key, value in (
                ("citation", self.citation),
                ("doi", self.doi),
                ("isbn", self.isbn),
            )
            if value
        }

    def text(self) -> str:
        """The citation for a person: the work, and the DOI or ISBN that finds it."""
        found_by = self.doi or self.isbn
        return f"{self.citation} ({found_by})" if found_by else self.citation


def _isbn_ok(value: str) -> bool:
    """Whether a string is an ISBN-10 or ISBN-13 with a valid check digit."""
    digits = re.sub(r"[\s-]", "", value).upper()
    if re.fullmatch(r"\d{13}", digits):
        total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(digits))
        return total % 10 == 0
    if re.fullmatch(r"\d{9}[\dX]", digits):
        total = sum(
            (10 - i) * (10 if d == "X" else int(d)) for i, d in enumerate(digits)
        )
        return total % 11 == 0
    return False


def citation_problems(citation: Citation, where: str) -> list[str]:
    """What keeps a citation from finding its work, one sentence each.

    The bar a reference list's references are held to: a citation, and a DOI
    or a valid ISBN.

    :param citation: The citation.
    :param where: What the sentences name it as.
    :return: The problems; empty when it cites its work.
    """
    problems = []
    if not (citation.citation or "").strip():
        problems.append(f"{where}: no citation")
    if not citation.doi and not citation.isbn:
        problems.append(f"{where}: neither a DOI nor an ISBN")
    if citation.doi and not _DOI.fullmatch(citation.doi):
        problems.append(f"{where}: '{citation.doi}' is not a DOI")
    if citation.isbn and not _isbn_ok(citation.isbn):
        problems.append(f"{where}: '{citation.isbn}' is not a valid ISBN")
    return problems


@dataclass(frozen=True)
class IonizationClass:
    """What a channel says of the molecule it ionized: one functional group.

    The identification level's 3b by ionization (step 5.8b of the assignment
    quality plan; ``docs/dev/assignment_confidence.md``, "P4 design: the
    identification level", point 4). Some channels ionize only a molecule that
    carries a group: a nitrate reagent deprotonates a molecule with an acidic
    hydrogen, a uronium reagent protonates one with a basic site. A row read
    through such a channel, its formula and split pinned at 4a, may state the
    class without naming any compound: "an acid of this formula".

    The entries are chemistry as data, authored by peaky's owner with every
    entry cited (PKY-35, PKY-39), one table per reagent profile
    (:attr:`ReagentProfile.ionization_classes`).

    :param mechanism: The channel, in the notation the mechanism table stores
        (``"[M-H]-"``). Read by its key
        (``mechanism_notation.mechanism_key``), so any spelling of the channel
        finds it.
    :param group: The group the ionization says the molecule carries, as a
        reader says it (``"an acidic hydrogen"``).
    :param noun: The class of molecule that carries it, which the row states
        (``"an acid"``, read "an acid of this formula").
    :param references: The works the entry rests on, at least one.
    """

    mechanism: str
    group: str
    noun: str
    references: tuple[Citation, ...] = ()

    def record(self) -> dict:
        """The entry as a run records it beside the level it stated."""
        return {
            "mechanism": self.mechanism,
            "group": self.group,
            "noun": self.noun,
            "references": [reference.record() for reference in self.references],
        }


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
    :param reagent_formula: The reagent ion's own composition (``"Br"``,
        ``"NO3"``, ``"^NO3"`` for the labelled nitrate, ``"CH4N2O"`` for urea),
        charge excluded. The cluster ions a source makes are built from it, so
        a labelled reagent's label follows into every one of them rather than
        being restated per cluster. None where the source has no single reagent
        species, as an electrospray does not.
    :param detection: Ionization-mechanism notations whose presence on a
        sample's mode identifies this profile. The fingerprint, in the
        standard adduct notation the mechanism table stores (``"[M+Br]-"``).
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
    :param min_carbon: Carbon floor the resolved grid gets: the formula-shape
        table's (``formula_shape.GRID_CARBON_FLOOR``), open, so a carbon-free
        formula is searched and the carbon-free signature caps it. A profile
        that needs a floor sets it here, a setting of its own search grid.
    :param mz_precision_ppm: A fixed untargeted m/z window in ppm, overriding
        the instrument-class default of
        :data:`INSTRUMENT_MZ_PRECISION_PPM`. Set only by the identity profile.
    :param source_solvents: The solvents the source's vapour carries, by
        formula (``reagents.SOLVENT_NAMES``): the reagent pass claims their
        clusters as the source's on their ladder (``reagents.solvent_ladders``).
        Empty for a source that builds none.
    :param aliases: Alternative names a run config may use.
    :param ionization_classes: The source's class table: per channel, the
        functional group its ionization says the molecule carries
        (:class:`IonizationClass`), each entry cited. A channel the table does
        not name says no group. Empty on every shipped profile until its
        entries are authored (PKY-39); :func:`ionization_class_problems` holds
        each entry to its citation.
    """

    name: str
    label: str
    polarity: str
    element_ranges: str
    reagent_formula: str | None = None
    detection: tuple[str, ...] = ()
    secondary_adducts: tuple[str, ...] = ()
    default_context: str = "none"
    label_isotope: str | None = None
    label_purity: float | None = None
    min_carbon: int = formula_shape.GRID_CARBON_FLOOR
    mz_precision_ppm: float | None = None
    source_solvents: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    ionization_classes: tuple[IonizationClass, ...] = ()


def ionization_class(
    profile: ReagentProfile | None, notation: str | None
) -> IonizationClass | None:
    """The group a channel of a profile says its molecule carries (level 3b).

    :param profile: The run's reagent profile; None names no table.
    :param notation: The channel a row was read through, in any spelling of
        its mechanism.
    :return: The profile's entry for the channel, or None where its table
        names none.
    """
    if profile is None or not notation:
        return None
    key = mechanism_key(notation)
    for entry in profile.ionization_classes:
        if mechanism_key(entry.mechanism) == key:
            return entry
    return None


def ionization_class_problems(profile: ReagentProfile) -> list[str]:
    """Everything that keeps a profile's class table from being read, one
    sentence each.

    Each entry names a channel that reads as a mechanism, once; a group and a
    class; and at least one work, each with a citation and a DOI or a valid
    ISBN (:func:`citation_problems`).

    :param profile: The profile.
    :return: The problems in table order; empty when the table may ship.
    """
    problems: list[str] = []
    seen: set[str] = set()
    for number, entry in enumerate(profile.ionization_classes, start=1):
        where = f"{profile.name} class {number} ({entry.mechanism})"
        try:
            standard_notation(entry.mechanism)
        except MechanismNotationError:
            problems.append(f"{where}: '{entry.mechanism}' is not a mechanism")
        key = mechanism_key(entry.mechanism)
        if key in seen:
            problems.append(f"{where}: the channel has an entry already")
        seen.add(key)
        if not entry.group.strip():
            problems.append(f"{where}: no group")
        if not entry.noun.strip():
            problems.append(f"{where}: no class")
        if not entry.references:
            problems.append(f"{where}: no reference")
        for index, reference in enumerate(entry.references, start=1):
            problems.extend(citation_problems(reference, f"{where}, reference {index}"))
    return problems


#: The solvents a positive chemical-ionization source's vapour carries: the
#: water every humid source holds, and the methanol, ethanol and acetone of a
#: laboratory's air and an inlet's cleaning. Their clusters with a proton, with
#: a hydronium and around a solvent less a hydride are the source's ions, not
#: the sample's (step 3.3d of the assignment plan).
POSITIVE_SOURCE_SOLVENTS: tuple[str, ...] = ("H2O", "CH4O", "C2H6O", "C3H6O")


#: The ceiling every shipped context sets on a reference source's window. It
#: opens silicon, phosphorus, fluorine, chlorine, bromine and iodine beside C, H,
#: N, O and S: the siloxanes, organophosphates, perfluorinated acids and reactive
#: iodine the shipped lists hold are families no formula grid reaches, so known
#: matching is the only way they are assigned. The carbon and mass caps are the
#: atmospheric window's, and they hold every shipped list: D6 is 444 Da, the
#: longest perfluorinated acid 614.
KNOWN_WINDOW_CEILING = KnownWindow(
    elements=frozenset({"C", "H", "N", "O", "S", "Si", "P", "F", "Cl", "Br", "I"}),
    max_carbon=40,
    max_mass=700.0,
)


#: The sulfur prior (step 3.6 of the assignment plan): a neutral carrying sulfur
#: needs corroboration beyond its mass fit to reach the top tier. An atmosphere
#: holds organosulfur only where it has a sulfur source, and the formula search
#: has no way to know whether it has one: sulfur formulas were 5% of the
#: assigned neutrals in the gate's nitrate batches, 2% in uronium and 22% on the
#: TOF, in an alpha-pinene system with no sulfur source at all. Sulfur's mass
#: defect lets a C/H/O/S formula absorb what a C/H/O/N one leaves, so the mass
#: fit alone cannot tell the two apart; the 34S line, 4.2% of M0 per atom, can.
PRIOR_SULFUR = "sulfur"

#: The odd-nitrogen prior: a neutral with two or more nitrogens and fewer than
#: two oxygens per nitrogen. An atmosphere's nitrogen reaches its organics as
#: nitrate and nitro groups, two or three oxygens per nitrogen; a formula with
#: more nitrogen than that is a reduced-nitrogen compound, which an oxidation
#: system rarely makes, or a grid's way of spending a mass on N2 against CO or
#: CH4 (N2 and CO differ by 11 mDa). On the gate's certified cylinder sets, a
#: nitrogen-free mixture, the nitrogen-rich C2 family was 21 to 34% of the
#: assigned rows. Organonitrates and nitroaromatics keep their tier: two
#: oxygens per nitrogen is the nitro group's own count.
PRIOR_ODD_NITROGEN = "odd_nitrogen"

#: Every prior a context can carry, in the order a formula meeting several is
#: reported in.
CONTEXT_PRIORS: tuple[str, ...] = (PRIOR_SULFUR, PRIOR_ODD_NITROGEN)

#: The element whose own isotope line corroborates a formula a prior asks about:
#: 34S (or 33S) for sulfur, 15N for nitrogen. A 13C line proves the carbon
#: count, which the mass fit had already spent; only a line of the element the
#: prior doubts says the element is there.
PRIOR_ELEMENT: dict[str, str] = {PRIOR_SULFUR: "S", PRIOR_ODD_NITROGEN: "N"}


def priors_met(counts: Mapping[str, int], priors: tuple[str, ...]) -> tuple[str, ...]:
    """Which of a context's priors a neutral's element counts meet.

    Pure arithmetic on counts, so a caller that already parsed the formula
    asks without parsing it again, and an isotope-labelled atom counts as its
    element (``element_counts`` folds ``[15N]`` into ``N``).

    :param counts: The neutral's element counts.
    :param priors: The priors to ask, in :data:`CONTEXT_PRIORS` order.
    :return: The priors the neutral meets, in the order given; empty for none.
    """
    met = []
    nitrogen = counts.get("N", 0)
    for prior in priors:
        if (
            prior == PRIOR_SULFUR
            and counts.get("S", 0) >= formula_shape.SULFUR_PRIOR_MIN
        ):
            met.append(prior)
        elif (
            prior == PRIOR_ODD_NITROGEN
            and nitrogen >= formula_shape.ODD_NITROGEN_MIN
            and counts.get("O", 0) < formula_shape.ODD_NITROGEN_OXYGEN_PER_N * nitrogen
        ):
            met.append(prior)
    return tuple(met)


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
    :param known_window: The ceiling on every reference source's window in
        Stage A: a source contributes a formula only inside both its own window
        and this one. None sets no ceiling, so each source is bounded by its own
        row alone.
    :param priors: The formula shapes this matrix holds only on corroboration
        (:data:`CONTEXT_PRIORS`): a formula the search elects that meets one
        reaches the top tier only where something beyond its mass fit saw it.
        Capped rather than gated: the grid keeps the formula, and the tiering
        pass reads the prior, at the formula-shape table's thresholds.
    :param windows_gate: Whether the ratio windows refuse a formula outside
        them rather than grade it (``heuristic_filter.rule_context_ratios``).
        Off in every shipped context: the windows are a floored factor of the
        plausibility (#3188).
    :param organonitrates: Whether the matrix makes organonitrates: NOx
        chemistry, whose dinitrates, trinitrates and nitroaromatics the ratio
        windows misjudge (:func:`nox_skeleton_applies`).
    """

    name: str
    label: str
    description: str = ""
    element_caps: Mapping[str, int] = field(default_factory=dict)
    h_to_c: tuple[float, float] | None = None
    o_to_c: tuple[float, float] | None = None
    n_to_c: tuple[float, float] | None = None
    dbe_to_c: tuple[float, float] | None = None
    known_window: KnownWindow | None = KNOWN_WINDOW_CEILING
    priors: tuple[str, ...] = ()
    organonitrates: bool = False
    windows_gate: bool = False

    def priors_met(self, formula: str | None) -> tuple[str, ...]:
        """The priors of this context a neutral formula meets.

        Fails open, like every chemistry rule here: a formula that cannot be
        parsed meets none.

        :param formula: The neutral formula.
        :return: The priors it meets, in :data:`CONTEXT_PRIORS` order.
        """
        if not self.priors:
            return ()
        counts = element_counts(formula or "")
        if not counts:
            return ()
        return priors_met(counts, self.priors)

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

#: The priors every atmospheric context carries: ambient air, a chamber and
#: indoor air. The other matrices hold sulfur and reduced nitrogen as real
#: chemistry - food and beverage, water's disinfection by-products, the
#: N-heterocycles of combustion, an object's own volatiles - and carry none.
ATMOSPHERIC_PRIORS: tuple[str, ...] = (PRIOR_SULFUR, PRIOR_ODD_NITROGEN)

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
    priors=ATMOSPHERIC_PRIORS,
    organonitrates=True,
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
    priors=ATMOSPHERIC_PRIORS,
    organonitrates=True,
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
    priors=ATMOSPHERIC_PRIORS,
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
    organonitrates=True,
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
    organonitrates=True,
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
    # The sulfur prior alone. Urea CIMS samples an atmosphere, whose sulfur
    # needs a source as anywhere else (2% of the gate's uronium assigned
    # neutrals carried it), but its analytes ARE the reduced-nitrogen bases the
    # odd-nitrogen prior doubts: the N/C window above is opened for them.
    priors=(PRIOR_SULFUR,),
)

NO_CONTEXT = ChemistryContext(
    name="none",
    label="None",
    description=(
        "No matrix prior: the reagent grid stands as it is and only the "
        "universal structural rules apply. The identity context."
    ),
    # No ceiling either, as the identity it is for the ratio windows: the ESI
    # profiles and the identity profile default to this context, and the
    # siloxane list's second citation reports those siloxanes as nanoESI
    # background.
    known_window=None,
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
    reagent_formula="Br",
    detection=("[M+Br]-",),
    # Carbonate and the dibromide cluster: channels a bromide source produces
    # that a mode is seldom configured with. 145 of the reference engine's main
    # peaks on the nitrate gate set are read through the carbonate channel.
    # Formate: see the nitrate profile.
    secondary_adducts=("[M+CO3]-", "[M+Br2]-", "[M+HCOO]-"),
    default_context=AMBIENT_AIR.name,
    aliases=("br", "bromide", "br-cims"),
)

UR = ReagentProfile(
    name="UR",
    label="Uronium (urea) CIMS",
    polarity="+",
    element_ranges="C0-40 H0-90 N0-8 O0-15 S0-2",
    reagent_formula="CH4N2O",
    detection=("[M+CH4N2O+H]+",),
    # Ammonium only. It is a real channel on this source - 1,648 [M+NH4]+ main
    # peaks on one Orbitrap that the engine could otherwise only read as
    # heavier N-free neutrals, the urea-NH4+ cluster present in every spectrum,
    # four in five of the reference engine's ammonium readings corroborated by
    # a second channel. Sodium is deliberately absent: no sodium cluster is
    # present in these spectra, and the reference engine's [M+Na]+ readings
    # there are mostly dim uncorroborated candidates - a channel that absorbs
    # unexplained mass rather than one the source produces.
    secondary_adducts=("[M+NH4]+",),
    default_context=URONIUM.name,
    source_solvents=POSITIVE_SOURCE_SOLVENTS,
    aliases=("ur", "uronium", "urea", "urea-cims"),
)

NO3 = ReagentProfile(
    name="NO3",
    label="Nitrate CIMS",
    polarity="-",
    element_ranges="C0-40 H0-60 N0-3 O0-25 S0-2",
    reagent_formula="NO3",
    detection=("[M+NO3]-",),
    # Carbonate, the channel the nitrate sets need most: the reference engine
    # reads 145 and 212 main peaks through it on those sets, and the mode does
    # not offer it, so every one of them can only be read here as some heavier
    # neutral through another channel.
    #
    # Formate, the channel a chamber dataset showed the engine lacked: with no
    # way to write a formate adduct, a series of alpha-pinene products read as
    # C11 acids at the top tier - C11H20O7 the batch's strongest assigned peak,
    # C11H20O5, C11H18O6, C11H18O7, C11H20O8 and C11H20O9 behind it, 24% of the
    # assigned-plus-candidate intensity - when nothing in that oxidation adds a
    # carbon and every one of them is, atom for atom, a C10 product the engine
    # assigns 46 Da lower plus formic acid. Mass and co-occurrence cannot part
    # the two readings, so the chemistry decides: the formate reading is the
    # row's only where the C10 neutral is itself seen through one of the
    # mode's own channels (the engine's partner gate), and otherwise the acid
    # is the row's reading, with the formate one set aside on it.
    secondary_adducts=("[M+CO3]-", "[M+HCOO]-"),
    default_context=AMBIENT_AIR.name,
    aliases=("no3", "nitrate", "nitrate-cims"),
)

NO3_15N = ReagentProfile(
    name="NO3_15N",
    label="15N-nitrate CIMS",
    polarity="-",
    element_ranges="C0-40 H0-60 N0-3 O0-25 S0-2",
    reagent_formula="^NO3",
    detection=("[M+^NO3]-",),
    # Carbonate carries no reagent nitrogen, so it is the same channel here as
    # on the unlabelled profile; formate likewise.
    secondary_adducts=("[M+CO3]-", "[M+HCOO]-"),
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
    reagent_formula="I",
    detection=("[M+I]-",),
    # Formate: see the nitrate profile.
    secondary_adducts=("[M+I2]-", "[M+HCOO]-"),
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
    # The alkali adducts belong here and only here: an electrospray source
    # really does produce them from residual salt, where a CIMS source's
    # spectra show no such cluster at all.
    secondary_adducts=("[M+NH4]+", "[M+Na]+", "[M+K]+"),
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

#: The charge-transfer source. An Orbitrap's EASY-IC internal-calibration
#: source is a fluoranthene ion beam, and run as a low-pressure chemical
#: ionization source it ionizes the sample by charge transfer: a mode built on
#: it declares electron transfer, ``[M]+.`` or ``[M]-.``, and nothing else.
#: Before this profile existed such a mode fell to the ESI
#: preset of its polarity, a C60 grid with no matrix prior, and on a chamber
#: dataset that read 357 of the 373 neutrals it committed on the negative
#: batch as formulas no atmosphere makes (C4H2N5-, C3N2O-, C3HN4-) while the
#: source's own ions stayed unassigned.
#:
#: The grid is peaky's EasyIC grid: reduced, hydrocarbon-rich chemistry
#: rather than an oxidation-product source, so it stops at O15 in positive
#: mode. Both polarities take the ambient-air context, whose element caps and
#: ratio windows are what reject the nitrogen-rich, hydrogen-poor formulas
#: above; the PAH corner peaky's own context opens (H/C down to 0.3) is left to
#: a reference list, since the one PAH the source is certain to show is its
#: reagent, and the reagent pass names that.
#:
#: The reagent's own channels are opportunistic here, not declared: charge
#: transfer keeps an aromatic intact as its radical cation, but the same source
#: abstracts a hydride from an alcohol or an alkane (``[M-H]+``, ethanol's only
#: channel, and the tropylium ion of toluene) and protonates where a proton is
#: to be had (``[M+H]+``, protonated acetone). In negative mode the anions the
#: discharge makes deprotonate acids (``[M-H]-``). A mode that declares only
#: electron transfer has those channels searched as secondary ones - switched
#: on by the source's own fingerprint and capped at candidate without
#: corroboration - and a same-ion family that reads one peak both as a radical
#: through electron transfer and as a closed-shell molecule through one of
#: them elects the molecule. A mode that declares proton transfer or
#: deprotonation beside electron transfer has said the source runs it: that
#: channel is the mode's own and is searched as one
#: (``reagents.SECONDARY_CHANNELS``).
#:
#: Electron transfer alone is not enough to name this source. An ambient-ion
#: mode on an APi-TOF - the air's own ions, no reagent - is declared the same
#: way, and the fleet holds such streams. The EASY-IC source is an Orbitrap's,
#: so the fingerprint is read only on that instrument class
#: (:data:`CHARGE_TRANSFER_INSTRUMENTS`); an electron-transfer mode anywhere
#: else keeps the path it had, the ESI preset of its polarity.
EASYIC_POS = ReagentProfile(
    name="EASYIC_POS",
    label="Charge transfer (EASY-IC), positive",
    polarity="+",
    element_ranges="C0-40 H0-80 N0-5 O0-15 S0-2",
    reagent_formula="C16H10",
    detection=("[M]+.",),
    # Hydride abstraction (``[M-H]+``: a hydride removed leaves a cation),
    # proton transfer, and methyl loss (``[M-CH3]+``: what charge transfer
    # leaves of a radical cation that sheds a methyl group, alpha-pinene's ion at
    # m/z 121 and a cyclic siloxane's base peak). All three are real channels of
    # this source and none is what a mode declares; see
    # ``reagents.SECONDARY_CHANNELS`` for what switches each on.
    secondary_adducts=("[M-H]+", "[M+H]+", "[M-CH3]+"),
    default_context=AMBIENT_AIR.name,
    # On the chamber dataset's positive batch the acetone dimer at m/z 117.091
    # sits in three of six samples and read as an analyte.
    source_solvents=POSITIVE_SOURCE_SOLVENTS,
    aliases=(
        "easyic",
        "easy-ic",
        "easyic+",
        "easy-ic+",
        "charge-transfer",
        "charge-transfer+",
        "ct+",
        "fluoranthene",
    ),
)

EASYIC_NEG = ReagentProfile(
    name="EASYIC_NEG",
    label="Charge transfer (EASY-IC), negative",
    polarity="-",
    # Oxygen to 20: the negative source reads acids, and a deprotonated acid
    # carries more oxygen than the hydrocarbons the positive one keeps intact.
    element_ranges="C0-40 H0-80 N0-5 O0-20 S0-2",
    reagent_formula="C16H10",
    detection=("[M]-.",),
    # Deprotonation, opened where the spectrum shows the source's anions
    # deprotonating acids of their own.
    secondary_adducts=("[M-H]-",),
    default_context=AMBIENT_AIR.name,
    aliases=("easyic-", "easy-ic-", "charge-transfer-", "ct-"),
)

REAGENT_PROFILES: dict[str, ReagentProfile] = {
    profile.name: profile
    for profile in (
        NO_PROFILE,
        BR,
        UR,
        NO3,
        NO3_15N,
        IODIDE,
        EASYIC_POS,
        EASYIC_NEG,
        ESI_POS,
        ESI_NEG,
    )
}

#: The instrument classes on which an electron-transfer mode is read as the
#: charge-transfer source, keyed by ``mascope_file.name.get_instrument_type``
#: values. The EASY-IC source is Orbitrap hardware; an ambient-ion mode on a
#: TOF declares the same electron transfer and is not this source.
CHARGE_TRANSFER_INSTRUMENTS: frozenset[str] = frozenset({"orbi"})

#: The profiles that fingerprint is read for.
_CHARGE_TRANSFER_PROFILES: frozenset[str] = frozenset(
    {EASYIC_POS.name, EASYIC_NEG.name}
)

_PROFILE_ALIASES: dict[str, str] = {
    alias: profile.name
    for profile in REAGENT_PROFILES.values()
    for alias in (profile.name.lower(), *profile.aliases)
}

#: Fingerprint order. A mode carrying several diagnostic mechanisms resolves the
#: same way every time, and the labelled nitrate is tested before the unlabelled
#: one because a 15N deployment often keeps both mechanisms on the mode.
#: Electron transfer comes after every reagent: a reagent mode that also
#: declares it is still that reagent's source, and only a mode with no reagent
#: at all is read as the charge-transfer one.
_DETECTION_ORDER: tuple[ReagentProfile, ...] = (
    UR,
    NO3_15N,
    NO3,
    BR,
    IODIDE,
    EASYIC_POS,
    EASYIC_NEG,
)

#: The generic profile per polarity, used when no mechanism is diagnostic and
#: the mode declares no electron transfer an Orbitrap could read as the
#: charge-transfer source - protonation or deprotonation alone, which is how an
#: electrospray or APCI mode is written, or electron transfer on any other
#: instrument.
#: Keyed by the single-character form the sample row carries, with the spelled-out
#: words accepted too - the same polarity is written both ways across the codebase
#: (a sample is "+", a batch "pos"), and a fallback that silently missed on the
#: spelling would look like a sample that said nothing at all.
_ESI_BY_POLARITY: dict[str, ReagentProfile] = {
    "+": ESI_POS,
    "positive": ESI_POS,
    "pos": ESI_POS,
    "-": ESI_NEG,
    "negative": ESI_NEG,
    "neg": ESI_NEG,
}

#: The polarity sign each accepted spelling stands for.
_POLARITY_SIGN: dict[str, str] = {
    key: profile.polarity for key, profile in _ESI_BY_POLARITY.items()
}


#: The reagent profiles whose runs read an organonitrate's carbon skeleton
#: (step 3.6): a nitrate source charges organonitrates as their nitrate
#: clusters, so its ledgers hold the dinitrates and nitroaromatics the windows
#: misjudge. Read on the reference engine's validation, which runs it for the
#: nitrate reagents alone.
NOX_SKELETON_PROFILES: frozenset[str] = frozenset({NO3.name, NO3_15N.name})

#: The instrument classes the skeleton reading runs on. An Orbitrap's window
#: keeps the reading's extra admissions to the formulas that fit its mass; a
#: TOF's would open them to candidate space the reading was never validated in.
NOX_SKELETON_INSTRUMENTS: frozenset[str] = frozenset({"orbi"})


def nox_skeleton_applies(
    profile: ReagentProfile, context: ChemistryContext, instrument_type: str | None
) -> bool:
    """Whether a run reads the carbon skeleton of an organonitrate.

    An -ONO2 or -NO2 group adds a nitrogen, two oxygens and one
    ring-or-double-bond, so a dinitrate, a trinitrate or a nitroaromatic sits
    outside Van Krevelen windows written for CHO skeletons
    (``heuristic_filter.nox_skeletons``). A run reads the skeleton where its
    reagent is nitrate (:data:`NOX_SKELETON_PROFILES`), on an Orbitrap
    (:data:`NOX_SKELETON_INSTRUMENTS`), in a context that makes organonitrates
    (:attr:`ChemistryContext.organonitrates`); a row the skeleton reading
    admits, and the formula's own reading does not, is a candidate at most.

    :param profile: The run's reagent profile.
    :param context: The run's chemistry context.
    :param instrument_type: The sample's instrument class.
    :return: True where the run reads the skeleton.
    """
    return (
        profile.name in NOX_SKELETON_PROFILES
        and context.organonitrates
        and (instrument_type or "").strip().lower() in NOX_SKELETON_INSTRUMENTS
    )


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
    instrument_type: str | None = None,
) -> ReagentProfile:
    """The profile a sample's ionization mechanisms identify.

    The mechanism panel is the fingerprint the deployment already maintains:
    a mode carrying ``[M+CH4N2O+H]+`` is a urea source whatever it is named,
    and one carrying ``[M+Br]-`` is a bromide source. On an Orbitrap, a mode
    with no reagent whose panel carries electron transfer (``[M]+.``, which is
    how that instrument's EASY-IC charge-transfer source is declared) is that
    source; on any other instrument electron transfer names an ambient-ion
    mode as readily, and is not read. Only when nothing is diagnostic does the
    polarity decide, and then the answer is the generic ESI preset - a broad
    grid and no matrix prior, because nothing was learned.

    A profile of the opposite polarity to the sample's is never the answer: a
    row stored under the wrong polarity, or a caller that did not filter the
    panel, must not turn a negative sample into a positive source.

    :param mechanism_notations: The mode's mechanism notations as the
        mechanism table stores them (``"[M+Br]-"``, ``"[M+CH4N2O+H]+"``). A
        legacy spelling (``"+Br-"``) is read as the same mechanism.
    :param polarity: The sample's polarity, ``"+"`` or ``"-"`` (the spelled-out
        forms are read too). A profile it contradicts is skipped, and it
        decides alone when no mechanism is diagnostic.
    :param instrument_type: The sample's instrument class (``"orbi"``,
        ``"tof"``), deciding whether electron transfer is read as the
        charge-transfer source.
    :return: The resolved profile; :data:`NO_PROFILE` when neither the
        mechanisms nor the polarity say anything, so an unrecognisable sample
        keeps the engine's historical behaviour rather than acquiring a grid
        nothing justifies.
    """
    seen = {mechanism_key(notation) for notation in mechanism_notations or ()}
    sign = _POLARITY_SIGN.get((polarity or "").strip().lower())
    charge_transfer = (
        instrument_type or ""
    ).strip().lower() in CHARGE_TRANSFER_INSTRUMENTS
    for profile in _DETECTION_ORDER:
        if sign is not None and profile.polarity and profile.polarity != sign:
            continue
        if profile.name in _CHARGE_TRANSFER_PROFILES and not charge_transfer:
            continue
        if seen.intersection(profile.detection):
            return profile
    return _ESI_BY_POLARITY.get((polarity or "").strip().lower(), NO_PROFILE)


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

    Carbon is floored at ``min_carbon`` where a profile sets a floor. None
    does since decision MAS-979: the floor was the last hard bound on a
    carbon-free formula, which is now searched, graded and capped - the
    carbon-free signature holds one at candidate unless a reference list
    names it (``implausibility``, ``formula_shape.exempt``).

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


def resolve_match_tolerance_ppm(instrument_type: str | None) -> float:
    """The window a predicted line is matched to a peak in, for this class.

    Not the search window (:func:`resolve_mz_precision_ppm`) and not the width
    a fit is judged at (:func:`resolve_fallback_sigma_ppm`); the three are
    named apart because they are three different statements, and on an
    Orbitrap they are 3, 5 and 0.3 ppm.

    :param instrument_type: ``"orbi"``, ``"tof"``, or None/unknown.
    :return: The matching window in ppm.
    """
    return INSTRUMENT_MATCH_TOLERANCE_PPM.get(
        (instrument_type or "").strip().lower(), DEFAULT_MATCH_TOLERANCE_PPM
    )


def resolve_fallback_sigma_ppm(instrument_type: str | None) -> float:
    """The mass-term width to score at when nothing has fitted the sample's own.

    What the fit score judges a mass error against where the run has fewer than
    :data:`mass_accuracy.MASS_ACCURACY_MIN_ANCHORS` known ions matched to fit a
    width from. Not the search window (:func:`resolve_mz_precision_ppm`), which
    is a different statement about a different thing. A property of the
    instrument and not of the chemistry, so no profile overrides it - a profile
    that wanted to would be saying the source changes what the analyser can
    resolve.

    Reached by :func:`mass_accuracy.scoring_sigma_ppm`, which is where a caller
    should put it: the fallback and a fitted width are widened the same way
    before either is scored against.

    :param instrument_type: ``"orbi"``, ``"tof"``, or None/unknown.
    :return: The width in ppm.
    """
    return INSTRUMENT_FALLBACK_SIGMA_PPM.get(
        (instrument_type or "").strip().lower(), DEFAULT_FALLBACK_SIGMA_PPM
    )
