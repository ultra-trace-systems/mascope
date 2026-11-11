"""The assignment bench's sets manifest: which files make each set, and how.

A manifest is a JSON file (``manifests/demo.json`` is the public one) naming
the bench's sets by letter. Each set says where its files come from, the
ionization mode its samples must resolve to and where that mode is seeded
from, the target collections and reference lists the instance needs for it,
the assignment profile and instrument class it is read under, and the window
the reference engine runs at::

    {
      "schema": "mascope-bench-manifest/1",
      "name": "demo",
      "sets": {
        "Db": {
          "label": "the demo bundle's bromide samples",
          "instrument_class": "orbitrap",
          "mode": "Bromide m/z 82-750",
          "profile": "BR",
          "reference_window_ppm": null,
          "seed": {"bundle": "1.4.4"},
          "collections": [],
          "reference_lists": "default",
          "files": [{"bundle": "1.4.4", "name": "Orbion_neg_Br_NoRI_....raw"}]
        }
      }
    }

A file is named in one of two forms. ``{"bundle": V, "name": N}`` is the
sample file ``N`` of the published demo bundle ``V``, read from the CLI's
bundle cache (``mascope demo fetch``). ``{"sha256": H}`` is the fleet
regression corpus object ``H``, read from a ``corpus checkout`` whose
``manifest.json`` gives its name. A seed is ``{"bundle": V}``, the bundle's
own ``seed/`` dump, or ``{"corpus": "seed.json"}``, the checkout's seed. The
corpus forms belong in a private manifest kept beside the bench, never in
the repository: the corpus's names and hashes are customer-derived.

This module only reads: it parses a manifest and resolves its files to paths.
``bench.py`` builds the instance from it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


SCHEMA = "mascope-bench-manifest/1"

#: A set's name, by the gate card's rule (``tooling/assignment_compare``): an
#: upper-case letter, then at most one digit or lower-case letter, then at
#: most a sign (the gate sets' A to L, C2, K3; the demo's Db and Du).
SET_NAME = re.compile(r"^[A-Z][0-9a-z]?[+-]?$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
INSTRUMENT_CLASSES = ("orbitrap", "tof")

_SET_KEYS = {
    "label",
    "instrument_class",
    "mode",
    "profile",
    "reference_window_ppm",
    "seed",
    "collections",
    "reference_lists",
    "files",
}


class ManifestError(ValueError):
    """A manifest that cannot be read, or a file it names that is not there."""


@dataclass(frozen=True)
class BundleFile:
    """A sample file of a published demo bundle."""

    bundle: str
    name: str

    @property
    def key(self) -> str:
        return f"bundle:{self.bundle}/{self.name}"


@dataclass(frozen=True)
class CorpusFile:
    """A fleet regression corpus object, named by its hash."""

    sha256: str

    @property
    def key(self) -> str:
        return f"sha256:{self.sha256}"


FileRef = BundleFile | CorpusFile


@dataclass(frozen=True)
class Seed:
    """Where a set's mode and collections are seeded from.

    ``bundle`` names a demo bundle whose ``seed/`` dump holds them;
    ``corpus`` a seed file inside the corpus checkout. Exactly one is set.
    """

    bundle: str | None = None
    corpus: str | None = None

    @property
    def key(self) -> str:
        return f"bundle:{self.bundle}" if self.bundle else f"corpus:{self.corpus}"


@dataclass(frozen=True)
class Window:
    """The reference engine's mass windows for a set, in ppm."""

    trust: float
    search: float


@dataclass(frozen=True)
class BenchSet:
    """One set of the bench: its files and how they are read."""

    name: str
    label: str
    instrument_class: str
    mode: str
    profile: str
    seed: Seed
    files: tuple[FileRef, ...]
    collections: tuple[str, ...] = ()
    #: ``"default"`` for every list that loads by default, else list ids.
    reference_lists: str | tuple[str, ...] = ()
    #: ``None`` runs the reference at its own defaults.
    reference_window_ppm: Window | None = None


@dataclass(frozen=True)
class Manifest:
    """A parsed manifest. ``digest`` identifies its content."""

    name: str
    sets: dict[str, BenchSet]
    digest: str
    description: str = ""
    corpus_checkout: str | None = None
    source: dict = field(default_factory=dict, compare=False, repr=False)

    def files(self) -> list[tuple[str, FileRef]]:
        """Every file with its set's name, in manifest order."""
        return [(s.name, f) for s in self.sets.values() for f in s.files]


@dataclass(frozen=True)
class ResolvedFile:
    """A manifest file found on disk: what to upload, and under which name."""

    ref: FileRef
    path: Path
    name: str
    sha256: str


# --- parsing ---


def _need(cond: bool, where: str, message: str) -> None:
    if not cond:
        raise ManifestError(f"{where}: {message}")


def _string(value, where: str) -> str:
    _need(isinstance(value, str) and value.strip() != "", where, "must be a text")
    return value


def _strings(value, where: str) -> tuple[str, ...]:
    _need(isinstance(value, list), where, "must be a list of texts")
    out = tuple(_string(v, f"{where}[{i}]") for i, v in enumerate(value))
    _need(len(set(out)) == len(out), where, "names an entry twice")
    return out


def _file(value, where: str) -> FileRef:
    _need(isinstance(value, dict), where, "must be an object")
    keys = set(value)
    if keys == {"bundle", "name"}:
        name = _string(value["name"], f"{where}.name")
        _need("/" not in name and "\\" not in name, where, "a name, not a path")
        return BundleFile(_string(value["bundle"], f"{where}.bundle"), name)
    if keys == {"sha256"}:
        sha = _string(value["sha256"], f"{where}.sha256")
        _need(bool(SHA256.match(sha)), where, "sha256 must be 64 lower-case hex digits")
        return CorpusFile(sha)
    raise ManifestError(
        f"{where}: a file is {{'bundle', 'name'}} or {{'sha256'}}, not {sorted(keys)}"
    )


def _seed(value, where: str) -> Seed:
    _need(isinstance(value, dict) and len(value) == 1, where, "one of bundle, corpus")
    ((kind, ref),) = value.items()
    if kind == "bundle":
        return Seed(bundle=_string(ref, f"{where}.bundle"))
    if kind == "corpus":
        return Seed(corpus=_string(ref, f"{where}.corpus"))
    raise ManifestError(
        f"{where}: a seed is {{'bundle'}} or {{'corpus'}}, not {kind!r}"
    )


def _window(value, where: str) -> Window | None:
    if value is None:
        return None
    _need(
        isinstance(value, dict) and set(value) == {"trust", "search"},
        where,
        "null, or {'trust': ppm, 'search': ppm}",
    )
    trust, search = value["trust"], value["search"]
    for key, ppm in (("trust", trust), ("search", search)):
        _need(
            isinstance(ppm, (int, float)) and not isinstance(ppm, bool) and ppm > 0,
            f"{where}.{key}",
            "must be a positive number",
        )
    _need(trust <= search, where, "the trust window is inside the search window")
    return Window(float(trust), float(search))


def _set(name: str, value, where: str) -> BenchSet:
    _need(
        bool(SET_NAME.match(name)),
        where,
        "a set is named as the gate card names one (A, C2, Db, I+)",
    )
    _need(isinstance(value, dict), where, "must be an object")
    unknown = set(value) - _SET_KEYS
    _need(not unknown, where, f"unknown keys {sorted(unknown)}")
    for key in ("instrument_class", "mode", "profile", "seed", "files"):
        _need(key in value, where, f"needs '{key}'")
    klass = value["instrument_class"]
    _need(
        klass in INSTRUMENT_CLASSES,
        f"{where}.instrument_class",
        f"one of {INSTRUMENT_CLASSES}",
    )
    files = value["files"]
    _need(
        isinstance(files, list) and files, f"{where}.files", "must list the set's files"
    )
    lists = value.get("reference_lists", [])
    reference_lists = (
        "default" if lists == "default" else _strings(lists, f"{where}.reference_lists")
    )
    return BenchSet(
        name=name,
        label=value.get("label", ""),
        instrument_class=klass,
        mode=_string(value["mode"], f"{where}.mode"),
        profile=_string(value["profile"], f"{where}.profile"),
        seed=_seed(value["seed"], f"{where}.seed"),
        files=tuple(_file(f, f"{where}.files[{i}]") for i, f in enumerate(files)),
        collections=_strings(value.get("collections", []), f"{where}.collections"),
        reference_lists=reference_lists,
        reference_window_ppm=_window(
            value.get("reference_window_ppm"), f"{where}.reference_window_ppm"
        ),
    )


def parse(data: dict, where: str = "manifest") -> Manifest:
    """Parse a manifest's JSON object.

    :raises ManifestError: naming the first thing wrong with it.
    """
    _need(isinstance(data, dict), where, "must be a JSON object")
    _need(data.get("schema") == SCHEMA, where, f"schema must be {SCHEMA!r}")
    unknown = set(data) - {"schema", "name", "description", "corpus_checkout", "sets"}
    _need(not unknown, where, f"unknown keys {sorted(unknown)}")
    sets_in = data.get("sets")
    _need(isinstance(sets_in, dict) and sets_in, f"{where}.sets", "must name a set")
    sets = {n: _set(n, v, f"{where}.sets.{n}") for n, v in sets_in.items()}

    seen: dict[str, str] = {}
    for set_name, ref in ((s.name, f) for s in sets.values() for f in s.files):
        _need(
            ref.key not in seen,
            f"{where}.sets.{set_name}",
            f"{ref.key} is in set {seen.get(ref.key)} already",
        )
        seen[ref.key] = set_name
    # One mode is one chemistry: two sets naming it must seed it from one place.
    seeds: dict[str, Seed] = {}
    for s in sets.values():
        _need(
            seeds.setdefault(s.mode, s.seed) == s.seed,
            f"{where}.sets.{s.name}.seed",
            f"mode {s.mode!r} is seeded from {seeds[s.mode].key} by another set",
        )

    checkout = data.get("corpus_checkout")
    if checkout is not None:
        _string(checkout, f"{where}.corpus_checkout")
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return Manifest(
        name=_string(data.get("name"), f"{where}.name"),
        description=data.get("description", ""),
        sets=sets,
        corpus_checkout=checkout,
        digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        source=data,
    )


def load(path: Path | str) -> Manifest:
    """Read and parse a manifest file."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"{path}: {error}") from None
    return parse(data, where=str(path))


# --- resolving files ---


def corpus_index(checkout: Path) -> dict[str, Path]:
    """A corpus checkout's objects by hash: sha256 -> the path to upload.

    The checkout's ``raw/`` links carry the files' names, which the upload
    needs (an instrument is read from the name, and the Thermo reader opens
    only a ``.raw``); the objects themselves are named by hash alone.
    """
    try:
        listing = json.loads((checkout / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"{checkout} is not a corpus checkout: {error}") from None
    out = {}
    for entry in listing.get("files", []):
        path = checkout / entry["file"]
        if not path.exists():
            path = checkout / "raw" / Path(entry["file"]).name
        out[entry["sha256"]] = path
    return out


def resolve(
    manifest: Manifest,
    bundle_dir: Callable[[str], Path],
    checkout: Path | None = None,
) -> dict[str, ResolvedFile]:
    """Find every file of the manifest on disk, by its key.

    :param bundle_dir: The cached bundle directory of a version (its
        ``manifest.json`` lists the raw files with their hashes).
    :param checkout: A ``corpus checkout``, for the corpus files.
    :raises ManifestError: listing every file that was not found.
    """
    missing: list[str] = []
    bundles: dict[str, dict[str, str]] = {}
    index: dict[str, Path] | None = None
    out: dict[str, ResolvedFile] = {}
    for set_name, ref in manifest.files():
        if isinstance(ref, BundleFile):
            if ref.bundle not in bundles:
                root = bundle_dir(ref.bundle)
                listing = json.loads(
                    (root / "manifest.json").read_text(encoding="utf-8")
                )
                bundles[ref.bundle] = {r["name"]: r["sha256"] for r in listing["raw"]}
            sha = bundles[ref.bundle].get(ref.name)
            path = bundle_dir(ref.bundle) / "raw" / ref.name
            if sha is None or not path.is_file():
                missing.append(
                    f"set {set_name}: {ref.name} is not in bundle {ref.bundle}"
                )
                continue
            out[ref.key] = ResolvedFile(ref, path, ref.name, sha)
        else:
            if checkout is None:
                raise ManifestError(
                    f"set {set_name} names corpus objects: give a corpus checkout "
                    "(--checkout, or corpus_checkout in the manifest)"
                )
            if index is None:
                index = corpus_index(checkout)
            path = index.get(ref.sha256)
            if path is None or not path.exists():
                missing.append(
                    f"set {set_name}: object {ref.sha256[:12]} is not in {checkout}"
                )
                continue
            out[ref.key] = ResolvedFile(ref, path, path.name, ref.sha256)
    if missing:
        raise ManifestError(
            f"{len(missing)} file(s) not found:\n  " + "\n  ".join(missing)
        )
    return out
