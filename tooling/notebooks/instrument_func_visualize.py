"""Plotly figure for the instrument-function notebooks.

Used by `voila_instrument_functions.ipynb`, which is the only caller. This lives
here rather than in `mascope_signal` so that the library does not carry plotly:
it was the one module in `libraries/signal` importing it, and plotly is declared
only in the root dev group, so `mascope_signal` could not import this module in a
production install anyway.

Its companion `update_chosen_peak` is not here: both instrument-function
notebooks define their own inline, so the library copy had no callers.
"""

from functools import partial

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots


subtitles = ("FWHM", "Chosen peak", "Resolution function")


def visualize(
    p_mzs: np.ndarray,
    p_fwhms: np.ndarray,
    p_fwhms_fit: np.ndarray,
    ndev: int,
    res_fun: partial,
) -> go.Figure:
    """Visualize the FWHM, chosen peak, and resolution function.

    This function creates a Plotly figure with subplots to visualize the Full Width at Half Maximum (FWHM),
    the chosen peak, and the resolution function based on the provided data.

    :param p_mzs: Array of m/z values.
    :type p_mzs: np.ndarray
    :param p_fwhms: Array of FWHM values corresponding to the m/z values.
    :type p_fwhms: np.ndarray
    :param p_fwhms_fit: Array of fitted FWHM values corresponding to the m/z values.
    :type p_fwhms_fit: np.ndarray
    :param ndev: Number of standard deviations to filter out outliers in the FWHM fit.
    :type ndev: int
    :param res_fun: Function to calculate the resolution based on m/z values.
    :type res_fun: partial
    :return: A Plotly figure with subplots visualizing the FWHM, chosen peak, and resolution function.
    :rtype: go.Figure
    """
    # Get residuals and standard deviation
    residuals = p_fwhms - p_fwhms_fit
    std_dev = np.std(residuals)
    is_outlier = (residuals > ndev * std_dev) | (residuals < -ndev * std_dev)

    # Remove outliers
    p_fwhms_filt = np.array(p_fwhms, dtype=np.double)[~is_outlier]
    mass = np.array(p_mzs, dtype=np.double)[~is_outlier]
    resolution = mass / p_fwhms_filt

    # Ensure data is sorted by x values
    sorted_indices = np.argsort(p_mzs)
    p_mzs_sort = p_mzs[sorted_indices]
    p_fwhms_fit_sort = p_fwhms_fit[sorted_indices]

    fig = make_subplots(
        rows=2,
        cols=2,
        specs=[[{}, {}], [{"colspan": 2}, None]],
        subplot_titles=subtitles,
    )

    # FWHM traces
    fig.add_traces(
        [
            go.Scatter(x=p_mzs, y=p_fwhms, mode="markers", name="True FWHM"),
            go.Scatter(
                x=p_mzs_sort,
                y=p_fwhms_fit_sort,
                mode="lines",
                line=dict(dash="dash"),
                name="Approximation",
            ),
            go.Scatter(
                x=np.concatenate([p_mzs_sort, p_mzs_sort[::-1]]),
                y=np.concatenate(
                    [
                        p_fwhms_fit_sort + ndev * std_dev,
                        (p_fwhms_fit_sort - ndev * std_dev)[::-1],
                    ]
                ),
                fill="toself",
                fillcolor="rgba(120, 120, 120, 0.2)",
                line=dict(color="rgba(0, 0, 0, 0)"),
                showlegend=False,
            ),
        ],
        rows=1,
        cols=1,
    )

    # Fitted resolution function traces
    mass_range = np.linspace(min(p_mzs), max(p_mzs), 100)
    fig.add_traces(
        [
            go.Scatter(
                x=mass_range,
                y=res_fun(mass_range),
                mode="lines",
                line=dict(color="red"),
                name="Fitted resolution function",
            ),
            go.Scatter(
                x=p_mzs,
                y=p_mzs / p_fwhms,
                mode="markers",
                marker=dict(color="grey"),
                name="Omitted pairs",
            ),
            go.Scatter(
                x=mass,
                y=resolution,
                mode="markers",
                marker=dict(color="black"),
                name="Used mass/resolution pairs",
            ),
        ],
        rows=2,
        cols=1,
    )

    # Chosen peak traces
    chosen_peak_trace = go.Scatter(
        x=[0], y=[0], name="Chosen peak", line=dict(color="coral")
    )
    fit_signal_trace = go.Scatter(
        x=[0],
        y=[0],
        name="Fitted signal",
        line=dict(color="steelblue", width=4, dash="dash"),
    )
    residuals_trace = go.Scatter(
        x=[0],
        y=[0],
        name="Residuals",
        fill="tozeroy",
        fillcolor="rgba(70, 130, 180, 0.5)",
        line=dict(color="rgba(0, 0, 0, 0)"),
    )
    fig.add_traces(
        [chosen_peak_trace, fit_signal_trace, residuals_trace], rows=1, cols=2
    )

    # Update layout
    fig.update_xaxes(title_text="mz", row=1, col=1)
    fig.update_yaxes(title_text="FWHM", row=1, col=1)
    fig.update_xaxes(title_text="mz", row=1, col=2)
    fig.update_yaxes(title_text="Counts", row=1, col=2)
    fig.update_xaxes(title_text="mz", row=2, col=1)
    fig.update_yaxes(title_text="Resolution", row=2, col=1)
    fig.update_layout(
        height=450, width=1000, margin=go.layout.Margin(l=30, r=30, b=30, t=30)
    )

    return fig
