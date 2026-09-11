"""The reference lists Mascope ships, and loading them.

The lists live in this package, one schema 2 file per list
(:mod:`mascope_reference.peaklist`). :func:`seed` loads each as its own source
through the same versioned ingest ``mascope reference sync`` runs - the list's
id names the source and its data version versions it - so a seeded source is
reported, re-activated and replaced like any other.

A list loads by default unless its header says ``load_by_default: false``,
which the radical lists do: a radical competes with the closed-shell molecule
for the same peak, so it is something to ask for. Seeding a deployment is opt-in
as a whole - a command an operator runs - and only the local demo does it
unasked.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Engine

from mascope_reference.adapters.peaklist import PeakListAdapter
from mascope_reference.ingest import DEFAULT_BATCH_SIZE, ingest
from mascope_reference.peaklist import PeakList, admitted_species, read_peak_list
from mascope_reference.schema import reference_source


#: Where the shipped lists live inside the package.
LISTS_DIRECTORY = "lists"


@dataclass(frozen=True)
class SeedOutcome:
    """What seeding did with one list."""

    list_id: str
    version: str
    #: False when this version was already the active one, so nothing loaded.
    loaded: bool
    ingested: int = 0
    #: Radicals a list without ``allow_radicals`` kept out of the load.
    held_back: int = 0


def lists_directory() -> Path:
    """The directory the shipped lists are read from."""
    return Path(str(files("mascope_reference").joinpath(LISTS_DIRECTORY)))


def catalogue(directory: Path | None = None) -> list[PeakList]:
    """Every list in a directory - the shipped ones by default - by file name.

    :param directory: Where to read the lists from; None for the shipped lists.
    :return: The lists, ordered by file name.
    """
    root = directory or lists_directory()
    return [read_peak_list(path) for path in sorted(root.glob("*.json"))]


def select_lists(
    lists: Sequence[PeakList],
    names: Sequence[str] | None = None,
    *,
    include_optional: bool = False,
) -> list[PeakList]:
    """The lists a seed loads.

    :param lists: The lists to choose from.
    :param names: List ids to load, exactly these and in this order; None for
        every list that loads by default.
    :param include_optional: With no names, also take the lists that do not
        load by default.
    :raises KeyError: For a name no list carries, naming the ones that exist.
    :return: The chosen lists.
    """
    if names:
        by_id = {peak_list.id: peak_list for peak_list in lists}
        unknown = [name for name in names if name not in by_id]
        if unknown:
            raise KeyError(
                f"No shipped reference list is named {', '.join(unknown)}. "
                f"Available: {', '.join(sorted(by_id))}"
            )
        return [by_id[name] for name in dict.fromkeys(names)]
    return [
        peak_list
        for peak_list in lists
        if peak_list.load_by_default or include_optional
    ]


def seed(
    engine: Engine,
    *,
    names: Sequence[str] | None = None,
    include_optional: bool = False,
    prune: bool = False,
    directory: Path | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress: Callable[[int], None] | None = None,
) -> list[SeedOutcome]:
    """Load lists, each as the active version of its own source.

    A list whose version is already the active one is left alone, so seeding
    twice changes nothing - which is what lets the demo seed on every start. A
    newer version replaces the older one the way a re-sync does, and ``prune``
    then deletes the load it replaced.

    :param engine: Synchronous engine for the target database.
    :param names: List ids to load; None for every list that loads by default.
    :param include_optional: With no names, also load the lists that do not
        load by default.
    :param prune: Delete a list's earlier loads once its new version is in.
    :param directory: Where to read the lists from; None for the shipped lists.
    :param batch_size: Rows per bulk insert.
    :param progress: Called with the running row count of the list loading.
    :raises KeyError: For a name no list carries, before anything is loaded.
    :return: One outcome per chosen list, in load order.
    """
    chosen = select_lists(
        catalogue(directory), names, include_optional=include_optional
    )
    outcomes = []
    for peak_list in chosen:
        version = peak_list.data_version or "unversioned"
        held_back = len(peak_list.species) - sum(1 for _ in admitted_species(peak_list))
        if _active_version(engine, peak_list.id) == version:
            outcomes.append(
                SeedOutcome(peak_list.id, version, loaded=False, held_back=held_back)
            )
            continue
        result = ingest(
            engine,
            PeakListAdapter(license=peak_list.license),
            peak_list.path,
            version,
            source_name=peak_list.id,
            batch_size=batch_size,
            prune=prune,
            progress=progress,
        )
        outcomes.append(
            SeedOutcome(
                peak_list.id,
                version,
                loaded=True,
                ingested=result.ingested,
                held_back=held_back,
            )
        )
    return outcomes


def _active_version(engine: Engine, source_name: str) -> str | None:
    """The version currently active for a source, or None."""
    with engine.connect() as conn:
        return (
            conn.execute(
                select(reference_source.c.version)
                .where(reference_source.c.name == source_name)
                .where(reference_source.c.is_active.is_(True))
            )
            .scalars()
            .first()
        )
