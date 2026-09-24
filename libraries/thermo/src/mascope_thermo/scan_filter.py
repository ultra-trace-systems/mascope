"""Thermo scan filters, parsed into the signature that tells scan streams apart.

Every scan of a Thermo acquisition carries a filter: one line of text that
says what the instrument was told to measure in that scan::

    FTMS - p NSI Full ms [40.0000-600.0000]
    FTMS + c ESI d Full ms2 445.1200@hcd30.00 [50.0000-500.0000]
    FTMS {1,2} + p NSI cv=-45.00 SIM msx ms [300.0000-320.0000, 400.0000-420.0000]
    ITMS + c NSI sid=35.00 d w Full ms3 500.00@cid35.00 300.00@cid35.00 [135.00-1000.00]

Scans that share a signature form a scan stream
(``docs/dev/ingest_routing_and_splitting.md``, section 4). Both reader
backends hand the filter over as text rendered from the file's scan events:
OpenTFRaw by its own filter builder, the Thermo library from its parsed
``IScanFilter``. One parser serves both, and it normalises numbers, so a
difference in precision does not split a key: OpenTFRaw writes m/z to four
decimals, the Thermo library to the file's own precision.

The grammar is Xcalibur's. An optional mass analyzer and ``{segment,event}``
come first, then the polarity, the scan data type and the ionization source.
Flags follow, then the scan mode, the MS order, one precursor per MSn stage and
the scan ranges. A token this parser does not know is kept as a flag rather
than dropped. A key that is too fine splits a file needlessly; one that is too
coarse pools scans that must not be pooled, and only the second goes
unnoticed.

The two renderings are not identical, because OpenTFRaw does not render every
token the Thermo library writes. The Thermo library writes ``lock`` into the
filter of every scan that found its lock mass, and OpenTFRaw never does.
Whether a scan found it is a per-scan outcome, so ``lock`` is kept in
``flags`` but left out of the signature. OpenTFRaw also leaves out the source
fragmentation (``sid=20.00``), the FAIMS compensation voltage (``cv=``), flags
such as wideband activation (``w``) and multiplexing (``msx``), and the
``{segment,event}`` prefix. The signature leaves out the prefix too; the
others stay in it, because they change what a scan measures, so there the
backends' keys can differ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


ANALYZERS = frozenset({"FTMS", "ITMS", "ASTMS", "TOFMS", "TQMS", "SQMS", "SECTOR"})
SOURCES = frozenset(
    {"EI", "CI", "FAB", "ESI", "APCI", "NSI", "TSP", "FD", "MALDI", "GD", "APPI"}
)
SCAN_MODES = frozenset({"Full", "SIM", "SRM", "CRM", "Z", "Q1MS", "Q3MS"})
# Flags that report how one scan went rather than what the method asked of it.
# Kept in ScanFilter.flags, left out of the signature.
STATE_FLAGS = frozenset({"lock"})

# "[40.0000-600.0000]" or "[300.0000-320.0000, 400.0000-420.0000]": the last
# bracketed group of the filter. Taken out before tokenising, because the
# ranges of a multiplexed scan are separated by ", " and would split apart.
_RANGES = re.compile(r"\[([^\[\]]*)\]\s*$")
_RANGE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$")
_SEGMENT_EVENT = re.compile(r"^\{(\d+),(\d+)\}$")
_MS_ORDER = re.compile(r"^ms(\d*)$")
# "445.1200@hcd30.00", chained "445.1200@cid30.00@hcd20.00", or "@etd" with no
# energy. The same shape backend._MS2_EVENT reads, as a whole token.
_PRECURSOR = re.compile(r"^(\d+(?:\.\d+)?)((?:@[A-Za-z]+-?[\d.]*)+)$")
_VALUED = re.compile(r"^(sid|cv)=(-?\d+(?:\.\d+)?)$")


@dataclass(frozen=True)
class Precursor:
    """One MSn stage: the isolated m/z and how it was activated.

    ``activation`` is lower-cased and keeps any chained activations, each
    energy at two decimals: ``"hcd30.00"`` or ``"cid30.00@hcd20.00"``. The
    decimals are normalised so that both backends' renderings give one key;
    ``backend._parse_ms2_event`` keeps them as rendered for its own grouping.
    """

    mz: float
    activation: str


@dataclass(frozen=True)
class ScanFilter:
    """A parsed scan filter.

    Every field is ``None`` (or empty) when the filter does not say, so a
    filter the parser cannot read at all still yields a value rather than an
    exception: its unknown tokens land in ``flags`` and still separate it from
    every other filter.
    """

    text: str
    analyzer: str | None = None
    segment: int | None = None
    event: int | None = None
    polarity: str | None = None
    data_type: str | None = None
    source: str | None = None
    source_fragmentation: float | None = None
    compensation_voltage: float | None = None
    dependent: bool = False
    flags: tuple[str, ...] = ()
    scan_mode: str | None = None
    ms_order: int | None = None
    precursors: tuple[Precursor, ...] = ()
    scan_ranges: tuple[tuple[float, float], ...] = ()

    @property
    def signature_flags(self) -> tuple[str, ...]:
        """The flags that belong in the signature: all but :data:`STATE_FLAGS`."""
        return tuple(flag for flag in self.flags if flag not in STATE_FLAGS)

    @property
    def folds_precursors(self) -> bool:
        """Whether scans of this filter belong to one family whatever they isolate.

        A data-dependent MSn scan isolates whatever the survey scan picked, so
        its precursor, and the scan range that follows from it, change from
        scan to scan. All of them form one stream. A targeted MSn scan
        isolates what the method names, so each precursor is its own stream.
        """
        return self.dependent and (self.ms_order or 1) > 1

    def signature(self, resolution: int | str | None = None) -> dict:
        """The fields that tell this filter's stream apart from others.

        The segment and scan event numbers are left out: they index the method's
        layout, not what a scan measures. So are :data:`STATE_FLAGS`, and the
        precursor m/z values and scan ranges of data-dependent MSn scans (see
        :attr:`folds_precursors`).

        :param resolution: The scan's FT resolution. It is not part of the
            filter, so the caller reads it from the scan's trailer.
        :return: A JSON-safe dict of the signature fields.
        """
        folded = self.folds_precursors
        return {
            "analyzer": self.analyzer,
            "polarity": self.polarity,
            "data_type": self.data_type,
            "source": self.source,
            "source_fragmentation": self.source_fragmentation,
            "compensation_voltage": self.compensation_voltage,
            "dependent": self.dependent,
            "flags": list(self.signature_flags),
            "scan_mode": self.scan_mode,
            "ms_order": self.ms_order,
            "precursors": [
                {"mz": None if folded else p.mz, "activation": p.activation}
                for p in self.precursors
            ],
            "scan_ranges": [] if folded else [list(r) for r in self.scan_ranges],
            "resolution": resolution,
        }

    def stream_key(self, resolution: int | str | None = None) -> str:
        """The signature as one stable line of text.

        Fields come in the filter's order and notation, so the key reads like
        the filter it came from: ``FTMS - p NSI Full ms [40.0000-600.0000] R=120000``.
        m/z values are rendered with four decimals and energies with two,
        whatever precision the reader rendered, so both backends produce the
        same key. A data-dependent MSn family shows ``*`` for its precursor
        m/z and no scan range.

        :param resolution: The scan's FT resolution, from its trailer.
        :return: The stream key.
        """
        folded = self.folds_precursors
        parts: list[str] = []
        if self.analyzer:
            parts.append(self.analyzer)
        for value in (self.polarity, self.data_type, self.source):
            if value:
                parts.append(value)
        if self.source_fragmentation is not None:
            parts.append(f"sid={self.source_fragmentation:.2f}")
        if self.compensation_voltage is not None:
            parts.append(f"cv={self.compensation_voltage:.2f}")
        if self.dependent:
            parts.append("d")
        parts.extend(self.signature_flags)
        if self.scan_mode:
            parts.append(self.scan_mode)
        if self.ms_order is not None:
            parts.append("ms" if self.ms_order == 1 else f"ms{self.ms_order}")
        for precursor in self.precursors:
            mz = "*" if folded else f"{precursor.mz:.4f}"
            parts.append(f"{mz}@{precursor.activation}")
        if self.scan_ranges and not folded:
            ranges = ", ".join(f"{lo:.4f}-{hi:.4f}" for lo, hi in self.scan_ranges)
            parts.append(f"[{ranges}]")
        if resolution is not None:
            parts.append(f"R={resolution}")
        return " ".join(parts)


def _energy(value: str) -> str:
    """An activation such as ``"hcd30.0"`` with its energy at two decimals."""
    match = re.match(r"^([a-z]+)(-?\d+(?:\.\d+)?)?$", value)
    if not match or match.group(2) is None:
        return value
    return f"{match.group(1)}{float(match.group(2)):.2f}"


def _parse_precursor(token: str) -> Precursor | None:
    match = _PRECURSOR.match(token)
    if not match:
        return None
    activations = match.group(2).lstrip("@").lower().split("@")
    return Precursor(
        mz=float(match.group(1)),
        activation="@".join(_energy(a) for a in activations),
    )


def _parse_ranges(text: str) -> tuple[tuple[float, float], ...] | None:
    """``"40.00-600.00, 700.00-800.00"`` as pairs, or ``None`` if any is malformed."""
    ranges = []
    for part in text.split(","):
        match = _RANGE.match(part)
        if not match:
            return None
        ranges.append((float(match.group(1)), float(match.group(2))))
    return tuple(ranges)


def parse_scan_filter(text: str | None) -> ScanFilter:
    """Parse a Thermo scan filter.

    Never raises. A token in an unexpected place, or one this parser does not
    know, is kept in ``flags``. A token that states a default by negating it
    (``!d``, ``!corona``) says nothing a missing token would not, and is
    dropped so that both spellings give the same signature.

    :param text: The filter as a reader renders it. ``None`` reads as empty.
    :return: The parsed filter.
    """
    text = (text or "").strip()
    rest = text
    scan_ranges: tuple[tuple[float, float], ...] = ()
    if match := _RANGES.search(rest):
        parsed = _parse_ranges(match.group(1))
        if parsed is not None:
            scan_ranges = parsed
            rest = rest[: match.start()]

    fields: dict = {}
    flags: list[str] = []
    precursors: list[Precursor] = []
    for index, token in enumerate(rest.split()):
        if index == 0 and token in ANALYZERS:
            fields["analyzer"] = token
        elif match := _SEGMENT_EVENT.match(token):
            fields["segment"], fields["event"] = int(match[1]), int(match[2])
        elif token in ("+", "-") and "polarity" not in fields:
            fields["polarity"] = token
        elif token in ("p", "c") and "data_type" not in fields:
            fields["data_type"] = token
        elif token in SOURCES and "source" not in fields:
            fields["source"] = token
        elif match := _VALUED.match(token):
            key = (
                "source_fragmentation" if match[1] == "sid" else "compensation_voltage"
            )
            fields[key] = float(match[2])
        elif token == "d":
            fields["dependent"] = True
        elif token in SCAN_MODES and "scan_mode" not in fields:
            fields["scan_mode"] = token
        elif (match := _MS_ORDER.match(token)) and "ms_order" not in fields:
            fields["ms_order"] = int(match[1]) if match[1] else 1
        elif "ms_order" in fields and (precursor := _parse_precursor(token)):
            precursors.append(precursor)
        elif token.startswith("!"):
            continue
        else:
            flags.append(token)

    return ScanFilter(
        text=text,
        flags=tuple(sorted(flags)),
        precursors=tuple(precursors),
        scan_ranges=scan_ranges,
        **fields,
    )
