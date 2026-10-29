"""
Which config settings a deployment overrides, and the layer that set each.

A deployment's settings come from three TOML layers, read in order by
`mascope_runtime.config.RuntimeConfigLoader`: `base.mascope.toml` and
`{mode}.mascope.toml`, both shipped with the release, then the env's own
`.runtime/env/<env>/{mode}.mascope.toml`. The loader merges them and drops
where each value came from. This module reads the same three files again,
keeps the origin of every key, and compares the effective value with the
default of the config model, so `mascope dev config` and `mascope prod config`
can list what a deployment runs differently and which file says so.

The module is stdlib-only at import and imports the config models lazily,
because `mascope prod config` does not run it on the host: it pipes this very
source into the backend container's Python (`python -`), so the report reads
the image's `base` and `mode` layers and validates against the image's models,
which are what the running backend uses. It must therefore stay one file that
imports nothing from `mascope_cli`.

Values are compared before the loader resolves them (relative paths, log
levels, `MASCOPE_API_PORT`), so a path reads as written in the TOML rather
than as an absolute path on one machine. Nothing is read from
`.runtime/secrets`, and a value whose key names a credential
(`access_token`, a password, a secret) is never printed.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tomllib
import types
import typing
from typing import Any


#: The layers in the order the loader applies them; "default" is the model's.
LAYERS = ("base", "mode", "env")

#: Keys the loader fills in or that only the dev launcher reads (the module's
#: name, its log colour, its launch groups and its command). They differ from
#: the model's default in every module and say nothing about how a deployment
#: behaves, so only `--all` shows them.
LAUNCHER_KEYS = frozenset({"name", "color", "tags", "run"})

#: A key whose value is a credential: its value is never printed. Matches the
#: end of the key, so `require_device_tokens` (a policy switch) is not one.
_SECRET_KEY = re.compile(r"(?:^|_)(?:token|password|passwd|secret|api_key)$")

#: What a credential's value reads as.
HIDDEN = "<hidden>"

#: The interpreter of the `uv tool install` in the backend image
#: (`server/backend/Dockerfile`), which `mascope prod config` runs this module
#: with inside the container.
IMAGE_PYTHON = "/opt/uv/tools/mascope/bin/python"

#: Where the backend image keeps its copies of the shipped layers
#: (`MASCOPE_PATH=/app` in the Dockerfile).
IMAGE_HOME = "/app"


def layer_paths(home: str, env: str, mode: str) -> dict[str, str]:
    """
    The three files the loader reads, keyed by layer.

    :param home: The runtime home (`MASCOPE_PATH`)
    :param env: The env's name
    :param mode: The runtime mode (`dev`, `prod`)
    :return: Path of each layer's TOML file
    """
    return {
        "base": os.path.join(home, "base.mascope.toml"),
        "mode": os.path.join(home, f"{mode}.mascope.toml"),
        "env": os.path.join(home, ".runtime", "env", env, f"{mode}.mascope.toml"),
    }


def read_layer(path: str) -> dict | None:
    """
    One layer's TOML, its section names as the loader keys them.

    The loader turns `[file-converter]` into the `file_converter` field of
    `RuntimeConfig`; this does the same so the layers line up with the model.

    :param path: The TOML file
    :return: Its sections, or None when the file does not exist
    """
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return {section.replace("-", "_"): value for section, value in raw.items()}


def layer_digest(layer: dict | None) -> str | None:
    """
    A digest of a layer's parsed content, blind to comments and layout.

    `mascope prod config` compares the image's shipped layers with the host's
    copies by digest, so the comparison sends no value out of the container.

    :param layer: A layer as `read_layer` returns it
    :return: The hex digest, or None for a missing layer
    """
    if layer is None:
        return None
    canonical = json.dumps(layer, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _deep_merge(base: dict, overlay: dict) -> dict:
    """The loader's merge: nested tables merge, anything else replaces."""
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _migrate(raw: dict) -> dict:
    """
    Move settings that changed section, as the loader does before validating.

    Uses the runtime's own `migrate_legacy_options` when it has one; a runtime
    older than it moves nothing, and neither does its loader.
    """
    try:
        from mascope_runtime.config import migrate_legacy_options
    except ImportError:
        return raw
    return migrate_legacy_options(copy.deepcopy(raw))


def _origins(layer: dict, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    """Every leaf key path a layer sets (a table's own keys, recursively)."""
    paths = set()
    for key, value in layer.items():
        path = (*prefix, key)
        if isinstance(value, dict):
            paths |= _origins(value, path)
        paths.add(path)
    return paths


def _model_of(annotation: Any) -> type | None:
    """
    The config model a field holds, for `Model` and `Model | None`.

    A field that may also hold something else (`DatetimeRange | str | None`)
    is a leaf: its TOML value is compared as a whole. A model is told by its
    `model_fields`, so the module needs no import of pydantic, which the CLI
    has only through the runtime.
    """
    args = typing.get_args(annotation)
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        members = [arg for arg in args if arg is not type(None)]
        return _model_of(members[0]) if len(members) == 1 else None
    if isinstance(annotation, type) and hasattr(annotation, "model_fields"):
        return annotation
    return None


_REQUIRED = object()


def _default_of(field: Any) -> Any:
    """A field's default, `_REQUIRED` when it has none."""
    if field.is_required():
        return _REQUIRED
    return field.get_default(call_default_factory=True)


def _plain(value: Any) -> Any:
    """A default as the TOML would write it (a model as its table)."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _shown(key: str, value: Any) -> Any:
    """The value as printed: a credential's is hidden, an empty one is not."""
    if _SECRET_KEY.search(key) and value not in (None, ""):
        return HIDDEN
    return value


def _walk(
    model: type | None,
    effective: dict,
    origin: dict[tuple[str, ...], str],
    path: tuple[str, ...],
    rows: list[dict],
) -> None:
    """
    One row per setting of a section, recursing into nested models.

    :param model: The section's config model, None for a key the model lacks
    :param effective: The section's merged values
    :param origin: The layer that last set each key path
    :param path: The section's key path
    :param rows: Where the rows go
    """
    fields = model.model_fields if model is not None else {}
    # A nested model's rows follow the section's own, as a TOML file writes
    # `[backend.database]` after the keys of `[backend]`.
    tables = []
    for key, field in fields.items():
        nested = _model_of(field.annotation)
        value = effective.get(key, _REQUIRED)
        if nested is not None and (value is _REQUIRED or isinstance(value, dict)):
            tables.append((nested, value if isinstance(value, dict) else {}, key))
            continue
        default = _default_of(field)
        if value is _REQUIRED:
            # Unset: the model's default, or nothing, which the loader would
            # refuse (a required setting no layer writes).
            value = None if default is _REQUIRED else default
            layer = "default"
        else:
            layer = origin.get((*path, key), "default")
        rows.append(
            _row(
                path,
                key,
                value=value,
                layer=layer,
                default=default,
                known=True,
            )
        )
    for key in effective:
        if key in fields:
            continue
        # A key the model does not know: the loader drops it without a word,
        # so a misspelt setting reads as if it were never written.
        rows.append(
            _row(
                path,
                key,
                value=effective[key],
                layer=origin.get((*path, key), "default"),
                default=_REQUIRED,
                known=False,
            )
        )
    for nested, values, key in tables:
        _walk(nested, values, origin, (*path, key), rows)


def _row(
    path: tuple[str, ...],
    key: str,
    *,
    value: Any,
    layer: str,
    default: Any,
    known: bool,
) -> dict:
    """One setting of the report, as JSON-ready data."""
    has_default = default is not _REQUIRED
    plain_default = _plain(default) if has_default else None
    plain_value = _plain(value)
    return {
        "section": ".".join(path),
        "key": key,
        "value": _shown(key, plain_value),
        "layer": layer,
        "default": _shown(key, plain_default),
        "has_default": has_default,
        "known": known,
        "differs": _differs(known, has_default, layer, plain_value, plain_default),
    }


def _differs(
    known: bool, has_default: bool, layer: str, value: Any, default: Any
) -> bool:
    """
    Whether a setting departs from what the release would run with.

    A key the model does not know always does: the loader ignores it. A
    setting with no model default (an agent's `host`) has the base layer's
    value as its baseline, so it departs only when a later layer sets it, or
    when nothing does, which the loader would refuse.
    """
    if not known:
        return True
    if not has_default:
        return layer != "base"
    return value != default


def collect(paths: dict[str, str]) -> dict:
    """
    Every setting of the effective config, with its layer and default.

    Reads the layers in the loader's order, merges and migrates them as it
    does, and attributes each key to the last layer whose own file sets it:
    an env toml that pins a value equal to the release's still pins it, and
    keeps it when a later release changes the default. Sections the model
    knows and no layer writes (an agent this deployment does not run) are
    left out, as the loader leaves them unset.

    :param paths: Path of each layer's TOML file (`layer_paths`)
    :return: `layers` (path, found and digest of each) and `rows` (one per
        setting: section, key, value, layer, default, has_default, known,
        differs)
    """
    from mascope_runtime.config import RuntimeConfig

    layers = {name: read_layer(paths[name]) for name in LAYERS}
    merged: dict = {}
    origin: dict[tuple[str, ...], str] = {}
    for name in LAYERS:
        layer = layers[name]
        if layer is None:
            continue
        merged = _deep_merge(merged, layer)
        # Attribute after the layer's own migration, so a setting written in
        # its old section is credited to the section it now lives in.
        for key_path in _origins(_migrate(layer)):
            origin[key_path] = name
    effective = _migrate(merged)

    rows: list[dict] = []
    fields = RuntimeConfig.model_fields
    for section, values in effective.items():
        if not isinstance(values, dict):
            continue
        model = _model_of(fields[section].annotation) if section in fields else None
        _walk(model, values, origin, (section,), rows)

    return {
        "layers": [
            {
                "layer": name,
                "path": paths[name],
                "found": layers[name] is not None,
                "digest": layer_digest(layers[name]),
            }
            for name in LAYERS
        ],
        "rows": rows,
    }


def select(report: dict, *, show_all: bool = False) -> list[dict]:
    """
    The rows a report shows.

    :param report: What `collect` returned
    :param show_all: Every setting of the effective config, not only the ones
        that differ from the model's default
    :return: The rows, in the report's order
    """
    if show_all:
        return list(report["rows"])
    return [
        row
        for row in report["rows"]
        if row["differs"] and (row["key"] not in LAUNCHER_KEYS or not row["known"])
    ]


def _toml_value(value: Any) -> str:
    """A value as it reads in a TOML file (close enough for a report)."""
    if value == HIDDEN:
        return value
    return json.dumps(value)


def _heading(section: str) -> str:
    """A section as the TOML files head it: `file_converter` is `file-converter`."""
    top, _, rest = section.partition(".")
    return top.replace("_", "-") + (f".{rest}" if rest else "")


def format_text(report: dict, *, show_all: bool = False, title: str = "") -> str:
    """
    The report as text, grouped by section like the TOML files.

    :param report: What `collect` returned
    :param show_all: Show the whole effective config
    :param title: A first line naming the env and mode
    :return: The text, ending in a newline
    """
    lines = [title] if title else []
    lines.append("Layers, applied in this order:")
    for layer in report["layers"]:
        found = "" if layer["found"] else "  (not found)"
        lines.append(f"  {layer['layer']:<5} {layer['path']}{found}")
    lines.append("")

    rows = select(report, show_all=show_all)
    if not rows:
        lines.append("Every setting has its model default.")
        return "\n".join(lines) + "\n"

    width = max(len(f"{row['key']} = {_toml_value(row['value'])}") for row in rows)
    width = min(width, 60)
    section = None
    for row in rows:
        if row["section"] != section:
            if section is not None:
                lines.append("")
            section = row["section"]
            lines.append(f"[{_heading(section)}]")
        setting = f"{row['key']} = {_toml_value(row['value'])}"
        if not row["known"]:
            note = "unknown key, ignored by the loader"
        elif not row["has_default"]:
            note = "no default"
        elif row["differs"]:
            note = f"default {_toml_value(row['default'])}"
        else:
            note = ""
        lines.append(f"  {setting:<{width}}  {row['layer']:<7}  {note}".rstrip())
    return "\n".join(lines) + "\n"


def format_json(report: dict, *, show_all: bool = False) -> str:
    """
    The report as JSON: its layers and the rows `select` keeps.

    :param report: What `collect` returned
    :param show_all: Show the whole effective config
    :return: The JSON document
    """
    shown = {"layers": report["layers"], "rows": select(report, show_all=show_all)}
    return json.dumps(shown, indent=2, default=str)


def title(env: str, mode: str, *, show_all: bool = False) -> str:
    """The report's first line, naming the env and mode it describes."""
    what = (
        "the effective config"
        if show_all
        else "settings that differ from the model defaults"
    )
    return f'Env "{env}" in {mode} mode: {what}'


def main(argv: list[str] | None = None) -> None:
    """
    Print the report as JSON: what `mascope prod config` runs in the image.

    :param argv: `--home`, `--env`, `--mode`
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--home", default=os.environ.get("MASCOPE_PATH", IMAGE_HOME))
    parser.add_argument("--env", required=True)
    parser.add_argument("--mode", required=True)
    args = parser.parse_args(argv)
    report = collect(layer_paths(args.home, args.env, args.mode))
    json.dump(report, sys.stdout, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
