"""
Tests for the `mascope env sync` filestore transfer.

Two behaviours are pinned here, both of which fail silently in production if
they regress:

- the rsync filter that narrows the transfer to an acquisition-date window.
  Only a real rsync proves the rules select the right files, so these tests
  assert the rule *strings* and their order — in particular that the trailing
  `- /filestore/*/*` survives (without it a "filtered" sync quietly transfers
  everything) and that nothing outside `/filestore/` is ever mentioned.
- the permission and ownership handling. rsync carries neither modes nor
  ownership across, so the mode bits must be pinned on the command line and a
  receiving uid that cannot be used by the app must be reported.

Nothing here touches the network or a real filesystem outside `tmp_path` and
the temp `MASCOPE_PATH` home: rsync, ssh and sudo all run against a fake
`subprocess.run` / `lib.run`.
"""

import datetime
import importlib
import os
import shlex
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from mascope_cli.cmd.env import _filter, _ownership, _sync


# The env package re-exports a `main` function that shadows the module of the
# same name, so import the module explicitly.
env_main = importlib.import_module("mascope_cli.cmd.env.main")

DAY = datetime.date


# --- Fixtures ---


@pytest.fixture
def posix_sync(monkeypatch):
    """
    Drive the POSIX branch of the rsync command construction.

    `cygwin_bin` raises on a Windows box without Cygwin and CI runs Linux, so
    the platform branch is always chosen explicitly rather than inherited.
    """
    monkeypatch.setattr(_sync, "_on_windows", lambda: False)
    monkeypatch.setattr(_sync, "cygwin_bin", lambda name: name)
    monkeypatch.setattr(_sync, "get_identity_args", lambda: [])


@pytest.fixture
def captured_rsync(monkeypatch):
    """
    Capture the rsync command string instead of running it.

    `on_run` hooks fire while rsync would be running, which is the only moment
    the temp filter file still exists.
    """
    captured = {"cmds": [], "on_run": []}

    def fake_run(command, **kwargs):
        captured["cmds"].append(command)
        for hook in captured["on_run"]:
            hook(command)
        return subprocess.CompletedProcess(command, captured.get("returncode", 0))

    monkeypatch.setattr(_sync.lib, "run", fake_run)
    monkeypatch.setattr(_sync, "check_after_sync", lambda *a, **kw: None)
    return captured


@pytest.fixture
def warnings(monkeypatch):
    """Collect the warnings `_ownership` emits."""
    collected = []
    monkeypatch.setattr(_ownership.runtime.logger, "warning", collected.append)
    return collected


@pytest.fixture
def source_env(mascope_home):
    """
    A source env with a two-instrument, three-date filestore on disk.

    Also carries the non-date `sample_batches/` cache and a `logs/` sibling,
    so tests can prove neither is mistaken for a date directory.
    """
    env_dir = mascope_home / ".runtime" / "env" / "sync-src"
    filestore = env_dir / "filestore"
    for instrument, days in (
        ("instrumentA", ("2026.03.01", "2026.03.05", "2026.04.02")),
        ("instrumentB", ("2026.03.05",)),
    ):
        for day in days:
            (filestore / instrument / day / f"{instrument}_{day}_sample").mkdir(
                parents=True, exist_ok=True
            )
    (filestore / "sample_batches" / "batch-1").mkdir(parents=True, exist_ok=True)
    (env_dir / "logs").mkdir(parents=True, exist_ok=True)
    yield env_dir
    shutil.rmtree(env_dir, ignore_errors=True)


# --- _filter: option and directory date parsing ---


def test_parse_option_date_accepts_iso():
    assert _filter.parse_option_date("2026-03-01", "--from") == DAY(2026, 3, 1)


@pytest.mark.parametrize("value", ["01/03/2026", "2026-13-01", "20260301", "yesterday"])
def test_parse_option_date_rejects_other_formats(value):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        _filter.parse_option_date(value, "--from")


def test_parse_option_date_names_the_offending_option():
    with pytest.raises(ValueError, match=r"--to"):
        _filter.parse_option_date("nope", "--to")


def test_parse_date_dir_accepts_the_filestore_format():
    assert _filter.parse_date_dir("2026.03.01") == DAY(2026, 3, 1)


@pytest.mark.parametrize(
    "name",
    [
        "sample_batches",  # the batch cache sits at the same level
        "instrumentA",  # so do instrument names, when globbed one level up
        "2026.3.1",  # unpadded is not what the filestore writes
        "2026.02.31",  # syntactically a date dir, not a real day
        "2026-03-01",  # the CLI format, not the directory format
    ],
)
def test_parse_date_dir_rejects_non_date_names(name):
    assert _filter.parse_date_dir(name) is None


# --- _filter: window selection ---


NAMES = ["2026.03.01", "2026.03.05", "2026.04.02", "sample_batches"]


def test_select_date_dirs_bounds_are_inclusive():
    selected = _filter.select_date_dirs(NAMES, DAY(2026, 3, 1), DAY(2026, 4, 2))

    assert selected == ["2026.03.01", "2026.03.05", "2026.04.02"]


def test_select_date_dirs_open_lower_bound():
    assert _filter.select_date_dirs(NAMES, None, DAY(2026, 3, 5)) == [
        "2026.03.01",
        "2026.03.05",
    ]


def test_select_date_dirs_open_upper_bound():
    assert _filter.select_date_dirs(NAMES, DAY(2026, 3, 5), None) == [
        "2026.03.05",
        "2026.04.02",
    ]


def test_select_date_dirs_excludes_dates_outside_the_window():
    assert _filter.select_date_dirs(NAMES, DAY(2026, 3, 2), DAY(2026, 3, 31)) == [
        "2026.03.05"
    ]


def test_select_date_dirs_never_selects_the_batch_cache():
    # sample_batches has no date in its path, so no window may ever pick it up
    # via the date rules - build_filter_rules decides it separately.
    assert "sample_batches" not in _filter.select_date_dirs(NAMES, None, None)


def test_select_date_dirs_deduplicates_and_sorts():
    names = ["2026.03.05", "2026.03.01", "2026.03.05"]

    assert _filter.select_date_dirs(names, None, None) == ["2026.03.01", "2026.03.05"]


# --- _filter: rsync rules ---


def test_build_filter_rules_order():
    rules = _filter.build_filter_rules(["2026.03.01", "2026.03.05"])

    assert rules == [
        "+ /filestore/",
        "+ /filestore/sample_batches/***",
        "+ /filestore/*/",
        "+ /filestore/*/2026.03.01/***",
        "+ /filestore/*/2026.03.05/***",
        "- /filestore/*/*",
    ]


def test_build_filter_rules_keeps_the_batch_cache_ahead_of_the_date_rules():
    # sample_batches is matched by "- /filestore/*/*" too; if its include came
    # later, first-match-wins would strip the cache contents.
    rules = _filter.build_filter_rules(["2026.03.01"])

    assert rules.index("+ /filestore/sample_batches/***") < rules.index(
        "- /filestore/*/*"
    )


def test_build_filter_rules_end_with_the_exclusion():
    # Losing this rule is the silent failure: rsync would report a filter and
    # still transfer every date directory.
    rules = _filter.build_filter_rules(["2026.03.01"])

    assert rules[-1] == "- /filestore/*/*"


def test_build_filter_rules_never_touch_the_rest_of_the_env():
    # The rsync transfer root is the whole env directory, so a stray rule here
    # would silently drop logs, agents, filestreams or the config overlays.
    rules = _filter.build_filter_rules(["2026.03.01"])

    assert all(rule.split(" ", 1)[1].startswith("/filestore/") for rule in rules)
    assert "- *" not in rules


def test_build_filter_rules_without_batches():
    rules = _filter.build_filter_rules(["2026.03.01"], include_batches=False)

    assert "+ /filestore/sample_batches/***" not in rules
    assert rules[-1] == "- /filestore/*/*"


def test_build_filter_rules_with_no_dates_excludes_every_date_dir():
    assert _filter.build_filter_rules([]) == [
        "+ /filestore/",
        "+ /filestore/sample_batches/***",
        "+ /filestore/*/",
        "- /filestore/*/*",
    ]


# --- _filter: listing the source ---


def test_source_date_dir_names_local(source_env):
    names = _filter.source_date_dir_names(None, "sync-src")

    assert names == ["2026.03.01", "2026.03.05", "2026.04.02", "batch-1"]


def test_source_date_dir_names_local_missing_filestore(mascope_home):
    (mascope_home / ".runtime" / "env" / "no-filestore").mkdir(
        parents=True, exist_ok=True
    )

    assert _filter.source_date_dir_names(None, "no-filestore") == []


def test_source_date_dir_names_remote(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=(
                "/srv/mascope/.runtime/env/e/filestore/instrumentA/2026.03.05\n"
                "/srv/mascope/.runtime/env/e/filestore/instrumentB/2026.03.05\n"
                "/srv/mascope/.runtime/env/e/filestore/instrumentA/2026.03.01\n"
                "\n"
            ),
        )

    monkeypatch.setattr(_filter.subprocess, "run", fake_run)
    monkeypatch.setattr(_filter, "cygwin_bin", lambda name: name)
    monkeypatch.setattr(_filter, "get_identity_args", lambda: [])
    monkeypatch.setattr(
        _filter, "remote_env_dir", lambda *a, **kw: "/srv/mascope/.runtime/env/e"
    )

    names = _filter.source_date_dir_names("user@host", "e", [])

    assert names == ["2026.03.01", "2026.03.05"]
    command = " ".join(captured["args"])
    assert "find -L" in command
    assert "-mindepth 2 -maxdepth 2 -type d" in command


def test_source_date_dir_names_remote_survives_a_failed_find(monkeypatch):
    monkeypatch.setattr(
        _filter.subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(args, 1, stdout=""),
    )
    monkeypatch.setattr(_filter, "cygwin_bin", lambda name: name)
    monkeypatch.setattr(_filter, "get_identity_args", lambda: [])
    monkeypatch.setattr(_filter, "remote_env_dir", lambda *a, **kw: "/srv/env")

    assert _filter.source_date_dir_names("user@host", "e", []) == []


# --- _sync.sync_filestore: rsync command construction ---


def _merge_file(command: str) -> str:
    """Extract the merge-file path from a captured rsync command string."""
    args = shlex.split(command)
    return args[args.index("--filter") + 1].split(" ", 1)[1]


def test_sync_filestore_without_dates_has_no_filter(
    posix_sync, captured_rsync, source_env
):
    _sync.sync_filestore("sync-src", "sync-dst")

    assert "--filter" not in captured_rsync["cmds"][0]


def test_sync_filestore_sets_deterministic_permissions(
    posix_sync, captured_rsync, source_env
):
    # Without --perms, new files inherit the *receiving* login's umask and
    # existing files keep whatever they had - the mode of a synced tree then
    # varies per host and per run.
    _sync.sync_filestore("sync-src", "sync-dst")

    assert "--perms" in captured_rsync["cmds"][0]
    assert "--chmod=D755,F644" in captured_rsync["cmds"][0]


def test_sync_filestore_with_dates_writes_a_merge_file(
    posix_sync, captured_rsync, source_env
):
    seen = {}

    def read_rules(command):
        seen["rules"] = open(_merge_file(command), encoding="utf-8").read()

    captured_rsync["on_run"] = [read_rules]

    _sync.sync_filestore(
        "sync-src", "sync-dst", from_date=DAY(2026, 3, 1), to_date=DAY(2026, 3, 31)
    )

    assert seen["rules"].splitlines() == [
        "+ /filestore/",
        "+ /filestore/sample_batches/***",
        "+ /filestore/*/",
        "+ /filestore/*/2026.03.01/***",
        "+ /filestore/*/2026.03.05/***",
        "- /filestore/*/*",
    ]


def test_sync_filestore_removes_the_filter_file_after_success(
    posix_sync, captured_rsync, source_env
):
    seen = {}
    captured_rsync["on_run"] = [lambda command: seen.update(path=_merge_file(command))]

    _sync.sync_filestore("sync-src", "sync-dst", from_date=DAY(2026, 3, 1))

    assert not os.path.exists(seen["path"])


def test_sync_filestore_removes_the_filter_file_after_failure(
    posix_sync, captured_rsync, source_env
):
    seen = {}
    captured_rsync["on_run"] = [lambda command: seen.update(path=_merge_file(command))]
    captured_rsync["returncode"] = 1

    with pytest.raises(RuntimeError, match="Filestore sync failed"):
        _sync.sync_filestore("sync-src", "sync-dst", from_date=DAY(2026, 3, 1))

    assert not os.path.exists(seen["path"])


def test_sync_filestore_raises_when_no_dates_match(
    posix_sync, captured_rsync, source_env
):
    with pytest.raises(RuntimeError, match="No filestore data matches"):
        _sync.sync_filestore("sync-src", "sync-dst", from_date=DAY(2030, 1, 1))

    assert captured_rsync["cmds"] == []


class _FakeFilterFile:
    """
    Stands in for the merge file the sync writes.

    Its string form is a Windows path so the Cygwin conversion can be pinned
    on the Linux CI runner too, while `parent` is a real directory so the
    cleanup in `sync_filestore` stays harmless.
    """

    def __init__(self, path, parent):
        self._path = path
        self.parent = parent

    def __str__(self):
        return self._path


def test_sync_filestore_filter_path_is_cygwin_converted(
    monkeypatch, captured_rsync, tmp_path
):
    # lib.run posix-splits the command string, which eats backslashes: an
    # unconverted C:\... path reaches rsync as garbage.
    scratch = tmp_path / "filter"
    scratch.mkdir()
    monkeypatch.setattr(_sync, "_on_windows", lambda: True)
    monkeypatch.setattr(_sync, "cygwin_bin", lambda name: f"C://cygwin64//bin//{name}")
    monkeypatch.setattr(_sync, "get_identity_args", lambda: [])
    monkeypatch.setattr(
        _sync, "_resolve_rsync_path", lambda *a, **kw: "C:\\mascope\\env\\e\\"
    )
    monkeypatch.setattr(
        _sync,
        "_write_filter_file",
        lambda *a, **kw: _FakeFilterFile(
            "C:\\Temp\\mascope-filter\\rules.txt", scratch
        ),
    )

    _sync.sync_filestore("sync-src", "sync-dst", from_date=DAY(2026, 3, 1))

    assert _merge_file(captured_rsync["cmds"][0]) == (
        "/cygdrive/c/Temp/mascope-filter/rules.txt"
    )
    assert not scratch.exists()  # the temp dir is swept once rsync is done


def test_sync_filestore_checks_ownership_after_a_successful_transfer(
    posix_sync, monkeypatch, source_env
):
    monkeypatch.setattr(_sync, "get_remote_mascope_path", lambda *a, **kw: "/srv")
    monkeypatch.setattr(
        _sync.lib, "run", lambda command, **kw: subprocess.CompletedProcess(command, 0)
    )
    seen = {}
    monkeypatch.setattr(
        _sync,
        "check_after_sync",
        lambda *args, **kwargs: seen.update(args=args, kwargs=kwargs),
    )

    _sync.sync_filestore("sync-src", "user@host:remote-env", chown=True)

    assert seen["args"] == ("user@host", "remote-env", None)
    assert seen["kwargs"] == {"chown": True}


def test_sync_filestore_skips_the_ownership_check_when_rsync_failed(
    posix_sync, monkeypatch, source_env
):
    monkeypatch.setattr(
        _sync.lib, "run", lambda command, **kw: subprocess.CompletedProcess(command, 1)
    )
    calls = []
    monkeypatch.setattr(_sync, "check_after_sync", lambda *a, **kw: calls.append(a))

    with pytest.raises(RuntimeError):
        _sync.sync_filestore("sync-src", "sync-dst")

    assert calls == []


# --- _ownership ---


def _fake_ssh(monkeypatch, stdout, returncode=0):
    """Route `_ownership`'s SSH calls to a canned result, capturing the argv."""
    captured = {"calls": []}

    def fake_run(args, **kwargs):
        captured["calls"].append(args)
        return subprocess.CompletedProcess(
            args, captured.get("returncode", returncode), stdout=stdout, stderr=""
        )

    monkeypatch.setattr(_ownership.subprocess, "run", fake_run)
    monkeypatch.setattr(_ownership, "cygwin_bin", lambda name: name)
    monkeypatch.setattr(_ownership, "get_identity_args", lambda: [])
    monkeypatch.setattr(_ownership, "remote_env_dir", lambda *a, **kw: "/srv/env/tof1")
    return captured


def test_check_after_sync_warns_on_uid_mismatch(monkeypatch, warnings):
    _fake_ssh(monkeypatch, stdout="1000:1000\n1001\n")

    _ownership.check_after_sync("user@host", "tof1")

    assert len(warnings) == 1
    assert "sudo chown -R 1000:1000 /srv/env/tof1" in warnings[0]
    assert "1001" in warnings[0]


def test_check_after_sync_silent_when_uids_match(monkeypatch, warnings):
    _fake_ssh(monkeypatch, stdout="1000:1000\n1000\n")

    _ownership.check_after_sync("user@host", "tof1")

    assert warnings == []


def test_check_after_sync_skipped_on_a_windows_local_target(monkeypatch, warnings):
    calls = _fake_ssh(monkeypatch, stdout="")
    monkeypatch.setattr(_ownership, "_on_windows", lambda: True)

    _ownership.check_after_sync(None, "tof1")

    assert calls["calls"] == []
    assert warnings == []


@pytest.mark.parametrize("stdout", ["", "nonsense\n", "1000:1000\n", "a:b\nc\n"])
def test_check_after_sync_never_raises(monkeypatch, warnings, stdout):
    # A broken check must not fail an otherwise good sync.
    _fake_ssh(monkeypatch, stdout=stdout, returncode=1)

    assert _ownership.check_after_sync("user@host", "tof1") is None
    assert warnings == []


def test_chown_runs_sudo_n_on_a_remote_target(monkeypatch, warnings):
    captured = _fake_ssh(monkeypatch, stdout="1000:1000\n1001\n")

    _ownership.check_after_sync("user@host", "tof1", chown=True)

    chown_call = " ".join(captured["calls"][-1])
    assert "sudo -n chown -R 1000:1000 /srv/env/tof1" in chown_call
    # chown -R does not follow a symlinked filestore (GNU default -P), and a
    # filestore on a data volume is a documented deployment layout.
    assert "readlink -f /srv/env/tof1/filestore" in chown_call


def test_chown_failure_reports_the_manual_command(monkeypatch, warnings):
    captured = _fake_ssh(monkeypatch, stdout="1000:1000\n1001\n")
    captured["returncode"] = 1

    _ownership.check_after_sync("user@host", "tof1", chown=True)

    assert len(warnings) == 2
    assert "sudo chown -R 1000:1000 /srv/env/tof1" in warnings[-1]


class _FakeEnvDir:
    """A local env dir with a scripted owner — POSIX uids on any platform."""

    def __init__(self, path, uid=None, gid=None, error=None):
        self._path = path
        self._stat = SimpleNamespace(st_uid=uid, st_gid=gid)
        self._error = error

    def stat(self):
        if self._error is not None:
            raise self._error
        return self._stat

    def __str__(self):
        return self._path


def test_check_after_sync_warns_on_a_local_uid_mismatch(monkeypatch, warnings):
    monkeypatch.setattr(_ownership, "_on_windows", lambda: False)
    monkeypatch.setattr(_ownership.os, "getuid", lambda: 1001, raising=False)
    monkeypatch.setattr(
        _ownership,
        "local_env_dir",
        lambda name: _FakeEnvDir(f"/home/dev/.runtime/env/{name}", 1000, 1000),
    )

    _ownership.check_after_sync(None, "tof1")

    assert len(warnings) == 1
    assert "sudo chown -R 1000:1000 /home/dev/.runtime/env/tof1" in warnings[0]


def test_check_after_sync_skips_a_local_target_that_is_not_there(monkeypatch, warnings):
    monkeypatch.setattr(_ownership, "_on_windows", lambda: False)
    monkeypatch.setattr(
        _ownership,
        "local_env_dir",
        lambda name: _FakeEnvDir("/gone", error=FileNotFoundError("no such dir")),
    )

    assert _ownership.check_after_sync(None, "tof1") is None
    assert warnings == []


# --- CLI wiring ---


@pytest.fixture
def stub_sync(monkeypatch):
    """Replace both sync halves so the command runs without SSH or Postgres."""
    calls = {"db": [], "filestore": []}
    monkeypatch.setattr(
        env_main, "sync_db", lambda *a, **kw: calls["db"].append((a, kw))
    )
    monkeypatch.setattr(
        env_main, "sync_filestore", lambda *a, **kw: calls["filestore"].append((a, kw))
    )
    monkeypatch.setattr(env_main, "env_exists_local", lambda name: True)
    return calls


@pytest.fixture
def cli_warnings(monkeypatch):
    collected = []
    monkeypatch.setattr(env_main.runtime.logger, "warning", collected.append)
    return collected


def _invoke(cli_runner, *extra):
    return cli_runner.invoke(
        env_main.env_app, ["sync", "src", "dev", "dst", "dev", *extra]
    )


def test_sync_rejects_a_malformed_from_date(cli_runner, stub_sync):
    result = _invoke(cli_runner, "--from", "01/03/2026")

    assert result.exit_code == 1
    assert stub_sync == {"db": [], "filestore": []}


def test_sync_rejects_from_after_to(cli_runner, stub_sync):
    result = _invoke(cli_runner, "--from", "2026-03-05", "--to", "2026-03-01")

    assert result.exit_code == 1
    assert stub_sync == {"db": [], "filestore": []}


def test_sync_accepts_an_equal_from_and_to(cli_runner, stub_sync):
    result = _invoke(
        cli_runner, "--from", "2026-03-01", "--to", "2026-03-01", "--skip-db"
    )

    assert result.exit_code == 0
    assert stub_sync["filestore"][0][1]["from_date"] == DAY(2026, 3, 1)


def test_sync_passes_parsed_dates_to_sync_filestore(cli_runner, stub_sync):
    result = _invoke(
        cli_runner, "--from", "2026-03-01", "--to", "2026-03-31", "--skip-db"
    )

    assert result.exit_code == 0
    kwargs = stub_sync["filestore"][0][1]
    assert kwargs["from_date"] == DAY(2026, 3, 1)
    assert kwargs["to_date"] == DAY(2026, 3, 31)


def test_sync_without_dates_passes_none(cli_runner, stub_sync):
    result = _invoke(cli_runner, "--skip-db")

    assert result.exit_code == 0
    kwargs = stub_sync["filestore"][0][1]
    assert kwargs["from_date"] is None and kwargs["to_date"] is None
    assert kwargs["chown"] is False


def test_sync_forwards_chown(cli_runner, stub_sync):
    result = _invoke(cli_runner, "--skip-db", "--chown")

    assert result.exit_code == 0
    assert stub_sync["filestore"][0][1]["chown"] is True


def test_sync_warns_when_dates_are_given_with_skip_filestore(
    cli_runner, stub_sync, cli_warnings
):
    result = _invoke(cli_runner, "--from", "2026-03-01", "--skip-filestore")

    assert result.exit_code == 0
    assert any("no effect" in w for w in cli_warnings)


def test_sync_warns_that_the_database_is_not_filtered(
    cli_runner, stub_sync, cli_warnings
):
    # A filtered filestore plus a full database leaves sample rows whose files
    # were never transferred - legitimate, but never silently.
    result = _invoke(cli_runner, "--from", "2026-03-01")

    assert result.exit_code == 0
    assert any("--skip-db" in w for w in cli_warnings)


def test_sync_does_not_warn_about_the_database_when_it_is_skipped(
    cli_runner, stub_sync, cli_warnings
):
    result = _invoke(cli_runner, "--from", "2026-03-01", "--skip-db")

    assert result.exit_code == 0
    assert cli_warnings == []
