from pydantic import BaseModel, Field


class SampleFileProps(BaseModel):
    """Sample file properties extracted from processed files."""

    filename: str = Field(description="Name of the file being processed")

    acquisition_params: dict = Field(
        default_factory=dict,
        description=(
            "Acquisition parameters reported by the instrument, sampled from the "
            "per-scan trailer: {source, scans_sampled, constant, varying}. "
            "'constant' holds the values identical across every sampled scan "
            "(the method-level candidates); 'varying' lists the names of keys "
            "that differed, without values. Deliberately untyped and stored only "
            "in .props -- it is evidence for designing a structured "
            "acquisition-method schema, not itself that schema. Empty when the "
            "reader cannot supply it. Values are stored verbatim, so their type "
            "depends on 'source': the Thermo backend reports every trailer value "
            "as text while OpenTFRaw parses them into typed scalars. Normalise "
            "per 'source' when analysing across a mixed corpus."
        ),
    )

    interval: float = Field(
        description="Mean measurement interval in seconds, i.e. length of a spectrum in the sample."
    )

    length: float = Field(description="Length of the sample file in seconds.")

    method_file: str = Field(description="Method file name from the file.")

    mz_calibration: dict | None = Field(description="Mass calibration properties.")

    range: list = Field(description="m/z range of the sample file.")

    polarity: str = Field(description="Polarity from the file.")

    sample_interval: float | None = Field(
        description=(
            "Sample interval in nanoseconds. The interval between two consecutive samples"
            "in the time-of-flight dimension. Not to be confused with measurement interval"
            "(interval property) which is the time between two consecutive spectra in the sample (i.e."
            "chromatographic dimension). Not known for the Orbitrap files."
        )
    )

    single_ion_signal: float | None = Field(
        description=(
            "Single ion signal [mV*ns/ion]. The signal produced by a single ion in the detector."
            "Not known for the Orbitrap files."
        )
    )

    timestamp: str = Field(description="Timestamp from the file in ISO format.")

    utc_offset: float = Field(description="Timestamp UTC offset in seconds.")

    utc_offset_source: str = Field(
        description=(
            "What determined utc_offset: 'file' (an offset embedded in the "
            "raw file), 'agent' (the IANA zone the uploading machine "
            "reported), or 'guess' (the converter host's own clock, the "
            "last-resort fallback)."
        )
    )

    acquisition_timezone: str | None = Field(
        default=None,
        description=(
            "IANA timezone of the uploading machine, when the agent reported "
            "a valid one. Recorded even when an offset embedded in the file "
            "took precedence."
        ),
    )
