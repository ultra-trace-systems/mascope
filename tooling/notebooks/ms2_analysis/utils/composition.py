import numpy as np
import pandas as pd

from mascope_tools.composition import (
    CompositionSearchConfig,
    HeuristicFilterConfig,
    assign_compositions,
)

from .config import MZ_MATCH_TOLERANCE
from .data_extractor import DataExtractor


class CompositionMap:
    def __init__(
        self,
        data: DataExtractor,
        composition_config: CompositionSearchConfig,
        heuristic_config: HeuristicFilterConfig,
    ):
        """Computes compositions for ms2 averaged peaks/centroids

        :param data: DataExtractor object containing the MS2 spectra and parent peaks
        :type data: DataExtractor
        :param composition_config: Parameters for composition assignment
        :type composition_config: CompositionSearchConfig
        :param heuristic_config: Additional heuristic parameters for composition assignment
        :type heuristic_config: HeuristicFilterConfig
        """
        self._data = data
        self._composition_config = composition_config
        self._heuristic_config = heuristic_config

        self.matches = {}
        for group in data.groups:
            mz = data.ms2_spectra[group].mz
            intensity = data.ms2_spectra[group].intensity
            ms2_peak_df = pd.DataFrame({"mz": mz, "intensity": intensity})

            assigned_peaks, _ = assign_compositions(
                ms2_peak_df,
                self._composition_config,
                self._heuristic_config,
            )

            assigned_peaks = ms2_peak_df[["mz"]].merge(
                assigned_peaks, on="mz", how="left"
            )
            placeholders = {
                "formula": "---",
                "ion": "---",
                "isotope_label": "---",
                "other_candidates": "",
            }
            assigned_peaks = assigned_peaks.fillna(placeholders)

            self.matches[group] = assigned_peaks.sort_values("mz").reset_index(
                drop=True
            )


def get_composition_label(mz: float, comp_df: pd.DataFrame) -> str:
    """Get the composition label for a given m/z based on
    the composition matches DataFrame.

    :param mz: m/z value to find the composition for
    :type mz: float
    :param comp_df: DataFrame containing composition matches.
    :type comp_df: pd.DataFrame
    :return: Composition label for a given m/z or "---" if not available.
    :rtype: str
    """
    if comp_df.empty or "ion" not in comp_df.columns:
        return "---"
    diffs = np.abs(comp_df["mz"].values - mz)
    closest = int(np.argmin(diffs))
    if diffs[closest] < MZ_MATCH_TOLERANCE:
        ion = comp_df["ion"].iloc[closest]
        if pd.notna(ion) and str(ion).strip() and str(ion).strip() != "---":
            return str(ion).strip()
    return "---"
