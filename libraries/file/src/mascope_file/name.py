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


#: Classes already settled, keyed by sample file name. A file's class is
#: decided once, by the reader that converted it, and never changes, so the
#: answer is worth keeping: the lookup is on the path of every spectrum, peak
#: and match read, several times per sample. Bounded because a converter
#: process sees an unbounded number of files over its life; the whole map is
#: dropped rather than aged, since a miss costs a couple of stat calls.
_INSTRUMENT_TYPE: dict[str, str] = {}
_INSTRUMENT_TYPE_LIMIT = 4096


def _remember_instrument_type(filename: str, instrument_type: str) -> str:
    """Keep a settled class, and say it back.

    :param filename: Sample file name
    :type filename: str
    :param instrument_type: The class settled for it
    :type instrument_type: str
    :return: The class, unchanged
    :rtype: str
    """
    if len(_INSTRUMENT_TYPE) >= _INSTRUMENT_TYPE_LIMIT:
        _INSTRUMENT_TYPE.clear()
    _INSTRUMENT_TYPE[filename] = instrument_type
    return instrument_type


def forget_instrument_type(filename: str) -> None:
    """Drop a file's remembered class, for a file that is going away.

    A stored file name is unique while the file exists and is free again once
    it is deleted, so a remembered class has to go with the file - otherwise a
    later file taking the same name would be read as the old one's class.

    :param filename: Sample file name
    :type filename: str
    """
    _INSTRUMENT_TYPE.pop(filename, None)


def _data_file_extension(filename: str) -> str | None:
    """The extension of the source data file kept in the sample's directory.

    Answers None rather than raising for anything that is not a stored sample
    whose source file is on disk: a name with no acquisition time in it has no
    filestore path at all, and callers pass such names (an instrument name on
    its own, a sample that was never converted here) expecting a fallback, not
    an error.

    :param filename: Sample file name
    :type filename: str
    :return: ".raw" or ".h5", or None when the sample keeps neither (its
        source file was never kept, or has been removed, leaving the zarr)
    :rtype: str | None
    """
    try:
        sample_data_path = parse_path_from_item_filename(filename)
    except (OSError, ValueError):
        return None
    for extension in INSTRUMENT_TYPE_BY_EXTENSION:
        if os.path.isfile(os.path.join(sample_data_path, f"data{extension}")):
            return extension
    return None


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


def get_instrument_type(filename: str) -> str:
    """Get instrument type (one of {"orbi", "tof"}) from sample file

    The data file decides: a ``.raw`` is an Orbitrap acquisition and a ``.h5``
    a TOF one, which is the same rule the reader applies when it converts the
    file. A sample that keeps no source file falls back to the class the
    reader recorded in its props, and a file converted before that field
    existed to the substrings in its instrument name.

    The name comes last on purpose. An instrument may be called anything, and
    plenty of ordinary names carry one of the substrings - "Rapid" and
    "Capillary" both contain "api" - so a name is the weakest evidence there
    is, not the strongest.

    :param filename: Sample file name
    :type filename: str
    :raises ValueError: Failed to detect instrument type
    :return: Instrument type, one of {"orbi", "tof"}
    :rtype: str
    """
    if (cached := _INSTRUMENT_TYPE.get(filename)) is not None:
        return cached

    extension = _data_file_extension(filename)
    if extension is not None:
        return _remember_instrument_type(
            filename, INSTRUMENT_TYPE_BY_EXTENSION[extension]
        )

    instrument_type = _instrument_type_from_props(filename)
    if instrument_type is None:
        instrument_type = resolve_instrument_type(
            get_instrument_name(filename), throw=False
        )
    if instrument_type is None:
        raise ValueError(
            f"Failed to get instrument type for instrument "
            f"{get_instrument_name(filename)}: the sample keeps no data file, "
            "its props record no class, and the name does not say"
        )
    return _remember_instrument_type(filename, instrument_type)


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
