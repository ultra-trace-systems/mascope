"""
Pure transformation logic for peak-centric assignment.

Inverts target-anchored match results (one row per target isotope) into
peak-anchored assignments (one row per observed peak), applies the
single-owner-per-peak arbitration, and maps untargeted composition-finder
results onto the same row shape.

No database access happens here - everything is DataFrame/dict manipulation
so the arbitration logic stays unit-testable. The service layer owns
persistence.
"""

import itertools
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from mascope_backend.api.controllers.match.lib.match_score_v2 import ion_scores_v2
from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    fit_isotope_names,
)
from mascope_backend.api.new.peak_assignments.monoisotopic import (
    monoisotopic_positions,
    monoisotopic_row,
)
from mascope_backend.api.new.peak_assignments.row_terms import (
    REFERENCE_IDENTITIES_COL,
    ROLE_ISO_CHILD,
    ROLE_M0,
    ROLE_UNASSIGNED,
    SOURCE_DATABASE,
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_BELOW_ASSIGNABILITY,
    TIER_CANDIDATE,
    TIER_RANK,
    TIER_UNASSIGNED,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_tools.composition.arbitration import (
    CANDIDATE_DENSITY,
    arbitrate_candidates,
    density_of,
)
from mascope_tools.composition.calibration import (
    Calibration,
    apply_calibration,
    apply_corroboration,
    calibration_for,
    corroboration_by_mechanism,
)
from mascope_tools.composition.envelope_tolerance import EnvelopeTolerance
from mascope_tools.composition.finder import (
    HELD_BY_LIBRARY,
    HELD_BY_LINES,
    KNOWN_DISPLACED,
    KNOWN_KEPT,
    KNOWN_RIVALS,
    ListReading,
    ReadingRivals,
)
from mascope_tools.composition.heuristic_filter import (
    SCORE_VERSION,
    element_counts,
    formula_plausibility,
    propose_same_ion_readings,
)
from mascope_tools.composition.line_model import within_intensity_window
from mascope_tools.composition.mass_accuracy import (
    MASS_ACCURACY_MIN_ANCHORS,
    ReagentOffset,
    SampleMassAccuracy,
    fit_sample_mass_accuracy,
    mass_accuracy_anchors,
)
from mascope_tools.composition.measurement_model import MeasurementModel
from mascope_tools.composition.models import (
    CompositionSearchConfig,
    HeuristicFilterConfig,
    PatternScoring,
)
from mascope_tools.composition.scoring import pattern_scoring_for_model
from mascope_tools.composition.utils import (
    formula_identity,
    ion_identity,
)


# Sentinel so a caller can pass calibration=None (explicitly uncalibrated) distinctly from
# "not provided" (fall back to the in-code registry via calibration_for).
_CALIBRATION_UNSET = object()


def untargeted_targets(
    eligible: pd.DataFrame,
    max_untargeted_peaks: int | None,
    ceiling: int,
) -> tuple[pd.DataFrame, dict]:
    """The peaks the untargeted stage will enumerate, and what it left behind.

    Every unexplained peak is eligible; the cap decides how many of them are
    searched, brightest first. Unset, that is all of them up to the ceiling -
    which exists so "all" cannot mean unbounded work, not because some number of
    peaks is the right number to look at.

    :param eligible: The unassigned peaks above the run's intensity threshold,
        with an ``intensity`` column.
    :param max_untargeted_peaks: The run's cap, or None for every peak.
    :param ceiling: The hard bound a cap may not exceed, and the effective cap
        when the run names none.
    :return: The peaks to search, and a scope dict for the run to record.
    """
    limit = (
        ceiling if max_untargeted_peaks is None else min(max_untargeted_peaks, ceiling)
    )
    targets = eligible.nlargest(limit, "intensity")
    scope = {
        "eligible_peaks": int(len(eligible)),
        "searched_peaks": int(len(targets)),
        "requested_limit": max_untargeted_peaks,
        "ceiling": int(ceiling),
        # True only when peaks were left unsearched, which is the question a
        # reader of the run has: not "was there a bound" but "did it bite".
        "limited": bool(len(targets) < len(eligible)),
        "at_ceiling": bool(max_untargeted_peaks is None and len(eligible) > ceiling),
    }
    return targets, scope


def reference_mirror_mask(match_isotope_df: pd.DataFrame) -> pd.Series:
    """Which rows of a Stage A match frame the reference mirror contributed.

    Told apart by what only a mirror row carries, a non-empty
    ``reference_identities`` list; the target library's rows carry none.

    :param match_isotope_df: A Stage A match frame.
    :return: A boolean mask on the frame's index, all False for a frame with no
        reference column.
    """
    if REFERENCE_IDENTITIES_COL not in match_isotope_df.columns:
        return pd.Series(False, index=match_isotope_df.index, dtype=bool)
    return (
        match_isotope_df[REFERENCE_IDENTITIES_COL]
        .apply(lambda value: isinstance(value, list) and bool(value))
        .astype(bool)
    )


def target_library_rows(match_isotope_df: pd.DataFrame) -> pd.DataFrame:
    """The rows of a Stage A match frame that the target library contributed.

    These are what a sample's mass accuracy is fitted from. A loaded reference
    mirror sits in the same frame, and its lines do not measure the instrument.
    A mirror is matched against every sample, so most of its pairings are lines
    the match window happened to reach rather than compounds the sample holds.
    An Orbitrap's window is too narrow for that to show. A TOF's is wide enough
    for such pairings to scatter across all of it, and then they set the width.
    Measured on the gate's three TOF sets with the default seed loaded, the
    mirror's lines were over 90% of the anchors, and the fitted width went from
    3.0, 5.1 and 2.6 ppm to 8.2, 8.2 and 6.5. Fitting over only the lines that
    won their peak still gave 7.9, 7.5 and 6.9, because a chance line seldom
    has a rival for its peak.

    The target library's own lines do not move when a mirror is loaded beside
    them, because the matcher pairs each ion on its own. A sample whose library
    matches too few of them to fit a width falls back to the instrument class's,
    as it does with no mirror loaded.

    :param match_isotope_df: A Stage A match frame.
    :return: The frame without the reference mirror's rows.
    """
    return match_isotope_df[~reference_mirror_mask(match_isotope_df)]


def is_target_library_row(assignment: dict) -> bool:
    """Whether a ledger row was committed for a compound of the target library.

    The inversion keeps a target winner's ``target_compound_id`` and writes a
    reference mirror's winner without one, with its identities in provenance
    instead. On a ledger row the compound id is therefore what tells the two
    Stage A sources apart.

    :param assignment: A ledger row as the engine builds it.
    :return: True for a Stage A row of the target library, False for a
        reference mirror's row and for every row another pass wrote.
    """
    return assignment.get("source") == SOURCE_DATABASE and bool(
        assignment.get("target_compound_id")
    )


def is_reference_mirror_row(assignment: dict) -> bool:
    """Whether a ledger row was committed for a reference mirror's formula.

    The other Stage A source, told apart from the target library by the compound
    id only a target winner keeps (:func:`is_target_library_row`).

    :param assignment: A ledger row as the engine builds it.
    :return: True for a Stage A row with no target compound, False for a target
        library row and for every row another pass wrote.
    """
    return assignment.get("source") == SOURCE_DATABASE and not is_target_library_row(
        assignment
    )


#: The distinct target ions Stage A's matched lines must come from before a
#: sample's own offset and width are fitted off them. One ion's lines share
#: that ion's own mass error, so counted as lines, a few bright ions with many
#: lines each - a large carbon count, S, Cl, Br - would calibrate the run on
#: themselves: their error the offset, their lines' agreement the width, and
#: every other row of both stages judged there. As many ions as the fit asks
#: for lines. Stage A is the only frame a model is fitted on before the
#: commits (``measurement_model.MODEL_V1``); the seeded chain fits none.
#:
#: Measured on the gate before it was set. Two sets fitted on fewer: the dense
#: uronium Orbitrap set on lines of five ions in each of its six samples, and
#: one labelled-nitrate sample on four. So few ions do not say where the axis
#: sits: on the uronium set their own medians spread from -0.26 to +0.81 ppm,
#: and the fits put the centre at 0.00 to -0.09 where each run's own
#: calibration, over 101 to 414 of its commits, put it at -0.18 to -0.23.
#: Falling back moved the centre by at most 0.09 ppm and the width from 0.17 to
#: 0.41 ppm to the class's 0.3; 114 of 9,100 committed rows changed, and the
#: runs' own calibrations did not move. A minimum of five would have moved the
#: nitrate sample alone, and four nothing.
STAGE_A_FIT_MIN_IONS = MASS_ACCURACY_MIN_ANCHORS


def sample_mass_accuracy(
    match_isotope_df,
    reagent: ReagentOffset | None = None,
    min_anchor_ions: int | None = None,
) -> SampleMassAccuracy:
    """Fit a sample's mass accuracy off the frame its Stage A fit was scored on.

    The fit runs over the target library's rows alone
    (:func:`target_library_rows`). Stage A scores its own ions at this width
    (:func:`score_ions_by_fit`) and the untargeted stage is scored at it, so
    both stages leave a reference mirror's lines out of it.

    :param match_isotope_df: The gated, fit-scored Stage A match frame.
    :param reagent: What the reagent pre-pass's lines said
        (:func:`mass_accuracy.reagent_line_offset`), carried beside the fit
        and never into it: the offset falls back to it only where the fit
        measured none.
    :param min_anchor_ions: The distinct ions the anchors must come from for
        either number to be measured; fewer, and the frame measured neither,
        however many lines they matched. One ion's lines share that ion's own
        mass error, so a bright ion matching eight of them alone would fit its
        own error as the offset and the floor as the width. Stage A asks for
        :data:`STAGE_A_FIT_MIN_IONS`. None counts lines alone.
    :return: The fitted offset and width, how many of the target library's
        matched lines they were fitted from and of how many ions, and the
        reagent lines' reading.
    """
    rows = target_library_rows(match_isotope_df)
    anchors = mass_accuracy_anchors(rows)
    anchor_ions = (
        int(rows.loc[anchors.index, "target_ion_id"].nunique())
        if "target_ion_id" in rows.columns
        else 0
    )
    if min_anchor_ions is not None and anchor_ions < min_anchor_ions:
        return SampleMassAccuracy(
            anchors=len(anchors), reagent=reagent, anchor_ions=anchor_ions
        )
    mu, sigma = fit_sample_mass_accuracy(rows)
    return SampleMassAccuracy(
        mu_ppm=mu,
        sigma_ppm=sigma,
        anchors=len(anchors),
        reagent=reagent,
        anchor_ions=anchor_ions,
    )


def pattern_scoring_for(
    match_params,
    model: MeasurementModel,
    resolution: Callable[[float], float] | None = None,
) -> PatternScoring:
    """How the untargeted stage scores this sample's isotope envelopes: at
    the run's measurement model.

    Everything the composition finder needs to judge a candidate as a
    measurement of THIS sample rather than of a generic Orbitrap: the width its
    mass errors actually have, the centre they sit at (read at each line's m/z
    where it follows one), the line-share tolerance, the window a line may be
    matched in, and how deep an envelope may be predicted. The derivation is
    the library's (:func:`scoring.pattern_scoring_for_model`), so an outside
    engine reading the sample through the SDK computes the same object from
    the run's record; this names what the engine hands it.

    In a run the model is its v1 (``measurement_model.MODEL_V1``): the fitted
    spread of the target library's own matched isotopologues, which is the
    instrument's measured accuracy on this sample, and its centre, with the
    instrument class's width and the reagent lines' centre standing in where
    the library matched too few ions. A reference mirror's lines are not part
    of that fit (:func:`target_library_rows`), because on a TOF they measure
    the match window. Judging a bromide set whose curated library holds two
    targets at the 5 ppm match tolerance instead would measure nothing: on a
    spectrum accurate to 0.3 ppm every candidate the search enumerated is then
    equally good and the election falls to the envelope alone. A decoy ranks
    at its control's model, a batch search at the sample's recorded one.

    :param match_params: The sample's resolved match parameters.
    :param model: The measurement model the run scores at.
    :param resolution: The file's resolving power as a function of m/z, or None
        where the run could not read one. With it, the predicted lines one peak
        of the file cannot separate are scored as one line, the sum of their
        shares: on a TOF the 13C peak also holds the 15N, 2H and 17O lines.
    :return: The scoring parameters for this sample's search.
    """
    return pattern_scoring_for_model(model, match_params, resolution)


#: The fewest peaks with a signal-to-noise over which the run records how the
#: column ranks against intensity; fewer, and the correlation is not read.
SNR_RANK_MIN_PEAKS = 30


def signal_to_noise_snapshot(peaks: pd.DataFrame) -> dict:
    """What a run records about the signal-to-noise it scored on.

    The fit reads each peak's ``signal_to_noise`` to decide whether a
    predicted line's absence is evidence, and a TOF file's column did not
    track height until its peaks were detected again under the fixed picker
    (a reference engine measured rank correlations of -0.07 to 0.17 on a TOF
    batch, where an Orbitrap file gives 0.99). The stored peak list does not
    say which picker wrote it, so the run records the column's own rank
    correlation with the intensity it scored on: near one is a noise estimate
    that follows the signal, near zero is one that does not. The intensity is
    what the engine scores (a TOF file's peak areas, an Orbitrap file's
    heights), so on a TOF the rank is against area, which is height times an
    m/z-dependent width.

    :param peaks: The peak frame the search scored over: ``intensity`` and,
        where the file carries one, ``signal_to_noise``.
    :return: A JSON-serializable dict for the run's scoring snapshot.
    """
    none = {"snr_source": "none", "snr_peaks": 0, "snr_intensity_spearman": None}
    if "signal_to_noise" not in peaks.columns:
        # "none": the file records no noise estimate, and every absent line
        # was judged by its expected share alone.
        return none
    snr = peaks["signal_to_noise"].to_numpy(dtype=float)
    intensity = peaks["intensity"].to_numpy(dtype=float)
    usable = np.isfinite(snr) & (snr > 0) & np.isfinite(intensity) & (intensity > 0)
    if not usable.any():
        # A column with no value in it is no estimate either
        return none
    spearman = None
    # A constant column has no rank to read (and spearmanr warns on it)
    if (
        usable.sum() >= SNR_RANK_MIN_PEAKS
        and np.ptp(snr[usable]) > 0
        and np.ptp(intensity[usable]) > 0
    ):
        rho = spearmanr(snr[usable], intensity[usable])[0]
        spearman = round(float(rho), 4) if np.isfinite(rho) else None
    return {
        # "peak_list": the per-peak column the file's peak detection wrote
        "snr_source": "peak_list",
        "snr_peaks": int(usable.sum()),
        "snr_intensity_spearman": spearman,
    }


def scoring_snapshot(
    scoring: PatternScoring, peaks: pd.DataFrame | None = None
) -> dict:
    """What a run records about how it matched a line, beside its model.

    :param scoring: The scoring parameters the search actually used.
    :param peaks: The peak frame the search scored over, whose
        signal-to-noise is recorded (:func:`signal_to_noise_snapshot`);
        None records nothing about it.
    :return: A JSON-serializable dict for the run's model record.
    """
    snapshot = {
        "mz_tolerance_ppm": float(scoring.mz_tolerance_ppm),
        "abundance_floor": float(scoring.abundance_floor),
        # Whether the envelope was predicted at the file's resolving power, one
        # line per peak it can separate, or one line per isotopologue because
        # the run could read no resolution for the file.
        "lines_merged_at_resolution": scoring.resolution is not None,
    }
    if peaks is not None:
        snapshot.update(signal_to_noise_snapshot(peaks))
    return snapshot


def measurement_model_record(
    model: MeasurementModel,
    *,
    ranked_at: MeasurementModel | None = None,
    scoring: dict | None = None,
    gate: dict | None = None,
) -> dict:
    """The run's one record of its measurement model (:data:`MEASUREMENT_MODEL_KEY`).

    The model the rows were scored and capped at
    (``MeasurementModel.record``), which a reader outside the run rebuilds
    (``MeasurementModel.from_run_config``); where that is v2, the v1 the
    search ranked at beside it; how a line was matched
    (:func:`scoring_snapshot`); and what the mass gate measured and capped at
    it, with what its refit on the commits measured (``mass_gate``).

    The first gate round of step 2.1 turned on exactly this and the run could
    not answer it: whether the width was the sample's own or the instrument
    class's is the difference between a candidate a whole ppm off being refused
    and being elected, and the row it decided says nothing about it.

    :param model: The model the rows were scored and capped at.
    :param ranked_at: The model the search ranked at, where it is not ``model``.
    :param scoring: How the run matched a line; None where it recorded none.
    :param gate: The mass gate's summary; None for a pass with no gate.
    :return: A JSON-serializable record.
    """
    record = model.record()
    if ranked_at is not None and ranked_at.version != model.version:
        record["ranked_at"] = ranked_at.record()
    if scoring is not None:
        record["scoring"] = scoring
    if gate is not None:
        record["gate"] = gate
    return record


def tier_for_evidence(
    evidence: float | None,
    *,
    candidate_threshold: float,
    assigned_threshold: float,
) -> str:
    """Map a peak's evidence onto a confidence tier.

    Evidence is ``fit x plausibility`` -- the measurement of how well the formula
    explains the observed isotope envelope, weighted by how chemically plausible
    that formula is at all. Tiering on the product rather than on the fit alone is
    what stops a chemically implausible formula from holding the ledger's
    strongest word on mass accuracy: it is already the currency both stages
    arbitrate a contested peak in, so the tier now agrees with the quantity that
    picked the winner in the first place.

    ``fit_score`` remains stored and displayed as the pure measurement; it is just
    no longer what buckets the row.

    The bands are keyword-only. They were positional, in the opposite order to
    their names, and every caller needed a comment saying so - a positional call
    written in band order silently inverted them and tiered a whole run wrong.

    :param evidence: The row's evidence, or None when it has none.
    :param candidate_threshold: Evidence at or above which a row is 'candidate'.
    :param assigned_threshold: Evidence at or above which a row is 'assigned'.
    :return: The tier this evidence earns under these bands.
    """
    if evidence is None or not np.isfinite(evidence) or evidence <= 0:
        return TIER_BELOW_ASSIGNABILITY
    if evidence >= assigned_threshold:
        return TIER_ASSIGNED
    if evidence >= candidate_threshold:
        return TIER_CANDIDATE
    return TIER_BELOW_ASSIGNABILITY


def tier_under_ceiling(row: dict) -> str | None:
    """A row's tier under the ceiling the mass gate measured for its line.

    What the mass gate's cap leaves the row at (``mass_gate.cap_mass_gate``),
    whether or not the cap has run yet. A pass that reads another row's tier
    as a bar - is it a partner, does its envelope predict lines that can take
    a tier - reads it here, so the bar is the same before and after the cap.
    The gate's is the one cap that can take a row under ``candidate``; every
    other cap stops there, so no bar at ``candidate`` reads another cap's work.

    :param row: A ledger row.
    :return: Its tier, or the ceiling where that is lower.
    """
    tier = row.get("tier")
    gate = (row.get("provenance") or {}).get("mass_gate")
    ceiling = gate.get("ceiling") if isinstance(gate, dict) else None
    if (
        tier in TIER_RANK
        and ceiling in TIER_RANK
        and TIER_RANK[ceiling] < TIER_RANK[tier]
    ):
        return ceiling
    return tier


#: The tiers at which a committed row shows its molecule (:func:`shows_molecule`).
SHOWN_TIERS = frozenset({TIER_ASSIGNED, TIER_CANDIDATE})

#: The channels a second channel's partner is read through: the mode's own,
#: the channels the run searched and did not open for itself, since an
#: opportunistic channel shows nothing on its own...
PARTNER_MODE_CHANNELS = "mode"
#: ...or any other channel the run searched, its opportunistic ones included.
PARTNER_ANY_CHANNEL = "any"

#: What a reading's corroboration is recorded as, by what corroborated it.
CORROBORATED_BY_ISOTOPOLOGUE = "isotopologue"
CORROBORATED_BY_LIST = "list"
CORROBORATED_BY_SECOND_CHANNEL = "second_channel"


@dataclass(frozen=True)
class SecondChannelRule:
    """What corroborates a reading through one channel: the one rule.

    A reading is corroborated, first match first, by its own committed
    isotopologue, by a list naming its neutral, or by a partner: a
    monoisotopic row of the same neutral (``utils.formula_identity``) through
    another channel. What lifts it is the rule's parameters, and each use of
    "a second channel" is a configuration of it (:data:`SEARCH_READING_RULE`,
    :data:`LIST_READING_RULE`, :data:`PARTNER_RULE`, :data:`CORROBORATION_RULE`), so the uses
    differ where their configurations say and nowhere else.

    :param isotopologue: Whether the reading's own committed isotopologue
        corroborates it.
    :param listed: Whether a list the sample was read against naming its
        neutral corroborates it.
    :param partner_tiers: The tiers at which a partner counts, read under the
        mass gate's ceiling (:func:`tier_under_ceiling`); None for any tier.
    :param partner_channels: The channels a partner is read through:
        :data:`PARTNER_MODE_CHANNELS` or :data:`PARTNER_ANY_CHANNEL`.
    """

    isotopologue: bool
    listed: bool
    partner_tiers: frozenset[str] | None
    partner_channels: str

    def is_partner(
        self, row: dict, notation_by_id: dict[str, str], minor_channels: frozenset[str]
    ) -> bool:
        """Whether a row is a partner under this rule.

        A monoisotopic row with a formula, through a channel the run searched
        (a channel it cannot name shows nothing), of the rule's channels and
        at one of its tiers.

        :param row: A ledger row.
        :param notation_by_id: The run's mechanisms, by the id the rows carry.
        :param minor_channels: The run's opportunistic channels.
        :return: Whether the row commits its neutral as a partner here.
        """
        if row.get("role") != ROLE_M0 or not row.get("assigned_formula"):
            return False
        channel = notation_by_id.get(str(row.get("ionization_mechanism_id")))
        if channel is None:
            return False
        if self.partner_channels == PARTNER_MODE_CHANNELS and channel in minor_channels:
            return False
        return self.partner_tiers is None or (
            tier_under_ceiling(row) in self.partner_tiers
        )

    def partners(
        self,
        rows: Iterable[dict],
        notation_by_id: dict[str, str],
        minor_channels: frozenset[str],
    ) -> dict[str, frozenset[str]]:
        """The neutrals this rule's partners commit among ``rows``, each with
        the channels it is committed through (:meth:`is_partner`).

        :param rows: Ledger rows.
        :param notation_by_id: The run's mechanisms, by the id the rows carry.
        :param minor_channels: The run's opportunistic channels.
        :return: Per neutral, as a :func:`formula_identity` key, the mechanism
            ids of its partners (what :meth:`corroboration` reads).
        """
        found: dict[str, set[str]] = {}
        for row in rows:
            if self.is_partner(row, notation_by_id, minor_channels):
                found.setdefault(formula_identity(row["assigned_formula"]), set()).add(
                    str(row.get("ionization_mechanism_id"))
                )
        return {neutral: frozenset(ids) for neutral, ids in found.items()}

    def corroboration(
        self,
        row: dict,
        *,
        partners: Mapping[str, Collection[str]],
        listed_formulas: frozenset[str] = frozenset(),
        isotopologue: bool = False,
    ) -> str | None:
        """What corroborates a reading under this rule, first match first.

        :param row: The reading's row.
        :param partners: This rule's partners (:meth:`partners`): per neutral,
            the mechanism ids it is committed through. One through the
            reading's own channel is no second channel, and the reading's own
            row is never its own partner.
        :param listed_formulas: The neutrals the sample's lists name.
        :param isotopologue: Whether the reading has a committed isotopologue.
        :return: :data:`CORROBORATED_BY_ISOTOPOLOGUE`,
            :data:`CORROBORATED_BY_LIST`, :data:`CORROBORATED_BY_SECOND_CHANNEL`,
            or None where nothing the rule counts corroborates it.
        """
        neutral = formula_identity(row.get("assigned_formula"))
        if self.isotopologue and isotopologue:
            return CORROBORATED_BY_ISOTOPOLOGUE
        if self.listed and neutral in listed_formulas:
            return CORROBORATED_BY_LIST
        own = str(row.get("ionization_mechanism_id"))
        if any(channel != own for channel in partners.get(neutral, ())):
            return CORROBORATED_BY_SECOND_CHANNEL
        return None


#: A search's reading through a secondary channel (step 3.4): a list naming
#: the neutral, or the sample showing it through one of the mode's own
#: channels. Its isotopologue is not enough: a 13C line proves the ion's
#: carbon count, never which neutral clustered with the channel's carrier -
#: benzene and methanol read through the carbonate channel reached "assigned"
#: on their isotopologue alone.
SEARCH_READING_RULE = SecondChannelRule(
    isotopologue=False,
    listed=True,
    partner_tiers=SHOWN_TIERS,
    partner_channels=PARTNER_MODE_CHANNELS,
)

#: A list's reading through a channel the run opened (step 3.3f): its own
#: isotopologue, or the sample showing the neutral through one of the mode's
#: own channels. The list is why the reading was made, so it is no
#: corroboration of it; what a search's reading lacks, a name for the
#: neutral, a list's reading has, so its isotopologue does count.
LIST_READING_RULE = SecondChannelRule(
    isotopologue=True,
    listed=False,
    partner_tiers=SHOWN_TIERS,
    partner_channels=PARTNER_MODE_CHANNELS,
)

#: A partner: the sample shows the molecule (:func:`shows_molecule`). What the
#: partner gate stands a gated reading on, the cross-channel pass weighs a
#: rival by, and the fragment claim yields to, and the partner the two
#: configurations above count.
PARTNER_RULE = SecondChannelRule(
    isotopologue=False,
    listed=False,
    partner_tiers=SHOWN_TIERS,
    partner_channels=PARTNER_MODE_CHANNELS,
)

#: The cross-channel pass's corroboration (``cross_channel.channels_by_neutral``):
#: two channels committed the neutral, at any tier and through any channel the
#: run searched, its opportunistic ones included.
#:
#: It differs from the three configurations above on purpose (MAS-1018). They
#: decide whether a reading may stand, so they bar a partner the sample does
#: not show. This one counts: it records which channels saw the neutral, and
#: the best tier on each travels with the count (``cross_channel.partner_tier``),
#: so the partner's tier is weighed rather than barred. The tiering pass reads
#: the count as the escape from its density, lone-peak and context caps.
#: Reading it at :data:`PARTNER_RULE` instead would move results, and is left
#: to be read on a card once the run has one measurement model (MAS-982).
CORROBORATION_RULE = SecondChannelRule(
    isotopologue=False,
    listed=False,
    partner_tiers=None,
    partner_channels=PARTNER_ANY_CHANNEL,
)


def shows_molecule(
    row: dict, notation_by_id: dict[str, str], minor_channels: frozenset[str]
) -> bool:
    """Whether the sample shows this row's molecule through it.

    The one test of "the sample shows this molecule" (:data:`PARTNER_RULE`), which
    the partner gate reads as a partner (:func:`apply_partner_gates`), the
    cross-channel pass as a rival the sample bears out (``cross_channel.
    partner_heights``), the fragment claim as a reading shown elsewhere
    (``reagent_pass.claim_fragments``), and the second-channel cap as a
    reading's partner (:func:`cap_second_channel`). A monoisotopic row that
    commits its neutral at candidate or better, under the mass gate's ceiling
    (:func:`tier_under_ceiling`), through one of the mode's own channels: a
    channel the run searched and did not open for itself. An opportunistic
    channel shows nothing on its own, and neither does a channel the run
    cannot name.

    It is read where the reading changes have settled and before any cap
    (``judging_loop.Judging.judge``): after the mass gate has measured the ledger,
    whose ceiling is the one cap below candidate and is read here, so the
    other caps cannot move the answer.

    :param row: A ledger row.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :param minor_channels: The run's opportunistic channels.
    :return: Whether the row shows its molecule.
    """
    return PARTNER_RULE.is_partner(row, notation_by_id, minor_channels)


def evidence_for(fit_score: float | None, formula: str | None) -> float | None:
    """Evidence for a committed formula: ``fit x plausibility``.

    The one place the product is spelled out for callers that hold a stored row
    rather than a live scoring frame - manual curation, the batch propagation, the
    import check. Both engine stages compute it inline instead, because they
    already carry the plausibility they arbitrated with and re-deriving it from
    the formula string would be a second, divergeable source of the same number.

    Plausibility is recomputed from the formula rather than read off whatever the
    row carries: it is a pure function of the formula (Seven Golden Rules), so
    there is nothing to gain from trusting a number a caller could have made up,
    and an imported row can then be checked without asking its author to declare
    one. It never decides whether a write happens, so a formula that cannot be
    parsed fails open at plausibility 1.0 and the evidence is the fit alone.

    :param fit_score: The row's fit score, or None.
    :param formula: The committed neutral formula, or None.
    :return: The evidence, or None when there is no fit score to weigh.
    """
    fit = _score_or_none(fit_score)
    if fit is None:
        return None
    if not formula:
        return fit
    try:
        return round(fit * float(formula_plausibility(formula)), 4)
    except Exception:  # plausibility must never decide whether a write happens
        return fit


def plausibility_for(formula: str | None) -> float | None:
    """This server's chemical plausibility for a committed formula, for storing.

    The other half of the product :func:`evidence_for` returns, spelled out for
    the same callers and for the same reason: manual curation and the import
    path both write it into ``provenance.plausibility``, which the peak
    inspector renders as this server's reading of the chemistry.

    Answers ``None`` where there is nothing this server can honestly claim - no
    formula, or one it could not parse. That second case needs stating, because
    ``formula_plausibility`` does not raise on it: it fails open to 1.0, which
    is the right answer for *weighing* a fit (an unreadable formula must not
    demote a row) and the wrong one for *displaying*, where it would assert
    perfect chemistry for a string nothing could read. Evidence therefore still
    comes out as the bare fit while the plausibility beside it reads as a dash,
    which is the honest rendering of "could not read it".

    :param formula: The row's committed neutral formula, or None.
    :return: The plausibility, or None when there is none to state.
    """
    if not formula:
        return None
    try:
        if element_counts(formula) is None:
            return None
        return round(float(formula_plausibility(formula)), 4)
    except Exception:  # chemistry must never decide whether a row is stored
        return None


def _float_or_none(value) -> float | None:
    """Coerce to float, mapping NaN/None/missing to None."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def _score_or_none(value) -> float | None:
    """Coerce a match score to float within [0, 1], or None."""
    score = _float_or_none(value)
    if score is None:
        return None
    return min(1.0, max(0.0, score))


def _str_or_none(value) -> str | None:
    """Coerce to str, mapping NaN/None to None."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return str(value)


#: Width of ``peak_assignment.isotope_formula``.
ISOTOPE_FORMULA_LENGTH = 256


def fit_isotope_formula(value) -> str | None:
    """An isotopologue formula that fits its column, or None.

    At a low resolution one peak merges many isotopologues, and the isotope
    generator names every one of them, separated by ``/``. For a large ion that
    carries bromine or nitrogen the names run past the column, and a single row
    too long fails the insert of the whole run. The column is a label the
    inspector renders, not a record of every contributor, so whole names are
    kept from the front and the rest are dropped (:func:`fit_isotope_names`,
    which the generator fits its own column with). The label carries no
    abundances, so the front - the lightest names - is what is kept.
    """
    text = _str_or_none(value)
    if text is None:
        return None
    return fit_isotope_names(text.split("/"), ISOTOPE_FORMULA_LENGTH)


def _isotope_offset_label(iso_mz: float, main_mz: float | None) -> str | None:
    """Label an isotopologue by its nominal mass offset from the ion's M0, the
    monoisotopic isotopologue: ``M+1``, ``M+2`` ... and, for an element whose
    most abundant isotope is not its lightest (iron), ``M-2``."""
    if main_mz is None or not np.isfinite(main_mz):
        return None
    offset = int(round(iso_mz - main_mz))
    if offset == 0:
        return "M0"
    return f"M+{offset}" if offset > 0 else f"M{offset}"


# Columns the ion-level fit score needs on the isotope frame. Absent (e.g. a lighter
# match path) -> the fit scoring is skipped and the per-isotopologue score stands.
_FIT_SCORE_COLS = frozenset(
    {"target_ion_id", "relative_abundance", "match_mz_error", "sample_peak_intensity"}
)


def drop_ions_claimed_elsewhere(
    match_isotope_df: pd.DataFrame,
    claimed_peak_ids: set[str],
    *,
    ion_ids: Collection[str] | None = None,
) -> pd.DataFrame:
    """Remove target ions whose peaks another pass has already claimed.

    Dropping the matched ROWS is not enough, and the difference is what this
    function exists for. An ion is inverted as a family - one M0 and its
    isotopologue children, the children naming the M0 as their owner - so
    removing only the row that landed on the claimed peak leaves the children
    behind with nothing to belong to: the lightest of them is read as the
    ion's M0, and the ledger carries an isotopologue family whose ion is not
    in it.

    So the whole ion goes when its monoisotopic peak is claimed: an ion whose M0
    is the reagent has the reagent's isotopologues, not its own. Any straggler
    row that landed on a claimed peak goes too, which keeps the ledger's one row
    per peak whichever part of the family the claim caught.

    :param match_isotope_df: The gated, scored isotope frame.
    :param claimed_peak_ids: Peaks another pass owns.
    :param ion_ids: The ions the drop applies to, every other ion's rows kept
        as they are; None applies it to every ion. The seeded scoring drops
        only the ions a sample's lists hold before its gating, as Stage A
        drops its own (``seeded_scoring.score_seeds``).
    :return: The frame without those ions.
    """
    if match_isotope_df.empty or not claimed_peak_ids:
        return match_isotope_df
    if not {"target_ion_id", "sample_peak_id"} <= set(match_isotope_df.columns):
        return match_isotope_df
    scope = None if ion_ids is None else {str(ion_id) for ion_id in ion_ids}
    claimed_ions = {
        ion_id
        for ion_id, group in match_isotope_df.groupby("target_ion_id", sort=False)
        if (scope is None or str(ion_id) in scope)
        and str(monoisotopic_row(group).get("sample_peak_id") or "") in claimed_peak_ids
    }
    keep = ~match_isotope_df["sample_peak_id"].isin(claimed_peak_ids)
    if scope is not None:
        keep |= ~match_isotope_df["target_ion_id"].astype(str).isin(scope)
    if claimed_ions:
        keep &= ~match_isotope_df["target_ion_id"].isin(claimed_ions)
    return match_isotope_df[keep]


def line_heights_within(
    match_isotope_df: pd.DataFrame,
    envelope_tolerance: EnvelopeTolerance | None = None,
) -> np.ndarray:
    """Per row, whether the line's height passes the line model's height test.

    The one matcher's height test (``line_model.within_intensity_window``), the
    one the finder admits a search candidate's lines by, read off a match
    frame: each line's ``match_abundance_error`` against its ion's main line,
    at its expected signal-to-noise, the main line's times the line's
    predicted share of it, at the run's fitted tolerance. A line the match
    pass read scan by scan (``ratio_scan_count``, an Orbitrap line read over
    the scans holding it and its main line) is read by its presence, so the
    window does not open below the share for it. Stage A and the seeded
    re-score gate their lines by it in place of the match parameters' fixed
    ratio tolerance (``apply_match_params(..., heights_within=...)``), so a
    line is the ion's on the same test wherever the run reads it (MAS-977).

    Positional, as a frame that has been through a gate and a scorer can carry
    a duplicated index. A row without an error is not within.

    :param match_isotope_df: A match frame (``target_ion_id``,
        ``relative_abundance``, ``match_abundance_error``, and
        ``signal_to_noise`` and ``ratio_scan_count`` where it has them).
    :param envelope_tolerance: The run's fitted tolerance, or None for today's.
    :return: A boolean per row.
    """
    if match_isotope_df.empty:
        return np.zeros(0, dtype=bool)
    codes, _ = pd.factorize(match_isotope_df["target_ion_id"], use_na_sentinel=False)
    abundance = pd.to_numeric(
        match_isotope_df["relative_abundance"], errors="coerce"
    ).to_numpy(float)
    error = pd.to_numeric(
        match_isotope_df["match_abundance_error"], errors="coerce"
    ).to_numpy(float)
    snr = (
        pd.to_numeric(match_isotope_df["signal_to_noise"], errors="coerce").to_numpy(
            float
        )
        if "signal_to_noise" in match_isotope_df
        else np.full(len(match_isotope_df), np.nan)
    )
    # Each ion's main line: its most abundant, the first of them on a tie, the
    # line the match pass read every other one against.
    order = np.lexsort(
        (np.arange(codes.size), -np.nan_to_num(abundance, nan=-np.inf), codes)
    )
    first = np.ones(codes.size, dtype=bool)
    first[1:] = codes[order][1:] != codes[order][:-1]
    main = np.empty(codes.max() + 1, dtype=int)
    main[codes[order][first]] = order[first]
    main_of = main[codes]
    main_snr = snr[main_of]
    # The finder's: no expected S/N where the main line carries none, so the
    # window stays fixed (``heuristic_filter.score_ion_envelope``).
    with np.errstate(divide="ignore", invalid="ignore"):
        expected_snr = np.where(
            np.isfinite(main_snr) & (main_snr > 0),
            main_snr * abundance / abundance[main_of],
            np.nan,
        )
    by_presence = (
        pd.to_numeric(match_isotope_df["ratio_scan_count"], errors="coerce")
        .notna()
        .to_numpy()
        if "ratio_scan_count" in match_isotope_df
        else None
    )
    return np.asarray(
        within_intensity_window(error, expected_snr, envelope_tolerance, by_presence),
        dtype=bool,
    )


def score_ions_by_fit(
    match_isotope_df: pd.DataFrame,
    fallback_sigma_ppm: float | None = None,
    reagent_offset: ReagentOffset | None = None,
    min_anchor_ions: int | None = None,
    envelope_tolerance: EnvelopeTolerance | None = None,
    model: MeasurementModel | None = None,
) -> pd.DataFrame:
    """Set each isotopologue's ``match_score`` to its ion's fit score (Stage A).

    The peak-centric engine adopts the fit score (`score_pattern_v2`) *deliberately*
    as its scoring engine -- unconditionally, not gated on the legacy
    ``MASCOPE_MATCH_SCORE_VERSION`` switch, per the epic's "coexist, don't replace"
    principle. Where the targeted matcher emits a per-isotopologue
    ``abundance_term * mz_term``, this replaces it with the consolidated ion-level
    fit quality: the whole predicted isotope envelope scored against the spectrum
    (mass, intensity, SNR-detectability), computed exactly as the aggregate match
    path does (`ion_score_v2` per ``target_ion_id`` with the sample's fitted mass
    accuracy). That accuracy is fitted over the target library's lines only
    (:func:`sample_mass_accuracy`), so every ion in the frame, a reference
    mirror's included, is scored at a width the mirror's own chance lines did
    not widen. Every isotopologue of an ion carries that ion's fit, so the
    single-owner arbitration in `invert_matches_to_peak_assignments` awards a
    contested peak to the better-corroborated assignment, not the one with the
    best single-peak mass hit.

    Call AFTER the gate (`seeded_scoring.gate_lines`: `apply_match_params` with
    the line model's height test, :func:`line_heights_within`): it zeroes
    ``sample_peak_intensity`` for isotopologues outside the m/z tolerance, and
    this treats any isotopologue the gating rejected (``match_score == 0``) as
    absent, so the fit score honours the gate's mass window, height test and
    intensity floor.

    The returned frame carries that verdict: an isotopologue the gating rejected
    comes back with ``sample_peak_intensity`` zeroed, not just excluded from the
    arithmetic. `apply_match_params` only zeroes the intensity of pairings outside
    the *m/z* tolerance or missing the isotope ratio by 100 % or more - one the
    gate rejected on the line model's height test or the intensity floor keeps
    its intensity, the signal the targeted ion sum counts - so without
    this the ownership guard in
    `invert_matches_to_peak_assignments` would let a pairing this function counted
    as ABSENT claim its peak anyway, carrying the ion's (possibly "assigned") fit
    score and blocking Stage B from explaining that peak. One gating decision, one
    frame.

    Real per-peak ``signal_to_noise`` (carried from the filestore by
    `compute_match_isotopes`) makes this the full v2 fit; rows without it are
    scored in `ion_score_v2`'s no-SNR mode. No-op (returns the frame
    unchanged) when empty or missing the required columns.

    NOTE: what this computes is the fit, which is NOT what the tier is read off.
    The confidence-tier bands sit on the EVIDENCE scale (fit x plausibility) --
    assigned/candidate = 0.75/0.45 on `PeakAssignmentConfig` (config.py). The fit
    is half of that product and stays the pure measurement; see
    `tier_for_evidence`. Per-instrument recalibration of the bands is a follow-up
    once verification labels accumulate.

    :param match_isotope_df: The gated match frame.
    :param fallback_sigma_ppm: The instrument class's width
        (``profiles.resolve_fallback_sigma_ppm``). A sample whose target library
        matched too few lines to fit a width is scored at it, the stand-in the
        untargeted stage takes (:func:`pattern_scoring_for`), so the two stages
        still score at one width. Without it such a sample is scored at
        ``score_pattern_v2``'s generic 2 ppm. Measured on the gate's bromide
        Orbitrap set, whose library matches two lines a sample, that generic
        width put 155 more of a loaded seed's rows at assigned tier once the
        seed's lines were out of the fit, and G1 rose from 8.0 to 15.3%. None
        keeps the generic width, for a caller with no instrument class to name.
    :param reagent_offset: What the reagent pre-pass's lines said about the
        offset (:func:`mass_accuracy.reagent_line_offset`). A sample whose
        target library matched too few lines to fit an offset is scored at it
        where it is beyond the width (:attr:`ReagentOffset.beyond_width`),
        which is the offset the untargeted stage is scored at too
        (:attr:`SampleMassAccuracy.scoring_mu_ppm`).
        Measured on the gate's labelled-nitrate set, whose curated lines are
        too few once its workaround entries are gone, the lines put the axis
        at -1.26 ppm and the run's own commits at -1.10, where the sample was
        otherwise scored at zero. None leaves such a sample uncorrected.
    :param min_anchor_ions: The distinct ions a fit's anchors must come from
        (:func:`sample_mass_accuracy`); a frame with fewer is scored at the two
        stand-ins above. Stage A passes :data:`STAGE_A_FIT_MIN_IONS`. None
        counts lines alone.
    :param envelope_tolerance: How well the run measures an isotope line's
        share, fitted on its own committed envelopes
        (:mod:`~mascope_backend.api.new.peak_assignments.envelope_fit`); None
        scores at today's 5% and threshold of 3, as Stage A's first pass does
        before the fit exists.
    :param model: The measurement model the frame is scored at instead of
        one fitted off it (:func:`score_frame_at`): its centre at each line's
        m/z, its width and its line-share tolerance, which then replaces
        ``envelope_tolerance``. Every pass but Stage A's first hands one: the
        run's v1 to Stage A's second pass and the seeded re-score, a decoy's
        control's (:mod:`decoy`), whose own rows are wrong formulas on a moved
        axis, and the recorded one to every reader outside a run. None fits
        the frame: Stage A's first pass, which is where v1 is fitted
        (``measurement_model.MODEL_V1``).
    :return: The gated frame with every ion's fit as its rows' ``match_score``.
    """
    if match_isotope_df.empty or not _FIT_SCORE_COLS.issubset(match_isotope_df.columns):
        return match_isotope_df

    df = match_isotope_df.copy()
    # Honour the gate (`seeded_scoring.gate_lines`): an isotopologue it
    # rejected (score 0) is absent to the fit score (its intensity may still be
    # set, e.g. on its m/z but outside the line model's height test at the
    # run's tolerance, or below the intensity floor).
    if "match_score" in df.columns:
        gated_out = pd.to_numeric(df["match_score"], errors="coerce").fillna(0.0) == 0
        df.loc[gated_out, "sample_peak_intensity"] = 0.0

    # The one measurement the untargeted stage is scored at too, read off this
    # same gated frame, with the same stand-ins where it measured nothing: the
    # class width for the width, the reagent lines' offset where it is beyond
    # the width.
    # The offset is None where neither measured one; the scorer reads that as
    # an uncorrected sample rather than a centred one.
    if model is None:
        model = MeasurementModel.v1(
            sample_mass_accuracy(
                df, reagent=reagent_offset, min_anchor_ions=min_anchor_ions
            ),
            fallback_sigma_ppm,
            envelope_tolerance,
        )
    # Return the GATED frame (not a fresh copy of the input): the zeroed intensities
    # are the record of which pairings this scoring counted as absent, and the
    # ownership guard downstream reads exactly that.
    df["match_score"] = score_frame_at(df, model)
    return df


def score_frame_at(scored_df: pd.DataFrame, model: MeasurementModel) -> pd.Series:
    """Every ion's fit in a gated frame, at a measurement model.

    Every ion at once, the fit ``ion_score_v2`` gives each one alone: each
    line's mass error read from the model's centre at the line's predicted
    m/z, in the model's width at that m/z (``MeasurementModel.fit_sigmas_ppm``,
    which the fit widens back to ``MeasurementModel.widths_ppm``: floored by a
    trend's absolute floor below its coverage), and its share at the model's
    tolerance. The mass gate reads a row's distance from the same centre in
    the same width (``MeasurementModel.z_of``).

    :param scored_df: A gated frame, an isotopologue the gate turned away
        already at zero intensity (:func:`score_ions_by_fit`).
    :param model: The model.
    :return: Each row's ion's fit, on the frame's index.
    """
    if scored_df.empty:
        return pd.Series(dtype=float, index=scored_df.index)
    mz_column = "mz" if "mz" in scored_df.columns else "sample_peak_mz"
    # Only a trend makes the centre and the width depend on the line's m/z.
    mz = (
        pd.to_numeric(scored_df[mz_column], errors="coerce").to_numpy(float)
        if model.trend is not None and mz_column in scored_df.columns
        else None
    )
    return ion_scores_v2(
        scored_df,
        "target_ion_id",
        sigma_ppm=model.fit_sigma_ppm if mz is None else model.fit_sigmas_ppm(mz),
        mu=model.scoring_mu_ppm if mz is None else model.centres_ppm(mz),
        tolerance=model.envelope_tolerance,
    )


def _row_reference_identities(row) -> list | None:
    """Reference identities carried on a match row, else None.

    Only reference-sourced isotope rows carry a non-empty list here; target and
    untargeted rows have NaN. Used to null the synthetic target FKs and to attach
    the one-to-many identities to the assignment's provenance.
    """
    value = row.get(REFERENCE_IDENTITIES_COL)
    return value if isinstance(value, list) and value else None


def calibration_meta(
    calibration,
    ionization_setup: dict | None = None,
    reagent_profile: str | None = None,
) -> dict | None:
    """What a run records about the confidence calibration it applied.

    The instrument class, whether the curve is provisional, and what it was fit
    from - the three fields every database-sourced row used to repeat in its
    provenance, now recorded once per run (``PeakAssignmentRun.confidence_calibration``)
    and folded back into each row by the detail read. ``None`` when the run was
    uncalibrated, which is what a reader takes to mean "no curve".

    A curve that records the ionization setups it was fit under adds them
    (``fit_setups``), and, given the setup the run searched under, whether it
    was not among them (``setup_mismatch``): the curve is applied either way,
    and this is where the run says it was applied across setups. A curve that
    records the reagent profile it was fit under adds it (``fit_profile``), and,
    given the profile the run resolved to, whether that was another
    (``profile_mismatch``).

    :param calibration: The calibration the run applied, or None.
    :param ionization_setup: The setup the run searched under, or None.
    :param reagent_profile: The reagent profile the run resolved to, or None.
    :return: The record for the run row, or None.
    """
    if calibration is None:
        return None
    meta = {
        "instrument": calibration.instrument,
        "provisional": calibration.provisional,
        "source": calibration.source,
    }
    setups = list(getattr(calibration, "ionization_setups", ()))
    if setups:
        meta["fit_setups"] = setups
        if ionization_setup is not None:
            meta["setup_mismatch"] = ionization_setup not in setups
    fit_profile = getattr(calibration, "reagent_profile", None)
    if fit_profile is not None:
        meta["fit_profile"] = fit_profile
        if reagent_profile is not None:
            meta["profile_mismatch"] = reagent_profile != fit_profile
    return meta


def _alternative_dict(
    row,
    reference_identities_by_formula: dict,
    main_isotope_ids: set | frozenset = frozenset(),
    main_mz_by_ion: dict | None = None,
) -> dict:
    """Build one runner-up candidate dict for a peak's ``alternatives`` list.

    Null the target FKs when the runner-up is itself a reference row, and attach
    the formula's reference identities (if any) so a runner-up known compound is
    still named.

    The isotopologue label is recorded the same way the winner's is. A runner-up
    is a target *isotope* that also landed on this peak, and it is just as free
    as the winner to be one of its ion's isotopologues rather than the main one -
    so without the label, promoting such a candidate by hand would enter a
    compound's M+1 into the ledger as the compound's main peak.
    """
    is_reference_row = _row_reference_identities(row) is not None
    formula = _str_or_none(row.get("target_compound_formula"))
    ion_id = _str_or_none(row.get("target_ion_id"))
    isotope_label = (
        "M0"
        if row.get("target_isotope_id") in main_isotope_ids
        else _isotope_offset_label(row["mz"], (main_mz_by_ion or {}).get(ion_id))
    )
    alternative = {
        "assigned_formula": formula,
        "ion_formula": _str_or_none(row.get("target_ion_formula")),
        "isotope_label": isotope_label,
        # The adduct the runner-up was scored under. A formula is only half an
        # assignment - without the mechanism a promoted runner-up would land on
        # the ledger as an adductless claim, and a verification's identity
        # (peak + formula + mechanism) would be incomplete. Older rows predate
        # this key; curation falls back to the target ion's mechanism there.
        "ionization_mechanism_id": _str_or_none(row.get("ionization_mechanism_id")),
        "target_compound_id": (
            None if is_reference_row else _str_or_none(row.get("target_compound_id"))
        ),
        "target_ion_id": (
            None if is_reference_row else _str_or_none(row.get("target_ion_id"))
        ),
        "fit_score": _score_or_none(row.get("match_score")),
        "mz_error_ppm": _float_or_none(row.get("match_mz_error")),
        "plausibility": _float_or_none(row.get("_plaus")),
        "source": SOURCE_DATABASE,
    }
    formula_identities = reference_identities_by_formula.get(formula_identity(formula))
    if formula_identities:
        alternative["reference_identities"] = formula_identities
    return alternative


#: The provenance key a row carries where the cap on its stored
#: ``alternatives`` (``max_alternatives``) left a rival out: how many distinct
#: rival formulas the run competed or proposed for the peak and stores no
#: entry of. A reading of the row's own ion (``same_ion``) is no rival and is
#: not counted. Absent where the cap cut no rival. The identification level
#: (``identification_level``) reads it: a rival the row does not store is one
#: no test can be shown to have excluded.
RIVALS_PAST_CAP = "rivals_past_cap"


#: The provenance key an untargeted winner carries where the run measured its
#: formula-only shortlist (step 4.15,
#: ``alternatives_scoring.seed_shortlists``, ``rival_set``): each formula, with the
#: ion, fit and mass error of the adduct that placed it on the peak best, or
#: ``placed: False`` where no adduct of the run places it there. An empty list
#: on a winner whose shortlist held no such formula. Absent on a winner the
#: pass did not reach, so its presence is what says the shortlist was
#: measured; the identification level reads it (``identification_level``).
MEASURED_SHORTLIST = "measured_shortlist"


def cap_alternatives(
    row: dict, entries: list, max_alternatives: int | None
) -> list | None:
    """Store a row's alternatives under the cap, counting the rivals it cuts.

    Every place that writes a row's ``alternatives`` cuts the list at the run's
    ``max_alternatives``; this does the cut and adds the distinct rival
    formulas it left out to :data:`RIVALS_PAST_CAP`, so the row can say that
    its shortlist is not all stored. A formula is a rival when it is not the
    row's own, is not a reading of the row's own ion (``same_ion``) and has no
    entry left in the stored list.

    :param row: The row, whose provenance is updated in place.
    :param entries: The alternatives in the order they rank.
    :param max_alternatives: The run's cap; 0 or None stores none.
    :return: The entries kept, or None where none is.
    """
    cap = max(int(max_alternatives or 0), 0)
    kept = list(entries[:cap])
    listed = {
        formula_identity(entry.get("assigned_formula"))
        for entry in kept
        if isinstance(entry, dict)
    }
    own = formula_identity(row.get("assigned_formula"))
    cut = (
        {
            formula_identity(entry.get("assigned_formula"))
            for entry in entries[cap:]
            if isinstance(entry, dict)
            and entry.get("assigned_formula")
            and not entry.get("same_ion")
        }
        - listed
        - {own}
    )
    if cut:
        provenance = row.get("provenance")
        if not isinstance(provenance, dict):
            provenance = {}
            row["provenance"] = provenance
        provenance[RIVALS_PAST_CAP] = int(provenance.get(RIVALS_PAST_CAP) or 0) + len(
            cut
        )
    return kept or None


def _restates_winner(contenders: "pd.DataFrame", winner) -> "pd.Series":
    """Mask of contender rows that restate the winner's own hypothesis.

    Same formula through the same ionization mechanism is one explanation of the
    peak, however many rows carried it into the frame and however each spelled
    it (:func:`formula_identity`) - the reference mirror sits in the same frame
    as the curated targets, so a compound that is both a target and a known
    reference contributes two rows, and two targets can share a formula
    outright. Excluding the winner by position alone left those twins in
    `alternatives`, where the inspector rendered them exactly like the committed
    assignment: the peak's own answer offered back as a close alternative.

    The isotope label adds nothing to that identity. Within one ion the matcher
    already forbids two isotopes from holding the same peak - `_match_assign`
    keeps a per-ion set of claimed peaks and awards a contested one to the higher
    relative abundance - so the winner's own ion is represented here exactly once.
    Two ions that share a formula and a mechanism can still claim one peak through
    different isotopologues, and that row is dropped as well: an alternative
    carries no isotope label, so it would render as the bare committed formula,
    which is the duplicate this screen exists to remove.

    :param contenders: The peak's rows other than the winning one, carrying
        ``_formula_key``.
    :param winner: The row that won the peak.
    :return: Boolean mask, True where the row is the winner's hypothesis again.
    """
    same = contenders["_formula_key"] == str(winner["_formula_key"])
    if "ionization_mechanism_id" in contenders.columns:
        same &= contenders["ionization_mechanism_id"].astype(str) == str(
            winner.get("ionization_mechanism_id")
        )
    return same


def _reference_copies(matched: "pd.DataFrame") -> "pd.Series":
    """Mask of reference mirror rows that restate a target library row on their peak.

    A copy is the same neutral (:func:`formula_identity`) through the same
    mechanism on the same peak: the same ion, so the same line of it.

    :param matched: The gated Stage A frame, carrying ``_formula_key``.
    :return: Boolean mask on the frame's index, True for a mirror row whose
        reading the target library also makes on that peak.
    """
    mirror = reference_mirror_mask(matched)
    if not mirror.any():
        return mirror
    mechanism = (
        matched["ionization_mechanism_id"].astype(str)
        if "ionization_mechanism_id" in matched.columns
        else pd.Series("", index=matched.index)
    )
    readings = list(
        zip(
            matched["sample_peak_id"].astype(str),
            matched["_formula_key"],
            mechanism,
            strict=True,
        )
    )
    library = {
        reading
        for reading, is_mirror in zip(readings, mirror, strict=True)
        if not is_mirror
    }
    return pd.Series(
        [
            bool(is_mirror) and reading in library
            for reading, is_mirror in zip(readings, mirror, strict=True)
        ],
        index=matched.index,
        dtype=bool,
    )


def _ion_keys(group: "pd.DataFrame") -> "pd.Series":
    """Each Stage A candidate's ion, as one ion is told from another
    (``utils.ion_identity``); its neutral where the frame names no ion, so a
    candidate without one is still a hypothesis of its own.

    :param group: One peak's candidates, carrying ``_formula_key``.
    :return: The keys, on the group's index.
    """
    ions = (
        group["target_ion_formula"]
        if "target_ion_formula" in group.columns
        else pd.Series(None, index=group.index, dtype=object)
    )
    return pd.Series(
        [
            ion_identity(_str_or_none(ion)) or ("", str(key))
            for ion, key in zip(ions, group["_formula_key"], strict=True)
        ],
        index=group.index,
        dtype=object,
    )


def _peak_candidates(matched: pd.DataFrame) -> Iterator[tuple]:
    """Each peak's candidates in a Stage A frame sorted by peak and rank:
    the peak, its winner (the first row) and every candidate as a frame, or
    None where the winner is the only one, which on a judging's frame is
    nearly every peak (MAS-1172). The peaks come in the order
    ``groupby("sample_peak_id", sort=False)`` gives them: the sort keeps each
    peak's rows together.
    """
    peaks = matched["sample_peak_id"].to_numpy(dtype=object)
    starts = [0, *np.flatnonzero(peaks[1:] != peaks[:-1]) + 1, len(peaks)]
    for start, end in itertools.pairwise(starts):
        if end - start == 1:
            yield peaks[start], matched.iloc[start], None
        else:
            group = matched.iloc[start:end]
            yield peaks[start], group.iloc[0], group


def _contenders(
    winner: pd.Series, group: pd.DataFrame | None, max_alternatives: int
) -> tuple[list[tuple[pd.Series, bool]], set[str], list[tuple]]:
    """What a peak's other candidates are to its winner
    (:func:`invert_matches_to_peak_assignments`).

    :param winner: The row that won the peak.
    :param group: Every candidate of the peak, the winner first; None where
        the winner is the only one, which leaves no runner, nothing past the
        cap and one reading to arbitrate.
    :param max_alternatives: Cap on stored runner-up candidates.
    :return: The runners-up, each with whether it reads the winner's own ion;
        the rival formulas the cap leaves unstored; and the readings the
        arbitration ranks, one per ion.
    """
    if group is None:
        return [], set(), [(str(winner["_formula_key"]), float(winner["_fit"]))]
    # The winner is never its own alternative. Dropping the rows that merely
    # restate it BEFORE the cap matters twice over: the duplicate never
    # reaches the inspector, and it does not burn one of the `max_alternatives`
    # slots that a genuinely different formula could have had.
    contenders = group.iloc[1:]
    rivals = contenders[~_restates_winner(contenders, winner)]
    runners = rivals.iloc[:max_alternatives] if max_alternatives else rivals.iloc[:0]
    # The rival formulas the cap leaves unstored, which the row counts
    # (`RIVALS_PAST_CAP`): a rival it does not store is one no reader can
    # see tested. The winner's own formula through another mechanism is no
    # rival, and neither is a reading of the winner's own ion, as
    # `cap_alternatives` leaves out `same_ion` entries.
    ion_keys = _ion_keys(group)
    winner_ion = ion_keys.iloc[0]
    past_cap: set[str] = set()
    if len(rivals) > len(runners):
        past_cap = (
            {
                str(key)
                for key, ion in zip(
                    rivals["_formula_key"],
                    ion_keys.loc[rivals.index],
                    strict=True,
                )
                if ion != winner_ion
            }
            - set(runners["_formula_key"])
            - {str(winner["_formula_key"])}
        )
    # The peak's candidates are its IONS (the arbitration's comment in
    # :func:`invert_matches_to_peak_assignments`).
    readings = list(
        zip(
            group["_formula_key"][~ion_keys.duplicated()],
            group["_fit"][~ion_keys.duplicated()],
            strict=True,
        )
    )
    return (
        [
            (row, ion_key == winner_ion)
            for (_, row), ion_key in zip(
                runners.iterrows(), ion_keys.loc[runners.index], strict=True
            )
        ],
        past_cap,
        readings,
    )


def invert_matches_to_peak_assignments(
    match_isotope_df: pd.DataFrame,
    sample_item_id: str,
    peak_assignment_run_id: str,
    candidate_threshold: float,
    assigned_threshold: float,
    max_alternatives: int = 5,
    instrument: str | None = None,
    calibration: "Calibration | None | object" = _CALIBRATION_UNSET,
) -> list[dict]:
    """Invert target-first match results into per-peak assignments (Stage A).

    The targeted matcher produces one row per target isotope with the
    sample_peak_id it hit (or a placeholder when unmatched). This groups those
    rows by peak, picks the best-scoring owner per peak (ties broken by
    smaller m/z error), and keeps the runners-up as alternatives - the
    single-owner-per-peak invariant.

    Roles: the winner is 'M0' when it is its ion's monoisotopic isotopologue -
    the M0 an isotope table counts from, the lightest peak of a bromine-rich
    cluster rather than the tallest, the labelled line of a labelled ion rather
    than the unlabelled remainder below it - otherwise 'iso_child' pointing at
    the assignment that holds the ion's M0 peak. An isotopologue is written
    only beside that assignment. One whose ion did not win its M0 peak here
    would say its peak is part of an envelope whose ion the ledger never
    commits, so it is left out and the peak goes to the untargeted stage - the
    rule that stage keeps for its own rows.

    :param match_isotope_df: Output of compute_match_isotopes enriched with
        target metadata columns (target_compound_id, target_compound_formula,
        target_ion_formula, ionization_mechanism_id).
    :param sample_item_id: Sample the peaks belong to.
    :param peak_assignment_run_id: Run the assignments belong to.
    :param candidate_threshold: Evidence threshold for the 'candidate' tier.
    :param assigned_threshold: Evidence threshold for the 'assigned' tier.
    :param max_alternatives: Cap on stored runner-up candidates per peak.
    :param instrument: Instrument class; selects the in-code calibration when ``calibration``
        is not passed. ``None`` -> uncalibrated.
    :param calibration: The confidence calibration to apply (from the D6 store). If omitted,
        falls back to the in-code registry via ``calibration_for(instrument)``; pass ``None``
        explicitly to force uncalibrated. Its ``corroboration_weights`` drive the P3 adduct
        co-occurrence fold-in on ``p_correct``.
    :return: One assignment dict per matched peak, ready for bulk insert.
    """
    if match_isotope_df.empty:
        return []

    # A peak may only be OWNED by a target isotopologue the gating accepted. The targeted
    # matcher pairs each target isotope to the nearest peak in a wide 0.5 Da search window
    # (for the legacy Match tab's ppm-error display), then `apply_match_params` zeroes
    # `sample_peak_intensity` for pairings outside the m/z tolerance and
    # `score_ions_by_fit` zeroes it for the ones the gating rejected on the line's height or
    # the intensity floor (both legs of one gating decision, see its docstring). Without this guard a trace
    # isotopologue whose real peak is absent claims whatever peak sits in that window - tens
    # to hundreds of ppm off, or below the floor the scorer treated as no peak at all, and
    # actually belonging to another compound - inheriting its ion's tier and blocking that
    # peak's correct assignment (Stage B or another target). Requiring a positive gated
    # intensity releases those peaks to the untargeted stage instead.
    matched = match_isotope_df[
        match_isotope_df["sample_peak_id"].notna()
        & (match_isotope_df["sample_peak_id"] != "")
        & (match_isotope_df["sample_peak_intensity"].fillna(0) > 0)
    ].copy()
    if matched.empty:
        return []

    # Reference identities keyed by canonical formula, drawn from the reference
    # rows in the frame. A peak's winner - target OR reference - inherits its
    # formula's identities in provenance, so a curated target keeps its
    # target_compound_id while still surfacing the known-compound name(s) that
    # share its formula (the convergence precedence: target owns the FK, reference
    # identity rides alongside).
    reference_identities_by_formula: dict[str, list] = {}
    if REFERENCE_IDENTITIES_COL in match_isotope_df.columns:
        reference_rows = match_isotope_df[reference_mirror_mask(match_isotope_df)]
        for formula, group in reference_rows.groupby(
            reference_rows["target_compound_formula"].map(formula_identity)
        ):
            reference_identities_by_formula[str(formula)] = group.iloc[0][
                REFERENCE_IDENTITIES_COL
            ]

    # Reference isotope per ion - the monoisotopic isotopologue, the M0 an isotope
    # table counts from - used for role attribution and isotope labelling. For a
    # bromine- or chlorine-rich ion that is the lightest peak of the cluster, not
    # the most intense one. Computed over the full target set so an ion whose M0
    # went unmatched still labels its isotopologues correctly where they are
    # listed as a peak's alternatives.
    references = match_isotope_df.iloc[monoisotopic_positions(match_isotope_df)]
    main_isotope_ids = set(references["target_isotope_id"])
    main_mz_by_ion = {
        str(ion): float(mz)
        for ion, mz in zip(references["target_ion_id"], references["mz"], strict=True)
    }

    # Arbitration (P2): rank a peak's competing candidates by evidence =
    # fit x chemical plausibility, not fit alone, so a chemically implausible formula
    # cannot win a peak on mass fit. The stored fit_score stays the pure measurement,
    # but evidence now decides the winner, the reported confidence AND the tier - the
    # three used to disagree, and a formula that won a peak on evidence could then be
    # banded as though it had fit cleanly.
    #
    # Every comparison of two candidates' formulas below is of their identities, so
    # one neutral spelled two ways is one hypothesis (:func:`formula_identity`).
    matched["_formula_key"] = (
        matched["target_compound_formula"].astype(str).map(formula_identity)
    )
    plaus_by_formula = {
        f: formula_plausibility(f) for f in matched["_formula_key"].unique()
    }
    matched["_plaus"] = matched["_formula_key"].map(plaus_by_formula)
    # A reference list's copy of a reading the target library makes on the same
    # peak is that reading again, and the library's row owns it. The list's names
    # still ride along on it (above), but the copy never takes the line, whichever
    # of the two the matcher happened to fit a shade better.
    matched = matched[~_reference_copies(matched)].copy()
    # Calibration maps the winner's evidence to P(correct) for this instrument. None
    # when the instrument has no curated calibration (e.g. TOF) -> the assignment is
    # reported uncalibrated rather than borrowing another instrument's curve. The service
    # passes the calibration from the D6 store; falling back to the in-code registry keeps
    # direct callers (and unit tests) working unchanged.
    if calibration is _CALIBRATION_UNSET:
        calibration = calibration_for(instrument)
    matched["_fit"] = matched["match_score"].map(lambda v: _score_or_none(v) or 0.0)
    matched["_evidence"] = matched["_fit"] * matched["_plaus"]
    matched["_abs_mz_error"] = matched["match_mz_error"].abs()
    # This row sort selects the winning ROW only; confidence and ties are delegated
    # to `arbitration.arbitrate_candidates` per peak below. The selection cannot be
    # delegated because the winner has to keep the whole match row - target FKs,
    # reference identities, isotope role, mass and abundance errors - which the
    # library's (formula, fit) view cannot carry, and because mass error is the
    # domain-meaningful middle key: between two equally plausible formulas that fit
    # equally well, the closer mass is the better assignment. The formula key is last
    # so two candidates equal on all three still resolve by the data rather than by
    # whatever order the matcher happened to emit them in.
    matched = matched.sort_values(
        ["sample_peak_id", "_evidence", "_abs_mz_error", "_formula_key"],
        ascending=[True, False, True, True],
    )

    assignments: list[dict] = []
    m0_assignment_by_ion: dict[str, str] = {}
    child_assignments: list[tuple[dict, str]] = []
    # (assignment, compound_id, adduct notation) for M0 winners, for the P3 corroboration
    # post-pass (a compound seen via several adducts lifts each one's p_correct).
    m0_corroboration: list[tuple[dict, str, str]] = []

    for sample_peak_id, winner, group in _peak_candidates(matched):
        runners, past_cap, readings = _contenders(winner, group, max_alternatives)
        # Arbitration confidence and the tie flag come from the shared
        # `arbitrate_candidates`, not an inline copy - one implementation, so a fix
        # in the library reaches the engine (issue #1731). Delegating buys the two
        # behaviours the inline copy had lost: duplicate formulas are COLLAPSED
        # before normalisation (the same formula arriving via two adducts is one
        # hypothesis, not two competitors splitting their own confidence into a
        # self-tie), and the tie gap is RELATIVE to the best evidence with an
        # absolute floor, instead of one absolute gap that called nearly
        # everything below 0.1 apart a tie.
        #
        # Confidence answers "which of this peak's candidates", NOT "how good is
        # this assignment" - an uncontested peak scores 1.0 however poorly its
        # single candidate fits, because there was nothing to lose to.
        # `n_candidates` is recorded alongside it so a 1.0 earned against
        # competitors is distinguishable from a 1.0 won by default; "how good" is
        # `p_correct` (the calibrated evidence), which does not divide by the
        # field and does fall for a poor lone candidate.
        #
        # And the peak's candidates are its IONS. Two list compounds that make
        # one ion through two channels (C10H18O3 through [M+Br]-, C10H19BrO3
        # through [M-H]-) share its mass and its envelope, so the spectrum
        # cannot tell them apart and neither is the other's rival: they are
        # one hypothesis, its best reading standing for it, as the search
        # elects one reading per ion before it ranks anything. The other
        # reading stays on the row as a reading of its ion (`same_ion`).
        arbitrated = arbitrate_candidates(readings)
        winner_arbitrated = next(
            c for c in arbitrated if c.formula == str(winner["_formula_key"])
        )
        confidence = winner_arbitrated.confidence
        is_tie = winner_arbitrated.is_tie

        # The winner's evidence, rounded once here and used for both the tier and
        # the provenance the ledger displays. Two roundings of one quantity is how
        # a row ends up in the band below the percentage its own chip shows.
        evidence = round(float(winner["_evidence"]), 4)

        # Calibrated P(correct) for the winner's evidence - only when this instrument
        # has a calibration; otherwise the assignment is honestly left uncalibrated.
        # Which curve it was is the run's to record (`calibration_meta`), not each
        # row's: one curve serves a whole run.
        p_correct = (
            round(float(apply_calibration(float(winner["_evidence"]), calibration)), 4)
            if calibration is not None
            else None
        )
        alternatives = [
            {
                **_alternative_dict(
                    row,
                    reference_identities_by_formula,
                    main_isotope_ids,
                    main_mz_by_ion,
                ),
                # Another reading of the winner's own ion, not a rival.
                **({"same_ion": True} if same_ion else {}),
            }
            for row, same_ion in runners
        ]

        ion_id = _str_or_none(winner.get("target_ion_id"))
        # A reference-*row* winner has synthetic target ids that must not persist as
        # FKs. Separately, any winner (target or reference) whose *formula* is in the
        # reference mirror inherits those identities in provenance.
        winner_is_reference_row = _row_reference_identities(winner) is not None
        formula_identities = reference_identities_by_formula.get(
            str(winner["_formula_key"])
        )
        is_main = winner["target_isotope_id"] in main_isotope_ids
        isotope_label = (
            "M0"
            if is_main
            else _isotope_offset_label(winner["mz"], main_mz_by_ion.get(ion_id))
        )

        assignment = {
            "peak_assignment_id": gen_id(32),
            "peak_assignment_run_id": peak_assignment_run_id,
            "sample_item_id": sample_item_id,
            "sample_peak_id": str(sample_peak_id),
            "sample_peak_mz": float(winner["sample_peak_mz"]),
            "sample_peak_intensity": float(winner["sample_peak_intensity"]),
            "sample_peak_tof": _float_or_none(winner.get("sample_peak_tof")),
            "role": ROLE_M0 if is_main else ROLE_ISO_CHILD,
            "assigned_formula": _str_or_none(winner.get("target_compound_formula")),
            "ion_formula": _str_or_none(winner.get("target_ion_formula")),
            "ionization_mechanism_id": _str_or_none(
                winner.get("ionization_mechanism_id")
            ),
            "isotope_label": isotope_label,
            "isotope_formula": fit_isotope_formula(
                winner.get("target_isotope_formula")
            ),
            "source": SOURCE_DATABASE,
            "fit_score": _score_or_none(winner["match_score"]),
            "mz_error_ppm": _float_or_none(winner["match_mz_error"]),
            "abundance_error": _float_or_none(winner["match_abundance_error"]),
            # Tiered on the evidence this peak was WON with, not on the fit alone:
            # the same product that beat the runners-up above decides which band
            # the winner lands in, so a formula that only won because nothing more
            # plausible competed cannot also claim the top tier on mass fit.
            #
            # The ROUNDED value, which is also what provenance records and the
            # ledger shows beside the tier. Tiering the full-precision product
            # instead would put a row reading 0.7499996 into the band below the
            # 75% its own chip displays.
            "tier": tier_for_evidence(
                evidence,
                candidate_threshold=candidate_threshold,
                assigned_threshold=assigned_threshold,
            ),
            "target_compound_id": _str_or_none(winner.get("target_compound_id")),
            # A reference-row winner carries only a synthetic ion id (for in-run
            # grouping), never persisted as a dangling FK; a target winner keeps its.
            "target_ion_id": None if winner_is_reference_row else ion_id,
            "owner_peak_assignment_id": None,
            "alternatives": alternatives or None,
            "provenance": {
                "confidence": round(confidence, 4),
                # How many DISTINCT formulas the confidence was normalised across
                # (duplicate arrivals of one formula collapse); 1 means the peak
                # was uncontested and the 1.0 above was won by default.
                "n_candidates": int(len(arbitrated)),
                # ...and how many of them the evidence could not SEPARATE, which
                # is the different question: a peak with nine candidates whose
                # winner clears them all is not the same peak as one with three
                # the evidence ranks equally. Read off the arbitration above
                # rather than competed again.
                #
                # On the MAIN row only. This loop writes one provenance blob for
                # every row of a winner's envelope, and an isotopologue was never
                # searched - it is a line predicted from the winner and matched,
                # so the peak's candidate count is not a measurement of it. The
                # untargeted stage drops it from a child for the same reason
                # (`finder.process_isotopes`), and the schema says an isotopologue
                # carries none.
                #
                # `around` cannot change the number HERE and is passed anyway:
                # the row sort above orders by `_evidence`, which is the same
                # product the arbitration ranks on, so this stage's winner is
                # always the arbitration's top and the anchored count is the top
                # tie set by construction. It matters in the untargeted stage,
                # where the finder ranks on the fit alone. Passing it keeps the
                # count anchored on what the row commits if this selection ever
                # stops agreeing - it is not pinned by a test, because no input
                # can make the two disagree while that sort key stands.
                **(
                    {
                        CANDIDATE_DENSITY: density_of(
                            arbitrated, around=str(winner["_formula_key"])
                        )
                    }
                    if is_main
                    else {}
                ),
                "plausibility": round(float(winner["_plaus"]), 4),
                "evidence": evidence,
                "is_tie": is_tie,
                **({RIVALS_PAST_CAP: len(past_cap)} if past_cap else {}),
                # The fit-score generation this evidence is on. Snapshotted here because
                # a verification label is only interpretable against the scoring that
                # produced it (see AssignmentVerification.score_version).
                "score_version": SCORE_VERSION,
                # P(correct) is the calibrated probability; null when uncalibrated,
                # so the UI can show "uncalibrated" instead of a fabricated number.
                # The curve it was read off is on the run; the detail read folds
                # it back in here as `calibrated` / `calibration`.
                "p_correct": p_correct,
                # One-to-many known-compound identities for a database-sourced peak
                # whose formula is in the reference mirror (name/source/license),
                # attached whether the target library or the reference set won it.
                **(
                    {"reference_identities": formula_identities}
                    if formula_identities
                    else {}
                ),
            },
        }
        assignments.append(assignment)

        if not is_main:
            child_assignments.append((assignment, ion_id))
        elif ion_id is not None:
            m0_assignment_by_ion[ion_id] = assignment["peak_assignment_id"]
            compound_id = _str_or_none(winner.get("target_compound_id"))
            notation = _str_or_none(winner.get("ionization_mechanism"))
            if compound_id and notation:
                m0_corroboration.append((assignment, compound_id, notation))

    # Attribute each isotopologue to its ion's M0 assignment, and leave out the
    # ones whose ion did not win its M0 peak in this run: their peaks go to the
    # untargeted stage. Settled after the loop, because an ion's M0 peak may be
    # decided after its isotopologue's.
    orphaned: set[str] = set()
    for assignment, ion_id in child_assignments:
        owner = m0_assignment_by_ion.get(ion_id)
        if owner is None:
            orphaned.add(assignment["peak_assignment_id"])
        else:
            assignment["owner_peak_assignment_id"] = owner
    if orphaned:
        assignments = [
            assignment
            for assignment in assignments
            if assignment["peak_assignment_id"] not in orphaned
        ]
        runtime.logger.info(
            f"Stage A: {len(orphaned)} isotopologue rows whose ion did not win its "
            f"monoisotopic peak were not written; their peaks go to the untargeted "
            f"stage."
        )

    # P3 corroboration: fold co-occurring adducts of the same compound into p_correct.
    if calibration is not None:
        _fold_adduct_corroboration(m0_corroboration, calibration.corroboration_weights)

    return assignments


def _fold_adduct_corroboration(
    m0_items: list[tuple[dict, str, str]],
    weights: "dict | None",
    tier_of: Callable[[dict], str] | None = None,
) -> None:
    """Fold adduct co-occurrence into each M0 winner's ``p_correct`` in place (P3).

    A compound assigned via several confident adducts corroborates each of them: for each winner
    we add the measured log-odds of the OTHER adducts its compound was seen via (see
    ``apply_corroboration``). Only confident (assigned/candidate) winners count toward the
    co-occurrence set, so a low-confidence sibling can't manufacture corroboration. No-op when the
    calibration carries no weights. Records the co-occurrence + boost in provenance for the UI.

    ``tier_of`` reads the tier a winner counts at; left out, the tier it was built at, which is
    the band its evidence earned (:func:`read_p_correct_again` passes that band at the moved
    evidence)."""
    if not weights or not m0_items:
        return
    # Keyed by mechanism once for the fold rather than once per winner.
    weights = corroboration_by_mechanism(weights)
    tier_of = tier_of or (lambda assignment: assignment["tier"])
    adducts_by_compound: dict[str, set[str]] = {}
    for assignment, compound_id, notation in m0_items:
        if tier_of(assignment) in (TIER_ASSIGNED, TIER_CANDIDATE):
            adducts_by_compound.setdefault(compound_id, set()).add(notation)
    for assignment, compound_id, notation in m0_items:
        all_adducts = adducts_by_compound.get(compound_id, set())
        others = all_adducts - {notation}
        if not others:
            continue
        prov = assignment["provenance"]
        p0 = prov.get("p_correct")
        p1 = apply_corroboration(p0, sorted(others), weights)
        prov["p_correct"] = round(p1, 4) if p1 is not None else None
        prov["corroboration"] = {
            "adducts": sorted(all_adducts),
            "n_adducts": len(all_adducts),
            "boost": round(p1 - p0, 4) if (p0 is not None and p1 is not None) else None,
        }


def read_p_correct_again(
    rows: list[dict],
    moved_ids: set[str],
    calibration: Calibration,
    notation_by_mechanism: dict[str, str],
    *,
    candidate_threshold: float,
    assigned_threshold: float,
) -> int:
    """Read the rows' ``p_correct`` again where a re-score moved their
    evidence, in place, as Stage A reads it
    (:func:`invert_matches_to_peak_assignments`), the one stage that writes
    one.

    A moved row's ``p_correct`` is the curve's at its new evidence. The adduct
    corroboration is then folded again over every monoisotopic row of each
    compound a moved row names (:func:`_fold_adduct_corroboration`), each one
    counting at the band its evidence now earns, as Stage A counts it at the
    band it was built at: a sibling that fell out of the confident bands no
    longer corroborates, and one that rose into them does. A row of another
    compound keeps its number as Stage A wrote it.

    :param rows: The run's rows.
    :param moved_ids: The rows whose evidence the re-score moved.
    :param calibration: The curve Stage A read the rows' ``p_correct`` off.
    :param notation_by_mechanism: The notation each mechanism's adduct was
        folded under, as Stage A's frame names it.
    :param candidate_threshold: The run's candidate band.
    :param assigned_threshold: The run's assigned band.
    :return: How many rows took a new ``p_correct``.
    """
    stage_a = [
        row
        for row in rows
        if row.get("role") in (ROLE_M0, ROLE_ISO_CHILD)
        and isinstance(row.get("provenance"), dict)
        and row["provenance"].get("p_correct") is not None
        and row["provenance"].get("evidence") is not None
    ]

    def compound(row: dict) -> str | None:
        return _str_or_none(row.get("target_compound_id"))

    touched = {
        compound(row)
        for row in stage_a
        if str(row.get("peak_assignment_id")) in moved_ids
        and row["role"] == ROLE_M0
        and compound(row) is not None
    }
    read: list[dict] = []
    folded: list[tuple[dict, str, str]] = []
    for row in stage_a:
        moved = str(row.get("peak_assignment_id")) in moved_ids
        refold = row["role"] == ROLE_M0 and compound(row) in touched
        if not (moved or refold):
            continue
        provenance = row["provenance"]
        provenance["p_correct"] = round(
            float(apply_calibration(float(provenance["evidence"]), calibration)), 4
        )
        provenance.pop("corroboration", None)
        read.append(row)
        notation = notation_by_mechanism.get(str(row.get("ionization_mechanism_id")))
        if refold and notation:
            folded.append((row, str(compound(row)), notation))

    def earned(row: dict) -> str:
        return tier_for_evidence(
            float(row["provenance"]["evidence"]),
            candidate_threshold=candidate_threshold,
            assigned_threshold=assigned_threshold,
        )

    _fold_adduct_corroboration(folded, calibration.corroboration_weights, earned)
    return len(read)


def record_stage_a_same_ion_readings(
    assignments: list[dict],
    *,
    mechanism_id_by_notation: dict[str, str],
    search_config: CompositionSearchConfig,
    heuristics_config: HeuristicFilterConfig,
    formula_formatter=None,
    max_alternatives: int = 5,
) -> int:
    """Give each Stage A row the other readings of its ion.

    An election leaves the readings it displaced on its winner, flagged
    ``same_ion``. A Stage A row was matched rather than elected, so it arrives
    with none, and the question they answer is as open on it: the ion a list's
    formula makes through one mechanism is, to the same mass and envelope,
    another neutral's through another. This writes the family the one
    enumerator gives that ion (``heuristic_filter.propose_same_ion_readings``,
    the search's own ``same_ion_family``) the way an election's displaced
    readings are written: ahead of the row's scored rivals, and carrying the
    row's own fit and mass error, since the ion and so the measurement is the
    same.

    A rival already on the row that restates a reading - the same neutral
    through the same mechanism, which the known set can hold as a compound of
    its own - is that reading, so it is flagged and moved ahead rather than
    repeated.

    A reference list's row and a target library's row alike: the library's
    formula is the workspace's own curation (:func:`is_target_library_row`),
    which settles which reading of the ion the row is
    (``cross_channel.same_ion_question``), and the other readings are still
    what the peak's ion reads as. Only a monoisotopic row gets them, as in an
    election: an isotopologue is its owner's ion on another line.

    :param assignments: Every row built for this sample; Stage A rows are
        modified in place.
    :param mechanism_id_by_notation: The mechanisms the run searched, in the
        finder's notation, mapped to their ids.
    :param search_config: The untargeted search's configuration, whose element
        box a reading has to sit in.
    :param heuristics_config: The untargeted search's heuristic filter.
    :param formula_formatter: How the untargeted stage writes a formula.
    :param max_alternatives: Cap on stored alternatives per row.
    :return: How many Stage A rows carry at least one reading.
    """
    return record_same_ion_readings(
        [row for row in assignments if row.get("source") == SOURCE_DATABASE],
        mechanism_id_by_notation=mechanism_id_by_notation,
        search_config=search_config,
        heuristics_config=heuristics_config,
        formula_formatter=formula_formatter,
        max_alternatives=max_alternatives,
    )


def record_same_ion_readings(
    rows: list[dict],
    *,
    mechanism_id_by_notation: dict[str, str],
    search_config: CompositionSearchConfig,
    heuristics_config: HeuristicFilterConfig,
    formula_formatter=None,
    max_alternatives: int = 5,
) -> int:
    """Give each monoisotopic row the other readings of its ion: the family
    the untargeted search would have given that ion, written as an election's
    displaced readings are (:func:`record_stage_a_same_ion_readings` says how).

    Stage A's rows take it, which were matched rather than elected, and so
    does a batch decision measured on a sample and read as the search's
    row there (``pipeline``), which a search of the sample would have elected
    out of that family. Each reading carries the row's own source, as an
    election's do.

    :param rows: The rows to give a family; modified in place. An isotopologue,
        a row with no formula and one through a mechanism the run did not
        search are left as they are.
    :param mechanism_id_by_notation: The mechanisms the run searched, in the
        finder's notation, mapped to their ids.
    :param search_config: The untargeted search's configuration, whose element
        box a reading has to sit in.
    :param heuristics_config: The untargeted search's heuristic filter.
    :param formula_formatter: How the untargeted stage writes a formula.
    :param max_alternatives: Cap on stored alternatives per row.
    :return: How many rows carry at least one reading.
    """
    notation_by_id = {
        str(mechanism_id): notation
        for notation, mechanism_id in mechanism_id_by_notation.items()
    }
    format_formula = formula_formatter or (lambda formula: formula)
    rows = [
        row
        for row in rows
        if row.get("role") == ROLE_M0
        and row.get("assigned_formula")
        and str(row.get("ionization_mechanism_id")) in notation_by_id
    ]
    families = propose_same_ion_readings(
        [
            (
                str(row["assigned_formula"]),
                notation_by_id[str(row["ionization_mechanism_id"])],
            )
            for row in rows
        ],
        list(mechanism_id_by_notation),
        search_config,
        heuristics_config,
    )
    carrying = 0
    for row, family in zip(rows, families, strict=True):
        if not family:
            continue
        rivals = list(row.get("alternatives") or [])
        readings = []
        for member in family:
            mechanism_id = mechanism_id_by_notation[member["ionization_mechanism"]]
            counts = element_counts(member["formula"])
            restated = next(
                (
                    index
                    for index, rival in enumerate(rivals)
                    if str(rival.get("ionization_mechanism_id")) == str(mechanism_id)
                    and element_counts(str(rival.get("assigned_formula") or ""))
                    == counts
                ),
                None,
            )
            if restated is not None:
                readings.append({**rivals.pop(restated), "same_ion": True})
                continue
            readings.append(
                {
                    "assigned_formula": format_formula(member["formula"]),
                    "ion_formula": row.get("ion_formula"),
                    "ionization_mechanism_id": mechanism_id,
                    "isotope_label": row.get("isotope_label"),
                    "fit_score": row.get("fit_score"),
                    "mz_error_ppm": row.get("mz_error_ppm"),
                    "plausibility": round(
                        float(formula_plausibility(member["formula"])), 4
                    ),
                    "same_ion": True,
                    "source": row.get("source"),
                }
            )
        row["alternatives"] = cap_alternatives(row, readings + rivals, max_alternatives)
        carrying += 1
    return carrying


#: The provenance block a list hit carries once the formula search has been
#: asked about its peak: the grid's rivals its density counts.
GRID_RIVALS = "grid_rivals"

#: The provenance block a search row carries where it took a list hit's peak:
#: the list reading it displaced and the evidence the two were weighed on.
LIST_READING = "list_reading"

#: How much a list reading's evidence counts for against the formula search's.
#: A rival takes a list hit's peak only where its evidence (fit times
#: plausibility) exceeds twice the reading's, and then by more than a tie (the
#: plan owner's answer on step 2.5f). Measured before it was taken, it lets the
#: grid take the peaks of readings the run already held below assignability,
#: and none on which the reference commits the list's formula.
LIST_PRIOR_WEIGHT = 2.0

#: How many of those rivals a row keeps by name. The count is on the row in
#: full; the names are for a reader, as a row's alternatives are.
GRID_RIVALS_KEPT = 5


def record_grid_rivals(rows: list[dict], measured: list) -> dict:
    """Record what the formula search held against each list hit's peak.

    A list hit's rivals are measured with the rest of its peak's, in the frame
    its own reading is and at the model the run caps at, and counted there
    into its density (``rival_set.measure_rival_sets``); the grid's are among
    them, seeded beside the search's readings (``ListElection.rivals_by_peak``).
    What this records is that the search measured the peak, and what it saw
    there at its own scale: the rivals its ranking could not separate from the
    reading, by name, the reading's fit beside them, how many candidates the
    window held, whether the searched element box holds the reading's formula,
    and the rival that cleared the list's prior and still did not take the peak.
    No count of them reaches the density from here.

    :param rows: The Stage A monoisotopic rows that were measured, modified in
        place.
    :param measured: What :func:`finder.rivals_of_readings` found for each, in
        the same order; None where it could not measure a row.
    :return: What the run records: how many rows were measured, and how many
        the search named a rival against.
    """
    with_rivals = 0
    for row, found in zip(rows, measured, strict=True):
        if found is None:
            continue
        provenance = row.get("provenance")
        if not isinstance(provenance, dict):
            provenance = {}
            row["provenance"] = provenance
        provenance[GRID_RIVALS] = {
            # The search's own ranking: the rivals it named, at its scale.
            "rivals": [
                {
                    "formula": rival["formula"],
                    "ion_formula": rival["ion"],
                    "ionization_mechanism": rival["ionization_mechanism"],
                    "fit_score": round(float(rival["fit_score"]), 4),
                    "mz_error_ppm": (
                        None
                        if rival["mz_error_ppm"] is None
                        else round(float(rival["mz_error_ppm"]), 4)
                    ),
                }
                for rival in found.rivals[:GRID_RIVALS_KEPT]
            ],
            "fit_score": round(float(found.fit_score), 4),
            "grid_candidates": found.candidates,
            # Uniqueness is relative to the searched box: a formula outside it
            # only meets the rivals the box builds.
            "in_grid": found.in_grid,
            **(
                {"held_against": _held_against_record(found.held_against)}
                if found.held_against
                else {}
            ),
        }
        with_rivals += bool(found.rivals)
    return {
        "measured": sum(1 for found in measured if found is not None),
        "with_rivals": with_rivals,
    }


def _held_against_record(held: dict) -> dict:
    """The rival a list hit kept its peak against, as the row records it."""
    return {
        "formula": held["formula"],
        "ion_formula": held["ion"],
        "ionization_mechanism": held["ionization_mechanism"],
        "fit_score": round(float(held["fit_score"]), 4),
        "prior": held["prior"],
        "why": held["why"],
        "unexplained_lines": [round(float(mz), 5) for mz in held["unexplained_lines"]],
    }


def list_readings(
    stage_a_assignments: list[dict],
    peaks_df: pd.DataFrame,
    notation_by_id: dict[str, str],
) -> dict[str, ListReading]:
    """The list readings the formula search is asked about, by row id.

    Every monoisotopic Stage A row whose channel the search runs, at its peak's
    m/z as the search's own frame holds it, so the search can key the reading
    on the value it walks. A compound of the sample's own target library keeps
    its peak whatever the grid holds, and its rivals are still counted.

    :param stage_a_assignments: Stage A's rows.
    :param peaks_df: The frame the search is handed, with ``sample_peak_id``.
    :param notation_by_id: The searched mechanisms, by id.
    :return: Row id -> the reading.
    """
    mz_by_peak = dict(
        zip(
            peaks_df["sample_peak_id"].astype(str),
            peaks_df["mz"].astype(float),
            strict=True,
        )
    )
    readings: dict[str, ListReading] = {}
    for row in stage_a_assignments:
        notation = notation_by_id.get(str(row.get("ionization_mechanism_id")))
        mz = mz_by_peak.get(str(row.get("sample_peak_id")))
        if (
            row.get("role") != ROLE_M0
            or not row.get("assigned_formula")
            or notation is None
            or mz is None
        ):
            continue
        readings[str(row["peak_assignment_id"])] = ListReading(
            mz=mz,
            formula=str(row["assigned_formula"]),
            ionization_mechanism=notation,
            mz_error_ppm=_float_or_none(row.get("mz_error_ppm")),
            keeps_peak=is_target_library_row(row),
        )
    return readings


@dataclass
class ListElection:
    """How the formula search settled the peaks the lists had read.

    :param stage_a: Stage A's rows less the list hits a rival took and their
        isotopologues; the kept hits carry the grid's rivals.
    :param matches: The search's rows less the markers of kept readings.
    :param released: The peaks a rival took, with the lines of the list hits it
        took them from, which the search's rows may now hold.
    :param displaced: Peak id -> the list hit a rival took it from, and the
        weighing.
    :param summary: What the run records under its search scope.
    :param rivals_by_peak: The grid's rivals of each kept list hit, by its
        peak, which the run measures with the hit's other rivals
        (``rival_set.named_rival_seeds``).
    """

    stage_a: list[dict]
    matches: pd.DataFrame
    released: set[str]
    displaced: dict[str, tuple[dict, dict]]
    summary: dict
    rivals_by_peak: dict[str, tuple[dict, ...]] = field(default_factory=dict)


def settle_list_election(
    stage_a_assignments: list[dict],
    matches_df: pd.DataFrame,
    readings: dict[str, ListReading],
) -> ListElection:
    """Apply what the search decided on the peaks the lists had read.

    A kept reading leaves its row as Stage A built it, with what the search
    saw on its peak recorded (:func:`record_grid_rivals`) and the grid's rivals
    handed on by peak (:attr:`ListElection.rivals_by_peak`), which the run
    measures with the row's other rivals and counts into its density there
    (``rival_set.measure_rival_sets``). The run counts
    the readings a rival cleared the prior against and still did not displace:
    for the lines it left unexplained, or because the reading is the target
    library's. A reading a rival beat leaves the ledger with its isotopologues,
    and their peaks go to the search's rows.

    :param stage_a_assignments: Stage A's rows.
    :param matches_df: The search's result, with the known-peak markers.
    :param readings: The readings the search was asked about, by row id.
    :return: The settled rows and what the run records.
    """
    row_by_id = {str(row["peak_assignment_id"]): row for row in stage_a_assignments}
    row_id_by_mz = {reading.mz: row_id for row_id, reading in readings.items()}
    kept_rows: list[dict] = []
    kept_measured: list = []
    displaced: dict[str, tuple[dict, dict]] = {}
    commits = matches_df
    if not matches_df.empty and KNOWN_KEPT in matches_df.columns:
        # A frame holds a missing value on the rows that are not markers, and
        # numpy's bool where every row is one.
        is_kept = matches_df[KNOWN_KEPT].eq(True)
        for _, result in matches_df[is_kept].iterrows():
            row = row_by_id.get(row_id_by_mz.get(float(result["mz"]), ""))
            if row is not None:
                found = result.get(KNOWN_RIVALS)
                kept_rows.append(row)
                # A reading the search could not read was not measured.
                kept_measured.append(
                    found if isinstance(found, ReadingRivals) else None
                )
        commits = matches_df[~is_kept]
    if not commits.empty and KNOWN_DISPLACED in commits.columns:
        for _, result in commits.iterrows():
            weighing = result.get(KNOWN_DISPLACED)
            if not isinstance(weighing, dict):
                continue
            row = row_by_id.get(row_id_by_mz.get(float(weighing["peak_mz"]), ""))
            if row is not None:
                displaced[str(row["sample_peak_id"])] = (row, dict(weighing))
    recorded = record_grid_rivals(kept_rows, kept_measured)
    held = Counter(
        found.held_against["why"]
        for found in kept_measured
        if found is not None and found.held_against
    )
    taken_ids = {str(row["peak_assignment_id"]) for row, _ in displaced.values()}
    released = set(displaced)
    stage_a = []
    for row in stage_a_assignments:
        owner = str(row.get("owner_peak_assignment_id") or "")
        if str(row["peak_assignment_id"]) in taken_ids or owner in taken_ids:
            released.add(str(row["sample_peak_id"]))
            continue
        stage_a.append(row)
    return ListElection(
        stage_a=stage_a,
        matches=commits,
        released=released,
        displaced=displaced,
        rivals_by_peak={
            str(row["sample_peak_id"]): found.rivals
            for row, found in zip(kept_rows, kept_measured, strict=True)
            if found is not None and found.rivals
        },
        summary={
            "measured": recorded["measured"] + len(displaced),
            "with_rivals": recorded["with_rivals"],
            "taken_by_rivals": len(displaced),
            "kept_by_lines": held[HELD_BY_LINES],
            "kept_by_library": held[HELD_BY_LIBRARY],
            "prior": LIST_PRIOR_WEIGHT,
        },
    )


#: On an alternative, what says it is the reading an isotopologue claim took
#: the peak from (:mod:`envelope_claims`).
DISPLACED_BY_CLAIM = "displaced_by_claim"

#: On an alternative, what says it is the list reading a rival took the peak
#: from (:func:`settle_list_election`).
DISPLACED_BY_RIVAL = "displaced_by_rival"


def reading_as_alternative(row: dict, displaced_by: str) -> dict:
    """What a row said, shaped as an alternative of the row that took its peak.

    The inspector lists it with the row's other readings, and promoting it by
    hand commits it again, with its compound.

    :param row: The row whose reading was displaced.
    :param displaced_by: What tells a reader this is the reading something took
        the peak from, rather than a rival the peak's own election considered:
        :data:`DISPLACED_BY_CLAIM` or :data:`DISPLACED_BY_RIVAL`.
    """
    provenance = row.get("provenance") or {}
    return {
        "assigned_formula": row.get("assigned_formula"),
        "ion_formula": row.get("ion_formula"),
        "ionization_mechanism_id": row.get("ionization_mechanism_id"),
        "isotope_label": row.get("isotope_label"),
        "target_compound_id": row.get("target_compound_id"),
        "target_ion_id": row.get("target_ion_id"),
        "fit_score": row.get("fit_score"),
        "mz_error_ppm": row.get("mz_error_ppm"),
        "plausibility": provenance.get("plausibility"),
        "source": row.get("source"),
        displaced_by: True,
        **(
            {REFERENCE_IDENTITIES_COL: provenance[REFERENCE_IDENTITIES_COL]}
            if provenance.get(REFERENCE_IDENTITIES_COL)
            else {}
        ),
    }


def record_displaced_list_readings(
    stage_b_assignments: list[dict],
    displaced: dict[str, tuple[dict, dict]],
    max_alternatives: int,
) -> int:
    """Give each search row that took a list hit's peak the reading it displaced.

    The reading goes first among the row's alternatives, and the weighing into
    :data:`LIST_READING`: the list's formula, its identities, and the evidence
    the two readings were weighed on with the prior.

    :return: How many of the taken peaks a search row holds.
    """
    holding = 0
    for assignment in stage_b_assignments:
        taken = displaced.get(str(assignment.get("sample_peak_id")))
        if taken is None or assignment.get("role") != ROLE_M0:
            continue
        row, weighing = taken
        provenance = assignment.setdefault("provenance", {})
        row_provenance = row.get("provenance") or {}
        provenance[LIST_READING] = {
            "assigned_formula": row.get("assigned_formula"),
            "ion_formula": row.get("ion_formula"),
            "source": row.get("source"),
            "target_compound_id": row.get("target_compound_id"),
            # The tier the reading's evidence gave it. The election settles on
            # the rows as Stage A built them, before any rule of the run judges
            # the ledger, so a reading the run would have held at candidate can
            # read assigned here.
            "tier": row.get("tier"),
            "evidence": round(float(weighing["evidence"]), 4),
            "fit_score": round(float(weighing["fit_score"]), 4),
            "rival_evidence": round(float(weighing["rival_evidence"]), 4),
            "prior": weighing["prior"],
            **(
                {REFERENCE_IDENTITIES_COL: row_provenance[REFERENCE_IDENTITIES_COL]}
                if row_provenance.get(REFERENCE_IDENTITIES_COL)
                else {}
            ),
        }
        assignment["alternatives"] = cap_alternatives(
            assignment,
            [
                reading_as_alternative(row, DISPLACED_BY_RIVAL),
                *(assignment.get("alternatives") or []),
            ],
            max_alternatives,
        )
        holding += 1
    return holding


def listed_formula_identities(known_isotopes_df: pd.DataFrame) -> frozenset[str]:
    """The neutrals a Stage A known set names, as :func:`formula_identity` keys.

    The target library and the reference lists the sample was read against,
    whatever channel each compound was read through: a list names a compound,
    not the channel it is seen through.

    :param known_isotopes_df: The known-isotope frame Stage A matched, with a
        ``target_compound_formula`` column; empty where no list was read.
    :return: The keys, empty for an empty frame.
    """
    if (
        known_isotopes_df.empty
        or "target_compound_formula" not in known_isotopes_df.columns
    ):
        return frozenset()
    return frozenset(
        formula_identity(formula)
        for formula in known_isotopes_df["target_compound_formula"].dropna().unique()
    )


@dataclass(frozen=True)
class ListedIon:
    """What Stage A's row for an ion of its known set says of the list.

    :ivar target_compound_id: The target library's compound, where the library
        holds the ion; None where only a reference list does.
    :ivar reference_identities: The reference lists' names for its neutral,
        which ride on the row whichever list holds the ion; None where no
        reference list names it.
    """

    target_compound_id: str | None
    reference_identities: list | None


def listed_ions(known_isotopes_df: pd.DataFrame) -> dict[tuple[str, str], ListedIon]:
    """The ions a Stage A known set holds, by neutral and channel.

    Stage A reads each list through the channels it is read through - the
    target library through the mode's own, a reference list through every
    channel the run searches - and commits an ion it matched as that list's
    row (:func:`invert_matches_to_peak_assignments`): the target library's
    names its compound, a reference list's names none, and either carries the
    reference lists' names for its neutral. A reading through a channel no
    list was read through is no list's row, though a list may still name its
    neutral (:func:`listed_formula_identities`).

    :param known_isotopes_df: The known-isotope frame Stage A matches; empty
        where no list was read. A frame that names no channel or no compound
        holds no ion.
    :return: ``(formula identity, mechanism id)`` -> what the list's row says;
        of several target compounds of one neutral, the first by id.
    """
    columns = {
        "target_compound_formula",
        "ionization_mechanism_id",
        "target_compound_id",
    }
    if known_isotopes_df.empty or not columns <= set(known_isotopes_df.columns):
        return {}
    frame = known_isotopes_df[known_isotopes_df["target_compound_formula"].notna()]
    identity = {
        formula: formula_identity(formula)
        for formula in frame["target_compound_formula"].unique()
    }
    mirror = reference_mirror_mask(frame)
    names: dict[str, list] = {}
    if mirror.any():
        for formula, identities in zip(
            frame.loc[mirror, "target_compound_formula"],
            frame.loc[mirror, REFERENCE_IDENTITIES_COL],
            strict=True,
        ):
            names.setdefault(identity[formula], identities)
    # A reference list's row carries no compound id, which is what tells the
    # two lists apart (:func:`is_target_library_row`).
    held = frame[
        ["target_compound_formula", "ionization_mechanism_id", "target_compound_id"]
    ].drop_duplicates()
    compounds: dict[tuple[str, str], str | None] = {}
    for formula, mechanism, compound in held.itertuples(index=False):
        key = (identity[formula], str(mechanism))
        library = None if pd.isna(compound) else str(compound)
        current = compounds.get(key)
        if library is not None and (current is None or library < current):
            compounds[key] = library
        else:
            compounds.setdefault(key, None)
    return {
        key: ListedIon(compound, names.get(key[0]))
        for key, compound in compounds.items()
    }


def cap_second_channel(
    assignments: list[dict],
    *,
    notation_by_id: dict[str, str],
    minor_channels: frozenset[str],
    opened_channels: frozenset[str] = frozenset(),
    listed_formulas: frozenset[str] = frozenset(),
    search_rule: SecondChannelRule = SEARCH_READING_RULE,
    list_rule: SecondChannelRule = LIST_READING_RULE,
) -> dict:
    """Cap at ``candidate`` each reading through a secondary channel that
    nothing corroborates, in place.

    A secondary channel is searched because the sample's own spectrum shows its
    carrier, not because any neutral it proposes has been demonstrated. So a
    reading through one commits at the ledger's strongest word only when
    something beyond the mass fit says which neutral clustered: the one
    second-channel rule (:class:`SecondChannelRule`), in the configuration for
    the row's kind.

    - **A search's reading**, through any secondary channel
      (:data:`SEARCH_READING_RULE`): a list naming the neutral, or the sample
      showing it through one of the mode's own channels. Its isotopologue is
      recorded (``isotopologue``) and lifts nothing.
    - **A list's reading through a channel the run opened**
      (:data:`LIST_READING_RULE`): its own isotopologue, or the sample showing the
      neutral. A list's reading through a secondary channel the mode declares
      is left as it was: the list was read through it before any channel was
      opened.

    Each reading's partners are its configuration's own: a row of either stage
    its ``partner_tiers`` and ``partner_channels`` admit
    (:meth:`SecondChannelRule.partners`), through a channel other than the
    reading's. Both configurations admit the partner gate's partner today
    (:data:`PARTNER_RULE`, :func:`shows_molecule`). They are read once on the
    ledger the reading changes settled
    (``judging_loop.Judging.judge``): this is a cap of the judging loop, which
    only lowers a tier, after the partner gate has settled every gated ion's
    reading. So the gate neither lifts it nor puts it back, and a reading the
    gate gave the row is judged here as any other. Before it was a cap, a
    search's reading was held at build time against the search's own rows at
    any tier, a list's against Stage A's, and the gate lifted and re-imposed
    the search's for its gated channels: three bars for one question.

    Every judged row records the verdict either way (``minor_channel``), so a
    run says which of its rows leaned on a channel it opened for itself.

    :param assignments: Both stages' rows, modified in place.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :param minor_channels: The run's secondary channels.
    :param opened_channels: The ones the run added to the mode's mechanisms
        (``ResolvedProfile.added_channels``), whose list readings are judged.
    :param listed_formulas: The neutrals the sample's lists name, as
        :func:`formula_identity` keys (:func:`listed_formula_identities`).
    :param search_rule: The configuration a search's reading is judged by.
    :param list_rule: The configuration a list's reading is judged by.
    :return: How many search readings and list readings were judged
        (``read``, ``list_read``), and how many of each the cap lowered
        (``capped``, ``list_capped``).
    """
    summary = {"read": 0, "capped": 0, "list_read": 0, "list_capped": 0}
    minor_ids = {
        mid for mid, notation in notation_by_id.items() if notation in minor_channels
    }
    if not minor_ids:
        return summary
    opened_ids = {mid for mid in minor_ids if notation_by_id[mid] in opened_channels}
    owners_with_children = {
        row["owner_peak_assignment_id"]
        for row in assignments
        if row.get("role") == ROLE_ISO_CHILD and row.get("owner_peak_assignment_id")
    }
    # One pass of the ledger per partner bar, which the two configurations
    # share today.
    partners_by_bar: dict[tuple, dict[str, frozenset[str]]] = {}
    for rule in (search_rule, list_rule):
        bar = (rule.partner_tiers, rule.partner_channels)
        if bar not in partners_by_bar:
            partners_by_bar[bar] = rule.partners(
                assignments, notation_by_id, minor_channels
            )
    for row in assignments:
        if row.get("role") != ROLE_M0 or not row.get("assigned_formula"):
            continue
        mechanism_id = row.get("ionization_mechanism_id")
        if mechanism_id not in minor_ids:
            continue
        list_row = row.get("source") == SOURCE_DATABASE
        if list_row and mechanism_id not in opened_ids:
            continue
        rule = list_rule if list_row else search_rule
        isotopologue = row.get("peak_assignment_id") in owners_with_children
        corroboration = rule.corroboration(
            row,
            partners=partners_by_bar[(rule.partner_tiers, rule.partner_channels)],
            listed_formulas=listed_formulas,
            isotopologue=isotopologue,
        )
        capped = corroboration is None and row.get("tier") == TIER_ASSIGNED
        verdict: dict = {"corroborated_by": corroboration, "capped": capped}
        if not list_row:
            # The line it has, which does not lift the cap on its own.
            verdict["isotopologue"] = isotopologue
        row.setdefault("provenance", {})["minor_channel"] = verdict
        if capped:
            row["tier"] = TIER_CANDIDATE
        summary["list_read" if list_row else "read"] += 1
        summary["list_capped" if list_row else "capped"] += capped
    return summary


def build_unassigned_assignments(
    peaks_df: pd.DataFrame,
    sample_item_id: str,
    peak_assignment_run_id: str,
) -> list[dict]:
    """Build placeholder rows for peaks no stage explained.

    Every observed peak gets exactly one row per run; peaks left over after
    Stage A and Stage B are persisted with role/tier 'unassigned' so the
    ledger is complete and queryable.

    :param peaks_df: DataFrame with sample_peak_id, mz, and intensity columns.
    """
    return [
        {
            "peak_assignment_id": gen_id(32),
            "peak_assignment_run_id": peak_assignment_run_id,
            "sample_item_id": sample_item_id,
            "sample_peak_id": str(row.sample_peak_id),
            "sample_peak_mz": float(row.mz),
            "sample_peak_intensity": float(row.intensity),
            "sample_peak_tof": None,
            "role": ROLE_UNASSIGNED,
            "assigned_formula": None,
            "ion_formula": None,
            "ionization_mechanism_id": None,
            "isotope_label": None,
            "isotope_formula": None,
            "source": None,
            "fit_score": None,
            "mz_error_ppm": None,
            "abundance_error": None,
            "tier": TIER_UNASSIGNED,
            "target_compound_id": None,
            "target_ion_id": None,
            "owner_peak_assignment_id": None,
            "alternatives": None,
            "provenance": None,
        }
        for row in peaks_df.itertuples(index=False)
    ]
