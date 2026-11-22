"""The ionization mechanisms a sample is assigned under.

The mechanisms its ionization mode binds, the ones a run records it searched,
the deployment's rows for a profile's secondary channels, and the notation
each mechanism is named by in a run's frames.
"""

from collections.abc import Iterable
from types import SimpleNamespace

import pandas as pd
from sqlalchemy import select

from mascope_backend.api.new.cheminfo.utils import to_explicit_isotope_format
from mascope_backend.api.new.ionization.modes.util import (
    fetch_sample_ionization_mechanism_ids,
)
from mascope_backend.api.new.peak_assignments.profiles import (
    RESOLVED_PROFILE_KEY,
    ResolvedProfile,
    with_secondary_channels,
)
from mascope_backend.db import IonizationMechanism, Sample, async_session
from mascope_backend.runtime import runtime
from mascope_match import unshift_mz
from mascope_match.compute.axis import current_mz_shift
from mascope_tools.composition.mechanism_notation import (
    MechanismNotationError,
    mechanism_key,
    mechanism_spellings,
    parse_mechanism,
)
from mascope_tools.composition.reagents import secondary_channels


async def fetch_sample_mechanisms(
    sample: Sample,
) -> tuple[list[str], list[SimpleNamespace]]:
    """Resolve the sample's ionization mechanisms once per run.

    Every stage of a run needs the same mechanism set, so it is fetched here
    once and threaded through rather than re-queried per stage (which also
    used to open a second pooled connection inside an already-open session).
    The mechanism rows are detached into plain namespaces so downstream
    CPU-bound work can use them off the event loop.

    :param sample: Sample model object
    :return: (the mechanism ids of the sample's ionization mode,
        polarity-matching mechanism rows as detached namespaces), both
        without a row a run does not search (:func:`_readable`)
    """
    mechanism_ids = await fetch_sample_ionization_mechanism_ids(sample.sample_item_id)
    async with async_session() as session:
        mechanisms = (
            (
                await session.execute(
                    select(IonizationMechanism).where(
                        IonizationMechanism.ionization_mechanism_id.in_(mechanism_ids),
                        IonizationMechanism.ionization_mechanism_polarity
                        == sample.polarity,
                    )
                )
            )
            .scalars()
            .all()
        )
    return _without_unreadable(mechanism_ids, mechanisms)


async def recorded_searched_mechanisms(
    sample, run_config: dict | None
) -> list[SimpleNamespace]:
    """The mechanisms a run of the sample searched, read off its record: the
    mode's own, and the secondary channels the run opened
    (``resolved_profile.secondary_channels``) that the mode does not declare,
    as :func:`_searched_mechanisms` composes them for the run itself.

    What a reader of a run's row proposes a formula through: the untargeted
    search proposed the row's shortlist through every channel it searched, so
    a shortlist formula that fits the peak only through an opened channel is
    placed there (``alternatives_scoring.score_row_alternatives``).

    :param sample: The sample view row.
    :param run_config: The row's run's stored config; None, or one recording
        no resolution, reads the mode's own mechanisms alone.
    :return: The mechanisms, the mode's first.
    """
    _, mechanisms = await fetch_sample_mechanisms(sample)
    snapshot = (run_config or {}).get(RESOLVED_PROFILE_KEY)
    opened = snapshot.get("secondary_channels") if isinstance(snapshot, dict) else None
    if not mechanisms or not opened:
        return mechanisms
    own = {mechanism_key(m.ionization_mechanism) for m in mechanisms}
    return mechanisms + [
        mechanism
        for mechanism in await fetch_mechanisms_by_notation(
            list(opened), sample.polarity
        )
        if mechanism_key(mechanism.ionization_mechanism) not in own
    ]


def _without_unreadable(
    mechanism_ids: list[str], mechanisms: list
) -> tuple[list[str], list[SimpleNamespace]]:
    """The mode's mechanism ids and this polarity's rows, less every row a run
    does not search (:func:`_readable`), the rows detached into plain
    namespaces.

    The row's id goes as well as the row: Stage A fetches target ions by these
    ids, and ions an older version generated for such a row read its text as
    some other mechanism. Only this polarity's rows are read here; a row of
    the other polarity never reaches the sample's Stage A, which fetches by
    polarity too.

    :param mechanism_ids: The mechanism ids of the sample's ionization mode.
    :param mechanisms: The polarity-matching rows among them.
    :return: The ids and the detached rows a run searches.
    """
    unreadable = {m.ionization_mechanism_id for m in mechanisms if not _readable(m)}
    return [
        mechanism_id for mechanism_id in mechanism_ids if mechanism_id not in unreadable
    ], [
        SimpleNamespace(
            ionization_mechanism_id=m.ionization_mechanism_id,
            ionization_mechanism=m.ionization_mechanism,
            ionization_mechanism_polarity=m.ionization_mechanism_polarity,
        )
        for m in mechanisms
        if m.ionization_mechanism_id not in unreadable
    ]


#: Mechanisms already reported as unreadable, so a stored row does not log for
#: every sample a run reads it for.
_reported_unreadable: set[str] = set()


def _readable(mechanism: IonizationMechanism) -> bool:
    """Whether a mechanism row is one peak assignment searches.

    The column reads a row in the standard notation (``StandardMechanism``),
    and one that reads in neither notation as it is stored: a free-text label
    an older validator let through. The composition finder refuses such a
    row and target-ion generation skips it, so a run leaves it out rather
    than failing on it, and reports it once. Every row that reads is searched:
    a multimer, a charge above one and a neutral loss (``[2M+H]+``,
    ``[M+2H]2+``, ``[M+H-H2O]+``) each at its own mass.
    """
    try:
        parse_mechanism(mechanism.ionization_mechanism)
    except MechanismNotationError:
        pass
    else:
        return True
    if mechanism.ionization_mechanism_id not in _reported_unreadable:
        _reported_unreadable.add(mechanism.ionization_mechanism_id)
        runtime.logger.warning(
            f"Ionization mechanism {mechanism.ionization_mechanism!r} "
            f"({mechanism.ionization_mechanism_id}) reads as neither notation, "
            "so peak assignment does not search it; correct or remove it."
        )
    return False


async def fetch_mechanisms_by_notation(
    notations: list[str], polarity: str | None
) -> list[SimpleNamespace]:
    """The deployment's mechanism rows for these notations, at this polarity.

    The secondary channels of a reagent profile are by definition not on the
    sample's ionization mode - that is what makes them opportunistic - so they
    cannot come from :func:`fetch_sample_mechanisms`. They still have to exist
    as rows, because an assignment references a mechanism by id; a channel the
    deployment has never declared is reported rather than invented.

    :param notations: Mechanism notations to look up.
    :param polarity: The sample's polarity; a mechanism of the wrong polarity
        cannot ionize this sample whatever its notation says.
    :return: The matching rows, detached for use off the event loop. A row is
        found by either spelling of its mechanism and read in the standard one.
    """
    if not notations:
        return []
    async with async_session() as session:
        rows = (
            (
                await session.execute(
                    select(IonizationMechanism).where(
                        IonizationMechanism.ionization_mechanism.in_(
                            mechanism_spellings(notations)
                        ),
                        IonizationMechanism.ionization_mechanism_polarity == polarity,
                    )
                )
            )
            .scalars()
            .all()
        )
    return [
        SimpleNamespace(
            ionization_mechanism_id=row.ionization_mechanism_id,
            ionization_mechanism=row.ionization_mechanism,
            ionization_mechanism_polarity=row.ionization_mechanism_polarity,
        )
        for row in rows
    ]


def _source_axis(peaks_df: pd.DataFrame) -> pd.DataFrame:
    """The peaks where the source made them: on the axis as stored.

    The two passes that look for the source's own ions read them here: the
    secondary channels' carriers (:func:`_resolve_secondary_channels`) and the
    reagent pre-pass's lines (:func:`_reagent_assignments`). A decoy moves the
    axis off every formula of the sample, not off the source that made the
    spectrum (``decoy.py``). Read on the moved axis, a dalton shift puts every
    carrier and reagent line out of those passes' reach, and the arm would
    search fewer channels than its control and offer the stages the source's
    brightest ions as analytes. Outside a decoy's shift the frame is the one
    given.

    :param peaks_df: The sample's peaks, as the run reads them.
    :return: The same peaks, in the same order, at their stored m/z.
    """
    if current_mz_shift() is None:
        return peaks_df
    return peaks_df.assign(mz=unshift_mz(peaks_df["mz"].to_numpy()))


async def _resolve_secondary_channels(
    sample, resolved_profile: ResolvedProfile, peaks_df: pd.DataFrame
) -> tuple[ResolvedProfile, list[SimpleNamespace]]:
    """Which of the profile's opportunistic channels this sample runs.

    The profile names what the source can produce, the spectrum says whether it
    does, and the mechanism table says whether this deployment can express it.
    All three have to agree before a channel is searched. A run and the run-less
    ingest fold resolve them here alike.

    :param sample: The sample view row.
    :param resolved_profile: The sample's resolved chemistry.
    :param peaks_df: The sample's peaks, whose spectrum is asked for each
        channel's carrier, on the axis as stored (:func:`_source_axis`): a
        decoy opens the channels its control opened.
    :return: The resolution carrying the channel evidence, and the deployment's
        mechanism rows for the profile's secondary channels.
    """
    secondary_mechanisms = await fetch_mechanisms_by_notation(
        [
            channel.notation
            for channel in secondary_channels(resolved_profile.profile.name)
        ],
        sample.polarity,
    )
    source = _source_axis(peaks_df)
    resolved_profile = with_secondary_channels(
        resolved_profile,
        source["mz"].to_numpy(),
        source["intensity"].to_numpy(),
        [m.ionization_mechanism for m in secondary_mechanisms],
    )
    return resolved_profile, secondary_mechanisms


def _searched_mechanisms(
    mechanisms: list[SimpleNamespace],
    secondary_mechanisms: list[SimpleNamespace],
    resolved_profile: ResolvedProfile,
) -> list[SimpleNamespace]:
    """The mechanisms a sample's channels are searched and read through.

    The mode's own mechanisms decide whether there is anything to search at all;
    the opportunistic channels are an addition to a sample's chemistry, not a
    substitute for it. A mode that declares nothing is one nobody has configured,
    and searching it through a channel the source happens to show would assign a
    sample whose ionization is unknown. The cross-channel pass reads the same
    set as the untargeted search: which channels a neutral COULD have been seen
    through is what makes seeing it in one of them evidence or not.

    Each channel is searched once: a secondary channel the mode declares itself
    is searched through the mode's mechanism, so only the ones the profile adds
    (``ResolvedProfile.added_channels``) are appended.

    :param mechanisms: The mode's own polarity-matching mechanisms.
    :param secondary_mechanisms: The deployment's rows for the profile's
        secondary channels (:func:`_resolve_secondary_channels`).
    :param resolved_profile: The resolution that says which of those this
        sample runs.
    :return: The mode's mechanisms and the secondary ones the sample runs, or
        none when the mode declares none.
    """
    if not mechanisms:
        return []
    # Read through the mechanism column, a row is already in the standard
    # notation that the profile's channel tables are written in.
    return mechanisms + [
        mechanism
        for mechanism in secondary_mechanisms
        if mechanism.ionization_mechanism in resolved_profile.added_channels
    ]


def given_secondary_channels(
    given_mechanism_ids: Iterable[str],
    searched_mechanisms: list[SimpleNamespace],
    secondary_mechanisms: list[SimpleNamespace],
    resolved_profile: ResolvedProfile,
) -> tuple[str, ...]:
    """The profile's secondary channels a given reading goes through that the
    sample does not search (``ResolvedProfile.given_channels``).

    A batch decision is measured on a member through the mechanism it was
    read through on its representative, whose sample may have opened a channel
    the member's did not (MAS-1039). Judged with the member's channels alone,
    such a reading would escape every rule a reading through an opened channel
    is held to.

    :param given_mechanism_ids: The mechanisms the given readings go through.
    :param searched_mechanisms: The ones the sample searches
        (:func:`_searched_mechanisms`); none when its mode declares none, and
        then no channel is added either.
    :param secondary_mechanisms: The deployment's rows for the profile's
        secondary channels (:func:`_resolve_secondary_channels`).
    :param resolved_profile: The sample's resolved chemistry.
    :return: The channels' notations, as the profile names them.
    """
    searched = {m.ionization_mechanism_id for m in searched_mechanisms}
    if not searched:
        return ()
    unsearched = set(given_mechanism_ids) - searched
    keys = {
        mechanism_key(m.ionization_mechanism)
        for m in secondary_mechanisms
        if m.ionization_mechanism_id in unsearched
    }
    return tuple(
        sorted(
            channel.notation
            for channel in secondary_channels(resolved_profile.profile.name)
            if mechanism_key(channel.notation) in keys
        )
    )


def judged_mechanisms(
    searched_mechanisms: list[SimpleNamespace],
    secondary_mechanisms: list[SimpleNamespace],
    resolved_profile: ResolvedProfile | None,
) -> list[SimpleNamespace]:
    """The mechanisms the lists are read and the judging names its rows'
    channels through: the searched ones, and the secondary channels a batch
    decision measured on the sample reads through that its spectrum did not
    open (``ResolvedProfile.given_channels``, MAS-1039).

    Only the rules that judge such a channel's own reading read it. The
    same-ion families (:func:`family_mechanisms`), the search, the winners'
    shortlists and the identification levels stay on the searched mechanisms:
    a reading through one of the sample's own channels gains no rival through
    a channel only another sample opened.
    """
    given = set(getattr(resolved_profile, "given_channels", ()))
    if not given:
        return searched_mechanisms
    searched = {m.ionization_mechanism_id for m in searched_mechanisms}
    return searched_mechanisms + [
        mechanism
        for mechanism in secondary_mechanisms
        if mechanism.ionization_mechanism in given
        and mechanism.ionization_mechanism_id not in searched
    ]


def family_mechanisms(
    rows: list[dict],
    searched_mechanisms: list[SimpleNamespace],
    judged: list[SimpleNamespace],
) -> list[tuple[list[dict], list[SimpleNamespace]]]:
    """The rows, split by the mechanisms their ions' same-ion families are
    read through.

    A row through one of the sample's own channels reads its family through
    the searched mechanisms alone. A given reading through a channel only
    another sample opened reads it through the judged ones
    (:func:`judged_mechanisms`), its own channel among them, as a run of the
    sample that opened it does (MAS-1039). So that reading is asked about the
    sample's own channels' readings of its ion, and none of the sample's own
    readings gains a rival through a channel its spectrum did not open.

    :param rows: The rows to give families.
    :param searched_mechanisms: The ones the sample searches.
    :param judged: :func:`judged_mechanisms`.
    :return: ``(rows, mechanisms)`` pairs, the sample's own rows first.
    """
    searched = {m.ionization_mechanism_id for m in searched_mechanisms}
    given = {m.ionization_mechanism_id for m in judged} - searched
    if not given:
        return [(rows, searched_mechanisms)]
    through_given = [
        row for row in rows if str(row.get("ionization_mechanism_id")) in given
    ]
    own = [row for row in rows if str(row.get("ionization_mechanism_id")) not in given]
    return [(own, searched_mechanisms), (through_given, judged)]


def _notation_by_id(searched_mechanisms: list[SimpleNamespace]) -> dict[str, str]:
    """The searched mechanisms' finder notations, keyed by mechanism id.

    Every row whose mechanism the search runs is here, the second of two rows
    of one mechanism included. The finder searches that mechanism once,
    through the first row (:func:`_untargeted_ionization_notations`), but the
    second row's own target ions still reach Stage A, and a reading through
    them is a reading through the same channel.
    """
    _, mechanism_id_by_notation = _untargeted_ionization_notations(searched_mechanisms)
    notation_by_id = {
        mechanism_id: notation
        for notation, mechanism_id in mechanism_id_by_notation.items()
    }
    for mechanism in searched_mechanisms:
        notation, _ = to_explicit_isotope_format(mechanism.ionization_mechanism)
        if notation in mechanism_id_by_notation:
            notation_by_id.setdefault(mechanism.ionization_mechanism_id, notation)
    return notation_by_id


def _untargeted_ionization_notations(
    mechanisms: list[SimpleNamespace],
) -> tuple[list[str], dict[str, str]]:
    """
    Resolve the sample's ionization mechanisms into the explicit-isotope
    notation used by the composition finder.

    Every notation table of a run is built here, in the standard adduct
    notation the mechanism column reads every row in, so it keys alike with
    the library's channel tables: ``[M+[15N]O3]-`` for ``[M+^NO3]-``.

    Each mechanism is searched once. Two rows can read as one mechanism:
    spellings of it stored before a mechanism had one spelling, which the
    unique column could not tell apart. Searched twice, the finder would
    propose every neutral through it twice. The first row is the one
    searched, and the one a reading through it is recorded under.

    :param mechanisms: The sample's polarity-matching mechanisms, resolved
        once per run by :func:`fetch_sample_mechanisms`
    :return: (explicit notation strings, notation -> mechanism id mapping)
    """
    notations: list[str] = []
    mechanism_id_by_notation: dict[str, str] = {}
    for mechanism in mechanisms:
        notation, _ = to_explicit_isotope_format(mechanism.ionization_mechanism)
        if notation in mechanism_id_by_notation:
            continue
        notations.append(notation)
        mechanism_id_by_notation[notation] = mechanism.ionization_mechanism_id
    return notations, mechanism_id_by_notation
