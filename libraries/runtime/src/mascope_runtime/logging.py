"""
Runtime logging configuration (Loguru).

Level policy: every record at WARNING or above is exported to error
monitoring when ``MASCOPE_SENTRY_DSN`` is set (see the sink below), so levels
express operator relevance, not verbosity:

- DEBUG/INFO: routine operation - expected data conditions, per-item
  progress, client errors, retries that usually succeed. Anything that fires
  per request, per file, or per loop item belongs here. INFO and above
  reaches the log files.
- WARNING: actionable operator signal, expected to be rare and to group into
  a single monitored issue.
- ERROR/CRITICAL: faults. Inside an except block use ``logger.exception`` so
  the traceback travels with the record, and log an incident exactly once -
  the outermost handler owns the record (no log-then-raise).
"""

# import type hint w/o circular import error
from __future__ import annotations

import typing


if typing.TYPE_CHECKING:
    import loguru

    from mascope_runtime import Runtime

    from .mode import RuntimeMode

import datetime
import glob
import inspect
import io
import json
import logging as std_logging
import os
import re
import shutil
import sys
import tempfile
import zipfile
from types import TracebackType
from typing import Callable, List

from loguru import logger
from rich.console import Console
from rich.traceback import Traceback


def _duckdb():
    """
    Import duckdb on first use.

    Only the log-query commands need it; keeping the import out of module
    scope lets the base install skip the dependency (the `logs` extra
    provides it).
    """
    try:
        import duckdb
    except ImportError as error:
        raise ImportError(
            "Log querying requires duckdb - install mascope_runtime[logs]"
        ) from error
    return duckdb


_INTERVAL_SHORTHAND_UNITS = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}
_INTERVAL_LONG_UNIT = r"(?:second|minute|hour|day|week|month|year)s?"


def _normalize_interval(value: str) -> str | None:
    """
    Normalize a user-supplied time interval into a string DuckDB parses the
    way the user meant it.

    DuckDB's interval cast is lenient with unrecognised text: `'60d'` casts
    to 60 *seconds*, not 60 days, silently collapsing the queried window.
    Only two spellings are accepted - compact shorthand (`60d`, `12h`) and
    spelled-out units (`60 days`, `1 hour 30 minutes`) - and everything else
    is rejected so a typo cannot quietly narrow a query.

    :param value: the raw interval string
    :return: a normalized interval string, or None when unrecognised
    """
    text = value.strip().lower()
    compact = re.fullmatch(r"(\d+)\s*([smhdw])", text)
    if compact:
        return f"{int(compact.group(1))} {_INTERVAL_SHORTHAND_UNITS[compact.group(2)]}"
    if re.fullmatch(
        rf"\d+\s*{_INTERVAL_LONG_UNIT}(?:\s+\d+\s*{_INTERVAL_LONG_UNIT})*", text
    ):
        pairs = re.findall(rf"(\d+)\s*({_INTERVAL_LONG_UNIT})", text)
        return " ".join(f"{int(number)} {unit}" for number, unit in pairs)
    return None


def _extract_log_archives(archives: List[str], dest_dir: str) -> tuple[int, int]:
    """
    Extract rotated log archives into ``dest_dir`` as flat ``*.log`` files.

    Rotated days exist only as zip containers (the file sink's
    ``compression``), and DuckDB reads gzip but not zip members, so querying
    past days means unpacking them first. Truncated and empty archives occur
    in production (rotation under multi-worker contention), so an unreadable
    archive is skipped and counted rather than failing the query.

    Member names are untrusted zip content: extracted files get flat
    sequential names and the member name is never used as a path, so a
    hostile archive cannot escape ``dest_dir``.

    :param archives: paths of ``.log.zip`` files to extract
    :param dest_dir: directory to extract into
    :return: (extracted member files, skipped unreadable archives)
    """
    extracted = 0
    skipped = 0
    for archive_index, archive in enumerate(sorted(archives)):
        try:
            with zipfile.ZipFile(archive) as container:
                for member_index, member in enumerate(container.namelist()):
                    target = os.path.join(
                        dest_dir, f"{archive_index:05d}_{member_index:03d}.log"
                    )
                    with container.open(member) as source, open(target, "wb") as sink:
                        shutil.copyfileobj(source, sink)
                    extracted += 1
        except (zipfile.BadZipFile, OSError):
            skipped += 1
    return extracted, skipped


# --- optional GlitchTip/Sentry error reporting ------------------------------
# Fully gated on MASCOPE_SENTRY_DSN: unset (the default) => no import, no
# sentry_sdk.init, no sink, i.e. zero behavior change. Set it to a GlitchTip
# project DSN to forward WARNING+ log records as events. Needs the optional
# dependency: `pip install mascope_runtime[sentry]`.
# MASCOPE_SENTRY_TRACES_RATE (0..1) additionally samples request transactions
# for GlitchTip's per-endpoint performance view; it does nothing without a DSN.
_SENTRY_LEVELS = {"WARNING": "warning", "ERROR": "error", "CRITICAL": "fatal"}
_sentry_ready = False


def _traces_sample_rate() -> float:
    """
    Read ``MASCOPE_SENTRY_TRACES_RATE`` as the transaction sample rate.

    Unset (the default) means 0.0: errors only, no performance tracing. A
    value that is not a number in [0, 1] logs a warning and keeps tracing off
    rather than raising - a typo in ``/etc/environment`` must not break the
    backend at import time.
    """
    raw = os.environ.get("MASCOPE_SENTRY_TRACES_RATE")
    if not raw:
        return 0.0
    try:
        rate = float(raw)
    except ValueError:
        rate = None
    if rate is None or not 0.0 <= rate <= 1.0:
        logger.warning(
            f"MASCOPE_SENTRY_TRACES_RATE={raw!r} is not a number in [0, 1]; "
            "performance tracing stays OFF."
        )
        return 0.0
    return rate


def _init_sentry(environment: str, release: str | None) -> bool:
    """
    Initialize the Sentry SDK once, iff ``MASCOPE_SENTRY_DSN`` is set.

    Returns True when error reporting is active (DSN set and ``sentry-sdk``
    importable), so the caller appends the loguru sink handler. Idempotent: a
    second call after a successful init returns True without re-initializing.
    """
    global _sentry_ready
    if _sentry_ready:
        return True
    dsn = os.environ.get("MASCOPE_SENTRY_DSN")
    if not dsn:
        return False
    try:
        import logging as std_logging

        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.loguru import LoguruIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        logger.warning(
            "MASCOPE_SENTRY_DSN is set but sentry-sdk is not installed; error "
            "reporting is OFF. Install mascope_runtime[sentry]."
        )
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        # Friendly per-server identity in events/alerts: the runtime env name
        # (e.g. "site1"). Without this the SDK falls back to the container
        # hostname, which under Docker is an opaque container id.
        server_name=os.environ.get("MASCOPE_ENV") or None,
        release=release,
        # 0.0 unless MASCOPE_SENTRY_TRACES_RATE opts this server into
        # performance tracing (per-endpoint latency in GlitchTip).
        traces_sample_rate=_traces_sample_rate(),
        profiles_sample_rate=0.0,  # GlitchTip does not consume profiles
        send_default_pii=False,  # no cookies / auth headers / client IPs
        max_request_body_size="never",
        auto_session_tracking=False,  # GlitchTip does not consume sessions
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
            # event_level=None neutralizes the auto-enabled default (which
            # would double-report ERROR events next to our own sink). Its
            # breadcrumb handler does not survive the logger.configure() that
            # follows init, so this integration exists only as that override.
            LoguruIntegration(level=std_logging.INFO, event_level=None),
        ],
    )
    _sentry_ready = True
    return True


def _sentry_sink(message) -> None:
    """
    Loguru sink: forward a WARNING+ record to GlitchTip as a Sentry event.

    Installed as an extra loguru handler only when ``_init_sentry`` succeeded.
    Must never raise (a sink cannot) and must never call ``logger.*`` (recurses).
    """
    import sentry_sdk

    record = message.record
    # Loop guard: never re-report the SDK's own transport/worker errors. Matters
    # if stdlib logging is ever routed into loguru - a failed send logged on
    # "sentry_sdk.errors" would otherwise recurse back through this sink.
    name = str(record["name"])
    if name.startswith("sentry_sdk") or name.startswith("urllib3"):
        return

    level_name = record["level"].name
    sentry_level = _SENTRY_LEVELS.get(level_name, level_name.lower())
    exc = record["exception"]  # loguru (type, value, traceback) tuple, or None
    try:
        with sentry_sdk.new_scope() as scope:
            scope.set_level(sentry_level)
            scope.set_tag("log_level", level_name)
            scope.set_tag("logger", name)
            if exc is not None:
                sentry_sdk.capture_exception((exc.type, exc.value, exc.traceback))
            else:
                sentry_sdk.capture_message(record["message"])
    except Exception:
        # A sink must never raise, and must not logger.* here (loop guard).
        pass


class InterceptHandler(std_logging.Handler):
    """
    Route stdlib ``logging`` records into loguru.

    Installed on the stdlib root logger by ``RuntimeLogging.configure`` so
    records from libraries that use ``logging.getLogger`` (zarr, thermo
    reader, ...) reach the loguru handlers - the log files, the terminal,
    and (for WARNING+) the error-monitoring sink. Based on the recipe from
    the loguru documentation.
    """

    def emit(self, record: std_logging.LogRecord) -> None:
        # Get corresponding loguru level if it exists.
        level: str | int
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message.
        frame, depth = inspect.currentframe(), 0
        while frame and (
            depth == 0 or frame.f_code.co_filename == std_logging.__file__
        ):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


highlight = re.compile("SUCCESS|WARNING|ERROR|CRITICAL")

palette = {
    "magenta": "#d8137f",
    "red": "#d65407",
    "orange": "#dc8a0e",
    "green": "#17ad98",
    "blue": "#149bda",
    "purple": "#796af5",
    "pink": "#c720ca",
    "white": "white",
}


class Stacktrace:
    """
    Dummy class for printing pretty stacktraces
    """

    pass


class RuntimeLogging:
    """
    Helper class to configure the runtime logger of a
    module.

    Since loguru configuration persists across modules,
    once we execute the `configure` method, we can just
    import `logger` from Loguru and the settings will
    persist. This is what the Runtime class does when
    exposing `runtime.logger`.

    Helper methods are here are mostly for formatting
    the CLI logs nicely.

    To be used with the `configure_logger` helper
    below.
    """

    _runtime: Runtime

    def __init__(self, runtime: Runtime) -> None:
        """
        Configures the runtime logger, saving the
        result to self._logger.

        :param runtime: The parent runtime context
        :type runtime: Runtime
        """
        self._runtime = runtime

    @property
    def runtime(self):
        """
        The parent runtime context.
        """
        return self._runtime

    @property
    def logger(self):
        """
        Get the configured logger.

        :return: the logger of the runtime module
        :rtype: loguru.Logger
        """
        return logger

    @property
    def dir(self):
        """
        Resolves the logging directory
        """
        return self.runtime.module.config.log_path

    def path(self, *args: list[str]):
        """
        Resolves the path relative to the logging directory

        :param *args: A list of path segments
        :type arg: list[str], optional
        :return: Resolved path
        :rtype: str
        """
        return os.path.join(self.dir, self.runtime.mode, *args)

    # module is not typed to prevent circular import
    def configure(self) -> None:
        """
        Configure the loguru logger, setting file and terminal logging
        handlers, log level formatting and other settings. Clears all
        previous configuration.

        :param module: the runtime module to configure the logging for
        :return: the loguru logger
        """
        # setup log path
        os.makedirs(self.path(), exist_ok=True)
        # define logging handlers
        file_handler = dict(
            sink=self.path(f"{{time:YYYY-MM-DD}}.{self.runtime.module.name}.log"),
            format=self.formatter(),
            level="INFO",  # avoid large file size
            enqueue=True,  # multiprocess safe
            serialize=True,  # output as JSON
            rotation=datetime.time(
                0, 0, 0, tzinfo=datetime.timezone.utc
            ),  # rotate daily at midnight UTC
            retention=datetime.timedelta(days=14),  # retain two weeks of files
            compression="zip",  # compress rotated files (~10x smaller on disk)
        )
        terminal_handler = dict(
            sink=sys.stdout,
            format=self.formatter(),
            colorize=True,
            level=self.runtime.module.config.log_level.upper(),
            enqueue=True,  # multiprocess safe
            catch=True,
        )
        # Optional GlitchTip/Sentry error reporting, OFF unless MASCOPE_SENTRY_DSN
        # is set. Init here (once) so the SDK is live before the backend builds
        # FastAPI() - fast.py imports this Runtime first. Must run BEFORE
        # logger.remove(): its "SDK missing" warning needs a live handler.
        # The CLI never reports: its WARNING+ records are user-facing terminal
        # output (bad arguments, status notices), not operator signal, and a
        # DSN exported in the shell env would otherwise turn every CLI warning
        # into a GlitchTip event.
        is_cli = self.runtime.module.name == "cli"
        sentry_on = not is_cli and _init_sentry(
            environment=str(self.runtime.mode), release=self.runtime.version
        )

        # create fresh config
        logger.remove()  # remove old settings

        handlers = [terminal_handler] if is_cli else [file_handler, terminal_handler]
        if sentry_on:
            # The callable sink reads the record directly, so it ignores the
            # text `format` used above.
            handlers.append(
                dict(sink=_sentry_sink, level="WARNING", enqueue=False, catch=True)
            )

        # Bridge stdlib logging into loguru: without this, records emitted via
        # logging.getLogger reach neither the log files nor the monitoring
        # sink. force=True replaces handlers from any earlier configure().
        # The sink's loop guard already skips sentry_sdk/urllib3 records.
        std_logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

        logger.configure(  # apply new settings
            handlers=handlers,
            levels=[
                dict(name="TRACE", color="<magenta>"),
                dict(name="DEBUG", color="<magenta>"),
                dict(name="INFO", color="<blue>"),
                dict(name="SUCCESS", color="<green>"),
                dict(name="WARNING", color="<yellow>"),
                dict(name="ERROR", color="<red><bold>"),
                dict(name="CRITICAL", color="<RED><bold>"),
            ],
            extra=dict(mod=self.runtime.module.name, key="", status_code="", method=""),
        )
        return logger

    def formatter(self) -> Callable[[loguru.Record], str]:
        """
        Factory that produces a format function used in
        the loguru logger.

        :return: the record formatting function
        """

        def format_record(record: loguru.Record):
            # STATUS

            # code
            raw_status_code = record["extra"]["status_code"]
            if isinstance(raw_status_code, str):
                if len(raw_status_code):
                    status_code = int(raw_status_code)
                else:
                    status_code = 0
            elif isinstance(raw_status_code, int):
                status_code = raw_status_code
            else:
                status_code = 0

            status_style = self.status_style(status_code)

            # FIELDS

            # overrides
            #  see the `query` method below
            override = record["extra"].get("override")
            if override:
                timestamp = override["time"]
                record["name"] = override["name"]
                record["function"] = override["function"]
                record["line"] = override["line"]
                record["extra"] = override["extra"]
                rich_exception = override["extra"].get("rich_exception")
            else:
                timestamp = "{time:HH:mm:ss.SSS!UTC}"
                rich_exception = None

            # head
            level = self.style("{level: >8}", "lvl")
            status = "{extra[status_code]: >3}"
            method = "{extra[method]: <7}"
            head = f"{timestamp} {level} " + status_style(f"{status} {method}")
            head_text = f"{record['level']} {record['extra']['status_code']} {record['extra']['method']}"

            # message
            record["extra"]["parsed_message"] = (
                record["message"]
                .replace(self.runtime.env.path(), "$env")
                .replace(self.runtime.path(), "$mascope")
            )
            message = "{extra[parsed_message]: <60}"
            message_text = f"{record['message']}"

            # tail
            module = record["extra"]["mod"]
            module_styled = self.style(
                module, f"fg {palette[self.runtime.config.color]}"
            )
            path = record["name"]
            func = record["function"]
            func_or_module = "[module]" if func == "<module>" else func
            line = record["line"]
            key = record["extra"]["key"]
            key_span = f"❯ {key}" if key and len(key) > 0 else ""
            tail = f"{module_styled} ❯ {path} ❯ {func_or_module}:{line} {key_span}"
            tail_text = f"{module} ❯ {path} ❯ {func}:{line} {key_span}"

            # highlight grep
            full_text = f"{head_text} {message_text} {tail_text}"
            grep = os.environ.get("MASCOPE_LOGGREP", None)
            match = grep in full_text if grep else highlight.match(full_text)
            tags = ["dim"] if not match else []

            # FORMAT
            fmt = self.style(f" {head} {message} {tail}\n", *tags)

            # TRACEBACKS
            output = io.StringIO()
            console = Console(file=output)
            trace_opt = record["extra"].get("trace", False)
            is_exception = record["exception"] is not None
            trace = None
            if is_exception:
                # pretty print exception traceback
                trace = Traceback.from_exception(*record["exception"])
            elif trace_opt:
                # construct stacktrace without exception
                trace = self.stacktrace(skip_frames=5)  # *
                # * we skip five frames in order to get directly to the
                # logger callsite, i.e. we avoid printing stack frames
                # from this module or loguru.
            if trace:
                # if trace exists, we add it to the message
                console.print("")
                console.print(trace)
                record["extra"]["rich_exception"] = output.getvalue()
                fmt += "{extra[rich_exception]}\n"
            elif rich_exception:
                record["extra"]["rich_exception"] = rich_exception
                fmt += "{extra[rich_exception]}\n"

            # format string
            return fmt

        # return the format function
        return format_record

    def style(self, msg: str, *tags: List[str]) -> str:
        """
        Helper for styling log messages

        :param msg: the message to style
        :param tags: list of tags to apply to the message
        :return: styled message string
        """
        # wrap style tags around msg
        start = ""
        for tag in tags:
            start += f"<{tag}>"
        end = ""
        for tag in reversed(tags):
            end += f"</{tag}>"
        return f"{start}{msg}{end}"

    def status_style(self, status_code: int) -> Callable:
        """
        Construct a status styling function from a status code.

        :param status_code: the status code
        :return: a styling function
        """

        # response
        def response(start):
            return start <= status_code and status_code < start + 100

        # color

        if response(100):
            # informational response
            def status_style(msg):
                return self.style(msg, "blue")
        elif response(200):
            # successful response
            def status_style(msg):
                return self.style(msg, "green")
        elif response(300):
            # redirection response
            def status_style(msg):
                return self.style(msg, "cyan")
        elif response(400):
            # client error response
            def status_style(msg):
                return self.style(msg, "bold", "yellow")
        elif response(500):
            # server error response
            def status_style(msg):
                return self.style(msg, "bold", "red")
        else:
            # other
            def status_style(msg):
                return self.style(msg, "magenta")

        return status_style

    def stacktrace(self, show_locals: bool = False, skip_frames: int = 0) -> Traceback:
        """
        Constructs a pretty stacktrace for situations where
        there is no exception.

        :param show_locals: whether to show local variables in the frame
        :param skip_frames: how many frames to skip in the begining
        :return traceback: a rich Traceback object
        """
        trace = None
        depth = 1
        while True:
            try:
                frame = sys._getframe(depth)
                depth += 1
            except Exception:
                break
            if depth > skip_frames:
                trace = TracebackType(trace, frame, frame.f_lasti, frame.f_lineno)
        exception = Exception("trace for debugging purposes (not a real exception)")
        stack = Traceback.extract(Stacktrace, exception, trace, show_locals=show_locals)
        return Traceback(stack, show_locals=show_locals)

    def query(
        self,
        level: str | None = "info",
        limit: int | None = None,
        grep: str | None = None,
        grep_context=25,
        from_datetime: str = None,
        to_datetime: str | None = None,
        interval: str | None = None,
        mode: RuntimeMode | None = None,
        service: str | None = None,
        json_output: bool = False,
    ):
        """
        Executes a query against dev or prod log files in the active runtime
        env.

        duckdb is used in-memory to injest all log files and filter the
        relevant fields and values.

        :param level: the log level above which to print
        :param limit: print only the `limit` most recent matching lines
        :param grep: a search pattern to filter log messages against
        :param grep_context: the number of rows before and after a `grep` match to include
        :param from_datetime: the start of the time range
        :param to_datetime: end of the time range
        :param interval: the width of the time range, e.g. '60d' or '60 days'
        :param mode: the runtime mode (dev or prod)
        :param service: only logs of one service/module (e.g. "backend")
        :param json_output: print raw NDJSON records instead of pretty lines
        """
        if from_datetime and to_datetime and interval:
            self.runtime.logger.error(
                "runtime.logging.query: cannot use 'from_datetime', 'to_datetime' and 'interval' together"
            )
            return
        if service and not re.fullmatch(r"[A-Za-z0-9_-]+", service):
            self.runtime.logger.error(
                f"runtime.logging.query: invalid service name {service!r}"
            )
            return
        if interval:
            normalized_interval = _normalize_interval(interval)
            if normalized_interval is None:
                self.runtime.logger.error(
                    f"runtime.logging.query: invalid interval {interval!r} - "
                    "give a number with a unit, e.g. '60d', '12h' or '60 days'"
                )
                return
            interval = normalized_interval

        # PREPARE - collect key variables and clauses. User-provided values go
        # through `params` (prepared-statement placeholders), never into the
        # SQL text: a quote in --grep or --from must not break (or inject
        # into) the query.
        pattern = f"*.{service}.log" if service else "*.log"
        log_dir = os.path.join(self.dir, mode or self.runtime.mode)
        log_path = os.path.join(log_dir, pattern)
        level_no = {
            "trace": 5,
            "debug": 10,
            "info": 20,
            "success": 25,
            "warning": 30,
            "error": 40,
            "critical": 50,
        }[level]
        limit_clause = f"LIMIT {int(limit)}" if limit else ""
        params: list = []
        from_clause = to_clause = ""
        if from_datetime:
            from_clause = "AND timestamp >= CAST(? AS TIMESTAMP)"
            params.append(from_datetime)
        if to_datetime:
            to_clause = "AND timestamp <= CAST(? AS TIMESTAMP)"
            params.append(to_datetime)
        if interval:
            if from_clause:
                to_clause = (
                    "AND timestamp <= CAST(? AS TIMESTAMP) + CAST(? AS INTERVAL)"
                )
                params.append(from_datetime)
                params.append(interval)
            elif to_clause:
                # the from-bound params must precede the to-bound one above
                from_clause = (
                    "AND timestamp >= CAST(? AS TIMESTAMP) - CAST(? AS INTERVAL)"
                )
                params = [to_datetime, interval] + params
            else:
                from_clause = (
                    "AND timestamp >= CAST(? AS TIMESTAMP) - CAST(? AS INTERVAL)"
                )
                params = [datetime.datetime.now().isoformat(), interval]

        # COLLECT - resolve the files to read. Rotated days exist only as
        # `.log.zip` archives, invisible to a `*.log` glob - without them a
        # query covers only the days not yet compressed, however wide the
        # requested time range. Rotation may also timestamp-suffix the stem,
        # so both archive shapes are matched.
        if service:
            archive_patterns = [f"*.{service}.log.zip", f"*.{service}.*.log.zip"]
        else:
            archive_patterns = ["*.log.zip"]
        archives = sorted(
            {
                path
                for archive_pattern in archive_patterns
                for path in glob.glob(os.path.join(log_dir, archive_pattern))
            }
        )
        sources = sorted(glob.glob(log_path))
        temp_dir = None
        if archives:
            temp_dir = tempfile.mkdtemp(prefix="mascope-log-query-")
            extracted, skipped = _extract_log_archives(archives, temp_dir)
            if skipped:
                self.runtime.logger.warning(
                    f"runtime.logging.query: skipped {skipped} unreadable log archive(s)"
                )
            if extracted:
                sources += sorted(glob.glob(os.path.join(temp_dir, "*.log")))

        try:
            # BUILD - construct the queries. The source paths come from the
            # runtime's own log dir and the temp dir above (never from user
            # input; the service name is validated), quoted for the SQL
            # literal all the same. ignore_errors skips malformed lines -
            # half-written records and truncated archive members occur in
            # production and must not fail the whole query.
            files_sql = ", ".join(
                "'" + source.replace("'", "''") + "'" for source in sources
            )
            base_query = f"""
                SELECT
                    json_extract(json, '$.record.time.repr')::TIMESTAMPTZ as timestamp,
                    json.record.level.name as level,
                    json.record.level.no as level_no,
                    json.record.extra.status_code as status,
                    json.record.extra.method as method,
                    json.record.message as message,
                    json.record.extra.mod as module,
                    json.record.name as path,
                    json.record.function as func,
                    json.record.line as line,
                    json.record.extra.key as key,
                    json
                FROM read_ndjson_objects([{files_sql}], ignore_errors=true)
                WHERE
                    level_no >= {level_no}
                    {from_clause}
                    {to_clause}
            """
            if not grep:
                # DESC + LIMIT selects the *most recent* N rows; the fetched
                # rows are reversed below so the printout still reads
                # oldest-first.
                query = f"""
                    WITH log AS (
                        {base_query}
                    )
                    SELECT
                      timestamp,
                      level,
                      message,
                      json
                    FROM log
                    ORDER BY log.timestamp DESC
                    {limit_clause}
                """
            elif grep:
                query = f"""
                    WITH log AS (
                        {base_query}
                    ),
                    context AS (
                        SELECT
                            log.*,
                            STRING_AGG(
                                CONCAT_WS(' ',
                                    log.level,
                                    log.status,
                                    log.method,
                                    log.message,
                                    CONCAT_WS(
                                        ' ❯ ',
                                        log.module,
                                        log.path,
                                        CONCAT(log.func, ':', log.line),
                                        log.key
                                    )
                                ),
                                ' '
                            )
                            OVER (
                                ORDER BY log.timestamp ROWS
                                BETWEEN {grep_context} PRECEDING
                                AND {grep_context} FOLLOWING
                            ) as context,
                        FROM log
                        ORDER BY log.timestamp
                    )
                    SELECT
                      timestamp,
                      level,
                      message,
                      json
                    FROM context ctx
                    WHERE ctx.context LIKE '%' || ? || '%'
                    ORDER BY ctx.timestamp DESC
                    {limit_clause}
                """
                params.append(grep)

            # EXECUTE - run the query and print the logs
            if not sources:
                # no log files to read (e.g. a --service that never logged):
                # an empty result, not a crash
                records = []
            else:
                duckdb = _duckdb()
                try:
                    with duckdb.connect() as conn:
                        records = conn.execute(query, params).fetchall()
                except duckdb.IOException:
                    # a file vanishing between the glob and the read (e.g.
                    # rotation mid-query) is an empty result, not a crash
                    records = []
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
        # undo the DESC ordering used for LIMIT: print oldest-first
        records.reverse()
        for (
            timestamp,
            level,
            message,
            raw,
        ) in records:
            if json_output:
                # raw NDJSON pass-through for machine consumers: no loguru
                # re-emission, so no ANSI colors and no re-formatting
                print(raw)
                continue
            override = json.loads(raw)["record"]
            override["time"] = timestamp.isoformat().replace("T", " ")
            # Spoof the logger to pretend to log the original record rather
            # than from here. Level and message come from the decoded record,
            # not the raw JSON string slices, so escape sequences render.
            logger.bind(override=override).log(
                override["level"]["name"], override["message"]
            )
        if not json_output:
            print(
                f"\n\n  Printed {len(records)} lines of logs from {log_path.split('*')[0]}"
            )

    def gc(
        self,
        mode: RuntimeMode,
        before: str | None,
        retain: str | None,
        dryrun: bool = False,
    ) -> None:
        """
        Garbage collect stale or empty log files, either 'before' a specified date
        or excluding a time interval (in days, weeks or months) to 'retain'.

        Rotated `.log.zip` archives are collected alongside live `.log` files:
        they are dated the same way and hold most of what a log dir keeps, so
        sweeping only the live files would leave the retention this command
        reports unenforced.

        :param mode: the runtime mode (dev or prod)
        :param before: the maximum date before which to delete log files
        :param retain: a time interval for which to keep log files
        :param dryrun: don't actually delete anything, just print a preview
        """

        if not mode:
            self.runtime.logger.error(
                "runtime.logging.gc: mode must be specified (dev or prod)"
            )
            return
        if (before and retain) or not (before or retain):
            self.runtime.logger.error(
                "runtime.logging.gc: must specify either before or retain argument (not both)"
            )
            return

        if retain:
            normalized_retain = _normalize_interval(retain)
            if normalized_retain is None:
                self.runtime.logger.error(
                    f"runtime.logging.gc: invalid retain interval {retain!r} - "
                    "give a number with a unit, e.g. '14d' or '2 weeks'"
                )
                return
            with _duckdb().connect() as conn:
                max_date = conn.execute(
                    "SELECT current_date - CAST(? AS INTERVAL)", [normalized_retain]
                ).fetchall()[0][0]
        elif before:
            max_date = datetime.datetime.strptime(before, "%Y-%m-%d")

        total_count = 0
        empty_count = 0
        stale_count = 0
        skip_count = 0

        prefix = "[DRY RUN] " if dryrun else ""

        # Rotated days survive only as `.log.zip` containers, and they are the
        # bulk of what a log dir holds - collecting `*.log` alone leaves a
        # retention window the command reports as enforced but is not. Both
        # shapes carry the same `<YYYY-MM-DD>.` prefix, so the date below reads
        # off either. A zero-byte `.zip` is a rotation that died mid-write and
        # is swept as empty, exactly like a zero-byte `.log`.
        log_dir = os.path.join(self.dir, mode)
        targets = sorted(
            glob.glob(os.path.join(log_dir, "*.log"))
            + glob.glob(os.path.join(log_dir, "*.log.zip"))
        )
        for f in targets:
            raw_date = os.path.split(f)[-1].split(".")[0]
            parsed_date = datetime.datetime.strptime(raw_date, "%Y-%m-%d")
            stale = parsed_date < max_date
            empty = os.stat(f).st_size == 0
            descs = []
            descs.append("stale" if stale else "fresh")
            descs.append("empty" if empty else "non-empty")
            if stale or empty:
                self.runtime.logger.info(f"{prefix} deleting {' & '.join(descs)}: {f}")
                if not dryrun:
                    os.remove(f)
                total_count += 1
                if stale:
                    stale_count += 1
                if empty:
                    empty_count += 1
            else:
                self.runtime.logger.info(f"{prefix} skipping {' & '.join(descs)}: {f}")
                skip_count += 1
        empty_text = "as well as empty files" if empty_count > 0 else ""
        self.runtime.logger.info(
            f"{prefix} garbage collected log files older than {max_date}{empty_text}"
        )
        self.runtime.logger.info(
            f"{prefix} deleted a total of {total_count} files: {stale_count} stale & {empty_count} empty. Skipped {skip_count} files."
        )
