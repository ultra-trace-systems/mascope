import os
import re

import datetime_glob

from mascope_file.runtime import runtime


# Instrument names are the first segment of a stored file name (before '_'):
# either read off the name the file arrived with, or put there by the server
# from the instrument the uploading agent reported. Only letters, digits and
# hyphens - no underscores, which are the separator.
_INSTRUMENT_RE = re.compile(r"^[a-zA-Z0-9\-]{1,64}$")

#: The instrument class each reader produces. A file's type is decided by the
#: reader that converted it, recorded in the file's props and on its database
#: row; the substrings in resolve_instrument_type are the older rule, kept for
#: names that carry them.
INSTRUMENT_TYPE_BY_EXTENSION = {".raw": "orbi", ".h5": "tof"}


FILENAME_DATETIME_PATTERNS = [
    "*%Y.%m.%d*%Hh%Mm%Ss*",
    "*%Y%m%d_%H%M_*",
    "*%Y%m%d%H%M%S*",
    "*%Y%m%d*%H%M%S*",
    "*%Y%m%d*%H%M*",
    "*%Y%m%d*",
]


def timestamp_from_filename(filename):
    for pattern in FILENAME_DATETIME_PATTERNS:
        matcher = datetime_glob.Matcher(pattern=pattern)
        dt = matcher.match(filename)
        if dt:
            # Parsed succesfully
            break
    if not dt:
        raise ValueError(f"Could not parse timestamp from filename: {filename}")
    return dt.as_datetime()


def parse_path_from_item_filename(item_filename):
    """Return path (relative to the filestore) of a sample file, based on its name

    Path is
        $filestore/<instrument>/yyyy.mm.dd/sample_name

    Parameters
    ----------
    sample_name : str
        Sample name (format: instrument_*%Y.%m.%d*%Hh%Mm%Ss*)
    """

    def parse_subdir_from_datetime(datetime):
        date_dir = "%.4d.%.2d.%.2d" % (datetime.year, datetime.month, datetime.day)
        return date_dir

    # Instrument name
    instrument = item_filename.split("_")[0]
    # Parse datetime and convert to date subdirectory name (yyyy.mm.dd)
    item_datetime = timestamp_from_filename(item_filename)
    date_dir = parse_subdir_from_datetime(item_datetime)
    # Join to sample path relative to the filestore
    return runtime.filestore(instrument, date_dir, item_filename)


def get_batch_cache_path(sample_batch_id):
    """Get path to the sample batch cache folder"""
    return os.path.join(runtime.filestore(), "sample_batches", sample_batch_id)


def filename_to_zarr_path(base_filename, variable):
    """Derive full path to a zarr dataset from sample filename and the desired variable

    :param base_filename: Sample file filename
    :type base_filename: str
    :param variable: Variable name inside the sample file
    :type variable: str
    :return: Full path
    :rtype: str
    """
    sample_data_path = parse_path_from_item_filename(base_filename)
    zarr_filename = variable + os.extsep + "zarr"
    return os.path.join(sample_data_path, zarr_filename)


def filename_to_datafile_path(base_filename):
    """Derive full path to a h5 or raw data file from sample filename

    :param base_filename: Sample file filename
    :type base_filename: str
    :return: Full path
    :rtype: str
    """
    # Get path to the sample file folder
    sample_data_path = parse_path_from_item_filename(base_filename)

    sample_file_type = get_sample_file_type(base_filename)

    # Get path to the datafile and verify if it exists
    match sample_file_type:
        case "tof_h5":
            return os.path.join(sample_data_path, "data.h5")
        case "orbi_raw":
            return os.path.join(sample_data_path, "data.raw")
        case "tof_zarr" | "orbi_zarr":
            raise FileNotFoundError(
                f"Sample file {sample_data_path} does not contain h5 or raw datafile"
            )


def validate_instrument_name(instrument: str) -> None:
    """Reject instrument names that don't match the expected pattern.

    Validates that the name contains only letters, digits, and hyphens (no
    underscores - those are used as the filename separator). The name no
    longer has to say which instrument class it is: the type is recorded by
    the reader that converts the file, so an instrument can be called what
    its operator calls it.

    :param instrument: Instrument name to validate
    :type instrument: str
    :raises ValueError: If the name is invalid
    """
    if not _INSTRUMENT_RE.match(instrument):
        raise ValueError(
            f"Invalid instrument name '{instrument}'. "
            "Must be 1-64 characters using only letters, digits, and hyphens."
        )


def get_instrument_name(filename: str) -> str:
    """Get instrument name from sample file

    Currently, the sample file name is assumed to begin with the instrument name,
    followed by an underscore.

    :param filename: Sample file name
    :type filename: str
    :return: Instrument name
    :rtype: str
    """
    instrument_name = filename.split("_")[0]
    return instrument_name


def resolve_instrument_type(instrument_name: str, throw: bool = True) -> str | None:
    """Get instrument type (one of {"orbi", "tof"}) from an instrument name

    :param instrument_name: instrument name
    :type instrument: str
    :raises ValueError: Failed to detect instrument type
    :return: Instrument type, one of {"orbi", "tof"} or None if
             not resolved and throw=False
    :rtype: str | None
    """
    name = instrument_name.lower()
    if "orbi" in name:
        instrument_type = "orbi"
    elif "tof" in name or "api" in name:
        instrument_type = "tof"
    else:
        if throw:
            raise ValueError(
                f"Failed to get instrument type for instrument {instrument_name}"
            )
        else:
            instrument_type = None
    return instrument_type


def _instrument_type_from_props(filename: str) -> str | None:
    """The instrument type the converter recorded in the file's props, if any.

    :param filename: Sample file name
    :type filename: str
    :return: "orbi" or "tof", or None when the file has no props or they
        predate the field
    :rtype: str | None
    """
    from mascope_file.io import read_props  # noqa: PLC0415  (io imports name)

    try:
        instrument_type = read_props(filename).get("instrument_type")
    except (OSError, ValueError):
        return None
    return instrument_type if instrument_type in ("orbi", "tof") else None


def get_instrument_type(filename: str) -> str | None:
    """Get instrument type (one of {"orbi", "tof"}) from sample file

    The name decides when it can (it carries "orbi" or "tof"); otherwise the
    type the reader recorded in the file's props at conversion. Files whose
    name does not say and whose props predate the field cannot exist: every
    file converted before the field was written carried a name that says.

    :param filename: Sample file name
    :type filename: str
    :raises ValueError: Failed to detect instrument type
    :return: Instrument type, one of {"orbi", "tof"}
    :rtype: str | None
    """
    instrument_name = get_instrument_name(filename)
    instrument_type = resolve_instrument_type(instrument_name, throw=False)
    if instrument_type is None:
        instrument_type = _instrument_type_from_props(filename)
    if instrument_type is None:
        raise ValueError(
            f"Failed to get instrument type for instrument {instrument_name}: "
            "the name does not say, and the file's props record none"
        )
    return instrument_type


def get_sample_file_type(filename: str) -> str:
    """Get sample file type based on the presence of a datafile
    in sample_data_path.
        *_h5 - h5 file is available
        *_raw - raw file is available
        *_zarr - no source data file.

    :param filename: Sample file name
    :type filename: str
    :return: Sample file type, one of [tof_h5, tof_zarr, orbi_raw, orbi_zarr]
    :rtype: str
    """
    sample_data_path = parse_path_from_item_filename(filename)
    instrument_type = get_instrument_type(filename)

    is_raw = os.path.isfile(os.path.join(sample_data_path, "data.raw"))
    is_h5 = os.path.isfile(os.path.join(sample_data_path, "data.h5"))

    match instrument_type:
        case "tof":
            return "tof_h5" if is_h5 else "tof_zarr"
        case "orbi":
            return "orbi_raw" if is_raw else "orbi_zarr"
    raise ValueError(f"Failed to determine sample file type for {filename}")
