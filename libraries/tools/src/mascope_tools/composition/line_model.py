"""The one line model: how an ion's isotope lines are predicted, found and checked.

Every reader of an ion's isotope lines asks the same four questions of a line:
is it expected (its predicted share clears the floor the reader predicts to), is
it present (a peak sits in its window), is its height within tolerance of its
share, and was it detectable (its expected signal-to-noise clears the run's
detectability threshold, so its absence is evidence). Before this module the
assignment pipeline answered them in about fourteen places, on two predictors
and two matchers (MAS-977). They are answered here, once:

* **The predictor.** :func:`predict_isotopes` gives an ion's fine structure, one
  line per isotopologue; :func:`predict_resolved_isotopes` gives the lines a
  file of its resolving power R(m/z) shows, the ones one peak cannot separate
  merged (:func:`resolve_lines`). The envelope match, the target library, the
  seeded chain, the chart and every check predict through them.
* **The matcher.** A line's peak is the nearest one inside the line's window
  (:func:`nearest_peaks`, :func:`nearest_peak`), and its height is tested at
  the run's fitted tolerance (:func:`within_intensity_window`).
* **The check.** :func:`line_detectable` (with :func:`detect_threshold`) says
  whether a line was detectable, :func:`height_within` whether a peak's height
  is within a reader's bounds of the line's share, :func:`height_deviation` how
  far outside the fitted band it sits in widths, :func:`tracks` whether its mass
  error follows its parent's, and :func:`check_line` asks the four questions at
  once. A reader that needs a different tolerance on purpose passes it as a
  named parameter; the constant it passes says why next to where it is defined.

In the assignment the match pass's ratio gate is this module's height test. A
line the gate turns away is absent to the fit score and its peak goes to the
search, so the gate decides which of Stage A's lines exist. Stage A and the
seeded re-score therefore gate their lines by :func:`within_intensity_window`
at the run's fitted tolerance, the test the finder admits a candidate's lines
by, in place of the match parameters' fixed ``isotope_ratio_tolerance``
(``peak_assignments.seeded_scoring.gate_lines``). The classic match keeps that
user-set tolerance: its results and their stored categories are what it
filters.

References: isotope fine structure, Lacki et al., Anal. Chem. 2017, 89, 3272
(IsoSpec); natural isotope abundances, Meija et al., Pure Appl. Chem. 2016, 88,
293; the isotope pattern as a filter on a formula, Kind & Fiehn, BMC
Bioinformatics 2007, 8, 105.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from IsoSpecPy import IsoDistribution, IsoThreshold, PeriodicTbl
from pyteomics.mass import Composition

from mascope_tools.composition.config import (
    ELECTRON_MASS,
    ENVELOPE_ABUNDANCE_FLOOR,
    HEIGHT_SUM_SPAN,
    ISOTOPE_MATCHING_INTENSITY_TOLERANCE,
    UNRESOLVED_MEMBER_DEPTH,
)
from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS, label_purity
from mascope_tools.composition.envelope_tolerance import (
    DEFAULT_TOLERANCE,
    DETECT_SNR_K,
    EnvelopeTolerance,
)
from mascope_tools.composition.utils import to_pyteomics


__all__ = [
    "DETECT_SNR_K",
    "HeightResolution",
    "LABELLED_REAGENT_PURITY",
    "LineCheck",
    "REL_DETECT_NO_SNR",
    "SHARE_WIDTH_FLOOR",
    "ResolvedLine",
    "TRACKING_WIDTHS",
    "anchor_on_monoisotopic",
    "check_line",
    "conf_to_label",
    "detect_threshold",
    "extract_isotope_labels",
    "fold_targets",
    "group_unresolved_lines",
    "height_deviation",
    "height_sum_span",
    "height_width",
    "height_within",
    "line_centre",
    "line_detectable",
    "line_groups",
    "merge_unresolved_lines",
    "monoisotopic_index",
    "nearest_peak",
    "nearest_peaks",
    "predict_isotopes",
    "predict_resolved_isotopes",
    "resolve_lines",
    "tracks",
    "within_intensity_window",
]


# --- The detectability threshold and the floors ------------------------------

# `DETECT_SNR_K`, the expected signal-to-noise at which a predicted line counts
# as one the spectrum should have shown, is `envelope_tolerance`'s, which this
# module imports; it is re-exported here, so the score, the predictor and the
# tolerance read one constant.


# Detectability threshold used when the base peak's SNR is unknown: an ABSENT predicted
# isotopologue at or above this fraction of the base peak is penalised regardless of SNR.
# Without a noise estimate the expected-SNR gate (p_i*s_0 >= k_detect) cannot be evaluated,
# and simply excluding every absent peak makes the score envelope-blind. Predicted
# envelopes run down to 1e-5 relative abundance (TARGET_LIBRARY_ABUNDANCE_FLOOR), so some
# threshold is mandatory; 0.10 is the ~1:10 dynamic range that any peak strong enough to be
# matched at all demonstrably exceeds, and it covers the diagnostic cases (Cl 32%, Br 97%,
# one 13C on C>=10). Below it, absence is uninformative without a noise estimate.
REL_DETECT_NO_SNR = 0.10


# --- Labelled-reagent custom elements ('^X' notation) ------------------------
# A labelled reagent atom is not 100% pure; e.g. 15N-nitrate is ~98% 15N / 2% 14N.
# We model it as a custom element '^X' whose isotope abundances are the labelled
# distribution, so predict_isotopes yields the heavy base AND the small light
# isotopologue. `purity` is the heaviest-isotope fraction (e.g. 0.98 for 15N).
# The element definitions come from the shared registry (custom_elements.py) --
# the single source of truth also used by the Mascope backend, so no molmass
# dependency and no duplicated isotope data.
LABELLED_REAGENT_PURITY = CUSTOM_ELEMENTS["^N"].default_purity


# symbol -> (regular_symbol, [(mass, mass_number), ... lightest first])
_CUSTOM_ELEMENT_DATA = {
    sym: (ce.base_element, list(ce.isotopes)) for sym, ce in CUSTOM_ELEMENTS.items()
}


def _custom_isotope_combinations(
    symbol: str, count: int, purity: float
) -> list[tuple[float, float, int]]:
    """Multinomial isotope combinations for `count` atoms of a labelled '^X'
    (two-isotope) element. Returns [(added_mass, probability, n_light), ...]."""
    from math import comb

    isos = _CUSTOM_ELEMENT_DATA[symbol][1]
    (m_light, _), (m_heavy, _) = isos[0], isos[-1]
    p_heavy, p_light = purity, 1.0 - purity
    out = []
    for k in range(count + 1):  # k = number of heavy (labelled) atoms
        n_light = count - k
        prob = comb(count, k) * p_heavy**k * p_light**n_light
        out.append((k * m_heavy + n_light * m_light, prob, n_light))
    return out


def _predict_isotopes_custom(
    ion_formula: str,
    ion_charge: int,
    purity: float,
    threshold: float = ENVELOPE_ABUNDANCE_FLOOR,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """predict_isotopes for an ion containing labelled '^X' custom elements: base
    (non-custom) envelope via IsoSpec, convolved with the labelled distribution(s)
    (multinomial; Cartesian product for several). Most-abundant peak first."""
    import itertools

    from mascope_tools.composition.utils import parse_composition, to_hill_order

    comp = parse_composition(ion_formula)
    customs = {s: int(comp[s]) for s in comp if s.startswith("^")}
    base = {s: int(comp[s]) for s in comp if not s.startswith("^") and comp[s] > 0}
    base_formula = to_hill_order(base) if base else ""

    if base_formula:
        peaks = IsoThreshold(formula=base_formula, threshold=threshold, get_confs=True)
        base_masses = [float(m) for m in peaks.masses]
        base_probs = [float(p) for p in peaks.probs]
        base_labels = extract_isotope_labels(base_formula, peaks)
    else:
        base_masses, base_probs, base_labels = [0.0], [1.0], ["M0"]

    combos_per_element = [
        [
            (
                mass,
                prob,
                n_light,
                _CUSTOM_ELEMENT_DATA[sym][0],
                _CUSTOM_ELEMENT_DATA[sym][1][0][1],
            )
            for (mass, prob, n_light) in _custom_isotope_combinations(sym, cnt, purity)
        ]
        for sym, cnt in customs.items()
    ]

    merged: dict[float, list] = {}
    for bm, bp, bl in zip(base_masses, base_probs, base_labels):
        for combo in itertools.product(*combos_per_element):
            mass = bm + sum(c[0] for c in combo)
            prob = bp
            for c in combo:
                prob *= c[1]
            if prob < threshold:
                continue
            deviations = [
                f"{light_mn}{regular}" + (str(n_light) if n_light > 1 else "")
                for (_, _, n_light, regular, light_mn) in combo
                if n_light
            ]
            parts = ([] if bl in ("M0", "", "---") else [bl]) + deviations
            label = "+".join(parts) if parts else "M0"
            key = round(mass, 4)
            if key in merged:
                m0, p0, _ = merged[key]
                tot = p0 + prob
                merged[key][0] = (m0 * p0 + mass * prob) / tot
                merged[key][1] = tot
            else:
                merged[key] = [mass, prob, label]

    items = sorted(merged.values(), key=lambda x: -x[1])  # most-abundant first
    mzs = np.array(
        [(m - ELECTRON_MASS * ion_charge) / abs(ion_charge) for m, _, _ in items]
    )
    probs = np.array([p for _, p, _ in items])
    labels = [lab for _, _, lab in items]
    return mzs, probs, labels


def predict_isotopes(
    ion_formula: str,
    ion_charge: int,
    purity: float | None = None,
    threshold: float | None = None,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Predict isotopic pattern for a given ion formula and charge.

    :param ion_formula: Ion formula string (may contain labelled '^X' elements).
    :type ion_formula: str
    :param ion_charge: Ion charge (e.g., +1, -1).
    :type ion_charge: int
    :param purity: Isotopic purity of labelled '^X' elements (heaviest-isotope
        fraction, e.g. 0.98 for a 98% 15N reagent). A property of the labelled
        reagent; the caller passes its value. Ignored for non-labelled ions.
        Defaults to the purity in effect (``custom_elements.label_purity``):
        the ionization setup's inside a run, else ``LABELLED_REAGENT_PURITY``.
    :type purity: float, optional
    :param threshold: Abundance below which a line is left out of the envelope,
        relative to the most abundant one. Defaults to
        :data:`ENVELOPE_ABUNDANCE_FLOOR`, which is what the scoring path
        wants: a line it cannot measure is noise in a cosine distance.

        A caller that CLAIMS peaks wants a lower one, because for it an omitted
        line is not a rounding error but a peak left in the residual for
        something else to explain. The reagent pre-pass passes its own isotopologue
        floor: the 18O line of a two-oxygen ion is 0.40% of the parent, under
        the 1% default, and on the gate that line was bright enough (7e4 counts,
        19th peak of the sample) for the untargeted stage to fit an analyte to
        once the reagent claimed its parent.
    :type threshold: float, optional
    :return: Tuple of predicted m/z values, relative intensities, and isotope labels.
    :rtype: tuple[np.ndarray, np.ndarray, list[str]]
    """
    cutoff = ENVELOPE_ABUNDANCE_FLOOR if threshold is None else threshold
    if "^" in ion_formula:
        try:
            return _predict_isotopes_custom(
                ion_formula,
                ion_charge,
                label_purity("^N") if purity is None else purity,
                cutoff,
            )
        except Exception:
            return [], [], []
    try:
        predicted_peaks = IsoThreshold(
            formula=ion_formula,
            threshold=cutoff,
            get_confs=True,
        )
        predicted_masses_neutral = np.fromiter(predicted_peaks.masses, dtype=float)
        predicted_intensities = np.fromiter(predicted_peaks.probs, dtype=float)
        isotope_labels = extract_isotope_labels(ion_formula, predicted_peaks)
        # Convert neutral masses to m/z
        predicted_mzs = (predicted_masses_neutral - ELECTRON_MASS * ion_charge) / abs(
            ion_charge
        )
    except Exception:
        predicted_mzs, predicted_intensities, isotope_labels = [], [], []

    return predicted_mzs, predicted_intensities, isotope_labels


def extract_isotope_labels(
    ion_formula: str, predicted_isotopes: IsoDistribution
) -> list[str]:
    """Convert isotope configurations to labels.
    Requires IsoDistribution with confs.

    Examples:
        >>> extract_isotope_labels(
        ...     "C6H12O6",
        ...     IsoThreshold(
        ...         formula="C6H12O6",
        ...         threshold=ENVELOPE_ABUNDANCE_FLOOR,
        ...         get_confs=True
        ...     )
        ... )
        ['M0', '13C', '18O']

    :param ion_formula: Ion formula string.
    :type ion_formula: str
    :param predicted_isotopes: Predicted isotope distribution.
    :type predicted_isotopes: IsoDistribution
    :return: List of isotope labels.
    :rtype: list[str]
    """
    # Remove the charge, its sign once per charge, for parsing
    ion_formula = ion_formula.rstrip("+-")
    try:
        composition = Composition(formula=to_pyteomics(ion_formula))
        elements = list(composition.keys())
        elemental_masses = [PeriodicTbl.symbol_to_masses[el] for el in elements]
        isotope_labels = [
            conf_to_label(conf, elements, elemental_masses)
            for conf in predicted_isotopes.confs
        ]
    except AttributeError:
        raise AttributeError(
            "Predicted isotopes must include configurations (confs) for label extraction."
        )
    return isotope_labels


def conf_to_label(conf, elements, isotope_masses):
    """Return isotope label string.

    :param conf: isotope counts for each element in the formula.
    :type conf: list[list[int]]
    :param elements: list of elements in the formula.
    :type elements: list[str]
    :param isotope_masses: list of isotope masses for each element.
    :type isotope_masses: list[list[float]]
    """
    label_parts = []
    for el, iso_counts, iso_masses in zip(elements, conf, isotope_masses):
        for idx, count in enumerate(iso_counts):
            if count == 0:
                continue

            # For the most abundant isotope (usually index 0), skip label unless it's the only one (M0)
            if idx == 0:
                continue

            mass_number = int(round(iso_masses[idx]))

            label_parts.append(f"{mass_number}{el}{count if count > 1 else ''}")

    if not label_parts:
        return "M0"
    return "+".join(label_parts)


def monoisotopic_index(predicted_mz, labels: Sequence[str]) -> int:
    """Which line of a predicted envelope is the ion itself.

    The line LABELLED ``M0``, not the first one and not the lightest one.
    IsoSpec orders configurations by abundance, so index 0 is the most abundant
    isotopologue and is the monoisotopic one only for an ion with no
    heavy-isotope-rich element; for a dibromide the most abundant line sits two
    mass units above the ion. And the lightest line is not it either: a 98% 15N
    reagent predicts its 14N impurity one mass unit BELOW the ion.

    :param predicted_mz: The envelope's m/z values, in the predictor's order.
    :param labels: The isotope label of each line, same order.
    :return: The position of the monoisotopic line, falling back to the
        lightest when nothing carries the label.
    """
    for position, label in enumerate(labels):
        if label == "M0":
            return position
    return int(np.argmin(predicted_mz))


def anchor_on_monoisotopic(
    predicted_mz, predicted_intensity, labels: Sequence[str]
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Reorder a predicted envelope so the ion's own line comes first.

    Three separate places downstream read index 0 as "the ion": the intensity
    an envelope is normalised to, :func:`score_pattern`'s
    "require monoisotopic detection" guard, and the finder's
    ``process_isotopes``, which writes its main row there. IsoSpec's order makes
    that true only by accident - for an ion with no heavy-isotope-rich element
    the most abundant configuration IS the monoisotopic one - and false exactly
    where it costs most.

    On a bromide source it cost bright peaks outright. A ``[M+Br2]-`` candidate is
    enumerated for a peak, its envelope is anchored on the 79Br81Br line two
    mass units above, that line is matched to whatever small peak sits there,
    and the target then measures thousands of percent too bright for its own
    monoisotopic line and goes unmatched - while the pattern still scores well
    enough to beat the mono-bromide reading that matched the target and its 13C
    line. The finder then wrote its main row at the anchor's m/z, and the peak
    the candidate was enumerated FOR got no row at all.

    Anchoring on the monoisotopic line removes the whole class, because that
    line is the target by construction: the composition search matched the
    ion's monoisotopic mass against the peak's m/z to propose it.

    :param predicted_mz: The envelope's m/z values.
    :param predicted_intensity: Their abundances, same order.
    :param labels: Their isotope labels, same order.
    :return: The three arrays with the monoisotopic line first, the rest in
        their original relative order.
    """
    predicted_mz = np.asarray(predicted_mz, dtype=float)
    predicted_intensity = np.asarray(predicted_intensity, dtype=float)
    labels = list(labels)
    if predicted_mz.size == 0:
        return predicted_mz, predicted_intensity, labels
    position = monoisotopic_index(predicted_mz, labels)
    if position == 0:
        return predicted_mz, predicted_intensity, labels
    order = [position] + [i for i in range(predicted_mz.size) if i != position]
    return (
        predicted_mz[order],
        predicted_intensity[order],
        [labels[i] for i in order],
    )


@dataclass(frozen=True)
class HeightResolution:
    """A file's resolving power, on a file whose intensity is a peak's height.

    An Orbitrap file's intensity is a peak's height, a TOF file's its area
    (``sample_inputs.load_sample_peaks`` in the backend). A peak's area holds
    every line under it, so a merged line on a TOF is its members' summed
    share at their abundance-weighted centre. A peak's height follows its
    dominant member: a member half a FWHM away sits on that member's flank and
    adds little to the apex (mascope#3338). So on a file read by heights a
    merged line is its dominant member, at that member's m/z, with that
    member's share and the shares of the members within :attr:`sum_span` of
    it. The members between :attr:`sum_span` and the group's span stay
    members of the line, so ownership and the claims still know the peak
    covers them, and add nothing to its predicted height.

    It reads the resolving power as the function it wraps does, so every
    reader that only asks for R(m/z) reads it unchanged; the line model reads
    the rest (:func:`height_sum_span`).

    :ivar resolution: The resolving power as a function of m/z.
    :ivar sum_span: How far from the dominant member a member still adds its
        share, in FWHM read at the dominant member
        (:data:`~mascope_tools.composition.config.HEIGHT_SUM_SPAN`).
    """

    resolution: Callable[[float], float]
    sum_span: float = HEIGHT_SUM_SPAN

    def __call__(self, mz: float) -> float:
        return self.resolution(mz)


def height_sum_span(resolution) -> float | None:
    """How far a merged line's members add to it on a file read by heights.

    :param resolution: A resolving power, a :class:`HeightResolution` or not.
    :return: Its :attr:`HeightResolution.sum_span`, or None for a file whose
        lines are read as areas: then a merged line is its members' sum.
    """
    if isinstance(resolution, HeightResolution):
        return float(resolution.sum_span)
    return None


def group_unresolved_lines(
    predicted_mz,
    predicted_intensity,
    resolution: Callable[[float], float],
    span: float = 0.5,
) -> list[np.ndarray]:
    """Group the predicted lines one peak of this width cannot separate.

    From the brightest line not yet taken, every line not yet taken within
    ``span`` FWHM either side of it (half by default) is one group, the FWHM
    read off the resolving power at that line. Starting from the brightest line rather than
    the lightest keeps a cluster whole: the 15N line sits 6 mDa under the 13C
    line, and a bin started there can take the 13C line and leave the 17O and
    2H lines, under 3 mDa from it, behind. A line whose resolving power or
    width cannot be read (not finite, not positive, an m/z at or under zero)
    is a group of its own.

    :param predicted_mz: The lines' m/z values.
    :param predicted_intensity: Their abundances, same order.
    :param resolution: The resolving power as a function of m/z.
    :param span: How far either side of its brightest line a group reaches, in
        FWHM.
    :return: One array of indices into the lines per group, the groups in the
        order their brightest lines were taken, each group's indices ascending.
    """
    predicted_mz = np.asarray(predicted_mz, dtype=float)
    predicted_intensity = np.asarray(predicted_intensity, dtype=float)
    # Brightest first: a peak sits on its dominant line, and which line
    # happens to be the lightest of a cluster (the 15N line, below 13C) must
    # not decide what the peak holds.
    order = np.lexsort((np.arange(predicted_mz.size), -predicted_intensity))
    taken = np.zeros(predicted_mz.size, dtype=bool)
    groups: list[np.ndarray] = []
    for brightest in order:
        if taken[brightest]:
            continue
        centre_mz = float(predicted_mz[brightest])
        try:
            resolving_power = float(resolution(centre_mz))
        except (TypeError, ValueError, ArithmeticError):
            resolving_power = float("nan")
        members = np.array([brightest])
        if np.isfinite(resolving_power) and resolving_power > 0:
            reach = centre_mz / resolving_power * span
            within = np.flatnonzero(~taken & (np.abs(predicted_mz - centre_mz) < reach))
            # A width at or under zero (an m/z at or under zero) takes no
            # line, not even its own.
            if within.size:
                members = within
        taken[members] = True
        groups.append(members)
    return groups


def _merged_line(
    members: np.ndarray,
    mzs: np.ndarray,
    intensities: np.ndarray,
    labels: Sequence[str],
    resolution=None,
) -> tuple[int, float, float]:
    """The one line a group of lines makes: the member that names it, its m/z
    and its abundance (:func:`_line_of`).

    The member that names it is the ion's own line, ``M0``, where it is one,
    else the brightest member.
    """
    centre, total = _line_of(members, mzs, intensities, resolution)
    own = [index for index in members if labels[index] == "M0"]
    named = (
        int(own[0])
        if own
        else int(np.asarray(members)[np.argmax(intensities[members])])
    )
    return named, centre, total


def merge_unresolved_lines(
    predicted_mz,
    predicted_intensity,
    labels: Sequence[str],
    resolution: Callable[[float], float] | None,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Merge the predicted lines one peak of the file's width cannot separate.

    The predictor returns the fine structure: one line per isotopologue. A peak
    on a TOF is far wider than that structure, so the one peak at the 13C
    position also holds the 15N, 2H and 17O lines a few mDa away, and scored
    against the 13C line alone the observed share reads high - by about 14% on a
    TOF gate set, where against the sum of the lines that peak holds the median
    was 1.03. An Orbitrap at low resolution does the same on fewer lines.

    A merged line is what that peak measures: its share is the sum of its
    members', its m/z their intensity-weighted centre. On a file read by
    heights (:class:`HeightResolution`, an Orbitrap) it is its dominant member,
    at that member's m/z, with the shares of the members close to it added
    (:func:`_line_of`). One peak holds, from the
    brightest line not yet taken, every line not yet taken within half the FWHM
    either side of it, the FWHM read off the file's resolving power at that
    line. Starting from the brightest line rather than the lightest keeps a
    cluster whole: the 15N line sits 6 mDa under the 13C line, and a bin
    started there can take the 13C line and leave the 17O and 2H lines, under
    3 mDa from it, behind. The targeted matcher's TOF library groups its
    lines the same way (:func:`group_unresolved_lines`, called by
    ``group_target_isotopes`` in the backend's target computation).

    The merged line keeps one label, because a row's label is parsed into its
    ion formula: ``M0`` where the ion's own line is a member, so the line
    :func:`anchor_on_monoisotopic` finds is still the ion's, else the brightest
    member's.

    :param predicted_mz: The envelope's m/z values, in the predictor's order.
    :param predicted_intensity: Their abundances, same order.
    :param labels: Their isotope labels, same order.
    :param resolution: The file's resolving power as a function of m/z, or None
        when the run could not read one; then nothing is merged.
    :return: The merged envelope, in the order of each line's labelled member
        in the input.
    """
    predicted_mz = np.asarray(predicted_mz, dtype=float)
    predicted_intensity = np.asarray(predicted_intensity, dtype=float)
    labels = list(labels)
    if resolution is None or predicted_mz.size < 2:
        return predicted_mz, predicted_intensity, labels

    merged = []
    for members in group_unresolved_lines(
        predicted_mz, predicted_intensity, resolution
    ):
        named, centre, total = _merged_line(
            members, predicted_mz, predicted_intensity, labels, resolution
        )
        merged.append((named, centre, total, labels[named]))
    merged.sort(key=lambda line: line[0])
    return (
        np.array([line[1] for line in merged], dtype=float),
        np.array([line[2] for line in merged], dtype=float),
        [line[3] for line in merged],
    )


def predict_resolved_isotopes(
    ion_formula: str,
    ion_charge: int,
    threshold: float,
    resolution: Callable[[float], float] | None,
    purity: float | None = None,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """An ion's envelope as a file of this resolving power shows it.

    :func:`predict_isotopes` with the lines one peak cannot separate merged
    (:func:`merge_unresolved_lines`). The members are predicted
    to :data:`UNRESOLVED_MEMBER_DEPTH` (a thousandth) of ``threshold``, because
    the lines a merged peak adds are each far under the floor - the 15N, 2H and
    17O lines of a TOF's 13C peak, fainter still for a small ion - and the
    floor is then applied to the merged lines,
    so the envelope holds the lines it held before, each with its whole share.
    Merged lines under the floor less than one FWHM from one another that
    together clear it are one kept line (:func:`resolve_lines`), and a
    merged line still under the floor less than one FWHM from a kept line is
    added to it rather than dropped (:func:`_fold_under_floor`): the three steps
    of :func:`resolve_lines`, which the target library resolves its lines by
    too.

    :param ion_formula: The ion's formula, no charge sign.
    :param ion_charge: Its charge.
    :param threshold: The envelope's floor, relative to its most abundant line.
    :param resolution: The file's resolving power as a function of m/z, or None,
        which predicts exactly as :func:`predict_isotopes` does.
    :param purity: A labelled reagent's isotopic purity, as
        :func:`predict_isotopes` takes it.
    :return: The envelope's m/z values, abundances and labels.
    """
    if resolution is None:
        return predict_isotopes(ion_formula, ion_charge, purity, threshold)
    lines = resolve_lines(
        *predict_isotopes(
            ion_formula, ion_charge, purity, threshold * UNRESOLVED_MEMBER_DEPTH
        ),
        resolution,
        threshold,
    )
    return (
        np.array([line.mz for line in lines], dtype=float),
        np.array([line.abundance for line in lines], dtype=float),
        [line.label for line in lines],
    )


def fold_targets(
    mzs,
    keep,
    resolution: Callable[[float], float],
) -> np.ndarray:
    """The kept line each line under the floor folds into, or -1.

    A line not kept goes to the nearest kept line less than one FWHM away, the
    FWHM read off the resolving power at that kept line; a line with none so
    near, and every kept line, gets -1. Distances are read against the kept
    lines as given, so the order the lines are folded in decides nothing. The
    envelope's floor (:func:`_fold_under_floor`) and the targeted matcher's
    TOF library (``group_target_isotopes`` in the backend's target
    computation) fold by it.

    :param mzs: The merged lines' m/z values.
    :param keep: Which of them are over the floor, same order.
    :param resolution: The resolving power as a function of m/z.
    :return: One index into the lines per line, -1 where nothing is folded.
    """
    mzs = np.asarray(mzs, dtype=float)
    keep = np.asarray(keep, dtype=bool)
    targets = np.full(mzs.size, -1, dtype=int)
    kept = np.flatnonzero(keep)
    dropped = np.flatnonzero(~keep)
    if not kept.size or not dropped.size:
        return targets
    fwhm = np.full(kept.size, np.nan)
    for position, index in enumerate(kept):
        try:
            resolving_power = float(resolution(float(mzs[index])))
        except (TypeError, ValueError, ArithmeticError):
            continue
        if np.isfinite(resolving_power) and resolving_power > 0:
            fwhm[position] = float(mzs[index]) / resolving_power
    for index in dropped:
        distance = np.abs(mzs[kept] - mzs[index])
        within = np.flatnonzero(distance < fwhm)
        if within.size:
            targets[index] = int(kept[within[np.argmin(distance[within])]])
    return targets


def _fold_under_floor(
    mzs: np.ndarray,
    intensities: np.ndarray,
    keep: np.ndarray,
    resolution: Callable[[float], float],
) -> tuple[np.ndarray, np.ndarray]:
    """Fold each merged line under the floor into a kept line it cannot leave.

    The fold step of :func:`resolve_lines`, on the merged lines alone.
    :func:`merge_unresolved_lines` bins at half a FWHM from the brightest line,
    so a line between half and one FWHM from a kept line is a bin of its own,
    and under the floor it would be dropped - though one peak of that width
    does not separate it from the kept line (mascope#2591: the 15N line of
    C4H8NO+ at a resolving power of 8000, 6.3 mDa from the 13C line where half
    a FWHM is 5.4 mDa). Such a line is added to the nearest kept line less than
    one FWHM away, read at that kept line, which then sits at their
    abundance-weighted centre. Distances are read against the kept lines as
    merged, so the order the lines are folded in decides nothing. Which lines
    are kept does not change: folding only adds to a kept line's share.

    :return: The m/z values and abundances, same length and order; only the
        kept lines' entries change.
    """
    targets = fold_targets(mzs, keep, resolution)
    weighted = mzs * intensities
    totals = intensities.copy()
    for index in np.flatnonzero(targets >= 0):
        nearest = int(targets[index])
        weighted[nearest] += mzs[index] * intensities[index]
        totals[nearest] += intensities[index]
    kept = np.flatnonzero(keep)
    folded_mzs = mzs.copy()
    folded = kept[totals[kept] > intensities[kept]]
    folded_mzs[folded] = weighted[folded] / totals[folded]
    return folded_mzs, totals


def nearest_peaks(
    mzs: np.ndarray, predicted: np.ndarray, tolerance_ppm: float
) -> tuple[np.ndarray, np.ndarray]:
    """The peak each predicted line matches: the nearest one inside its window.

    :func:`score_ion_envelope`'s ``argmin`` over each line's window, for every
    line at once. Inside a window the nearest peak is the last one below the
    line or the first one at or above it, and a tie goes to the one below, as
    ``argmin`` gives it the first index; among equal m/z values it is the first
    of them. Distances within a window are differences of numbers within a
    factor of two of each other, which floating point computes exactly, so the
    comparison picks the peak ``argmin`` does.

    :param mzs: The peak list's m/z values, sorted ascending.
    :param predicted: The predicted lines' m/z values.
    :param tolerance_ppm: The match window, either side.
    :return: Whether each line's window holds a peak, and that peak's index
        (a valid index, but no match, where it holds none).
    """
    delta = predicted * tolerance_ppm * 1e-6
    start = np.searchsorted(mzs, predicted - delta, side="left")
    end = np.searchsorted(mzs, predicted + delta, side="right")
    found = start < end
    if not mzs.size:
        return found, np.zeros(predicted.size, dtype=np.intp)
    # The window starts below its line, so the first peak at or above the line
    # is never before the window's first one.
    upper = np.searchsorted(mzs, predicted, side="left")
    has_upper = upper < end
    lower = np.minimum(upper - 1, end - 1)
    has_lower = lower >= start
    last = mzs.size - 1
    lower = np.maximum(
        np.searchsorted(mzs, mzs[np.clip(lower, 0, last)], side="left"), start
    )
    upper_distance = np.abs(mzs[np.clip(upper, 0, last)] - predicted)
    lower_distance = np.abs(mzs[np.clip(lower, 0, last)] - predicted)
    take_lower = has_lower & (~has_upper | (lower_distance <= upper_distance))
    return found, np.clip(np.where(take_lower, lower, upper), 0, last)


def within_intensity_window(
    intensity_error,
    expected_snr,
    tolerance: EnvelopeTolerance | None,
    by_presence: np.ndarray | None = None,
) -> np.ndarray:
    """Whether an isotopologue's height is close enough to its share to be the
    ion's line at all.

    The window is :data:`ISOTOPE_MATCHING_INTENSITY_TOLERANCE` either side of
    the predicted share. On a file whose fitted response curve opens lower
    (:mod:`envelope_tolerance`), the window opens down to the curve's lower end
    at the line's expected S/N: a dim line censored scan by scan reads below
    its share, and the instrument genuinely delivers it there, so turning it
    away would hand the score an absent line to charge (step 5.3 measured a
    median 0.44 of the share at an expected S/N of 10 to 50). The upper end
    stays: a line read well above its share is the guard against an unrelated
    peak, and a curve says nothing about lines read high. A line whose expected
    S/N is unknown, and a file with no curve, keep the fixed window. So does a
    line read by its scan presence (:mod:`scan_presence`): its share is read at
    its mean per scan, the dilution the curve allows for taken out, as the fit
    score's band holds it to the share itself (:func:`height_deviation`).

    :param intensity_error: Each line's observed / predicted share minus one.
    :param expected_snr: Each line's expected S/N: the anchor's S/N times the
        line's predicted share; NaN where the anchor carries none.
    :param tolerance: The run's fitted tolerance, or None for today's.
    :param by_presence: Per line, whether its share was read by its scan
        presence (:func:`scan_presence.presence_corrected`), or None for none.
    :return: Per line, whether it is kept.
    """
    tolerance = tolerance or DEFAULT_TOLERANCE
    error = np.asarray(intensity_error, dtype=float)
    lower, upper = tolerance.admitted(expected_snr)
    if by_presence is not None:
        lower = np.where(by_presence, upper, lower)
    low = np.minimum(1.0 - ISOTOPE_MATCHING_INTENSITY_TOLERANCE, lower) - 1.0
    return (error >= low) & (error <= ISOTOPE_MATCHING_INTENSITY_TOLERANCE)


# --- The resolved lines, with their members -----------------------------------


@dataclass(frozen=True)
class ResolvedLine:
    """One line a peak of the file's width shows.

    :ivar mz: The members' abundance-weighted centre; on a file read by
        heights (:class:`HeightResolution`) the dominant member's m/z.
    :ivar abundance: Their summed abundance; on a file read by heights the
        dominant member's and those of the members close to it
        (:func:`_line_of`).
    :ivar label: The label of the member that names the line: the ion's own
        line, ``M0``, where it is one, else the brightest member of the group
        the line started as.
    :ivar members: The fine-structure lines it holds, as indices into the
        predictor's lines, ascending.
    """

    mz: float
    abundance: float
    label: str
    members: tuple[int, ...]


def _line_of(
    members: Sequence[int],
    mzs: np.ndarray,
    intensities: np.ndarray,
    resolution=None,
) -> tuple[float, float]:
    """The centre and the abundance of the line a set of fine lines makes.

    On a file read by areas (a TOF, or no :class:`HeightResolution`) the
    members' abundance-weighted centre and their summed abundance: a peak's
    area holds every line under it. On a file read by heights (an Orbitrap) the
    dominant member's m/z, and its abundance plus that of every member less than
    :attr:`HeightResolution.sum_span` FWHM from it, the FWHM read at the
    dominant member: the further members sit on its flank and add little to
    the apex (mascope#3338). Where the resolving power cannot be read there,
    the dominant member alone. The brightest member is the dominant one, the
    first of them on a tie.
    """
    members = np.asarray(members, dtype=int)
    weights = intensities[members]
    sum_span = height_sum_span(resolution)
    if sum_span is not None and members.size > 1:
        dominant = int(members[np.argmax(weights)])
        centre = float(mzs[dominant])
        try:
            resolving_power = float(resolution(centre))
        except (TypeError, ValueError, ArithmeticError):
            resolving_power = float("nan")
        # A NaN reads False here; an infinite one reaches no other member.
        if resolving_power > 0:
            reach = centre / resolving_power * sum_span
            adds = np.abs(mzs[members] - centre) < reach
            adds[members == dominant] = True
            return centre, float(weights[adds].sum())
        return centre, float(intensities[dominant])
    total = float(weights.sum())
    centre = (
        float(np.sum(mzs[members] * weights) / total)
        if total > 0
        else float(mzs[members].mean())
    )
    return centre, total


def line_centre(mzs, intensities, members: Sequence[int]) -> float:
    """Where the line a set of fine lines makes sits: their abundance-weighted
    centre, or their mean where they carry no abundance."""
    return _line_of(
        members, np.asarray(mzs, dtype=float), np.asarray(intensities, dtype=float)
    )[0]


def line_groups(
    mzs, intensities, resolution: float | Callable[[float], float] | None
) -> list[list[int]]:
    """The fine lines one peak of the file's width holds, as index lists.

    The first step of :func:`resolve_lines` alone (:func:`group_unresolved_lines`)
    for a reader that reads a group's members, not the floored envelope: the
    batch's diagnostic and merged-row readers. Without a usable resolving power
    each line is a group of its own.

    :param mzs: The fine lines' m/z values.
    :param intensities: Their abundances, same order.
    :param resolution: The resolving power, a number or a function of m/z, or
        None.
    :return: One index list per group.
    """
    mzs = np.asarray(mzs, dtype=float)
    if resolution is None or mzs.size < 2:
        return [[index] for index in range(mzs.size)]
    if not callable(resolution):
        if not resolution > 0:
            return [[index] for index in range(mzs.size)]
        resolution = lambda _mz, power=float(resolution): power  # noqa: E731
    return [
        list(map(int, members))
        for members in group_unresolved_lines(
            mzs, np.asarray(intensities, dtype=float), resolution
        )
    ]


def _join_under_floor(fine, lines, resolution, level):
    """Step 2 of :func:`resolve_lines`: lines under the floor less than one
    FWHM from one another that together clear it are one line over it.

    On a file read by areas the lines' shares add; on one read by heights
    (:class:`HeightResolution`) only where their members sit close enough to
    add at the apex, so the joined line is read as a line of all their
    members is (:func:`_line_of`). The area's sum is taken as it was, so a
    TOF's lines are what they were to the last bit.

    :param fine: The fine lines' m/z values and abundances, and the label of
        each line's naming member.
    :param lines: Per line, its naming member, its members, whether it is kept,
        its centre and its abundance.
    :param resolution: The resolving power.
    :param level: The floor, as an abundance.
    :return: The naming members, the members and whether each is kept, of the
        lines left once the joined ones are taken into their heads.
    """
    mzs, intensities, line_labels = fine
    names, members, keep, centres, totals = lines
    under = np.flatnonzero(~keep)
    absorbed = np.zeros(len(members), dtype=bool)
    for group in group_unresolved_lines(
        centres[under], totals[under], resolution, span=1.0
    ):
        together = under[group]
        if height_sum_span(resolution) is None:
            joined = float(totals[together].sum())
        else:
            joined = _line_of(
                [member for index in together for member in members[index]],
                mzs,
                intensities,
                resolution,
            )[1]
        if joined < level:
            continue
        head, _, _ = _merged_line(together, centres, totals, line_labels)
        for index in together:
            if index != head:
                members[head].extend(members[index])
                absorbed[index] = True
        members[head].sort()
        keep[head] = True
    stays = np.flatnonzero(~absorbed)
    return (
        [names[index] for index in stays],
        [members[index] for index in stays],
        keep[stays],
    )


def resolve_lines(
    predicted_mz,
    predicted_intensity,
    labels: Sequence[str],
    resolution: Callable[[float], float] | None,
    floor: float,
    *,
    keep_under_floor: bool = False,
) -> list[ResolvedLine]:
    """The lines a file of this resolving power shows, from an ion's fine structure.

    Three steps, each read off the resolving power at the line it starts from:

    1. One peak holds, from the brightest line not yet taken, every line not
       yet taken within half a FWHM either side (:func:`group_unresolved_lines`,
       :func:`merge_unresolved_lines`). On a file read by heights
       (:class:`HeightResolution`, an Orbitrap's) a line is read as its
       dominant member, on any other as its members' sum (:func:`_line_of`).
    2. Lines under the floor less than one FWHM from one another that together
       clear it are one line over it (mascope#3027;
       the target library's lines too since MAS-977, mascope#3163).
    3. A line still under the floor less than one FWHM from a line over it is
       added to that line (:func:`_fold_under_floor`, mascope#2591).

    The envelope keeps the lines over the floor alone
    (:func:`predict_resolved_isotopes`). The target library, which stores every
    line and leaves the floor to its readers, also keeps the lines under it
    that nothing took (``keep_under_floor``).

    :param predicted_mz: The fine lines' m/z values, in the predictor's order.
    :param predicted_intensity: Their abundances, same order.
    :param labels: Their labels, same order.
    :param resolution: The resolving power as a function of m/z, or None, which
        merges nothing: each fine line is a line of its own.
    :param floor: The envelope's floor, relative to the most abundant line.
    :param keep_under_floor: Whether a line under the floor that no line over
        it took is kept.
    :return: The lines, in the order of each line's naming member in the input.
    """
    mzs = np.asarray(predicted_mz, dtype=float)
    intensities = np.asarray(predicted_intensity, dtype=float)
    labels = list(labels)
    if not mzs.size:
        return []
    if resolution is None:
        groups = [np.array([index]) for index in range(mzs.size)]
    else:
        groups = group_unresolved_lines(mzs, intensities, resolution)
    named = []
    for members in groups:
        own, _, _ = _merged_line(members, mzs, intensities, labels)
        named.append((own, sorted(int(index) for index in members)))
    named.sort(key=lambda line: line[0])
    names = [own for own, _ in named]
    members = [list(group) for _, group in named]

    def arrays() -> tuple[np.ndarray, np.ndarray]:
        pairs = [_line_of(group, mzs, intensities, resolution) for group in members]
        return (
            np.array([centre for centre, _ in pairs], dtype=float),
            np.array([total for _, total in pairs], dtype=float),
        )

    centres, totals = arrays()
    level = floor * float(totals.max())
    keep = totals >= level
    if resolution is not None:
        under = np.flatnonzero(~keep)
        if under.size and float(totals[under].sum()) >= level:
            names, members, keep = _join_under_floor(
                (mzs, intensities, [labels[own] for own in names]),
                (names, members, keep, centres, totals),
                resolution,
                level,
            )
            centres, totals = arrays()
        targets = fold_targets(centres, keep, resolution)
        for index in np.flatnonzero(targets >= 0):
            members[int(targets[index])].extend(members[index])
        for group in members:
            group.sort()
    else:
        targets = np.full(len(members), -1, dtype=int)
    lines = []
    for index, group in enumerate(members):
        if not keep[index] and (targets[index] >= 0 or not keep_under_floor):
            continue
        centre, total = _line_of(group, mzs, intensities, resolution)
        lines.append(ResolvedLine(centre, total, labels[names[index]], tuple(group)))
    return lines


# --- The matcher ---------------------------------------------------------------


def nearest_peak(
    mzs: np.ndarray,
    line_mz: float,
    tolerance_ppm: float,
    *,
    skip: frozenset[int] | set[int] = frozenset(),
) -> int | None:
    """The peak a line matches: the nearest one inside its window, or None.

    :func:`nearest_peaks` for one line; the peaks need not be sorted. A tie
    goes to the first of them in the list, the lighter one in a sorted list,
    as :func:`nearest_peaks` gives it.

    :param mzs: The peak list's m/z values.
    :param line_mz: Where the line is predicted, on the axis the peaks are read
        on.
    :param tolerance_ppm: The match window, either side.
    :param skip: Peaks another line or reading has already taken, which this
        line cannot take.
    :return: The peak's index, or None where the window holds none.
    """
    mzs = np.asarray(mzs, dtype=float)
    distance = np.abs(mzs - float(line_mz))
    within = np.flatnonzero(distance <= float(line_mz) * tolerance_ppm * 1e-6)
    if skip:
        within = np.array([index for index in within if int(index) not in skip])
    if not within.size:
        return None
    return int(within[np.argmin(distance[within])])


# --- The check -----------------------------------------------------------------

#: How many of a line's mass widths its mass error may sit from its parent's and
#: still be read as following it: a line of the ion rather than a peak that
#: happens to sit at its mass. Three, because the quantity tested is a
#: DIFFERENCE of two measurements and this is a three sigma test on it: the
#: difference is about as wide as the Orbitrap class's precision on the sets
#: that have real envelopes (0.28 to 0.45 ppm against a 0.3 ppm class), so
#: testing at the bare precision is a two-thirds-of-one-sigma test and throws
#: away a third to a half of the genuine children. Measured on the gate, the
#: share of children kept at one, two and three times the precision: A 59, 84,
#: 91%; B 66, 88, 95%; C 67, 78, 81%; C2 66, 82, 90%; D 50, 74, 84%. The mass
#: gate's corroboration (``mass_gate.tracking_of``) and the finder's lines a
#: rival must explain (``finder._tracking_lines``) both read it; the finder read
#: two widths of its own until MAS-977, for the same question.
TRACKING_WIDTHS = 3.0


def detect_threshold(tolerance: EnvelopeTolerance | None) -> float:
    """The expected signal-to-noise from which a predicted line was detectable.

    The run's fitted threshold where its response curve set one (step 3.5b,
    :attr:`EnvelopeTolerance.detect_snr`), else :data:`DETECT_SNR_K`.
    """
    if tolerance is not None and tolerance.detect_snr is not None:
        return float(tolerance.detect_snr)
    return float(DETECT_SNR_K)


def line_detectable(
    share,
    base_snr,
    *,
    tolerance: EnvelopeTolerance | None = None,
    detect_snr: float | None = None,
    rel_detect_no_snr: float = REL_DETECT_NO_SNR,
):
    """Whether a predicted line was detectable, so its absence is evidence.

    Its expected signal-to-noise, the anchor's times the line's share of it,
    at or above the run's threshold (:func:`detect_threshold`, or
    ``detect_snr`` where the caller recorded the one it asked at); where the
    anchor carries no usable signal-to-noise, a share of at least
    ``rel_detect_no_snr``.

    :param share: The line's predicted share of the anchor; scalar or array.
    :param base_snr: The anchor's signal-to-noise, or None / NaN / not positive
        for none; an array gives each line its own anchor's.
    :param tolerance: The run's fitted envelope tolerance, or None.
    :param detect_snr: The threshold to ask at, overriding the tolerance's.
    :param rel_detect_no_snr: The share asked without a signal-to-noise.
    :return: Per line, whether it was detectable; a bool for a scalar share.
    """
    threshold = detect_threshold(tolerance) if detect_snr is None else detect_snr
    shares = np.asarray(share, dtype=float)
    snr = np.asarray(np.nan if base_snr is None else base_snr, dtype=float)
    known = np.isfinite(snr) & (snr > 0)
    with np.errstate(invalid="ignore"):
        answer = np.where(known, shares * snr >= threshold, shares >= rel_detect_no_snr)
    return bool(answer) if answer.ndim == 0 else answer


def height_within(
    observed: float,
    expected: float,
    *,
    at_most: float | None = None,
    at_least: float | None = None,
) -> bool:
    """Whether a peak's height is within a reader's bounds of a line's.

    The bounds are factors of the expected height: ``at_most`` 2 reads a peak
    up to twice the line as that line, ``at_least`` 0.5 down to half of it.
    Each reader passes the bounds it needs, named where they are defined with
    why they differ from the fitted band (:func:`height_deviation`).

    :param observed: The peak's height, or its observed share.
    :param expected: The line's predicted height, or share, on the same scale.
    :param at_most: The largest factor admitted, or None for no ceiling.
    :param at_least: The smallest factor admitted, or None for no floor.
    :return: Whether the height is within both bounds.
    """
    if at_most is not None and observed > at_most * expected:
        return False
    if at_least is not None and observed < at_least * expected:
        return False
    return True


#: The narrowest a line's height is ever judged, as a share of its anchor: a
#: dim line's noise and the tolerance's floor of its share can both run to
#: nothing, and the fit score and the batch's ratio readers would then judge
#: it against no width at all.
SHARE_WIDTH_FLOOR = 1e-3


def height_width(
    observed,
    expected,
    *,
    tolerance: EnvelopeTolerance | None = None,
    inverse_snr=0.0,
    width_floor: float = SHARE_WIDTH_FLOOR,
):
    """How wide a line's observed share is read, as a share of its anchor.

    Its noise (the observed share times ``inverse_snr``), the tolerance's floor
    of the predicted share, or ``width_floor``, whichever is widest: the width
    :func:`height_deviation` counts a share's distance from the band in.

    :return: The width per line (a float for scalars).
    """
    tolerance = tolerance or DEFAULT_TOLERANCE
    width = np.maximum.reduce(
        [
            np.asarray(observed, dtype=float) * np.asarray(inverse_snr, dtype=float),
            tolerance.floor * np.asarray(expected, dtype=float),
            np.full(np.broadcast(observed, expected).shape, width_floor),
        ]
    )
    return float(width) if np.ndim(width) == 0 else width


def height_deviation(
    observed,
    expected,
    expected_snr,
    *,
    tolerance: EnvelopeTolerance | None = None,
    inverse_snr=0.0,
    by_presence=None,
    width_floor: float = SHARE_WIDTH_FLOOR,
):
    """How far a line's observed share sits outside the band the run admits, in
    widths; zero inside.

    The band is :meth:`EnvelopeTolerance.admitted` at the line's expected
    signal-to-noise, in units of the predicted share (a line read by its scan
    presence is held to the share itself, ``by_presence``). The width is the
    observed share's noise (``inverse_snr``, the line's and its anchor's
    inverse signal-to-noise in quadrature), the tolerance's floor of the
    predicted share, or ``width_floor``, whichever is widest. The fit score
    charges a matched line ``exp(-deviation**2 / 2)``; the batch's ratio
    readers hold a line within a number of widths
    (``diagnostic_isotopologues.HOLD_WIDTHS``).

    :param observed: Each line's observed share of its anchor.
    :param expected: Each line's predicted share.
    :param expected_snr: Each line's expected signal-to-noise, NaN where unknown.
    :param tolerance: The run's fitted envelope tolerance, or None for today's.
    :param inverse_snr: The observed share's relative noise.
    :param by_presence: Per line, whether its share was read by its scan
        presence, or None for none.
    :param width_floor: The narrowest width, as a share of the anchor.
    :return: The signed deviation per line, an array (a float for scalars).
    """
    tolerance = tolerance or DEFAULT_TOLERANCE
    observed = np.asarray(observed, dtype=float)
    expected = np.asarray(expected, dtype=float)
    lower, upper = tolerance.admitted(np.atleast_1d(np.asarray(expected_snr, float)))
    if by_presence is not None:
        lower = np.where(by_presence, upper, lower)
    low, high = lower * expected, upper * expected
    outside = np.where(
        observed < low, observed - low, np.where(observed > high, observed - high, 0.0)
    )
    width = np.broadcast_to(
        height_width(
            observed,
            expected,
            tolerance=tolerance,
            inverse_snr=inverse_snr,
            width_floor=width_floor,
        ),
        np.shape(outside),
    )
    deviation = outside / width
    if np.ndim(observed) == 0 and np.ndim(expected) == 0:
        return float(np.asarray(deviation).reshape(-1)[0])
    return deviation


def tracks(
    child_error_ppm,
    parent_error_ppm,
    width_ppm: float,
    *,
    widths: float = TRACKING_WIDTHS,
) -> bool:
    """Whether a line's mass error follows its parent's.

    :param child_error_ppm: The line's mass error.
    :param parent_error_ppm: Its parent's.
    :param width_ppm: The width the difference is read in: the instrument
        class's precision, or the run's fitted width.
    :param widths: How many widths it may differ by (:data:`TRACKING_WIDTHS`).
    :return: Whether both are finite and differ by no more than the bar.
    """
    try:
        child, parent = float(child_error_ppm), float(parent_error_ppm)
    except (TypeError, ValueError):
        return False
    if not (np.isfinite(child) and np.isfinite(parent)):
        return False
    return abs(child - parent) <= widths * float(width_ppm)


@dataclass(frozen=True)
class LineCheck:
    """The four answers about one predicted line.

    :ivar expected: Its predicted share clears the floor the reader predicts to.
    :ivar present: A peak sits in its window.
    :ivar within: That peak's height is within the reader's bounds of the line's
        (False where no peak is present).
    :ivar detectable: Its expected signal-to-noise clears the run's threshold,
        so its absence is evidence.
    :ivar peak: The peak's index, or None.
    """

    expected: bool
    present: bool
    within: bool
    detectable: bool
    peak: int | None = None

    @property
    def missing(self) -> bool:
        """Expected and detectable, and no peak holds it."""
        return self.expected and self.detectable and not self.present


def check_line(
    line_mz: float,
    share: float,
    mzs: np.ndarray,
    intensities: np.ndarray,
    *,
    anchor_intensity: float,
    anchor_snr: float | None,
    tolerance_ppm: float,
    floor: float = ENVELOPE_ABUNDANCE_FLOOR,
    tolerance: EnvelopeTolerance | None = None,
    detect_snr: float | None = None,
    rel_detect_no_snr: float = REL_DETECT_NO_SNR,
    at_most: float | None = None,
    at_least: float | None = None,
) -> LineCheck:
    """Ask one predicted line the four questions.

    :param line_mz: Where the line is predicted, on the peaks' axis.
    :param share: Its predicted share of the anchor.
    :param mzs: The peaks' m/z values, sorted ascending.
    :param intensities: Their heights, same order.
    :param anchor_intensity: The anchor's observed height.
    :param anchor_snr: The anchor's signal-to-noise, or None.
    :param tolerance_ppm: The match window, either side.
    :param floor: The share under which a line is not expected.
    :param tolerance: The run's fitted envelope tolerance, for its threshold.
    :param detect_snr: The detectability threshold, overriding the tolerance's.
    :param rel_detect_no_snr: The share asked without a signal-to-noise.
    :param at_most: The height bound above, a factor of the line's height.
    :param at_least: The height bound below, a factor of the line's height.
    :return: The answers.
    """
    peak = nearest_peak(mzs, line_mz, tolerance_ppm)
    expected_height = float(share) * float(anchor_intensity)
    within = peak is not None and height_within(
        float(intensities[peak]), expected_height, at_most=at_most, at_least=at_least
    )
    return LineCheck(
        expected=float(share) >= floor,
        present=peak is not None,
        within=within,
        detectable=line_detectable(
            share,
            anchor_snr,
            tolerance=tolerance,
            detect_snr=detect_snr,
            rel_detect_no_snr=rel_detect_no_snr,
        ),
        peak=peak,
    )
