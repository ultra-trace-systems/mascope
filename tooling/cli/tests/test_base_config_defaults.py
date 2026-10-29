"""
The feature settings of base.mascope.toml are the model's defaults.

Every feature setting of `[meta]` and `[backend]` with a default is a live line
in the base layer holding exactly that default, so checking a release's values
means reading the live lines and nothing else. A setting unset by default has
no live line, and its example sits in the EXAMPLES block at the end of the
file, where it cannot be taken for a default. These tests read the copy
`mascope init` writes, which `test_package_data.py` holds identical to the
repo root one.
"""

import re
import shutil
import tomllib
from pathlib import Path

import pytest

import mascope_cli
from mascope_runtime.config import (
    BackendConfig,
    FileAgentConfig,
    FrontendConfig,
    MetaConfig,
    ModuleConfig,
)
from mascope_runtime.main import Runtime


DATA_DIR = Path(mascope_cli.__file__).resolve().parent / "data"
BASE = DATA_DIR / "base.mascope.toml"
EXAMPLES_MARKER = "# EXAMPLES - NOT DEFAULTS"

# Fields that are not feature settings: the env's identity and paths, and the
# backend's infrastructure, which the base layer and prod.mascope.toml set on
# purpose rather than as a copy of a default.
_NOT_FEATURES = {
    "meta": {"log_level", "description", "api_port", "filestore"},
    "backend": {"container_name", "database", "filestreams", "redis", "workers"},
}

_MODELS = {
    "meta": MetaConfig,
    "backend": BackendConfig,
    "frontend": FrontendConfig,
    "file-agent": FileAgentConfig,
}


def _feature_fields(section: str) -> dict:
    """The feature settings of a section: field name -> its default."""
    model = _MODELS[section]
    inherited = set(ModuleConfig.model_fields) if section != "meta" else set()
    return {
        name: field.default
        for name, field in model.model_fields.items()
        if name not in inherited and name not in _NOT_FEATURES[section]
    }


@pytest.mark.parametrize("section", ["meta", "backend"])
def test_every_feature_setting_is_a_live_default(section):
    """A field with a default has a live line holding it; an unset one has
    none. A field added without its base line fails here, and so does a base
    line edited away from the model, which would silently change the
    default of every deployment that does not set it."""
    base = tomllib.loads(BASE.read_text(encoding="utf-8"))[section]
    problems = []
    for name, default in _feature_fields(section).items():
        if default is None:
            if name in base:
                problems.append(f"{name} is unset by default but has a live line")
        elif name not in base:
            problems.append(f"{name} has no live line (default {default!r})")
        elif base[name] != default:
            problems.append(f"{name} = {base[name]!r}, the default is {default!r}")
    assert not problems, problems


def _examples() -> list[tuple[str, str]]:
    """(section, key) of every example in the EXAMPLES block."""
    text = BASE.read_text(encoding="utf-8")
    assert EXAMPLES_MARKER in text
    found, section = [], None
    for line in text.split(EXAMPLES_MARKER, 1)[1].splitlines():
        if heading := re.fullmatch(r"# \[([\w-]+)\]", line):
            section = heading.group(1)
        elif setting := re.match(r"# (\w+) = ", line):
            found.append((section, setting.group(1)))
    return found


def test_a_commented_setting_is_only_ever_an_example():
    """Outside the EXAMPLES block no setting is commented out: a commented
    `# key = value` line reads as a default as easily as an example."""
    text = BASE.read_text(encoding="utf-8").split(EXAMPLES_MARKER, 1)[0]
    commented = re.findall(r"^#\s*\w+ = .*$", text, flags=re.MULTILINE)
    assert not commented, commented


def test_every_example_is_a_setting_unset_by_default():
    """An example of a setting that has a default would show a value other
    than the default, the very confusion the block exists to avoid."""
    examples = _examples()
    assert ("backend", "reference_licenses") in examples
    for section, key in examples:
        assert section in _MODELS, f"{key}: the example names no known section"
        field = _MODELS[section].model_fields.get(key)
        assert field is not None, f"[{section}] {key} is not a setting"
        assert field.default is None, f"[{section}] {key} has a default"


@pytest.mark.parametrize("mode", ["dev", "prod"])
def test_a_legacy_upload_cap_still_wins_over_the_base_default(tmp_path, mode):
    """The base layer's live `tus_max_upload_gb = 5` must not hide a cap a
    site raised under [backend] before the setting moved to [meta]."""
    for name in ("base.mascope.toml", "dev.mascope.toml", "prod.mascope.toml"):
        shutil.copy(DATA_DIR / name, tmp_path / name)
    env = tmp_path / ".runtime" / "env" / "default"
    env.mkdir(parents=True)
    (env / f"{mode}.mascope.toml").write_text("[backend]\ntus_max_upload_gb = 20\n")

    runtime = Runtime("cli", env="default", mode=mode, path=str(tmp_path), log=False)

    assert runtime.meta.tus_max_upload_gb == 20
