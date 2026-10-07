"""Unit tests for the watcher wiring and failed_uploads exclusion in the agent.

Hermetic: the agent is built and not started, and the wait between two looks
at a file is patched out; no observer threads are started.
"""

from queue import Empty

import pytest
from watchdog.events import FileMovedEvent

from mascope_file_agent import Agent, watcher


class StubLogger:
    def error(self, message):
        pass

    def warning(self, message):
        pass

    def info(self, message):
        pass

    def debug(self, message):
        pass


@pytest.fixture
def make_agent(monkeypatch, make_settings):
    monkeypatch.setattr(watcher, "POLL_INTERVAL", 0)

    def make(**settings):
        return Agent(make_settings(**settings), logger=StubLogger())

    return make


def test_recursive_flag_reaches_the_watcher(make_agent):
    agent = make_agent(recursive=True)
    assert agent.watcher.recursive is True
    # and the default stays non-recursive
    assert make_agent().watcher.recursive is False


def test_file_in_subfolder_is_queued(make_agent, tmp_path):
    agent = make_agent(recursive=True)
    sample = tmp_path / "day1" / "x.raw"
    sample.parent.mkdir()
    sample.write_text("data")

    agent.watcher.on_filesystem_object_created(str(sample))

    assert agent.uploader.jobs.get(timeout=5) == str(sample)


def test_file_in_failed_uploads_is_ignored(make_agent, tmp_path):
    # failed_uploads holds copies of files that already failed; picking
    # them up under recursive watching would loop them forever
    agent = make_agent(recursive=True)
    failed = tmp_path / "failed_uploads" / "x.raw"
    failed.parent.mkdir()
    failed.write_text("data")

    agent.watcher.on_filesystem_object_created(str(failed))

    with pytest.raises(Empty):
        agent.uploader.jobs.get(timeout=0.2)


def _moved(source, target):
    return FileMovedEvent(str(source), str(target))


def test_a_file_renamed_to_a_watched_name_is_taken(make_agent, tmp_path):
    """Acquisition software that writes x.tmp and renames it x.raw when done."""
    agent = make_agent()

    agent.watcher.handler.dispatch(_moved(tmp_path / "x.tmp", tmp_path / "x.raw"))

    assert agent.watcher._seen.get(timeout=5) == str(tmp_path / "x.raw")


def test_a_file_renamed_away_from_the_mask_is_left_alone(make_agent, tmp_path):
    """The observer reports a move when either name matches the mask.

    Taken under its new name, x.raw.bak would be refused for its extension and
    copied to failed_uploads, as though the agent had tried to upload it.
    """
    agent = make_agent()

    agent.watcher.handler.dispatch(_moved(tmp_path / "x.raw", tmp_path / "x.raw.bak"))

    with pytest.raises(Empty):
        agent.watcher._seen.get(timeout=0.2)


def test_a_file_gone_before_it_is_complete_is_a_line_not_a_traceback(
    make_agent, tmp_path
):
    """Renamed or deleted within the second the watcher gives it to settle.

    It used to surface as "Unexpected error handling filesystem event" with a
    traceback, for the most ordinary thing acquisition software does.
    """
    agent = make_agent()
    said = []
    agent.logger.warning = said.append
    agent.logger.exception = lambda message: pytest.fail(message)

    agent.watcher.on_filesystem_object_created(str(tmp_path / "x.raw"))

    assert len(said) == 1
    assert said[0].startswith(
        "x.raw: not uploaded, as it was gone before it was complete"
    )
    with pytest.raises(Empty):
        agent.uploader.jobs.get(timeout=0.2)
