import numpy as np
import pandas as pd
from pyteomics.mass import calculate_mass

from mascope_tools.alignment.calibration import CentroidedSpectrum
from mascope_tools.composition.config import ELECTRON_MASS
from mascope_tools.composition.utils import parse_ionization

from .composition import CompositionMap, get_composition_label
from .config import MIN_TIC_FRACTION, MZ_MATCH_TOLERANCE
from .data_extractor import DataExtractor


# Hydrogen atom mass for proton-transfer correction
_H_MASS: float = calculate_mass(formula="H")


class ClusterClassifier:
    """Classify CI-MS2 parent peaks into fragmentation types.

    Declustering: cluster breaks, charge stays with reagent.
        MS2 dominated by parent + reagent ion.
    Proton transfer: acidic analyte is deprotonated (neg mode)
        or protonated (pos mode). MS2 shows parent + analyte-derived
        fragment at parent_mz − proton_transfer_mass.
    Undetermined - neither fragment is clearly present.

    :param data: DataExtractor object containing the MS2 spectra and parent peaks.
    :type data: DataExtractor
    :param compositions: CompositionMap object with composition assignments for MS2 peaks.
    :type compositions: CompositionMap
    :param reagent: Ionization reagent formula with charge (e.g. "+[15N]O3-").
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
        self._mechanism = parse_ionization(reagent)

        # Reagent ion m/z (what appears in MS2 when charge stays with reagent)
        self._reagent_ion_mz = self._mechanism.mass

        # Neutral reagent mass (mass of the uncharged reagent molecule).
        # mechanism.mass = composition.mass() − ELECTRON_MASS * charge
        # -> composition.mass() = mechanism.mass + ELECTRON_MASS * charge
        self._reagent_neutral_mass = (
            self._mechanism.mass + ELECTRON_MASS * self._mechanism.charge
        )

        # Proton-transfer neutral leaving-group mass:
        #   neg mode (charge=-1): leaving = HR  -> R_neutral + H
        #   pos mode (charge=+1): leaving = R-H -> R_neutral - H
        self._proton_transfer_mass = (
            self._reagent_neutral_mass - self._mechanism.charge * _H_MASS
        )

        self._classification = self._classify()

    @property
    def classification(self) -> pd.DataFrame:
        """DataFrame with one row per parent peak.

        Columns: mz, type, reagent_intensity, analyte_fragment_mz,
        analyte_fragment_intensity, parent_composition.
        """
        return self._classification

    @property
    def reagent_ion_mz(self) -> float:
        return self._reagent_ion_mz

    @property
    def reagent_neutral_mass(self) -> float:
        return self._reagent_neutral_mass

    @property
    def proton_transfer_mass(self) -> float:
        """Neutral leaving-group mass for proton-transfer fragmentation."""
        return self._proton_transfer_mass

    @property
    def reagent_ion_formula(self) -> str:
        """Reagent ion formula including charge sign (e.g. ``[15N]O3-``)."""
        sign = "+" if self._mechanism.charge > 0 else "-"
        return self._mechanism.formula + sign

    @property
    def declustering_parents(self) -> np.ndarray:
        df = self._classification
        return df.loc[df["type"] == "Declustering", "mz"].values

    @property
    def proton_transfer_parents(self) -> np.ndarray:
        df = self._classification
        return df.loc[df["type"] == "Proton transfer", "mz"].values

    @property
    def undetermined_parents(self) -> np.ndarray:
        df = self._classification
        return df.loc[df["type"] == "Undetermined", "mz"].values

    def _classify(self) -> pd.DataFrame:
        rows: list[dict] = []

        for group in self._data.groups:
            pp = group.parent_peak_mz
            ms2 = self._data.ms2_spectra[group]
            tic = self._data.ms2_tic[group]
            if ms2.mz.size == 0 or tic <= 0:
                continue

            comp_df = self._compositions.matches.get(group, pd.DataFrame())

            # --- Reagent ion intensity ---
            reagent_int = self._find_reagent_intensity(ms2, comp_df)

            # --- Analyte fragment intensity (proton transfer product) ---
            analyte_frag_mz = pp - self._proton_transfer_mass
            analyte_int = self._find_peak_intensity(ms2, analyte_frag_mz)

            # --- Parent composition label ---
            parent_comp = get_composition_label(pp, comp_df)

            # --- Classify ---
            min_intensity = tic * MIN_TIC_FRACTION
            if reagent_int < min_intensity and analyte_int < min_intensity:
                frag_type = "Undetermined"
            elif reagent_int >= analyte_int:
                frag_type = "Declustering"
            else:
                frag_type = "Proton transfer"

            rows.append(
                {
                    "group": group,
                    "mz": pp,
                    "type": frag_type,
                    "reagent_intensity": reagent_int,
                    "analyte_fragment_mz": analyte_frag_mz,
                    "analyte_fragment_intensity": analyte_int,
                    "parent_composition": parent_comp,
                }
            )

        if not rows:
            return pd.DataFrame(
                columns=[
                    "mz",
                    "type",
                    "reagent_intensity",
                    "analyte_fragment_mz",
                    "analyte_fragment_intensity",
                    "parent_composition",
                ]
            )
        return pd.DataFrame(rows)

    def _find_reagent_intensity(
        self, ms2: CentroidedSpectrum, comp_df: pd.DataFrame
    ) -> float:
        """Find the reagent ion intensity in the MS2 spectrum.

        First tries composition-based detection (formula == "()" marks a
        reagent ion in the assignment pipeline).  Falls back to m/z proximity.

        :param ms2: MS2 spectrum for this parent peak
        :type ms2: CentroidedSpectrum
        :param comp_df: DataFrame with composition matches for this parent peak
                        (columns should include "mz" and "formula")
        :type comp_df: pd.DataFrame
        :return: Intensity of the reagent ion peak, or 0 if not found.
        :rtype: float
        """
        # Composition-based: look for reagent ion marker
        if not comp_df.empty and "formula" in comp_df.columns:
            reagent_rows = comp_df[comp_df["formula"] == "()"]
            if not reagent_rows.empty:
                reagent_mz = reagent_rows.iloc[0]["mz"]
                idx = np.argmin(np.abs(ms2.mz - reagent_mz))
                if np.abs(ms2.mz[idx] - reagent_mz) < MZ_MATCH_TOLERANCE:
                    return float(ms2.intensity[idx])

        # Fallback: match by reagent ion m/z
        return self._find_peak_intensity(ms2, self._reagent_ion_mz)

    @staticmethod
    def _find_peak_intensity(ms2: CentroidedSpectrum, target_mz: float) -> float:
        """Return intensity of the closest peak to *target_mz*, or 0."""
        if ms2.mz.size == 0:
            return 0.0
        idx = int(np.argmin(np.abs(ms2.mz - target_mz)))
        if np.abs(ms2.mz[idx] - target_mz) < MZ_MATCH_TOLERANCE:
            return float(ms2.intensity[idx])
        return 0.0
