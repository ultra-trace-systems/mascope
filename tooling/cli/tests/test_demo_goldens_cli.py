"""
The demo CLI's MS2 golden wiring: what the bundle build exports and counts as
covered, what `mascope demo verify` compares, and what the cached-bundle check
verifies.

Hermetic: the backend's golden-export module is a stand-in in ``sys.modules``
fed per test, so no database is read, and the bundles are directories under
``tmp_path``.

    uv run pytest tooling/cli/tests/test_demo_goldens_cli.py
"""

import json
import sys
import types

import pandas as pd
import pytest

from mascope_cli.cmd.demo import build_bundle, bundles
from mascope_cli.cmd.demo import main as demo_main
from mascope_cli.cmd.demo import verify as verify_mod


MS1 = "Orbion_pos_Ur_NoRI_20250811142302.raw"
MS2 = "Orbion_MS2_15NO3_manual-C10H18O2_20261015034940.raw"


def _db_name(published: str) -> str:
    """The filename the demo database holds for a published bundle filename:
    the converter re-inserts the acquisition timestamp and drops the extension."""
    instrument, rest = published.removesuffix(".raw").split("_", 1)
    stamp = rest.rsplit("_", 1)[-1]
    readable = (
        f"{stamp[0:4]}.{stamp[4:6]}.{stamp[6:8]}-"
        f"{stamp[8:10]}h{stamp[10:12]}m{stamp[12:14]}s"
    )
    return f"{instrument}_{readable}_{rest}"


def _peak(filename: str) -> dict:
    return {
        "filename": filename,
        "target_isotope_id": "iso-urea-1",
        "target_isotope_formula": "CH5N2O+",
        "mz": 61.03964,
        "height": 1000.0,
        "match_score": 0.9,
    }


def _ms2_row(filename: str, intensity: float = 1512.0) -> dict:
    return {
        "filename": filename,
        "polarity": "-",
        "parent_peak_mz": 233.116,
        "activation": "hcd15.00",
        "scan_count": 13,
        "isolation_width": 1.0,
        "mz": 62.9862,
        "intensity": intensity,
        "resolution": 60000.0,
        "signal_to_noise": 40.0,
    }


def _raw_entries(*names: str) -> list[dict]:
    return [{"name": n, "sha256": "x" * 64, "bytes": 1} for n in names]


@pytest.fixture
def backend_goldens(monkeypatch):
    """A stand-in for ``mascope_backend.db.scripts.export_goldens``, fed per test."""
    module = types.ModuleType("mascope_backend.db.scripts.export_goldens")
    module.peaks, module.ms2, module.ions, module.ingested = [], [], [], []
    module.get_golden_peaks = lambda: list(module.peaks)
    module.get_golden_ms2 = lambda: list(module.ms2)
    module.get_golden_ions = lambda: list(module.ions)
    module.get_ingested_filenames = lambda: list(module.ingested)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


# --- the build's export -----------------------------------------------------


def test_a_file_with_ms2_spectra_and_no_matched_peaks_is_covered(
    backend_goldens, tmp_path
):
    """An MS2 acquisition whose MS1 scans matched nothing is covered by its
    spectra, and those are written as the ms2 golden with their checksum."""
    backend_goldens.peaks = [_peak(_db_name(MS1))]
    backend_goldens.ms2 = [_ms2_row(_db_name(MS2))]
    backend_goldens.ingested = [_db_name(MS1), _db_name(MS2)]

    expected = build_bundle.export_goldens(tmp_path, _raw_entries(MS1, MS2))

    assert expected is not None
    assert expected["ms2"] == "expected/ms2.parquet"
    ms2_path = tmp_path / "expected" / "ms2.parquet"
    assert pd.read_parquet(ms2_path).to_dict("records") == backend_goldens.ms2
    assert expected["ms2_sha256"] == bundles.sha256_file(ms2_path)
    assert expected["peaks"] == "expected/peaks.parquet"


def test_a_file_with_neither_peaks_nor_spectra_is_refused(backend_goldens, tmp_path):
    backend_goldens.peaks = [_peak(_db_name(MS1))]
    backend_goldens.ingested = [_db_name(MS1), _db_name(MS2)]

    with pytest.raises(RuntimeError, match="neither matched peaks nor MS2 spectra"):
        build_bundle.export_goldens(tmp_path, _raw_entries(MS1, MS2))

    assert not (tmp_path / "expected").exists()


def test_an_ms1_only_run_records_no_ms2_block(backend_goldens, tmp_path):
    backend_goldens.peaks = [_peak(_db_name(MS1))]
    backend_goldens.ingested = [_db_name(MS1)]

    expected = build_bundle.export_goldens(tmp_path, _raw_entries(MS1))

    assert expected is not None
    assert "ms2" not in expected
    assert "ms2_sha256" not in expected
    assert not (tmp_path / "expected" / "ms2.parquet").exists()


# --- the verify command -----------------------------------------------------


def _bundle_dir(tmp_path, peaks: list[dict], ms2: list[dict] | None):
    """A local bundle directory: a manifest with the goldens it holds."""
    root = tmp_path / "bundle"
    (root / "expected").mkdir(parents=True)
    pd.DataFrame(peaks).to_parquet(root / "expected" / "peaks.parquet", index=False)
    expected = {"peaks": "expected/peaks.parquet"}
    if ms2 is not None:
        pd.DataFrame(ms2).to_parquet(root / "expected" / "ms2.parquet", index=False)
        expected["ms2"] = "expected/ms2.parquet"
    manifest = {
        "bundle_version": "0.0.1",
        "expected": expected,
        "tolerances": {"mz_ppm": 0.1, "intensity_rel": 0.01},
    }
    (root / bundles.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _parquet(tmp_path, name: str, rows: list[dict]):
    path = tmp_path / name
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


@pytest.fixture
def verify_app(monkeypatch):
    """The demo group as a checkout builds it, with the env setup the group's
    callback does left out: `verify --local --actual` needs no demo env.

    The callback still pins ``MASCOPE_ENV`` and the singleton's ``env.name``
    to the demo env; the conftest's reset puts both back after the test.
    """
    monkeypatch.setattr(demo_main, "_ensure_demo_env", lambda: None)
    return demo_main.make_demo_app()


def test_verify_compares_the_ms2_goldens_given_beside_the_peaks(
    cli_runner, verify_app, tmp_path
):
    peaks = [_peak(_db_name(MS1))]
    ms2 = [_ms2_row(_db_name(MS2))]
    root = _bundle_dir(tmp_path, peaks, ms2)
    actual_peaks = _parquet(tmp_path, "actual_peaks.parquet", peaks)
    drifted = _parquet(
        tmp_path,
        "drifted_ms2.parquet",
        [_ms2_row(_db_name(MS2), intensity=1512.0 * 1.05)],
    )
    same = _parquet(tmp_path, "same_ms2.parquet", ms2)
    args = ["verify", "--local", str(root), "--actual", str(actual_peaks)]

    assert (
        cli_runner.invoke(verify_app, [*args, "--actual-ms2", str(drifted)]).exit_code
        == 1
    )
    assert (
        cli_runner.invoke(verify_app, [*args, "--actual-ms2", str(same)]).exit_code == 0
    )


def test_verify_with_peaks_alone_warns_about_the_ms2_goldens_and_passes_on_the_peaks(
    cli_runner, verify_app, tmp_path, monkeypatch
):
    peaks = [_peak(_db_name(MS1))]
    root = _bundle_dir(tmp_path, peaks, [_ms2_row(_db_name(MS2))])
    actual_peaks = _parquet(tmp_path, "actual_peaks.parquet", peaks)
    compared = []
    monkeypatch.setattr(
        verify_mod, "compare_ms2", lambda *a, **k: compared.append(1) or []
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        demo_main.runtime.logger, "warning", lambda m, *a, **k: warnings.append(str(m))
    )

    result = cli_runner.invoke(
        verify_app, ["verify", "--local", str(root), "--actual", str(actual_peaks)]
    )

    assert result.exit_code == 0
    assert compared == []
    assert any("--actual-ms2" in w for w in warnings)


def test_verify_of_an_ms1_only_bundle_compares_no_spectra(
    cli_runner, verify_app, tmp_path, monkeypatch
):
    peaks = [_peak(_db_name(MS1))]
    root = _bundle_dir(tmp_path, peaks, None)
    actual_peaks = _parquet(tmp_path, "actual_peaks.parquet", peaks)
    actual_ms2 = _parquet(tmp_path, "actual_ms2.parquet", [_ms2_row(_db_name(MS2))])

    def _never(*_a, **_k):
        raise AssertionError("compare_ms2 ran against a bundle without MS2 goldens")

    monkeypatch.setattr(verify_mod, "compare_ms2", _never)

    result = cli_runner.invoke(
        verify_app,
        [
            "verify",
            "--local",
            str(root),
            "--actual",
            str(actual_peaks),
            "--actual-ms2",
            str(actual_ms2),
        ],
    )

    assert result.exit_code == 0


# --- the cached-bundle check ------------------------------------------------


def test_cached_bundle_check_verifies_the_ms2_golden(monkeypatch, tmp_path):
    """A truncated or edited ms2.parquet fails the manifest check as an
    edited peaks.parquet does."""
    bundle = bundles.Bundle(version="0.0.1")
    monkeypatch.setitem(bundles.BUNDLES, bundle.version, bundle)
    monkeypatch.setattr(bundles, "cache_root", lambda: tmp_path / "demo")
    root = bundles.bundle_dir(bundle.version)
    (root / "expected").mkdir(parents=True)
    for name in ("peaks", "ms2"):
        (root / "expected" / f"{name}.parquet").write_bytes(f"{name} golden".encode())
    manifest = {
        "bundle_version": bundle.version,
        "raw": [],
        "expected": {
            "peaks": "expected/peaks.parquet",
            "sha256": bundles.sha256_file(root / "expected" / "peaks.parquet"),
            "ms2": "expected/ms2.parquet",
            "ms2_sha256": bundles.sha256_file(root / "expected" / "ms2.parquet"),
        },
    }
    (root / bundles.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    assert bundles.verify_manifest(bundle.version) == []

    (root / "expected" / "ms2.parquet").write_bytes(b"ms2 golden, edited")
    problems = bundles.verify_manifest(bundle.version)
    assert problems == [
        "checksum mismatch: expected/ms2.parquet "
        f"(expected {manifest['expected']['ms2_sha256'][:12]}..., "
        f"got {bundles.sha256_file(root / 'expected' / 'ms2.parquet')[:12]}...)"
    ]

    (root / "expected" / "ms2.parquet").unlink()
    assert bundles.verify_manifest(bundle.version) == ["missing: expected/ms2.parquet"]
