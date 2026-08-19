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
from mascope_thermo.backend import open_backend
from mascope_thermo.thermo import get_polarity_options


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

        :return: Sample length [s]
        :rtype: float
        """
        times = self.file_handle.scan_times(ms_type=None)  # all scans, seconds
        return float(times.max()) if times.size else 0.0  # [s]

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
        except Exception:
            _log.warning(
                "Could not read acquisition parameters for %s",
                self.file_to_process,
                exc_info=True,
            )
            return {}

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

        :return: UTC offset [s] and its source ("agent" or "guess")
        :rtype: tuple[int, str]
        """
        local_dt = datetime.fromisoformat(self.timestamp)
        zone = self._context_timezone()
        if zone is not None:
            offset = local_dt.replace(tzinfo=zone).utcoffset()
            return int(offset.total_seconds()), "agent"
        # A naive datetime's astimezone() attaches the host's zone at that
        # wall time; total_seconds() keeps west-of-UTC offsets negative.
        offset = local_dt.astimezone().utcoffset()
        return int(offset.total_seconds()), "guess"

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
