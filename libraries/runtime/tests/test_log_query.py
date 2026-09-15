"""
Tests for log querying (`RuntimeLogging.query`).

These run real DuckDB queries against NDJSON log files written into a temp
directory shaped like a runtime env's log dir (`<base>/<mode>/<date>.<module>.log`),
and cover the agent-facing behaviors: raw NDJSON output (--json), newest-N
limiting, grep patterns containing SQL-hostile quotes, the per-service filter,
the empty-glob case, rotated `.log.zip` archive inclusion, and interval
validation. They skip when the optional `duckdb` dependency
(`mascope_runtime[logs]`) is not installed.

Record timestamps are written with the machine's local UTC offset: naive
`--from`/`--to` values are compared in DuckDB's session timezone (the system
local zone), mirroring how the CLI is used on a server.
"""

import datetime
import json
import zipfile

import pytest


pytest.importorskip("duckdb")

import mascope_runtime.logging as rl  # noqa: E402


# --- fake runtime + log fixtures -------------------------------------------


class _FakeModuleConfig:
    def __init__(self, log_path):
        self.log_path = log_path
        self.log_level = "info"


class _FakeModule:
    def __init__(self, log_path):
        self.name = "cli"
        self.config = _FakeModuleConfig(log_path)


class _FakeRuntime:
    """Just enough Runtime for RuntimeLogging.query()."""

    def __init__(self, base):
        self.module = _FakeModule(base)
        self.mode = "dev"
        self.logger = rl.logger


def _time(hour, minute, second, day=23):
    """2026-06-<day> HH:MM:SS in the machine's local zone, loguru-repr style."""
    naive = datetime.datetime(2026, 6, day, hour, minute, second)
    return naive.astimezone().isoformat(sep=" ", timespec="microseconds")


def _record(time_repr, message, level="INFO", level_no=20, module="backend"):
    """One loguru-serialized NDJSON line, shaped like `serialize=True` output."""
    return {
        "text": f"{message}\n",
        "record": {
            "time": {"repr": time_repr, "timestamp": 0},
            "level": {"name": level, "no": level_no, "icon": ""},
            "message": message,
            "name": f"mascope_{module}.some.module",
            "function": "run",
            "line": 1,
            "module": "some",
            "file": {"name": "some.py", "path": "/x/some.py"},
            "process": {"id": 1, "name": "MainProcess"},
            "thread": {"id": 1, "name": "MainThread"},
            "elapsed": {"repr": "0:00:00", "seconds": 0.0},
            "exception": None,
            "extra": {"mod": module, "key": "", "status_code": "", "method": ""},
        },
    }


QUOTED_MESSAGE = 'meeting at 9 o\'clock failed: quote "test"'


@pytest.fixture
def log_env(tmp_path):
    """A log dir with backend + file-converter logs, wrapped in RuntimeLogging."""
    log_dir = tmp_path / "dev"
    log_dir.mkdir()
    backend = [_record(_time(10, 0, i), f"backend event {i}") for i in range(5)]
    backend.append(_record(_time(10, 1, 0), QUOTED_MESSAGE, level="ERROR", level_no=40))
    converter = [_record(_time(10, 0, 30), "converter event", module="file-converter")]
    _write_ndjson(log_dir / "2026-06-23.backend.log", backend)
    _write_ndjson(log_dir / "2026-06-23.file-converter.log", converter)
    return rl.RuntimeLogging(_FakeRuntime(str(tmp_path)))


def _write_ndjson(path, records):
    with open(path, "w", encoding="utf-8") as file:
        file.writelines(json.dumps(record) + "\n" for record in records)


def _json_lines(capsys):
    """Parse the captured stdout as one JSON record per line."""
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _messages(capsys):
    return [line["record"]["message"] for line in _json_lines(capsys)]


# --- json output ------------------------------------------------------------


def test_json_output_is_raw_ndjson(log_env, capsys):
    log_env.query(json_output=True)

    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert len(lines) == 7  # 6 backend + 1 converter
    # raw records pass through, no summary line, no ANSI colors
    assert all("record" in line for line in lines)
    assert "Printed" not in out
    assert "\x1b[" not in out


# --- newest-N limiting ------------------------------------------------------


def test_limit_returns_most_recent_oldest_first(log_env, capsys):
    log_env.query(json_output=True, limit=2)

    # the two newest records (converter at 10:00:30, error at 10:01:00),
    # still printed in chronological order
    assert _messages(capsys) == ["converter event", QUOTED_MESSAGE]


# --- grep -------------------------------------------------------------------


def test_grep_with_single_quote_does_not_crash(log_env, capsys):
    log_env.query(json_output=True, grep="9 o'clock", grep_context=0)

    assert _messages(capsys) == [QUOTED_MESSAGE]


def test_grep_includes_context_rows(log_env, capsys):
    log_env.query(json_output=True, grep="o'clock", grep_context=2, service="backend")

    # the match plus the two preceding rows (nothing follows the match)
    assert _messages(capsys) == ["backend event 3", "backend event 4", QUOTED_MESSAGE]


# --- service filter ---------------------------------------------------------


def test_service_filter_selects_one_module(log_env, capsys):
    log_env.query(json_output=True, service="file-converter")

    assert _messages(capsys) == ["converter event"]


def test_service_with_no_files_prints_nothing(log_env, capsys):
    log_env.query(json_output=True, service="nonexistent")
    assert _json_lines(capsys) == []


def test_service_rejects_hostile_names(log_env, capsys):
    log_env.query(json_output=True, service="../../etc")
    assert _json_lines(capsys) == []


# --- level + time filters (regression) --------------------------------------


def test_level_filter(log_env, capsys):
    log_env.query(json_output=True, level="error")

    lines = _json_lines(capsys)
    assert [line["record"]["level"]["name"] for line in lines] == ["ERROR"]


def test_time_range_filter(log_env, capsys):
    log_env.query(
        json_output=True,
        from_datetime="2026-06-23 10:00:02",
        to_datetime="2026-06-23 10:00:04",
    )

    assert _messages(capsys) == [
        "backend event 2",
        "backend event 3",
        "backend event 4",
    ]


def test_interval_anchored_to_from(log_env, capsys):
    log_env.query(
        json_output=True,
        from_datetime="2026-06-23 10:00:03",
        interval="2 seconds",
        service="backend",
    )

    assert _messages(capsys) == ["backend event 3", "backend event 4"]


# --- pretty output ----------------------------------------------------------


def test_pretty_output_decodes_json_escapes(log_env, capsys):
    """The default printout must render `\\"` escapes, not show them raw."""
    sink_lines = []
    sink_id = rl.logger.add(
        lambda message: sink_lines.append(message.record["message"]),
        level="ERROR",
    )
    try:
        log_env.query(level="error")
    finally:
        rl.logger.remove(sink_id)

    assert sink_lines == [QUOTED_MESSAGE]
    assert "Printed 1 lines" in capsys.readouterr().out


# --- rotated archives -------------------------------------------------------


def _write_zip(path, member_name, content):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member_name, content)


def _ndjson(records):
    return "".join(json.dumps(record) + "\n" for record in records)


@pytest.fixture
def archived_log_env(tmp_path):
    """A log dir mixing live files with rotated, truncated, corrupt and empty archives."""
    log_dir = tmp_path / "dev"
    log_dir.mkdir()
    _write_ndjson(
        log_dir / "2026-06-23.backend.log",
        [_record(_time(10, 0, i), f"live event {i}") for i in range(2)],
    )
    # a plainly-named rotated day and a worker-suffixed one (both shapes occur)
    _write_zip(
        log_dir / "2026-06-22.backend.log.zip",
        "2026-06-22.backend.log",
        _ndjson([_record(_time(9, 0, 0, day=22), "archived event day22")]),
    )
    _write_zip(
        log_dir / "2026-06-21.backend.2026-06-22_00-00-00_000001.log.zip",
        "2026-06-21.backend.log",
        _ndjson([_record(_time(9, 0, 0, day=21), "archived event day21")]),
    )
    _write_zip(
        log_dir / "2026-06-22.file-converter.log.zip",
        "2026-06-22.file-converter.log",
        _ndjson(
            [
                _record(
                    _time(9, 30, 0, day=22),
                    "archived converter event",
                    module="file-converter",
                )
            ]
        ),
    )
    # a member whose tail was cut mid-record: the valid line must survive
    _write_zip(
        log_dir / "2026-06-20.backend.log.zip",
        "2026-06-20.backend.log",
        _ndjson([_record(_time(8, 0, 0, day=20), "salvaged event")])
        + '{"text": "half a rec',
    )
    # a corrupt container and an empty one (both occur in production rotation)
    (log_dir / "2026-06-19.backend.log.zip").write_bytes(b"this is not a zip archive")
    with zipfile.ZipFile(log_dir / "2026-06-18.backend.log.zip", "w"):
        pass
    return rl.RuntimeLogging(_FakeRuntime(str(tmp_path)))


def test_query_includes_rotated_archives(archived_log_env, capsys):
    archived_log_env.query(json_output=True)

    assert _messages(capsys) == [
        "salvaged event",
        "archived event day21",
        "archived event day22",
        "archived converter event",
        "live event 0",
        "live event 1",
    ]


def test_service_filter_spans_archives(archived_log_env, capsys):
    archived_log_env.query(json_output=True, service="backend")

    assert _messages(capsys) == [
        "salvaged event",
        "archived event day21",
        "archived event day22",
        "live event 0",
        "live event 1",
    ]


def test_time_filter_applies_to_archived_days(archived_log_env, capsys):
    archived_log_env.query(
        json_output=True,
        from_datetime="2026-06-22 00:00:00",
        to_datetime="2026-06-22 23:59:59",
    )

    assert _messages(capsys) == ["archived event day22", "archived converter event"]


def test_unreadable_archive_warns_and_is_skipped(archived_log_env, capsys):
    warnings = []
    sink_id = rl.logger.add(
        lambda message: warnings.append(message.record["message"]), level="WARNING"
    )
    try:
        archived_log_env.query(json_output=True)
    finally:
        rl.logger.remove(sink_id)

    assert any("skipped 1 unreadable log archive" in message for message in warnings)
    assert len(_messages(capsys)) == 6


# --- interval validation ----------------------------------------------------


def test_interval_shorthand_matches_spelled_out(log_env, capsys):
    log_env.query(
        json_output=True,
        from_datetime="2026-06-23 10:00:03",
        interval="2s",
        service="backend",
    )

    assert _messages(capsys) == ["backend event 3", "backend event 4"]


def test_invalid_interval_is_rejected_not_narrowed(log_env, capsys):
    """'60d'-style typos used to cast to seconds, silently collapsing the window."""
    errors = []
    sink_id = rl.logger.add(
        lambda message: errors.append(message.record["message"]), level="ERROR"
    )
    try:
        log_env.query(json_output=True, interval="60x")
        log_env.query(json_output=True, interval="60")
    finally:
        rl.logger.remove(sink_id)

    assert _json_lines(capsys) == []
    assert len([error for error in errors if "invalid interval" in error]) == 2


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("60d", "60 days"),
        ("12H", "12 hours"),
        ("2w", "2 weeks"),
        ("45 s", "45 seconds"),
        ("60 days", "60 days"),
        ("1 hour 30 minutes", "1 hour 30 minutes"),
        ("3months", "3 months"),
        ("60", None),
        ("60x", None),
        ("days", None),
        ("1; DROP TABLE log", None),
    ],
)
def test_normalize_interval(raw, normalized):
    assert rl._normalize_interval(raw) == normalized


# --- gc interval handling ---------------------------------------------------


def test_gc_retain_shorthand_dryrun_keeps_files(log_env, tmp_path):
    log_env.gc(mode="dev", before=None, retain="7d", dryrun=True)

    assert (tmp_path / "dev" / "2026-06-23.backend.log").exists()


def test_gc_invalid_retain_is_rejected(log_env, tmp_path):
    errors = []
    sink_id = rl.logger.add(
        lambda message: errors.append(message.record["message"]), level="ERROR"
    )
    try:
        log_env.gc(mode="dev", before=None, retain="7x", dryrun=False)
    finally:
        rl.logger.remove(sink_id)

    assert any("invalid retain interval" in message for message in errors)
    assert (tmp_path / "dev" / "2026-06-23.backend.log").exists()


# --- gc coverage of rotated archives ----------------------------------------


def test_gc_collects_stale_archives_too(archived_log_env, tmp_path):
    """Retention has to reach the archives, or it barely reaches anything.

    Rotated days exist only as `.log.zip`, so they are the bulk of a log dir -
    a sweep of `*.log` alone deletes the one uncompressed day and reports
    "garbage collected log files older than <date>" over a directory it left
    almost untouched. The same sweep is what the query above now reads from,
    so the two must agree about which days still exist.
    """
    log_dir = tmp_path / "dev"
    archives = sorted(path.name for path in log_dir.glob("*.log.zip"))
    assert archives, "fixture no longer writes archives"

    # Every file the fixture wrote is dated June 2026; `before` is a fixed date
    # rather than a retain interval so this cannot drift with the wall clock.
    archived_log_env.gc(mode="dev", before="2026-07-01", retain=None, dryrun=False)

    assert list(log_dir.glob("*.log.zip")) == [], (
        f"stale archives survived the sweep: {archives}"
    )
    assert list(log_dir.glob("*.log")) == []


def test_gc_dryrun_keeps_archives(archived_log_env, tmp_path):
    """A preview must not delete the archives it now also lists."""
    log_dir = tmp_path / "dev"
    before = sorted(path.name for path in log_dir.iterdir())

    archived_log_env.gc(mode="dev", before="2026-07-01", retain=None, dryrun=True)

    assert sorted(path.name for path in log_dir.iterdir()) == before


def test_gc_keeps_fresh_archives(archived_log_env, tmp_path):
    """Only stale ones go: an archive inside the window is left alone."""
    log_dir = tmp_path / "dev"

    archived_log_env.gc(mode="dev", before="2026-06-01", retain=None, dryrun=False)

    assert sorted(path.name for path in log_dir.glob("*.log.zip")) == sorted(
        [
            "2026-06-18.backend.log.zip",
            "2026-06-19.backend.log.zip",
            "2026-06-20.backend.log.zip",
            "2026-06-21.backend.2026-06-22_00-00-00_000001.log.zip",
            "2026-06-22.backend.log.zip",
            "2026-06-22.file-converter.log.zip",
        ]
    )
