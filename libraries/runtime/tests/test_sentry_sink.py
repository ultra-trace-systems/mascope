"""
Tests for the optional GlitchTip/Sentry error sink (`mascope_runtime.logging`).

The sink is entirely gated on ``MASCOPE_SENTRY_DSN`` and forwards WARNING+ loguru
records to GlitchTip via ``sentry-sdk``. These tests inject a fake ``sentry_sdk``
into ``sys.modules`` so they run without the optional ``sentry`` extra installed,
and cover: the default-OFF gate, init wiring/idempotency, the WARNING+ capture
paths (message vs exception), how each is grouped (call-site fingerprint, bound
override, log entry beside an exception), the SDK-loop guard, never-raise
behavior, and the CLI exclusion in ``RuntimeLogging.configure``. One test drives a real loguru
logger end to end.
"""

import logging as std_logging
import sys
import types

import pytest

import mascope_runtime.logging as rl


# --- fake sentry_sdk -------------------------------------------------------


class _FakeScope:
    def __init__(self):
        self.level = None
        self.tags = {}
        self.extras = {}
        self.fingerprint = None
        self.event_processors = []

    def set_level(self, value):
        self.level = value

    def set_tag(self, key, value):
        self.tags[key] = value

    def set_extra(self, key, value):
        self.extras[key] = value

    def add_event_processor(self, func):
        self.event_processors.append(func)

    def process(self, event):
        """Run the scope's processors over an event, as the SDK would."""
        for func in self.event_processors:
            event = func(event, {})
        return event

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_fake_sentry():
    """Build a fake sentry_sdk (+ integration submodules) and register it."""
    mod = types.ModuleType("sentry_sdk")
    mod.init_calls = []
    mod.captured = []
    mod.last_scope = None

    def init(**kwargs):
        mod.init_calls.append(kwargs)

    def new_scope():
        mod.last_scope = _FakeScope()
        return mod.last_scope

    def capture_exception(error=None, **kwargs):
        mod.captured.append(("exc", error))

    def capture_message(message, level=None, **kwargs):
        mod.captured.append(("msg", message, level))

    mod.init = init
    mod.new_scope = new_scope
    mod.capture_exception = capture_exception
    mod.capture_message = capture_message

    sys.modules["sentry_sdk"] = mod
    sys.modules["sentry_sdk.integrations"] = types.ModuleType("sentry_sdk.integrations")
    for sub, cls in (
        ("fastapi", "FastApiIntegration"),
        ("starlette", "StarletteIntegration"),
        ("loguru", "LoguruIntegration"),
    ):
        submod = types.ModuleType(f"sentry_sdk.integrations.{sub}")
        setattr(submod, cls, type(cls, (), {"__init__": lambda self, *a, **k: None}))
        sys.modules[f"sentry_sdk.integrations.{sub}"] = submod
    return mod


@pytest.fixture
def fake_sentry(monkeypatch):
    """Inject a fake sentry_sdk and reset the one-time init guard."""
    monkeypatch.setattr(rl, "_sentry_ready", False)
    mod = _install_fake_sentry()
    yield mod
    for name in (
        "sentry_sdk",
        "sentry_sdk.integrations",
        "sentry_sdk.integrations.fastapi",
        "sentry_sdk.integrations.starlette",
        "sentry_sdk.integrations.loguru",
    ):
        sys.modules.pop(name, None)


# --- fake loguru record ----------------------------------------------------


class _Level:
    def __init__(self, name):
        self.name = name


class _Exc:
    def __init__(self, type_, value, traceback):
        self.type = type_
        self.value = value
        self.traceback = traceback


class _Message:
    def __init__(self, record):
        self.record = record


def _msg(
    name="app.module",
    level="ERROR",
    message="boom",
    exc=None,
    function="work",
    line=42,
    extra=None,
):
    return _Message(
        {
            "name": name,
            "level": _Level(level),
            "message": message,
            "exception": exc,
            "function": function,
            "line": line,
            "extra": extra or {},
        }
    )


# --- _init_sentry ----------------------------------------------------------


def test_init_off_by_default(monkeypatch):
    monkeypatch.delenv("MASCOPE_SENTRY_DSN", raising=False)
    monkeypatch.setattr(rl, "_sentry_ready", False)
    assert rl._init_sentry("prod", "v1.0.0") is False


def test_init_enables_with_dsn(monkeypatch, fake_sentry):
    monkeypatch.setenv("MASCOPE_SENTRY_DSN", "http://key@host:8000/1")
    assert rl._init_sentry("prod", "v1.2.3") is True

    assert len(fake_sentry.init_calls) == 1
    call = fake_sentry.init_calls[0]
    assert call["dsn"] == "http://key@host:8000/1"
    assert call["environment"] == "prod"
    assert call["release"] == "v1.2.3"
    assert call["traces_sample_rate"] == 0.0
    assert call["send_default_pii"] is False


def test_init_is_idempotent(monkeypatch, fake_sentry):
    monkeypatch.setenv("MASCOPE_SENTRY_DSN", "http://key@host:8000/1")
    assert rl._init_sentry("prod", None) is True
    assert rl._init_sentry("prod", None) is True
    assert len(fake_sentry.init_calls) == 1  # not re-initialized


def test_init_missing_sdk_returns_false(monkeypatch):
    monkeypatch.setenv("MASCOPE_SENTRY_DSN", "http://key@host:8000/1")
    monkeypatch.setattr(rl, "_sentry_ready", False)
    # sys.modules[name] = None makes `import name` raise ImportError.
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    assert rl._init_sentry("prod", None) is False


def test_init_server_name_from_env(monkeypatch, fake_sentry):
    monkeypatch.setenv("MASCOPE_SENTRY_DSN", "http://key@host:8000/1")
    monkeypatch.setenv("MASCOPE_ENV", "site1")
    assert rl._init_sentry("prod", None) is True
    assert fake_sentry.init_calls[0]["server_name"] == "site1"


def test_init_traces_off_by_default(monkeypatch, fake_sentry):
    monkeypatch.setenv("MASCOPE_SENTRY_DSN", "http://key@host:8000/1")
    monkeypatch.delenv("MASCOPE_SENTRY_TRACES_RATE", raising=False)
    assert rl._init_sentry("prod", None) is True
    assert fake_sentry.init_calls[0]["traces_sample_rate"] == 0.0


def test_init_traces_rate_from_env(monkeypatch, fake_sentry):
    monkeypatch.setenv("MASCOPE_SENTRY_DSN", "http://key@host:8000/1")
    monkeypatch.setenv("MASCOPE_SENTRY_TRACES_RATE", "0.1")
    assert rl._init_sentry("prod", None) is True
    assert fake_sentry.init_calls[0]["traces_sample_rate"] == 0.1


@pytest.mark.parametrize("raw", ["banana", "1.5", "-0.1", "nan", ""])
def test_traces_rate_rejects_bad_values(monkeypatch, raw):
    """Anything but a number in [0, 1] keeps tracing off (and must not raise)."""
    monkeypatch.setenv("MASCOPE_SENTRY_TRACES_RATE", raw)
    assert rl._traces_sample_rate() == 0.0


def test_init_server_name_falls_back_to_hostname(monkeypatch, fake_sentry):
    # No MASCOPE_ENV -> pass None so the SDK falls back to the hostname.
    monkeypatch.setenv("MASCOPE_SENTRY_DSN", "http://key@host:8000/1")
    monkeypatch.delenv("MASCOPE_ENV", raising=False)
    assert rl._init_sentry("prod", None) is True
    assert fake_sentry.init_calls[0]["server_name"] is None


# --- _sentry_sink ----------------------------------------------------------


def test_sink_captures_exception(fake_sentry):
    err = ValueError("nope")
    rl._sentry_sink(_msg(level="ERROR", exc=_Exc(ValueError, err, None)))

    assert fake_sentry.captured == [("exc", (ValueError, err, None))]
    assert fake_sentry.last_scope.level == "error"
    assert fake_sentry.last_scope.tags["log_level"] == "ERROR"
    assert fake_sentry.last_scope.tags["logger"] == "app.module"


def test_sink_keeps_the_log_line_beside_the_exception(fake_sentry):
    """The line names what failed - a file, a batch - and the exception alone
    rarely does; dropping it left raw file failures unattributable."""
    err = OSError(22, "Invalid argument")
    rl._sentry_sink(
        _msg(
            level="ERROR",
            message="Failed to process file run_042.raw (1048576 bytes)",
            exc=_Exc(OSError, err, None),
        )
    )

    assert fake_sentry.captured == [("exc", (OSError, err, None))]
    # As the event's log entry, which monitoring shows beside the exception
    # without letting it change the issue's title or grouping.
    event = fake_sentry.last_scope.process({"exception": {"values": []}})
    assert event["logentry"] == {
        "formatted": "Failed to process file run_042.raw (1048576 bytes)"
    }
    assert event["exception"] == {"values": []}


def test_sink_leaves_exception_grouping_to_the_exception(fake_sentry):
    """Exceptions already group by type and location; a call-site fingerprint
    would merge every exception one handler catches into one issue."""
    err = ValueError("nope")
    rl._sentry_sink(_msg(level="ERROR", exc=_Exc(ValueError, err, None)))
    assert fake_sentry.last_scope.fingerprint is None


def test_sink_captures_message_without_exception(fake_sentry):
    rl._sentry_sink(_msg(level="WARNING", message="disk almost full", exc=None))

    assert fake_sentry.captured == [("msg", "disk almost full", None)]
    assert fake_sentry.last_scope.level == "warning"


def test_sink_groups_messages_by_call_site(fake_sentry):
    """Warnings that embed a path or id group into one issue per logging call,
    not one per entity they name."""
    for path in ("/streams/a.raw", "/streams/b.raw"):
        rl._sentry_sink(
            _msg(
                name="mascope_backend.file_converter.base_processor",
                level="WARNING",
                message=f"worker died holding {path}",
                function="requeue_inflight",
                line=118,
            )
        )
        assert fake_sentry.last_scope.fingerprint == [
            "mascope_backend.file_converter.base_processor:requeue_inflight:118"
        ]

    assert [c[1] for c in fake_sentry.captured] == [
        "worker died holding /streams/a.raw",
        "worker died holding /streams/b.raw",
    ]


def test_sink_separates_call_sites_in_one_function(fake_sentry):
    rl._sentry_sink(_msg(level="WARNING", function="work", line=10))
    first = fake_sentry.last_scope.fingerprint
    rl._sentry_sink(_msg(level="WARNING", function="work", line=20))
    assert fake_sentry.last_scope.fingerprint != first


def test_sink_honours_a_bound_fingerprint(fake_sentry):
    """A call site grouped per entity on purpose keeps its own key."""
    rl._sentry_sink(
        _msg(
            level="WARNING",
            message="drift on instrument X",
            extra={rl.SENTRY_FINGERPRINT: ["drift:X"]},
        )
    )
    assert fake_sentry.last_scope.fingerprint == ["drift:X"]


def test_sink_bound_default_restores_text_grouping(fake_sentry):
    rl._sentry_sink(
        _msg(level="WARNING", extra={rl.SENTRY_FINGERPRINT: ["{{ default }}"]})
    )
    assert fake_sentry.last_scope.fingerprint == ["{{ default }}"]


@pytest.mark.parametrize(
    "bound, expected",
    [
        ("drift:X", ["drift:X"]),  # one part, not one per character
        (("drift", 7), ["drift", "7"]),
        (7, ["7"]),
    ],
)
def test_sink_normalizes_a_bound_fingerprint(fake_sentry, bound, expected):
    rl._sentry_sink(_msg(level="WARNING", extra={rl.SENTRY_FINGERPRINT: bound}))
    assert fake_sentry.last_scope.fingerprint == expected


def test_sink_bound_fingerprint_applies_to_exceptions(fake_sentry):
    err = ValueError("nope")
    rl._sentry_sink(
        _msg(
            level="ERROR",
            exc=_Exc(ValueError, err, None),
            extra={rl.SENTRY_FINGERPRINT: ["per-batch:7"]},
        )
    )
    assert fake_sentry.captured == [("exc", (ValueError, err, None))]
    assert fake_sentry.last_scope.fingerprint == ["per-batch:7"]


def test_sink_maps_critical_to_fatal(fake_sentry):
    rl._sentry_sink(_msg(level="CRITICAL", exc=None))
    assert fake_sentry.last_scope.level == "fatal"


@pytest.mark.parametrize("name", ["sentry_sdk.errors", "urllib3.connectionpool"])
def test_sink_loop_guard_skips_sdk_records(fake_sentry, name):
    rl._sentry_sink(
        _msg(name=name, level="ERROR", exc=_Exc(ValueError, ValueError(), None))
    )
    assert fake_sentry.captured == []


def test_sink_never_raises(fake_sentry):
    def _boom(*a, **k):
        raise RuntimeError("transport down")

    fake_sentry.capture_message = _boom
    fake_sentry.capture_exception = _boom
    # Must swallow the transport error rather than propagate out of the sink.
    rl._sentry_sink(_msg(level="ERROR", message="x", exc=None))


# --- configure() gating -----------------------------------------------------
# The sink handler is appended for server modules only. The CLI is excluded:
# its WARNING+ records are user-facing terminal output, and a DSN exported in
# the shell env must not turn them into GlitchTip events.


class _FakeModuleConfig:
    def __init__(self, log_path):
        self.log_path = log_path
        self.log_level = "info"


class _FakeModule:
    def __init__(self, name, log_path):
        self.name = name
        self.config = _FakeModuleConfig(log_path)


class _FakeEnv:
    def __init__(self, base):
        self._base = base

    def path(self):
        return self._base


class _FakeRuntimeConfig:
    color = "blue"


class _FakeRuntime:
    """Just enough Runtime for RuntimeLogging.configure() and its formatter."""

    def __init__(self, name, base):
        self._base = base
        self.module = _FakeModule(name, base)
        self.mode = "dev"
        self.version = "0.0.0"
        self.config = _FakeRuntimeConfig()
        self.env = _FakeEnv(base)

    def path(self, *args):
        return self._base


@pytest.fixture
def sink_events(monkeypatch):
    """Route the sentry sink to a recorder and force _init_sentry to succeed."""
    events = []
    monkeypatch.setattr(rl, "_init_sentry", lambda **kwargs: True)
    monkeypatch.setattr(rl, "_sentry_sink", lambda message: events.append(message))
    root = std_logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield events
    rl.logger.remove()
    # configure() replaces the stdlib root handlers (InterceptHandler);
    # restore them so later tests see an unmodified logging setup
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def test_configure_skips_sentry_for_cli(tmp_path, monkeypatch, sink_events):
    init_calls = []
    monkeypatch.setattr(
        rl, "_init_sentry", lambda **kwargs: init_calls.append(kwargs) or True
    )

    logger = rl.RuntimeLogging(_FakeRuntime("cli", str(tmp_path))).configure()
    logger.warning("routine CLI warning")

    assert init_calls == []  # the SDK is never even initialized for the CLI
    assert sink_events == []


def test_configure_adds_sentry_sink_for_server_modules(tmp_path, sink_events):
    logger = rl.RuntimeLogging(_FakeRuntime("backend", str(tmp_path))).configure()
    logger.warning("server warning")
    logger.info("below threshold")

    assert [m.record["message"] for m in sink_events] == ["server warning"]


def test_configure_bridges_stdlib_logging(tmp_path, sink_events):
    """WARNING+ records from logging.getLogger must reach the sink too."""
    rl.RuntimeLogging(_FakeRuntime("backend", str(tmp_path))).configure()

    stdlib_logger = std_logging.getLogger("some.thirdparty")
    stdlib_logger.warning("stdlib warning")
    stdlib_logger.info("stdlib info below threshold")

    assert [m.record["message"] for m in sink_events] == ["stdlib warning"]


# --- end-to-end through a real loguru logger -------------------------------


def test_sink_via_real_loguru(fake_sentry):
    from loguru import logger

    sink_id = logger.add(rl._sentry_sink, level="WARNING", enqueue=False, catch=False)
    try:
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("work failed")  # -> capture_exception (has traceback)
        logger.warning("plain warning")  # -> capture_message
        logger.info("ignored below threshold")  # -> nothing (INFO < WARNING)
    finally:
        logger.remove(sink_id)

    kinds = [c[0] for c in fake_sentry.captured]
    assert kinds == ["exc", "msg"]


def test_sink_via_real_loguru_reads_call_site_and_binding(fake_sentry):
    """The record keys the sink reads exist on real loguru records."""
    import inspect

    from loguru import logger

    scopes = []
    new_scope = fake_sentry.new_scope

    def _recording_new_scope():
        scopes.append(new_scope())
        return scopes[-1]

    fake_sentry.new_scope = _recording_new_scope
    sink_id = logger.add(rl._sentry_sink, level="WARNING", enqueue=False, catch=False)
    try:
        line = inspect.currentframe().f_lineno + 1
        logger.warning("unbound")
        logger.bind(**{rl.SENTRY_FINGERPRINT: ["entity:1"]}).warning("bound")
    finally:
        logger.remove(sink_id)

    function = "test_sink_via_real_loguru_reads_call_site_and_binding"
    assert scopes[0].fingerprint == [f"{__name__}:{function}:{line}"]
    assert scopes[1].fingerprint == ["entity:1"]
