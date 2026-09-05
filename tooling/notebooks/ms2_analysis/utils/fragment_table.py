import base64

import ipywidgets as widgets
import numpy as np
import pandas as pd
from IPython.display import display

from .composition import CompositionMap, get_composition_label
from .config import DEFAULT_MAX_FRAGMENTS
from .data_extractor import DataExtractor


class FragmentTable:
    def __init__(
        self,
        data: DataExtractor,
        compositions: CompositionMap,
        max_fragments: int = DEFAULT_MAX_FRAGMENTS,
    ):
        self._data = data
        self._compositions = compositions
        self._max_fragments = max_fragments
        self._current_df = pd.DataFrame()

        # One entry per group: a stepped-energy acquisition gives one
        # precursor several spectra, one per collision energy.
        self._parent_peak_options = {str(group): group for group in data.groups}

        self._dropdown = widgets.Dropdown(
            options=self._parent_peak_options,
            description="Parent peak:",
            style={"description_width": "auto"},
            layout=widgets.Layout(width="250px"),
        )
        self._dropdown.observe(self._update, names="value")

        self._export_btn = widgets.Button(
            description="Export CSV",
            icon="download",
            layout=widgets.Layout(width="130px"),
        )
        self._export_btn.on_click(self._export_csv)

        self._table_html = widgets.HTML(value="")
        self._download_html = widgets.HTML(value="")

        self._container = widgets.VBox(
            [
                widgets.HBox(
                    [self._dropdown, self._export_btn],
                    layout=widgets.Layout(align_items="center", gap="8px"),
                ),
                self._table_html,
                self._download_html,
            ]
        )

    def show(self):
        """Displays the fragment table widget."""
        self._update()
        display(self._container)

    def _build_table_df(self, group) -> pd.DataFrame:
        """Builds a DataFrame for the fragments of the given MS2 group."""
        ms2_spec = self._data.ms2_spectra[group]
        ms2_tic = self._data.ms2_tic[group]
        comp_df = self._compositions.matches.get(group, pd.DataFrame())

        frag_mzs = ms2_spec.mz
        frag_ints = ms2_spec.intensity
        n = len(frag_mzs)

        compositions = ["---"] * n
        if not comp_df.empty and "ion" in comp_df.columns and len(comp_df) == n:
            for i, ion in enumerate(comp_df["ion"].values):
                if pd.notna(ion) and str(ion).strip() and str(ion).strip() != "---":
                    compositions[i] = str(ion).strip()

        pct_tic = (frag_ints / ms2_tic * 100) if ms2_tic > 0 else np.zeros(n)

        fragments = pd.DataFrame(
            {
                "m/z": frag_mzs,
                "Intensity": frag_ints,
                "% TIC": pct_tic,
                "Composition": compositions,
            }
        )
        return (
            fragments.sort_values("Intensity", ascending=False)
            .head(self._max_fragments)
            .reset_index(drop=True)
        )

    def _get_parent_info(self, group) -> dict:
        """Retrieves metadata for the given MS2 group."""
        parent_int = self._data.parent_peak_intensities[group]
        iso_tic = self._data.ms1_isolation_tic[group]
        pct_tic = (parent_int / iso_tic * 100) if iso_tic > 0 else 0.0
        hcd = self._data.hcd_energy_map[group]
        comp_df = self._compositions.matches.get(group, pd.DataFrame())

        parent_comp = get_composition_label(group.parent_peak_mz, comp_df)

        return {
            "intensity": parent_int,
            "pct_tic": pct_tic,
            "composition": parent_comp,
            "hcd": hcd,
        }

    def _render_html(self, parent_info: dict, df: pd.DataFrame) -> str:
        """Renders the HTML for the parent peak metadata and fragment table."""
        pi = parent_info
        hcd_str = ", ".join(str(v) for v in pi["hcd"])
        html = (
            "<table style='font-family:monospace; font-size:13px; margin-bottom:8px; "
            "border-collapse:collapse;'>"
            f"<tr><td style='padding:2px 8px; color:#888;'>Intensity (MS1)</td>"
            f"<td style='padding:2px 8px;'><b>{pi['intensity']:,.0f}</b></td></tr>"
            f"<tr><td style='padding:2px 8px; color:#888;'>% TIC (isolation window)</td>"
            f"<td style='padding:2px 8px;'><b>{pi['pct_tic']:.1f}</b></td></tr>"
            f"<tr><td style='padding:2px 8px; color:#888;'>HCD energy</td>"
            f"<td style='padding:2px 8px;'><b>{hcd_str} V</b></td></tr>"
            f"<tr><td style='padding:2px 8px; color:#888;'>Composition</td>"
            f"<td style='padding:2px 8px;'><b>{pi['composition']}</b></td></tr>"
            "</table>"
        )

        html += (
            "<table style='border-collapse:collapse; font-family:monospace; font-size:13px;'>"
            "<thead><tr style='background:#f0f0f0; border-bottom:2px solid #888;'>"
        )
        for col in df.columns:
            html += f"<th style='padding:4px 10px; text-align:right;'>{col}</th>"
        html += "</tr></thead><tbody>"

        for _, row in df.iterrows():
            html += "<tr style='border-bottom:1px solid #ddd;'>"
            for col in df.columns:
                val = row[col]
                if col == "m/z":
                    val = f"{val:.4f}"
                elif col == "Intensity":
                    val = f"{val:,.0f}"
                elif col == "% TIC":
                    val = f"{val:.1f}"
                html += f"<td style='padding:4px 10px; text-align:right;'>{val}</td>"
            html += "</tr>"

        html += "</tbody></table>"
        return html

    def _update(self, change=None):
        """Updates the fragment table based on the selected parent peak."""
        group = self._dropdown.value
        self._current_df = self._build_table_df(group)
        parent_info = self._get_parent_info(group)
        self._table_html.value = self._render_html(parent_info, self._current_df)
        self._download_html.value = ""

    def _export_csv(self, _btn=None):
        """Prepares the CSV data for download and updates the download link."""
        import io

        buf = io.StringIO()
        for group in self._data.groups:
            pi = self._get_parent_info(group)
            hcd_str = ", ".join(str(v) for v in pi["hcd"])

            buf.write(f"m/z,{group.parent_peak_mz:.4f}\n")
            buf.write(f"Activation,{group.activation}\n")
            buf.write(f"Intensity (MS1),{pi['intensity']:.0f}\n")
            buf.write(f"% TIC (isolation window),{pi['pct_tic']:.1f}\n")
            buf.write(f'HCD energy,"{hcd_str} V"\n')
            buf.write(f"Composition,{pi['composition']}\n")

            df = self._build_table_df(group)
            df.to_csv(buf, index=False)
            buf.write("\n")

        b64 = base64.b64encode(buf.getvalue().encode()).decode()
        filename = "ms2_fragments.csv"
        self._download_html.value = (
            f'<a download="{filename}" '
            f'href="data:text/csv;base64,{b64}" '
            f'style="display:inline-block; margin-top:4px; padding:4px 12px; '
            f'background:#1976d2; color:white; border-radius:4px; text-decoration:none;">'
            f"Download {filename}</a>"
        )
