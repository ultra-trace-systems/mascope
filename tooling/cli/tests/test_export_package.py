"""
Tests for `mascope export package`.

The package is built by the backend's own code (``python -m
mascope_backend.deposit``), which is tested with the backend. A source
checkout runs it on this machine against the dev stack; any other install
runs it in the prod backend container and copies the archive out. The builder and docker are stubbed here:
what is pinned is how the command starts the builder in each install, how it
reads the outcome from the builder's log, how the archive gets to where it
was asked for, and what the operator is told.
"""

import json
import os
import subprocess
import sys
import zipfile

import pytest

from mascope_cli.cmd import export as export_cmd
from mascope_cli.cmd.prod.db import scripts as prod_scripts
from mascope_cli.main import app
from mascope_cli.runtime import runtime


CONTAINER_PYTHON = "/opt/uv/tools/mascope/bin/python"
PACKAGES = "/app/.runtime/env/default/temp/packages"
INSIDE = f"{PACKAGES}/mascope-package_ds1_Air.zip"


def _run(command) -> str:
    """The run a container build was started as."""
    return command[command.index("--run") + 1]


def _inside(command) -> str:
    """Where the builder in the container writes the archive of ``command``."""
    return f"{PACKAGES}/{_run(command)}/mascope-package_ds1_Air.zip"


def _write_archive(path, **dataset) -> None:
    """A package archive with a manifest, as much as the summary reads."""
    manifest = {
        "dataset": {"license": "CC-BY-4.0", "authors": [{"name": "A"}], **dataset}
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mascope-package_ds1_Air/README.md", "# Air\n")
        zf.writestr("mascope-package_ds1_Air/manifest.json", json.dumps(manifest))


def _package(archive, **counts) -> dict:
    return {
        "archive": str(archive),
        "bytes": 1234567,
        "package": "mascope-package_ds1_Air",
        "files": 9,
        "raw_files": 3,
        "raw_files_included": 3,
        "raw_files_changed": 0,
        "batches": 2,
        "target_collections": 1,
        **counts,
    }


class FakeBuilder:
    """Stand in for :func:`export_cmd._stream`: records the command line and
    the environment, and makes the archive the builder would have. An
    outcome may be a function of the command line."""

    def __init__(self, *, status=0, outcome=None, make=None, interrupt=False):
        self.status = status
        self.outcome = outcome
        self.make = make
        self.interrupt = interrupt
        self.calls: list[tuple[list[str], dict | None]] = []

    def __call__(self, command, env=None):
        self.calls.append((list(command), env))
        if self.interrupt:
            raise KeyboardInterrupt
        if self.make is not None:
            self.make(command)
        if callable(self.outcome):
            return self.status, self.outcome(command)
        return self.status, self.outcome


class FakeDocker:
    """Stand in for ``subprocess.run`` against the prod backend container:
    the interpreter probe, the copy out and the removal."""

    def __init__(
        self,
        *,
        python=CONTAINER_PYTHON,
        copy_exit=0,
        pkill_exit=0,
        dataset=None,
        interrupt=False,
    ):
        self.python = python
        self.pkill_exit = pkill_exit
        self.copy_exit = copy_exit
        self.dataset = dataset or {}
        self.interrupt = interrupt
        self.calls: list[list[str]] = []

    def __call__(self, cmd, capture_output=False, text=False, check=False):  # noqa: ARG002
        self.calls.append(list(cmd))
        if cmd[:2] == ["docker", "cp"]:
            if self.interrupt:
                raise KeyboardInterrupt
            if self.copy_exit == 0:
                _write_archive(cmd[3], **self.dataset)
            return subprocess.CompletedProcess(cmd, self.copy_exit, "", "no space left")
        if cmd[:2] == ["docker", "exec"] and "sh" in cmd:
            if self.python is None:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, self.python + "\n", "")
        if cmd[:2] == ["docker", "exec"] and "pkill" in cmd:
            return subprocess.CompletedProcess(
                cmd, self.pkill_exit, "", "pkill: not found"
            )
        if cmd[:2] == ["docker", "exec"] and "rm" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected command: {cmd}")

    def commands(self, verb: str) -> list[list[str]]:
        return [cmd for cmd in self.calls if verb in cmd]


@pytest.fixture
def errors(monkeypatch):
    collected: list[str] = []
    monkeypatch.setattr(export_cmd.runtime.logger, "error", collected.append)
    return collected


def _refused(result) -> bool:
    """Whether the command stopped itself, rather than failing on an error."""
    return result.exit_code == 1 and isinstance(result.exception, SystemExit)


@pytest.fixture
def warnings(monkeypatch):
    collected: list[str] = []
    monkeypatch.setattr(export_cmd.runtime.logger, "warning", collected.append)
    return collected


@pytest.fixture
def checkout(monkeypatch, tmp_path):
    """A source checkout; its builder writes where it is asked to."""

    def make(command):
        output = command[command.index("--output") + 1]
        _write_archive(f"{output}/mascope-package_ds1_Air.zip")

    builder = FakeBuilder(make=make)
    builder.outcome = {"package": _package(tmp_path / "mascope-package_ds1_Air.zip")}
    monkeypatch.setattr(export_cmd, "source_checkout", lambda: tmp_path)
    monkeypatch.setattr(export_cmd, "_stream", builder)
    return builder


@pytest.fixture
def operator_install(monkeypatch):
    """An install that is not a source checkout: a server's, or the
    standalone CLI. The prod backend container builds the package."""
    builder = FakeBuilder(
        outcome=lambda command: {"package": _package(_inside(command))}
    )
    docker = FakeDocker()
    monkeypatch.setattr(export_cmd, "source_checkout", lambda: None)
    monkeypatch.setattr(export_cmd, "_stream", builder)
    monkeypatch.setattr(export_cmd.subprocess, "run", docker)
    monkeypatch.setattr(prod_scripts.subprocess, "run", docker)
    return builder, docker


def test_a_checkout_builds_the_package_here_from_the_dev_database(
    cli_runner, checkout, tmp_path, monkeypatch
):
    """The builder runs under this interpreter, on the active env, in dev
    mode, and writes straight to where the archive was asked for."""
    runtime.state.override("mode", "prod")
    monkeypatch.setenv("MASCOPE_ENV", "dev-env")
    result = cli_runner.invoke(
        app, ["export", "package", "ds1", "--output", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    ((command, env),) = checkout.calls
    assert command == [
        sys.executable,
        "-m",
        "mascope_backend.deposit",
        "ds1",
        "--output",
        str(tmp_path.resolve()),
    ]
    assert env["MASCOPE_ENV"] == "dev-env"
    assert runtime.state.mode == "dev"
    assert "mascope-package_ds1_Air.zip" in result.output
    assert (
        "3 of 3 raw files, 2 batch spreadsheets, 1 target collections" in result.output
    )


def test_the_global_env_option_picks_the_env_a_checkout_packages(
    cli_runner, checkout, tmp_path
):
    result = cli_runner.invoke(
        app, ["--env", "other", "export", "package", "ds1", "-o", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    ((_, env),) = checkout.calls
    assert env["MASCOPE_ENV"] == "other"


def test_the_operator_cli_builds_in_the_container_and_copies_the_archive_out(
    cli_runner, operator_install, tmp_path
):
    """Outside a checkout the prod stack holds the data. This holds where the
    backend's code is importable too, as it is here and in a server's
    install built from its clone: that install cannot reach the database."""
    builder, docker = operator_install
    result = cli_runner.invoke(app, ["export", "package", "ds1", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
    container = runtime.full_config.backend.get_backend_container_name("prod")
    ((command, _),) = builder.calls
    run = _run(command)
    assert command == [
        "docker",
        "exec",
        container,
        CONTAINER_PYTHON,
        "-m",
        "mascope_backend.deposit",
        "ds1",
        "--run",
        run,
    ]
    target = tmp_path.resolve() / "mascope-package_ds1_Air.zip"
    assert docker.commands("cp") == [
        ["docker", "cp", f"{container}:{_inside(command)}", str(target)]
    ]
    assert docker.commands("rm") == [
        ["docker", "exec", container, "rm", "-rf", f"{PACKAGES}/{run}"]
    ]
    assert docker.commands("pkill") == []
    assert target.is_file()
    assert str(target) in result.output


def test_two_runs_build_into_directories_of_their_own(
    cli_runner, operator_install, tmp_path
):
    """Two operators, or a retry while the first build still runs, must not
    copy out or remove each other's archive."""
    builder, docker = operator_install
    for _ in range(2):
        result = cli_runner.invoke(
            app, ["export", "package", "ds1", "-o", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
    first, second = (_run(command) for command, _ in builder.calls)
    assert first != second
    assert [cmd[-1] for cmd in docker.commands("rm")] == [
        f"{PACKAGES}/{first}",
        f"{PACKAGES}/{second}",
    ]


def test_an_interrupted_build_is_stopped_in_the_container(
    cli_runner, operator_install, tmp_path
):
    """``docker exec`` passes no signal on, so the builder would go on
    writing the archive: it is sent SIGINT, and removes its directory."""
    builder, docker = operator_install
    builder.interrupt = True
    result = cli_runner.invoke(app, ["export", "package", "ds1", "-o", str(tmp_path)])
    # The interrupt is not swallowed: the command ends as interrupted.
    assert result.exit_code == 130
    container = runtime.full_config.backend.get_backend_container_name("prod")
    ((command, _),) = builder.calls
    assert docker.commands("pkill") == [
        ["docker", "exec", container, "pkill", "-INT", "-f", _run(command)]
    ]
    assert docker.commands("cp") == []
    assert docker.commands("rm") == []


@pytest.mark.parametrize(("pkill_exit", "said"), [(0, False), (1, False), (127, True)])
def test_a_failed_stop_in_the_container_is_reported(
    cli_runner, operator_install, warnings, tmp_path, pkill_exit, said
):
    """pkill exits 1 when the builder had already ended; anything else but 0
    means it may still be writing the archive, which nothing else would show."""
    builder, docker = operator_install
    builder.interrupt = True
    docker.pkill_exit = pkill_exit
    result = cli_runner.invoke(app, ["export", "package", "ds1", "-o", str(tmp_path)])
    assert result.exit_code == 130
    failed = [w for w in warnings if "Could not stop the build" in w]
    assert bool(failed) is said
    if said:
        assert f"exit {pkill_exit}" in failed[0]
        assert "until the backend restarts" in failed[0]


def test_an_interrupted_copy_still_removes_the_archive_from_the_container(
    cli_runner, operator_install, tmp_path
):
    """The builder is done by then: there is nothing to stop."""
    builder, docker = operator_install
    docker.interrupt = True
    result = cli_runner.invoke(app, ["export", "package", "ds1", "-o", str(tmp_path)])
    # The interrupt is not swallowed: the command ends as interrupted.
    assert result.exit_code == 130
    container = runtime.full_config.backend.get_backend_container_name("prod")
    ((command, _),) = builder.calls
    assert docker.commands("rm") == [
        ["docker", "exec", container, "rm", "-rf", f"{PACKAGES}/{_run(command)}"]
    ]
    assert docker.commands("pkill") == []


def test_an_archive_outside_the_runs_directory_is_not_removed(
    cli_runner, operator_install, tmp_path
):
    """Only the directory this run named is removed with ``rm -rf``, whatever
    the builder reports."""
    builder, docker = operator_install
    builder.outcome = {"package": _package(INSIDE)}
    result = cli_runner.invoke(app, ["export", "package", "ds1", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert docker.commands("cp") != []
    assert docker.commands("rm") == []


def test_a_path_with_brackets_is_shown_as_it_is(cli_runner, operator_install, tmp_path):
    """Rich would take ``[deposits]`` for markup and drop it."""
    directory = tmp_path / "[deposits]"
    directory.mkdir()
    result = cli_runner.invoke(app, ["export", "package", "ds1", "-o", str(directory)])
    assert result.exit_code == 0, result.output
    assert f"{directory.resolve()}/mascope-package_ds1_Air.zip" in result.output


def test_an_output_path_names_the_archive(cli_runner, operator_install, tmp_path):
    _, docker = operator_install
    target = tmp_path / "deposit.zip"
    result = cli_runner.invoke(app, ["export", "package", "ds1", "-o", str(target)])
    assert result.exit_code == 0, result.output
    assert docker.commands("cp")[0][-1] == str(target.resolve())


def test_in_container_is_asked_for_from_a_checkout(
    cli_runner, checkout, monkeypatch, tmp_path
):
    builder = FakeBuilder(outcome={"package": _package(INSIDE)})
    docker = FakeDocker()
    monkeypatch.setattr(export_cmd, "_stream", builder)
    monkeypatch.setattr(export_cmd.subprocess, "run", docker)
    monkeypatch.setattr(prod_scripts.subprocess, "run", docker)
    result = cli_runner.invoke(
        app, ["export", "package", "ds1", "-o", str(tmp_path), "--in-container"]
    )
    assert result.exit_code == 0, result.output
    assert checkout.calls == []
    assert builder.calls[0][0][:2] == ["docker", "exec"]


def test_a_stopped_stack_is_reported(cli_runner, operator_install, tmp_path, errors):
    builder, docker = operator_install
    docker.python = None
    result = cli_runner.invoke(app, ["export", "package", "ds1", "-o", str(tmp_path)])
    assert _refused(result)
    assert "the stack must be running" in errors[0]
    assert builder.calls == []


def test_a_failed_copy_still_removes_the_archive_from_the_container(
    cli_runner, operator_install, tmp_path, errors
):
    _, docker = operator_install
    docker.copy_exit = 1
    result = cli_runner.invoke(app, ["export", "package", "ds1", "-o", str(tmp_path)])
    assert _refused(result)
    assert errors == [
        "Could not copy the package out of "
        f"'{runtime.full_config.backend.get_backend_container_name('prod')}': "
        "no space left"
    ]
    assert len(docker.commands("rm")) == 1


@pytest.mark.parametrize(
    ("status", "outcome", "said"),
    [
        (1, {"error": "There is no dataset 'ds1'"}, "There is no dataset 'ds1'"),
        (1, None, "Building the package failed (exit 1)"),
        (0, None, "Building the package failed (exit 0)"),
        (1, {"package": _package(INSIDE)}, "Building the package failed (exit 1)"),
    ],
)
def test_a_builder_that_fails_or_reports_nothing_fails_the_command(
    cli_runner, operator_install, tmp_path, errors, status, outcome, said
):
    builder, docker = operator_install
    builder.status, builder.outcome = status, outcome
    result = cli_runner.invoke(app, ["export", "package", "ds1", "-o", str(tmp_path)])
    assert _refused(result)
    assert errors[0].startswith(said)
    assert docker.commands("cp") == []


@pytest.mark.parametrize("suffix", [os.sep, f"{os.sep}.", f"{os.sep}x{os.sep}.."])
def test_an_output_ending_in_a_separator_is_a_directory_made_if_missing(
    cli_runner, operator_install, tmp_path, suffix
):
    """``Path`` drops the trailing separator: read as one, ``-o deposits/``
    would name the archive ``deposits``."""
    _, docker = operator_install
    directory = tmp_path / "deposits"
    result = cli_runner.invoke(
        app, ["export", "package", "ds1", "-o", f"{directory}{suffix}"]
    )
    assert result.exit_code == 0, result.output
    target = directory.resolve() / "mascope-package_ds1_Air.zip"
    assert docker.commands("cp")[0][-1] == str(target)
    assert target.is_file()


def test_an_existing_directory_ending_in_a_separator_is_written_into(
    cli_runner, operator_install, tmp_path
):
    """The help's own example, ``-o ~/deposits/``."""
    _, docker = operator_install
    directory = tmp_path / "deposits"
    directory.mkdir()
    result = cli_runner.invoke(
        app, ["export", "package", "ds1", "-o", f"{directory}{os.sep}"]
    )
    assert result.exit_code == 0, result.output
    target = directory.resolve() / "mascope-package_ds1_Air.zip"
    assert docker.commands("cp")[0][-1] == str(target)
    assert target.is_file()


def test_a_checkout_builds_into_the_directory_made_for_it(
    cli_runner, checkout, tmp_path
):
    directory = tmp_path / "deposits"
    checkout.outcome = {"package": _package(directory / "mascope-package_ds1_Air.zip")}
    result = cli_runner.invoke(
        app, ["export", "package", "ds1", "-o", f"{directory}{os.sep}"]
    )
    assert result.exit_code == 0, result.output
    ((command, _),) = checkout.calls
    assert command[-2:] == ["--output", str(directory.resolve())]
    assert (directory / "mascope-package_ds1_Air.zip").is_file()


@pytest.mark.parametrize("name", ["p.zip", f"deposits{os.sep}"])
def test_an_output_in_no_directory_is_refused(
    cli_runner, checkout, tmp_path, errors, name
):
    """Only the archive, or the directory for it, is made: a mistyped path
    above it is not."""
    missing = tmp_path / "no"
    result = cli_runner.invoke(
        app, ["export", "package", "ds1", "-o", f"{missing}{os.sep}{name}"]
    )
    assert _refused(result)
    assert errors == [f"No such directory: {missing.resolve()}"]
    assert checkout.calls == []
    assert not missing.exists()


def test_an_output_directory_that_is_a_file_is_refused(
    cli_runner, checkout, tmp_path, errors
):
    archive = tmp_path / "deposit.zip"
    archive.write_bytes(b"")
    result = cli_runner.invoke(
        app, ["export", "package", "ds1", "-o", f"{archive}{os.sep}"]
    )
    assert _refused(result)
    assert errors == [f"Not a directory: {archive.resolve()}"]
    assert checkout.calls == []
    assert archive.read_bytes() == b""


def test_the_operator_is_told_what_to_check_before_depositing(
    cli_runner, operator_install, tmp_path, warnings
):
    builder, docker = operator_install
    builder.outcome = lambda command: {
        "package": _package(_inside(command), raw_files_included=2, raw_files_changed=1)
    }
    docker.dataset = {"license": None, "authors": None}
    result = cli_runner.invoke(app, ["export", "package", "ds1", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert warnings == [
        "1 sample file(s) have no raw file in the filestore, only the data "
        "converted from them: the package describes them without the file.",
        "1 raw file(s) are not the files uploaded: their SHA-256 differs from "
        "the one recorded at upload. The manifest gives both.",
        "The dataset records no licence, which a repository asks for. Add it "
        "to the dataset in Mascope and build the package again.",
        "The dataset records no authors, which a repository asks for. Add them "
        "to the dataset in Mascope and build the package again.",
    ]


def test_a_complete_package_warns_of_nothing(
    cli_runner, operator_install, tmp_path, warnings
):
    result = cli_runner.invoke(app, ["export", "package", "ds1", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert warnings == []


@pytest.mark.parametrize(
    ("line", "outcome"),
    [
        ('{"package": {"archive": "a.zip"}}\n', {"package": {"archive": "a.zip"}}),
        # A colourised log line leaves its reset code on the next line.
        ('\x1b[0m{"error": "no dataset"}\n', {"error": "no dataset"}),
        ("12:00:00 INFO Packaging dataset {name}\n", None),
        ('{"streams": []}\n', None),
        ("no braces at all\n", None),
    ],
)
def test_the_outcome_is_read_from_the_builders_log(line, outcome):
    assert export_cmd._outcome(line) == outcome


def test_the_builders_log_is_shown_as_it_goes_and_its_outcome_kept(capfd):
    script = (
        "print('Packaging dataset'); "
        'print(\'\\x1b[0m{"package": {"files": 4}}\'); '
        "print('Wrote it')"
    )
    status, outcome = export_cmd._stream([sys.executable, "-c", script])
    assert (status, outcome) == (0, {"package": {"files": 4}})
    shown = capfd.readouterr().out
    assert shown == "Packaging dataset\nWrote it\n"
