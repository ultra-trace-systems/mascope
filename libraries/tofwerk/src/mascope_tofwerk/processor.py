# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone

import numpy as np
from scipy.signal import find_peaks
from scipy.stats import median_abs_deviation

import mascope_signal.compute as m_compute
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
from mascope_backend.file_converter.errors import EmptyAcquisitionError
from mascope_tofwerk.tofwerk import open_h5_file


def _recorded_scan_times(buf_times) -> np.ndarray:
    """
    Scan start times actually recorded, with the unwritten tail trimmed off.

    TofDaq pre-allocates ``TimingData/BufTimes`` as (writes x bufs) and fills
    it as the run proceeds, so an aborted acquisition leaves a tail of zeros -
    often whole rows of them. The recorded scans are therefore everything up
    to the last non-zero entry *anywhere* in the array, not just in the last
    row: a run that stopped mid-block still recorded every scan before it.

    Index 0 is deliberately kept. Buf times are relative to acquisition start,
    so the first scan legitimately reads 0.0 and is indistinguishable from an
    unwritten slot by value alone - which is why a file whose every timestamp
    is zero is reported as empty rather than as a scan at t=0.

    :param buf_times: The ``TimingData/BufTimes`` dataset.
    :return: 1-D array of recorded scan start times, at least two long.
    :rtype: numpy.ndarray
    :raises EmptyAcquisitionError: When fewer than two scans were recorded,
        so no interval and no length can be measured.
    """
    times = np.asarray(buf_times[:]).flatten()
    non_zero = np.where(times != 0)[0]
    if non_zero.size == 0:
        raise EmptyAcquisitionError(
            "The file contains no scans; the acquisition is empty or was aborted."
        )
    recorded = times[: non_zero[-1] + 1]
    if recorded.size < 2:
        # One scan gives no inter-scan spacing to average; computing it anyway
        # yields NaN (mean of an empty diff), which would otherwise be stored
        # as the sample's interval and length.
        raise EmptyAcquisitionError(
            "The file contains only one scan; the acquisition was aborted "
            "before a measurable time axis was recorded."
        )
    return recorded


# Threshold factor for determining blank measurements based on noise level
NOISE_THRESHOLD_FACTOR = 5
# Threshold for signal-to-noise ratio to classify a measurement as blank
BLANK_SNR_THRESHOLD = 10


class H5Processor(BaseFileProcessor):
    """Read and process TOF h5 files"""

    def __init__(self, socket_client, file_queue, shutdown_event, peak_guard=None):
        super().__init__(
            socket_client=socket_client,
            file_queue=file_queue,
            shutdown_event=shutdown_event,
            peak_guard=peak_guard,
        )

    @property
    def file_extension(self) -> str:
        """Get the file extension for h5 files

        :return: File extension
        :rtype: str
        """
        return ".h5"

    @property
    def filename(self) -> str:
        """Get the processed filename."""
        return self._strip_filepath(self.file_to_process)

    @property
    def acquisition_params(self) -> dict:
        """Acquisition parameters reported by the instrument.

        Not implemented for TofDaq h5 files yet, so reported as empty. The h5
        root attributes and the untouched ``Instrument Data`` group (voltages,
        pressures, temperatures) are the natural source when the Orbitrap
        capture has shown which parameters are worth modelling; there is no
        committed h5 fixture to develop it against today.

        :return: Empty dict
        :rtype: dict
        """
        return {}

    @property
    def _is_blank_measurement(self) -> bool:
        """Determine if the file being processed is a blank/zero measurement

        This is determined by analyzing spikes/peaks in sum signal and comparing the
        maximum peak height to the noise level (signal to noise ratio).
        If the maximum peak to noise ratio is below a defined threshold,
        the measurement is classified as blank.
        """
        # Get the sum signal and find potential peaks
        sum_signal = m_compute.get_sum_signal(self.filename).values
        peak_indices, _ = find_peaks(sum_signal)
        peak_heights = sum_signal[peak_indices]

        # Compute noise level
        noise_mad = median_abs_deviation(peak_heights, scale="normal")
        if noise_mad == 0:
            # Only one peak, or the signal is saturated, or it's truly empty
            return True
        noise_std = 1.4826 * noise_mad
        noise_threshold = noise_std * NOISE_THRESHOLD_FACTOR

        max_signal_to_noise = np.max(peak_heights) / noise_threshold

        return max_signal_to_noise < BLANK_SNR_THRESHOLD

    @property
    @with_file_context
    def interval(self) -> float:
        """Mean measurement interval in seconds, i.e. length of one spectrum in the sample

        :return: Measurement interval [s]
        :rtype: float
        """
        recorded = _recorded_scan_times(self.file_handle["TimingData"]["BufTimes"])

        # Calculate the mean difference between consecutive datapoints
        differences = np.diff(recorded)
        return float(np.mean(differences))  # [s]

    @property
    @with_file_context
    def length(self) -> float:
        """Length of the sample file in seconds

        :return: Sample length [s]
        :rtype: float
        """
        # One read of BufTimes serves both terms below. Deriving the interval
        # here rather than through `self.interval` also avoids re-entering
        # `with_file_context`, which would open the h5 file a second time.
        recorded = _recorded_scan_times(self.file_handle["TimingData"]["BufTimes"])

        # Total length of the sample file is the difference between
        # starts of the first and the last scan + mean interval between scans
        interval = float(np.mean(np.diff(recorded)))
        return float(recorded[-1] - recorded[0]) + interval  # [s]

    @property
    @with_file_context
    def method_file(self) -> str:
        """TofDaq Recorder configuration file name used in the acquisition.

        :return: Configuration file name
        :rtype: str
        """
        method_file = self.file_handle.attrs["Configuration File"]
        return (
            method_file.decode() if isinstance(method_file, bytes) else str(method_file)
        ).strip()

    @property
    @with_file_context
    def mz_calibration(self) -> dict:
        """Mass calibration properties

        :return: Mass calibration properties
        :rtype: dict
        """
        # Get FullSpectra attributes reference
        attrs = self.file_handle["FullSpectra"].attrs
        # Number of mass calibration parameters
        try:
            num_params = attrs["MassCalibration nbrParameters"][0]
            # Get mass calibration parameters
            mass_calib_params = [
                float(attrs[f"MassCalibration p{i + 1}"][0]) for i in range(num_params)
            ]
            mass_calib_mode = int(attrs["MassCalibMode"][0])
        except IndexError:
            num_params = attrs["MassCalibration nbrParameters"]
            # Get mass calibration parameters
            mass_calib_params = [
                float(attrs[f"MassCalibration p{i + 1}"]) for i in range(num_params)
            ]
            mass_calib_mode = int(attrs["MassCalibMode"])

        return {
            "mode": mass_calib_mode,
            "par": mass_calib_params,
        }

    @property
    @with_file_context
    def range(self) -> list:
        """m/z range of the sample file

        :return: m/z range [first, last]
        :rtype: list
        """
        mass_axis = self.file_handle["FullSpectra"]["MassAxis"]
        return [float(mass_axis[0]), float(mass_axis[-1])]

    @property
    @with_file_context
    def polarity(self) -> str:
        """Polarity option in the sample file.

        :return: '-' for negative, '+' for positive
        :rtype: str
        """
        ion_mode = self.file_handle.attrs["IonMode"]
        ion_mode_str = (
            ion_mode.decode() if isinstance(ion_mode, bytes) else str(ion_mode)
        )
        ion_mode_str = ion_mode_str.strip().lower()
        if ion_mode_str == "negative":
            return "-"
        elif ion_mode_str == "positive":
            return "+"

    @property
    @with_file_context
    def sample_interval(self) -> float:
        """Sample interval in nanoseconds

        :return: Sample interval [ns]
        :rtype: float
        """
        try:
            return float(
                self.file_handle["FullSpectra"].attrs["SampleInterval"][0] * 1e9
            )  # [s]->[ns]
        except IndexError:
            return float(self.file_handle["FullSpectra"].attrs["SampleInterval"] * 1e9)

    @property
    @with_file_context
    def single_ion_signal(self) -> float:
        """Single ion signal [mV*ns/ion]

        :return: Single ion signal [mV*ns/ion]
        :rtype: float
        """
        try:
            sis = float(self.file_handle["FullSpectra"].attrs["Single Ion Signal"][0])
        except IndexError:
            sis = float(self.file_handle["FullSpectra"].attrs["Single Ion Signal"])
        return sis

    @property
    @with_file_context
    def timestamp(self) -> str:
        """Timestamp in isoformat, local timezone

        :return: Timestamp
        :rtype: str
        """
        try:
            filetime = float(
                self.file_handle["TimingData"].attrs["AcquisitionTimeZero"][0]
            )
        except IndexError:
            filetime = float(
                self.file_handle["TimingData"].attrs["AcquisitionTimeZero"]
            )
        # Windows FILETIME ticks: 100-nanosecond intervals since 1601-01-01
        epoch = datetime(1601, 1, 1)
        python_datetime = epoch + timedelta(
            microseconds=filetime // 10, seconds=self.utc_offset
        )
        # Omit microseconds
        python_datetime = python_datetime.replace(microsecond=0)
        return python_datetime.isoformat()

    def _acquisition_instant_utc(self) -> datetime:
        """Acquisition start as an aware UTC instant.

        Requires an open file handle (call under ``with_file_context``).

        :return: Acquisition start in UTC
        :rtype: datetime
        """
        try:
            filetime = float(
                self.file_handle["TimingData"].attrs["AcquisitionTimeZero"][0]
            )
        except IndexError:
            filetime = float(
                self.file_handle["TimingData"].attrs["AcquisitionTimeZero"]
            )
        # Windows FILETIME ticks: 100-nanosecond intervals since 1601-01-01 UTC
        epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
        return epoch + timedelta(microseconds=filetime // 10)

    def _resolve_utc_offset(self) -> tuple[float, str]:
        """Resolve the UTC offset, preferring what the file itself declares.

        Order: the LocalTimeOffsetToUTC attribute the acquisition software
        wrote ("file"); else the zone the uploading machine reported,
        evaluated at the acquisition instant ("agent"); else the converter
        host's zone at that instant ("guess") - the legacy fallback, wrong
        whenever the instrument PC and the converter host disagree on
        timezone or DST. Requires an open file handle.

        :return: UTC offset [s] and its source ("file", "agent" or "guess")
        :rtype: tuple[float, str]
        """
        try:
            try:
                hours = float(
                    self.file_handle["TimingData"].attrs["LocalTimeOffsetToUTC"][0]
                )
            except IndexError:
                hours = float(
                    self.file_handle["TimingData"].attrs["LocalTimeOffsetToUTC"]
                )
            return hours * 3600.0, "file"
        except KeyError:
            pass

        acquired_utc = self._acquisition_instant_utc()
        zone = self._context_timezone()
        if zone is not None:
            offset = acquired_utc.astimezone(zone).utcoffset()
            return offset.total_seconds(), "agent"
        # astimezone() with no argument attaches the host's zone at that
        # instant; total_seconds() keeps west-of-UTC offsets negative.
        offset = acquired_utc.astimezone().utcoffset()
        return offset.total_seconds(), "guess"

    @property
    @with_file_context
    def utc_offset(self) -> float:
        """UTC offset in seconds applied to the local timestamp

        :return: UTC offset in seconds
        :rtype: float
        """
        return self._resolve_utc_offset()[0]

    @property
    @with_file_context
    def utc_offset_source(self) -> str:
        """What determined utc_offset: "file", "agent" or "guess"

        :return: Offset source
        :rtype: str
        """
        return self._resolve_utc_offset()[1]

    @staticmethod
    def _file_context_manager(file_path: str):
        """Context manager for h5 files

        :param file_path: Path to the h5 file
        :type file_path: str
        :return: Context manager for h5 file
        :rtype: context manager
        """
        return open_h5_file(file_path)

    def _process_instrument_config(
        self, sample_file_props: SampleFileProps
    ) -> tuple[any, any, any, any]:
        """Fit instrument functions"""
        dmz = 0.5
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
        resolution_function = [partial_coefficients["a"], partial_coefficients["b"]]

        return (
            peakshape,
            resolution_function,
            peakshape_numpy,
            resolution_function_partial,
        )
