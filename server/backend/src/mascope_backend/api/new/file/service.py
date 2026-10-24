import asyncio
import datetime
import os
import shutil
import tempfile

from sqlalchemy import select

from mascope_backend import vocabulary
from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.lib.exceptions.api_exceptions import (
    NotFoundException,
    raise_api_warning,
)
from mascope_backend.api.new.temp.storage import user_temp_path
from mascope_backend.db import SampleFile, SampleItem, async_session
from mascope_backend.runtime import runtime
from mascope_file.name import (
    filename_to_datafile_path,
    get_instrument_type,
    get_sample_file_type,
    parse_path_from_item_filename,
)
from mascope_thermo.backend import open_backend
from mascope_thermo.mzml import CvParam, InstrumentConfiguration, write_mzml
from mascope_thermo.thermo import NoScansFoundError


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    error_notification_rooms=["user_id"],
)
async def download_files(
    sample_file_ids: list[str],
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Downloads sample files by their unique IDs and prepares them for client download.

    Steps:
    1. Fetch all sample files from the database using the provided IDs
    2. For multiple files, create a temporary directory and copy files there
    3. For a single file, copy it directly to the temp directory
    4. Create a zip archive for multiple files
    5. Return information about found and not found files

    :param sample_file_ids: List of IDs of the sample files to download
    :type sample_file_ids: list[str]
    :param independent_transaction: Flag to indicate if the operation should be treated as an independent transaction
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Optional identifier for the processing task
    :type process_id: str | None, optional
    :param parent_id: Optional identifier of the parent task
    :type parent_id: str | None, optional
    :raises ApiException: If no downloadable files are found
    :return: A dictionary with information about found and not found files
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Fetch sample file by ID
        sample_files = (
            (
                await session.execute(
                    select(SampleFile).where(
                        SampleFile.sample_file_id.in_(sample_file_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if not sample_files:
            raise NotFoundException("No sample files found with the provided IDs")
    not_found = []
    found = []
    download = None
    if len(sample_files) > 1:
        # create a download folder using a datetime stamp, scoped to the user
        datetime_stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        dirname = f"sample_files_{datetime_stamp}"
        download_dir = user_temp_path(user_id, dirname)
        os.mkdir(download_dir)
        # copy available files to the folder
        for sample_file in sample_files:
            # derive sample file path
            file_path = parse_path_from_item_filename(sample_file.filename)
            instrument_type = get_instrument_type(sample_file.filename)
            extension = "raw" if instrument_type == "orbi" else "h5"
            from_path = os.path.join(file_path, f"data.{extension}")
            # copy the sample file to the download folder
            if os.path.exists(from_path):
                to_path = os.path.join(
                    download_dir, f"{sample_file.filename}.{extension}"
                )
                shutil.copy(from_path, to_path)
                found.append(sample_file.to_dict())
            else:
                not_found.append(sample_file.to_dict())
        # zip the folder and set the zip as the download path
        if len(found) > 0:
            download = f"{dirname}.zip"
            zipfile_path = user_temp_path(user_id, dirname)
            shutil.make_archive(zipfile_path, "zip", download_dir)
    else:
        # derive sample file path
        sample_file = sample_files[0]
        file_path = parse_path_from_item_filename(sample_file.filename)
        instrument_type = get_instrument_type(sample_file.filename)
        extension = "raw" if instrument_type == "orbi" else "h5"
        from_path = os.path.join(file_path, f"data.{extension}")
        # copy the sample file to the temp folder
        if os.path.exists(from_path):
            download = f"{sample_file.filename}.{extension}"
            to_path = user_temp_path(user_id, download)
            if os.path.exists(to_path):
                os.remove(to_path)
            shutil.copy(from_path, to_path)
            found.append(sample_file.to_dict())
        else:
            not_found.append(sample_file.to_dict())

    files_found = (
        f"retrieved {len(found)} file{'s' if len(found) > 1 else ''}" if found else None
    )
    files_not_found = (
        f"skipped {len(not_found)} file{'s' if len(not_found) > 1 else ''} which could not be found: {', '.join([f['filename'] for f in not_found])}"
        if not_found
        else None
    )
    message = "File download: " + " and ".join(
        [m for m in [files_found, files_not_found] if m]
    )

    if not_found:
        # raise_api_warning logs the message itself
        raise_api_warning(
            message,
            {
                "data": {
                    "download": download,
                    "found": [f["filename"] for f in found],
                    "not_found": [f["filename"] for f in not_found],
                }
            },
        )
    else:
        runtime.logger.info(message)
        return {
            "message": message,
            "data": {
                "found": found,
                "not_found": not_found,
            },
            "_notification_data": {
                "download": download,
            },
        }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    error_notification_rooms=["user_id"],
)
async def export_mzml(
    sample_file_ids: list[str],
    sample_item_ids: list[str],
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Writes sample files, and the time windows of samples, as mzML for download.

    A sample file is written whole, as ``<filename>.mzML``. A sample is its
    file's scans of the sample's polarity between its ``t0`` and ``t1``, the
    scans the sample reads, as ``<filename>.<sample id>.mzML``
    (:func:`mascope_thermo.mzml.write_mzml`). One result is offered as the file
    itself, more than one as a zip. Only a Thermo raw file can be written: a
    TOF file, or a file whose raw data is not in the filestore, is skipped, and
    so is a sample whose window holds no scan. The instrument a file names is
    written in the terms of :mod:`mascope_backend.vocabulary`, the batch
    spreadsheet's, not in the reader's own (:func:`_instrument_configuration`).

    :param sample_file_ids: The sample files to write whole.
    :param sample_item_ids: The samples to write the windows of.
    :param independent_transaction: Flag to indicate if the operation should be treated as an independent transaction
    :param user_id: Current user triggered operation (for user notifications)
    :param process_id: Optional identifier for the processing task
    :param parent_id: Optional identifier of the parent task
    :raises NotFoundException: If none of the ids is found
    :return: A dictionary with the names written and the ones skipped
    :rtype: dict
    """
    async with async_session() as session:
        sample_files = (
            (
                await session.execute(
                    select(SampleFile.filename).where(
                        SampleFile.sample_file_id.in_(sample_file_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        samples = (
            await session.execute(
                select(
                    SampleItem.sample_item_id,
                    SampleFile.filename,
                    SampleItem.polarity,
                    SampleItem.t0,
                    SampleItem.t1,
                )
                .join(
                    SampleFile, SampleFile.sample_file_id == SampleItem.sample_file_id
                )
                .where(SampleItem.sample_item_id.in_(sample_item_ids))
            )
        ).all()
    if not sample_files and not samples:
        raise NotFoundException("No sample files or samples found with the given IDs")

    # (name of the mzML, sample file, polarity, t0, t1)
    jobs = [
        (f"{filename}.mzML", filename, None, None, None) for filename in sample_files
    ]
    jobs += [
        (
            f"{sample.filename}.{sample.sample_item_id}.mzML",
            sample.filename,
            sample.polarity,
            sample.t0,
            sample.t1,
        )
        for sample in samples
    ]

    # One file is written where it is downloaded from; more are written into
    # a folder of their own, which is zipped and removed. The folder's name is
    # made unique, not only stamped: two exports started within one second
    # run side by side and would otherwise share it, and its zip.
    single = len(jobs) == 1
    out_dir = None
    if not single:
        datetime_stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        out_dir = tempfile.mkdtemp(
            prefix=f"mzml_{datetime_stamp}_",
            dir=os.path.dirname(user_temp_path(user_id, "mzml")),
        )
        dirname = os.path.basename(out_dir)

    found: list[str] = []
    not_found: list[str] = []
    download = None
    try:
        for name, filename, polarity, t0, t1 in jobs:
            if get_sample_file_type(filename) != "orbi_raw":
                not_found.append(name)
                continue
            datafile_path = filename_to_datafile_path(filename)
            try:
                instrument = await asyncio.to_thread(
                    _instrument_configuration, datafile_path
                )
                await asyncio.to_thread(
                    write_mzml,
                    datafile_path,
                    user_temp_path(user_id, name)
                    if single
                    else os.path.join(out_dir, name),
                    polarity=polarity,
                    t_min=t0,
                    t_max=t1,
                    instrument=instrument,
                )
            except NoScansFoundError:
                not_found.append(name)
                continue
            found.append(name)

        if found:
            if single:
                download = found[0]
            else:
                download = f"{dirname}.zip"
                await asyncio.to_thread(
                    shutil.make_archive,
                    user_temp_path(user_id, dirname),
                    "zip",
                    out_dir,
                )
    finally:
        if out_dir is not None:
            shutil.rmtree(out_dir, ignore_errors=True)

    files_found = (
        f"wrote {len(found)} file{'s' if len(found) > 1 else ''}" if found else None
    )
    files_not_found = (
        f"skipped {len(not_found)} which could not be written as mzML: {', '.join(not_found)}"
        if not_found
        else None
    )
    message = "mzML export: " + " and ".join(
        [m for m in [files_found, files_not_found] if m]
    )

    if not_found:
        # raise_api_warning logs the message itself
        raise_api_warning(
            message,
            {
                "data": {
                    "download": download,
                    "found": found,
                    "not_found": not_found,
                }
            },
        )
    runtime.logger.info(message)
    return {
        "message": message,
        "data": {"found": found, "not_found": not_found},
        "_notification_data": {"download": download},
    }


def _instrument_configuration(datafile_path: str) -> InstrumentConfiguration:
    """
    The instrument an Orbitrap raw file names, as its mzML writes it.

    The model is the vocabulary's term for the model the raw file names, as
    the batch spreadsheet's is, or the ontology's Thermo parent term for a
    model the vocabulary does not know; source, analyser and detector are the
    vocabulary's terms for an Orbitrap.

    :param datafile_path: The Orbitrap raw file.
    :return: Its instrument configuration.
    :rtype: InstrumentConfiguration
    """
    with open_backend(datafile_path) as backend:
        model = backend.instrument_details().get("Model")
    model_term = (
        vocabulary.instrument_model_term(model) or vocabulary.THERMO_INSTRUMENT_MODEL
    )

    def param(term: vocabulary.CvTerm | None) -> CvParam | None:
        return CvParam(term.accession, term.name) if term else None

    return InstrumentConfiguration(
        model=param(model_term),
        source=param(vocabulary.source_term("orbi")),
        analyser=param(vocabulary.analyser_term("orbi")),
        detector=param(vocabulary.detector_term("orbi")),
    )
