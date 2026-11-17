"""The assignment bench's ``init`` and ``status`` (``tooling/assignment_bench``).

The manifest parser and its three file forms (a demo bundle sample, a corpus
object read from a fake ``corpus checkout``, a file in the manifest's own
folder, checked by its hash) and a set's authors' list, the bundle seed read from a
``pg_dump``'s COPY blocks and loaded through the API, and ``init`` over a fake
instance and a fake API: what a first build records, that a second one over
the same manifest starts nothing and changes nothing, what it does when the
manifest grows or shrinks, and how it names a sample that resolved no mode or
has no peaks. No host, no server.
"""

import copy
import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

from mascope_cli.cmd.demo import bundles


ROOT = Path(__file__).resolve().parents[2] / "assignment_bench"


def _load():
    spec = importlib.util.spec_from_file_location("assignment_bench", ROOT / "bench.py")
    module = importlib.util.module_from_spec(spec)
    # Registered first: its dataclasses look their module up while it loads.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load()
mf = bench.bench_manifest


# --------------------------------------------------------------- fixtures


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _bundle(
    tmp_path,
    version="9.9.9",
    names=("I_neg_Br_a.raw", "I_neg_Br_b.raw", "I_pos_Ur_a.raw"),
):
    """A cached demo bundle: raw files and the manifest listing their hashes."""
    root = tmp_path / "bundles" / version
    (root / "raw").mkdir(parents=True, exist_ok=True)
    raw = []
    for name in names:
        (root / "raw" / name).write_text(name)
        raw.append({"name": name, "sha256": _sha(name), "bytes": len(name)})
    (root / "manifest.json").write_text(
        json.dumps({"bundle_version": version, "raw": raw})
    )
    return lambda v: tmp_path / "bundles" / v


def _set(mode, files, seed=None, **extra):
    return {
        "instrument_class": "orbitrap",
        "mode": mode,
        "profile": "BR",
        "seed": seed or {"bundle": "9.9.9"},
        "files": files,
        **extra,
    }


def _manifest(tmp_path, sets, name="t", **extra):
    path = tmp_path / f"{name}.json"
    path.write_text(
        json.dumps({"schema": mf.SCHEMA, "name": name, "sets": sets, **extra})
    )
    return path


def _two_sets(
    tmp_path, br=("I_neg_Br_a.raw", "I_neg_Br_b.raw"), ur=("I_pos_Ur_a.raw",)
):
    return _manifest(
        tmp_path,
        {
            "Xb": _set(
                "Bromide",
                [{"bundle": "9.9.9", "name": n} for n in br],
                reference_lists="default",
            ),
            "Xu": _set(
                "Uronium", [{"bundle": "9.9.9", "name": n} for n in ur], profile="UR"
            ),
        },
    )


SEED = {
    "ionization_mechanisms": [
        {
            "ionization_mechanism_id": "m1",
            "ionization_mechanism": "[M+Br]-",
            "ionization_mechanism_polarity": "-",
        },
        {
            "ionization_mechanism_id": "m2",
            "ionization_mechanism": "[M-H]-",
            "ionization_mechanism_polarity": "-",
        },
        {
            "ionization_mechanism_id": "m3",
            "ionization_mechanism": "[M+H]+",
            "ionization_mechanism_polarity": "+",
        },
    ],
    "ionization_modes": [
        {
            "ionization_mode_id": "b",
            "ionization_mode_name": "Bromide",
            "ionization_mode_token": "Br",
            "ionization_mode_polarity": "-",
            "ionization_mechanism_ids": ["m1", "m2"],
            "calibration_collection_id": "cb",
            "diagnostic_collection_id": None,
        },
        {
            "ionization_mode_id": "u",
            "ionization_mode_name": "Uronium",
            "ionization_mode_token": "Ur",
            "ionization_mode_polarity": "+",
            "ionization_mechanism_ids": ["m3"],
            "calibration_collection_id": "cu",
            "diagnostic_collection_id": "du",
        },
    ],
    "target_collections": [
        {
            "target_collection_id": c,
            "target_collection_name": n,
            "target_collection_type": t,
            "target_collection_description": "",
        }
        for c, n, t in (
            ("cb", "Br calibrants", "CALIBRANTS"),
            ("cu", "Ur calibrants", "CALIBRANTS"),
            ("du", "Ur monitor", "DIAGNOSTICS"),
            ("x1", "Terpenes", "TARGETS"),
            ("x2", "Twins", "TARGETS"),
            ("x3", "Twins", "TARGETS"),
        )
    ],
    "target_compounds": [
        {
            "target_compound_id": "k1",
            "target_compound_name": "bromide",
            "target_compound_formula": "Br",
            "cas_number": None,
        },
        {
            "target_compound_id": "k2",
            "target_compound_name": "urea",
            "target_compound_formula": "CH4N2O",
            "cas_number": None,
        },
        {
            "target_compound_id": "k3",
            "target_compound_name": "pinene",
            "target_compound_formula": "C10H16",
            "cas_number": None,
        },
    ],
    "collection_members": [
        {"target_collection_id": "cb", "target_compound_id": "k1"},
        {"target_collection_id": "cu", "target_compound_id": "k2"},
        {"target_collection_id": "du", "target_compound_id": "k2"},
        {"target_collection_id": "x1", "target_compound_id": "k3"},
    ],
}


class FakeApi:
    """The instance's API: records what is created and what is uploaded."""

    def __init__(self, instance):
        self.instance = instance
        self.mechanisms = [
            {
                "ionization_mechanism_id": "srv-h",
                "ionization_mechanism": "[M-H]-",
                "ionization_mechanism_polarity": "-",
            }
        ]
        self.modes = []
        self.collections = []
        self.uploads = []
        self.attempts = []
        self.refusals = 0
        #: An UploadError every upload raises, until it is set back to None.
        self.refuse_with = None
        self.peaks = {}
        self.calls = []

    def get_list(self, path):
        return {
            "/api/ionization_mechanisms": self.mechanisms,
            "/api/ionization/modes": self.modes,
        }[path]

    def post(self, path, payload):
        self.calls.append((path, payload))
        if path == "/api/ionization_mechanisms":
            row = {**payload, "ionization_mechanism_id": f"srv-m{len(self.mechanisms)}"}
            self.mechanisms.append(row)
            return row
        if path == "/api/target/collections":
            row = {**payload, "target_collection_id": f"srv-c{len(self.collections)}"}
            self.collections.append(row)
            return row
        if path == "/api/ionization/modes":
            self.modes.append(payload)
            return payload
        raise AssertionError(path)

    def upload(self, file):
        self.attempts.append(file.name)
        if self.refuse_with is not None:
            raise self.refuse_with
        if self.refusals:
            self.refusals -= 1
            raise bench.UploadError("503: no file converter is connected", retry=True)
        self.uploads.append(file.name)
        self.instance.ingest(file)

    def peak_count(self, sample_item_id):
        peaks = self.peaks.get(sample_item_id, 120)
        if isinstance(peaks, Exception):
            raise peaks
        return peaks


class FakeInstance:
    """The bench env: a database of sample files by hash, and a call log."""

    def __init__(self, tmp_path, env="bench"):
        self.env = env
        self.rows = {}
        self.calls = []
        self.mode_of_token = {"Br": "Bromide", "Ur": "Uronium"}
        self.drop_next = set()
        #: Polls of samples() during which every file is still being processed
        #: and the converter still has files queued.
        self.busy_polls = 0
        self.polls = 0
        self.seeds = {"9.9.9": SEED}
        self.secret = tmp_path / "owner_secret.txt"
        self.checks = 0
        #: The env exists and is not the bench's: check_env refuses it.
        self.foreign = False
        self.sha = "c0ffee" * 6 + "abcd"

    def check_env(self):
        # Kept apart from ``calls``: a check reads, it changes nothing.
        self.checks += 1
        if self.foreign:
            raise bench.BenchError(f"env {self.env!r} is not the bench's")

    def bind(self):
        self.calls.append("bind")
        return 3, 8093

    def checkout(self, ref):
        self.calls.append(("checkout", ref))
        return self.sha

    def start(self):
        self.calls.append("start")
        return "http://localhost:8093"

    def stop(self):
        self.calls.append("stop")

    def drop(self):
        self.calls.append("drop")
        self.rows.clear()

    def reference_seed(self, lists):
        self.calls.append(("reference_seed", tuple(lists)))

    def samples(self):
        self.polls += 1
        rows = copy.deepcopy(self.rows)
        if self.polls <= self.busy_polls:
            for row in rows.values():
                row["processing_status"] = "bound"
        return rows

    def streams(self):
        return (["queued.raw"] if self.polls <= self.busy_polls else []), []

    def bundle_seed(self, version):
        self.calls.append(("bundle_seed", version))
        return copy.deepcopy(self.seeds[version])

    def owner_secret(self):
        return self.secret

    def ingest(self, file):
        """What the pipeline makes of an upload: a file, one sample, a mode by token."""
        if file.name in self.drop_next:
            self.drop_next.discard(file.name)
            return
        n = len(self.rows) + 1
        token = file.name.split("_")[2]
        self.rows[file.sha256] = {
            "sha256": file.sha256,
            "sample_file_id": f"F{n}",
            "filename": file.name,
            "processing_status": "done",
            "processing_detail": "Matched 1 sample.",
            "items": [
                {"sample_item_id": f"S{n}", "mode": self.mode_of_token.get(token)}
            ],
        }


@pytest.fixture
def rig(tmp_path):
    instance = FakeInstance(tmp_path)
    api = FakeApi(instance)
    home = bench.Home(tmp_path / "home")
    b = bench.Bench(
        home=home,
        instance=instance,
        connect=lambda base: api,
        bundle_dir=_bundle(tmp_path),
        poll_s=0,
        upload_pause_s=0,
        sleep=lambda s: None,
    )
    return b, instance, api, home


# ---------------------------------------------------------------- manifest


def test_the_demo_manifest_names_the_default_bundles_ms1_samples():
    m = mf.load(ROOT / "manifests" / "demo.json")

    assert list(m.sets) == ["Db", "Du"]
    assert {len(s.files) for s in m.sets.values()} == {80, 81}
    versions = {f.bundle for _, f in m.files()}
    # The decision behind the bench: the demo set is the bundle the CLI fetches by default.
    assert versions == {bundles.DEFAULT_BUNDLE_VERSION}
    assert all(isinstance(f, mf.BundleFile) for _, f in m.files())
    assert m.sets["Db"].mode == "Bromide m/z 82-750" and m.sets["Db"].profile == "BR"
    assert m.sets["Du"].mode == "Uronium m/z 122-740" and m.sets["Du"].profile == "UR"
    assert all("_Br_" in f.name for f in m.sets["Db"].files)
    assert all("_Ur_" in f.name for f in m.sets["Du"].files)
    assert all(
        s.seed == mf.Seed(bundle=bundles.DEFAULT_BUNDLE_VERSION)
        for s in m.sets.values()
    )


def test_a_manifest_takes_both_file_forms_and_a_window(tmp_path):
    sha = _sha("object")
    m = mf.load(
        _manifest(
            tmp_path,
            {
                "D": _set("Bromide", [{"bundle": "9.9.9", "name": "I_neg_Br_a.raw"}]),
                "E": _set(
                    "[S1] Bromide",
                    [{"sha256": sha}],
                    seed={"corpus": "seed.json"},
                    instrument_class="tof",
                    reference_window_ppm={"trust": 8, "search": 25},
                    collections=["Terpenes"],
                    reference_lists=["perfluorocarboxylic-acids"],
                ),
            },
        )
    )

    e = m.sets["E"]
    assert e.files == (mf.CorpusFile(sha),)
    assert e.seed == mf.Seed(corpus="seed.json")
    assert e.reference_window_ppm == mf.Window(8.0, 25.0)
    assert (e.instrument_class, e.collections, e.reference_lists) == (
        "tof",
        ("Terpenes",),
        ("perfluorocarboxylic-acids",),
    )
    assert m.sets["D"].files == (mf.BundleFile("9.9.9", "I_neg_Br_a.raw"),)
    assert m.sets["D"].reference_window_ppm is None


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d.update(schema="other/1"), "schema must be"),
        (
            lambda d: d["sets"].update(DB=d["sets"].pop("D")),
            "as the gate card names one",
        ),
        (lambda d: d["sets"]["D"].update(instrument_class="fticr"), "one of"),
        (lambda d: d["sets"]["D"].pop("mode"), "needs 'mode'"),
        (lambda d: d["sets"]["D"].update(colour="red"), "unknown keys"),
        (lambda d: d["sets"]["D"].update(files=[]), "must list"),
        (
            lambda d: d["sets"]["D"]["files"].append({"sha256": "ABC"}),
            "64 lower-case hex",
        ),
        (
            lambda d: d["sets"]["D"]["files"].append(
                {"bundle": "9", "name": "a", "sha256": "x"}
            ),
            "a file is",
        ),
        (
            lambda d: d["sets"]["D"]["files"].append(
                {"bundle": "9", "name": "raw/a.raw"}
            ),
            "not a path",
        ),
        (
            lambda d: d["sets"]["D"].update(seed={"bundle": "9", "corpus": "s"}),
            "one of bundle, corpus",
        ),
        (
            lambda d: d["sets"]["D"].update(
                reference_window_ppm={"trust": 25, "search": 8}
            ),
            "inside the search",
        ),
        (
            lambda d: d["sets"]["D"].update(
                reference_window_ppm={"trust": 0, "search": 8}
            ),
            "positive",
        ),
        (lambda d: d["sets"]["D"].update(collections=["a", "a"]), "twice"),
        (
            lambda d: d["sets"].update(
                E=_set("Other", [{"bundle": "9.9.9", "name": "I_neg_Br_a.raw"}])
            ),
            "is in set D already",
        ),
        (
            lambda d: d["sets"].update(
                E=_set(
                    "Bromide",
                    [{"bundle": "9.9.9", "name": "x"}],
                    seed={"bundle": "1.0.0"},
                )
            ),
            "seeded from bundle:9.9.9 by another set",
        ),
    ],
)
def test_a_manifest_that_is_wrong_says_where(change, message):
    data = {
        "schema": mf.SCHEMA,
        "name": "t",
        "sets": {"D": _set("Bromide", [{"bundle": "9.9.9", "name": "I_neg_Br_a.raw"}])},
    }
    change(data)
    with pytest.raises(mf.ManifestError, match=message):
        mf.parse(data)


def test_the_digest_follows_the_content_not_the_layout(tmp_path):
    sets = {"D": _set("Bromide", [{"bundle": "9.9.9", "name": "I_neg_Br_a.raw"}])}
    a = mf.parse({"schema": mf.SCHEMA, "name": "t", "sets": sets})
    b = mf.parse(
        json.loads(
            json.dumps({"sets": sets, "name": "t", "schema": mf.SCHEMA}, indent=4)
        )
    )
    sets["D"]["profile"] = "UR"
    c = mf.parse({"schema": mf.SCHEMA, "name": "t", "sets": sets})

    assert a.digest == b.digest != c.digest


def test_bundle_files_resolve_from_the_cache_with_their_hashes(tmp_path):
    m = mf.load(_two_sets(tmp_path))
    files = mf.resolve(m, _bundle(tmp_path))

    got = files["bundle:9.9.9/I_neg_Br_a.raw"]
    assert got.path == tmp_path / "bundles" / "9.9.9" / "raw" / "I_neg_Br_a.raw"
    assert (got.name, got.sha256) == ("I_neg_Br_a.raw", _sha("I_neg_Br_a.raw"))
    assert len(files) == 3


def test_a_file_the_bundle_lacks_is_named(tmp_path):
    m = mf.load(_two_sets(tmp_path, ur=("I_pos_Ur_a.raw", "I_pos_Ur_zz.raw")))

    with pytest.raises(
        mf.ManifestError, match=r"1 file\(s\) not found:\n  set Xu: I_pos_Ur_zz.raw"
    ):
        mf.resolve(m, _bundle(tmp_path))


def test_a_file_the_bundle_lists_but_the_cache_lost_is_named(tmp_path):
    m = mf.load(_two_sets(tmp_path))
    bundle_dir = _bundle(tmp_path)
    (bundle_dir("9.9.9") / "raw" / "I_neg_Br_b.raw").unlink()

    with pytest.raises(
        mf.ManifestError, match="set Xb: I_neg_Br_b.raw is not in bundle 9.9.9"
    ):
        mf.resolve(m, bundle_dir)


def _checkout(tmp_path, names):
    """A fake ``corpus checkout``: raw/ links named for the files, to objects by hash."""
    root = tmp_path / "checkout"
    objects = tmp_path / "objects"
    (root / "raw").mkdir(parents=True)
    objects.mkdir()
    files = []
    for name in names:
        sha = _sha(name)
        (objects / sha).write_text(name)
        (root / "raw" / name).symlink_to(objects / sha)
        files.append({"file": f"raw/{name}", "sha256": sha, "tier": "core"})
    (root / "manifest.json").write_text(json.dumps({"files": files}))
    (root / "seed.json").write_text(json.dumps(SEED))
    return root


def test_corpus_objects_resolve_through_a_checkout_under_their_names(tmp_path):
    root = _checkout(tmp_path, ["S9_neg_Br_x.raw", "S9_neg_Br_y.raw"])
    sha = _sha("S9_neg_Br_y.raw")
    m = mf.parse(
        {
            "schema": mf.SCHEMA,
            "name": "p",
            "sets": {"E": _set("Bromide", [{"sha256": sha}])},
        }
    )

    files = mf.resolve(m, _bundle(tmp_path), root)

    got = files[f"sha256:{sha}"]
    assert (got.path, got.name, got.sha256) == (
        root / "raw" / "S9_neg_Br_y.raw",
        "S9_neg_Br_y.raw",
        sha,
    )
    with pytest.raises(mf.ManifestError, match="give a corpus checkout"):
        mf.resolve(m, _bundle(tmp_path), None)
    absent = mf.parse(
        {
            "schema": mf.SCHEMA,
            "name": "p",
            "sets": {"E": _set("Bromide", [{"sha256": _sha("gone")}])},
        }
    )
    with pytest.raises(mf.ManifestError, match="is not in"):
        mf.resolve(absent, _bundle(tmp_path), root)
    # A link whose object is gone (a checkout older than a store repair).
    (tmp_path / "objects" / sha).unlink()
    with pytest.raises(mf.ManifestError, match=f"object {sha[:12]} is not in"):
        mf.resolve(m, _bundle(tmp_path), root)


def _folder(tmp_path, names, sets_of):
    """A private manifest in a folder of its own, its files and seed beside it."""
    root = tmp_path / "private"
    (root / "raw").mkdir(parents=True)
    for name in names:
        (root / "raw" / name).write_text(name)
    (root / "seed.json").write_text(json.dumps(SEED))
    path = root / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema": mf.SCHEMA,
                "name": "private",
                "sets": sets_of(
                    {n: {"path": f"raw/{n}", "sha256": _sha(n)} for n in names}
                ),
            }
        )
    )
    return path


def test_a_manifest_takes_files_and_a_seed_from_its_own_folder(tmp_path):
    path = _folder(
        tmp_path,
        ["T9_neg_Br_x.h5"],
        lambda f: {
            "Nc": _set(
                "Bromide",
                [f["T9_neg_Br_x.h5"]],
                seed={"file": "seed.json"},
                instrument_class="tof",
            )
        },
    )

    m = mf.load(path)

    assert m.sets["Nc"].files == (
        mf.FolderFile("raw/T9_neg_Br_x.h5", _sha("T9_neg_Br_x.h5")),
    )
    assert m.sets["Nc"].seed == mf.Seed(file="seed.json")
    assert m.folder == path.parent.resolve()
    # The key is the hash, as a corpus object's: one file, however it is named.
    assert m.sets["Nc"].files[0].key == f"sha256:{_sha('T9_neg_Br_x.h5')}"


@pytest.mark.parametrize(
    "file, message",
    [
        ({"path": "../elsewhere/a.h5", "sha256": "a" * 64}, "inside the manifest's"),
        ({"path": "/abs/a.h5", "sha256": "a" * 64}, "inside the manifest's"),
        ({"path": "raw\\a.h5", "sha256": "a" * 64}, "forward slashes"),
        ({"path": "C:/a.h5", "sha256": "a" * 64}, "inside the manifest's"),
        ({"path": "raw/a.h5", "sha256": "A" * 64}, "64 lower-case hex"),
        ({"path": "raw/a.h5"}, "a file is"),
    ],
)
def test_a_folder_file_must_stay_in_the_folder(file, message):
    data = {
        "schema": mf.SCHEMA,
        "name": "t",
        "sets": {"Nc": _set("Bromide", [file], seed={"file": "seed.json"})},
    }
    with pytest.raises(mf.ManifestError, match=message):
        mf.parse(data)
    data["sets"]["Nc"].update(
        files=[{"bundle": "9.9.9", "name": "I_neg_Br_a.raw"}], seed={"file": "../s"}
    )
    with pytest.raises(mf.ManifestError, match="inside the manifest's"):
        mf.parse(data)


def test_folder_files_resolve_beside_the_manifest_and_are_hashed(tmp_path):
    names = ["T9_neg_Br_x.h5", "T9_neg_Br_y.h5"]
    path = _folder(
        tmp_path,
        names,
        lambda f: {"Nc": _set("Bromide", list(f.values()), seed={"file": "seed.json"})},
    )
    m = mf.load(path)

    files = mf.resolve(m, _bundle(tmp_path))

    got = files[f"sha256:{_sha('T9_neg_Br_y.h5')}"]
    assert (got.path, got.name, got.sha256) == (
        path.parent.resolve() / "raw" / "T9_neg_Br_y.h5",
        "T9_neg_Br_y.h5",
        _sha("T9_neg_Br_y.h5"),
    )
    # Parsed from an object, the manifest has no folder to read them from.
    with pytest.raises(mf.ManifestError, match="load the manifest from its file"):
        mf.resolve(mf.parse(m.source), _bundle(tmp_path))
    # A file whose content moved on since the manifest named it is refused.
    (path.parent / "raw" / "T9_neg_Br_x.h5").write_text("something else")
    with pytest.raises(
        mf.ManifestError,
        match=r"1 file\(s\) hash to something other.*\n  set Nc: raw/T9",
    ):
        mf.resolve(m, _bundle(tmp_path))
    (path.parent / "raw" / "T9_neg_Br_x.h5").unlink()
    with pytest.raises(mf.ManifestError, match=r"1 file\(s\) not found"):
        mf.resolve(m, _bundle(tmp_path))


def _truth(tmp_path, name, formulas):
    """An authors' list in the private folder, and the manifest's entry for it."""
    folder = tmp_path / "private" / "truth"
    folder.mkdir(parents=True, exist_ok=True)
    text = json.dumps(formulas)
    (folder / f"{name}.json").write_text(text)
    return {"file": f"truth/{name}.json", "sha256": _sha(text)}


def _with_truth(tmp_path, truth, **extra):
    """A private manifest of one TOF set that names ``truth``."""
    return _folder(
        tmp_path,
        ["T9_neg_Br_x.h5"],
        lambda f: {
            "Nc": _set(
                "Bromide",
                [f["T9_neg_Br_x.h5"]],
                seed={"file": "seed.json"},
                instrument_class="tof",
                truth=truth,
                **extra,
            )
        },
    )


def test_a_set_names_its_authors_list_by_its_path_and_hash(tmp_path):
    truth = _truth(tmp_path, "Nc", ["C10H15NO8", "C10 H16 O3", "NO8C10H15"])
    m = mf.load(_with_truth(tmp_path, truth))

    assert m.sets["Nc"].truth == mf.Truth("truth/Nc.json", truth["sha256"])
    # Read as written, spaces dropped; the card compares by element counts.
    assert mf.read_truth(m) == {
        "Nc": {
            "sha256": truth["sha256"],
            "formulas": ["C10H15NO8", "C10H16O3", "NO8C10H15"],
        }
    }
    # A set without one reads none; the list's hash is part of the digest.
    assert mf.read_truth(mf.load(_two_sets(tmp_path))) == {}
    other = copy.deepcopy(m.source)
    other["sets"]["Nc"]["truth"]["sha256"] = "b" * 64
    assert mf.parse(other).digest != m.digest


@pytest.mark.parametrize(
    "truth, message",
    [
        ({"file": "../t.json", "sha256": "a" * 64}, "inside the manifest's"),
        ({"file": "truth\\t.json", "sha256": "a" * 64}, "forward slashes"),
        ({"file": "t.json"}, "an authors' list is"),
        (["C10H16O3"], "an authors' list is"),
        ({"file": "t.json", "sha256": "A" * 64}, "64 lower-case hex"),
    ],
)
def test_an_authors_list_is_a_file_in_the_folder_with_its_hash(truth, message):
    data = {
        "schema": mf.SCHEMA,
        "name": "t",
        "sets": {
            "Nc": _set(
                "Bromide",
                [{"bundle": "9.9.9", "name": "I_neg_Br_a.raw"}],
                truth=truth,
            )
        },
    }
    with pytest.raises(mf.ManifestError, match=message):
        mf.parse(data)


def test_an_authors_list_that_is_not_one_stops_before_anything(tmp_path):
    truth = _truth(tmp_path, "Nc", ["C10H16O3"])
    path = _with_truth(tmp_path, truth)
    m = mf.load(path)
    folder = path.parent

    # Parsed from an object, the manifest has no folder to read it from.
    with pytest.raises(mf.ManifestError, match="load the manifest from its file"):
        mf.read_truth(mf.parse(m.source))
    # A list whose content moved on since the manifest named it.
    (folder / "truth" / "Nc.json").write_text(json.dumps(["C10H16O3", "C5H8O4"]))
    with pytest.raises(mf.ManifestError, match="hashes to something other"):
        mf.read_truth(m)
    (folder / "truth" / "Nc.json").unlink()
    with pytest.raises(mf.ManifestError, match=r"truth/Nc.json is not in"):
        mf.read_truth(m)

    # An ion, a charge or an adduct is not a neutral formula; the entry is
    # named by its place, never by its text.
    for bad, where in (
        (["C10H16O3", "C10H15NO8NO3-"], "[1]"),
        (["[13C]C9H16O3"], "[0]"),
        (["C10H16O3", 42], "[1]"),
        ({"formulas": ["C10H16O3"]}, "not a list"),
        ([], "not a list"),
    ):
        entry = _truth(tmp_path, "Nc", bad)
        data = json.loads(path.read_text())
        data["sets"]["Nc"]["truth"] = entry
        path.write_text(json.dumps(data))
        with pytest.raises(mf.ManifestError) as raised:
            mf.read_truth(mf.load(path))
        assert where in str(raised.value)
        assert "NO3-" not in str(raised.value)


# -------------------------------------------------------------------- seed


COPY_SQL = (
    "SET statement_timeout = 0;\n"
    "COPY public.ionization_mode (ionization_mode_id, ionization_mode_name, ionization_mode_token, "
    "ionization_mode_polarity, ionization_mechanism_ids, calibration_collection_id, "
    "diagnostic_collection_id, system_key, instrument) FROM stdin;\n"
    'b\tBromide m/z 82-750\tBr_NoRI\t-\t["m1", "m2"]\tcb\t\\N\t\\N\t\\N\n'
    'sysBromide\tBromide, negative\t\\N\t-\t["m1"]\t\\N\t\\N\tbromide\t\\N\n'
    "\\.\n\n"
    "COPY public.target_collection (target_collection_id, target_collection_name, "
    "target_collection_description, target_collection_type, workspace_id) FROM stdin;\n"
    "cb\tBr calibrants\tline one\\nline two\\ttab \\\\ back\tCALIBRANTS\t\\N\n"
    "\\.\n"
    'COPY public."user" (id, email) FROM stdin;\n'
    "\\.\n"
)


def test_a_dumps_copy_blocks_read_as_rows_with_escapes_and_nulls():
    tables = bench.parse_copy(COPY_SQL)

    assert tables["user"] == []
    (coll,) = tables["target_collection"]
    assert coll["target_collection_description"] == "line one\nline two\ttab \\ back"
    assert coll["workspace_id"] is None
    assert [r["ionization_mode_id"] for r in tables["ionization_mode"]] == [
        "b",
        "sysBromide",
    ]


def test_a_bundle_seed_keeps_the_user_modes_in_the_corpus_seed_shape():
    seed = bench.seed_from_tables(bench.parse_copy(COPY_SQL))

    (mode,) = seed["ionization_modes"]
    assert mode == {
        "ionization_mode_id": "b",
        "ionization_mode_name": "Bromide m/z 82-750",
        "ionization_mode_token": "Br_NoRI",
        "ionization_mode_polarity": "-",
        "ionization_mechanism_ids": ["m1", "m2"],
        "calibration_collection_id": "cb",
        "diagnostic_collection_id": None,
    }
    assert [c["target_collection_name"] for c in seed["target_collections"]] == [
        "Br calibrants"
    ]


def test_a_selection_takes_a_modes_mechanisms_and_collections():
    chosen = bench.select_seed(SEED, ["Uronium"], ["Terpenes"])

    assert [m["ionization_mode_name"] for m in chosen["ionization_modes"]] == [
        "Uronium"
    ]
    assert [m["ionization_mechanism_id"] for m in chosen["ionization_mechanisms"]] == [
        "m3"
    ]
    assert [c["target_collection_id"] for c in chosen["target_collections"]] == [
        "cu",
        "du",
        "x1",
    ]
    assert {c["target_compound_id"] for c in chosen["target_compounds"]} == {"k2", "k3"}
    with pytest.raises(bench.BenchError, match="no mode"):
        bench.select_seed(SEED, ["Nitrate"], [])
    with pytest.raises(bench.BenchError, match="2 collections named 'Twins'"):
        bench.select_seed(SEED, ["Uronium"], ["Twins"])


def test_a_seed_loads_through_the_api_once(tmp_path):
    api = FakeApi(FakeInstance(tmp_path))
    seeded = {"modes": {}, "collections": {}, "reference_lists": []}
    chosen = bench.select_seed(SEED, ["Bromide", "Uronium"], ["Terpenes"])

    assert bench.load_seed(api, chosen, "bundle:9.9.9", seeded) == []

    # [M-H]- was on the server already: two mechanisms are new.
    assert [m["ionization_mechanism"] for m in api.mechanisms[1:]] == [
        "[M+Br]-",
        "[M+H]+",
    ]
    names = [c["target_collection_name"] for c in api.collections]
    assert names == ["Br calibrants", "Ur calibrants", "Ur monitor", "Terpenes"]
    assert (
        api.collections[0]["target_compounds_create"][0]["target_compound_formula"]
        == "Br"
    )
    bromide, uronium = api.modes
    assert bromide == {
        "ionization_mode_name": "Bromide",
        "ionization_mode_polarity": "-",
        "ionization_mechanism_ids": ["srv-m1", "srv-h"],
        "ionization_mode_token": "Br",
        "calibration_collection_id": "srv-c0",
    }
    assert (
        uronium["calibration_collection_id"],
        uronium["diagnostic_collection_id"],
    ) == ("srv-c1", "srv-c2")
    assert seeded["modes"] == {"Bromide": "bundle:9.9.9", "Uronium": "bundle:9.9.9"}
    assert seeded["collections"]["bundle:9.9.9#Terpenes"] == "srv-c3"

    posts = len(api.calls)
    assert bench.load_seed(api, chosen, "bundle:9.9.9", seeded) == []
    assert len(api.calls) == posts


def test_a_mode_whose_collection_failed_is_not_created(tmp_path):
    api = FakeApi(FakeInstance(tmp_path))
    real_post = api.post

    def refuse_calibrants(path, payload):
        if payload.get("target_collection_name") == "Br calibrants":
            return None
        return real_post(path, payload)

    api.post = refuse_calibrants
    seeded = {"modes": {}, "collections": {}, "reference_lists": []}

    problems = bench.load_seed(
        api, bench.select_seed(SEED, ["Bromide"], []), "s", seeded
    )

    assert problems == [
        "collection 'Br calibrants' could not be created",
        "mode 'Bromide' is missing ['calibration_collection_id']; not created",
    ]
    assert api.modes == [] and seeded["modes"] == {}


# -------------------------------------------------------------------- init


def test_a_first_init_builds_the_sets_and_records_every_sample(rig, tmp_path):
    b, instance, api, home = rig

    assert b.init(_two_sets(tmp_path), ref="develop") == 0

    state = json.loads(home.state.read_text())
    assert (state["env"], state["slot"], state["api_port"]) == ("bench", 3, 8093)
    assert state["build"]["ref"] == "develop"
    assert sorted(api.uploads) == ["I_neg_Br_a.raw", "I_neg_Br_b.raw", "I_pos_Ur_a.raw"]
    xb = state["sets"]["Xb"]
    assert xb["mode"] == "Bromide" and xb["profile"] == "BR"
    record = xb["samples"]["bundle:9.9.9/I_neg_Br_a.raw"]
    assert record["problems"] == []
    assert (
        record["sample_file_id"]
        == instance.rows[_sha("I_neg_Br_a.raw")]["sample_file_id"]
    )
    assert record["items"] == [
        {
            "sample_item_id": instance.rows[_sha("I_neg_Br_a.raw")]["items"][0][
                "sample_item_id"
            ],
            "mode": "Bromide",
            "peaks": 120,
        }
    ]
    assert list(state["sets"]["Xu"]["samples"]) == ["bundle:9.9.9/I_pos_Ur_a.raw"]
    assert state["seeded"]["modes"] == {
        "Bromide": "bundle:9.9.9",
        "Uronium": "bundle:9.9.9",
    }
    assert state["seeded"]["reference_lists"] == ["default"]
    assert ("reference_seed", ("default",)) in instance.calls
    # The manifest's copy lies beside the state, and the backend was stopped.
    assert json.loads(home.manifest.read_text())["name"] == "t"
    assert instance.calls[-1] == "stop"


def test_a_second_init_over_the_same_manifest_changes_nothing(rig, tmp_path):
    b, instance, api, home = rig
    path = _two_sets(tmp_path)
    b.init(path)
    before = home.state.read_bytes()
    instance.calls.clear()
    posts = len(api.calls)

    assert b.init(path) == 0

    assert instance.calls == []
    assert len(api.calls) == posts and len(api.uploads) == 3
    assert home.state.read_bytes() == before


def test_a_grown_manifest_uploads_only_what_is_new(rig, tmp_path):
    b, instance, api, home = rig
    b.init(_two_sets(tmp_path, br=("I_neg_Br_a.raw",)))
    api.uploads.clear()
    instance.calls.clear()

    assert b.init(_two_sets(tmp_path)) == 0

    assert api.uploads == ["I_neg_Br_b.raw"]
    assert not any(
        c[0] == "bundle_seed" for c in instance.calls if isinstance(c, tuple)
    )
    assert not any(
        c[0] == "reference_seed" for c in instance.calls if isinstance(c, tuple)
    )
    state = json.loads(home.state.read_text())
    assert len(state["sets"]["Xb"]["samples"]) == 2


def test_a_file_the_instance_already_holds_is_not_uploaded_again(rig, tmp_path):
    b, instance, api, home = rig
    path = _two_sets(tmp_path)
    files = mf.resolve(mf.load(path), _bundle(tmp_path))
    # An earlier init uploaded it and stopped before it wrote the state.
    instance.ingest(files["bundle:9.9.9/I_neg_Br_a.raw"])

    assert b.init(path) == 0

    assert sorted(api.uploads) == ["I_neg_Br_b.raw", "I_pos_Ur_a.raw"]
    state = json.loads(home.state.read_text())
    assert (
        state["sets"]["Xb"]["samples"]["bundle:9.9.9/I_neg_Br_a.raw"]["sample_file_id"]
        == "F1"
    )


@pytest.mark.parametrize(
    "shrunk, message",
    [
        (
            lambda tmp_path: _two_sets(tmp_path, br=("I_neg_Br_a.raw",)),
            "Xb's bundle:9.9.9/I_neg_Br_b.raw",
        ),
        (
            lambda tmp_path: _manifest(
                tmp_path,
                {
                    "Xb": _set(
                        "Bromide",
                        [
                            {"bundle": "9.9.9", "name": n}
                            for n in ("I_neg_Br_a.raw", "I_neg_Br_b.raw")
                        ],
                    )
                },
            ),
            "set Xu is in the instance",
        ),
    ],
)
def test_a_shrunk_manifest_is_refused(rig, tmp_path, shrunk, message):
    b, instance, api, home = rig
    b.init(_two_sets(tmp_path))
    instance.calls.clear()

    with pytest.raises(bench.BenchError, match=message):
        b.init(shrunk(tmp_path))
    assert instance.calls == []


def test_a_set_whose_mode_changed_is_refused(rig, tmp_path):
    b, instance, api, home = rig
    b.init(_two_sets(tmp_path))
    data = json.loads(_two_sets(tmp_path).read_text())
    data["sets"]["Xu"]["mode"] = "Bromide"
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(data))

    with pytest.raises(
        bench.BenchError, match="Xu's mode changed from 'Uronium' to 'Bromide'"
    ):
        b.init(path)


def test_a_sample_without_a_mode_or_peaks_is_named_and_checked_again(rig, tmp_path):
    b, instance, api, home = rig
    del instance.mode_of_token["Ur"]
    api.peaks = {"S2": 0}

    assert b.init(_two_sets(tmp_path)) == 1

    state = json.loads(home.state.read_text())
    ur = state["sets"]["Xu"]["samples"]["bundle:9.9.9/I_pos_Ur_a.raw"]
    item = ur["items"][0]["sample_item_id"]
    assert ur["problems"] == [f"sample {item} resolved no mode"]
    br_b = state["sets"]["Xb"]["samples"]["bundle:9.9.9/I_neg_Br_b.raw"]
    assert br_b["problems"] == ["sample S2 has no peaks"]

    # Fixed on the server (a mode added by hand): the next init looks again
    # without uploading anything.
    instance.rows[_sha("I_pos_Ur_a.raw")]["items"][0]["mode"] = "Uronium"
    api.peaks = {}
    instance.calls.clear()
    assert b.init(_two_sets(tmp_path)) == 0
    assert len(api.uploads) == 3 and "start" in instance.calls
    state = json.loads(home.state.read_text())
    assert (
        state["sets"]["Xu"]["samples"]["bundle:9.9.9/I_pos_Ur_a.raw"]["problems"] == []
    )


def test_a_sample_that_resolved_another_mode_is_named(rig, tmp_path):
    b, instance, api, home = rig
    instance.mode_of_token["Ur"] = "Bromide"

    assert b.init(_two_sets(tmp_path)) == 1

    state = json.loads(home.state.read_text())
    (problem,) = state["sets"]["Xu"]["samples"]["bundle:9.9.9/I_pos_Ur_a.raw"][
        "problems"
    ]
    assert problem.endswith("resolved 'Bromide', not 'Uronium'")


def test_a_file_that_ends_unprocessed_is_named(rig, tmp_path):
    b, instance, api, home = rig
    real_ingest = instance.ingest

    def needs_chemistry(file):
        real_ingest(file)
        if "_Ur_" in file.name:
            row = instance.rows[file.sha256]
            row.update(
                processing_status="needs_chemistry",
                processing_detail="No mode.",
                items=[],
            )

    instance.ingest = needs_chemistry

    assert b.init(_two_sets(tmp_path)) == 1

    state = json.loads(home.state.read_text())
    assert state["sets"]["Xu"]["samples"]["bundle:9.9.9/I_pos_Ur_a.raw"][
        "problems"
    ] == [
        "needs_chemistry: No mode.",
        "no samples",
    ]


def test_uploads_hold_on_while_no_converter_is_connected(rig, tmp_path):
    b, instance, api, home = rig
    api.refusals = 4

    assert b.init(_two_sets(tmp_path)) == 0
    assert len(api.uploads) == 3


def test_init_waits_for_the_pipeline_before_it_checks(rig, tmp_path):
    b, instance, api, home = rig
    instance.busy_polls = 4

    assert b.init(_two_sets(tmp_path)) == 0

    assert instance.polls > 4
    state = json.loads(home.state.read_text())
    assert {
        r["status"] for s in state["sets"].values() for r in s["samples"].values()
    } == {"done"}


def test_a_pipeline_that_does_not_settle_is_named(rig, tmp_path):
    b, instance, api, home = rig
    instance.busy_polls = 10**6
    b.wait_s = 0

    assert b.init(_two_sets(tmp_path)) == 1

    state = json.loads(home.state.read_text())
    (problem,) = state["sets"]["Xu"]["samples"]["bundle:9.9.9/I_pos_Ur_a.raw"][
        "problems"
    ]
    assert problem == "still processing (bound)"


def test_a_file_the_pipeline_lost_is_uploaded_once_more(rig, tmp_path):
    b, instance, api, home = rig
    instance.drop_next = {"I_neg_Br_b.raw"}

    assert b.init(_two_sets(tmp_path)) == 0

    assert api.uploads.count("I_neg_Br_b.raw") == 2
    assert api.uploads.count("I_neg_Br_a.raw") == 1


def test_a_rebuild_drops_the_instance_and_builds_it_again(rig, tmp_path):
    b, instance, api, home = rig
    path = _two_sets(tmp_path)
    b.init(path)
    home.creds.write_text("{}")
    api.uploads.clear()
    api.modes.clear()
    instance.calls.clear()

    assert b.init(path, rebuild=True) == 0

    assert instance.calls[:2] == ["stop", "drop"]
    assert not home.creds.exists()
    assert sorted(api.uploads) == ["I_neg_Br_a.raw", "I_neg_Br_b.raw", "I_pos_Ur_a.raw"]
    assert ("bundle_seed", "9.9.9") in instance.calls


def test_a_rebuild_discards_a_restore_point_left_behind(rig, tmp_path):
    b, instance, api, home = rig
    path = _two_sets(tmp_path)
    b.init(path)
    dump = tmp_path / "bench_restore.dump"
    dump.write_text("old")
    home.write_private(
        home.restore_point,
        json.dumps({"dump": str(dump), "revisions": ["r2"], "ref": "t", "sha": "b"}),
    )

    assert b.init(path, rebuild=True) == 0

    assert not home.restore_point.exists()
    assert not dump.exists()


def test_keep_running_leaves_the_backend_up(rig, tmp_path):
    b, instance, api, home = rig

    b.init(_two_sets(tmp_path), keep_running=True)

    assert "start" in instance.calls and "stop" not in instance.calls


def test_a_private_manifest_builds_from_a_corpus_checkout(rig, tmp_path):
    b, instance, api, home = rig
    root = _checkout(tmp_path, ["S9_neg_Br_x.raw", "S9_pos_Ur_y.raw"])
    path = _manifest(
        tmp_path,
        {
            "E": _set(
                "Bromide",
                [{"sha256": _sha("S9_neg_Br_x.raw")}],
                seed={"corpus": "seed.json"},
                collections=["Terpenes"],
            ),
            "F": _set(
                "Uronium",
                [{"sha256": _sha("S9_pos_Ur_y.raw")}],
                seed={"corpus": "seed.json"},
            ),
        },
        name="private",
        corpus_checkout=str(root),
    )

    assert b.init(path) == 0

    assert sorted(api.uploads) == ["S9_neg_Br_x.raw", "S9_pos_Ur_y.raw"]
    assert not any(
        isinstance(c, tuple) and c[0] == "bundle_seed" for c in instance.calls
    )
    assert {m["ionization_mode_name"] for m in api.modes} == {"Bromide", "Uronium"}
    assert "Terpenes" in {c["target_collection_name"] for c in api.collections}
    state = json.loads(home.state.read_text())
    assert state["seeded"]["collections"]["corpus:seed.json#Terpenes"]
    assert list(state["sets"]["E"]["samples"]) == [f"sha256:{_sha('S9_neg_Br_x.raw')}"]
    instance.calls.clear()
    assert b.init(path) == 0 and instance.calls == []


def state_modes(home):
    """The modes the bench's state says it seeded, with the seed each came from."""
    return json.loads(home.state.read_text())["seeded"]["modes"]


def test_a_private_manifest_builds_from_files_in_its_folder(rig, tmp_path):
    b, instance, api, home = rig
    path = _folder(
        tmp_path,
        ["T9_neg_Br_x.h5"],
        lambda f: {
            "Nc": _set(
                "Bromide",
                [f["T9_neg_Br_x.h5"]],
                seed={"file": "seed.json"},
                instrument_class="tof",
                collections=["Terpenes"],
            ),
            # A bundle set beside it, as the demo sets sit beside the private ones.
            "Xu": _set("Uronium", [{"bundle": "9.9.9", "name": "I_pos_Ur_a.raw"}]),
        },
    )

    assert b.init(path) == 0

    assert sorted(api.uploads) == ["I_pos_Ur_a.raw", "T9_neg_Br_x.h5"]
    # Each set's mode from its own seed: the bundle's dump, and the folder's file.
    seeded_from_bundle = [
        c for c in instance.calls if isinstance(c, tuple) and c[0] == "bundle_seed"
    ]
    assert seeded_from_bundle == [("bundle_seed", "9.9.9")]
    assert state_modes(home) == {"Bromide": "file:seed.json", "Uronium": "bundle:9.9.9"}
    assert {m["ionization_mode_name"] for m in api.modes} == {"Bromide", "Uronium"}
    assert "Terpenes" in {c["target_collection_name"] for c in api.collections}
    state = json.loads(home.state.read_text())
    assert state["seeded"]["collections"]["file:seed.json#Terpenes"]
    assert list(state["sets"]["Nc"]["samples"]) == [f"sha256:{_sha('T9_neg_Br_x.h5')}"]
    instance.calls.clear()
    assert b.init(path) == 0 and instance.calls == []


def test_init_keeps_an_authors_list_in_the_state_and_never_seeds_it(rig, tmp_path):
    b, instance, api, home = rig
    formulas = ["C10H15NO8", "C7H9NO5"]
    path = _with_truth(
        tmp_path, _truth(tmp_path, "Nc", formulas), collections=["Terpenes"]
    )

    assert b.init(path) == 0

    state = json.loads(home.state.read_text())
    assert state["sets"]["Nc"]["truth"] == {
        "sha256": _sha(json.dumps(formulas)),
        "formulas": formulas,
    }
    # The engine reads a batch's target collections as known targets: the
    # list reaches no collection, no compound and no upload of the instance.
    sent = json.dumps(api.calls) + json.dumps(api.uploads)
    assert not any(formula in sent for formula in formulas)
    assert "Nc.json" not in sent
    out = io.StringIO()
    b.status(out)
    assert "authors' list of 2 formulas" in out.getvalue()

    # A list that changes is a manifest that changes: recorded, nothing started.
    instance.calls.clear()
    data = json.loads(path.read_text())
    data["sets"]["Nc"]["truth"] = _truth(tmp_path, "Nc", formulas + ["C5H8O4"])
    path.write_text(json.dumps(data))
    assert b.init(path) == 0
    assert instance.calls == []
    state = json.loads(home.state.read_text())
    assert state["sets"]["Nc"]["truth"]["formulas"] == formulas + ["C5H8O4"]

    # And one the manifest no longer names leaves the state.
    del data["sets"]["Nc"]["truth"]
    path.write_text(json.dumps(data))
    assert b.init(path) == 0
    assert "truth" not in json.loads(home.state.read_text())["sets"]["Nc"]


def test_a_list_that_is_not_one_stops_init_before_the_instance(rig, tmp_path):
    b, instance, api, home = rig
    truth = _truth(tmp_path, "Nc", ["C10H16O3"])
    path = _with_truth(tmp_path, truth)
    (path.parent / "truth" / "Nc.json").write_text(json.dumps(["C10H15NO8NO3-"]))

    with pytest.raises(mf.ManifestError, match="hashes to something other"):
        b.init(path)
    assert instance.calls == [] and not home.state.exists()


def test_status_lists_the_env_and_the_sets(rig, tmp_path):
    b, instance, api, home = rig
    out = io.StringIO()
    assert b.status(out) == 1
    assert "no bench in" in out.getvalue()

    b.init(_two_sets(tmp_path))
    out = io.StringIO()
    assert b.status(out) == 0

    text = out.getvalue()
    assert "env       bench  (slot 3, api :8093)" in text
    assert "started   develop at c0ffeec0f" in text
    assert (
        "Xb   orbitrap BR     2/2 files ready, 2 samples, mode 'Bromide', "
        "window reference default, built at c0ffeec0f"
    ) in text
    assert "Xu   orbitrap UR     1/1 files ready, 1 samples, mode 'Uronium'" in text

    # A state from before builds were kept per file: the state's build.
    state = json.loads(home.state.read_text())
    for held in state["sets"].values():
        for record in held["samples"].values():
            del record["build"]
    home.state.write_text(json.dumps(state))
    out = io.StringIO()
    b.status(out)
    assert out.getvalue().count("built at c0ffeec0f") == 2
    assert "reference none yet" in text and "cards     none yet" in text


def test_a_state_of_another_env_is_refused(rig, tmp_path):
    b, instance, api, home = rig
    b.init(_two_sets(tmp_path))
    instance.env = "other"

    with pytest.raises(bench.BenchError, match="state of env 'bench', not 'other'"):
        b.init(_two_sets(tmp_path))


def test_the_lock_refuses_a_second_command(tmp_path):
    home = bench.Home(tmp_path / "home")
    with home.lock():
        with pytest.raises(bench.BenchError, match="holds the lock"):
            with home.lock():
                pass


def test_count_peaks_reads_the_answer_however_it_is_wrapped():
    assert bench.count_peaks({"data": {"mz": [1.0, 2.0], "heights": [3, 4]}}) == 2
    assert bench.count_peaks({"mz": [1.0]}) == 1
    assert bench.count_peaks({"data": {"mz": []}}) == 0
    assert bench.count_peaks({"data": None}) == 0


# ------------------------------------------------------- what can go wrong


def test_a_file_never_ingested_is_uploaded_on_the_next_init(rig, tmp_path):
    b, instance, api, home = rig
    api.refuse_with = bench.UploadError("[HTTP 503] no converter", retry=True)

    assert b.init(_two_sets(tmp_path)) == 1
    state = json.loads(home.state.read_text())
    record = state["sets"]["Xu"]["samples"]["bundle:9.9.9/I_pos_Ur_a.raw"]
    assert record["problems"] == ["never ingested"]

    api.refuse_with = None
    assert b.init(_two_sets(tmp_path)) == 0

    assert sorted(api.uploads) == ["I_neg_Br_a.raw", "I_neg_Br_b.raw", "I_pos_Ur_a.raw"]
    state = json.loads(home.state.read_text())
    assert all(
        not r["problems"] for s in state["sets"].values() for r in s["samples"].values()
    )


def test_only_an_upload_that_may_pass_is_tried_again(rig, tmp_path):
    b, instance, api, home = rig
    api.refuse_with = bench.UploadError("[HTTP 401] token refused", retry=False)

    assert b.init(_two_sets(tmp_path)) == 1
    assert sorted(api.attempts) == [
        "I_neg_Br_a.raw",
        "I_neg_Br_b.raw",
        "I_pos_Ur_a.raw",
    ]

    api.attempts.clear()
    api.refuse_with = bench.UploadError("[HTTP 503] no converter", retry=True)
    assert b.init(_two_sets(tmp_path)) == 1
    assert api.attempts.count("I_pos_Ur_a.raw") == bench.UPLOAD_ATTEMPTS


def test_an_sdk_error_is_retried_only_on_a_503_or_a_dropped_connection():
    from mascope_sdk.exceptions import (
        AuthenticationError,
        ConflictError,
        MascopeConnectionError,
        MascopeTimeoutError,
        ServerError,
        ValidationError,
    )

    def retry(error):
        return bench.upload_error(error).retry

    assert retry(ServerError("no converter", status_code=503))
    assert retry(MascopeConnectionError("refused"))
    assert retry(MascopeTimeoutError("slow"))
    assert not retry(ServerError("crashed", status_code=500))
    assert not retry(AuthenticationError("token", status_code=401))
    assert not retry(ValidationError("name", status_code=400))
    assert not retry(ConflictError("hash", status_code=409))
    assert "[HTTP 401]" in str(
        bench.upload_error(AuthenticationError("token", status_code=401))
    )


def _changed(tmp_path, name, change):
    data = json.loads(_two_sets(tmp_path).read_text())
    change(data["sets"])
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(data))
    return path


def test_a_manifest_that_only_reads_its_sets_another_way_is_recorded(rig, tmp_path):
    b, instance, api, home = rig
    b.init(_two_sets(tmp_path))
    instance.calls.clear()
    posts = len(api.calls)

    def reread(sets):
        sets["Xu"].update(
            profile="ZZ",
            label="read anew",
            instrument_class="tof",
            reference_window_ppm={"trust": 8, "search": 25},
        )

    path = _changed(tmp_path, "reread", reread)
    assert b.init(path) == 0

    assert instance.calls == [] and len(api.calls) == posts
    state = json.loads(home.state.read_text())
    xu = state["sets"]["Xu"]
    assert (xu["profile"], xu["label"], xu["instrument_class"]) == (
        "ZZ",
        "read anew",
        "tof",
    )
    assert xu["reference_window_ppm"] == {"trust": 8.0, "search": 25.0}
    assert state["manifest"]["digest"] == mf.load(path).digest
    assert json.loads(home.manifest.read_text())["sets"]["Xu"]["profile"] == "ZZ"

    before = home.state.read_bytes()
    assert b.init(path) == 0
    assert home.state.read_bytes() == before


def test_an_env_that_is_not_the_benchs_is_refused_before_anything(rig, tmp_path):
    b, instance, api, home = rig
    instance.foreign = True

    with pytest.raises(bench.BenchError, match="not the bench's"):
        b.init(_two_sets(tmp_path), rebuild=True)
    assert instance.calls == []

    with pytest.raises(bench.BenchError, match="not the bench's"):
        b.init(_two_sets(tmp_path))
    assert instance.calls == []


def test_a_peak_count_the_api_refused_is_not_read_as_no_peaks(rig, tmp_path):
    b, instance, api, home = rig
    api.peaks = {"S2": bench.ApiError("peaks answered 401")}

    assert b.init(_two_sets(tmp_path)) == 1

    state = json.loads(home.state.read_text())
    record = state["sets"]["Xb"]["samples"]["bundle:9.9.9/I_neg_Br_b.raw"]
    assert record["problems"] == ["sample S2: peaks answered 401"]
    assert record["items"][0]["peaks"] is None


class _Response:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self.body = body

    def json(self):
        return self.body


def test_the_http_peak_count_names_an_error_answer(tmp_path):
    api = bench.HttpApi("http://localhost:1", bench.Home(tmp_path))
    answers = iter([_Response(200, {"data": {"mz": [1.0, 2.0]}}), _Response(500)])
    api.session.get = lambda *a, **k: next(answers)

    assert api.peak_count("S1") == 2
    with pytest.raises(bench.ApiError, match="peaks answered 500"):
        api.peak_count("S2")


def test_each_file_keeps_the_build_that_ingested_it(rig, tmp_path):
    b, instance, api, home = rig
    del instance.mode_of_token["Ur"]
    first = instance.sha
    assert b.init(_two_sets(tmp_path, br=("I_neg_Br_a.raw",))) == 1
    # A state from before builds were kept per file.
    state = json.loads(home.state.read_text())
    del state["sets"]["Xb"]["samples"]["bundle:9.9.9/I_neg_Br_a.raw"]["build"]
    home.state.write_text(json.dumps(state))

    instance.mode_of_token["Ur"] = "Uronium"
    instance.rows[_sha("I_pos_Ur_a.raw")]["items"][0]["mode"] = "Uronium"
    instance.sha = "beef" * 10
    assert b.init(_two_sets(tmp_path)) == 0

    state = json.loads(home.state.read_text())
    assert state["build"]["sha"] == instance.sha
    xb = state["sets"]["Xb"]["samples"]
    assert xb["bundle:9.9.9/I_neg_Br_a.raw"]["build"] == first
    assert xb["bundle:9.9.9/I_neg_Br_b.raw"]["build"] == instance.sha
    # A recheck looked again at what the first build ingested.
    xu = state["sets"]["Xu"]["samples"]["bundle:9.9.9/I_pos_Ur_a.raw"]
    assert xu["problems"] == [] and xu["build"] == first

    out = io.StringIO()
    b.status(out)
    assert "built at beefbeefb, c0ffeec0f" in out.getvalue()
    assert "started   develop at beefbeefb" in out.getvalue()


def test_a_mechanism_in_the_old_spelling_maps_to_the_servers(tmp_path):
    api = FakeApi(FakeInstance(tmp_path))
    api.mechanisms.append(
        {
            "ionization_mechanism_id": "srv-br",
            "ionization_mechanism": "[M+Br]-",
            "ionization_mechanism_polarity": "-",
        }
    )
    seed = copy.deepcopy(SEED)
    seed["ionization_mechanisms"][0]["ionization_mechanism"] = "+Br-"
    seeded = {"modes": {}, "collections": {}, "reference_lists": []}

    chosen = bench.select_seed(seed, ["Bromide"], [])
    assert bench.load_seed(api, chosen, "s", seeded) == []

    assert not [c for c in api.calls if c[0] == "/api/ionization_mechanisms"]
    assert api.modes[0]["ionization_mechanism_ids"] == ["srv-br", "srv-h"]


# ---------------------------------------------------------- the dev instance


def _dev(tmp_path, monkeypatch, database=False, env="bench"):
    runtime = tmp_path / "runtime"
    (runtime / ".runtime").mkdir(parents=True, exist_ok=True)
    home = bench.Home(tmp_path / "home")
    home.make()
    dev = bench.DevInstance(home, env=env, runtime=runtime)
    monkeypatch.setattr(dev, "_database_exists", lambda: database)
    return dev, runtime, home


def _bind(runtime, worktree, env):
    from mascope_cli.instance import resolve_or_allocate

    resolve_or_allocate(
        worktree=str(worktree), home=runtime, port_in_use=lambda p: False, env=env
    )


def test_a_fresh_env_or_one_bound_to_the_bench_is_the_benchs(tmp_path, monkeypatch):
    dev, runtime, home = _dev(tmp_path, monkeypatch)
    dev.check_env()

    _bind(runtime, home.worktree.resolve(), "bench")
    (runtime / ".runtime" / "env" / "bench").mkdir(parents=True)
    dev.check_env()


@pytest.mark.parametrize("held_by", ["registry", "folder", "database"])
def test_an_env_someone_else_holds_is_refused(tmp_path, monkeypatch, held_by):
    dev, runtime, home = _dev(tmp_path, monkeypatch, database=held_by == "database")
    if held_by == "registry":
        _bind(runtime, tmp_path / "another-worktree", "bench")
    if held_by == "folder":
        (runtime / ".runtime" / "env" / "bench").mkdir(parents=True)

    with pytest.raises(bench.BenchError, match="not the bench's"):
        dev.check_env()


def test_an_env_name_that_is_no_name_is_refused(tmp_path):
    with pytest.raises(bench.BenchError, match="not an env name"):
        bench.DevInstance(bench.Home(tmp_path), env='x"; drop database y; --')
    assert bench.main(["--home", str(tmp_path), "--env", "a b", "status"]) == 2


def test_status_needs_no_runtime_home(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MASCOPE_PATH", raising=False)
    monkeypatch.delenv("MASCOPE_BENCH_HOME", raising=False)

    assert bench.main(["--home", str(tmp_path / "home"), "status"]) == 1
    assert "no bench in" in capsys.readouterr().out
    with pytest.raises(bench.BenchError, match="MASCOPE_PATH is not set"):
        bench.DevInstance(bench.Home(tmp_path)).runtime


def test_a_command_line_that_runs_the_backend_is_known():
    assert bench.runs_dev_run(["uv", "run", "--no-sync", "mascope", "dev", "run"])
    assert bench.runs_dev_run(
        ["/w/.venv/bin/python", "/w/.venv/bin/mascope", "dev", "run"]
    )
    assert not bench.runs_dev_run(["/w/.venv/bin/pytest", "dev", "run"])
    assert not bench.runs_dev_run(["mascope", "dev", "db"])


def _spawn(cwd, script):
    """A process group leader whose command line is ``<script> dev run``."""
    import subprocess

    script.write_text("#!/bin/sh\nsleep 60\n")
    script.chmod(0o755)
    return subprocess.Popen(
        [str(script), "dev", "run"], cwd=cwd, start_new_session=True
    )


@pytest.mark.parametrize(
    "name, in_worktree, killed",
    [
        ("mascope", True, True),
        ("mascope", False, False),
        ("sleeper", True, False),
    ],
)
def test_stop_signals_only_the_benchs_backend(
    tmp_path, monkeypatch, name, in_worktree, killed
):
    import subprocess

    dev, runtime, home = _dev(tmp_path, monkeypatch)
    home.worktree.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    proc = _spawn(home.worktree if in_worktree else elsewhere, tmp_path / name)
    monkeypatch.setattr(bench.time, "sleep", lambda s: None)
    try:
        home.pid.write_text(str(proc.pid))
        dev.stop()

        assert not home.pid.exists()
        if killed:
            assert proc.wait(timeout=10) < 0
        else:
            with pytest.raises(subprocess.TimeoutExpired):
                proc.wait(timeout=0.5)
    finally:
        proc.kill()
        proc.wait()


def test_stop_leaves_a_process_that_leads_no_group_alone(tmp_path, monkeypatch):
    import os
    import subprocess
    import time

    dev, runtime, home = _dev(tmp_path, monkeypatch)
    home.worktree.mkdir()
    script = tmp_path / "mascope"
    script.write_text("#!/bin/sh\nsleep 60\n")
    script.chmod(0o755)
    pidfile = tmp_path / "child.pid"
    # The script runs as a child of a shell that leads its own group, so its
    # pid is not its group's.
    leader = subprocess.Popen(
        ["sh", "-c", f'"{script}" dev run & echo $! > "{pidfile}"; wait'],
        cwd=home.worktree,
        start_new_session=True,
    )
    try:
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            time.sleep(0.05)
        child = int(pidfile.read_text())
        monkeypatch.setattr(bench.time, "sleep", lambda s: None)
        home.pid.write_text(str(child))

        dev.stop()

        assert not home.pid.exists()
        with pytest.raises(subprocess.TimeoutExpired):
            leader.wait(timeout=0.5)
        os.kill(child, 0)
    finally:
        os.killpg(leader.pid, 9)
        leader.wait()


def test_the_http_upload_says_whether_to_try_again(tmp_path, monkeypatch):
    import mascope_sdk
    from mascope_sdk.exceptions import AuthenticationError, ServerError

    monkeypatch.setattr(mascope_sdk, "SERVICE_NAME", mascope_sdk.SERVICE_NAME)
    api = bench.HttpApi("http://localhost:1", bench.Home(tmp_path))
    api._agent_token = "t"
    raw = tmp_path / "a.raw"
    raw.write_text("a")
    file = mf.ResolvedFile(ref=None, name="a.raw", path=raw, sha256=_sha("a"))
    answers = iter(
        [
            ServerError("no converter", status_code=503),
            AuthenticationError("token", status_code=401),
            None,
        ]
    )

    def post(**kwargs):
        error = next(answers)
        if error is not None:
            raise error

    monkeypatch.setattr(mascope_sdk, "api_post_file_tus", post)

    with pytest.raises(bench.UploadError) as refused:
        api.upload(file)
    assert refused.value.retry
    with pytest.raises(bench.UploadError) as refused:
        api.upload(file)
    assert not refused.value.retry and "[HTTP 401]" in str(refused.value)
    assert api.upload(file) is None


def test_a_file_refused_then_taken_then_lost_is_sent_once_more(rig, tmp_path):
    b, instance, api, home = rig
    api.refusals = 1
    instance.drop_next = {"I_neg_Br_a.raw"}

    assert b.init(_two_sets(tmp_path)) == 0

    assert api.uploads.count("I_neg_Br_a.raw") == 2
