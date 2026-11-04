"""The readings of an MS2 group: its fragments as formulas, over named ions.

What ``GET /api/samples/{id}/ms2/readings`` makes of a group, the composition
core (:mod:`mascope_tools.composition.ms2`) doing the reading. Design note
``docs/dev/ms2_analysis.md`` section 2.5; the rules and every threshold of the
core are in ``libraries/tools/docs/ms2.md``, and its section 11 says what this
module adds.

**The window.** A group is one (polarity, parent peak, activation) group, and
its isolation window is its parent peak's: the precursor and whatever was
co-isolated with it, at every energy the parent peak was fragmented at. The
core reads a window's spectra together - a peak is a harmonic ghost or a side
lobe by its place across the energies, and a fourth loss piece is admitted on
a ladder seen at any energy (``ladder_gate``) - so the readings of a group are
read over every group of its parent peak in the sample, one averaged spectrum
each, as peaky reads a parent's window.

**Window ions are named, never guessed** (decision 4), and checked
(:func:`window_ions`):

1. Each must lie in the window, within +/-(width / 2 + 5 mDa) of the key at
   the widest width the group's scans used. A group that stores no width has
   no window to hold a co-isolate in, and holds its precursor alone.
2. Two named ions within the reading tolerance of each other are aliases: one
   peak reads as both and the window cannot tell them apart, so only the first
   named is read, and the other is named as left out.
3. One of them is the target, the group's precursor: the key is the m/z the
   method typed, which must lie within max(10 ppm, 5 mDa) of the target's
   exact m/z, as a key typed with two decimals does. Of several, the one
   nearest the located precursor.

A named ion that breaks 1 is refused (:class:`IonRefused`) with the nearest
key and its offset from it, so a typo or another window's ion says where it
belongs; a window none of whose ions keeps 3, with the nearest named ion's
offset from its key.

**The recalibration.** Each window is recalibrated once, by one constant-ppm
offset on the target's located precursor (``mass_offset``), when that peak is
the target's: within max(10 ppm, 0.3 mDa) of its exact m/z, as peaky matches
a target to its located precursor. Otherwise the window is read on the served
axis, offset 0, and the response says why.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from mascope_signal.compute import MS2_WINDOW_PAD
from mascope_signal.ms2_store import group_key
from mascope_thermo.thermo import PRECURSOR_SEARCH_MDA, PRECURSOR_SEARCH_PPM
from mascope_tools.composition.ms2 import (
    CORE_VERSION,
    DEFAULT_TOLERANCE,
    LADDER_K,
    READING_TOLERANCE_MDA,
    Reading,
    format_ion,
    ion_mz,
    ladder_gate,
    mass_offset,
    parse_ion,
    read_window,
    spectrum_counts,
)


#: How far the group's key may sit from its target's exact m/z: the window the
#: precursor is searched for in around a key (``PRECURSOR_SEARCH_PPM``,
#: ``PRECURSOR_SEARCH_MDA``, max(10 ppm, 5 mDa)). The key is the isolation
#: centre the method typed: one typed with two decimals sits millidaltons off
#: its ion, and a formula of another window sits farther.
KEY_MATCH_PPM = PRECURSOR_SEARCH_PPM
KEY_MATCH_MDA = PRECURSOR_SEARCH_MDA
#: How near the target's exact m/z its located precursor must lie for the
#: window to be recalibrated on it: peaky's ``TARGET_MATCH_PPM``, over the
#: reading window's absolute floor. Located precursors sat within 1.8 ppm of
#: their formulas on peaky's own files, and the nearest other window ion 16 ppm
#: away (2.2 mDa at m/z 137): a peak farther off is not the target's, and an
#: offset on it would move every reading by as much.
TARGET_MATCH_PPM = 10.0
TARGET_MATCH_MDA = READING_TOLERANCE_MDA
#: The most loss pieces a reading may have: a fourth only on a ladder.
K_MAX = LADDER_K
#: What the response says its window ions are: the ions its caller named.
IONS_NAMED = "named"
#: The layout of the readings this module makes of the core's: part of the
#: cache key beside the core's version, raised whenever what a response says
#: of the same readings changes, so a payload cached by another layout is
#: never served.
READINGS_FORMAT = 1


class IonRefused(ValueError):
    """A named ion the window does not hold, or a window none of whose named
    ions is its precursor; the message says where it belongs."""


@dataclass(frozen=True)
class NamedIon:
    """A named ion: its formula as the core writes it, and its exact m/z."""

    ion: str
    mz: float


@dataclass(frozen=True)
class WindowIons:
    """The ions a window is read against, and the named ions left out.

    ``ions`` is the order the window is read in, the target first and then the
    other named ions in the order named; ``left_out`` the aliases, each a
    record naming the ion it is an alias of.
    """

    target: NamedIon
    ions: tuple[NamedIon, ...]
    left_out: tuple[dict, ...]


def parse_ions(text: str, polarity: str | None) -> list[NamedIon]:
    """The comma-separated ion formulas of ``ions``, as the core writes them,
    in the order named.

    :param text: ``"C8H11N4O2+,C10H11O4+"``: Mascope's notation, 15N as
        ``[15N]``, peaky's ``^N`` accepted.
    :param polarity: The sample's polarity, which every ion must have.
    :raises IonRefused: For no ion, a formula that is no singly charged ion
        (the core's reason), one ion named twice, or an ion of the other
        polarity than the sample's.
    """
    named: list[NamedIon] = []
    for item in text.split(","):
        if not item.strip():
            continue
        try:
            counts, charge = parse_ion(item)
        except ValueError as e:
            # A '+' typed into a URL's query string arrives as a space
            hint = (
                "; a '+' in a query string reads as a space, so send it as %2B"
                if item != item.rstrip()
                else ""
            )
            raise IonRefused(f"'{item.strip()}' is no ion formula: {e}{hint}") from e
        ion = format_ion(counts, charge)
        if any(n.ion == ion for n in named):
            raise IonRefused(f"{ion} is named twice")
        sign = "+" if charge > 0 else "-"
        if polarity in ("+", "-") and sign != polarity:
            raise IonRefused(
                f"{ion} is {'a cation' if sign == '+' else 'an anion'}, and the "
                f"sample's MS2 scans are of polarity '{polarity}'"
            )
        named.append(NamedIon(ion, ion_mz(ion)))
    if not named:
        raise IonRefused(
            "No ion named: name the window ions as ion formulas, the "
            "precursor's first, e.g. 'C8H11N4O2+,C10H11O4+'"
        )
    return named


def _matches_key(mz: float, key_mz: float) -> bool:
    """Whether an ion's exact m/z is one a key typed for it may sit at."""
    return abs(mz - key_mz) <= max(KEY_MATCH_PPM * mz * 1e-6, KEY_MATCH_MDA * 1e-3)


def _offset(mz: float, key_mz: float) -> str:
    """An ion's offset from a key, in mDa and ppm of the key."""
    return f"{(mz - key_mz) * 1e3:+.2f} mDa ({(mz - key_mz) / key_mz * 1e6:+.1f} ppm)"


def _nearest_key(mz: float, groups: Sequence[dict]) -> str:
    """The key of the group whose parent peak is nearest ``mz``, and the
    offset of ``mz`` from it."""
    nearest = min(groups, key=lambda g: abs(mz - g["parent_peak_mz"]))
    key = group_key(nearest["parent_peak_mz"], nearest["activation"])
    return f"{key}, {_offset(mz, nearest['parent_peak_mz'])} from it"


def window_ions(
    named: Sequence[NamedIon],
    group: dict,
    width: float | None,
    located_mz: float | None,
    groups: Sequence[dict],
) -> WindowIons:
    """The ions a group's window is read against (the module's rules 1 to 3).

    :param named: The named ions, in the order named (:func:`parse_ions`).
    :param group: The group, as the summary lists it.
    :param width: The width its window is drawn at, None for none.
    :param located_mz: Its located precursor, None for none.
    :param groups: Every group of the sample, for the nearest key a refused
        ion is told of.
    :raises IonRefused: For a named ion the window does not hold, or no named
        ion at the key.
    """
    key = group_key(group["parent_peak_mz"], group["activation"])
    key_mz = group["parent_peak_mz"]
    for n in named:
        if width is not None:
            if abs(n.mz - key_mz) <= width / 2 + MS2_WINDOW_PAD:
                continue
            where = (
                f"lies in no isolation window of the group {key} "
                f"({key_mz:.4f} +/- {width / 2 + MS2_WINDOW_PAD:.4f})"
            )
        elif _matches_key(n.mz, key_mz):
            continue
        else:
            where = (
                f"is not the precursor of the group {key}, whose scans store no "
                "isolation width, so that its window holds its precursor alone"
            )
        raise IonRefused(
            f"{n.ion} (m/z {n.mz:.5f}) {where}. The nearest key is "
            f"{_nearest_key(n.mz, groups)}."
        )

    kept: list[NamedIon] = []
    left_out: list[dict] = []
    for n in named:
        alias = next(
            (
                k
                for k in kept
                if abs(n.mz - k.mz) <= DEFAULT_TOLERANCE.window_da(max(n.mz, k.mz))
            ),
            None,
        )
        if alias is None:
            kept.append(n)
            continue
        left_out.append(
            {
                "ion": n.ion,
                "mz": n.mz,
                "alias_of": alias.ion,
                "offset_mda": (n.mz - alias.mz) * 1e3,
                "note": (
                    f"{n.ion} left out: {(n.mz - alias.mz) * 1e3:+.2f} mDa from "
                    f"{alias.ion}, inside the reading tolerance, so one peak reads "
                    "as both and the window cannot tell them apart; the first "
                    "named is read"
                ),
            }
        )

    at_key = [n for n in kept if _matches_key(n.mz, key_mz)]
    if not at_key:
        nearest = min(kept, key=lambda n: abs(n.mz - key_mz))
        raise IonRefused(
            f"No named ion is the precursor of the group {key}: none lies within "
            f"max({KEY_MATCH_PPM:g} ppm, {KEY_MATCH_MDA:g} mDa) of its key "
            f"{key_mz:.4f}, and the nearest, {nearest.ion} (m/z {nearest.mz:.5f}), "
            f"is {_offset(nearest.mz, key_mz)} from it. Name the precursor's ion "
            "with the co-isolates."
        )
    anchor = key_mz if located_mz is None else located_mz
    # min keeps the first named of equally near ones
    target = min(at_key, key=lambda n: abs(n.mz - anchor))
    return WindowIons(
        target=target,
        ions=(target, *(n for n in kept if n is not target)),
        left_out=tuple(left_out),
    )


def recalibration(target: NamedIon, located_mz: float | None) -> dict:
    """The window's one constant-ppm offset, measured on its target's located
    precursor, when that peak is the target's.

    :return: ``offset_ppm`` (0 when the window is not recalibrated),
        ``anchor_ion`` and ``anchor_mz`` (the target and its located
        precursor, None when not recalibrated), ``residual_ppm`` and
        ``note``, which says why a window is not recalibrated.
    """
    plain = {"offset_ppm": 0.0, "anchor_ion": None, "anchor_mz": None}
    if located_mz is None:
        return {
            **plain,
            "residual_ppm": None,
            "note": (
                "No precursor was located in any spectrum of the window, so it "
                "is read on the served axis, with no offset."
            ),
        }
    error = (located_mz - target.mz) / target.mz * 1e6
    tolerance = max(TARGET_MATCH_PPM * target.mz * 1e-6, TARGET_MATCH_MDA * 1e-3)
    if abs(located_mz - target.mz) > tolerance:
        return {
            **plain,
            "residual_ppm": None,
            "note": (
                f"The precursor located at {located_mz:.5f} is {error:+.1f} ppm "
                f"from {target.ion}, farther than max({TARGET_MATCH_PPM:g} ppm, "
                f"{TARGET_MATCH_MDA:g} mDa): it is not taken for the target's, "
                "and the window is read on the served axis, with no offset. An "
                f"offset of {error:+.1f} ppm would match it."
            ),
        }
    offset = mass_offset([(located_mz, target.ion)])
    (anchor,) = offset.anchors
    return {
        "offset_ppm": offset.offset_ppm,
        "anchor_ion": target.ion,
        "anchor_mz": located_mz,
        "residual_ppm": anchor.residual_ppm,
        "note": None,
    }


def window_spectra(
    spectra: Mapping[tuple[float, str], tuple[np.ndarray, ...]],
    parent_peak_mz: float,
    noise_threshold: float,
) -> dict[str, dict[str, np.ndarray]]:
    """The window's spectra as the core reads them: each group of the parent
    peak, keyed by its group key in the order given, its centroids at or
    above the S/N floor as the centroids route serves them.

    :param spectra: The sample's averaged spectra by (parent peak m/z,
        activation), ``(masses, intensities, resolutions, signal_to_noise)``
        on the file's calibrated axis.
    """
    window = {}
    for (pp, activation), (masses, intensities, resolutions, sn) in spectra.items():
        if pp != parent_peak_mz:
            continue
        mask = sn >= noise_threshold
        window[group_key(pp, activation)] = {
            "mz": np.asarray(masses[mask], dtype=np.float64),
            "intensity": np.asarray(intensities[mask], dtype=np.float64),
            "signal_to_noise": np.asarray(sn[mask], dtype=np.float64),
            "resolution": np.asarray(resolutions[mask], dtype=np.float64),
        }
    return window


def cache_key(
    group: str,
    named: Sequence[NamedIon],
    spectra: Mapping[str, Mapping[str, np.ndarray]],
    width: float | None,
    located_mz: float | None,
    noise_threshold: float,
) -> str:
    """What a group's readings are cached under: a digest of everything they
    are made of.

    The core's version and this module's layout, the group asked for, the ions
    named in their order, the window's width and located precursor, the S/N
    floor, and the window's spectra themselves, every value's bytes. A
    recalibration of the file moves the spectra, a sample of another window
    averages other scans, and either is another key: an entry is served only
    for the same inputs, and so answers what reading them again would.
    """
    digest = hashlib.sha256()
    head = {
        "core_version": CORE_VERSION,
        "format": READINGS_FORMAT,
        "group": group,
        "ions": [n.ion for n in named],
        "width": width,
        "located_mz": located_mz,
        "noise_threshold": noise_threshold,
        "spectra": list(spectra),
    }
    digest.update(json.dumps(head, sort_keys=True).encode())
    for peaks in spectra.values():
        for name in ("mz", "intensity", "signal_to_noise", "resolution"):
            digest.update(name.encode())
            digest.update(np.ascontiguousarray(peaks[name], dtype="<f8").tobytes())
    return digest.hexdigest()


def _value(value):
    """A reading's field as served: None where the row has none, an empty
    text or a NaN, which JSON cannot carry."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value or None
    return value


def _row(reading: Reading, signal_to_noise: float) -> dict:
    """One served row of a reading (``docs/ms2.md`` section 11)."""
    return {
        "peak": reading.peak,
        "mz": _value(reading.mz),
        "mz_cal": _value(reading.mz_cal),
        "intensity": _value(reading.intensity),
        "signal_to_noise": _value(signal_to_noise),
        "role": reading.role,
        "window_ion": _value(reading.parent_ion),
        "formula": _value(reading.formula),
        "parity": _value(reading.parity),
        "dbe": _value(reading.dbe),
        "ppm": _value(reading.ppm),
        "loss": _value(reading.loss),
        "pieces": list(reading.pieces),
        "unnamed_pieces": reading.unnamed_pieces,
        "radical_loss": reading.radical_loss,
        "rank": reading.rank,
        "n_candidates": reading.n_candidates,
        "n_parents": reading.n_parents,
        "ladder_support": _value(reading.ladder_support),
        "note": _value(reading.flag_note),
    }


def read_group(
    spectra: Mapping[str, Mapping[str, np.ndarray]],
    window: WindowIons,
    offset_ppm: float,
) -> list[dict]:
    """The readings of a window's spectra, per spectrum.

    Read by the core as one window (``read_window`` at ``k_max`` 4, its
    harmonic ghosts and side lobes flagged, then ``ladder_gate``), at the
    window's offset, against its ions in their order.

    :return: Per spectrum, in the order given: ``group`` (its key), its counts
        (``spectrum_counts``: ``tic``, ``n_peaks``, ``n_harmonic``,
        ``harmonic_intensity``, ``n_satellite``, ``satellite_intensity``) and
        ``readings``, one row per (peak, window ion, reading), every peak in
        it at least once.
    """
    ions = {n.ion: n.ion for n in window.ions}
    rows = ladder_gate(read_window(spectra, ions, k_max=K_MAX, offset_ppm=offset_ppm))
    counts = {c.spectrum: c for c in spectrum_counts(rows)}
    by_spectrum: dict[str, list[dict]] = {key: [] for key in spectra}
    for r in rows:
        by_spectrum[r.spectrum].append(
            _row(r, float(spectra[r.spectrum]["signal_to_noise"][r.peak]))
        )
    out = []
    for key, served in by_spectrum.items():
        c = counts.get(key)
        out.append(
            {
                "group": key,
                "tic": c.tic if c else 0.0,
                "n_peaks": c.n_peaks if c else 0,
                "n_harmonic": c.n_harmonic if c else 0,
                "harmonic_intensity": c.harmonic_intensity if c else 0.0,
                "n_satellite": c.n_satellite if c else 0,
                "satellite_intensity": c.satellite_intensity if c else 0.0,
                "readings": served,
            }
        )
    return out


#: What ``ions`` says each window ion is: the group's precursor, or an ion
#: co-isolated with it.
ROLE_TARGET = "target"
ROLE_CO_ISOLATE = "co_isolate"


def served_ions(window: WindowIons) -> list[dict]:
    """The window ions as served, in the order read: each ion, its exact m/z
    and its role, the target first."""
    return [
        {
            "ion": n.ion,
            "mz": n.mz,
            "role": ROLE_TARGET if n is window.target else ROLE_CO_ISOLATE,
        }
        for n in window.ions
    ]


def _described_window(
    described: dict,
    width: float | None,
    noise_threshold: float | None,
) -> dict:
    """What a response says of a group's window beside its ions and spectra."""
    return {
        **described,
        "width": width,
        "ions_source": IONS_NAMED,
        "noise_threshold": noise_threshold,
        "k_max": K_MAX,
        "tolerance": {"ppm": DEFAULT_TOLERANCE.ppm, "mda": DEFAULT_TOLERANCE.mda},
    }


def payload(
    described: dict,
    window: WindowIons,
    spectra: Mapping[str, Mapping[str, np.ndarray]],
    width: float | None,
    located_mz: float | None,
    noise_threshold: float,
) -> bytes:
    """A group's readings as the response's ``data``, in JSON: what is cached,
    and what is served, the first time as every later one.

    :param described: The group (``group``, ``polarity``, ``parent_peak_mz``,
        ``activation``).
    :param window: Its window ions (:func:`window_ions`).
    :param spectra: Its window's spectra (:func:`window_spectra`).
    :param width: The width its window was drawn at.
    :param located_mz: Its located precursor.
    :param noise_threshold: The S/N floor its spectra were read at.
    """
    recalibrated = recalibration(window.target, located_mz)
    data = {
        **_described_window(described, width, noise_threshold),
        "ions": served_ions(window),
        "left_out": list(window.left_out),
        "recalibration": recalibrated,
        "spectra": read_group(spectra, window, recalibrated["offset_ppm"]),
    }
    return json.dumps(data, allow_nan=False, separators=(",", ":")).encode()


def unread(described: dict) -> dict:
    """The ``data`` of a group whose file's MS2 scans are not read: no ion,
    no spectrum."""
    return {
        **_described_window(described, None, None),
        "ions": [],
        "left_out": [],
        "recalibration": None,
        "spectra": [],
    }
