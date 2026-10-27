"""
Chemistry detection on a file being bound: rung 5, as an audit.

Each polarity of a file the pipeline binds or parks is read for the reagent
ions its spectrum shows (:mod:`mascope_tools.composition.chemistry_detection`),
and what the detector calls is stored on the file beside what bound it
(``sample_file.binding_evidence``,
``docs/dev/ingest_routing_and_splitting.md`` section 5.4). It binds nothing:
the rung that bound the file still bound it, and a parked file still waits for
a person. What it is for is the measurement that decides whether it ever may -
how often a call agrees with the token, the acquisition record, the method or
a person, read on the corpus and on each server's shadow.

**It never costs a file its processing.** A spectrum that cannot be read or
a store that is not there is logged, and the file's evidence is cleared
rather than left as an earlier run wrote it, beside another binding. The
detector runs after the binding and changes nothing the run does next.

**Switched by ``backend.chemistry_detection``**: ``"audit"``, the default,
reads and stores; ``"off"`` stores nothing.

**One answer per polarity**, read off the peaks the polarity's sample reads:
a stitched polarity's composite, each m/z from the segment that owns it, so
a reagent window and the analyte windows of one method are one spectrum. The
scan ranges that decide what was recorded at all are the census's, the MS1
streams of the polarity; a file without a census is judged against its own
m/z range.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select, update

from mascope_backend.api.controllers.samples.lib.samples_peaks import extract_peaks
from mascope_backend.db import (
    IonizationMechanism,
    IonizationMode,
    SampleFile,
    async_session,
)
from mascope_backend.runtime import runtime
from mascope_tools.composition.chemistry_detection import (
    ChemistryDetection,
    detect_chemistry,
)
from mascope_tools.composition.profiles import detect_reagent_profile


def detects_chemistry() -> bool:
    """Whether this deployment reads its files' chemistry at all.

    ``chemistry_detection`` in the runtime ``[backend]`` config: ``"audit"``
    (the default) reads and stores it, ``"off"`` stores nothing.
    """
    return getattr(runtime.config, "chemistry_detection", "audit") != "off"


@dataclass(frozen=True)
class PolarityBinding:
    """What bound one polarity of a file, as its sample records it.

    :param ionization_mode_id: The mode the polarity was bound to.
    :param bound_by: The rung its sample records (``sample_item.bound_by``),
        None where it records none - a mode kept from an earlier run that no
        rung decided.
    """

    ionization_mode_id: str
    bound_by: str | None


def ms1_polarities(streams: Sequence[dict] | None, polarities: str | None) -> list[str]:
    """The polarities of a file that hold MS1 scans, in the file's order.

    :param streams: The file's scan-stream census.
    :param polarities: ``sample_file.polarity``.
    :return: Those the census shows MS1 streams of; every polarity of the
        file when there is no census, which says nothing either way.
    """
    if not streams:
        return list(polarities or "")
    surveyed = {
        (stream.get("signature") or {}).get("polarity")
        for stream in streams
        if (stream.get("signature") or {}).get("ms_order") == 1
    }
    return [polarity for polarity in polarities or "" if polarity in surveyed]


def polarity_scan_ranges(
    streams: Sequence[dict] | None,
    polarity: str,
    file_range: Sequence[float] | None,
) -> list[tuple[float, float]]:
    """The m/z ranges a polarity's MS1 scans recorded.

    The census's own, every MS1 stream of the polarity's scan ranges; for a
    file without a census - one converted before it, or a TOF file, whose
    reader takes none - the file's m/z range.

    :param streams: The file's scan-stream census.
    :param polarity: ``"+"`` or ``"-"``.
    :param file_range: ``sample_file.range``, ``[low, high]``.
    :return: The ranges, empty when neither says.
    """
    ranges: list[tuple[float, float]] = []
    for stream in streams or ():
        signature = stream.get("signature") or {}
        if signature.get("ms_order") != 1 or signature.get("polarity") != polarity:
            continue
        for scan_range in signature.get("scan_ranges") or ():
            try:
                low, high = (float(value) for value in scan_range)
            except (TypeError, ValueError):
                continue
            ranges.append((low, high))
    if ranges:
        return sorted(set(ranges))
    if file_range and len(file_range) == 2:
        try:
            return [(float(file_range[0]), float(file_range[1]))]
        except (TypeError, ValueError):
            return []
    return []


def _read_spectrum(
    filename: str, polarity: str, instrument_type: str
) -> tuple[list[float], list[float]]:
    """A polarity's peaks, as its sample reads them: heights on an Orbitrap,
    areas on a TOF, each averaged over its own stream's scans. Blocking."""
    peaks = extract_peaks(filename, polarity, 0.0, 0.0)
    values = peaks.heights if instrument_type == "orbi" else peaks.areas
    return peaks.mz_values, list(values or [])


async def _bound_profiles(
    bindings: Mapping[str, PolarityBinding], instrument_type: str
) -> dict[str, str]:
    """The profile each bound polarity's mode names by its mechanisms.

    Read as assignment reads a sample's profile
    (:func:`~mascope_tools.composition.profiles.detect_reagent_profile`), so
    a call and a binding are compared in one vocabulary.

    :return: Polarity to profile name, for the polarities whose mode exists.
    """
    if not bindings:
        return {}
    async with async_session() as session:
        modes = {
            mode.ionization_mode_id: mode
            for mode in (
                await session.execute(
                    select(IonizationMode).where(
                        IonizationMode.ionization_mode_id.in_(
                            {b.ionization_mode_id for b in bindings.values()}
                        )
                    )
                )
            ).scalars()
        }
        mechanism_ids = {
            mechanism_id
            for mode in modes.values()
            for mechanism_id in mode.ionization_mechanism_ids or ()
        }
        notations = {
            mechanism.ionization_mechanism_id: mechanism.ionization_mechanism
            for mechanism in (
                await session.execute(
                    select(IonizationMechanism).where(
                        IonizationMechanism.ionization_mechanism_id.in_(mechanism_ids)
                    )
                )
            ).scalars()
        }
    profiles = {}
    for polarity, binding in bindings.items():
        mode = modes.get(binding.ionization_mode_id)
        if mode is None:
            continue
        profiles[polarity] = detect_reagent_profile(
            [
                notations[mechanism_id]
                for mechanism_id in mode.ionization_mechanism_ids or ()
                if mechanism_id in notations
            ],
            polarity,
            instrument_type=instrument_type,
        ).name
    return profiles


def polarity_evidence(
    detection: ChemistryDetection,
    binding: PolarityBinding | None,
    bound_profile: str | None,
) -> dict:
    """One polarity's stored evidence: what bound it, beside what was detected.

    ``agrees`` is the audit's question: True or False where the detector made
    a call and a mode was bound, None where either is missing - a parked
    polarity, or a spectrum that called nothing.
    """
    agrees = (
        detection.profile == bound_profile
        if detection.called and bound_profile is not None
        else None
    )
    return {
        "bound": None
        if binding is None
        else {
            "ionization_mode_id": binding.ionization_mode_id,
            "bound_by": binding.bound_by,
            "profile": bound_profile,
        },
        "agrees": agrees,
        "detection": detection.to_json(),
    }


async def _detect(
    sample_file: SampleFile,
    streams: Sequence[dict] | None,
    polarities: Sequence[str],
    bindings: Mapping[str, PolarityBinding],
) -> dict:
    """Each polarity's evidence (:func:`record_chemistry_detection`)."""
    instrument_type = sample_file.instrument_type
    bound_profiles = await _bound_profiles(bindings, instrument_type)
    evidence = {}
    for polarity in polarities:
        mz, intensity = await asyncio.to_thread(
            _read_spectrum, sample_file.filename, polarity, instrument_type
        )
        detection = detect_chemistry(
            mz,
            intensity,
            polarity=polarity,
            instrument_type=instrument_type,
            scan_ranges=polarity_scan_ranges(streams, polarity, sample_file.range),
        )
        evidence[polarity] = polarity_evidence(
            detection, bindings.get(polarity), bound_profiles.get(polarity)
        )
    return evidence


async def record_chemistry_detection(
    sample_file: SampleFile,
    streams: Sequence[dict] | None,
    polarities: Sequence[str],
    bindings: Mapping[str, PolarityBinding] | None = None,
) -> dict | None:
    """Detect each polarity's chemistry and store it beside its binding.

    Audit only: the evidence is written to the file and logged where it
    disagrees with the binding, and nothing else reads it. Never raises.

    :param sample_file: The file, bound or parked.
    :param streams: Its scan-stream census.
    :param polarities: The MS1 polarities to read.
    :param bindings: What bound each polarity this run; empty or None for a
        parked file.
    :return: The evidence written; None when the file has no spectrum, the
        read failed (the column is cleared then) or the write did.
    """
    bindings = dict(bindings or {})
    filename = sample_file.filename
    evidence: dict | None = None
    # A blank, or an acquisition of fragmentation scans alone, is stored
    # without MS1 peaks: there is no spectrum to read, and its evidence is
    # cleared like a failed read's. So is a file bound while detection is
    # switched off.
    if sample_file.instrument_function_id is not None and detects_chemistry():
        try:
            evidence = await _detect(sample_file, streams, polarities, bindings)
        except Exception as exc:  # noqa: BLE001 - an audit never costs a file its run
            # WARNING: the detector is expected to read every converted file;
            # a failure is a fault to look at, not a data condition.
            runtime.logger.opt(exception=True).warning(
                f"Chemistry detection on '{filename}' failed: {exc}"
            )
    try:
        # Written whatever came of it: an earlier run's evidence describes
        # that run's binding, and must not stand beside this one's.
        async with async_session() as session:
            await session.execute(
                update(SampleFile)
                .where(SampleFile.sample_file_id == sample_file.sample_file_id)
                .values(binding_evidence=evidence)
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - as above
        runtime.logger.opt(exception=True).warning(
            f"Could not store the chemistry detection of '{filename}': {exc}"
        )
        return None
    for polarity, item in (evidence or {}).items():
        if item["agrees"] is False:
            detection = item["detection"]
            # INFO: the audit's finding, a data condition a person reads in
            # aggregate; nothing about the file changed.
            runtime.logger.info(
                f"Chemistry detection reads '{filename}' ({polarity}) as "
                f"{detection['profile']} (score {detection['score']}), but it "
                f"was bound to a {item['bound']['profile']} mode "
                f"by {item['bound']['bound_by'] or 'an earlier run'}; audit only"
            )
    return evidence
