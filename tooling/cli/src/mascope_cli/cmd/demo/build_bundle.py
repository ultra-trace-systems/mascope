"""
MAINTAINER tooling: build a demo bundle from the raw source of truth.

Produces, in the output bundle directory (the publishable tree):

- ``raw/``            - a copy of the de-identified raw files (source of truth)
- ``manifest.json``   - checksums, parsed measurement metadata, tool versions,
                        and the tolerances the reproducibility test uses
- ``snapshot/``       - (with ``--update``) a ``pg_dump`` of the ``mascope_demo``
                        database plus its filestore tree, for the instant path
- ``expected/``       - (with ``--update``) golden outputs for the reproducibility
                        test; see :func:`export_goldens`

And, as a *sibling* of the bundle directory (NOT inside it, so it is never
published): ``<bundle>.deid_report.md`` - everything readable from filenames
(and, best-effort, embedded ``.raw`` metadata) for human sign-off before
publishing. See :func:`_write_deid_report`.

The raw files and their checksums are always (re)written. The derived
``snapshot/`` and ``expected/`` artifacts are only (re)built when ``--update`` is
passed, so a metadata-only refresh never silently changes the goldens.

See ``docs/demo_dataset.md`` for the full publish workflow.
"""

import json
import re
import shutil
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path

from mascope_cli.cmd.demo import bundles
from mascope_cli.cmd.demo._seed import DEMO_ENV, env_dir
from mascope_cli.cmd.demo.verify import DEFAULT_TOLERANCES
from mascope_cli.runtime import runtime


# Full acquisition filename, e.g.
#   KORBI2_2025.08.11-14h23m12s_pos_Ur_NoRI_1_20250811142302.raw
# Captures every component so de-identification can drop the redundant
# human-readable timestamp and the run index while keeping the compact stamp.
_RAW_NAME = re.compile(
    r"^(?P<instrument>[A-Za-z0-9]+)_"
    r"(?P<date>\d{4}\.\d{2}\.\d{2})-(?P<time>\d{2}h\d{2}m\d{2}s)_"
    r"(?P<polarity>pos|neg)_"
    r"(?P<sample>[^_]+)_"
    r"(?P<rest>.+?)_"
    r"(?P<runidx>\d+)_"
    r"(?P<stamp>\d{14})\.raw$"
)

# Already-de-identified filename, e.g. Orbion_pos_Ur_NoRI_20250811142302.raw.
# Lets the build run idempotently on files that were renamed in place.
_DEID_NAME = re.compile(
    r"^(?P<instrument>[A-Za-z0-9]+)_"
    r"(?P<polarity>pos|neg)_"
    r"(?P<sample>[^_]+)_"
    r"(?P<rest>.+?)_"
    r"(?P<stamp>\d{14})\.raw$"
)

# Instrument label aliasing for de-identification. Original -> published alias.
INSTRUMENT_ALIASES = {"KORBI2": "Orbion"}


def _deidentify_name(
    name: str, aliases: dict[str, str] | None = None
) -> tuple[str, dict[str, str]]:
    """
    Compute the de-identified filename for a raw acquisition file.

    Transformation (see docs/demo_dataset.md):
    - alias the instrument label (``KORBI2`` -> ``Orbion``),
    - drop the redundant human-readable timestamp (``2025.08.11-14h23m12s``),
    - drop the run index (the ``_1_`` segment),
    - keep polarity, sample, RI marker, and the compact acquisition stamp.

    Example::

        KORBI2_2025.08.11-14h23m12s_pos_Ur_NoRI_1_20250811142302.raw
        -> Orbion_pos_Ur_NoRI_20250811142302.raw

    :param name: Original raw filename.
    :param aliases: Instrument alias map. Defaults to :data:`INSTRUMENT_ALIASES`.
    :return: ``(new_name, parsed_original)``. If the name does not match the
             expected pattern, ``new_name`` is the original (unchanged) and
             ``parsed_original`` is empty so the caller can warn.
    """
    alias_map = INSTRUMENT_ALIASES if aliases is None else aliases

    m = _RAW_NAME.match(name)
    if m:
        parts = m.groupdict()
        instrument = alias_map.get(parts["instrument"], parts["instrument"])
        new_name = (
            f"{instrument}_{parts['polarity']}_{parts['sample']}_"
            f"{parts['rest']}_{parts['stamp']}.raw"
        )
        return new_name, parts

    # Already de-identified (e.g. renamed in place): leave unchanged, but still
    # parse so measurement metadata works. Derive the date from the compact stamp.
    d = _DEID_NAME.match(name)
    if d:
        parts = d.groupdict()
        stamp = parts["stamp"]
        parts["date"] = f"{stamp[0:4]}.{stamp[4:6]}.{stamp[6:8]}"
        return name, parts

    return name, {}


def _opentfraw_version() -> str | None:
    """Best-effort resolution of the installed OpenTFRaw reader version."""
    for dist in ("mascope-opentfraw", "opentfraw"):
        try:
            return f"{dist}=={pkg_version(dist)}"
        except PackageNotFoundError:
            continue
    return None


def _match_score_version() -> int | None:
    """The match-score implementation behind expected/ions.parquet (1 = legacy
    Sum(score*rel_ab), 2 = consolidated mascope_tools v2), so a bundle self-identifies
    which scorer produced its goldens. Best-effort; None if the backend is unavailable."""
    try:
        from mascope_backend.api.controllers.match.lib.match_score_v2 import (
            match_score_version,
        )

        return match_score_version()
    except Exception:
        return None


def _copy_raw(raw_dir: Path, out_dir: Path) -> tuple[list[dict], list[dict]]:
    """
    Copy + de-identify raw files into ``out_dir/raw``.

    Each source file is renamed to its de-identified name (instrument aliased,
    redundant timestamp + run index removed) while its byte content is unchanged
    - so the recorded SHA-256 is the hash of the original content under the new
    name.

    :param raw_dir: Source directory of original raw files.
    :param out_dir: Bundle output directory.
    :return: ``(entries, renames)`` where ``entries`` are the published
             ``{name, sha256, bytes}`` manifest entries (de-identified names),
             and ``renames`` are ``{original, new, parsed}`` records for the
             de-identification report.
    :raises FileNotFoundError: If ``raw_dir`` has no ``.raw`` files.
    :raises RuntimeError: If de-identification produces a name collision.
    """
    raw_files = sorted(p for p in raw_dir.iterdir() if p.suffix.lower() == ".raw")
    if not raw_files:
        raise FileNotFoundError(f"No .raw files found in {raw_dir}")

    dest = out_dir / "raw"
    dest.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    renames: list[dict] = []
    seen: dict[str, str] = {}
    unmatched = 0

    for src in raw_files:
        new_name, parsed = _deidentify_name(src.name)
        if not parsed:
            unmatched += 1
            runtime.logger.warning(
                f"Filename did not match the expected pattern, "
                f"copied unchanged: {src.name}"
            )
        if new_name in seen:
            raise RuntimeError(
                f"De-identified name collision: '{new_name}' from both "
                f"'{seen[new_name]}' and '{src.name}'"
            )
        seen[new_name] = src.name

        shutil.copy2(src, dest / new_name)
        entries.append(
            {
                "name": new_name,
                "sha256": bundles.sha256_file(src),
                "bytes": src.stat().st_size,
            }
        )
        renames.append({"original": src.name, "new": new_name, "parsed": parsed})

    runtime.logger.success(
        f"Copied + de-identified {len(entries)} raw file(s) into {dest}"
        + (f" ({unmatched} unmatched)" if unmatched else "")
    )
    return entries, renames


def _write_deid_report(out_dir: Path, renames: list[dict]) -> Path:
    """
    Write a human-readable de-identification report for sign-off.

    Shows the rename transformation applied to every file (original -> published)
    so the data owner can confirm nothing sensitive remains. Because it lists the
    ORIGINAL filenames (including the real instrument label), it is a LOCAL
    sign-off artifact and must never be published. It is therefore written as a
    *sibling* of the bundle directory (``<bundle>.deid_report.md``), not inside
    it, so it cannot be swept into the published archive by accident.

    Embedded ``.raw`` binary metadata (operator, sample comments, instrument
    serial) is intentionally NOT rewritten here - flag anything sensitive
    manually before publishing.

    :param out_dir: Bundle output directory.
    :param renames: ``{original, new, parsed}`` records from :func:`_copy_raw`.
    :return: Path to the written report (a sibling of ``out_dir``).
    """
    parsed = [r["parsed"] for r in renames if r["parsed"]]
    src_instruments = sorted({p["instrument"] for p in parsed})
    samples = sorted({p["sample"] for p in parsed})
    polarities = sorted({p["polarity"] for p in parsed})
    alias_lines = [
        f"  - {inst} -> {INSTRUMENT_ALIASES.get(inst, inst + ' (NOT aliased!)')}"
        for inst in src_instruments
    ]

    lines = [
        "# Demo bundle de-identification report",
        "",
        "> LOCAL SIGN-OFF ARTIFACT - lists original filenames including the real",
        "> instrument label. Do NOT publish this file with the bundle.",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Files: {len(renames)}",
        "",
        "## Transformation applied to filenames",
        "",
        "- Instrument label aliased:",
        *alias_lines,
        "- Removed the redundant human-readable timestamp "
        "(e.g. `2025.08.11-14h23m12s`)",
        "- Removed the run index (the `_1_` segment)",
        "- Kept polarity, sample short-name, RI marker, and the "
        "compact acquisition stamp",
        "",
        f"Sample short-names retained: {', '.join(samples)}",
        f"Polarities: {', '.join(polarities)}",
        "",
        "Confirm the retained sample short-names are not sensitive. The compact",
        "acquisition stamp (and thus acquisition date/time) is retained deliberately.",
        "",
        "## Not checked automatically",
        "",
        "Embedded `.raw` binary metadata (operator name, sample comments,",
        "instrument serial) is not parsed or scrubbed by this tool. Inspect a",
        "sample of files in the vendor software if any of these could be",
        "sensitive for this dataset.",
        "",
        "## Rename map (original -> published)",
        "",
        *[f"- `{r['original']}` -> `{r['new']}`" for r in renames],
        "",
    ]
    # Sibling of the bundle dir, not inside it: keeps the original-name listing
    # out of the publishable tree so it can't be archived by accident.
    report = out_dir.parent / f"{out_dir.name}.deid_report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    runtime.logger.success(f"Wrote de-identification report: {report}")
    return report


def _dump_demo_db(out_dir: Path, subdir: str) -> Path:
    """
    ``pg_dump`` the ``mascope_demo`` database into ``out_dir/subdir``.

    Requires the dev PostgreSQL container to be running with a populated
    ``mascope_demo`` database.

    :param out_dir: Bundle output directory.
    :param subdir: Bundle subdirectory (``"seed"`` or ``"snapshot"``).
    :return: Path to the dump file inside the bundle.
    :raises RuntimeError: If the dump fails.
    """
    from mascope_cli.pg import dirs, pg_dump

    db_cfg = runtime.full_config.backend.database
    container = db_cfg.get_postgres_container_name(mode="dev")
    database = db_cfg.get_postgres_database_name(DEMO_ENV)

    dest_dir = out_dir / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Dump via the standard bind-mounted backups dir, then move into the bundle.
    backups_dir, mount = dirs(transfer=False, mode="dev")
    dump_path = pg_dump(
        container, db_cfg.user, database, backups_dir, mount, label=f"demo-{subdir}"
    )
    final_dump = dest_dir / "mascope_demo.dump"
    shutil.move(str(dump_path), str(final_dump))
    return final_dump


def export_seed(out_dir: Path) -> dict:
    """
    Export the current ``mascope_demo`` database as the reference **seed**.

    Run this when the demo env holds only authored reference data - ionization
    modes/mechanisms, the instrument config, and calibration/diagnostic
    collections - and *no* ingested raw files yet. The seed is what ``--rebuild``
    restores before uploading raw, so the pipeline can calibrate and match.

    :param out_dir: Bundle output directory.
    :return: The manifest ``seed`` block.
    :raises RuntimeError: If the dump fails.
    """
    final_dump = _dump_demo_db(out_dir, "seed")
    runtime.logger.success(f"Exported reference seed to {out_dir / 'seed'}")
    return {
        "dump": "seed/mascope_demo.dump",
        "sha256": bundles.sha256_file(final_dump),
    }


def export_snapshot(out_dir: Path) -> dict:
    """
    Export the current ``mascope_demo`` database + filestore as the full snapshot.

    Run this after a complete rebuild (raw ingested, matching done). This is what
    the instant ``mascope demo`` path restores.

    :param out_dir: Bundle output directory.
    :return: The manifest ``snapshot`` block.
    :raises RuntimeError: If the dump fails.
    """
    final_dump = _dump_demo_db(out_dir, "snapshot")

    # Copy the demo env filestore into the bundle.
    src_filestore = env_dir() / "filestore"
    dest_filestore = out_dir / "snapshot" / "filestore"
    if src_filestore.is_dir():
        if dest_filestore.exists():
            shutil.rmtree(dest_filestore)
        shutil.copytree(src_filestore, dest_filestore)
    else:
        runtime.logger.warning(
            f"No demo filestore at {src_filestore}; snapshot has DB only"
        )

    runtime.logger.success(f"Exported snapshot to {out_dir / 'snapshot'}")
    return {
        "dump": "snapshot/mascope_demo.dump",
        "sha256": bundles.sha256_file(final_dump),
        "filestore": "snapshot/filestore",
    }


def export_goldens(out_dir: Path) -> dict | None:
    """
    Export golden peak outputs for the reproducibility test.

    Reads the matched isotope peaks from the demo database (the ``m/z`` +
    intensity the pipeline produced, keyed by ``target_isotope_formula``) and
    writes them to ``expected/peaks.parquet``. The matching comparison side is
    :func:`verify.compare_peaks`.

    Requires a populated ``mascope_demo`` database (run ``mascope demo
    --rebuild`` and confirm matching first). The demo callback pins
    ``MASCOPE_ENV=demo``, so the in-process query targets ``mascope_demo``.

    :param out_dir: Bundle output directory.
    :return: The manifest ``expected`` block, or ``None`` if no peaks have been
             matched yet (logged as a warning so ``--update`` still records the
             snapshot rather than aborting).
    """
    import pandas as pd

    # Lazy import: pulls the backend DB graph (needs MASCOPE_ENV + the postgres
    # secret, both present in the demo flow). Mirrors `_seed_credentials`.
    from mascope_backend.db.scripts.export_goldens import (
        get_golden_ions,
        get_golden_peaks,
    )

    rows = get_golden_peaks()
    if not rows:
        runtime.logger.warning(
            "No matched peaks in the demo database - skipping golden export. "
            "Run 'mascope demo --rebuild' and confirm matching before --update."
        )
        return None

    dest_dir = out_dir / "expected"
    dest_dir.mkdir(parents=True, exist_ok=True)
    peaks_path = dest_dir / "peaks.parquet"
    pd.DataFrame(rows).to_parquet(peaks_path, index=False)
    runtime.logger.success(f"Exported {len(rows)} golden peak(s) to {peaks_path}")

    expected = {
        "peaks": "expected/peaks.parquet",
        "sha256": bundles.sha256_file(peaks_path),
    }

    # Per-ION scores: this is the level the consolidated v2 match score operates at
    # (the per-isotopologue peaks.parquet above is unchanged by it).
    ion_rows = get_golden_ions()
    if ion_rows:
        ions_path = dest_dir / "ions.parquet"
        pd.DataFrame(ion_rows).to_parquet(ions_path, index=False)
        runtime.logger.success(f"Exported {len(ion_rows)} golden ion(s) to {ions_path}")
        expected["ions"] = "expected/ions.parquet"
        expected["ions_sha256"] = bundles.sha256_file(ions_path)

    return expected


def build(
    out_dir: Path,
    raw_dir: Path | None = None,
    update: bool = False,
    seed: bool = False,
) -> Path:
    """
    Build (or refresh) a demo bundle directory.

    The raw files are the source of truth and only need copying once. The derived
    database dumps are captured at different times in the authoring workflow and
    accumulate in the manifest across invocations:

    - ``seed=True`` exports ``seed/`` - the reference data (ionization modes,
      instrument config, calibration/diagnostic collections) captured *before*
      ingesting raw. Restored by ``mascope demo --rebuild``.
    - ``update=True`` exports ``snapshot/`` (+ filestore) and the ``expected/``
      goldens - the full state captured *after* ingestion + matching. Restored by
      the instant ``mascope demo`` path.

    A block already present in the manifest is preserved when not regenerated, so
    you can run ``--seed`` and ``--update`` in separate steps.

    ``raw_dir`` is required the first time (to populate ``raw/`` + the manifest).
    On a later refresh it may be omitted: the bundle's existing ``raw/`` and
    measurement metadata are reused, skipping the copy + re-hash of every raw
    file. Pass it again only to deliberately refresh the raw set.

    :param out_dir: Bundle output directory (created if missing).
    :param raw_dir: Directory of de-identified raw files (source of truth).
        Required on first build; optional when refreshing an existing bundle.
    :param update: Rebuild the derived snapshot + goldens.
    :param seed: Capture the reference seed dump.
    :return: The output bundle directory.
    :raises FileNotFoundError: If ``raw_dir`` is given but missing, or if it is
        omitted and the bundle has no raw set to reuse.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load any prior manifest first: it carries the derived blocks across runs
    # and, when --raw is omitted, the raw set + measurement metadata to reuse.
    prior: dict = {}
    manifest_path = out_dir / bundles.MANIFEST_NAME
    if manifest_path.is_file():
        prior = json.loads(manifest_path.read_text(encoding="utf-8"))

    if raw_dir is not None:
        # First build, or an explicit raw refresh: (re)copy + de-identify the raw
        # files, recompute their checksums, and rewrite the de-id report. The
        # published instrument label is the alias, never the original.
        if not raw_dir.is_dir():
            raise FileNotFoundError(f"Raw directory not found: {raw_dir}")
        raw_entries, renames = _copy_raw(raw_dir, out_dir)
        _write_deid_report(out_dir, renames)
        parsed = next((r["parsed"] for r in renames if r["parsed"]), {})
        orig_instrument = parsed.get("instrument", "")
        measurement = {
            "instrument": INSTRUMENT_ALIASES.get(orig_instrument, orig_instrument)
            or None,
            "instrument_type": "orbi",
            "acquired": parsed.get("date"),
        }
    elif "raw" in prior:
        # Refresh only: reuse the raw set + metadata already in the bundle so a
        # seed/snapshot capture need not re-copy and re-hash the raw source.
        raw_entries = prior["raw"]
        measurement = prior.get("measurement", {})
        runtime.logger.info(
            f"Reusing {len(raw_entries)} raw file(s) already in {out_dir} "
            "(--raw omitted)"
        )
    else:
        raise FileNotFoundError(
            f"--raw is required: {out_dir} has no raw set in its manifest yet. "
            "Pass --raw once to create the bundle, then omit it on refreshes."
        )

    manifest: dict = {
        "bundle_version": out_dir.name,
        "created": datetime.now(timezone.utc).isoformat(),
        "measurement": measurement,
        "produced_with": {
            "mascope_version": runtime.parse_version(),
            "opentfraw_version": _opentfraw_version(),
            "match_score_version": _match_score_version(),
        },
        "raw": raw_entries,
        # Preserve hand-tuned tolerances across a refresh; default on first build.
        "tolerances": prior.get("tolerances", DEFAULT_TOLERANCES),
    }
    for key in ("seed", "snapshot", "expected"):
        if key in prior:
            manifest[key] = prior[key]

    if seed:
        manifest["seed"] = export_seed(out_dir)
    if update:
        manifest["snapshot"] = export_snapshot(out_dir)
        # Keep any prior goldens (copied above) if this run matched nothing.
        expected = export_goldens(out_dir)
        if expected is not None:
            manifest["expected"] = expected

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    runtime.logger.success(f"Demo bundle built at {out_dir}")
    return out_dir
