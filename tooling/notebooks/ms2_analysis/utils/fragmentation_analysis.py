import numpy as np
import pandas as pd
import plotly.graph_objects as go
from IPython.display import HTML, display

from .cluster_classifier import ClusterClassifier
from .composition import CompositionMap, get_composition_label
from .config import MZ_MATCH_TOLERANCE
from .data_extractor import DataExtractor
from .ratio_chart import _COLOR_FRAGMENT, _COLOR_PARENT, RatioChart


_COLOR_TYPE_B = "#D55E00"  # Okabe-Ito orange (colorblind-friendly)


class FragmentationAnalysis:
    """Automated fragmentation‐type analysis for CI‐MS2 data.

    Classifies each parent peak into:

    * Declustering: charge stays with reagent
      (MS2 shows reagent ion).
    * Proton transfer: analyte is deprotonated (neg) or
      protonated (pos). MS2 shows analyte‑derived fragment at
      *parent_mz − proton_transfer_mass*.
    * **Undetermined** - neither fragment is clearly present.

    Then displays separate 100 % stacked bar charts for each type.

    :param data: DataExtractor object containing the MS2 spectra and parent peaks
    :type data: DataExtractor
    :param compositions: Assigned compositions for each MS2 spectrum
    :type compositions: CompositionMap
    :param reagent: Reagent adduct in Mascope notation (e.g. "+[15N]O3-")
    :type reagent: str
    """

    def __init__(
        self,
        data: DataExtractor,
        compositions: CompositionMap,
        reagent: str,
    ):
        self._data = data
        self._compositions = compositions
        self._classifier = ClusterClassifier(data, compositions, reagent)

    @property
    def classification(self) -> pd.DataFrame:
        return self._classifier.classification

    @property
    def classifier(self) -> ClusterClassifier:
        return self._classifier

    def show(self):
        """Display summary, declustering chart, then proton-transfer chart."""
        self._show_summary()
        self.show_declustering()
        self.show_proton_transfer()

    def show_declustering(self):
        """Display the declustering (charge stays with reagent) chart."""
        parents = self._classifier.declustering_parents
        if parents.size == 0:
            display(
                HTML(
                    '<p style="font-family: Arial, Helvetica, sans-serif; '
                    'font-size: 12px; color: #000;">No declustering parents found.</p>'
                )
            )
            return

        df = self._build_declustering_df(parents)
        if df.empty:
            display(
                HTML(
                    '<p style="font-family: Arial, Helvetica, sans-serif; '
                    'font-size: 12px; color: #000;">'
                    "No declustering data to display.</p>"
                )
            )
            return

        self._show_declustering_chart(df)

    def show_proton_transfer(self):
        """Display the proton-transfer (analyte deprotonated/protonated) chart."""
        parents = self._classifier.proton_transfer_parents
        if parents.size == 0:
            display(
                HTML(
                    '<p style="font-family: Arial, Helvetica, sans-serif; '
                    'font-size: 12px; color: #000;">'
                    "No proton-transfer parents found.</p>"
                )
            )
            return

        df = self._build_proton_transfer_df(parents)
        if df.empty:
            display(
                HTML(
                    '<p style="font-family: Arial, Helvetica, sans-serif; '
                    'font-size: 12px; color: #000;">'
                    "No proton-transfer data to display.</p>"
                )
            )
            return

        self._show_proton_transfer_chart(df)

    # --- Summary table ---

    def _show_summary(self):
        clf = self._classifier.classification
        if clf.empty:
            display(
                HTML(
                    '<p style="font-family: Arial, Helvetica, sans-serif; '
                    'font-size: 12px; color: #000;">No parent peaks classified.</p>'
                )
            )
            return

        counts = clf["type"].value_counts()
        n_a = int(counts.get("Declustering", 0))
        n_b = int(counts.get("Proton transfer", 0))
        n_u = int(counts.get("Undetermined", 0))
        total = len(clf)

        reagent_html = RatioChart._to_html_formula(self._classifier.reagent_ion_formula)

        html = (
            '<div style="font-family: Arial, Helvetica, sans-serif; font-size: 12px;'
            " color: #000; max-width: 700px; background: white; padding: 10px;"
            'line-height: 1.6;">'
            f"<b>Fragmentation classification</b> — reagent {reagent_html}<br>"
            f"Declustering: <b>{n_a}</b> &nbsp;|&nbsp; "
            f"Proton transfer: <b>{n_b}</b> &nbsp;|&nbsp; "
            f"Undetermined: <b>{n_u}</b> &nbsp;|&nbsp; "
            f"Total: <b>{total}</b>"
            "</div>"
        )
        display(HTML(html))

    # --- Declustering chart building (m/z-based reagent ion lookup) ---

    def _build_declustering_df(self, parents: np.ndarray) -> pd.DataFrame:
        """Build DataFrame for declustering chart, calculating reagent ion and parent
        fractions for each parent peak.

        :param parents: Parent peaks classified as declustering by the ClusterClassifier
        :type parents: np.ndarray
        :return: DataFrame with columns: composition, mz, fragment_frac (reagent ion
                fraction), parent_frac (parent ion fraction)
        :rtype: pd.DataFrame
        """
        parent_set = set(parents)
        reagent_mz = self._classifier.reagent_ion_mz
        rows = []

        for group in self._data.groups:
            pp = group.parent_peak_mz
            if pp not in parent_set:
                continue

            ms2 = self._data.ms2_spectra[group]
            tic = self._data.ms2_tic[group]
            comp_df = self._compositions.matches.get(group, pd.DataFrame())

            if ms2.mz.size == 0 or tic <= 0:
                continue

            # Parent peak intensity
            parent_idx = int(np.argmin(np.abs(ms2.mz - pp)))
            parent_tic_pct = float(ms2.intensity[parent_idx]) / tic * 100

            # Reagent ion by m/z proximity
            reagent_idx = int(np.argmin(np.abs(ms2.mz - reagent_mz)))
            if np.abs(ms2.mz[reagent_idx] - reagent_mz) > MZ_MATCH_TOLERANCE:
                continue
            reagent_tic_pct = float(ms2.intensity[reagent_idx]) / tic * 100

            total = parent_tic_pct + reagent_tic_pct
            if total <= 0:
                continue

            parent_comp = get_composition_label(pp, comp_df)

            rows.append(
                {
                    "composition": parent_comp,
                    "group": group,
                    "mz": pp,
                    "fragment_frac": reagent_tic_pct / total * 100,
                    "parent_frac": parent_tic_pct / total * 100,
                }
            )

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        return df.sort_values("mz", ascending=True).reset_index(drop=True)

    def _show_declustering_chart(self, df: pd.DataFrame):
        n = len(df)
        x_pos = list(range(n))

        tick_labels = [
            f"{row['mz']:.1f}<br>{RatioChart._to_html_formula(row['composition'])}"
            for _, row in df.iterrows()
        ]

        fig = go.FigureWidget()

        # Bottom bar: Reagent ion fraction
        fig.add_trace(
            go.Bar(
                x=x_pos,
                y=df["fragment_frac"].tolist(),
                name="Reagent ion",
                marker=dict(
                    color=_COLOR_FRAGMENT,
                    line=dict(color="black", width=0.5),
                ),
                hovertemplate=(
                    "<i>m</i>/<i>z</i> %{customdata:.2f}<br>"
                    "Reagent: %{y:.1f}%<extra></extra>"
                ),
                customdata=df["mz"].tolist(),
            )
        )

        # Top bar: Parent fraction
        fig.add_trace(
            go.Bar(
                x=x_pos,
                y=df["parent_frac"].tolist(),
                name="Parent",
                marker=dict(
                    color=_COLOR_PARENT,
                    line=dict(color="black", width=0.5),
                ),
                hovertemplate=(
                    "<i>m</i>/<i>z</i> %{customdata:.2f}<br>"
                    "Parent: %{y:.1f}%<extra></extra>"
                ),
                customdata=df["mz"].tolist(),
            )
        )

        fig.update_layout(
            barmode="stack",
            font=dict(family="Arial, Helvetica, sans-serif", size=11),
            height=400,
            width=max(700, n * 35 + 200),
            legend=dict(
                x=0.98,
                y=0.98,
                xanchor="right",
                yanchor="top",
                font=dict(size=10),
                bgcolor="rgba(255,255,255,0.8)",
                bordercolor="black",
                borderwidth=0.5,
            ),
            xaxis=dict(
                tickvals=x_pos,
                ticktext=tick_labels,
                tickangle=45,
                title="<i>m</i>/<i>z</i>",
                title_font=dict(size=13),
                showline=True,
                linewidth=1,
                linecolor="black",
                mirror=True,
                ticks="outside",
            ),
            yaxis=dict(
                range=[0, 100],
                dtick=20,
                ticksuffix="%",
                title="Fraction (%)",
                title_font=dict(size=13),
                gridcolor="rgba(0,0,0,0.08)",
                showline=True,
                linewidth=1,
                linecolor="black",
                mirror=True,
                ticks="outside",
            ),
            margin=dict(l=80, r=40, t=30, b=120),
            plot_bgcolor="white",
        )

        display(fig)

        # Caption
        reagent_html = RatioChart._to_html_formula(self._classifier.reagent_ion_formula)
        caption = (
            f"<b>Figure.</b> Declustering fragmentation "
            f"across parent peaks. "
            f"Reagent ion: {reagent_html}. "
            f"Blue = reagent ion fraction, grey = parent (intact cluster). "
            f"Each bar shows "
            f"<i>I</i><sub>reagent</sub> / "
            f"(<i>I</i><sub>reagent</sub> + <i>I</i><sub>parent</sub>) "
            f"&times; 100&thinsp;% (bottom) and parent fraction (top). "
            f"Sorted by <i>m</i>/<i>z</i>."
        )
        display(
            HTML(
                '<p style="font-family: Arial, Helvetica, sans-serif; '
                "font-size: 11px; max-width: 700px; line-height: 1.45; "
                f'color: #000; background: white; padding: 8px;">'
                f"{caption}</p>"
            )
        )

    # --- Proton-transfer chart building ---

    def _build_proton_transfer_df(self, parents: np.ndarray) -> pd.DataFrame:
        """Build DataFrame for proton-transfer chart, calculating analyte fragment and
        parent fractions for each parent peak.

        :param parents: Parent peaks classified as proton-transfer by the
                        ClusterClassifier
        :type parents: np.ndarray
        :return: DataFrame with columns: composition, fragment_composition, mz,
                fragment_mz, fragment_frac (analyte fragment fraction),
                parent_frac (parent ion fraction)
        :rtype: pd.DataFrame
        """
        parent_set = set(parents)
        pt_mass = self._classifier.proton_transfer_mass
        rows = []

        for group in self._data.groups:
            pp = group.parent_peak_mz
            if pp not in parent_set:
                continue

            ms2 = self._data.ms2_spectra[group]
            tic = self._data.ms2_tic[group]
            comp_df = self._compositions.matches.get(group, pd.DataFrame())

            if ms2.mz.size == 0 or tic <= 0:
                continue

            # Parent peak intensity
            parent_idx = int(np.argmin(np.abs(ms2.mz - pp)))
            parent_tic_pct = float(ms2.intensity[parent_idx]) / tic * 100

            # Analyte fragment at parent_mz - proton_transfer_mass
            frag_mz = pp - pt_mass
            frag_idx = int(np.argmin(np.abs(ms2.mz - frag_mz)))
            if np.abs(ms2.mz[frag_idx] - frag_mz) > MZ_MATCH_TOLERANCE:
                continue
            frag_tic_pct = float(ms2.intensity[frag_idx]) / tic * 100

            total = parent_tic_pct + frag_tic_pct
            if total <= 0:
                continue

            # Composition labels
            parent_comp = get_composition_label(pp, comp_df)
            frag_comp = get_composition_label(ms2.mz[frag_idx], comp_df)

            rows.append(
                {
                    "composition": parent_comp,
                    "fragment_composition": frag_comp,
                    "group": group,
                    "mz": pp,
                    "fragment_mz": float(ms2.mz[frag_idx]),
                    "fragment_frac": frag_tic_pct / total * 100,
                    "parent_frac": parent_tic_pct / total * 100,
                }
            )

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        return df.sort_values("mz", ascending=True).reset_index(drop=True)

    def _show_proton_transfer_chart(self, df: pd.DataFrame):
        n = len(df)
        x_pos = list(range(n))

        tick_labels = [
            f"{row['mz']:.1f}<br>{RatioChart._to_html_formula(row['composition'])}"
            for _, row in df.iterrows()
        ]

        fig = go.FigureWidget()

        # Bottom bar: Analyte fragment fraction
        fig.add_trace(
            go.Bar(
                x=x_pos,
                y=df["fragment_frac"].tolist(),
                name="Analyte fragment",
                marker=dict(color=_COLOR_TYPE_B, line=dict(color="black", width=0.5)),
                hovertemplate=(
                    "<i>m</i>/<i>z</i> %{customdata[0]:.2f}<br>"
                    "Fragment <i>m</i>/<i>z</i> %{customdata[1]:.2f}<br>"
                    "Analyte frag: %{y:.1f}%<extra></extra>"
                ),
                customdata=list(zip(df["mz"].tolist(), df["fragment_mz"].tolist())),
            )
        )

        # Top bar: Parent fraction
        fig.add_trace(
            go.Bar(
                x=x_pos,
                y=df["parent_frac"].tolist(),
                name="Parent",
                marker=dict(color=_COLOR_PARENT, line=dict(color="black", width=0.5)),
                hovertemplate=(
                    "<i>m</i>/<i>z</i> %{customdata:.2f}<br>"
                    "Parent: %{y:.1f}%<extra></extra>"
                ),
                customdata=df["mz"].tolist(),
            )
        )

        fig.update_layout(
            barmode="stack",
            font=dict(family="Arial, Helvetica, sans-serif", size=11),
            height=400,
            width=max(700, n * 35 + 200),
            legend=dict(
                x=0.98,
                y=0.98,
                xanchor="right",
                yanchor="top",
                font=dict(size=10),
                bgcolor="rgba(255,255,255,0.8)",
                bordercolor="black",
                borderwidth=0.5,
            ),
            xaxis=dict(
                tickvals=x_pos,
                ticktext=tick_labels,
                tickangle=45,
                title="<i>m</i>/<i>z</i>",
                title_font=dict(size=13),
                showline=True,
                linewidth=1,
                linecolor="black",
                mirror=True,
                ticks="outside",
            ),
            yaxis=dict(
                range=[0, 100],
                dtick=20,
                ticksuffix="%",
                title="Fraction (%)",
                title_font=dict(size=13),
                gridcolor="rgba(0,0,0,0.08)",
                showline=True,
                linewidth=1,
                linecolor="black",
                mirror=True,
                ticks="outside",
            ),
            margin=dict(l=80, r=40, t=30, b=120),
            plot_bgcolor="white",
        )

        display(fig)

        # Caption
        reagent_html = RatioChart._to_html_formula(self._classifier.reagent_ion_formula)
        caption = (
            f"<b>Figure.</b> Proton transfer fragmentation "
            f"across parent peaks. Reagent: {reagent_html}. "
            f"Orange = deprotonated analyte fraction "
            f"(<i>m</i>/<i>z</i><sub>parent</sub> \u2212 "
            f"<i>m</i><sub>neutral leaving group</sub>), "
            f"grey = parent (intact cluster). "
            f"Each bar shows "
            f"<i>I</i><sub>analyte</sub> / "
            f"(<i>I</i><sub>analyte</sub> + <i>I</i><sub>parent</sub>) "
            f"&times; 100&thinsp;% (bottom) and parent fraction (top). "
            f"Sorted by <i>m</i>/<i>z</i>."
        )
        display(
            HTML(
                '<p style="font-family: Arial, Helvetica, sans-serif; '
                "font-size: 11px; max-width: 700px; line-height: 1.45; "
                f'color: #000; background: white; padding: 8px;">'
                f"{caption}</p>"
            )
        )
