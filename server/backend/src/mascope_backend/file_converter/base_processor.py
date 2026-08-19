# -*- coding: utf-8 -*-
"""Base file processor class for shared functionality between different file type processors."""

import os
import shutil
from abc import ABC, ABCMeta, abstractmethod
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import mascope_sdk
from mascope_file.io import write_props
from mascope_file.name import (
    get_instrument_name,
    parse_path_from_item_filename,
)
from mascope_signal.instrument_func.fit import InsufficientPeaksError
from mascope_signal.peak import compute_peaks, write_empty_peak_timeseries

from .api import (
    check_sample_file_db_record,
    create_instrument_config_db_record,
    create_sample_file_db_record,
    delete_sample_file_by_filename,
)
from .errors import describe_exception
from .peak_guard import PeakDetectionGuard
from .runtime import runtime
from .schema import SampleFileProps


# Configure service name to use in request headers
mascope_sdk.SERVICE_NAME = "file-converter"


def with_file_context(prop_getter) -> callable:
    """Abstract file context manager decorator

    :param prop_getter: Property getter function
    :type prop_getter: callable
    :return: Wrapped property getter
    """

    def wrapper(self):
        # Use the class's context manager, passing the file path
        with self._file_context_manager(self.file_to_process) as file_handle:
            self.file_handle = file_handle
            prop = prop_getter(self)

        self.file_handle = None
        return prop

    return wrapper


class FileProcessorMeta(ABCMeta):
    """Metaclass that automatically creates abstract properties based on SampleFileProps fields."""

    def __new__(mcs, name, bases, namespace, **kwargs):
        # Only apply to BaseFileProcessor, not its subclasses
        if name == "BaseFileProcessor":
            schema_fields = SampleFileProps.model_fields

            if "__annotations__" not in namespace:
                namespace["__annotations__"] = {}

            # Create abstract properties for each schema field
            for field_name, field_info in schema_fields.items():
                if field_name in namespace:
                    continue

                # Add type to class annotations for type checker recognition
                field_type = field_info.annotation
                namespace["__annotations__"][field_name] = field_type

                def make_abstract_property(prop_type, description):
                    """Create abstract property with proper closure"""

                    def getter(self):
                        raise NotImplementedError

                    getter.__doc__ = description
                    getter.__annotations__ = {"return": prop_type}

                    return property(abstractmethod(getter))

                # Get description from field info
                description = getattr(
                    field_info, "description", f"Abstract property for {field_name}"
                )

                # Add the abstract property to the namespace
                namespace[field_name] = make_abstract_property(field_type, description)

        return super().__new__(mcs, name, bases, namespace, **kwargs)


class BaseFileProcessor(Thread, ABC, metaclass=FileProcessorMeta):
    """Base class for file processors with shared functionality.

    Abstract properties are automatically generated from SampleFileProps schema fields
    using the FileProcessorMeta metaclass. This ensures a single source of truth
    for property definitions.
    """

    def __init__(
        self,
        socket_client,
        file_queue=Queue(),
        shutdown_event=Event(),
        peak_guard: PeakDetectionGuard | None = None,
    ):
        Thread.__init__(self)
        runtime.logger.info(f"{self.__class__.__name__} initialized")

        self.socket_client = socket_client
        self.file_queue = file_queue
        self.shutdown_event = shutdown_event
        self.cancel_event = Event()
        self.active = Event()
        self.peak_guard = peak_guard

        self.file_to_process = None  # Path to the file to process
        self.file_handle = None  # Abstract file reference, managed by context manager

    # Additional abstract properties not in SampleFileProps
    @property
    @abstractmethod
    def file_extension(self) -> str:
        """Get the file extension for the specific file type.

        :return: File extension
        :rtype: str
        """
        raise NotImplementedError

    # Abstract methods - must be implemented by subclasses
    @staticmethod
    @abstractmethod
    def _file_context_manager(file_path: str):
        """Get the file context manager for the specific file type.

        :param file_path: Path to the file
        :type file_path: str
        :return: File context manager
        """
        raise NotImplementedError

    @abstractmethod
    def _process_instrument_config(
        self, sample_file_props: SampleFileProps
    ) -> tuple[any, any, any, any]:
        """Fit instrument functions."""
        raise NotImplementedError

    @property
    @abstractmethod
    def _is_blank_measurement(self) -> bool:
        """Determine if the file being processed a blank/zero measurement (no peaks)."""
        pass

    # Common methods - used by all subclasses
    def _check_orphan_sample_file_filestore(self, filename: str) -> bool:
        """Check if file's directory exists in filestore without corresponding
        database record."""
        try:
            # Check if filestore directory exists
            data_path = parse_path_from_item_filename(filename)
            if not os.path.exists(data_path):
                return False

            # Get file context and check database record
            file_context = self._get_file_context()
            return not check_sample_file_db_record(filename, file_context.access_token)

        except RuntimeError:
            raise
        except Exception:
            runtime.logger.exception(f"Error checking orphaned filestore {filename}")
            return False

    def _cleanup_successful_file(self, file_to_process: str, file_basename: str):
        """Handle successful file processing cleanup."""
        try:
            # Delete file from streams folder
            runtime.logger.info("Deleting file from the streams folder")
            os.remove(file_to_process)
            runtime.logger.info(f"Successfully deleted file: {file_to_process}")
        except FileNotFoundError:
            # File already deleted - this is not critical
            runtime.logger.info(
                f"File {file_to_process} was already deleted from streams folder"
            )
        except PermissionError as e:
            # File locked - this may indicate an issue with file handle cleanup
            # in _finalize(); the file remains in the streams folder and may
            # need manual cleanup.
            runtime.logger.warning(f"Could not delete file {file_to_process}: {e}")
        except Exception:
            # Other deletion errors - log but don't fail
            runtime.logger.exception(
                f"Unexpected error during file deletion {file_to_process}"
            )

        # Always clear context at the end, regardless of file deletion success
        self.socket_client.context_manager.clear_context(file_basename)

    def _compute_peaks(
        self, filename: str, instrument_functions: tuple[any, any, any]
    ) -> None:
        """Compute peaks for the processed file.

        Uses the peak detection guard to avoid running peak detection
        concurrently for the same file (per filename).
        """
        if self.peak_guard is not None:
            is_acquired, acquisition_failure_reason = self.peak_guard.acquire(filename)
            if not is_acquired:
                raise RuntimeError(acquisition_failure_reason)
        try:
            compute_peaks(filename, instrument_functions)
        finally:
            # Release the guard in case of any exception to avoid deadlocks,
            # but only if it was acquired successfully
            if self.peak_guard is not None:
                self.peak_guard.release(filename)

    def _copy_file_to_filestore(self, source_path: str, target_dir: str) -> None:
        """Copy raw file to filestore."""
        data_raw_path = os.path.join(target_dir, f"data{self.file_extension}")
        shutil.copy(source_path, data_raw_path)

    def _create_db_record(
        self,
        sample_file_props: SampleFileProps,
        instrument_function_id: str | None,
    ) -> None:
        """
        Create database record for the sample file.

        None instrument_function_id indicates blank measurement.

        :param sample_file_props: File properties
        :type sample_file_props: SampleFileProps
        :param instrument_function_id: FK to instrument config
        :type instrument_function_id: str | None
        :raises RuntimeError: If database record creation fails
        """
        try:
            file_context = self._get_file_context()
            create_sample_file_db_record(
                sample_file_props,
                instrument_function_id,
                access_token=file_context.access_token,
                device_id=getattr(file_context, "device_id", None),
            )

        except Exception as e:
            # No log here: the raised RuntimeError is logged with its
            # traceback by the processing loop's handler
            error_msg = f"Failed to create database record: {e}"
            # Delete filestore directory on failure
            filename = sample_file_props.filename
            data_path = parse_path_from_item_filename(filename)
            if os.path.exists(data_path):
                shutil.rmtree(data_path)
            raise RuntimeError(error_msg) from e

    def _create_filestore_directory(
        self, sample_file_props: SampleFileProps, source_file_path: str
    ) -> None:
        """Create filestore directory, write properties, and copy file."""
        filename = sample_file_props.filename
        data_path = parse_path_from_item_filename(filename)

        # Create sample file directory, will raise FileExistsError if directory exists
        os.makedirs(data_path)

        try:
            # Write properties to the sample file
            write_props(filename, sample_file_props.model_dump(by_alias=True))

            # Copy file to the sample file folder using subclass-specific method
            self._copy_file_to_filestore(source_file_path, data_path)

        except Exception:
            # Cleanup directory if file operations fail
            if os.path.exists(data_path):
                shutil.rmtree(data_path)
            raise

    def _create_instrument_config(
        self,
        sample_file_props: SampleFileProps,
    ) -> tuple[str, any, any]:
        """Create instrument config. Fit instrument functions (sub-class specific)
        and write to database

        :rtype: tuple[str, any, any]
        :return: Tuple of (instrument_function_id, peakshape_numpy, resolution_function_partial)
        """
        (
            peakshape,
            resolution_function,
            peakshape_numpy,
            resolution_function_partial,
        ) = self._process_instrument_config(sample_file_props)

        file_context = self._get_file_context()

        instrument_function_id = create_instrument_config_db_record(
            sample_file_props,
            peakshape,
            resolution_function,
            access_token=file_context.access_token,
        )
        return instrument_function_id, peakshape_numpy, resolution_function_partial

    def _emit_progress_notification(self, progress: int):
        """Emit file processing progress notification."""
        file_basename = os.path.basename(self.file_to_process)
        instrument = get_instrument_name(file_basename)

        self.socket_client.emit(
            "file_processing_progress",
            {
                "filename": file_basename,
                "instrument": instrument,
                "progress": progress,
            },
        )

    def _finalize(self):
        """Finalize processing - close file and reset state."""
        self.active.clear()
        self.cancel_event.clear()

    def _get_file_context(self):
        """Get file context for the current file being processed."""
        base_filename = os.path.basename(self.file_to_process)

        if not (
            file_context := self.socket_client.context_manager.get_context(
                base_filename
            )
        ):
            raise RuntimeError(
                f"File {base_filename} not registered in file converter service"
            )
        return file_context

    def _context_timezone(self) -> ZoneInfo | None:
        """The uploading machine's timezone, resolved from the file context.

        None when no socket client or context is registered, the agent
        reported no zone, or the reported name is not a known IANA zone
        (logged; the offset then falls back to the processor's own
        resolution order).
        """
        context_manager = getattr(self.socket_client, "context_manager", None)
        if context_manager is None:
            return None
        base_filename = os.path.basename(self.file_to_process)
        context = context_manager.get_context(base_filename)
        zone_name = getattr(context, "instrument_timezone", None) if context else None
        if not zone_name:
            return None
        try:
            return ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            runtime.logger.warning(
                f"Uploader reported unknown timezone '{zone_name}' for "
                f"{base_filename}; falling back for the UTC offset"
            )
            return None

    @property
    def acquisition_timezone(self) -> str | None:
        """IANA timezone of the uploading machine, when it reported a valid one."""
        zone = self._context_timezone()
        return zone.key if zone is not None else None

    def _get_sample_file_props(self) -> SampleFileProps:
        """Extract sample file properties from the opened file.

        Note: Properties are dynamically generated by FileProcessorMeta metaclass
        from SampleFileProps schema fields.
        """
        # Dynamic approach - build properties dict from schema fields
        props_data = {}
        schema_fields = SampleFileProps.model_fields

        for field_name in schema_fields.keys():
            # Get the property value using getattr to avoid linter issues
            value = getattr(self, field_name)
            props_data[field_name] = value

        return SampleFileProps(**props_data)

    def _handle_failed_file(self, file_path: str) -> None:
        """Handle failed file - moves file to failed_files folder if possible."""
        runtime.logger.info(
            f"File {file_path} was not processed, moving to the folder of failed files"
        )
        try:
            failed_folder = os.path.join(os.path.dirname(file_path), "failed_files")
            os.makedirs(failed_folder, exist_ok=True)
            # Use full path to enable overwrite if the file already exists
            failed_file = os.path.join(failed_folder, os.path.basename(file_path))
            shutil.move(file_path, failed_file)
            runtime.logger.info(f"Moved failed file to: {failed_file}")
        except PermissionError as e:
            # File is locked - this indicates the file is still being processed;
            # it remains in the streams folder and may need manual cleanup.
            runtime.logger.warning(f"Could not move locked file {file_path}: {e}")
        except Exception:
            runtime.logger.exception(
                f"Failed to move file {file_path} to the error folder"
            )

    def _process_as_blank(self, sample_file_props: SampleFileProps) -> None:
        """Ingest a file as a blank measurement.

        Skips peak detection, writes an empty peak timeseries, and creates a
        sample file DB record without an instrument config. Used both for files
        classified as blank and for low-signal files whose instrument functions
        cannot be fit.
        """
        write_empty_peak_timeseries(sample_file_props.filename)
        self._create_db_record(sample_file_props, instrument_function_id=None)
        self._emit_progress_notification(100)

    def _process_file(
        self, sample_file_props: SampleFileProps, file_path: str, retry_count: int = 0
    ) -> None:
        """Process file with orphaned directory handling."""
        filename = sample_file_props.filename

        try:
            self._emit_progress_notification(0)
            # Create filestore directory, write properties, and copy file
            self._create_filestore_directory(sample_file_props, file_path)

            self._emit_progress_notification(10)

            if self._is_blank_measurement:
                # Routine classification of a measurement, not a fault
                runtime.logger.info(
                    f"Blank measurement detected: {filename}, skipping peak detection"
                )
                self._process_as_blank(sample_file_props)
                return

            # Fit instrument functions and get the ID
            try:
                instrument_function_id, *instrument_functions = (
                    self._create_instrument_config(sample_file_props)
                )
            except InsufficientPeaksError:
                # Low-signal file just above the blank threshold: the spectrum has
                # too few quality peaks to fit instrument functions. Treat it as a
                # blank measurement instead of failing the whole file.
                runtime.logger.info(
                    f"Insufficient quality peaks to fit instrument functions for "
                    f"{filename}, treating as blank measurement"
                )
                self._process_as_blank(sample_file_props)
                return

            self._emit_progress_notification(25)

            # Compute peak data
            self._compute_peaks(filename, instrument_functions)

            self._emit_progress_notification(90)

            # Create sample file DB record with instrument config FK
            self._create_db_record(sample_file_props, instrument_function_id)

            self._emit_progress_notification(100)

        except FileExistsError as exc:
            # Check if filestore exists without database record (orphaned)
            if self._check_orphan_sample_file_filestore(filename):
                if retry_count >= 1:
                    runtime.logger.error(
                        f"Retry limit reached for orphaned filestore cleanup: {filename}"
                    )
                    raise exc
                runtime.logger.info(
                    f"Found orphaned filestore for {filename}, cleaning up and retrying..."
                )

                self._remove_orphaned_filestore(filename)

                # Retry after cleanup
                self._process_file(
                    sample_file_props, file_path, retry_count=retry_count + 1
                )
            else:
                # Routine user mistake (re-uploading an existing file); the
                # raised FileExistsError is what reports it upstream.
                runtime.logger.info(
                    f"File already exists in the filestore with valid database record: {filename}"
                )
                raise FileExistsError(
                    "File already exists, please delete the old file, rename the file you want to "
                    "upload or contact the administrator."
                ) from exc

    def _remove_orphaned_filestore(self, filename: str) -> None:
        """Remove orphaned filestore directory and any database record."""
        try:
            file_context = self._get_file_context()
            delete_sample_file_by_filename(filename, file_context.access_token)
        except Exception:
            # No log here: the re-raised exception is logged with its
            # traceback by the processing loop's handler
            raise

    def _strip_filepath(self, filepath: str) -> str:
        """Strip path and file extension"""
        return os.path.splitext(os.path.basename(filepath))[0]

    def run(self):
        """Main processing loop."""
        runtime.logger.info(f"Running {self.__class__.__name__} ({self.name})")

        # Main loop
        while not self.shutdown_event.is_set():
            try:
                file_basename = None
                instrument = None
                self.file_to_process = self.file_queue.get(timeout=0.1)
                file_basename = os.path.basename(self.file_to_process)
                instrument = get_instrument_name(file_basename)

                # Main processing block
                try:
                    # Set active flag
                    runtime.logger.info(
                        f"Processing file: {Path(self.file_to_process).name}"
                    )
                    self.active.set()

                    # Get sample file properties using subclass implementation
                    sample_file_props = self._get_sample_file_props()

                    # Process file (raises exceptions on failure)
                    self._process_file(sample_file_props, self.file_to_process)

                    runtime.logger.info(
                        f"Finished processing file: {Path(self.file_to_process).name}"
                    )

                    # CRITICAL: Finalize BEFORE cleanup to release file locks
                    self._finalize()

                    self.socket_client.emit(
                        "file_processing_success",
                        {
                            "filename": file_basename,
                            "instrument": instrument,
                        },
                    )

                    # Success: delete file and clear context
                    self._cleanup_successful_file(self.file_to_process, file_basename)

                except Exception as e:
                    error_msg = describe_exception(e)
                    if isinstance(e, FileExistsError):
                        # Routine duplicate upload: one warning, no traceback
                        runtime.logger.warning(
                            f"Failed to process file {Path(self.file_to_process).name}: {e}"
                        )
                    else:
                        runtime.logger.exception(
                            f"Failed to process file {Path(self.file_to_process).name}"
                        )

                    # CRITICAL: Finalize BEFORE error emission to ensure file is closed
                    self._finalize()

                    # Emit clean error message
                    self.socket_client.emit(
                        "file_processing_error",
                        {
                            "filename": file_basename,
                            "instrument": instrument,
                            "error": error_msg,
                        },
                    )

                    # Clear context after error emission
                    self.socket_client.context_manager.clear_context(file_basename)
                    self._handle_failed_file(self.file_to_process)

            except Empty:
                # No file to process, continue
                continue
            except Exception as e:
                # Catch any unexpected errors
                runtime.logger.exception(
                    f"Unexpected error in {self.__class__.__name__}"
                )
                if self.file_to_process is not None and file_basename is not None:
                    # Ensure finalize is called before emission
                    self._finalize()

                    self.socket_client.emit(
                        "file_processing_error",
                        {
                            "filename": file_basename,
                            "instrument": instrument or "unknown",
                            "error": describe_exception(e),
                        },
                    )

                    # Clear context after emission
                    self.socket_client.context_manager.clear_context(file_basename)

        # Out of main loop
        runtime.logger.info(f"Exiting {self.__class__.__name__} ({self.name})")
