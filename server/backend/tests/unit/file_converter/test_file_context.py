"""
Tests for the file-context registry (``file_converter/socket/session.py``).

The upload endpoint registers a context under the *uploaded* filename; the
converter later looks it up under names that may carry an extension and/or a
polarity suffix. Normalization is what makes those two meet - a miss here is
the "file is not registered" ingestion failure, so the equivalences are pinned
down explicitly.
"""

from mascope_backend.file_converter.socket.session import (
    FileContext,
    FileContextManager,
)


def make_context(filename: str) -> FileContext:
    return FileContext(
        filename=filename,
        user_id=1,
        username="file-agent",
        role_id=1,
        access_token="token",
    )


class TestFileContextManager:
    def test_lookup_ignores_extension(self):
        manager = FileContextManager()
        manager.register_file(make_context("Orbion_pos_sample.raw"))

        assert manager.get_context("Orbion_pos_sample") is not None
        assert manager.get_context("Orbion_pos_sample.raw") is not None

    def test_lookup_ignores_polarity_suffix(self):
        # The converter processes per-polarity items suffixed _+ / _-; both
        # must resolve to the context registered at upload time.
        manager = FileContextManager()
        manager.register_file(make_context("TOF1_sample.h5"))

        assert manager.get_context("TOF1_sample_+") is not None
        assert manager.get_context("TOF1_sample_-") is not None
        # Extension is stripped before polarity, so the combined form works too.
        assert manager.get_context("TOF1_sample_+.h5") is not None

    def test_unknown_file_returns_none(self):
        manager = FileContextManager()
        assert manager.get_context("never_registered.raw") is None

    def test_clear_context_removes_all_variants(self):
        manager = FileContextManager()
        manager.register_file(make_context("Orbion_pos_sample.raw"))

        manager.clear_context("Orbion_pos_sample_+")

        assert manager.get_context("Orbion_pos_sample.raw") is None

    def test_clear_context_tells_the_owner_the_normalized_name(self):
        # The socket client drops the file's rejected tokens with it (MAS-534),
        # under the name it keeps them by
        cleared = []
        manager = FileContextManager(on_clear=cleared.append)
        manager.register_file(make_context("Orbion_pos_sample.raw"))

        manager.clear_context("Orbion_pos_sample_+")

        assert cleared == ["Orbion_pos_sample"]

    def test_context_carries_uploader_identity(self):
        manager = FileContextManager()
        manager.register_file(make_context("Orbion_pos_sample.raw"))

        context = manager.get_context("Orbion_pos_sample")

        assert context.username == "file-agent"
        assert context.access_token == "token"
