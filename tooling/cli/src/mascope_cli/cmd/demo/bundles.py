"""
Demo bundle registry and local cache resolution.

A *demo bundle* is the versioned, published artifact that powers
`mascope demo` and the end-to-end reproducibility test. It contains the
de-identified raw instrument files (the source of truth), a derived database
snapshot for instant loading, derived golden outputs for the reproducibility
test, and a `manifest.json` describing all of the above.

See `docs/demo_dataset.md` for the full design. This module is intentionally
free of Typer/CLI concerns - it only knows where bundles live (remote and
local) and how to read their manifests.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Manifest filename inside every bundle.
MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class Bundle:
    """
    A published demo bundle version.

    :param version: Bundle version tag (semver, e.g. ``"1.0.0"``). Also the cache
                    subdirectory name.
    :param url: Permanent download URL of the bundle archive (Zenodo). ``None``
                until the bundle has been published, in which case only a
                locally-built bundle (via ``mascope demo snapshot``) can be used.
    :param archive_md5: Expected MD5 of the downloaded archive, if known. This is
                        the checksum Zenodo displays for the file, so it can be
                        copied straight from the record. Verified before
                        extraction. ``None`` skips the archive-level check (the
                        manifest still verifies every file inside with SHA-256).
    :param doi: Zenodo DOI for citation, if published.
    """

    version: str
    url: Optional[str] = None
    archive_md5: Optional[str] = None
    doi: Optional[str] = None


# --- Registry -------------------------------------------------------------
#
# Add a new entry here when publishing a new bundle version to Zenodo, then
# bump DEFAULT_BUNDLE_VERSION. Keep older versions so pinned reproducibility
# runs remain resolvable.

BUNDLES: dict[str, Bundle] = {
    "1.0.0": Bundle(
        version="1.0.0",
        url="https://zenodo.org/records/20929489/files/mascope-demo-dataset-v1.zip",
        archive_md5="921e9d22c7be4a1e5bc78ab515b60f8b",
        doi="10.5281/zenodo.20929489",
    ),
    "1.1.0": Bundle(
        version="1.1.0",
        url="https://zenodo.org/records/21168568/files/mascope-demo-dataset-v1.1.zip",
        archive_md5="75a45d97088b089d0421630ac0f1497f",
        doi="10.5281/zenodo.21168568",
    ),
    "1.2.0": Bundle(
        version="1.2.0",
        url="https://zenodo.org/records/21899498/files/mascope-demo-dataset-v1.2.zip",
        archive_md5="6d7b4686c31518ca931722a77366f146",
        doi="10.5281/zenodo.21899498",
    ),
    "1.2.1": Bundle(
        version="1.2.1",
        url="https://zenodo.org/records/21994087/files/mascope-demo-dataset-v1.2.1.zip",
        archive_md5="7fe2fba94648f97b5af3224c3b0291c2",
        doi="10.5281/zenodo.21994087",
    ),
}

DEFAULT_BUNDLE_VERSION = "1.2.1"


def get_bundle(version: Optional[str] = None) -> Bundle:
    """
    Resolve a registered bundle by version.

    :param version: Bundle version tag. Defaults to
                    :data:`DEFAULT_BUNDLE_VERSION` when ``None``.
    :raises KeyError: If no bundle is registered under ``version``.
    :return: The matching :class:`Bundle`.
    """
    resolved = version or DEFAULT_BUNDLE_VERSION
    if resolved not in BUNDLES:
        available = ", ".join(sorted(BUNDLES))
        raise KeyError(
            f"Unknown demo bundle version '{resolved}'. Available: {available}."
        )
    return BUNDLES[resolved]


def cache_root() -> Path:
    """
    Return the local cache root for downloaded/built demo bundles.

    Located under the Mascope runtime dir so it is naturally gitignored and
    shared across runtime envs (a bundle is env-independent).

    :return: ``{MASCOPE_PATH}/.runtime/demo``.
    """
    return Path(os.environ["MASCOPE_PATH"]) / ".runtime" / "demo"


def bundle_dir(version: Optional[str] = None) -> Path:
    """
    Return the local directory where a given bundle version is cached.

    :param version: Bundle version tag. Defaults to the registry default.
    :return: ``{cache_root}/{version}``. Not guaranteed to exist yet.
    """
    return cache_root() / get_bundle(version).version


def is_cached(version: Optional[str] = None) -> bool:
    """
    Check whether a bundle has been fetched/built and looks complete.

    A bundle is considered cached when its manifest is present. Integrity of
    the individual files is validated separately by :func:`verify_manifest`.

    :param version: Bundle version tag. Defaults to the registry default.
    :return: ``True`` if the bundle's manifest exists locally.
    """
    return (bundle_dir(version) / MANIFEST_NAME).is_file()


def load_manifest(version: Optional[str] = None) -> dict:
    """
    Load and parse a cached bundle's ``manifest.json``.

    :param version: Bundle version tag. Defaults to the registry default.
    :raises FileNotFoundError: If the bundle is not cached locally.
    :return: The parsed manifest as a dict.
    """
    manifest_path = bundle_dir(version) / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"No cached demo bundle at {manifest_path.parent}. "
            "Run 'mascope demo fetch' first."
        )
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """
    Compute the SHA-256 hex digest of a file, streaming to bound memory.

    :param path: File to hash.
    :param chunk_size: Read chunk size in bytes (default 1 MiB).
    :return: Lowercase hex digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def md5_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """
    Compute the MD5 hex digest of a file, streaming to bound memory.

    Used only for the archive-level download check, because MD5 is the checksum
    Zenodo publishes for each file. Per-file bundle integrity uses the stronger
    :func:`sha256_file`.

    :param path: File to hash.
    :param chunk_size: Read chunk size in bytes (default 1 MiB).
    :return: Lowercase hex digest.
    """
    digest = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest(version: Optional[str] = None) -> list[str]:
    """
    Verify every file listed in a cached bundle's manifest against its SHA-256.

    Checks the raw files, the seed/snapshot dumps, and the expected goldens.
    Returns a list of human-readable problems; an empty list means the bundle is
    intact.

    :param version: Bundle version tag. Defaults to the registry default.
    :return: List of mismatch/missing-file messages (empty when all good).
    """
    root = bundle_dir(version)
    manifest = load_manifest(version)
    problems: list[str] = []

    def _check(rel_path: str, expected: Optional[str]) -> None:
        if not expected:
            return
        target = root / rel_path
        if not target.is_file():
            problems.append(f"missing: {rel_path}")
            return
        actual = sha256_file(target)
        if actual != expected:
            problems.append(
                f"checksum mismatch: {rel_path} "
                f"(expected {expected[:12]}..., got {actual[:12]}...)"
            )

    for entry in manifest.get("raw", []):
        _check(f"raw/{entry['name']}", entry.get("sha256"))

    for block in ("seed", "snapshot"):
        dump = manifest.get(block, {})
        if dump.get("dump"):
            _check(dump["dump"], dump.get("sha256"))

    expected = manifest.get("expected", {})
    if expected.get("peaks"):
        _check(expected["peaks"], expected.get("sha256"))

    return problems
