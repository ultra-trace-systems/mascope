"""The instrument function of each scan stream of a file detected per stream.

A file's instrument function - its peak shape, and its resolution as a
function of m/z - is fitted on its summed signal, once per file
(:func:`mascope_signal.instrument_func.fit.fit_instrument_functions`). A raw
Orbitrap file whose peaks are detected per scan stream and stitched
(``mascope_signal.stitch``) holds several scan ranges, each acquired at its
own settings, and one fit over all of them is a compromise between ranges
whose peaks need not be of one width. So each of its streams is fitted as
well, on the stream's own summed signal, the signal its peaks are picked
from (``docs/dev/ingest_routing_and_splitting.md``, 4.3 and 4.5).

A stream too thin to fit takes the fit of a sibling: a stream of the same
analyzer at the same resolution setting, of its own polarity first and then
the nearest in m/z. With no such sibling it takes the file's. Its record
says which.

The fits are recorded in the peak store's attributes, beside the stitch map
and keyed by stream as the m/z calibration's per-stream factors are
(``mascope_signal.mz_factor``):

.. code-block:: python

    {
        "file": {"resolution_function": [a]},
        "streams": {
            "FTMS - p NSI Full ms [132.0000-490.0000] R=120000": {
                "resolution_function": [a],
                "peakshape": {"x": [...], "y": [...]},
                "source": "fitted",
                "origin": None,
                "peaks": 41,
                "note": None,
            },
            ...
        },
    }

A store detected whole carries none: its one function is the file's.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_signal.compute as m_compute
import mascope_signal.mz_factor as m_factor
import mascope_thermo.thermo as m_thermo
from mascope_signal.instrument_func import fit as m_fit
from mascope_signal.runtime import runtime
from mascope_thermo.scan_filter import parse_stream_key


#: The peak store attribute the streams' instrument functions are kept in.
INSTRUMENT_FUNCTIONS_ATTR = "instrument_functions"

#: How a stream came by its instrument function: fitted on its own summed
#: signal, a sibling stream's, or the file's.
FITTED, SIBLING, FILE = "fitted", "sibling", "file"

#: The m/z an Orbitrap's resolution is stated at, by convention.
RESOLUTION_REFERENCE_MZ = 200.0


def _fit_stream(filename: str, key: str) -> tuple:
    """Fit one stream's instrument function, at the converter's settings.

    On the stream's summed signal as the raw file reads it, not as the file's
    sum-signal cache keeps it: the fit reads peak shapes and widths, which
    the m/z calibration does not move by anything a width shows, and a
    detection leaves no stream's signal cached behind it.
    """
    sum_signal, _ = m_thermo.compute_sum_signal(
        m_name.filename_to_datafile_path(filename), stream=key
    )
    return m_fit.fit_instrument_functions(
        filename,
        dmz=m_fit.ORBI_FIT_DMZ,
        r_sq_thres=m_fit.FIT_R_SQ_THRESHOLD,
        sum_signal=sum_signal,
    )


def _middle(stream: dict) -> float | None:
    """The middle of a stream's scan ranges in m/z, or None where it states
    none."""
    ranges = (stream.get("signature") or {}).get("scan_ranges") or []
    if not ranges:
        return None
    return (min(r[0] for r in ranges) + max(r[1] for r in ranges)) / 2


def _sibling(streams: list[dict], index: int, fitted: set[int]) -> int | None:
    """The fitted stream a stream too thin to fit takes its function from.

    One of the same analyzer at the same resolution setting, which are what
    set a peak's width; of the stream's own polarity before another, then
    the one nearest in m/z, then the first in the file.
    """
    signature = streams[index].get("signature") or {}
    middle = _middle(streams[index])

    def rank(other: int) -> tuple:
        other_signature = streams[other].get("signature") or {}
        other_middle = _middle(streams[other])
        distance = (
            abs(other_middle - middle)
            if middle is not None and other_middle is not None
            else float("inf")
        )
        return (
            other_signature.get("polarity") != signature.get("polarity"),
            distance,
            other,
        )

    candidates = [
        other
        for other in fitted
        if (streams[other].get("signature") or {}).get("analyzer")
        == signature.get("analyzer")
        and (streams[other].get("signature") or {}).get("resolution")
        == signature.get("resolution")
    ]
    return min(candidates, key=rank) if candidates else None


def _record(peak_shape: dict, resolution_function) -> dict:
    """A fit as the store records it: JSON-safe."""
    return {
        "resolution_function": m_fit.resolution_coefficients(resolution_function),
        "peakshape": {
            "x": np.asarray(peak_shape["x"], dtype=float).tolist(),
            "y": np.asarray(peak_shape["y"], dtype=float).tolist(),
        },
    }


def fit_stream_functions(
    filename: str,
    streams: list[dict],
    file_functions: tuple,
) -> tuple[list[tuple], dict]:
    """Fit the instrument function of each stream of a file detected per
    stream.

    :param filename: Sample file filename
    :type filename: str
    :param streams: The streams the file's peaks are detected per, as the
        census gives them (``mascope_thermo.streams.peak_streams``)
    :type streams: list[dict]
    :param file_functions: The file's own peak shape and resolution function,
        what a stream with no fit and no sibling takes
    :type file_functions: tuple
    :return: Per stream, in the order given, its peak shape and resolution
        function; and the record of them the peak store keeps
        (:data:`INSTRUMENT_FUNCTIONS_ATTR`)
    :rtype: tuple[list[tuple], dict]
    """
    fits: dict[int, tuple] = {}
    notes: dict[int, str] = {}
    peaks: dict[int, int] = {}
    for index, stream in enumerate(streams):
        try:
            peak_shape, resolution_function, stats = _fit_stream(
                filename, stream["key"]
            )
        except ValueError as error:
            # Too few quality peaks, mostly: a short reagent scan, a window
            # that read little. A data condition, which the record states.
            notes[index] = str(error)
            continue
        except Exception as error:  # noqa: BLE001 - the stream still has a function
            # A fit that fails otherwise costs the stream its own function,
            # not the file its peaks: it is given another, as a thin one is
            # The file is named by the line below that says what the stream
            # takes instead; this one is the same for every file it happens to
            runtime.logger.opt(exception=True).warning(
                "Fitting the instrument function of a scan stream failed"
            )
            notes[index] = f"Its fit failed: {error}"
            continue
        fits[index] = (peak_shape, resolution_function)
        peaks[index] = (stats.get("peakshape") or {}).get("num_of_peaks")

    functions: list[tuple] = []
    record_streams: dict[str, dict] = {}
    for index, stream in enumerate(streams):
        if index in fits:
            source, origin, chosen = FITTED, None, fits[index]
        else:
            sibling = _sibling(streams, index, set(fits))
            if sibling is not None:
                source, origin = SIBLING, streams[sibling]["key"]
                chosen = fits[sibling]
            else:
                source, origin, chosen = FILE, None, tuple(file_functions)
            runtime.logger.info(
                f"Scan stream '{stream['key']}' of '{filename}' has no instrument "
                f"function of its own ({notes.get(index)}); it takes "
                + (f"that of '{origin}'" if origin else "the file's")
            )
        functions.append(chosen)
        record_streams[stream["key"]] = {
            **_record(*chosen),
            "source": source,
            "origin": origin,
            "peaks": peaks.get(index),
            "note": notes.get(index),
        }
    record = {
        "file": {
            "resolution_function": m_fit.resolution_coefficients(file_functions[1])
        },
        "streams": record_streams,
    }
    return functions, record


def resolution_at_reference(coefficients: list[float] | None) -> float | None:
    """The resolution an Orbitrap resolution function gives at m/z 200, how an
    Orbitrap's resolution is stated.

    :param coefficients: The function's stored coefficients
    :return: The resolution, or None for a function that is not an
        Orbitrap's
    """
    if not coefficients or len(coefficients) != 1:
        return None
    return float(m_fit.r_orbi(RESOLUTION_REFERENCE_MZ, coefficients[0]))


class SegmentResolution:
    """The resolution of a stitched polarity: each m/z by the function of the
    stream that owns it under the stitch map.

    A peak of a composite is read from the stream that owns its m/z
    (``mascope_signal.stitch.owners``), so its expected width is that
    stream's. An m/z no run owns is in no composite, and goes by the
    ``fallback``.
    """

    def __init__(
        self,
        runs: list,
        functions: dict[int, Callable],
        fallback: Callable,
        factors: np.ndarray | None = None,
    ):
        """
        :param runs: The polarity's runs, ``[lower, upper, stream index]``
        :param functions: The resolution function of each stream, by index
        :param fallback: What an m/z outside every run goes by
        :param factors: The m/z calibration factor of each stream, by index,
            which places a run on the file's calibrated axis
        """
        self._runs = runs
        self._functions = functions
        self._fallback = fallback
        self._factors = factors

    def owner(self, mz: np.ndarray) -> np.ndarray:
        """The index of the stream that owns each m/z, -1 where none does.

        The runs are in m/z as the instrument recorded them, and a run is
        placed on the file's axis by the factor of the stream that owns it,
        as ``mascope_signal.compute.peak_store_composite`` places it.
        """
        mz = np.asarray(mz, dtype=np.float64)
        out = np.full(mz.shape, -1, dtype=np.int64)
        for lower, upper, index in self._runs:
            factor = 1.0 if self._factors is None else float(self._factors[index])
            inside = (mz >= lower * factor) & (mz < upper * factor) & (out == -1)
            out[inside] = index
        return out

    def __call__(self, mz):
        scalar = np.ndim(mz) == 0
        values = np.atleast_1d(np.asarray(mz, dtype=np.float64))
        owner = self.owner(values)
        out = np.asarray(self._fallback(values), dtype=np.float64).copy()
        for index, function in self._functions.items():
            rows = owner == index
            if rows.any():
                out[rows] = function(values[rows])
        return float(out[0]) if scalar else out


def _polarity_of(key: str) -> str | None:
    """The polarity a stream key names."""
    try:
        return parse_stream_key(key).scan_filter.polarity
    except Exception:  # noqa: BLE001 - a key that does not parse names none
        return None


def peak_store_resolution(
    base_filename: str,
    fallback: Callable,
    polarity: str | None = None,
    stream: str | None = None,
) -> Callable:
    """The resolution function a file's peaks are read by, from the stream
    each came from.

    A file detected per stream whose store records its streams' functions
    (:func:`fit_stream_functions`) is read stream by stream: asked for one
    stream, by that stream's function; asked for a polarity, by the
    function of the stream that owns each m/z under the stitch map
    (:class:`SegmentResolution`), or of the polarity's one stream. Anything
    else - a store detected whole, one detected before the streams were
    fitted apart, a polarity it does not hold - goes by ``fallback``, the
    file's own.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param fallback: The file's own resolution function
    :type fallback: Callable
    :param polarity: The polarity the peaks are of
    :type polarity: str, optional
    :param stream: Key of the one stream the peaks are of
    :type stream: str, optional
    :raises FileNotFoundError: If the file has no peak store
    :return: Resolution as a function of m/z
    :rtype: Callable
    """
    header = m_compute.read_peak_store_header(base_filename)
    record = header.attrs.get(INSTRUMENT_FUNCTIONS_ATTR) or {}
    by_key = record.get("streams") or {}
    if not header.streams or not by_key:
        return fallback

    def function_of(key: str) -> Callable | None:
        coefficients = (by_key.get(key) or {}).get("resolution_function")
        return m_fit.resolution_function_of(coefficients) if coefficients else None

    if stream is not None:
        return function_of(stream) or fallback
    if polarity is None:
        return fallback
    stitch = header.stitch_map() or {}
    runs = (stitch.get("runs") or {}).get(polarity)
    if runs:
        functions = {
            index: function
            for index, key in enumerate(header.streams)
            if (function := function_of(key)) is not None
        }
        calibration = m_io.read_props(base_filename).get("mz_calibration")
        return SegmentResolution(
            runs,
            functions,
            fallback,
            m_factor.stream_factors(calibration, header.streams),
        )
    own = [key for key in header.streams if _polarity_of(key) == polarity]
    if len(own) == 1:
        return function_of(own[0]) or fallback
    return fallback
