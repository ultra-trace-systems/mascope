"""Unit tests for the watcher wiring and failed_uploads exclusion in the agent.

Hermetic: the agent is built and not started, and the wait between two looks
at a file is patched out; no observer threads are started.
"""

from queue import Empty

import pytest

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
