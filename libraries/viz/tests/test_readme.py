"""The package README's chart examples run as written, against a synced
store: an example that raises teaches nothing."""

import re
from pathlib import Path

import pandas as pd
import pytest

from mascope_viz.charts import Figure


README = Path(__file__).parents[1] / "README.md"


def _chart_example(
    heading: str = "## Charts: the Figure spec and its renderers",
) -> str:
    section = README.read_text().split(heading, 1)[1]
    match = re.search(r"```python\n(.*?)```", section, re.S)
    assert match, f"the section {heading!r} has no Python example"
    return match.group(1)


@pytest.mark.parametrize("campaign", ["halide_negative"], indirect=True)
def test_the_readme_chart_example_runs_on_a_synced_store(synced, tmp_path, monkeypatch):
    store = synced[2].store
    code = _chart_example()
    assert '"ion:..."' in code
    species = store.table("species")
    named = species.dropna(subset=["name"])
    key = next(key for key in named["series_key"] if key.startswith("ion:"))
    name = named.loc[named["series_key"] == key, "name"].iloc[0]
    # Another batch's row for the species without its name, as a batch
    # fetched before the species was named gives it.
    unnamed = species[species["series_key"] == key].head(1).assign(name=None)
    table = store.table
    monkeypatch.setattr(
        store,
        "table",
        lambda which, **kw: (
            pd.concat([table(which, **kw), unnamed], ignore_index=True)
            if which == "species"
            else table(which, **kw)
        ),
    )
    monkeypatch.chdir(tmp_path)
    namespace = {"store": store}
    exec(compile(code.replace('"ion:..."', repr(key)), str(README), "exec"), namespace)
    fig = namespace["fig"]
    assert isinstance(fig, Figure)
    # Its legend names each series by its species' name, never by its key.
    names = dict(zip(fig.data["key"], fig.data["name"]))
    assert names == {key: name, "tic": "TIC"}
    assert {path.name for path in tmp_path.iterdir()} >= {"species.png", "species.html"}


@pytest.mark.parametrize("campaign", ["halide_negative"], indirect=True)
def test_the_readme_chart_kinds_example_runs_on_a_synced_store(
    synced, tmp_path, monkeypatch
):
    store = synced[2].store
    code = _chart_example("## The chart kinds\n")
    assert '"ion:..."' in code
    key = next(k for k in store.table("species")["series_key"] if k.startswith("ion:"))
    monkeypatch.chdir(tmp_path)
    namespace = {"store": store}
    exec(compile(code.replace('"ion:..."', repr(key)), str(README), "exec"), namespace)
    fig = namespace["fig"]
    assert isinstance(fig, Figure) and fig.id == "van_krevelen"
    assert set(fig.summary["by_class"]) >= {"CHO", "CHON", "CHOS"}
    assert (tmp_path / "van_krevelen.html").exists()
