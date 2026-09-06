from dataclasses import dataclass
from typing import Dict, Optional

from mascope_backend.runtime import runtime


@dataclass
class FileContext:
    """File processing context with user information"""

    filename: str
    user_id: int
    username: str
    role_id: int
    access_token: str
    # The paired device behind the upload (None for web uploads and for
    # agents predating the device registry), and the IANA timezone the
    # uploading machine reported (None when it sent none).
    device_id: Optional[int] = None
    instrument_timezone: Optional[str] = None
    # The file's name on the uploading machine, before the server filed it
    # under the instrument the agent reported (None when nothing renamed it).
    source_filename: Optional[str] = None


class FileContextManager:
    """Manages file contexts for file converter"""

    def __init__(self):
        self._file_contexts: Dict[str, FileContext] = {}

    def _remove_extension(self, filename: str) -> str:
        """Remove .h5 or .raw extension if present

        :param filename: Original filename
        :return: Filename without extension
        """
        if filename.endswith((".h5", ".raw")):
            return filename.rsplit(".", 1)[0]
        return filename

    def _remove_polarity(self, filename: str) -> str:
        """Remove polarity suffix (_+ or _-) if present

        :param filename: Filename potentially with polarity
        :return: Filename without polarity
        """
        if filename.endswith(("_+", "_-")):
            return filename.rsplit("_", 1)[0]
        return filename

    def _normalize_filename(self, filename: str) -> str:
        """Normalize filename by removing extension and polarity

        :param filename: Original filename
        :return: Normalized filename for context lookup
        """
        base_name = self._remove_extension(filename)
        normalized = self._remove_polarity(base_name)
        return normalized

    def register_file(self, context: FileContext):
        """Register file processing context with normalized filename

        :param context: FileContext to register
        """
        normalized_filename = self._normalize_filename(context.filename)
        self._file_contexts[normalized_filename] = context
        runtime.logger.debug(f"Registered context for filename: {normalized_filename}")

    def get_context(self, filename: str) -> Optional[FileContext]:
        """Get context for file using normalized filename

        :param filename: Filename to look up
        :return: FileContext if found, None otherwise
        """
        normalized_filename = self._normalize_filename(filename)
        file_context = self._file_contexts.get(normalized_filename)
        if file_context is None:
            runtime.logger.debug(
                f"No context found for filename: {normalized_filename}"
            )
            runtime.logger.trace(f"Registered contexts: {self._file_contexts}")
        return file_context

    def clear_context(self, filename: str):
        """Clear file context after processing

        :param filename: Filename to clear context for
        """
        normalized_filename = self._normalize_filename(filename)
        if normalized_filename in self._file_contexts:
            self._file_contexts.pop(normalized_filename)
            runtime.logger.debug(f"Cleared context for {normalized_filename}")
