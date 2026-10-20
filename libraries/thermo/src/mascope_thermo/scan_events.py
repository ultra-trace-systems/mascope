"""When a file's scan events are evidently misread.

OpenTFRaw derives a scan's analyzer, polarity, MS order and filter, and the
calibration it converts the scan's profile to m/z with, from the scan's event.
Events read from the wrong bytes give noise that nothing tells apart from
data, except where it names an analyzer the instrument cannot have. So on an
instrument whose only analyzer is an Orbitrap, a scan read as an analyzer no
Orbitrap instrument has refuses the file, whatever made the events misread
(GitHub issue #2394).

**What is judged.** The instrument model is the one OpenTFRaw names: the
first model name it finds in the file's first 64 KB, the sequence row's
sample name, comment and paths included, and only where none is found, the
instrument's own InstID record. A hybrid's file whose free text names a
Q Exactive is therefore read as a Q Exactive's, and its ion-trap scans are
what a hybrid records. So a file is refused only for a scan read as an
analyzer other than the Orbitrap and the ion trap, or as one OpenTFRaw
cannot name. A misread that lands only on the Orbitrap's and the ion trap's
codes goes unseen. The Orbitrap Astral is the exception to that set, and is
kept from the check otherwise (``_ORBITRAP_INSTRUMENT_ANALYZERS``).

A leaf module, imported by both ``mascope_thermo.backend``, which judges
every file it reads, and the interim ``mascope_thermo.v66_scan_events``,
which judges the files it re-reads when it opens them.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence


# Instrument models whose only analyzer is an Orbitrap, as OpenTFRaw names
# them: the Exploris family and the Q Exactive family.
_ORBITRAP_ONLY_MODELS = ("Exploris", "Exactive")

# What an Orbitrap instrument records a scan with: the Orbitrap, and on a
# hybrid the ion trap. Not the Orbitrap Astral's own analyzer, ASTMS, which
# OpenTFRaw 2.0.0 does not name (it names codes 0 to 5), so an Astral scan
# reads as none. What keeps an Astral file from being refused is the order of
# OpenTFRaw's registry instead: "Orbitrap Astral" is its first entry, so a
# file that names it anywhere in its first 64 KB is never read as an Exploris
# or a Q Exactive. ASTMS belongs here once the reader names it.
_ORBITRAP_INSTRUMENT_ANALYZERS = frozenset({"FTMS", "ITMS"})


class MisreadScanEventsError(ValueError):
    """A raw file whose scan events the reader has evidently misread.

    Every value the reader derives from such an event, the m/z axis of the
    scan's profile included, is noise, so the file is refused rather than
    read on it.

    A ``ValueError``, the error class whose text a file's processing status
    shows the person who reads it, and deliberately not a
    ``NoScansFoundError``: the file holds scans, and calling it empty would
    say the wrong thing about it.
    """


def misread_analyzers(model: str | None, analyzers: Sequence[str | None]) -> str | None:
    """What says a file's scan events are misread, or None.

    :param model: The instrument model, as OpenTFRaw names it.
    :param analyzers: Every scan's analyzer, as ``scan_table()`` reads it.
    :return: None when the scans are not judged or none says so; otherwise
        what is wrong, as a clause the caller's message is built around,
        with how many scans read as each analyzer.
    """
    if not model or not any(name in model for name in _ORBITRAP_ONLY_MODELS):
        return None
    foreign = sum(
        1 for analyzer in analyzers if analyzer not in _ORBITRAP_INSTRUMENT_ANALYZERS
    )
    if not foreign:
        return None
    read = ", ".join(
        f"{analyzer or 'none'} {count}"
        for analyzer, count in Counter(analyzers).most_common()
    )
    return (
        f"{foreign} of its {len(analyzers)} scans read as an analyzer no "
        f"Orbitrap instrument has, and the {model} has only the Orbitrap "
        f"(scans per analyzer read: {read})"
    )


def refuse_misread_analyzers(
    model: str | None, analyzers: Sequence[str | None]
) -> None:
    """Refuse a file whose scans read as an analyzer its instrument has not.

    :param model: The instrument model, as OpenTFRaw names it.
    :param analyzers: Every scan's analyzer, as ``scan_table()`` reads it.
    :raises MisreadScanEventsError: When :func:`misread_analyzers` finds the
        file's scan events misread.
    """
    problem = misread_analyzers(model, analyzers)
    if problem:
        raise MisreadScanEventsError(
            f"The scan events of this file cannot be read: {problem}, so the "
            "raw-file reader has misread them (GitHub issue #2394)."
        )
