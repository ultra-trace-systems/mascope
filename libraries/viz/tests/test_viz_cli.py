"""
The command line, ``mascope-viz``, on a short synthetic campaign behind
:class:`fixtures.server.FakeClient`, which stands in for ``MascopeClient``:
what each command prints, as text and as JSON, that the options reach the
library, and that no frame is ever printed.
"""

import dataclasses
import json
from importlib.metadata import entry_points
from types import SimpleNamespace

import pandas as pd
import pytest
from fixtures.campaign import HALIDE_NEGATIVE, Campaign, generate
from fixtures.server import SCOPE, FakeClient
from typer.testing import CliRunner

from mascope_sdk.exceptions import (
    AuthenticationError,
    ConfigurationError,
    MascopeAPIError,
)
from mascope_viz import cli
from mascope_viz.data import resolve, views
from mascope_viz.data import store as store_module
from mascope_viz.data.store import CampaignStore, server_name, store_path


#: Four days, the third unfiled, the run changing on the second.
SHORT = dataclasses.replace(
    HALIDE_NEGATIVE,
    days=4,
    gap_day=2,
    run_change_day=1,
    n_species=8,
    n_targets=3,
    episodes=(),
)

#: The store's clock: a month after the campaign.
NOW = pd.Timestamp("2026-08-01T00:00:00Z")

#: The fake server's one dataset.
DATASET = ["--dataset", "Campaign"]


@pytest.fixture(scope="module")
def short_campaign() -> Campaign:
    return generate(SHORT, seed=3)


@pytest.fixture
def server(short_campaign, monkeypatch, tmp_path):
    """A fresh server over the short campaign, which the command line
    connects to, with its stores under *tmp_path*.

    ``made`` records each client's arguments, ``slept`` each pause; a
    client made with a URL takes it, as ``MascopeClient`` does.
    """
    client = FakeClient(short_campaign)
    client.made = []
    client.slept = []

    def made(**kwargs):
        client.made.append(kwargs)
        if kwargs.get("url"):
            client.url = kwargs["url"]
        return client

    monkeypatch.setattr(cli, "MascopeClient", made)
    monkeypatch.setattr(store_module, "_now", lambda: NOW)
    monkeypatch.setattr(store_module.time, "sleep", client.slept.append)
    monkeypatch.setenv(store_module.ROOT_ENV, str(tmp_path / "stores"))
    monkeypatch.delenv("MASCOPE_WORKSPACE", raising=False)
    # Unset, and set back as it was after the test: the command line sets it
    monkeypatch.setenv(cli.LOG_LEVEL, "INFO")
    monkeypatch.delenv(cli.LOG_LEVEL)
    # No .env of the developer's: one is looked for from here up
    monkeypatch.chdir(tmp_path)
    return client


def run(*args: str):
    return CliRunner().invoke(cli.app, list(args))


def payload(*args: str, code: int = 0) -> dict:
    """The JSON a command prints, having checked its exit status."""
    result = run(*args, "--json")
    assert result.exit_code == code, (result.stdout, result.stderr)
    assert result.stdout.isascii()
    return json.loads(result.stdout)


def text(*args: str, code: int = 0) -> str:
    result = run(*args)
    assert result.exit_code == code, (result.stdout, result.stderr)
    assert result.stdout.isascii() and result.stderr.isascii()
    return result.stdout


def _store(client: FakeClient) -> CampaignStore:
    return CampaignStore(store_path(client, resolve(client, SCOPE)))


def _ion(campaign: Campaign) -> str:
    keys = campaign.tables["species"]["series_key"].astype(str)
    return sorted(keys[keys.str.startswith("ion:")])[0]


# --- the command -------------------------------------------------------------


def test_the_console_script_runs_the_app():
    (script,) = entry_points(group="console_scripts", name="mascope-viz")
    assert script.value == "mascope_viz.cli:app"
    assert script.load() is cli.app


@pytest.mark.parametrize(
    "command",
    [[], ["scopes"], ["store"], ["store", "sync"], ["store", "status"],
     ["store", "rm"], ["view"]],
)  # fmt: skip
def test_the_help_is_ascii(command):
    result = run(*command, "--help")
    assert result.exit_code == 0
    assert result.stdout.isascii()
    # Rich's boxes are not drawn
    assert "Usage: mascope-viz" in result.stdout


# --- scopes ------------------------------------------------------------------


def test_scopes_lists_the_batches_files_and_span(server, short_campaign):
    found = payload("scopes", *DATASET)

    samples = short_campaign.tables["samples"]
    files = short_campaign.tables["files"]
    assert found["ambiguous"] is False
    assert found["key"] == resolve(server, SCOPE).key
    assert [b["sample_batch_id"] for b in found["batches"]] == server.batch_ids
    assert found["n_batches"] == len(server.batch_ids)
    assert [b["n_samples"] for b in found["batches"]] == [
        len(server.by_batch[b]) for b in server.batch_ids
    ]
    assert found["n_samples"] == len(samples)
    assert found["n_files"] == len(files)
    assert found["n_unfiled"] == int((~files["in_batch"]).sum()) > 0
    assert pd.Timestamp(found["first_sample_utc"]) == samples["datetime_utc"].min()
    assert pd.Timestamp(found["last_sample_utc"]) == samples["datetime_utc"].max()
    assert pd.Timestamp(found["first_file_utc"]) == files["datetime_utc"].min()
    assert pd.Timestamp(found["last_file_utc"]) == files["datetime_utc"].max()
    assert found["display_tz"] == SHORT.timezone
    assert found["instruments"] == [SHORT.instrument]
    assert [c["target_collection_id"] for c in found["collections"]] == ["c1"]
    assert found["collections"][0]["n_batches"] == len(server.batch_ids)
    assert found["store"] == {"path": str(_store(server).path), "synced": False}

    shown = text("scopes", *DATASET)
    assert f"{len(server.batch_ids)} batch(es), {len(samples):,} sample(s)" in shown
    for batch_id in server.batch_ids:
        assert f'{batch_id}  "Campaign {batch_id}"' in shown
    first = samples["datetime_utc"].min().tz_convert(SHORT.timezone).isoformat()
    assert f"samples {first} .. " in shown
    assert "(not synced)" in shown
    assert '"Targets"' in shown


def test_scopes_says_when_the_store_is_synced(server):
    payload("store", "sync", *DATASET)
    assert payload("scopes", *DATASET)["store"]["synced"] is True
    assert "(synced)" in text("scopes", *DATASET)


def test_scopes_lists_the_candidates_of_an_ambiguous_name(server):
    server.dataset_rows.append(
        {"dataset_id": "d2", "dataset_name": "Campaign two", "instrument": "x"}
    )
    found = payload("scopes", "--dataset", "Camp")
    assert found["ambiguous"] is True
    assert [c["id"] for c in found["candidates"]["dataset"]] == ["d1", "d2"]

    shown = text("scopes", "--dataset", "Camp")
    assert "dataset matches 2" in shown
    assert 'd1  "Campaign"' in shown
    assert 'd2  "Campaign two"' in shown


@pytest.mark.parametrize(
    "command",
    [["store", "sync"], ["store", "status"], ["store", "rm"], ["view", "coverage"]],
)
def test_an_ambiguous_scope_does_nothing_and_says_so(server, command, tmp_path):
    server.dataset_rows.append(
        {"dataset_id": "d2", "dataset_name": "Campaign two", "instrument": "x"}
    )
    failed = payload(*command, "--dataset", "Camp", code=1)
    assert failed["error"] == "ambiguous"
    assert [c["id"] for c in failed["candidates"]["dataset"]] == ["d1", "d2"]
    assert not (tmp_path / "stores").exists()

    result = run(*command, "--dataset", "Camp")
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "error: The scope is ambiguous (dataset matches 2)" in result.stderr


def test_status_says_two_polarities_each_read_by_one_run_mix_nothing(server):
    # The run changes after the first batch, which is of the other polarity
    server.batch_polarity[server.batch_ids[0]] = "+"
    payload("store", "sync", *DATASET)

    status = payload("store", "status", *DATASET)
    assert status["mixed_runs"] is False
    assert status["run_changes"] == []
    shown = text("store", "status", *DATASET)
    assert "  formula readings (2, one reading per polarity):" in shown


def test_scopes_lists_up_to_the_limit(server):
    found = payload("scopes", *DATASET, "--limit", "1")
    assert [b["sample_batch_id"] for b in found["batches"]] == server.batch_ids[:1]
    assert found["n_batches"] == len(server.batch_ids)
    shown = text("scopes", *DATASET, "--limit", "1")
    assert f"and {len(server.batch_ids) - 1} more batch(es)" in shown
    assert server.batch_ids[1] not in shown
    assert len(payload("scopes", *DATASET, "--limit", "0")["batches"]) == len(
        server.batch_ids
    )


def test_a_missing_polarity_or_type_prints_as_missing(server, monkeypatch):
    # Beside the other batches' polarities, a missing one is NaN, not None
    server.batch_polarity["batch-01"] = None
    two = [
        {"target_collection_id": "c1", "target_collection_name": "Targets",
         "target_collection_type": "TARGETS", "workspace_id": "ws1"},
        {"target_collection_id": "c2", "target_collection_name": "Untyped",
         "target_collection_type": None, "workspace_id": "ws1"},
    ]  # fmt: skip
    monkeypatch.setattr(
        FakeClient,
        "collections",
        property(lambda self: SimpleNamespace(list=lambda **_: pd.DataFrame(two))),
    )
    found = payload("scopes", *DATASET)
    assert found["batches"][0]["polarity"] is None
    assert found["collections"][1]["target_collection_type"] is None

    shown = text("scopes", *DATASET)
    assert "nan" not in shown
    lines = shown.splitlines()
    (batch,) = [line for line in lines if line.startswith("  batch-01 ")]
    assert batch.split()[3] == "-"
    (untyped,) = [line for line in lines if line.startswith("  c2 ")]
    assert untyped.split()[2] == "-"


def test_the_scope_options_reach_the_scope(server):
    found = payload(
        "scopes",
        *DATASET,
        "--batches",
        "batch-01",
        "--batch",
        "batch-04",
        "--instrument",
        "instrument",
        "--from",
        "2026-06-01T12:00:00",
        "--to",
        "2026-06-04T12:00:00",
        "--polarity",
        "negative",
        "--mode",
        "Campaign mode",
        "--collection",
        "Targets",
        "--tz",
        "UTC",
    )
    assert found["scope"] == {
        "dataset": "d1",
        "batches": ["batch-01", "batch-04"],
        "instrument": SHORT.instrument,
        "start": "2026-06-01T12:00:00+00:00",
        "end": "2026-06-04T12:00:00+00:00",
        "polarity": "-",
        "mode": "im-1",
        "collection": "c1",
    }
    assert found["display_tz"] == "UTC"
    assert [b["sample_batch_id"] for b in found["batches"]] == ["batch-01", "batch-04"]
    assert found["batches"][0]["sample_batch_utc_modified"] == (
        "2026-07-02T00:00:00+00:00"
    )
    server.modified["batch-01"] = pd.NaT
    found = payload("scopes", *DATASET)
    assert found["batches"][0]["sample_batch_utc_modified"] is None
    assert "  collection c1" in text("scopes", *DATASET, "--collection", "Targets")
    # A bound without a zone is in the instrument's when --tz is left out
    found = payload("scopes", *DATASET, "--from", "2026-06-01T12:00:00")
    assert found["start_utc"] == "2026-06-01T09:00:00+00:00"


def test_a_failure_is_its_error_as_json_and_a_message_in_text(server):
    failed = payload("scopes", "--dataset", "Nope", code=1)
    assert failed["error"] == "not_found"
    assert failed["available"] == ["Campaign"]

    result = run("scopes", "--dataset", "Nope")
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error: No dataset matching 'Nope'")


@pytest.mark.parametrize(
    "options",
    [
        ["--polarity", "sideways"],
        ["--from", "2026-06-03", "--to", "2026-06-01"],
        ["--tz", "Mars/Olympus"],
    ],
)
def test_an_invalid_scope_is_refused(server, options):
    failed = payload("scopes", *DATASET, *options, code=1)
    assert failed["error"] == "invalid_scope"


def test_a_missing_configuration_says_what_to_set(server, tmp_path, monkeypatch):
    sdk = (
        "Multiple workspaces available: ['a', 'b']. "
        "Please specify one with MascopeClient(workspace=...)."
    )

    def unconfigured(**kwargs):
        raise ConfigurationError(sdk)

    monkeypatch.setattr(cli, "MascopeClient", unconfigured)
    monkeypatch.delenv("MASCOPE_URL", raising=False)
    monkeypatch.delenv("MASCOPE_ACCESS_TOKEN", raising=False)
    where = "in the environment or a .env file"

    no_url = payload("scopes", *DATASET, code=1)
    assert no_url["error"] == "not_configured"
    assert no_url["message"] == (
        f"Mascope URL not configured: set MASCOPE_URL {where}, or pass --server."
    )

    # The URL given, the token is what is missing, and no option takes it
    no_token = payload("scopes", *DATASET, "--server", "http://x.test", code=1)
    assert no_token["error"] == "not_configured"
    assert no_token["message"] == (
        f"Mascope access token not configured: set MASCOPE_ACCESS_TOKEN {where}. "
        "No option takes it: --server gives the URL alone."
    )
    (tmp_path / ".env").write_text("MASCOPE_URL=http://x.test\n")
    assert payload("scopes", *DATASET, code=1) == no_token

    # Both set, the workspace is left
    monkeypatch.setenv("MASCOPE_ACCESS_TOKEN", "token")
    workspace = payload("scopes", *DATASET, code=1)
    assert workspace["error"] == "not_configured"
    assert workspace["message"] == (
        f"{sdk} With mascope-viz, name it with --workspace, or MASCOPE_WORKSPACE "
        f"{where}."
    )
    result = run("scopes", *DATASET)
    assert result.exit_code == 1 and result.stdout == ""
    assert result.stderr == f"error: {workspace['message']}\n"


# --- the server and the workspace --------------------------------------------


def test_the_credentials_are_the_sdks(server, tmp_path, monkeypatch):
    payload("scopes", *DATASET)
    assert server.made[-1] == {"url": None, "workspace": None}

    (tmp_path / ".env").write_text("MASCOPE_WORKSPACE=from the file\n")
    payload("scopes", *DATASET)
    assert server.made[-1]["workspace"] == "from the file"

    monkeypatch.setenv("MASCOPE_WORKSPACE", "from the environment")
    payload("scopes", *DATASET)
    assert server.made[-1]["workspace"] == "from the environment"

    payload("scopes", *DATASET, "--workspace", "named")
    assert server.made[-1]["workspace"] == "named"


def test_the_sdk_logs_warnings_alone_unless_told_otherwise(
    server, tmp_path, monkeypatch
):
    payload("scopes", *DATASET)
    assert cli.os.environ[cli.LOG_LEVEL] == cli.QUIET

    monkeypatch.setenv(cli.LOG_LEVEL, "DEBUG")
    payload("scopes", *DATASET)
    assert cli.os.environ[cli.LOG_LEVEL] == "DEBUG"

    monkeypatch.delenv(cli.LOG_LEVEL)
    (tmp_path / ".env").write_text(f"{cli.LOG_LEVEL}=INFO\n")
    payload("scopes", *DATASET)
    # The client reads it from the file
    assert cli.LOG_LEVEL not in cli.os.environ


def test_the_server_selects_the_store_directory(server, tmp_path):
    url = "http://other.test:9000"
    synced = payload("store", "sync", *DATASET, "--server", url)
    assert server.made[-1]["url"] == url
    assert synced["store"] == str(
        tmp_path / "stores" / server_name(url) / server.workspace_id / synced["key"]
    )
    assert payload("store", "status", *DATASET, "--server", url)["exists"] is True

    server.url = FakeClient.url
    assert payload("store", "status", *DATASET)["exists"] is False


# --- store -------------------------------------------------------------------


def test_sync_then_status_reports_every_batch_cached(server):
    before = payload("store", "status", *DATASET)
    assert before["exists"] is False
    assert before["key"] == resolve(server, SCOPE).key
    assert "not synced" in text("store", "status", *DATASET)

    synced = payload("store", "sync", *DATASET)
    assert synced["fetched"] == {b: "new" for b in server.batch_ids}
    assert synced["n_fetched"] == len(server.batch_ids)
    assert synced["unchanged"] == [] and synced["failed"] == {}
    assert synced["complete"] is True
    assert synced["store"] == str(_store(server).path)

    status = payload("store", "status", *DATASET)
    assert status["exists"] is True
    assert status["complete"] is True
    assert status["incomplete"] == []
    assert status["n_batches"] == len(server.batch_ids)
    assert [b["sample_batch_id"] for b in status["batches"]] == server.batch_ids
    assert {b["state"] for b in status["batches"]} == {"synced"}
    assert status["rows"] == _store(server).status().rows
    assert status["mixed_runs"] is True
    assert len(status["run_changes"]) == 1

    shown = text("store", "status", *DATASET)
    assert "; complete" in shown
    assert "  formula readings (2, mixed: a formula's series joins readings" in shown
    assert (
        f"{len(server.batch_ids)} batch(es) ({len(server.batch_ids)} synced)" in shown
    )
    assert '"mascope 0.5.0" -> "mascope 0.6.0"' in shown
    for batch_id in server.batch_ids:
        assert f'{batch_id}  "Campaign {batch_id}"  synced' in shown

    again = payload("store", "sync", *DATASET)
    assert again["fetched"] == {}
    assert again["unchanged"] == server.batch_ids
    assert (again["n_unchanged"], again["n_removed"]) == (3, 0)
    assert "0 batch(es) read, 3 unchanged, 0 removed" in text("store", "sync", *DATASET)

    server.deleted.add("batch-04")
    gone = payload("store", "sync", *DATASET)
    assert (gone["removed"], gone["n_removed"]) == (["batch-04"], 1)


def test_sync_pauses_between_batches(server):
    payload("store", "sync", *DATASET, "--pause", "0.25")
    assert server.slept == [0.25] * (len(server.batch_ids) - 1)
    payload("store", "rm", *DATASET)
    server.slept.clear()
    payload("store", "sync", *DATASET)
    assert server.slept == [0.5] * (len(server.batch_ids) - 1)


def test_a_sync_that_leaves_a_batch_behind_exits_1(server):
    server.fail[("ion_series", "batch-02")] = MascopeAPIError("Bad Gateway")
    synced = payload("store", "sync", *DATASET, code=1)
    assert list(synced["failed"]) == ["batch-02"]
    assert "Bad Gateway" in synced["failed"]["batch-02"]
    assert synced["complete"] is False
    assert synced["n_failed"] == 1

    shown = text("store", "sync", *DATASET, code=1)
    assert "1 batch(es) failed" in shown
    assert 'batch-02 "Campaign batch-02": "Could not read the ion series' in shown

    status = payload("store", "status", *DATASET)
    assert status["complete"] is False
    assert status["incomplete"] == ["1 batch(es) not brought up to date"]
    assert "NOT complete: 1 batch(es) not brought up to date" in text(
        "store", "status", *DATASET
    )


def test_status_lists_up_to_the_limit(server):
    payload("store", "sync", *DATASET)
    status = payload("store", "status", *DATASET, "--limit", "1")
    assert [b["sample_batch_id"] for b in status["batches"]] == server.batch_ids[:1]
    assert status["n_batches"] == len(server.batch_ids)
    shown = text("store", "status", *DATASET, "--limit", "1")
    assert f"and {len(server.batch_ids) - 1} more batch(es)" in shown


def test_rm_deletes_the_store(server):
    payload("store", "sync", *DATASET)
    path = _store(server).path
    size = _store(server).status().size_bytes

    removed = payload("store", "rm", *DATASET)
    assert removed == {
        "key": resolve(server, SCOPE).key,
        "path": str(path),
        "removed": True,
        "size_bytes": size,
    }
    assert not path.exists()
    assert payload("store", "status", *DATASET)["exists"] is False

    assert "3 batch(es) read (3 new), 0 unchanged" in text("store", "sync", *DATASET)
    shown = text("store", "rm", *DATASET)
    assert shown == f"removed store {removed['key']} at {path} ({cli._size(size)})\n"

    again = payload("store", "rm", *DATASET)
    assert again["removed"] is False and again["size_bytes"] == 0
    assert "nothing to remove" in text("store", "rm", *DATASET)


@pytest.mark.parametrize("n_left", [1, 2])
def test_rm_fails_when_the_delete_leaves_files(server, monkeypatch, n_left):
    payload("store", "sync", *DATASET)
    path = _store(server).path
    kept = sorted(found for found in path.rglob("*") if found.is_file())[:n_left]
    size = sum(found.stat().st_size for found in kept)

    def held_open(self):
        # As files another program holds open stop the delete: silently
        for found in self.path.rglob("*"):
            if found.is_file() and found not in kept:
                found.unlink()

    monkeypatch.setattr(CampaignStore, "remove", held_open)
    failed = payload("store", "rm", *DATASET, code=1)
    assert failed["error"] == "not_removed"
    assert failed["path"] == str(path)
    assert (failed["n_files"], failed["size_bytes"]) == (n_left, size)
    assert "run `mascope-viz store rm` again" in failed["message"]

    result = run("store", "rm", *DATASET)
    assert result.exit_code == 1 and result.stdout == ""
    assert result.stderr.startswith(
        f"error: The store at {path} was not removed: {n_left} file(s), "
        f"{cli._size(size)}, are still there."
    )


# --- view --------------------------------------------------------------------


@pytest.fixture
def synced(server):
    payload("store", "sync", *DATASET)
    return server, _store(server)


def test_view_series_prints_a_summary_and_never_the_frame(
    synced, short_campaign, tmp_path
):
    client, store = synced
    ion = _ion(short_campaign)
    found = payload("view", "series", *DATASET, "--keys", ion, "-k", "tic")

    frame = views.series(store, [ion, "tic"])
    assert found["view"] == "series"
    assert found["rows"] == len(frame) > 50
    assert found["columns"] == list(frame.columns)
    assert pd.Timestamp(found["span"]["first"]) == frame["t"].min()
    assert pd.Timestamp(found["span"]["last"]) == frame["t"].max()
    assert found["attrs"]["cadence"] == frame.attrs["cadence"]
    assert found["attrs"]["auto"] is True
    assert found["n_keys"] == 2
    for entry, key in zip(found["entries"], [ion, "tic"], strict=True):
        rows = frame[frame["key"] == key]
        at = rows["value"].idxmax()
        assert entry["key"] == key
        assert entry["rows"] == len(rows)
        assert entry["n"] == rows["value"].notna().sum()
        assert entry["median"] == pytest.approx(rows["value"].median())
        assert entry["max"] == pytest.approx(rows["value"].max())
        assert pd.Timestamp(entry["max_at"]) == rows.at[at, "t"]
    species = store.table("species").set_index("series_key")
    assert found["entries"][0]["name"] == species.loc[ion, "name"]
    assert found["entries"][0]["ion_formula"] == species.loc[ion, "ion_formula"]
    assert found["entries"][1]["name"] == "TIC"
    assert found["entries"][1]["ion_formula"] is None
    assert found["store"]["complete"] is True
    assert found["csv"] == []

    shown = text("view", "series", *DATASET, "--keys", ion, "-k", "tic")
    lines = shown.splitlines()
    # Two lines of what it is, the span, a header and a line per key
    assert len(lines) == 6
    assert lines[0] == (
        f"view series of store {found['key']}: {len(frame):,} row(s), 2 key(s)"
    )
    assert f'"{ion}"' in lines[4] and '"TIC"' in lines[5]
    assert rows.at[at, "t"].isoformat() in lines[5]
    # No bin's time but the span's ends and the maxima's
    printed = {found["span"]["first"], found["span"]["last"]}
    printed |= {entry["max_at"] for entry in found["entries"]}
    for t in frame["t"].unique():
        assert (t.isoformat() in shown) == (t.isoformat() in printed)


def test_view_csv_writes_the_frame(synced, short_campaign, tmp_path):
    _, store = synced
    ion = _ion(short_campaign)
    path = tmp_path / "out" / "series.csv"
    found = payload("view", "series", *DATASET, "-k", ion, "--csv", str(path))
    assert found["csv"] == [str(path)]
    written = pd.read_csv(path)
    frame = views.series(store, [ion])
    assert list(written.columns) == list(frame.columns)
    assert len(written) == len(frame)
    pd.testing.assert_series_equal(
        written["value"], frame["value"], check_names=False, check_dtype=False
    )
    assert f"csv {path}" in text(
        "view", "series", *DATASET, "-k", ion, "--csv", str(path)
    )


def test_view_coverage_writes_its_gaps_beside_its_bins(synced, tmp_path):
    _, store = synced
    path = tmp_path / "coverage.csv"
    found = payload("view", "coverage", *DATASET, "--csv", str(path))
    gaps = tmp_path / "coverage.gaps.csv"
    assert found["csv"] == [str(path), str(gaps)]
    result = views.coverage(store)
    assert len(pd.read_csv(path)) == len(result.bins)
    assert len(pd.read_csv(gaps)) == len(result.gaps) == found["n_gaps"] == 1


def test_view_coverage_summarises_files_samples_and_gaps(synced, short_campaign):
    _, store = synced
    found = payload("view", "coverage", *DATASET)
    result = views.coverage(store)
    files = short_campaign.tables["files"]
    assert found["n_files"] == result.n_files == len(files)
    assert found["n_unfiled"] == result.n_unfiled
    assert found["n_samples"] == result.n_samples
    gap = short_campaign.truth.gap
    assert len(found["gaps"]) == 1
    assert pd.Timestamp(found["gaps"][0]["start"]) == result.gaps["start"].iloc[0]
    assert found["gaps"][0]["duration"] == pytest.approx(
        result.gaps["duration"].iloc[0].total_seconds()
    )
    assert found["gaps"][0]["n_unfiled"] == gap.files
    # The unfiled files have no mode, and the campaign's polarity
    assert found["groups"] == [
        {"polarity": "-", "mode": "im-1", "mode_name": "Campaign mode",
         "n_files": len(files) - gap.files, "n_unfiled": 0,
         "n_samples": result.n_samples},
        {"polarity": "-", "mode": None, "mode_name": None, "n_files": gap.files,
         "n_unfiled": gap.files, "n_samples": 0},
    ]  # fmt: skip

    shown = text("view", "coverage", *DATASET)
    assert '-         "Campaign mode"' in shown
    assert "-         (none)" in shown
    assert "gaps (1, 1 d in all), longest first:" in shown


def test_view_coverage_shows_the_id_of_a_mode_the_server_did_not_name(server):
    server.fail[("ionization.modes", None)] = AuthenticationError("no")
    payload("store", "sync", *DATASET)

    found = payload("view", "coverage", *DATASET)

    assert [(g["mode"], g["mode_name"]) for g in found["groups"]] == [
        ("im-1", None),
        (None, None),
    ]
    assert '-         "im-1"' in text("view", "coverage", *DATASET)


def test_view_coverage_tells_two_modes_of_one_name_apart(server):
    # The same chemistry on a second instrument: another mode, one name
    first = server.batch_ids[0]
    server.by_batch[first] = server.by_batch[first].assign(ionization_mode_id="im-2")
    server.system_modes = {"im-2": "Campaign mode"}
    payload("store", "sync", *DATASET)

    found = payload("view", "coverage", *DATASET)

    assert [(g["mode"], g["mode_name"]) for g in found["groups"]] == [
        ("im-1", "Campaign mode"),
        ("im-2", "Campaign mode"),
        (None, None),
    ]
    shown = text("view", "coverage", *DATASET)
    assert '-         "Campaign mode" ("im-1")' in shown
    assert '-         "Campaign mode" ("im-2")' in shown


def test_view_coverage_names_a_mode_alone_when_no_other_shares_its_name(server):
    first = server.batch_ids[0]
    server.by_batch[first] = server.by_batch[first].assign(ionization_mode_id="im-2")
    server.system_modes = {"im-2": "Other mode"}
    payload("store", "sync", *DATASET)

    shown = text("view", "coverage", *DATASET)

    assert '-         "Campaign mode"  ' in shown
    assert '-         "Other mode"  ' in shown
    assert '("im-' not in shown


def test_view_coverage_names_a_mode_alone_under_two_polarities(server):
    # One mode in both polarities is two groups of one name, and one mode
    first = server.batch_ids[0]
    server.by_batch[first] = server.by_batch[first].assign(polarity="+")
    payload("store", "sync", *DATASET)

    found = payload("view", "coverage", *DATASET)

    groups = [(g["polarity"], g["mode"], g["mode_name"]) for g in found["groups"]]
    assert ("+", "im-1", "Campaign mode") in groups
    assert ("-", "im-1", "Campaign mode") in groups
    shown = text("view", "coverage", *DATASET)
    assert '+         "Campaign mode"  ' in shown
    assert '-         "Campaign mode"  ' in shown
    assert '("im-' not in shown


#: Each view with the options it needs, and its frame from the library.
CALLS = {
    "series": (["-k", "tic"], lambda s: views.series(s, ["tic"])),
    "diurnal": (["-k", "tic"], lambda s: views.diurnal(s, ["tic"])),
    "periods": (["-k", "tic"], lambda s: views.periods(s, ["tic"], "day")),
    "coverage": ([], lambda s: views.coverage(s).bins),
    "composition": ([], lambda s: views.composition(s, "all")),
    "spectrum": ([], lambda s: views.spectrum(s)),
    "top": ([], lambda s: views.top(s, 10, "median", period="week")),
}


@pytest.mark.parametrize("name", cli.VIEWS)
def test_every_view_prints_one_json_object(synced, name):
    _, store = synced
    options, call = CALLS[name]
    found = payload("view", name, *DATASET, *options, "--limit", "0")
    frame = call(store)
    assert found["view"] == name
    assert found["rows"] == len(frame)
    assert found["columns"] == list(frame.columns)
    assert found["attrs"]["view"] == name
    assert found["attrs"]["tz"] == SHORT.timezone
    assert "keys" not in found["attrs"]

    shown = text("view", name, *DATASET, *options)
    assert shown.startswith(f"view {name} of store {found['key']}: ")
    assert len(shown.splitlines()) < 25


def test_the_views_options_reach_the_views(synced, short_campaign):
    ion = _ion(short_campaign)

    def attrs(*args):
        return payload("view", *args, *DATASET)["attrs"]

    got = attrs(
        "series", "-k", ion, "--cadence", "1h", "--normalise", "tic", "--stat",
        "mean", "--tz", "UTC", "--window-from", "2026-06-01T06:00:00",
        "--window-to", "2026-06-02T06:00:00",
    )  # fmt: skip
    assert (got["cadence"], got["normalise"], got["stat"], got["tz"]) == (
        "1h",
        "tic",
        "mean",
        "UTC",
    )
    assert (got["start"], got["end"]) == (
        "2026-06-01T06:00:00+00:00",
        "2026-06-02T06:00:00+00:00",
    )
    assert attrs("series", "-k", ion, "--cadence", "raw")["cadence"] == "raw"
    assert attrs("diurnal", "-k", ion, "--by", "weekday")["by"] == "weekday"
    assert attrs("diurnal", "-k", ion, "--normalise", ion)["normalise"] == ion
    assert attrs("periods", "-k", ion)["period"] == "day"
    assert attrs("periods", "-k", ion, "--period", "week")["period"] == "week"
    got = attrs("coverage")
    assert (got["cadence"], got["min_gap"]) == ("1h", "0 days 01:00:00")
    got = attrs("coverage", "--cadence", "6h", "--min-gap", "2h")
    assert (got["cadence"], got["min_gap"]) == ("6h", "0 days 02:00:00")
    got = attrs("composition")
    assert (got["period"], got["by"]) == ("all", None)
    got = attrs("composition", "--period", "day", "--by", "class")
    assert (got["period"], got["by"]) == ("day", "class")
    got = attrs("top")
    assert (got["by"], got["period"]) == ("median", "week")
    got = attrs("top", "--by", "ratio", "--period", "day")
    assert (got["by"], got["period"]) == ("ratio", "day")
    assert len(payload("view", "top", *DATASET)["entries"]) == 10
    assert len(payload("view", "top", *DATASET, "-n", "3")["entries"]) == 3
    got = attrs(
        "spectrum", "--window-from", "2026-06-01", "--window-to", "2026-06-02",
        "--other-from", "2026-06-04", "--other-to", "2026-06-05",
    )  # fmt: skip
    zone = SHORT.timezone
    assert got["period"] == [
        pd.Timestamp("2026-06-01", tz=zone).tz_convert("UTC").isoformat(),
        pd.Timestamp("2026-06-02", tz=zone).tz_convert("UTC").isoformat(),
    ]
    assert got["other"] == [
        pd.Timestamp("2026-06-04", tz=zone).tz_convert("UTC").isoformat(),
        pd.Timestamp("2026-06-05", tz=zone).tz_convert("UTC").isoformat(),
    ]
    assert attrs("spectrum")["other"] is None
    # Of the keys asked for alone
    found = payload("view", "composition", *DATASET, "-k", _formula(short_campaign))
    assert [entry["key"] for entry in found["entries"]] == [_formula(short_campaign)]


def _formula(campaign: Campaign) -> str:
    keys = campaign.tables["species"]["series_key"].astype(str)
    return sorted(keys[keys.str.startswith("formula:")])[0]


def test_the_entries_are_capped_at_the_limit(synced):
    found = payload("view", "composition", *DATASET, "--limit", "2")
    assert len(found["entries"]) == 2
    assert found["n_keys"] > 2
    shares = [entry["share"] for entry in found["entries"]]
    assert shares == sorted(shares, reverse=True)
    # The biggest share first, whatever the keys' order
    _, store = synced
    frame = views.composition(store, "all")
    every = payload("view", "composition", *DATASET, "--limit", "0")["entries"]
    assert [entry["key"] for entry in every] == list(
        frame.sort_values("share", ascending=False, kind="stable")["key"]
    )
    assert [entry["key"] for entry in every] != sorted(frame["key"])
    shown = text("view", "composition", *DATASET, "--limit", "2")
    assert f"and {found['n_keys'] - 2} more key(s)" in shown


def test_a_view_reports_mixed_runs(synced):
    found = payload("view", "composition", *DATASET)
    assert found["attrs"]["mixed_runs"] is True
    assert len(found["attrs"]["run_changes"]) == 1
    shown = text("view", "composition", *DATASET)
    assert "mixed runs: the formulas are read by more than one engine run" in shown
    assert '"mascope 0.5.0" -> "mascope 0.6.0"' in shown


@pytest.mark.parametrize(
    "args, message",
    [
        (["coverage", "--keys", "tic"], "View coverage takes no --keys"),
        (["series", "-k", "tic", "--by", "month"], "View series takes no --by"),
        (["diurnal", "-k", "tic", "--stat", "mean"], "View diurnal takes no --stat"),
        (["top", "--window-to", "2026-06-02", "--other-from", "2026-06-01"],
         "View top takes no --other-from/--other-to"),
        (["series"], "View series needs --keys"),
        (["series", "-k", "ion:nope"], "The store holds no series 'ion:nope'"),
        (["series", "-k", "tic", "--cadence", "fortnightly"], "fortnightly"),
        (["top", "--by", "luck"], "by='luck'"),
    ],
)  # fmt: skip
def test_a_view_refuses_what_it_cannot_take(synced, args, message):
    failed = payload("view", *args, *DATASET, code=1)
    assert failed["error"] == "invalid_argument"
    assert message in failed["message"]


def test_an_unknown_view_is_a_usage_error(synced):
    result = run("view", "pie", *DATASET)
    assert result.exit_code == 2
    assert "'pie' is no view" in result.stderr


def test_a_view_before_a_sync_says_to_sync(server):
    failed = payload("view", "coverage", *DATASET, code=1)
    assert failed["error"] == "not_synced"
    assert failed["path"] == str(_store(server).path)
    assert "store sync" in failed["message"]


def test_a_view_of_an_incomplete_store_warns(server):
    server.fail[("ion_series", "batch-02")] = MascopeAPIError("Bad Gateway")
    payload("store", "sync", *DATASET, code=1)
    found = payload("view", "coverage", *DATASET)
    assert found["store"]["complete"] is False
    shown = text("view", "coverage", *DATASET)
    assert "warning: the store is not complete (1 batch(es)" in shown


# --- ASCII -------------------------------------------------------------------


def test_names_are_printed_as_ascii_values(server):
    name = "Kampanja äö‮\nx"
    server.dataset_rows[0]["dataset_name"] = name

    found = run("scopes", "--dataset", "d1", "--json")
    assert found.exit_code == 0 and found.stdout.isascii()

    payload("store", "sync", "--dataset", "d1")
    shown = text("view", "coverage", "--dataset", "d1")
    assert shown.isascii()

    result = run("scopes", "--dataset", "Nope")
    assert result.exit_code == 1
    assert result.stderr.isascii()
    assert "Kampanja \\xe4\\xf6\\u202e\\nx" in result.stderr
    assert cli.quoted(name) == '"Kampanja \\u00e4\\u00f6\\u202e\\nx"'


def test_paths_are_printed_as_ascii(server, tmp_path, monkeypatch):
    # A path is printed as it is, not quoted: its other characters escaped
    monkeypatch.setenv(store_module.ROOT_ENV, str(tmp_path / "st-äö漢"))
    escaped = "st-\\xe4\\xf6\\u6f22"
    for command in (["scopes"], ["store", "sync"], ["store", "status"]):
        assert escaped in text(*command, *DATASET), command

    csv = tmp_path / "ö.csv"
    shown = text("view", "coverage", *DATASET, "--csv", str(csv))
    assert csv.exists()
    assert "  csv " + str(tmp_path / "\\xf6.csv") in shown
    assert escaped in text("store", "rm", *DATASET)


# --- what each view's entries are --------------------------------------------


def test_the_entries_of_diurnal_name_the_hour_and_group(synced, short_campaign):
    _, store = synced
    ion = _ion(short_campaign)
    found = payload("view", "diurnal", *DATASET, "-k", ion, "--by", "weekday")
    frame = views.diurnal(store, [ion], by="weekday")
    (entry,) = found["entries"]
    at = frame["value"].idxmax()
    assert entry["max_at"] == frame.at[at, "hour"]
    assert entry["max_group"] == frame.at[at, "group"]
    assert entry["max"] == pytest.approx(frame["value"].max())
    shown = text("view", "diurnal", *DATASET, "-k", ion, "--by", "weekday")
    assert "HOUR  GROUP" in shown


def test_the_entries_of_composition_by_class_are_the_classes(synced):
    _, store = synced
    found = payload("view", "composition", *DATASET, "--by", "class")
    frame = views.composition(store, "all", by="class")
    assert [e["key"] for e in found["entries"]] == list(
        frame.sort_values("share", ascending=False)["class"]
    )
    assert [e["share"] for e in found["entries"]] == pytest.approx(
        sorted(frame["share"], reverse=True)
    )
    assert "name" not in found["entries"][0]
    shown = text("view", "composition", *DATASET, "--by", "class")
    assert "KEY     N  MEDIAN" in shown


def test_a_formula_of_no_class_is_a_group_of_its_own():
    t = pd.Timestamp("2026-06-01", tz="UTC")
    frame = pd.DataFrame(
        {
            "period": [t, t, t],
            "class": ["CHO", None, "CHON"],
            "mean": [1.0, 2.0, 3.0],
            "share": [1 / 6, 2 / 6, 3 / 6],
        }
    )
    entries = cli._per_key(frame, "class", "mean", "period", share=True)
    assert [entry["key"] for entry in entries] == ["CHON", None, "CHO"]
    assert [entry["share"] for entry in entries] == pytest.approx([0.5, 1 / 3, 1 / 6])
    assert entries[1]["max"] == 2.0 and entries[1]["max_at"] == t
    payload = {"attrs": {"tz": "UTC", "cadence": "all", "by": "class"}, "key": "k"}
    payload.update(rows=3, n_keys=3, entries=entries, csv=[])
    payload["span"] = {"first": t, "last": t}
    status = cli.StoreStatus(path=cli.Path("."), exists=False)
    lines = cli._view_lines("composition", payload, status)
    assert [line.split()[0] for line in lines[4:7]] == ['"CHON"', "(none)", '"CHO"']


def test_the_entries_of_spectrum_are_its_brightest_or_biggest_changes(synced):
    _, store = synced
    found = payload("view", "spectrum", *DATASET)
    frame = views.spectrum(store)
    assert [e["key"] for e in found["entries"]] == list(
        frame.sort_values("value", ascending=False, kind="stable")["key"]
    )
    assert set(found["entries"][0]) == {
        "key", "name", "formula", "ion_formula", "mz", "value", "n"
    }  # fmt: skip

    window = ["--window-to", "2026-06-02", "--other-from", "2026-06-04"]
    found = payload("view", "spectrum", *DATASET, *window)
    frame = views.spectrum(
        store, (None, "2026-06-02"), ("2026-06-04", None), tz=SHORT.timezone
    )
    order = frame["diff"].abs().sort_values(ascending=False, kind="stable").index
    assert [e["key"] for e in found["entries"]] == list(frame.loc[order, "key"])
    assert found["entries"][0]["diff"] == pytest.approx(frame.loc[order[0], "diff"])
    shown = text("view", "spectrum", *DATASET, *window).splitlines()
    (header,) = [line for line in shown if line.startswith("  KEY")]
    assert header.split() == [
        "KEY",
        "NAME",
        "MZ",
        "VALUE",
        "N",
        "OTHER",
        "DIFF",
        "RATIO",
    ]


def test_the_entries_of_top_are_its_ranking(synced):
    _, store = synced
    # Over days: the four days are in one week
    ranked = ["-n", "4", "--by", "ratio", "--period", "day"]
    found = payload("view", "top", *DATASET, *ranked)
    frame = views.top(store, 4, "ratio", period="day")
    assert [e["key"] for e in found["entries"]] == list(frame["key"])
    assert [e["rank"] for e in found["entries"]] == [1, 2, 3, 4]
    species = store.table("species").set_index("series_key")
    assert [e["ion_formula"] for e in found["entries"]] == list(
        species.loc[list(frame["key"]), "ion_formula"]
    )
    assert [e["value"] for e in found["entries"]] == pytest.approx(list(frame["value"]))
    shown = text("view", "top", *DATASET, *ranked)
    assert shown.count("\n") == 3 + 1 + 4 + 2
    # None of them is new
    assert all(line.endswith("  no") for line in shown.splitlines()[4:8])


def test_a_key_without_a_name_goes_by_its_formula_and_the_tic_by_its_own(server):
    # A compound with no name: NaN in the store's species, beside the names
    for rows in server.ions.values():
        rows.loc[rows["target_ion_id"] == "ti0003", "target_compound_name"] = None
    payload("store", "sync", *DATASET)
    keys = ["-k", "ion:ti0003", "-k", "tic"]

    def named(view: str) -> dict[str, str]:
        """Each key's NAME cell in the view's table."""
        # A row of top starts with its rank
        at = 1 if view == "top" else 0
        rows = [
            line.split() for line in text("view", view, *DATASET, *keys).splitlines()
        ]
        return {
            row[at]: row[at + 1]
            for row in rows
            if row[at : at + 1] in (['"ion:ti0003"'], ['"tic"'])
        }

    expected = {'"ion:ti0003"': '"C2H2O2"', '"tic"': '"TIC"'}
    assert named("series") == expected
    assert named("top") == expected
    top = payload("view", "top", *DATASET, *keys)
    (tic,) = [entry for entry in top["entries"] if entry["key"] == "tic"]
    assert (tic["name"], tic["formula"], tic["ion_formula"]) == ("TIC", None, None)

    # A spectrum's keys are formulas, which the store names by themselves:
    # its name cell falls back the same way
    entry = {"key": "formula:C2H2O2|[M+I]-", "name": float("nan"),
             "formula": "C2H2O2", "mz": 200.0, "value": 1.0, "n": 1}  # fmt: skip
    found = {"attrs": {"tz": "UTC", "period": [None, None], "other": None}}
    found.update(key="k", rows=1, n_keys=1, entries=[entry], csv=[])
    found["span"] = {"first": None, "last": None}
    status = cli.StoreStatus(path=cli.Path("."), exists=False)
    (line,) = [
        line
        for line in cli._view_lines("spectrum", found, status)
        if line.startswith('  "formula:')
    ]
    assert line.split()[1] == '"C2H2O2"'


# --- what status says --------------------------------------------------------


def test_status_says_when_and_what_failed(server):
    server.fail[("ion_series", "batch-02")] = MascopeAPIError("Bad Gateway")
    payload("store", "sync", *DATASET, code=1)
    shown = text("store", "status", *DATASET)
    assert "synced 2026-08-01T00:00:00+00:00 (0 s ago)" in shown
    assert 'batch-02  "Campaign batch-02"  failed' in shown
    assert '  batch-02: "Could not read the ion series' in shown


def test_status_marks_a_batch_read_while_a_run_was_in_flight(server):
    server.newer["batch-04"] = [
        {
            "batch_peak_run_id": "rebuilding",
            "action": "rebuild",
            "engine": "mascope",
            "engine_version": "0.9.0",
            "status": "running",
            "current": False,
            "config": None,
        }
    ]
    payload("store", "sync", *DATASET)
    shown = text("store", "status", *DATASET)
    assert 'batch-04  "Campaign batch-04"  synced (run in flight)' in shown
    assert '"Campaign batch-01"  synced  ' in shown


@pytest.mark.parametrize(
    "n_bytes, shown",
    [(0, "0 B"), (999, "999 B"), (1000, "1.0 kB"), (12_300_000, "12.3 MB"),
     (4_000_000_000, "4.0 GB"), (5 * 10**12, "5000.0 GB")],
)  # fmt: skip
def test_sizes_read_in_units(n_bytes, shown):
    assert cli._size(n_bytes) == shown


@pytest.mark.parametrize(
    "seconds, shown",
    [(None, "-"), (0, "0 s"), (59, "59 s"), (60, "1 min"), (3661, "1 h 1 min"),
     (2 * 86400 + 5 * 3600 + 60, "2 d 5 h")],
)  # fmt: skip
def test_ages_read_in_their_two_largest_units(seconds, shown):
    assert cli._age(seconds) == shown


@pytest.mark.parametrize(
    "value, shown",
    [(None, "-"), (True, "yes"), (False, "no"), (1440, "1,440"),
     (123456.7, "1.235e+05"), (float("nan"), "-")],
)  # fmt: skip
def test_numbers_read_short(value, shown):
    assert cli._number(value) == shown


@pytest.mark.parametrize(
    "values, shown",
    [((None, "b"), "b"), ((float("nan"), "b"), "b"), ((pd.NA, "b"), "b"),
     (("", "b"), "b"), (("a", "b"), "a"), ((0, "b"), 0), ((None, None), "-")],
)  # fmt: skip
def test_a_cell_falls_back_past_what_is_not_set(values, shown):
    assert cli._first_set(*values, default="-") == shown


def test_a_stopped_sync_leaves_the_store_incomplete(server):
    from mascope_sdk.exceptions import AuthenticationError

    server.fail[("batch_peaks.runs", "batch-02")] = AuthenticationError("expired")
    failed = payload("store", "sync", *DATASET, code=1)
    assert failed["error"] == "not_authenticated"

    status = payload("store", "status", *DATASET)
    assert status["complete"] is False
    assert status["incomplete"] == [
        "the last sync was stopped before its end",
        "the rollups of 1 day(s) pending",
    ]
    shown = text("store", "status", *DATASET)
    assert (
        "  never synced to its end; NOT complete: the last sync was stopped before "
        "its end; the rollups of 1 day(s) pending"
    ) in shown


#: The views and an option of theirs that takes a value.
NEEDS = {
    "series": ["-k", "tic"],
    "diurnal": ["-k", "tic"],
    "periods": ["-k", "tic"],
    "coverage": [],
    "composition": [],
    "top": [],
}


@pytest.mark.parametrize("name", NEEDS)
def test_every_view_takes_the_window_and_the_zone(synced, name):
    found = payload(
        "view", name, *DATASET, *NEEDS[name], "--tz", "UTC",
        "--window-from", "2026-06-02T00:00:00", "--window-to", "2026-06-04T00:00:00",
    )  # fmt: skip
    assert found["attrs"]["tz"] == "UTC"
    assert found["attrs"]["start"] == "2026-06-02T00:00:00+00:00"
    assert found["attrs"]["end"] == "2026-06-04T00:00:00+00:00"
    shown = text(
        "view", name, *DATASET, *NEEDS[name], "--window-from", "2026-06-02T00:00:00"
    )
    assert "  window 2026-06-02T00:00:00+03:00 .. -" in shown


@pytest.mark.parametrize("name", [*(n for n in NEEDS if n != "coverage"), "spectrum"])
def test_every_view_that_normalises_takes_the_normaliser(synced, name):
    found = payload("view", name, *DATASET, *NEEDS.get(name, []), "--normalise", "tic")
    assert found["attrs"]["normalise"] == "tic"
    assert found["attrs"]["normalise_name"] == "TIC"


def test_spectrum_and_top_take_keys(synced, short_campaign):
    formula = _formula(short_campaign)
    found = payload("view", "spectrum", *DATASET, "-k", formula)
    assert [entry["key"] for entry in found["entries"]] == [formula]
    assert payload("view", "spectrum", *DATASET, "--tz", "UTC")["attrs"]["tz"] == "UTC"
    ion = _ion(short_campaign)
    found = payload("view", "top", *DATASET, "-k", ion, "-k", "tic")
    assert {entry["key"] for entry in found["entries"]} == {ion, "tic"}


def test_the_view_says_how_it_was_computed(synced, short_campaign):
    ion = _ion(short_campaign)
    zone = SHORT.timezone
    found = payload("view", "series", *DATASET, "-k", ion)
    shown = text("view", "series", *DATASET, "-k", ion).splitlines()
    assert shown[1] == (
        f"  cadence {found['attrs']['cadence']} (auto), median per bin, p10-p90 "
        f"band, normalise none, times in {zone}"
    )
    shown = text(
        "view", "series", *DATASET, "-k", "tic", "--cadence", "1h", "--stat", "max",
        "--normalise", ion,
    ).splitlines()  # fmt: skip
    name = payload("view", "series", *DATASET, "-k", "tic", "--normalise", ion)
    assert shown[1] == (
        f'  cadence 1h, max per bin, p10-p90 band, normalise "{ion}" '
        f'("{name["attrs"]["normalise_name"]}"), times in {zone}'
    )
    shown = text("view", "diurnal", *DATASET, "-k", ion, "--by", "season")
    assert f"  cadence hour of day, by season, normalise none, times in {zone}" in shown
    shown = text("view", "coverage", *DATASET, "--min-gap", "90min")
    assert f"  cadence 1h, gaps from 1 h 30 min, times in {zone}" in shown
    shown = text("view", "spectrum", *DATASET, "--window-to", "2026-06-02")
    assert f"  normalise none, times in {zone}" in shown
    assert "  period - .. 2026-06-02T00:00:00+03:00" in shown
    # Every sample's spectrum has no period to say
    assert "  period" not in text("view", "spectrum", *DATASET)
    shown = text("view", "top", *DATASET, "--by", "new")
    assert "  ranked by new, first week 2026-06-01T00:00:00+03:00, last " in shown


def test_coverage_lists_the_longest_gaps_first(server, short_campaign, tmp_path):
    # Two hours of the first day's samples left out: a gap before the day's
    samples = short_campaign.tables["samples"]
    first = samples["t_mid_utc"].iloc[0]
    hidden = samples[
        (samples["t_mid_utc"] >= first + pd.Timedelta(hours=6))
        & (samples["t_mid_utc"] < first + pd.Timedelta(hours=8))
    ]
    server.hidden = set(hidden["sample_item_id"].astype(str))
    payload("store", "sync", *DATASET)
    found = payload("view", "coverage", *DATASET)
    assert found["n_gaps"] == 2
    durations = [gap["duration"] for gap in found["gaps"]]
    assert durations == sorted(durations, reverse=True)
    assert durations[0] >= 86400 > durations[1] > 7000
    assert pd.Timestamp(found["gaps"][0]["start"]) > pd.Timestamp(
        found["gaps"][1]["start"]
    )
    limited = payload("view", "coverage", *DATASET, "--limit", "1")
    assert limited["gaps"] == found["gaps"][:1]
    assert limited["n_gaps"] == 2
    shown = text("view", "coverage", *DATASET, "--limit", "1")
    assert "  ... and 1 more gap(s); --limit 0 lists them all" in shown
    assert "1 d" in shown.splitlines()[-2]
