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
anyway. A file with no census - every Tofwerk h5, and any file converted
before the census existed - falls back to its own polarity and mass range,
which is the same thing at the resolution such a file records it.

**The chemistry key** is what unanimity is judged on. Two modes built on the
same ionization mechanisms are the same chemistry however each is named, so a
key seen under "Nitrate" on one instrument and "NO3" on another is not
ambiguous. Comparing mode rows instead would call a third of the production
fleet's method keys ambiguous where comparing chemistries calls a fortieth.
"""

from __future__ import annotations

import hashlib
import ntpath
import posixpath


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

#: Bound on a stored method key, matching the ``method_binding`` column.
_METHOD_KEY_LIMIT = 256

#: Bound on a stored signature class, matching the ``method_binding`` column.
#: A polarity that pools many streams is clipped rather than refused: the
#: clipped value is still stable for the method, which is all the key needs.
_SIGNATURE_LIMIT = 512


def method_key(method_file: str | None) -> str:
    """The method key for an acquisition's recorded method name.

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
    # Both separators, whatever host reads the file: an Orbitrap reports a
    # Windows path, and Mascope runs on Linux, where ntpath.basename is the
    # only one of the two that splits it.
    name = posixpath.basename(ntpath.basename(str(method_file).strip()))
    key = name.strip().casefold()
    if not key or key in CONSTANT_METHOD_NAMES:
        return ""
    return key[:_METHOD_KEY_LIMIT]


def signature_class(
    streams: list[dict] | None,
    polarity: str,
    mz_range: list | tuple | None = None,
) -> str:
    """What the instrument was told to measure, for one polarity.

    The MS1 stream keys of that polarity, de-duplicated and sorted so that a
    method whose streams interleave in a different order still reads as one
    class, joined with ``" + "``.

    :param streams: The file's scan-stream census
        (``SampleFileProps.scan_streams``), or ``None``/``[]`` when it took
        none.
    :param polarity: The polarity to describe, ``"+"`` or ``"-"``.
    :param mz_range: The file's own mass range, used only when there is no
        census.
    :return: The signature class. ``""`` when neither a census nor a range
        says anything, which keys the binding on the instrument alone.
    :rtype: str
    """
    keys = sorted(
        {
            str(stream.get("key"))
            for stream in streams or []
            if (stream.get("signature") or {}).get("ms_order") == 1
            and (stream.get("signature") or {}).get("polarity") == polarity
            and stream.get("key")
        }
    )
    if keys:
        return " + ".join(keys)[:_SIGNATURE_LIMIT]

    # No census: the polarity and mass range the file itself records. That is
    # the whole of what a Tofwerk acquisition varies, so for those files this
    # is the signature class rather than a degraded stand-in for one.
    bounds = _range_text(mz_range)
    return f"{polarity} {bounds}".strip() if bounds else polarity.strip()


def _range_text(mz_range: list | tuple | None) -> str:
    """``[40.0000-600.0000]`` for a file's mass range, or ``""``.

    Rendered to four decimals like the census's own scan ranges, so the two
    sources of a signature class read alike.
    """
    if not mz_range or len(mz_range) < 2:
        return ""
    try:
        low, high = float(mz_range[0]), float(mz_range[1])
    except (TypeError, ValueError):
        return ""
    return f"[{low:.4f}-{high:.4f}]"


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

    Its ionization mechanisms, sorted and joined: the same string for two
    modes a deployment happens to have named differently, and a different one
    the moment the reagents differ. A mode with no mechanisms keys on ``""``,
    so any two such modes read as one chemistry - they describe no reagent to
    tell apart, and nothing creates one.

    :param mechanism_ids: ``ionization_mode.ionization_mechanism_ids``.
    :return: The chemistry key.
    :rtype: str
    """
    return ",".join(sorted(str(mid) for mid in (mechanism_ids or []) if mid))
