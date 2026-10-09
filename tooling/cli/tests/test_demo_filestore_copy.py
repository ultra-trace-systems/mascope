"""
The demo bundle carries data between deployments, never a deployment id.

A filestore holds the file (``deployment.json``) in which its backend keeps
the id it generated for the deployment. Packaged into a bundle, it would name
the bundle's builder in every stack seeded from it - CI's e2e stack, every
``mascope demo``, every testbed - and a re-seed would replace the id a stack
already had. So the snapshot export leaves it out, and a seed keeps the env's
own.
"""

import json

from mascope_cli.cmd.demo import _seed, build_bundle


SAMPLE = ("instrument-x", "2026.01.01", "instrument-x_sample")


def _filestore(root, deployment_id=None):
    """A filestore of one sample file, with a deployment file if given an id."""
    sample = root.joinpath(*SAMPLE)
    sample.mkdir(parents=True)
    (sample / "data.raw").write_bytes(b"raw")
    if deployment_id is not None:
        (root / "deployment.json").write_text(
            json.dumps({"deployment_id": deployment_id}), encoding="utf-8"
        )
    return root


def _kept_id(filestore):
    with open(filestore / "deployment.json", encoding="utf-8") as f:
        return json.load(f)["deployment_id"]


def test_the_snapshot_export_leaves_the_builders_id_behind(tmp_path, monkeypatch):
    env = tmp_path / "env"
    _filestore(env / "filestore", deployment_id="builder")
    out = tmp_path / "bundle"
    dump = out / "snapshot" / "mascope_demo.dump"
    dump.parent.mkdir(parents=True)
    dump.write_bytes(b"dump")
    monkeypatch.setattr(build_bundle, "_dump_demo_db", lambda out_dir, name: dump)
    monkeypatch.setattr(build_bundle, "env_dir", lambda env_name="demo": env)

    block = build_bundle.export_snapshot(out)

    snapshot = out / block["filestore"]
    assert snapshot.joinpath(*SAMPLE, "data.raw").read_bytes() == b"raw"
    assert not (snapshot / "deployment.json").exists()


def _seed_from(tmp_path, monkeypatch, bundle_id):
    """Point the seed at a bundle (carrying ``bundle_id``, if any) and an env."""
    bundle = tmp_path / "bundle"
    _filestore(bundle / "snapshot" / "filestore", deployment_id=bundle_id)
    env = tmp_path / "env"
    monkeypatch.setattr(_seed, "env_dir", lambda env_name="demo": env)
    monkeypatch.setattr(
        _seed,
        "_resolve",
        lambda version, source_dir: (
            bundle,
            {"snapshot": {"filestore": "snapshot/filestore"}},
        ),
    )
    return env / "filestore"


def test_a_reseed_keeps_the_envs_own_id(tmp_path, monkeypatch):
    """The rest of the env's filestore is replaced; its identity is not."""
    filestore = _seed_from(tmp_path, monkeypatch, bundle_id="builder")
    filestore.mkdir(parents=True)
    (filestore / "deployment.json").write_text(
        json.dumps({"deployment_id": "mine"}), encoding="utf-8"
    )
    (filestore / "left-over").write_text("from before", encoding="utf-8")

    _seed.restore_filestore()

    assert _kept_id(filestore) == "mine"
    assert not (filestore / "left-over").exists()
    assert filestore.joinpath(*SAMPLE, "data.raw").read_bytes() == b"raw"


def test_a_first_seed_takes_no_id_from_the_bundle(tmp_path, monkeypatch):
    """A bundle that carries an id anyway - one built before the export left
    it out - does not hand it on: the stack generates its own on start."""
    filestore = _seed_from(tmp_path, monkeypatch, bundle_id="builder")

    _seed.restore_filestore()

    assert filestore.joinpath(*SAMPLE, "data.raw").read_bytes() == b"raw"
    assert not (filestore / "deployment.json").exists()
