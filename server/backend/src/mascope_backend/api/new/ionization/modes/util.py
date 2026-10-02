from sqlalchemy import exists, or_, select

from mascope_backend.db import (
    IonizationMode,
    SampleFile,
    SampleItem,
    async_session,
)
from mascope_backend.method_keys import instrument_key
from mascope_backend.runtime import runtime


COLLECTION_ID_FIELDS = ("calibration_collection_id", "diagnostic_collection_id")


class NoTokenMatchError(ValueError):
    """No configured ionization mode token occurs in a file's name.

    Told apart from tokens that match but not one mode per polarity: a file
    no token matches may have been bound another way, its chemistry chosen by
    hand, while an ambiguous name is a configuration to fix.
    """


def _mode_name(mode: IonizationMode) -> str:
    token = mode.ionization_mode_token
    return f"'{mode.ionization_mode_name}'" + (f" (token '{token}')" if token else "")


def one_mode_per_polarity(
    sample_file: SampleFile, modes: list[IonizationMode]
) -> tuple[list[IonizationMode], list[str]]:
    """Pick the one mode of each polarity a file holds.

    The rule every way of binding a file shares - by its name's tokens, or by
    modes a person chose: each polarity of the file takes exactly one mode.

    :param sample_file: The file to bind.
    :param modes: The candidates: the modes its tokens match, or the ones
        chosen for it.
    :return: The mode of each polarity, in the file's polarity order, and a
        phrase for each polarity that has none or more than one.
    """
    chosen, problems = [], []
    for polarity in sample_file.polarity:
        matching = [mode for mode in modes if mode.ionization_mode_polarity == polarity]
        if len(matching) == 1:
            chosen.append(matching[0])
        elif not matching:
            problems.append(f"no mode matches polarity {polarity}")
        else:
            names = ", ".join(_mode_name(mode) for mode in matching)
            problems.append(f"{len(matching)} modes match polarity {polarity}: {names}")
    return chosen, problems


async def fetch_mode_collection_ids(ionization_mode_id: str) -> dict[str, str | None]:
    """Fetch the calibration and diagnostic collection ids a mode is bound to.

    Used by the update route to tell a binding the request is *changing* from
    one it is merely echoing back.

    A mode that does not exist reports no bindings, so every id the request
    names counts as new and is checked. That fails closed; the service's own
    lookup still answers 404 for a caller who clears the check.

    :param ionization_mode_id: The ID of the ionization mode to read.
    :type ionization_mode_id: str
    :return: The mode's current collection ids, keyed by field name.
    :rtype: dict[str, str | None]
    """
    async with async_session() as session:
        row = (
            await session.execute(
                select(
                    IonizationMode.calibration_collection_id,
                    IonizationMode.diagnostic_collection_id,
                ).where(IonizationMode.ionization_mode_id == ionization_mode_id)
            )
        ).one_or_none()

    if row is None:
        return dict.fromkeys(COLLECTION_ID_FIELDS)
    return {field: getattr(row, field) for field in COLLECTION_ID_FIELDS}


async def fetch_all_ionization_modes() -> list[IonizationMode]:
    """Fetch all ionization modes from the database.

    Every mode, the ones Mascope ships included: this is what routing reads,
    and the next binding rung routes *to* those rows. Hiding an unadopted mode
    is the listing's concern, and ``get_ionization_modes`` does it there.

    :return: A list of all ionization modes.
    :rtype: list[IonizationMode]
    """
    async with async_session() as session:
        result = await session.execute(select(IonizationMode))
        ionization_modes = result.scalars().all()
        return ionization_modes


def system_mode_adopted():
    """Whether a deployment has taken up a mode Mascope ships.

    One definition, used everywhere, because the answer decides two things
    that have to agree: whether the mode is offered, and whether it may be
    dropped when a mechanism it holds is deleted. Read differently in the two
    places, a mode could be hidden from the listing and still pin its
    mechanisms behind a refusal naming a mode nobody can see.

    A mode is adopted once it carries anything of the deployment's: either
    target collection, or a sample bound to it. Anything less and the mode is
    still exactly as it was seeded.

    :return: A SQL predicate over ``IonizationMode``.
    """
    return or_(
        IonizationMode.calibration_collection_id.is_not(None),
        IonizationMode.diagnostic_collection_id.is_not(None),
        exists().where(
            SampleItem.ionization_mode_id == IonizationMode.ionization_mode_id
        ),
    )


async def fetch_listable_ionization_modes() -> list[IonizationMode]:
    """Fetch the ionization modes a deployment should be offered.

    A mode Mascope ships is left out until the deployment adopts it. Before
    that it calibrates and matches nothing, so offering it would only invite a
    sample that cannot be processed. After it, the mode has to be listed: it
    is a mode like any other, and a sample bound to one carries an id the
    browser must resolve.

    :return: A list of the deployment's own modes and the seeded ones it
        has adopted.
    :rtype: list[IonizationMode]
    """
    async with async_session() as session:
        result = await session.execute(
            select(IonizationMode).where(
                or_(IonizationMode.system_key.is_(None), system_mode_adopted())
            )
        )
        ionization_modes = result.scalars().all()
        return ionization_modes


async def fetch_batch_ionization_mechanism_ids(sample_batch_id: str) -> list[str]:
    """Fetch ionization mechanism IDs for a given sample batch.

    :param sample_batch_id: The ID of the sample batch to fetch ionization mechanism IDs for.
    :type sample_batch_id: str
    :return: A list of ionization mechanism IDs associated with the sample batch.
    :rtype: list[str]
    """
    async with async_session() as session:
        # Get samples
        result = await session.execute(
            select(SampleItem).where(SampleItem.sample_batch_id == sample_batch_id)
        )
        batch_sample_items = result.scalars().all()
        # Collect unique ionization mode ids in the batch samples
        batch_ion_mode_ids = list(
            set([item.ionization_mode_id for item in batch_sample_items])
        )
        # Get the ionization modes
        result = await session.execute(
            select(IonizationMode).where(
                IonizationMode.ionization_mode_id.in_(batch_ion_mode_ids)
            )
        )
        batch_ion_modes = result.scalars().all()
        batch_ionization_mechanism_ids = list(
            set(
                ionization_mechanism_id
                for ion_mode in batch_ion_modes
                for ionization_mechanism_id in ion_mode.ionization_mechanism_ids
            )
        )
        return batch_ionization_mechanism_ids


async def fetch_sample_ionization_mechanism_ids(sample_item_id: str) -> list[str]:
    """Fetch ionization mechanism IDs for a given sample item.

    :param sample_item_id: The ID of the sample item to fetch ionization mechanism IDs for.
    :type sample_item_id: str
    :return: A list of ionization mechanism IDs associated with the sample item.
    :rtype: list[str]
    """
    async with async_session() as session:
        # Get samples
        result = await session.execute(
            select(SampleItem).where(SampleItem.sample_item_id == sample_item_id)
        )
        sample_item = result.scalars().one_or_none()
        if not sample_item:
            raise ValueError(f"Sample item with ID {sample_item_id} not found")

        # Get the ionization mode
        result = await session.execute(
            select(IonizationMode).where(
                IonizationMode.ionization_mode_id == sample_item.ionization_mode_id
            )
        )
        sample_ion_mode = result.scalars().one_or_none()

        return sample_ion_mode.ionization_mechanism_ids


async def resolve_ionization_modes_by_peaks(
    sample_file: SampleFile,
) -> list[IonizationMode]:
    """Resolve ionization modes based on peaks in the sample file.

    :param sample_file: The sample file to resolve ionization modes for.
    :type sample_file: SampleFile
    :raises NotImplementedError: Not implemented yet.
    :return: A list of resolved ionization modes.
    :rtype: list[IonizationMode]
    """

    all_ionization_modes = await fetch_all_ionization_modes()
    for ionization_mode in all_ionization_modes:
        if ionization_mode.ionization_mode_polarity not in sample_file.polarity:
            continue
        # TODO: Fetch the calibration isotopes for
        # ionization_mode.calibration_collection_id (via get_target_isotopes)
        # and match them against the peaks in the sample file.
        raise NotImplementedError(
            "Resolving ionization modes by peaks. Calibration isotopes matching not implemented yet",
        )


def applies_to_instrument(mode: IonizationMode, instrument: str | None) -> bool:
    """Whether a mode is a candidate for files of ``instrument``.

    A mode scoped to one instrument is matched against that instrument's file
    names alone; an unscoped mode is matched against every instrument's, as all
    of them were before the scope existed (#1463).

    Compared folded, through :func:`instrument_key`: ``SampleFile.instrument``
    is recorded with inconsistent case, so a mode scoped to ``ORBI-1`` has to
    match the files recorded as ``orbi-1`` too, or they park.

    :param mode: The mode to test.
    :param instrument: The instrument a file was acquired on.
    :return: True when the mode may match that instrument's files.
    :rtype: bool
    """
    return mode.instrument is None or instrument_key(mode.instrument) == instrument_key(
        instrument
    )


def _modes_matching_tokens(
    sample_file: SampleFile, all_ionization_modes: list[IonizationMode]
) -> list[IonizationMode]:
    """The modes whose token occurs in a file's name, in a polarity it holds.

    Scoped to the file's instrument: a mode belonging to another instrument is
    not a candidate, however well its token reads in this file's name.

    :param sample_file: The file to match.
    :param all_ionization_modes: Every configured mode.
    :return: Every matching mode, which may be none, one or several per
        polarity - the caller decides what to do about that.
    :rtype: list[IonizationMode]
    """
    file_polarities = set(sample_file.polarity)
    matched = []
    for ionization_mode in all_ionization_modes:
        if not ionization_mode.ionization_mode_token:
            continue
        if not applies_to_instrument(ionization_mode, sample_file.instrument):
            continue
        if (
            ionization_mode.ionization_mode_polarity in file_polarities
            and ionization_mode.ionization_mode_token in sample_file.filename
        ):
            runtime.logger.debug(
                f"Matched ionization mode token: {ionization_mode.ionization_mode_token} "
                f"with filename: {sample_file.filename}"
            )
            matched.append(ionization_mode)
    return matched


def _tokens_overlap(one: str | None, other: str | None) -> bool:
    """Whether one token contains the other, so one name can match both.

    Symmetric, and the relation :func:`token_is_unique` refuses within a scope:
    either way round, a name carrying the longer token carries both.
    """
    if not one or not other:
        return False
    return one in other or other in one


def _token_covers(scoped_token: str | None, shared_token: str | None) -> bool:
    """Whether an instrument's own token is at least as specific as a shared one.

    One way round, deliberately. The override is for an instrument that means
    its own thing by a token everyone else uses, so the scoped token has to
    *contain* the shared one - equal tokens, or a longer scoped one like
    ``NO3_15N`` over a shared ``NO3``.

    The reverse is a different configuration entirely: a shared ``NO3_15N``
    beside an instrument's ``NO3``. A file named ``..._NO3_15N_...`` carries
    both, and the more specific one is the shared mode, so letting the scope
    win would bind a 15N file to the instrument's plain nitrate on the strength
    of a shorter token. That stays ambiguous and parks for a person, as it did
    before a token could be scoped at all.
    """
    if not scoped_token or not shared_token:
        return False
    return shared_token in scoped_token


def _prefer_scoped(
    matched: list[IonizationMode], sample_file: SampleFile
) -> list[IonizationMode]:
    """Drop an unscoped match that a scoped match of the same token overrides.

    The instrument's own mode wins, which is what makes a scope worth setting:
    a site can keep the token it has always used as an unscoped mode and add a
    scoped one for the instrument that means something else by it, instead of
    having to scope every mode at once to stop them colliding.

    **Only where the instrument's own token covers the shared one.** An
    unscoped BR and a scoped NO3 both matching one name is not an override of
    anything - it is a name that says two chemistries, which was refused and
    parked for a person before the scope existed and must go on being. Nor is a
    shared NO3_15N beside an instrument's NO3: there the shared mode is the more
    specific reading of the name, so the scope does not get to win on a shorter
    token (:func:`_token_covers`).

    Applied per polarity, because a file's polarities are bound
    independently - an instrument may have its own negative mode and share the
    positive one with everything else.

    :param matched: What the tokens matched, from :func:`_modes_matching_tokens`.
    :param sample_file: The file being bound.
    :return: The matches, with an unscoped one dropped where a scoped match of
        the same polarity carries a token that covers it.
    :rtype: list[IonizationMode]
    """
    scoped = [mode for mode in matched if mode.instrument is not None]
    if not scoped:
        return matched
    kept = []
    for mode in matched:
        overridden = mode.instrument is None and any(
            other.ionization_mode_polarity == mode.ionization_mode_polarity
            and _token_covers(other.ionization_mode_token, mode.ionization_mode_token)
            for other in scoped
        )
        if overridden:
            runtime.logger.debug(
                f"Mode '{mode.ionization_mode_name}' applies to every instrument, but "
                f"{sample_file.instrument} has its own mode carrying a token "
                f"covering this one in polarity {mode.ionization_mode_polarity}; the "
                f"instrument's own one is used"
            )
            continue
        kept.append(mode)
    return kept


async def resolve_ionization_modes_by_tokens(
    sample_file: SampleFile, modes: list[IonizationMode] | None = None
) -> list[IonizationMode]:
    """Resolve ionization modes based on tokens in the sample file.

    A mode matches when its token is a substring of the file name and its
    polarity occurs in the file. Every polarity of the file must match exactly
    one mode: a dual-polarity file whose name matches two positive tokens and
    no negative one is refused, not routed twice as positive.

    :param sample_file: The sample file to resolve ionization modes for.
    :type sample_file: SampleFile
    :param modes: Every configured mode, for a caller asking about many files
        in a row - the modes are the same for all of them, and fetching them
        per file is a query per file. Every request path leaves it unset and
        gets today's modes, which is the only answer a file being processed
        may be bound on. The two kinds of failure below are how a caller that
        asks in bulk tells "no token names this file" from "its name says two
        chemistries": the first is what the method binding rung is for, the
        second is a configuration to fix.
    :raises NoTokenMatchError: If no mode's token occurs in the name.
    :raises ValueError: If a polarity of the file matches no mode or more
        than one.
    :return: One mode per polarity of the file, in the file's polarity order.
    :rtype: list[IonizationMode]
    """
    runtime.logger.debug(
        f"Resolving ionization modes by tokens for {sample_file.filename}"
    )
    if modes is None:
        modes = await fetch_all_ionization_modes()
    matched_ionization_modes = _prefer_scoped(
        _modes_matching_tokens(sample_file, modes), sample_file
    )

    if not matched_ionization_modes:
        raise NoTokenMatchError(
            f"No ionization mode tokens found for file {sample_file.filename}. "
            "Configure tokens in ionization settings"
        )

    chosen, problems = one_mode_per_polarity(sample_file, matched_ionization_modes)
    if problems:
        raise ValueError(
            f"Ionization mode tokens must match exactly one mode per polarity in "
            f"file {sample_file.filename}, but {'; '.join(problems)}. "
            "Configure tokens in ionization settings"
        )

    return chosen


def tokens_conflict(
    token: str | None,
    instrument: str | None,
    other_token: str | None,
    other_instrument: str | None,
) -> bool:
    """Whether two modes' tokens would leave a file with no single answer.

    Tokens that do not overlap never conflict: no one name carries both. For
    the ones that do, the scopes decide, in three cases:

    - **The same scope.** Two unscoped modes, or two of one instrument, are
      matched against the same names with nothing to break the tie.
    - **Two named instruments.** Never matched against one file, so each
      instrument's tokens are its own - which is what the scope is for.
    - **One scoped, one shared.** Both match the scoped instrument's files, and
      :func:`_prefer_scoped` settles it *only* where the instrument's own token
      covers the shared one. The other way round - a shared ``NO3_15N`` beside
      an instrument's ``NO3`` - it does not, and in one polarity that pair is a
      dead end: every file of that instrument naming 15N matches both and parks,
      for good, because the instrument cannot add its own ``NO3_15N`` either
      (that overlaps its ``NO3`` within one scope). So it is refused when it is
      configured.

    **Polarity is not considered, here or anywhere an overlap is refused.** Two
    overlapping tokens in *different* polarities could in fact be routed - a
    shared ``NO3_15N`` in positive mode beside an instrument's ``NO3`` in
    negative mode binds each polarity to one mode - and this refuses them
    anyway, as every overlap has been refused since before a mode could be
    scoped. Conservative rather than reasoned: taking polarity into account
    would start allowing pairs the check has always rejected, which is a change
    worth making on its own evidence rather than inside this one.

    :param token: The token being set.
    :param instrument: The scope it is being set under, or None for every
        instrument.
    :param other_token: An existing mode's token.
    :param other_instrument: That mode's scope.
    :return: True when the two could not be told apart on one file.
    :rtype: bool
    """
    if not _tokens_overlap(token, other_token):
        return False
    # Folded, as every comparison of an instrument name is: ORBI-1 and orbi-1
    # are one instrument, so two modes scoped to those spellings share a scope.
    key, other_key = instrument_key(instrument), instrument_key(other_instrument)
    if key == other_key:
        return True
    if key and other_key:
        return False
    scoped, shared = (token, other_token) if key else (other_token, token)
    return not _token_covers(scoped, shared)


async def token_is_unique(
    token: str,
    ignore_id: str | None = None,
    instrument: str | None = None,
) -> bool:
    """Validate if an ionization mode token overlaps with an existing one.

    Overlap, not equality: a token that contains another, or is contained by
    one, would make a file name match both. It still does not completely
    guarantee that a specific filename would only match one token.

    **Compared within the scope** (#1463). A mode scoped to one instrument is
    never matched against another instrument's file names, so requiring its
    token to be unique across the whole server would forbid exactly what the
    scope is for: two instruments spelling their own chemistry the same way in
    their own file names. A scoped and an unscoped mode are still compared,
    because both match the scoped instrument's files -
    :func:`tokens_conflict` has the rule.

    :param token: The ionization mode token to validate.
    :type token: str
    :param ignore_id: An optional ionization mode ID to ignore during the check (useful when updating).
    :type ignore_id: str | None
    :param instrument: The scope the token is being set under, or None for
        every instrument.
    :type instrument: str | None
    :return: True if the token is unique, False otherwise.
    :rtype: bool
    """
    all_modes = await fetch_all_ionization_modes()
    for mode in all_modes:
        if not mode.ionization_mode_token or mode.ionization_mode_id == ignore_id:
            continue
        if tokens_conflict(
            token, instrument, mode.ionization_mode_token, mode.instrument
        ):
            runtime.logger.debug(
                f"Token '{token}' conflicts with existing token "
                f"'{mode.ionization_mode_token}'"
            )
            return False
    return True
