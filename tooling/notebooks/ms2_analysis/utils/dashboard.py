import ipywidgets as widgets
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from colorcet import glasbey_hv as colormap
from IPython.display import display

from .composition import CompositionMap, get_composition_label
from .data_extractor import DataExtractor


class Ms2Dashboard:
    def __init__(self, data: DataExtractor, compositions: CompositionMap):
        self._data = data
        self._compositions = compositions
        self._half_iso = data.isolation_width / 2

        # Build dropdown options. One entry per group, not per precursor: a
        # stepped-energy acquisition measures one precursor at several
        # collision energies, and each is its own spectrum.
        self._parent_peak_options = {
            f"{group} (HCD "
            f"{','.join(str(v) for v in data.hcd_energy_map[group])}V)": group
            for group in data.groups
        }

        # Figures
        self._fig_survey = go.FigureWidget()
        self._fig_survey.update_layout(
            title="Averaged Survey Spectrum (MS1)",
            height=300,
            xaxis_title="m/z",
            yaxis_title="Intensity",
            margin=dict(l=60, r=20, t=40, b=40),
        )

        self._fig_fragments = go.FigureWidget()
        self._fig_fragments.update_layout(
            title="Averaged Fragment Spectrum (MS2)",
            height=300,
            xaxis_title="m/z",
            yaxis_title="Intensity",
            margin=dict(l=60, r=20, t=40, b=40),
        )

        # Widgets
        self._parent_dropdown = widgets.Dropdown(
            options=self._parent_peak_options,
            description="Parent peak:",
            style={"description_width": "auto"},
            layout=widgets.Layout(width="350px"),
        )
        self._info_label = widgets.HTML(value="")
        self._parent_dropdown.observe(self._update, names="value")

        self._dashboard = widgets.VBox(
            [
                widgets.HBox(
                    [self._parent_dropdown, self._info_label],
                    layout=widgets.Layout(align_items="center"),
                ),
                self._fig_survey,
                self._fig_fragments,
            ]
        )

    def show_fragments(self):
        """Display the MS1 survey and MS2 fragment spectra dashboard."""
        self._update()
        display(self._dashboard)

    @staticmethod
    def _make_stem_traces(
        mz,
        intensity,
        color="steelblue",
        name="Peaks",
        highlight_mz=None,
        highlight_tol=0.01,
    ):
        """Create stem-plot traces: vertical lines for a centroided spectrum."""
        xs, ys = [], []
        for m, i in zip(mz, intensity):
            xs.extend([m, m, None])
            ys.extend([0, i, None])

        traces = [
            go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                line=dict(color=color, width=1),
                name=name,
                showlegend=False,
                hoverinfo="skip",
            ),
            go.Scatter(
                x=mz,
                y=intensity,
                mode="markers",
                marker=dict(size=4, color=color),
                name=name,
                hovertemplate="m/z: %{x:.4f}<br>Intensity: %{y:.2e}<extra></extra>",
            ),
        ]

        if highlight_mz is not None:
            idx = np.argmin(np.abs(mz - highlight_mz))
            if abs(mz[idx] - highlight_mz) < highlight_tol:
                traces.append(
                    go.Scatter(
                        x=[mz[idx]],
                        y=[intensity[idx]],
                        mode="markers",
                        marker=dict(size=12, color="orange", symbol="diamond"),
                        name="Parent peak",
                        hovertemplate="Parent m/z: %{x:.4f}<br>Intensity: %{y:.2e}<extra></extra>",
                    )
                )

        return traces

    def _update(self, change=None):
        group = self._parent_dropdown.value
        pp = group.parent_peak_mz
        d = self._data
        half_iso = self._half_iso

        # --- Info label ---
        self._info_label.value = (
            f"&emsp; <b>Isolation width:</b> {d.isolation_width} m/z"
        )

        # --- Composition data (needed by both MS1 and MS2 charts) ---
        ms2_spec = d.ms2_spectra[group]
        comp_df = self._compositions.matches.get(group, pd.DataFrame())
        comp_mzs = (
            comp_df["mz"].values
            if not comp_df.empty and "mz" in comp_df.columns
            else np.array([])
        )
        comp_ions = (
            comp_df["ion"].values
            if not comp_df.empty and "ion" in comp_df.columns
            else np.array([])
        )

        # Look up parent peak composition from MS2 fragment matches
        parent_ion_label = None
        if len(comp_mzs) > 0:
            idx = np.argmin(np.abs(comp_mzs - pp))
            if abs(comp_mzs[idx] - pp) < half_iso:
                ion = comp_ions[idx]
                if pd.notna(ion) and str(ion).strip() and ion != "---":
                    parent_ion_label = str(ion).strip()

        # --- Survey spectrum (MS1 within isolation window) ---
        mz = d.ms1_spectrum.mz
        intensity = d.ms1_spectrum.intensity
        within = np.abs(mz - pp) <= half_iso
        mz_w, int_w = mz[within], intensity[within]
        ms1_y_max = max(float(np.max(int_w)), 1.0) if len(int_w) > 0 else 1.0
        ms1_label_offset = 0.03 * ms1_y_max

        with self._fig_survey.batch_update():
            self._fig_survey.data = []
            if len(mz_w) > 0:
                for t in self._make_stem_traces(
                    mz_w,
                    int_w,
                    color="steelblue",
                    name="MS1",
                    highlight_mz=pp,
                    highlight_tol=half_iso,
                ):
                    self._fig_survey.add_trace(t)
                self._fig_survey.update_xaxes(range=[pp - half_iso, pp + half_iso])

                # Annotate parent peak with its ion composition
                if parent_ion_label is not None:
                    pidx = np.argmin(np.abs(mz_w - pp))
                    self._fig_survey.add_trace(
                        go.Scatter(
                            x=[float(mz_w[pidx])],
                            y=[float(int_w[pidx]) + ms1_label_offset],
                            mode="text",
                            text=[parent_ion_label],
                            textposition="top center",
                            textfont=dict(size=13),
                            showlegend=False,
                            cliponaxis=False,
                            hoverinfo="skip",
                        )
                    )
                self._fig_survey.update_layout(yaxis_range=[0, ms1_y_max * 1.15])
            self._fig_survey.update_layout(uirevision=group.key)

        # --- Fragment spectrum (MS2) ---

        with self._fig_fragments.batch_update():
            self._fig_fragments.data = []
            if ms2_spec.mz.size > 0:
                ms2_y_max = max(float(np.max(ms2_spec.intensity)), 1.0)
                ms2_label_offset = 0.03 * ms2_y_max
                for t in self._make_stem_traces(
                    ms2_spec.mz, ms2_spec.intensity, color="seagreen", name="MS2"
                ):
                    self._fig_fragments.add_trace(t)

                # Add composition labels above assigned peaks
                if len(comp_ions) == len(ms2_spec.mz):
                    label_mzs, label_ints, label_texts = [], [], []
                    for i, ion in enumerate(comp_ions):
                        if pd.notna(ion) and str(ion).strip() and ion != "---":
                            label_mzs.append(float(ms2_spec.mz[i]))
                            label_ints.append(
                                float(ms2_spec.intensity[i]) + ms2_label_offset
                            )
                            label_texts.append(str(ion))
                    if label_texts:
                        self._fig_fragments.add_trace(
                            go.Scatter(
                                x=label_mzs,
                                y=label_ints,
                                mode="text",
                                text=label_texts,
                                textposition="top center",
                                textfont=dict(size=13),
                                showlegend=False,
                                cliponaxis=False,
                                hoverinfo="skip",
                            )
                        )
                self._fig_fragments.update_layout(yaxis_range=[0, ms2_y_max * 1.15])
            self._fig_fragments.update_layout(uirevision=group.key)

    def show_timeseries(self, n_fragments: int = 3, normalize_by: str | None = "tic"):
        """Display an interactive timeseries dashboard for MS2 fragments.

        Timeseries data is loaded lazily — only when a parent peak is selected
        in the dropdown. The top *n_fragments* fragments (by total intensity)
        are plotted.

        :param n_fragments: Number of top fragments to plot, defaults to 3.
        :type n_fragments: int
        :param normalize_by: Normalization mode passed to the server.
            ``"tic"`` normalizes by scan TIC, ``None`` returns raw intensities.
        :type normalize_by: str
        """
        fig_ts = go.FigureWidget()
        y_title = "Intensity (TIC-normalized)" if normalize_by == "tic" else "Intensity"
        fig_ts.update_layout(
            title="Fragment Timeseries",
            height=350,
            xaxis_title="Time",
            yaxis_title=y_title,
            margin=dict(l=60, r=20, t=40, b=40),
        )

        ts_dropdown = widgets.Dropdown(
            options=self._parent_peak_options,
            description="Parent peak:",
            style={"description_width": "auto"},
            layout=widgets.Layout(width="350px"),
        )

        d = self._data
        compositions = self._compositions

        def _on_parent_change(change=None):
            group = ts_dropdown.value
            pp = group.parent_peak_mz

            # Lazy fetch: request the timeseries for this single group
            ts_data = d._ms2.get_timeseries(
                parent_peak_mz=pp,
                noise_threshold=d.params.get("noise_threshold", 10.0),
                parent_peak_tolerance=d.params.get("parent_peak_tolerance", 0.001),
                normalize_by=normalize_by,
                activation=group.activation or None,
            )

            with fig_ts.batch_update():
                fig_ts.data = []
                if ts_data is None or not ts_data.get("mz_values"):
                    fig_ts.update_layout(uirevision=group.key)
                    return

                ts_df = pd.DataFrame(
                    data=ts_data["values"],
                    index=pd.Index(ts_data["mz_values"], name="mz"),
                    columns=pd.to_datetime(ts_data["time"]),
                )

                if ts_df.empty:
                    fig_ts.update_layout(uirevision=group.key)
                    return

                # Select top N fragments by total intensity
                totals = ts_df.sum(axis=1).sort_values(ascending=False)
                top_mzs = totals.head(n_fragments).index

                comp_df = compositions.matches.get(group, pd.DataFrame())

                for i, frag_mz in enumerate(top_mzs):
                    row = ts_df.loc[frag_mz]
                    rgb = colormap[i % len(colormap)]
                    color = (
                        f"rgb({int(rgb[0] * 255)},"
                        f"{int(rgb[1] * 255)},"
                        f"{int(rgb[2] * 255)})"
                    )
                    x_str = [
                        t.isoformat() if hasattr(t, "isoformat") else str(t)
                        for t in row.index
                    ]

                    ion_label = get_composition_label(frag_mz, comp_df)
                    trace_name = (
                        f"{frag_mz:.4f} m/z ({ion_label})"
                        if ion_label != "---"
                        else f"{frag_mz:.4f} m/z"
                    )

                    fig_ts.add_trace(
                        go.Scatter(
                            x=x_str,
                            y=row.values.astype(float),
                            mode="lines",
                            name=trace_name,
                            line=dict(color=color, width=1.5),
                            hovertemplate=(
                                f"m/z {frag_mz:.4f}<br>"
                                "Time: %{x}<br>Intensity: %{y:.4e}<extra></extra>"
                            ),
                        )
                    )

                fig_ts.update_layout(uirevision=group.key)

        ts_dropdown.observe(_on_parent_change, names="value")
        _on_parent_change()

        dashboard = widgets.VBox([ts_dropdown, fig_ts])
        display(dashboard)
