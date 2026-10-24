"""A raw file, or a time window of one, written as mzML.

The writing is OpenTFRaw's own ``RawFile.to_mzml``, which writes every scan of
a file. What this module adds is the window and one correction, made in a
streaming pass over that output, so that a file of any length goes through in
constant memory.

**What a spectrum holds.** ``to_mzml`` writes each scan's centroids - the
peaks :meth:`ReaderBackend.centroids_per_scan` reads, m/z as 64-bit and
intensity as 32-bit floats - and labels the spectrum "profile spectrum"
(``MS:1000128``). The profile is not in the file. The pass relabels every
spectrum "centroid spectrum" (``MS:1000127``), which is what it is; the test
beside this module holds the arrays to the reader's centroids, so a reader
upgrade that starts writing the profile fails there rather than leaving the
label wrong the other way.

**What a window keeps.** The scans the reader selects for the same polarity
and time range (:meth:`ReaderBackend.scan_indices`, MS1 and MS2 alike), so a
sample's file holds the scans the sample reads, the first-scan outlier its
selection leaves out included. The file's chromatograms are left out of a
window: ``to_mzml`` writes them over the whole file, which a window's file
would then contradict. A whole-file export keeps every scan and both
chromatograms.

**What the instrument is.** ``to_mzml`` describes the instrument from a model
table of its own, which does not agree with the PSI-MS ontology everywhere
(an accession of its table can be a term that is no instrument model), and
with no ion source, no detector and no ``componentList``. Given an
:class:`InstrumentConfiguration`, the pass replaces every
``instrumentConfiguration`` the writer declares, under the writer's own ids
so that the run and each scan still point at one, with the model term and,
when all three are known, a ``componentList`` of the source, the analyser and
the detector. The terms are the caller's: this module maps no names to
accessions.

The pass relies on the layout ``to_mzml`` writes, one element tag per line,
and checks what it relies on: a spectrum whose scan number it cannot read, or
a spectrum count that disagrees with the list's, raises :class:`MzMLLayoutError`
instead of writing a file that would not parse.
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import NamedTuple
from xml.sax.saxutils import quoteattr

import opentfraw

from mascope_thermo.backend import Polarity, open_backend


PROFILE_TERM = 'accession="MS:1000128" name="profile spectrum"'
CENTROID_TERM = 'accession="MS:1000127" name="centroid spectrum"'

_SPECTRUM_LIST = re.compile(r'<spectrumList count="(\d+)"')
_SPECTRUM_OPEN = re.compile(r"<spectrum\s")
_SPECTRUM_SCAN = re.compile(r'\bid="[^"]*\bscan=(\d+)[^"]*"')
_SPECTRUM_INDEX = re.compile(r'\bindex="\d+"')
_CONFIGURATION_OPEN = re.compile(r'<instrumentConfiguration\s[^>]*\bid="([^"]*)"')


class MzMLLayoutError(ValueError):
    """The writer's output is not laid out the way the pass reads it."""


class CvParam(NamedTuple):
    """One PSI-MS ``cvParam``: its accession, its name and its value."""

    accession: str
    name: str
    value: str = ""


class InstrumentConfiguration(NamedTuple):
    """What an mzML file says the instrument is, as PSI-MS terms.

    :param model: The instrument model term.
    :param source: The ion source term, None if unknown.
    :param analyser: The mass analyser term, None if unknown.
    :param detector: The detector term, None if unknown. A ``componentList``
        is written only with all three: mzML requires a source, an analyser
        and a detector in one.
    """

    model: CvParam
    source: CvParam | None = None
    analyser: CvParam | None = None
    detector: CvParam | None = None


def write_mzml(
    datafile_path: str,
    out_path: str,
    polarity: Polarity | None = None,
    t_min: float | None = None,
    t_max: float | None = None,
    instrument: InstrumentConfiguration | None = None,
) -> int:
    """Write ``datafile_path`` as mzML 1.1.0 to ``out_path``.

    With no polarity and no time bound, every scan of the file is written.
    Any of them makes it a window: the scans the reader selects for them.

    :param datafile_path: The Thermo raw file.
    :param out_path: Where to write the mzML. Written whole or not at all: the
        file appears only once it is complete.
    :param polarity: ``"+"`` or ``"-"``, the window's polarity.
    :param t_min: The window's start [s], inclusive.
    :param t_max: The window's end [s], inclusive.
    :param instrument: The instrument, written in place of the writer's
        ``instrumentConfigurationList``; None keeps the writer's.
    :raises NoScansFoundError: The window holds no scan.
    :raises MzMLLayoutError: The writer's output could not be read.
    :return: The number of spectra written.
    """
    keep: set[int] | None = None
    if polarity is not None or t_min is not None or t_max is not None:
        with open_backend(datafile_path) as backend:
            keep = set(
                backend.scan_indices(
                    polarity=polarity, t_min=t_min, t_max=t_max, ms_type=None
                )
            )

    out_dir = os.path.dirname(os.path.abspath(out_path))
    full = tempfile.NamedTemporaryFile(
        dir=out_dir, prefix=".mzml-", suffix=".full", delete=False
    )
    full.close()
    # Named apart from ``out_path`` so that two writes of one output, say the
    # same sample file exported twice at once, never share their staging file.
    part = tempfile.NamedTemporaryFile(
        dir=out_dir, prefix=".mzml-", suffix=".part", delete=False
    )
    part.close()
    part = part.name
    try:
        # Each spectrum keeps the reader's extra values for its scan (its
        # trailer, as userParams): about a tenth of the file, and what the
        # instrument recorded of the scan beyond the CV terms.
        opentfraw.RawFile(datafile_path).to_mzml(full.name)
        with (
            open(full.name, encoding="utf-8") as source,
            open(part, "w", encoding="utf-8") as sink,
        ):
            written = _filter(source, sink, keep, instrument=instrument)
        os.replace(part, out_path)
    finally:
        for leftover in (full.name, part):
            if os.path.exists(leftover):
                os.remove(leftover)
    return written


def _filter(
    source,
    sink,
    keep: set[int] | None,
    instrument: InstrumentConfiguration | None = None,
) -> int:
    """Copy ``source`` to ``sink``: the spectra ``keep`` names (every one if
    ``None``), relabelled and re-indexed, no chromatogram in a window, and the
    instrument configurations replaced by ``instrument`` when it is given.

    :return: The number of spectra written.
    """
    listed: int | None = None
    written = 0
    in_spectrum = False
    keeping = False
    in_chromatograms = False
    configurations: list[str] | None = None
    configurations_written = False

    for line in source:
        if configurations is not None:
            match = _CONFIGURATION_OPEN.search(line)
            if match:
                configurations.append(match.group(1))
            if "</instrumentConfigurationList>" in line:
                if not configurations:
                    raise MzMLLayoutError(
                        "The writer's instrument configuration list is empty"
                    )
                sink.write(_configuration_list(configurations, instrument))
                configurations = None
                configurations_written = True
            continue
        if in_spectrum:
            if keeping:
                sink.write(line.replace(PROFILE_TERM, CENTROID_TERM))
            if "</spectrum>" in line:
                in_spectrum = False
            continue
        if in_chromatograms:
            if "</chromatogramList>" in line:
                in_chromatograms = False
            continue

        if _SPECTRUM_OPEN.search(line):
            scan = _SPECTRUM_SCAN.search(line)
            if scan is None:
                raise MzMLLayoutError(f"A spectrum without a scan number: {line!r}")
            keeping = keep is None or int(scan.group(1)) in keep
            if keeping:
                line = _SPECTRUM_INDEX.sub(f'index="{written}"', line, count=1)
                written += 1
                sink.write(line)
            in_spectrum = "</spectrum>" not in line
            continue

        if instrument is not None and "<instrumentConfigurationList" in line:
            if "</instrumentConfigurationList>" in line:
                raise MzMLLayoutError(
                    f"An instrument configuration list on one line: {line!r}"
                )
            configurations = []
            continue

        if keep is not None and "<chromatogramList" in line:
            in_chromatograms = "</chromatogramList>" not in line
            continue

        match = _SPECTRUM_LIST.search(line)
        if match:
            listed = int(match.group(1))
            if keep is not None:
                line = _SPECTRUM_LIST.sub(
                    f'<spectrumList count="{len(keep)}"', line, count=1
                )
        sink.write(line)

    if instrument is not None and not configurations_written:
        raise MzMLLayoutError(
            "The writer's output has no instrument configuration list"
        )
    if listed is None:
        raise MzMLLayoutError("The writer's output has no spectrum list")
    expected = listed if keep is None else len(keep)
    if written != expected:
        raise MzMLLayoutError(
            f"{written} spectra written where {expected} were expected"
        )
    return written


def _cv_param(term: CvParam, indent: str) -> str:
    return (
        f'{indent}<cvParam cvRef="MS" accession={quoteattr(term.accession)}'
        f" name={quoteattr(term.name)} value={quoteattr(term.value)}/>\n"
    )


def _configuration_list(ids: list[str], instrument: InstrumentConfiguration) -> str:
    """The ``instrumentConfigurationList`` for ``instrument``, one
    ``instrumentConfiguration`` under each of the writer's ``ids``."""
    components = [
        ("source", instrument.source),
        ("analyzer", instrument.analyser),
        ("detector", instrument.detector),
    ]
    complete = all(term is not None for _tag, term in components)
    lines = [f'  <instrumentConfigurationList count="{len(ids)}">\n']
    for id_ in ids:
        lines.append(f"    <instrumentConfiguration id={quoteattr(id_)}>\n")
        lines.append(_cv_param(instrument.model, " " * 6))
        if complete:
            lines.append(f'      <componentList count="{len(components)}">\n')
            for order, (tag, term) in enumerate(components, start=1):
                lines.append(f'        <{tag} order="{order}">\n')
                lines.append(_cv_param(term, " " * 10))
                lines.append(f"        </{tag}>\n")
            lines.append("      </componentList>\n")
        lines.append("    </instrumentConfiguration>\n")
    lines.append("  </instrumentConfigurationList>\n")
    return "".join(lines)
