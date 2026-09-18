"""
Tests for `mascope file scans`.

The command reads a raw file with Mascope's own reader. A checkout runs it in
process. The standalone operator CLI ships without it, so the file goes into
the prod backend container, is read there with ``python -m
mascope_thermo.streams``, and is removed again. The reader is stubbed here in
both places: what is pinned is how the command gets a report and what it
shows. The reader itself is tested in ``libraries/thermo/tests``.
"""

import json
import subprocess

import pytest

from mascope_cli.cmd import file as file_cmd
from mascope_cli.cmd.prod.db import scripts as prod_scripts
from mascope_cli.main import app


CONTAINER_PYTHON = "/opt/uv/tools/mascope/bin/python"

REPORT = {
    "file": "sample.raw",
    "model": "Orbitrap Exploris 120",
    "method_file": "C:\\Xcalibur\\methods\\ambient_neg.meth",
    "scans": 6,
    "streams": [
        {
            "key": "FTMS - p NSI Full ms [40.0000-160.0000] R=120000",
            "signature": {"polarity": "-", "ms_order": 1},
            "scans": 3,
            "blocks": 3,
            "t_first": 0.5,
            "t_last": 5.5,
            "filters": 1,
            "acquisition_params": {},
            "top_peaks": [[61.98839, 95.7], [124.98446, 20.1]],
        },
        {
            "key": "FTMS - p NSI Full ms [128.0000-600.0000] R=120000",
            "signature": {"polarity": "-", "ms_order": 1},
            "scans": 3,
            "blocks": 3,
            "t_first": 1.5,
            "t_last": 6.5,
            "filters": 1,
            "acquisition_params": {},
            "top_peaks": [[188.93, 50.0]],
        },
    ],
}


@pytest.fixture
def raw_file(tmp_path):
    path = tmp_path / "sample.raw"
    path.write_bytes(b"not really a raw file")
    return path


class FakeDocker:
    """
    Stand in for ``subprocess.run`` against the prod backend container.

    Answers the interpreter probe, the copy in, the reader run and the
    removal, and records every command.
    """

    def __init__(self, *, python=CONTAINER_PYTHON, stdout=None, read_exit=0):
        self.python = python
        self.stdout = json.dumps(REPORT) if stdout is None else stdout
        self.read_exit = read_exit
        self.calls: list[list[str]] = []

    def __call__(self, cmd, capture_output=False, text=False, check=False):  # noqa: ARG002
        self.calls.append(list(cmd))
        if cmd[:2] == ["docker", "cp"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] != ["docker", "exec"]:
            raise AssertionError(f"unexpected command: {cmd}")
        if "sh" in cmd:
            if self.python is None:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, self.python + "\n", "")
        if "-m" in cmd:
            return subprocess.CompletedProcess(
                cmd, self.read_exit, self.stdout, "Traceback: reader exploded"
            )
        if "rm" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected command: {cmd}")

    def commands(self, verb: str) -> list[list[str]]:
        return [cmd for cmd in self.calls if verb in cmd]


@pytest.fixture
def operator_install(monkeypatch):
    """No reader on this host, and a FakeDocker for the backend container."""
    docker = FakeDocker()
    monkeypatch.setattr(file_cmd, "_reader_available", lambda: False)
    monkeypatch.setattr(file_cmd.subprocess, "run", docker)
    monkeypatch.setattr(prod_scripts.subprocess, "run", docker)
    return docker


def test_a_checkout_reads_the_file_in_process(cli_runner, raw_file, monkeypatch):
    seen = {}

    def _report(path, top):
        seen.update(path=path, top=top)
        return REPORT

    monkeypatch.setattr(file_cmd, "_reader_available", lambda: True)
    monkeypatch.setattr(file_cmd, "_report_in_process", _report)

    result = cli_runner.invoke(app, ["file", "scans", str(raw_file), "--top", "3"])

    assert result.exit_code == 0, result.output
    assert seen == {"path": raw_file, "top": 3}
    assert "FTMS - p NSI Full ms [40.0000-160.0000] R=120000" in result.output
    assert "61.9884" in result.output
    assert "ambient_neg.meth" in result.output
    assert "Polarity - has 2 MS1 streams" in result.output


def test_an_operator_install_reads_the_file_in_the_backend_container(
    cli_runner, raw_file, operator_install
):
    result = cli_runner.invoke(app, ["file", "scans", str(raw_file), "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == REPORT

    (copy,) = operator_install.commands("cp")
    target = copy[-1].split(":", 1)[1]
    assert copy[2] == str(raw_file)
    assert target.startswith("/tmp/mascope-file-scans-") and target.endswith(".raw")

    (read,) = operator_install.commands("-m")
    assert read[3:7] == [CONTAINER_PYTHON, "-m", "mascope_thermo.streams", target]
    assert read[-2:] == ["--top", "5"]

    (remove,) = operator_install.commands("rm")
    assert remove[-1] == target


def test_the_copy_is_removed_even_when_the_reader_fails(
    cli_runner, raw_file, operator_install
):
    operator_install.read_exit = 1

    result = cli_runner.invoke(app, ["file", "scans", str(raw_file)])

    assert result.exit_code == 1
    assert len(operator_install.commands("rm")) == 1


def test_a_stopped_stack_is_reported_before_anything_is_copied(
    cli_runner, raw_file, operator_install
):
    operator_install.python = None

    result = cli_runner.invoke(app, ["file", "scans", str(raw_file)])

    assert result.exit_code == 1
    assert operator_install.commands("cp") == []


def test_in_container_is_honoured_in_a_checkout(
    cli_runner, raw_file, operator_install, monkeypatch
):
    monkeypatch.setattr(file_cmd, "_reader_available", lambda: True)

    def _refuse(path, top):  # noqa: ARG001
        raise AssertionError("must not read in process")

    monkeypatch.setattr(file_cmd, "_report_in_process", _refuse)

    result = cli_runner.invoke(
        app, ["file", "scans", str(raw_file), "--in-container", "--json"]
    )

    assert result.exit_code == 0, result.output
    assert len(operator_install.commands("-m")) == 1


@pytest.mark.parametrize("name", ["sample.h5", "sample.txt"])
def test_only_raw_files_are_read(cli_runner, tmp_path, operator_install, name):
    path = tmp_path / name
    path.write_bytes(b"")

    result = cli_runner.invoke(app, ["file", "scans", str(path)])

    assert result.exit_code == 1
    assert operator_install.calls == []


def test_a_missing_file_is_refused(cli_runner, tmp_path, operator_install):
    result = cli_runner.invoke(app, ["file", "scans", str(tmp_path / "gone.raw")])

    assert result.exit_code == 1
    assert operator_install.calls == []


@pytest.mark.parametrize(
    "stdout",
    [
        json.dumps(REPORT),
        "INFO loaded the reader\n" + json.dumps(REPORT) + "\n\n",
        # A colourised log line leaves its reset code on the next line.
        "\x1b[2m INFO loaded\n\x1b[0m" + json.dumps(REPORT) + "\r\n",
    ],
)
def test_the_report_is_the_last_line_of_the_readers_output(stdout):
    assert file_cmd._parse_report(stdout) == REPORT


@pytest.mark.parametrize("stdout", ["", "\n", "INFO no report here\n"])
def test_output_without_a_report_is_an_error(stdout):
    with pytest.raises(ValueError):
        file_cmd._parse_report(stdout)
