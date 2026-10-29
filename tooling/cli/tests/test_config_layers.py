"""
Tests for the config layer report, `mascope dev config` and `mascope prod config`.

`mascope_cli.config_layers` reads the three config layers the backend reads
and says, for every setting, the layer that set it and whether it departs
from the config model's default. The prod command runs that module inside the
backend container through `docker exec`, mocked here, so the suite needs no
Docker.
"""

import ast
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from mascope_cli import config_layers
from mascope_cli.main import app


# The prod package re-exports a `main` function that shadows the module of
# the same name, so import the module explicitly.
prod_main = importlib.import_module("mascope_cli.cmd.prod.main")

BASE = """
[meta]
api_port = 8090

[backend]
color = "green"
run = "uv run mascope-backend run api-server"
composite_scan_streams = true
require_device_tokens = true

[backend.database]
pool_size = 3
pool_timeout = 120

[backend.redis]
image = "redis:7-alpine"

[file-converter]
raw_threads = 1

[frontend]
sample_table_defaults = { columns = ["sample_item_name"], sort_field = "index", sort_order = -1 }

[file-agent]
source = './agents/file'
host = 'localhost'
access_token = ''
"""

MODE = """
[backend.database]
pool_size = 3
max_connections = 200
"""

ENV = """
[backend]
calibration_quality_gate = "enforce"
tus_max_upload_gb = 40
pool_sise = 9

[backend.database]
pool_timeout = 120

[file-agent]
host = 'https://server-a.invalid'
access_token = 'tok-do-not-print'

[backnd]
run = "a section the model lacks"
"""


def _home(root: Path, env: str | None = ENV, mode: str = "prod") -> dict:
    """A runtime home with the three layers; returns their paths."""
    (root / "base.mascope.toml").write_text(BASE)
    (root / f"{mode}.mascope.toml").write_text(MODE)
    paths = config_layers.layer_paths(str(root), "site", mode)
    if env is not None:
        Path(paths["env"]).parent.mkdir(parents=True)
        Path(paths["env"]).write_text(env)
    return paths


def _rows(report: dict) -> dict[tuple[str, str], dict]:
    return {(row["section"], row["key"]): row for row in report["rows"]}


@pytest.fixture
def report(tmp_path):
    return config_layers.collect(_home(tmp_path))


# --- collect: which layer set each setting ---


def test_each_setting_is_credited_to_the_last_layer_that_sets_it(report):
    rows = _rows(report)

    assert rows[("backend", "calibration_quality_gate")]["layer"] == "env"
    assert rows[("backend", "calibration_quality_gate")]["value"] == "enforce"
    assert rows[("backend", "calibration_quality_gate")]["default"] == "warn"
    assert rows[("backend.database", "max_connections")]["layer"] == "mode"
    assert rows[("file_converter", "raw_threads")]["layer"] == "base"


def test_a_layer_that_repeats_a_value_still_owns_it(report):
    # The env pins pool_timeout to the value base already gives it: the pin
    # stays when a later release changes base, so it is the env's.
    rows = _rows(report)

    assert rows[("backend.database", "pool_timeout")]["layer"] == "env"
    assert rows[("backend.database", "pool_size")]["layer"] == "mode"


def test_base_is_a_layer_of_its_own_not_the_default(report):
    rows = _rows(report)

    raw_threads = rows[("file_converter", "raw_threads")]
    assert (raw_threads["value"], raw_threads["default"]) == (1, 2)
    assert raw_threads["differs"]
    # Set by base to the model's own default: no departure.
    streams = rows[("backend", "composite_scan_streams")]
    assert streams["layer"] == "base"
    assert not streams["differs"]


def test_an_unset_setting_reads_as_the_models_default(report):
    method_binding = _rows(report)[("backend", "method_binding")]

    assert method_binding["layer"] == "default"
    assert method_binding["value"] == method_binding["default"] == "shadow"
    assert not method_binding["differs"]


def test_a_nested_table_is_walked_key_by_key(report):
    rows = _rows(report)

    sort_order = rows[("frontend.sample_table_defaults", "sort_order")]
    assert (sort_order["value"], sort_order["layer"], sort_order["differs"]) == (
        -1,
        "base",
        True,
    )
    sort_field = rows[("frontend.sample_table_defaults", "sort_field")]
    assert not sort_field["differs"]


def test_a_key_the_model_lacks_is_reported_as_unknown(report):
    typo = _rows(report)[("backend", "pool_sise")]

    assert not typo["known"]
    assert typo["differs"]
    assert typo["layer"] == "env"


def test_an_unknown_key_departs_whichever_layer_writes_it(report):
    rows = _rows(report)

    image = rows[("backend.redis", "image")]
    assert (image["known"], image["layer"], image["differs"]) == (False, "base", True)
    section = rows[("backnd", "run")]
    assert (section["known"], section["layer"]) == (False, "env")
    # A launcher key's name does not hide it when the model does not know it.
    assert section in config_layers.select(report)


def test_a_setting_in_its_old_section_is_credited_where_it_now_lives(report):
    rows = _rows(report)

    assert ("backend", "tus_max_upload_gb") not in rows
    moved = rows[("meta", "tus_max_upload_gb")]
    assert (moved["value"], moved["layer"], moved["differs"]) == (40, "env", True)


def test_a_setting_without_a_default_departs_only_above_base(report):
    rows = _rows(report)

    assert not rows[("file_agent", "source")]["differs"]
    host = rows[("file_agent", "host")]
    assert (host["layer"], host["has_default"], host["differs"]) == (
        "env",
        False,
        True,
    )


def test_a_layer_that_sets_the_default_back_over_base_departs(tmp_path):
    # Base runs one raw thread against the model's two; a server whose env
    # toml asks for two again runs differently from the others.
    report = config_layers.collect(
        _home(tmp_path, env="[file-converter]\nraw_threads = 2\n")
    )

    raw_threads = _rows(report)[("file_converter", "raw_threads")]
    assert (raw_threads["value"], raw_threads["default"]) == (2, 2)
    assert raw_threads["layer"] == "env"
    assert raw_threads["overrides"] == {"layer": "base", "value": 1}
    assert raw_threads["differs"]
    assert raw_threads in config_layers.select(report)
    line = next(
        line
        for line in config_layers.format_text(report).splitlines()
        if "raw_threads" in line
    )
    assert line.split() == [
        "raw_threads",
        "=",
        "2",
        "env",
        "default;",
        "over",
        "base",
        "1",
    ]


def test_a_layer_that_repeats_the_value_below_overrides_nothing(report):
    pool_timeout = _rows(report)[("backend.database", "pool_timeout")]

    assert pool_timeout["overrides"] is None
    # A setting no lower layer writes has the default as its baseline.
    assert _rows(report)[("backend", "calibration_quality_gate")]["overrides"] is None


def test_the_mode_layer_overrides_base_too(tmp_path):
    (tmp_path / "base.mascope.toml").write_text("[file-converter]\nraw_threads = 1\n")
    (tmp_path / "prod.mascope.toml").write_text("[file-converter]\nraw_threads = 2\n")

    report = config_layers.collect(
        config_layers.layer_paths(str(tmp_path), "s", "prod")
    )

    raw_threads = _rows(report)[("file_converter", "raw_threads")]
    assert raw_threads["overrides"] == {"layer": "base", "value": 1}
    assert raw_threads["differs"]


def test_each_module_is_named_as_the_loader_names_it(report):
    rows = _rows(report)

    for section, name in (("backend", "backend"), ("file_converter", "file-converter")):
        row = rows[(section, "name")]
        assert (row["value"], row["default"], row["differs"]) == (name, name, False)
    every = config_layers.format_text(report, show_all=True)
    assert "name = null" not in every
    # meta is no module: the loader's name has no field to land in.
    assert ("meta", "name") not in rows


def test_a_date_in_a_layer_prints_as_its_iso_form(tmp_path):
    report = config_layers.collect(
        _home(tmp_path, env="[backend]\nrollout_started = 2026-10-01T00:00:00\n")
    )

    text = config_layers.format_text(report)

    assert 'rollout_started = "2026-10-01 00:00:00"' in text


def test_a_section_no_layer_writes_is_left_out(report):
    assert not any(row["section"] == "tof_agent" for row in report["rows"])


def test_a_missing_env_layer_is_reported_not_found(tmp_path):
    report = config_layers.collect(_home(tmp_path, env=None))

    found = {layer["layer"]: layer["found"] for layer in report["layers"]}
    assert found == {"base": True, "mode": True, "env": False}
    assert _rows(report)[("backend", "calibration_quality_gate")]["layer"] == (
        "default"
    )


# --- credentials ---


def test_a_credential_is_never_in_the_report(report):
    token = _rows(report)[("file_agent", "access_token")]

    assert token["value"] == config_layers.HIDDEN
    text = config_layers.format_text(report, show_all=True)
    assert "tok-do-not-print" not in text
    assert "tok-do-not-print" not in json.dumps(report)
    assert "tok-do-not-print" not in config_layers.format_json(report, show_all=True)


NESTED_CREDENTIALS = """
[file-agent]
auth = { access_token = "nested-token", hosts = [{ password = "in-a-list" }] }
signing_secret_key = "a-secret-key"
cloud_credentials = "some-credentials"
private_key = "a-private-key"

[file-agent.extra]
password = "under-an-unknown-table"

[backend.databse]
password = "under-a-misspelt-section"
"""


def test_a_credential_nested_in_a_table_is_never_in_the_report(tmp_path):
    report = config_layers.collect(_home(tmp_path, env=NESTED_CREDENTIALS))
    rows = _rows(report)

    assert rows[("file_agent", "auth")]["value"] == {
        "access_token": config_layers.HIDDEN,
        "hosts": [{"password": config_layers.HIDDEN}],
    }
    assert rows[("backend", "databse")]["value"] == {"password": config_layers.HIDDEN}
    outputs = (
        json.dumps(report),
        config_layers.format_text(report, show_all=True),
        config_layers.format_json(report, show_all=True),
    )
    for secret in (
        "nested-token",
        "in-a-list",
        "a-secret-key",
        "some-credentials",
        "a-private-key",
        "under-an-unknown-table",
        "under-a-misspelt-section",
    ):
        assert not any(secret in output for output in outputs), secret


def test_the_credential_a_layer_overrides_is_hidden_too(tmp_path):
    (tmp_path / "base.mascope.toml").write_text(
        "[file-agent]\nsource = '.'\nhost = 'h'\naccess_token = 'base-token'\n"
    )
    paths = config_layers.layer_paths(str(tmp_path), "site", "prod")
    Path(paths["env"]).parent.mkdir(parents=True)
    Path(paths["env"]).write_text("[file-agent]\naccess_token = 'env-token'\n")

    report = config_layers.collect(paths)

    token = _rows(report)[("file_agent", "access_token")]
    assert token["overrides"] == {"layer": "base", "value": config_layers.HIDDEN}
    assert "base-token" not in json.dumps(report)


def test_a_policy_switch_named_after_tokens_is_shown(report):
    assert _rows(report)[("backend", "require_device_tokens")]["value"] is True


def test_an_empty_credential_reads_as_empty(tmp_path):
    report = config_layers.collect(_home(tmp_path, env=None))

    assert _rows(report)[("file_agent", "access_token")]["value"] == ""


# --- select and format ---


def test_the_overrides_view_leaves_out_defaults_and_launcher_keys(report):
    shown = {(row["section"], row["key"]) for row in config_layers.select(report)}

    assert ("backend", "calibration_quality_gate") in shown
    assert ("backend", "pool_sise") in shown
    assert ("backend", "method_binding") not in shown
    assert ("backend", "color") not in shown
    assert ("backend", "run") not in shown
    every = {
        (row["section"], row["key"])
        for row in config_layers.select(report, show_all=True)
    }
    assert {("backend", "method_binding"), ("backend", "color")} <= every


def test_the_text_reads_like_the_toml_files(report):
    text = config_layers.format_text(report, title="Env 'site'")
    lines = text.splitlines()

    assert lines[0] == "Env 'site'"
    assert "[file-converter]" in lines
    assert lines.index("[backend]") < lines.index("[backend.database]")
    gate = next(line for line in lines if "calibration_quality_gate" in line)
    assert gate.split() == [
        "calibration_quality_gate",
        "=",
        '"enforce"',
        "env",
        "default",
        '"warn"',
    ]
    typo = next(line for line in lines if "pool_sise" in line)
    assert "unknown key" in typo
    assert "method_binding" not in text
    assert "method_binding" in config_layers.format_text(report, show_all=True)


def test_a_deployment_on_every_default_says_so(tmp_path):
    (tmp_path / "base.mascope.toml").write_text("[meta]\napi_port = 8090\n")
    paths = config_layers.layer_paths(str(tmp_path), "site", "prod")

    text = config_layers.format_text(config_layers.collect(paths))

    assert "Every setting has its model default." in text


# --- the module runs on its own, as the prod command runs it in the image ---


def test_the_module_imports_only_the_stdlib_and_the_runtime():
    # The prod command pipes the file into the image's Python, where the
    # CLI may be another release or absent: it must stand on its own.
    tree = ast.parse(Path(config_layers.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add(node.module.split(".")[0])

    allowed = set(sys.stdlib_module_names) | {"mascope_runtime"}
    assert imported <= allowed


def test_the_module_piped_into_python_prints_the_report(tmp_path):
    _home(tmp_path)
    source = Path(config_layers.__file__).read_text(encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-",
            "--home",
            str(tmp_path),
            "--env",
            "site",
            "--mode",
            "prod",
        ],
        input=source,
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    )

    report = json.loads(result.stdout)
    assert _rows(report)[("backend", "calibration_quality_gate")]["layer"] == "env"
    assert "tok-do-not-print" not in result.stdout


# --- mascope dev config ---


@pytest.fixture
def dev_env_layer(mascope_home):
    path = mascope_home / ".runtime" / "env" / "default" / "dev.mascope.toml"
    path.write_text('[backend]\ncalibration_quality_gate = "enforce"\n')
    yield path
    path.unlink()


def test_dev_config_reports_the_env_layer(cli_runner, dev_env_layer):
    result = cli_runner.invoke(app, ["dev", "config"])

    assert result.exit_code == 0, result.output
    assert str(dev_env_layer) in result.stdout
    gate = next(
        line
        for line in result.stdout.splitlines()
        if "calibration_quality_gate" in line
    )
    assert gate.split()[3] == "env"


def test_dev_config_json_lists_every_setting_with_all(cli_runner, dev_env_layer):
    result = cli_runner.invoke(app, ["dev", "config", "--json", "--all"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout[result.stdout.index("{") :])
    rows = _rows(report)
    assert rows[("backend", "calibration_quality_gate")]["layer"] == "env"
    # base.mascope.toml writes the feature settings at their defaults: the
    # row names base as the layer, but it departs from nothing.
    assert rows[("backend", "method_binding")]["layer"] == "base"
    assert rows[("backend", "method_binding")]["differs"] is False
    # A setting unset by default has no line in any layer.
    assert rows[("backend", "deployment_id")]["layer"] == "default"


# --- mascope prod config ---


class _Exec:
    """Stands in for `docker exec`, running the piped module on the host."""

    def __init__(self, home: Path):
        self.home = home
        self.calls = []
        self.returncode = 0

    def __call__(self, cmd, **kwargs):
        if cmd[:2] != ["docker", "exec"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        self.calls.append({"cmd": cmd, **kwargs})
        if self.returncode:
            return subprocess.CompletedProcess(
                cmd, self.returncode, "", "no such container"
            )
        report = config_layers.collect(
            config_layers.layer_paths(str(self.home), "default", "prod")
        )
        return subprocess.CompletedProcess(cmd, 0, json.dumps(report) + "\n", "")


@pytest.fixture
def image(tmp_path, monkeypatch, mascope_home):
    """An image home holding the same shipped layers as the host's."""
    for name in ("base.mascope.toml", "prod.mascope.toml"):
        (tmp_path / name).write_bytes((mascope_home / name).read_bytes())
    fake = _Exec(tmp_path)
    monkeypatch.setattr(prod_main.subprocess, "run", fake)
    return fake


def test_prod_config_reads_the_layers_inside_the_backend_container(cli_runner, image):
    result = cli_runner.invoke(app, ["prod", "config"])

    assert result.exit_code == 0, result.output
    (call,) = image.calls
    assert call["cmd"] == [
        "docker",
        "exec",
        "-i",
        "mascope_prod_backend",
        config_layers.IMAGE_PYTHON,
        "-",
        "--home",
        config_layers.IMAGE_HOME,
        "--env",
        "default",
        "--mode",
        "prod",
    ]
    assert call["input"] == Path(config_layers.__file__).read_text(encoding="utf-8")
    assert "[backend.database]" in result.stdout
    assert "differs from the image's copy" not in result.output


def test_prod_config_warns_when_the_hosts_copy_is_not_the_images(cli_runner, image):
    (image.home / "base.mascope.toml").write_text("[meta]\napi_port = 8091\n")

    result = cli_runner.invoke(app, ["prod", "config", "--json"])

    assert result.exit_code == 0, result.output
    assert "base.mascope.toml differs from the image's copy" in result.stderr
    assert "prod.mascope.toml differs" not in result.stderr
    # The host copy still feeds compose: the warning says what it sets and
    # how to bring it back in line.
    warning = " ".join(result.stderr.split())
    assert "still passes the host copy's settings to compose" in warning
    assert "[backend.database]" in warning
    assert "[frontend]" in warning
    assert "`mascope init --force` brings the host copy in line" in warning
    report = json.loads(result.stdout[result.stdout.index("{") :])
    assert _rows(report)[("meta", "api_port")]["value"] == 8091


def test_prod_config_fails_when_the_container_does_not_answer(cli_runner, image):
    image.returncode = 1

    result = cli_runner.invoke(app, ["prod", "config"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Could not read the config" in result.stderr


def test_prod_config_reads_the_active_env(cli_runner, image, monkeypatch):
    monkeypatch.setattr(prod_main.runtime.env, "name", "site")

    result = cli_runner.invoke(app, ["prod", "config"])

    assert result.exit_code == 0, result.output
    (call,) = image.calls
    assert call["cmd"][call["cmd"].index("--env") + 1] == "site"
    assert result.stdout.count('Env "site" in prod mode') == 1


def test_prod_config_fails_when_docker_cannot_run(cli_runner, image, monkeypatch):
    def no_docker(cmd, **kwargs):
        if cmd[:2] == ["docker", "exec"]:
            raise FileNotFoundError("docker")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(prod_main.subprocess, "run", no_docker)

    result = cli_runner.invoke(app, ["prod", "config"])

    assert result.exit_code == 1
    assert "Could not read the config" in result.stderr
