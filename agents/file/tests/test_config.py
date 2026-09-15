"""Unit tests for the File Agent configuration handling.

Hermetic: everything runs against tmp_path, no runtime or network needed.
Run with `uv run pytest` in `agents/file`.
"""

import os
import tomllib

import pytest

from mascope_file_agent import config


# --- normalize_host ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mascope.example.com", "mascope.example.com"),
        ("  mascope.example.com  ", "mascope.example.com"),
        ("https://mascope.example.com", "mascope.example.com"),
        ("https://mascope.example.com/", "mascope.example.com"),
        ("https://mascope.example.com/some/page", "mascope.example.com"),
        ("mascope.example.com/some/page", "mascope.example.com"),
        ("http://mascope.example.com", "http://mascope.example.com"),
        ("http://localhost:8090/", "http://localhost:8090"),
        ("", ""),
    ],
)
def test_normalize_host(raw, expected):
    assert config.normalize_host(raw) == expected


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("mascope.example.com", "https://mascope.example.com"),
        ("http://localhost:8090", "http://localhost:8090"),
        ("https://mascope.example.com", "https://mascope.example.com"),
    ],
)
def test_base_url(host, expected):
    assert config.base_url(host) == expected


# --- toml_str ---


@pytest.mark.parametrize(
    "value",
    [
        r"C:\Xcalibur\Data",
        "plain",
        "with 'single' quotes",
        'with "double" quotes',
        "with\ttab and\nnewline",
        r"quote' and \backslash",
    ],
)
def test_toml_str_roundtrip(value):
    parsed = tomllib.loads(f"key = {config.toml_str(value)}")
    assert parsed["key"] == value


# --- is_valid_instrument ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Orbi-Lab2", True),
        ("tof3", True),
        ("orbi lab", False),
        ("orbi_lab", False),  # an underscore would end the name early
        ("", False),
        ("a" * 64, True),
        ("a" * 65, False),
    ],
)
def test_is_valid_instrument(value, expected):
    assert config.is_valid_instrument(value) is expected


# --- merge_settings / missing_settings ---


def test_merge_settings_applies_defaults_and_drops_unknown_keys():
    merged = config.merge_settings({"host": "example.com", "obsolete_key": True})
    assert merged["host"] == "example.com"
    assert merged["mask"] == "*.raw"
    assert merged["timeout"] == 3
    assert merged["recursive"] is False
    assert "obsolete_key" not in merged
    assert config.missing_settings(merged) == ["access_token", "source"]


def test_merge_settings_complete_config_has_nothing_missing():
    merged = config.merge_settings(
        {"host": "example.com", "access_token": "tok", "source": r"C:\data"}
    )
    assert config.missing_settings(merged) == []


# --- user config read/write ---


def test_write_and_load_user_config_roundtrip(tmp_path):
    config_path = os.path.join(tmp_path, config.CONFIG_FILENAME)
    settings = config.merge_settings(
        {
            "host": "mascope.example.com",
            "access_token": "secret-token",
            "source": r"C:\Xcalibur\Data",
            "mask": "*.raw",
            "recursive": True,
            "instrument": "Orbi-Lab2",
            "filename_suffix": "_lab1",
        }
    )
    config.write_user_config(config_path, settings)
    assert config.load_user_config(config_path) == settings


def test_load_user_config_invalid_toml_raises_config_error(tmp_path):
    config_path = os.path.join(tmp_path, config.CONFIG_FILENAME)
    with open(config_path, "w", encoding="utf-8") as f:
        f.write("[file-agent\nhost = oops")
    with pytest.raises(config.ConfigError, match="TOML syntax error"):
        config.load_user_config(config_path)


# --- legacy migration ---


def test_load_legacy_config_migrates_configured_install(tmp_path):
    legacy = tmp_path / "prod.mascope.toml"
    legacy.write_text(
        "[meta]\nlog_level = 'info'\n"
        "[file-agent]\n"
        "host = 'mascope.example.com'\n"
        "access_token = 'legacy-token'\n"
        "source = 'C:/legacy/data'\n"
        "mask = '*.raw'\n",
        encoding="utf-8",
    )
    settings = config.load_legacy_config(str(tmp_path))
    assert settings["host"] == "mascope.example.com"
    assert settings["access_token"] == "legacy-token"
    assert settings["source"] == "C:/legacy/data"


def test_load_legacy_config_ignores_unconfigured_template(tmp_path):
    legacy = tmp_path / "prod.mascope.toml"
    legacy.write_text(
        "[file-agent]\nhost = 'localhost'\naccess_token = ''\n", encoding="utf-8"
    )
    assert config.load_legacy_config(str(tmp_path)) is None


def test_load_legacy_config_no_file(tmp_path):
    assert config.load_legacy_config(str(tmp_path)) is None


# --- generated runtime config ---


def test_write_runtime_config_is_valid_and_absolute(tmp_path):
    env_path = tmp_path / "env"
    env_path.mkdir()
    settings = config.merge_settings(
        {
            "host": "mascope.example.com",
            "access_token": "tok",
            "source": r"C:\Xcalibur\Data",
            "filename_prefix": "lab1_",
        }
    )
    config.write_runtime_config(str(env_path), settings, str(tmp_path))

    with open(env_path / "prod.mascope.toml", "rb") as f:
        parsed = tomllib.load(f)

    assert "meta" in parsed  # required by the runtime config model
    agent = parsed["file-agent"]
    assert agent["host"] == "mascope.example.com"
    assert agent["access_token"] == "tok"
    assert agent["source"] == r"C:\Xcalibur\Data"
    assert agent["recursive"] is False
    assert agent["filename_prefix"] == "lab1_"
    assert "filename_suffix" not in agent
    # logs resolve next to config.toml, not into the nested runtime env
    assert agent["log_path"] == os.path.join(str(tmp_path), "logs")


def test_runtime_config_carries_every_user_settable_key(tmp_path):
    """The generated runtime file is what the agent actually reads.

    A setting written to config.toml but missing here is silently inert, which
    is how the timezone override was dead on arrival. Assert the bridge carries
    everything a user can set rather than one key at a time.
    """
    settings = config.merge_settings(
        {
            "host": "mascope.example.com",
            "access_token": "tok",
            "source": str(tmp_path),
            "timezone": "Europe/Helsinki",
            "instrument": "Orbi-Lab2",
            "filename_prefix": "pre_",
            "filename_suffix": "_post",
        }
    )
    env_path = tmp_path / "env"
    env_path.mkdir()

    config.write_runtime_config(str(env_path), settings, str(tmp_path))

    with open(env_path / "prod.mascope.toml", "rb") as handle:
        generated = tomllib.load(handle)["file-agent"]

    # Every optional setting the user can set, plus the required ones.
    for key in (*config.REQUIRED_SETTINGS, *config.DEFAULT_SETTINGS):
        if settings.get(key) in (None, ""):
            continue
        assert key in generated, f"{key} is settable but never reaches the agent"

    assert generated["timezone"] == "Europe/Helsinki"
    assert generated["instrument"] == "Orbi-Lab2"


def test_write_user_config_retries_a_locked_replace(tmp_path, monkeypatch):
    """Windows keeps config.toml locked while another process holds it open."""
    cfg = tmp_path / config.CONFIG_FILENAME
    settings = config.merge_settings(
        {"host": "h", "access_token": "tok", "source": str(tmp_path)}
    )
    config.write_user_config(str(cfg), settings)

    attempts = {"n": 0}
    real_replace = os.replace

    def flaky_replace(src, dst):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise PermissionError("locked by another process")
        return real_replace(src, dst)

    monkeypatch.setattr(config.os, "replace", flaky_replace)
    monkeypatch.setattr(config, "_REPLACE_RETRY_DELAY_S", 0)

    settings["access_token"] = "rotated"
    config.write_user_config(str(cfg), settings)

    assert attempts["n"] == 3
    assert config.load_user_config(str(cfg))["access_token"] == "rotated"
    # No debris left in the config directory.
    assert [p.name for p in tmp_path.iterdir()] == [config.CONFIG_FILENAME]
