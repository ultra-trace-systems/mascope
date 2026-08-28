from pydantic import BaseModel, Field, field_validator, model_validator

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel
from mascope_backend.api.models.calibration.config import calibration_config


class GetMzCalibrationQueryParams(QueryParamsModel):
    sample_item_id: str | None = Field(
        None,
        description="Sample item ID to fetch m/z calibration for.",
    )
    instrument: str | None = Field(
        None,
        description="Instrument name to query for its last m/z calibration.",
    )

    @model_validator(mode="after")
    def check_sample_item_id_or_instrument(self) -> "GetMzCalibrationQueryParams":
        """Validate exactly one of sample_item_id or instrument is provided."""
        if not self.sample_item_id and not self.instrument:
            raise ValueError(
                "Specify a sample item ID or an instrument name "
                "to search for m/z calibration."
            )
        if self.sample_item_id and self.instrument:
            raise ValueError(
                "Specify only one: either a sample item ID "
                "or an instrument name, not both."
            )
        return self


class MzCalibrationParams(BaseModel):
    refine_window: int = Field(
        ...,
        description=(
            "Maximum allowed m/z difference (ppm window) for considering "
            "a peak as a potential calibration match."
        ),
    )
    mz_error_tolerance: float | None = Field(
        None,
        description=(
            "Maximum allowed mean m/z error after calibration "
            "for the calibration to be accepted."
        ),
    )
    snr_threshold: float | None = Field(
        None,
        description="Minimum signal-to-noise ratio for calibration peaks.",
    )
    match_score_min: float = Field(
        calibration_config.DEFAULT_MATCH_SCORE_MIN,
        description=(
            "Minimum match score for a peak to be considered a valid calibration match."
        ),
    )
    peak_intensity_min: float = Field(
        calibration_config.DEFAULT_PEAK_INTENSITY_MIN,
        description="Minimum intensity for peaks to be considered in calibration.",
    )
    isotope_abundance_min: float = Field(
        calibration_config.DEFAULT_ISOTOPE_ABUNDANCE_MIN,
        description=(
            "Minimum relative abundance required for an isotope peak "
            "to be considered in calibration."
        ),
    )

    def with_defaults(self, defaults: "MzCalibrationParams") -> "MzCalibrationParams":
        """Fill missing parameters from another MzCalibrationParams instance.

        The use case: automatic processing pipeline, where the params below
        are not provided by the user, but we want to fill them in with sensible
        defaults.

        Steps:
        - For each nullable field, keep self value if set, else use default

        :param defaults: Fallback parameter values.
        :type defaults: MzCalibrationParams
        :return: New instance with missing values filled from defaults.
        :rtype: MzCalibrationParams
        """
        return self.model_copy(
            update={
                "refine_window": (
                    self.refine_window
                    if self.refine_window is not None
                    else defaults.refine_window
                ),
                "mz_error_tolerance": (
                    self.mz_error_tolerance
                    if self.mz_error_tolerance is not None
                    else defaults.mz_error_tolerance
                ),
                "snr_threshold": (
                    self.snr_threshold
                    if self.snr_threshold is not None
                    else defaults.snr_threshold
                ),
            }
        )


class OrbiCalibrationParams(MzCalibrationParams):
    mz_error_tolerance: float = Field(calibration_config.ORBI_MZ_ERROR_TOLERANCE)
    refine_window: int = Field(calibration_config.ORBI_DEFAULT_REFINE_WINDOW)
    snr_threshold: float = Field(calibration_config.ORBI_SNR_THRESHOLD)


class TofCalibrationParams(MzCalibrationParams):
    mz_error_tolerance: float = Field(calibration_config.TOF_MZ_ERROR_TOLERANCE)
    refine_window: int = Field(calibration_config.TOF_DEFAULT_REFINE_WINDOW)
    snr_threshold: float = Field(calibration_config.TOF_SNR_THRESHOLD)


class CalibrationFitParams(MzCalibrationParams):
    calibration_collection_id: str | None = Field(
        None, description="Calibration collection ID"
    )
    ionization_mechanism_ids: list[str] | None = Field(
        None, description="Ionization mechanism IDs."
    )
    polarity: str | None = Field(None, description="Polarity of the ionization mode")


class CalibrationMzApplyBody(BaseModel):
    fit: dict = Field(..., description="Fit parameters")


#: Longest skip reason accepted. The label is rendered inside a badge tooltip,
#: so a reason long enough to need scrolling would not be read anyway; the cap
#: also keeps the JSON column from collecting pasted log dumps.
CALIBRATION_SKIP_REASON_MAX_LENGTH = 200


class CalibrationSkipBody(BaseModel):
    reason: str = Field(
        ...,
        min_length=1,
        max_length=CALIBRATION_SKIP_REASON_MAX_LENGTH,
        description=(
            "Why calibration is not being applied to this file. Required: the "
            "point of the marker is that it is attributable and explained, so "
            "an empty label would leave exactly the ambiguity it replaces."
        ),
    )

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        """Trim the label and refuse one that is only whitespace.

        ``min_length`` runs against the submitted string, so a reason of spaces
        alone passes it and would be stored as an empty label.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("Provide a reason for skipping the m/z calibration.")
        return stripped
