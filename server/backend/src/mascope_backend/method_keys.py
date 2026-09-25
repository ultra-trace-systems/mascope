"""
The identity a method binding is keyed on.

A binding maps (instrument, method key, signature class) to an ionization
mode, so that a file whose method has been seen before routes without a
filename token (``docs/dev/ingest_routing_and_splitting.md``, section 5.3).
This module builds those three strings, and the chemistry key the binding
records its history under. All pure: no database, no reader, no runtime, so
the ingest path, the backfill script and the tests share one definition.

**The method key** is the acquisition method's file name, without its
directory and case folded. Instruments report it differently - an Orbitrap
gives a path into the instrument's method store, a Tofwerk h5 gives its
configuration file name - and the same method reaches Mascope spelled both
ways over the years, so only the basename is kept.

**A name that never varies is not a name.** Some instruments report one
fixed configuration name for every acquisition they take. Such a name
separates nothing, so it is treated as no method name at all: the key falls
back to the signature class, which yields to the filename token. Leaving it
in would be worse than dropping it - an instrument that has so far run one
chemistry looks perfectly consistent, learns a binding from the constant, and
then outranks the token the day the reagent changes.

**The signature class** is what the instrument was told to measure, for one
polarity: analyzer, source, scan mode, scan ranges and resolution, taken from
the scan-stream census. A method that alternates two ranges within a polarity
has both in its class, because today's peak detection pools them into one
peak list. Precursors are deliberately absent: the census keys a
data-dependent MSn family as one stream, and only MS1 streams are read here
anyway.

**A file with no census keys on its polarity, or on nothing at all.** Which
of the two depends on the instrument. A reader that never takes a census -
TofDaq h5 is one acquisition on one mass axis, with no per-scan filter to
tell scans apart - says everything it has to say with the polarity, so that
is the class. A reader that normally does take one, on a file converted
before the census existed, is a different case: the class is *unknown*, and
:func:`signature_class` answers ``None`` so the caller learns nothing from
that file. Guessing would be worse, because the guess and the census would
key the same method two ways and split its history between them - one half
holding the disagreements, the other doing the routing.

*What must not be used is the file's own mass range.* It is an outcome, not
an instruction: a TofDaq file records the ends of its mass axis, which move
with each file's mass calibration, so on one production instrument 2,095
files of one method carry 1,331 distinct ranges. Keying on it gives nearly
every file a binding of its own, and unanimity is never tested at all.

**The chemistry key** is what unanimity is judged on. Two modes built on the
same ionization mechanisms are the same chemistry however each is named, so a
key seen under "Nitrate" on one instrument and "NO3" on another is not
ambiguous. Comparing mode rows instead would call a third of the production
fleet's method keys ambiguous where comparing chemistries calls a fortieth.
"""

from __future__ import annotations

import hashlib
import ntpath


#: Method names an instrument reports for every acquisition, so that they
#: separate nothing. Case folded, as :func:`method_key` compares them.
#:
#: ``currentacquisition.ini`` is Tofwerk's: on the production fleet it is the
#: method name of 82% of all TOF files, across several reagent chemistries and
#: on instruments that have never reported anything else. Recognising a
#: constant by measurement instead - a name every one of an instrument's files
#: has carried - is the obvious generalisation, and is left until a second
#: vendor needs it: on few files "constant so far" and "the only method run so
#: far" are the same reading, and acting on it would hold back exactly the
#: bindings a new deployment needs most.
CONSTANT_METHOD_NAMES: frozenset[str] = frozenset({"currentacquisition.ini"})

#: Instrument types whose reader records a scan-stream census. A file of one
#: of these that carries none was converted before the census existed, so its
#: signature class is unknown rather than absent - see :func:`signature_class`.
CENSUS_BEARING_INSTRUMENT_TYPES: frozenset[str] = frozenset({"orbi"})

#: Width of the ``method_binding.method_key`` column.
METHOD_KEY_COLUMN = 256

#: Width of the ``method_binding.signature_class`` column. A polarity that
#: pools many streams is clipped for storage rather than refused; the digest
#: is taken from the full value, so two methods that differ only past this
#: length still key apart.
SIGNATURE_CLASS_COLUMN = 512


def method_key(method_file: str | None) -> str:
    """The method key for an acquisition's recorded method name.

    Not clipped: the caller clips for storage with :func:`clipped`, and the
    digest is taken from the whole value.

    :param method_file: ``sample_file.method_file``, as the instrument
        reported it: a path, a bare name, empty, or absent.
    :return: The case-folded basename, or ``""`` when the name is absent or
        is one that never varies. ``""`` means "no method name": the caller
        keys on the signature class alone, and that binding yields to the
        filename token.
    :rtype: str
    """
    if not method_file:
        return ""
    # ntpath, on any host: it splits on "/" as well as "\\", so it handles
    # both the Windows path an Orbitrap reports and a posix one, while
    # posixpath would leave a Windows path whole on Linux.
    key = ntpath.basename(str(method_file).strip()).strip().casefold()
    if not key or key in CONSTANT_METHOD_NAMES:
        return ""
    return key


def usable_streams(streams: object) -> list[dict]:
    """The census entries a caller may walk without guarding each field.

    Every entry that comes back is a dict whose ``signature`` is a dict. A
    census of another shape is a ``.props`` nothing in Mascope wrote; the
    entries that do not fit are dropped rather than failing the file.

    One filter, because two callers have to agree on what counts as a census:
    :func:`process.status.read_scan_streams` reads it for the binding, and
    ``backfill_scan_stream_census`` decides from it which files still need
    one. If they disagreed, a file one of them called filled would be a file
    the other skipped for good.

    :param streams: The ``scan_streams`` field of a ``.props``, whatever it
        holds.
    :return: The entries of a shape a caller may walk; empty for anything else.
    :rtype: list[dict]
    """
    if not isinstance(streams, list):
        return []
    return [
        stream
        for stream in streams
        if isinstance(stream, dict) and isinstance(stream.get("signature"), dict)
    ]


def signature_class(
    streams: list[dict] | None,
    polarity: str,
    instrument_type: str | None = None,
) -> str | None:
    """What the instrument was told to measure, for one polarity.

    The MS1 stream keys of that polarity, de-duplicated and sorted so that a
    method whose streams interleave in a different order still reads as one
    class, joined with ``" + "``. Not clipped: the caller clips for storage
    with :func:`clipped`, and the digest is taken from the whole value.

    With no census the answer depends on the instrument. One whose reader
    never records a census has nothing further to say, and its polarity is
    the class. One whose reader normally does - a file converted before the
    census existed - has an *unknown* class, and this answers ``None`` so
    that nothing is learned from it: a stand-in would key the same method
    apart from the census that later files carry, and split its history.

    :param streams: The file's scan-stream census
        (``SampleFileProps.scan_streams``), or ``None``/``[]`` when it has
        none. Entries must be dicts with a dict ``signature``, which
        ``process.status.read_scan_streams`` guarantees.
    :param polarity: The polarity to describe, ``"+"`` or ``"-"``.
    :param instrument_type: ``sample_file.instrument_type``, read only when
        there is no census.
    :return: The signature class, or ``None`` when it cannot be known.
    :rtype: str | None
    """
    keys = sorted(
        {
            str(stream["key"])
            for stream in streams or []
            if stream.get("signature", {}).get("ms_order") == 1
            and stream.get("signature", {}).get("polarity") == polarity
            and stream.get("key")
        }
    )
    if keys:
        return " + ".join(keys)

    if instrument_type in CENSUS_BEARING_INSTRUMENT_TYPES:
        return None
    return polarity.strip()


def clipped(value: str, limit: int) -> str:
    """A key as its column stores it.

    Only for storage. Everything that decides identity - the digest, and so
    which files share a binding - reads the unclipped value, or two methods
    differing only past the column width would share one row.

    :param value: The full key.
    :param limit: The column width, :data:`METHOD_KEY_COLUMN` or
        :data:`SIGNATURE_CLASS_COLUMN`.
    :return: The value, clipped.
    :rtype: str
    """
    return value[:limit]


def binding_digest(instrument: str, key: str, signature: str) -> str:
    """The stored identity of a binding, as one short comparable string.

    The three parts are joined under a separator none of them can contain -
    a NUL - so that no pair of different identities can fold into one string,
    and hashed. The digest is what carries the uniqueness: an index over the
    three columns themselves would be up to 832 characters wide, and Postgres
    refuses a btree entry past about 2700 bytes, so a method name in a
    multi-byte script could have been stored right up to the point the index
    refused it.

    :param instrument: ``sample_file.instrument``.
    :param key: The method key, from :func:`method_key`.
    :param signature: The signature class, from :func:`signature_class`.
    :return: A 64-character hex digest.
    :rtype: str
    """
    joined = "\0".join((instrument or "", key or "", signature or ""))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def chemistry_key(mechanism_ids: list[str] | None) -> str:
    """The chemistry a mode stands for, as one comparable string.

    Its ionization mechanisms as a set, sorted and joined: the same string for
    two modes a deployment happens to have named differently, and a different
    one the moment the reagents differ. A mode with no mechanisms keys on
    ``""``, so any two such modes read as one chemistry - they describe no
    reagent to tell apart, and nothing creates one.

    A repeated mechanism id counts once, as it does everywhere else: the
    modes API compares mechanism lists as sets (``_mechanism_set``) and its
    create schema accepts a repeat, so a mode stored with one would otherwise
    read as a second chemistry and record a disagreement that did not happen.

    :param mechanism_ids: ``ionization_mode.ionization_mechanism_ids``.
    :return: The chemistry key.
    :rtype: str
    """
    return ",".join(sorted({str(mid) for mid in (mechanism_ids or []) if mid}))
