import re

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from IPython.display import HTML, display

from .composition import CompositionMap, get_composition_label
from .data_extractor import DataExtractor


_COLOR_FRAGMENT = "#0072B2"  # Okabe-Ito blue (colorblind-friendly)
_COLOR_PARENT = "#AAAAAA"  # Muted grey


class RatioChart:
    def __init__(
        self,
        data: DataExtractor,
        compositions: CompositionMap,
        target_fragment: str,
        parent_filter: set[float] | None = None,
    ):
        self._data = data
        self._compositions = compositions
        self._target_fragment = target_fragment.strip()
        self._parent_filter = parent_filter
        self._ratio_df = self._build_ratio_df()

    def _build_ratio_df(self) -> pd.DataFrame:
        """Build a DataFrame with the target fragment fraction for each parent peak."""
        rows = []
        for group in self._data.groups:
            pp = group.parent_peak_mz
            if self._parent_filter is not None and pp not in self._parent_filter:
                continue
            ms2_spec = self._data.ms2_spectra[group]
            ms2_tic = self._data.ms2_tic[group]
            comp_df = self._compositions.matches.get(group, pd.DataFrame())

            # Parent peak TIC% in MS2: find peak closest to pp
            if ms2_spec.mz.size == 0 or ms2_tic <= 0:
                continue
            parent_idx = np.argmin(np.abs(ms2_spec.mz - pp))
            parent_tic_pct = float(ms2_spec.intensity[parent_idx]) / ms2_tic * 100

            # Target fragment TIC% in MS2
            fragment_tic_pct = 0.0
            if not comp_df.empty and "ion" in comp_df.columns:
                ion_values = comp_df["ion"].astype(str).str.strip()
                match_mask = ion_values == self._target_fragment
                if match_mask.any():
                    frag_idx = match_mask.values.nonzero()[0][0]
                    fragment_tic_pct = (
                        float(ms2_spec.intensity[frag_idx]) / ms2_tic * 100
                    )

            total = parent_tic_pct + fragment_tic_pct
            if total <= 0:
                continue

            # Parent composition label
            parent_comp = get_composition_label(pp, comp_df)

            rows.append(
                {
                    "composition": parent_comp,
                    "group": group,
                    "mz": pp,
                    "fragment_frac": fragment_tic_pct / total * 100,
                    "parent_frac": parent_tic_pct / total * 100,
                }
            )

        if not rows:
            return pd.DataFrame(
                columns=["composition", "mz", "fragment_frac", "parent_frac"]
            )

        df = pd.DataFrame(rows)
        df = (
            df.groupby("composition", sort=False)
            .agg({"mz": "mean", "fragment_frac": "mean", "parent_frac": "mean"})
            .reset_index()
        )
        return df.sort_values("mz", ascending=True).reset_index(drop=True)

    @staticmethod
    def _to_html_formula(formula: str) -> str:
        """Convert an ion formula string to HTML with sub/superscript tags."""
        if not formula or formula == "---":
            return formula

        s = str(formula).strip()

        # Extract isotope brackets, e.g. [15N] -> placeholder + HTML
        isotope_placeholders: list[tuple[str, str]] = []

        def _replace_isotope(m: re.Match) -> str:
            mass, elem = m.group(1), m.group(2)
            tag = chr(ord("A") + len(isotope_placeholders))
            placeholder = f"\x00{tag}\x00"
            isotope_placeholders.append((placeholder, f"<sup>{mass}</sup>{elem}"))
            return placeholder

        s = re.sub(r"\[(\d+)([A-Za-z]+)\]", _replace_isotope, s)

        # Extract trailing charge sign only
        charge_match = re.search(r"([+\-]+)$", s)
        charge_html = ""
        if charge_match:
            charge_str = charge_match.group(1).replace("-", "\u2212")
            charge_html = f"<sup>{charge_str}</sup>"
            s = s[: charge_match.start()]

        # Subscript element counts (digits not inside placeholders)
        s = re.sub(r"(\d+)", r"<sub>\1</sub>", s)

        # Re-insert isotope HTML
        for placeholder, html in isotope_placeholders:
            s = s.replace(placeholder, html)

        return s + charge_html

    def show(self):
        """Display a 100% stacked bar chart with a figure caption."""
        df = self._ratio_df
        if df.empty:
            display(
                go.FigureWidget().update_layout(
                    title="No data: target fragment not found in any parent peak",
                    height=200,
                )
            )
            return

        n = len(df)
        x_pos = list(range(n))

        # Build tick labels: m/z value + HTML formula
        tick_labels = [
            f"{row['mz']:.1f}<br>{self._to_html_formula(row['composition'])}"
            for _, row in df.iterrows()
        ]

        fig = go.FigureWidget()

        # --- Bottom bar: Target fragment fraction ---
        fig.add_trace(
            go.Bar(
                x=x_pos,
                y=df["fragment_frac"].tolist(),
                name=f"Target fragment ({self._target_fragment})",
                marker=dict(color=_COLOR_FRAGMENT, line=dict(color="black", width=0.5)),
                hovertemplate=(
                    "<i>m</i>/<i>z</i> %{customdata:.2f}<br>"
                    "Fragment: %{y:.1f}%<extra></extra>"
                ),
                customdata=df["mz"].tolist(),
            )
        )

        # --- Top bar: Parent fraction ---
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

        # --- Layout ---
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

        # --- Figure caption ---
        frag_html = self._to_html_formula(self._target_fragment)
        caption = (
            f"<b>Figure.</b> Fragmentation efficiency for {frag_html} across parent "
            f"peaks, shown as a 100&thinsp;% stacked bar chart. "
            f"Blue = target fragment fraction, grey = parent (intact cluster). "
            f"Each bar represents "
            f"<i>I</i><sub>fragment</sub> / "
            f"(<i>I</i><sub>fragment</sub> + <i>I</i><sub>parent</sub>) "
            f"&times; 100&thinsp;% (bottom) and the complementary parent fraction "
            f"(top), computed from the averaged MS2 spectrum. "
            f"Compounds are sorted by <i>m</i>/<i>z</i>; assigned molecular "
            f"compositions are shown beneath each bar."
        )
        display(
            HTML(
                '<p style="font-family: Arial, Helvetica, sans-serif; '
                "font-size: 11px; max-width: 700px; line-height: 1.45; "
                f'color: #333; background: white; padding: 8px;">'
                f"{caption}</p>"
            )
        )
