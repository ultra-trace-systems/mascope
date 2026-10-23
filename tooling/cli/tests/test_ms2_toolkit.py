"""
The MS2 notebook toolkit's isolation widths (`tooling/notebooks/ms2_analysis`).

The toolkit has no suite of its own; it lives under `tooling/` beside the CLI,
and this one runs on every PR. The SDK client is a stand-in that answers the
MS2 summary as the server does, so nothing here needs a server.
"""

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from tooling.notebooks.ms2_analysis.utils.dashboard import (  # noqa: E402
    isolation_width_label,
)
from tooling.notebooks.ms2_analysis.utils.data_extractor import (  # noqa: E402
    DataExtractor,
    Ms2Group,
)


class _Ms2:
    def __init__(self, summary: dict, ms1: dict | None = None):
        self._summary = summary
        self._ms1 = ms1

    def get_summary(self, **_kwargs):
        return self._summary

    def get_averaged_centroids(self, **_kwargs):
        return {}

    def get_ms1_centroids(self, **_kwargs):
        return self._ms1


class _Samples:
    def __init__(self, ms2: _Ms2):
        self._ms2 = ms2

    def ms2(self, _sample_item_id):
        return self._ms2


class _Client:
    def __init__(self, summary: dict, ms1: dict | None = None):
        self.samples = _Samples(_Ms2(summary, ms1))


def _group(mz, width, widths=None, activation="hcd20.00"):
    group = {
        "parent_peak_mz": mz,
        "activation": activation,
        "hcd_energy": [20.0],
        "isolation_width": width,
        "scan_count": 10,
    }
    if widths is not None:
        group["isolation_widths"] = [
            {"isolation_width": w, "scan_count": n} for w, n in widths
        ]
    return group


def _summary(*groups, isolation_width=None):
    return {
        "parent_peaks": sorted({g["parent_peak_mz"] for g in groups}),
        "groups": list(groups),
        "isolation_width": isolation_width,
    }


def _extract(summary, ms1=None):
    return DataExtractor(_Client(summary, ms1), "sample")


def test_a_group_at_two_widths_takes_the_widest():
    data = _extract(_summary(_group(200.0, None, [(1.0, 12), (4.0, 3)])))
    group = Ms2Group(200.0, "hcd20.00")
    assert data.isolation_widths[group] == 4.0
    assert data.isolation_width_counts[group] == [(1.0, 12), (4.0, 3)]


def test_the_ms1_window_of_a_two_width_group_covers_the_widest():
    # An ion 1.5 m/z from the precursor is outside a 1 m/z window but inside a
    # 4 m/z one: only the widest width's window counts it.
    ms1 = {"mz": [200.0, 201.5, 205.0], "intensity": [10.0, 5.0, 100.0]}
    data = _extract(_summary(_group(200.0, None, [(1.0, 12), (4.0, 3)])), ms1)
    assert data.ms1_isolation_tic[Ms2Group(200.0, "hcd20.00")] == 15.0


def test_a_group_at_one_width_keeps_it():
    data = _extract(
        _summary(
            _group(200.0, 1.0, [(1.0, 10)]),
            _group(300.0, 2.0, [(2.0, 10)]),
        )
    )
    assert data.isolation_widths[Ms2Group(200.0, "hcd20.00")] == 1.0
    assert data.isolation_widths[Ms2Group(300.0, "hcd20.00")] == 2.0


def test_a_server_without_widths_falls_back_on_the_file_width():
    data = _extract(_summary(_group(200.0, None), isolation_width=1.5))
    group = Ms2Group(200.0, "hcd20.00")
    assert data.isolation_widths[group] == 1.5
    assert data.isolation_width_counts[group] == []


def test_a_group_with_no_width_at_all_is_still_refused():
    with pytest.raises(ValueError, match="store no isolation width"):
        _extract(_summary(_group(200.0, None, [])))


def test_the_label_names_one_width():
    assert isolation_width_label(1.0, [(1.0, 10)]) == (
        "&emsp; <b>Isolation width:</b> 1.0 m/z"
    )
    assert isolation_width_label(1.5, []) == "&emsp; <b>Isolation width:</b> 1.5 m/z"


def test_the_label_names_every_width_and_the_widest():
    label = isolation_width_label(4.0, [(1.0, 12), (4.0, 3)])
    assert "1.0 m/z (12 scans)" in label
    assert "4.0 m/z (3 scans)" in label
    assert label.endswith("the window uses the widest, 4.0 m/z")


class _NoCompositions:
    matches: dict = {}


def test_the_dashboard_windows_a_two_width_group_at_the_widest(monkeypatch):
    import ipywidgets
    import plotly.graph_objects as go

    from tooling.notebooks.ms2_analysis.utils.dashboard import Ms2Dashboard

    # plotly's FigureWidget needs anywidget, which the environment lacks, and
    # a layout box takes only widgets: plain figures and no layout stand in.
    monkeypatch.setattr(go, "FigureWidget", go.Figure)
    monkeypatch.setattr(ipywidgets, "HBox", lambda *a, **k: None)
    monkeypatch.setattr(ipywidgets, "VBox", lambda *a, **k: None)

    ms1 = {"mz": [200.0, 201.5], "intensity": [10.0, 5.0]}
    data = _extract(_summary(_group(200.0, None, [(1.0, 12), (4.0, 3)])), ms1)
    dashboard = Ms2Dashboard(data, _NoCompositions())
    dashboard._update()
    assert dashboard._info_label.value == isolation_width_label(
        4.0, [(1.0, 12), (4.0, 3)]
    )
    assert tuple(dashboard._fig_survey.layout.xaxis.range) == (198.0, 202.0)
