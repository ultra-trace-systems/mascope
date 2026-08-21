import logging
import os
from datetime import datetime

from mascope_backend.api.new.instrument_configs.schemas import (
    InstrumentConfigFitParams,
    PeakShape,
)
from mascope_backend.api.new.instrument_configs.service import fit_instrument_functions
from mascope_backend.file_converter.base_processor import (
    BaseFileProcessor,
    SampleFileProps,
    with_file_context,
)
from mascope_backend.file_converter.errors import (
    EMPTY_ACQUISITION_MESSAGE,
    EmptyAcquisitionError,
)
from mascope_thermo.backend import open_backend
from mascope_thermo.thermo import NoScansFoundError, get_polarity_options


_log = logging.getLogger(__name__)


class RawProcessor(BaseFileProcessor):
    """Reads and processes Orbi raw files"""

    def __init__(
        self,
        socket_client,
        file_queue,
        shutdown_event,
        peak_guard=None,
    ):
        super().__init__(
            socket_client=socket_client,
            file_queue=file_queue,
            shutdown_event=shutdown_event,
            peak_guard=peak_guard,
        )

    @property
    def file_extension(self) -> str:
        """Get the file extension for raw files

        :return: File extension
        :rtype: str
        """
        return ".raw"

    @property
    def filename(self) -> str:
        """Base filename of the raw file currently being processed

        :return: Base filename
        :rtype: str
        """
        filename = self._strip_filepath(self.file_to_process).replace(" ", "_")
        timestamp = datetime.fromisoformat(self.timestamp).strftime(
            "%Y.%m.%d-%Hh%Mm%Ss"
        )
        # Add timestamp to the filename
        return filename.replace("_", f"_{timestamp}_", 1)

    @property
    def _is_blank_measurement(self) -> bool:
        """Determine if the file being processed is a blank/zero measurement

        All Orbitrap raw files are assumed to be non-blank measurements.
        """
        return False

    @property
    @with_file_context
    def interval(self) -> float:
        """Mean measurement interval in seconds, i.e. length of one spectrum in the sample

        :return: Measurement interval [s]
        :rtype: float
        """
        scans = self.file_handle.num_scans()
        return self.length / scans if scans else 0.0  # [s]

    @property
    @with_file_context
    def length(self) -> float:
        """Length of the sample file in seconds

        No filters are applied, so a reader that finds nothing here is
        reporting a file with no scans at all. It says so with
        ``NoScansFoundError``, which ``_get_sample_file_props`` names as an
        empty acquisition for the extraction as a whole.

        :return: Sample length [s]
        :rtype: float
        :raises NoScansFoundError: When the file holds no scans.
        """
        times = self.file_handle.scan_times(ms_type=None)  # all scans, seconds
        # No `if times.size` guard: scan selection raises rather than handing
        # back an empty array, so max() always has something to take.
        return float(times.max())  # [s]

    @property
    @with_file_context
    def acquisition_params(self) -> dict:
        """Acquisition parameters reported by the instrument.

        Sampled from the per-scan trailer; see
        ``ReaderBackend.acquisition_parameters``. Capturing this must never cost
        us a file, so any reader failure degrades to an empty dict and a
        warning rather than failing ingestion.

        :return: Acquisition parameter summary, or {} if unavailable
        :rtype: dict
        """
        try:
            return self.file_handle.acquisition_parameters()
        except NoScansFoundError:
            # A scanless file has no per-scan trailer to sample, and neither
            # has one whose scans are all MS2. Either way it is a property of
            # the data, so it must not reach the generic handler below: that
            # logs a traceback at WARNING, the level the error-monitoring sink
            # subscribes to, and this property is read before the one that
            # fails the file - so an empty acquisition would be reported as a
            # fault before the extraction could fail it as data.
            _log.info(
                "No scans to sample acquisition parameters from for %s",
                self.file_to_process,
            )
            return {}
        except Exception:
            _log.warning(
                "Could not read acquisition parameters for %s",
                self.file_to_process,
                exc_info=True,
            )
            return {}

    def _get_sample_file_props(self) -> SampleFileProps:
        """Extract the sample file properties, naming a scanless file as data.

        Every property that asks the reader for scans raises
        ``NoScansFoundError`` when the file recorded none, and which property
        gets there first is decided by the order of the schema fields. Naming
        the condition once, around the whole walk, is what keeps that ordering
        from mattering: no property has to carry its own arm, a property added
        later is covered by construction, and both reader backends are covered
        by the same one.

        :return: The properties extracted from the file
        :rtype: SampleFileProps
        :raises EmptyAcquisitionError: When the file recorded no scans.
        """
        try:
            return super()._get_sample_file_props()
        except NoScansFoundError as e:
            raise EmptyAcquisitionError(EMPTY_ACQUISITION_MESSAGE) from e

    @property
    def method_file(self) -> str:
        """Instrument method file name.

        Not exposed by the OpenTFRaw reader, so reported as empty (the Thermo
        backend also returned "" when absent).

        :return: Instrument method file name
        :rtype: str
        """
        return ""

    @property
    def mz_calibration(self) -> None:
        """M/z calibration coefficient is not applicable for Orbi files

        :return: None
        :rtype: None
        """
        return None

    @property
    @with_file_context
    def range(self) -> list:
        """M/z range of the sample file

        :return: M/z range
        :rtype: list
        """
        low, high = self.file_handle.mass_range()
        return [low, high]

    @property
    def polarity(self) -> str:
        """Polarity options in the sample file

        :return: Polarity options
        :rtype: str
        """
        return get_polarity_options(self.file_to_process)

    @property
    def sample_interval(self) -> None:
        """Sample interval is not applicable for Orbi files

        :return: None
        :rtype: None
        """
        return None

    @property
    def single_ion_signal(self) -> None:
        """Single ion signal is not applicable for Orbi files

        :return: None
        :rtype: None
        """
        return None

    @property
    @with_file_context
    def timestamp(self) -> str:
        """Acquisition timestamp in isoformat, local timezone.

        Uses the reader's file creation date when available; the OpenTFRaw reader
        does not currently surface it, so we fall back to the file's modification
        time. (See ReaderBackend.created.)

        :return: Timestamp
        :rtype: str
        """
        created = self.file_handle.created()
        if created is None:
            created = datetime.fromtimestamp(os.path.getmtime(self.file_to_process))
            # INFO: expected for readers that do not surface the creation
            # date; fires per processed file
            _log.info(
                "Reader did not provide an acquisition date; using file mtime for %s",
                self.file_to_process,
            )
        return created.isoformat()

    def _resolve_utc_offset(self) -> tuple[int, str]:
        """Resolve the UTC offset for this file's local timestamp.

        Raw files embed no offset of their own, so the order is: the zone
        the uploading machine reported, evaluated at the file's own
        timestamp ("agent"); else the converter host's zone at that
        timestamp ("guess") - the legacy fallback, wrong whenever the
        instrument PC and the converter host disagree on timezone or DST.

        Both readings go through ``_wall_time_offset``, which resolves the
        daylight-saving hours a bare wall clock cannot name on its own - the
        repeated one and the skipped one - deliberately, and warns when it
        had to, so a timestamp on the wrong side of a transition can be
        explained rather than merely being wrong.

        Cached for the file being processed. ``utc_offset`` and
        ``utc_offset_source`` are separate schema fields, so the props
        collector asks for both and would otherwise resolve twice - reopening
        the raw file through ``self.timestamp`` each time, and reporting any
        DST anomaly twice for one acquisition. The cache is emptied as each
        file is picked up, so it cannot outlive the file it describes.

        :return: UTC offset [s] and its source ("agent" or "guess")
        :rtype: tuple[int, str]
        """
        cached = self._per_file_cache.get("utc_offset")
        if cached is not None:
            return cached

        local_dt = datetime.fromisoformat(self.timestamp)
        zone = self._context_timezone()
        # zone=None reads it in the converter host's own zone, which is what
        # the "guess" fallback means; total_seconds() keeps west-of-UTC
        # offsets negative.
        resolved = (
            self._wall_time_offset(local_dt, zone),
            "agent" if zone is not None else "guess",
        )
        self._per_file_cache["utc_offset"] = resolved
        return resolved

    @property
    def utc_offset(self) -> int:
        """UTC offset in seconds applied to the local timestamp

        :return: UTC offset [s]
        :rtype: int
        """
        return self._resolve_utc_offset()[0]

    @property
    def utc_offset_source(self) -> str:
        """What determined utc_offset: "agent" or "guess"

        :return: Offset source
        :rtype: str
        """
        return self._resolve_utc_offset()[1]

    @staticmethod
    def _file_context_manager(file_path: str):
        """Context manager for raw files.

        Uses the reader-backend seam (OpenTFRaw by default, Thermo when its DLLs
        are configured) so file processing needs no proprietary dependency.

        :param file_path: Path to the raw file
        :return: Reader backend bound to the file
        :rtype: ReaderBackend
        """
        return open_backend(file_path)

    def _process_instrument_config(
        self, sample_file_props: SampleFileProps
    ) -> tuple[any, any, any, any]:
        """Fit instrument functions"""
        dmz = 0.01
        fit_params = InstrumentConfigFitParams()
        (
            peakshape_numpy,
            resolution_function_partial,
            _,
        ) = fit_instrument_functions(
            sample_file_props.filename, r_sq_thres=fit_params.threshold, dmz=dmz
        )

        # Convert peakshape to lists to be serialized
        peakshape = PeakShape(
            x=peakshape_numpy["x"].tolist(), y=peakshape_numpy["y"].tolist()
        )

        # Get resolution function coefficients
        partial_coefficients = resolution_function_partial.keywords
        resolution_function = [partial_coefficients["a"]]

        return (
            peakshape,
            resolution_function,
            peakshape_numpy,
            resolution_function_partial,
        )
