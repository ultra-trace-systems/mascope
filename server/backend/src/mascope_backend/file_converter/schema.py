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
            "per 'source' when analysing across a mixed corpus. The scans are "
            "sampled from every MS1 scan, so for a file with more than one scan "
            "stream the summary mixes them; scan_streams holds one per stream."
        ),
    )

    scan_streams: list[dict] = Field(
        default_factory=list,
        description=(
            "The file's scan streams: its scans grouped into the experiments of "
            "its acquisition method. An MS1 scan belongs to the experiment its "
            "method segment and scan event name; a file that records no event, "
            "and every MSn scan, is grouped by scan signature alone. The "
            "signature is the fields of the scan filter that say what a scan "
            "measured (analyzer, polarity, data type, source, source "
            "fragmentation, FAIMS CV, scan mode, MS order, targeted precursors, "
            "scan ranges) plus the FT resolution. Per stream: signature_key "
            "(the signature as text), key (the stream's name in this file: the "
            "signature_key, closed by 'event=N' - 'segment=S event=N' outside "
            "the method's first segment - where two experiments of the file "
            "share it, so unique within the file and not stable across "
            "files), signature, scan_segment and scan_event (MS1 streams only; "
            "counted from 1; null where the file records none), scans, blocks, "
            "t_first and t_last [s], filters, and acquisition_params sampled "
            "from its own scans (MS1 streams only; an MSn stream carries {}). "
            "A stream's identity across files is signature_key with "
            "scan_segment and scan_event. A file converted before streams "
            "followed the experiment carries none of those three and one "
            "stream per signature, whose key is its signature. A census "
            "stored only in .props: processing still pools every "
            "MS1 scan of a polarity. Empty for TofDaq files, for files converted "
            "before the census existed, and when the reader cannot supply it."
        ),
    )

    axis_fingerprint: dict = Field(
        default_factory=dict,
        description=(
            "The axis fingerprint of a raw Orbitrap file: the m/z offset its "
            "mass calibration stored in each frequency band of the Orbitrap "
            "axis, read back from the stored profile of at most eight scans "
            "per acquisition mode (mascope_thermo.axis_fingerprint). A JSON "
            "document under the schema mascope.axis_fingerprint, whose "
            "'status' says what it holds: 'ok' or 'no_fingerprint' (the whole "
            "document: per mode, its frequency bands, the grid of their edges, "
            "the lock mass each scan used and the Mascope stream_keys of its "
            "scans; for the file, the census of every scan), 'unavailable' "
            "(the reader cannot read the stored points) or 'error' (the "
            "computation failed; 'error' says how). 'version' and "
            "'extractor.params' say what computed it, so a document computed "
            "otherwise can be told apart and computed again. A measurement "
            "stored only in .props: Mascope never alters an m/z with it. "
            "Empty for TofDaq files, for files converted before it existed or "
            "with [backend] axis_fingerprint = 'off', and for a file with no "
            "scans."
        ),
    )

    ms2_store: dict = Field(
        default_factory=dict,
        description=(
            "The file's MS2 store (mascope_signal.ms2_store), 'ms2.zarr' in its "
            "sample directory: its fragmentation scans read once, which the MS2 "
            "routes read instead of the raw file. What it is: 'layout' and "
            "'version' (a store of another is not read), "
            "'parent_peak_tolerance' (the tolerance [Da] its groups were made "
            "at), 'ppm' (the bin its averages were made in), 'reader' (the "
            "reader backend that read the raw file) and 'groups', each "
            "(polarity, parent peak, activation) group's 'polarity', 'key' "
            "('137.0960@hcd25.00') and 'scan_count'. Written after the rest of "
            "the props, once the file is in the filestore, for a file whose "
            "scan_streams census holds a fragmentation stream, and again when "
            "the file is re-processed. Empty for any other file, for a file "
            "converted before the store existed, and for one whose store could "
            "not be written: their MS2 routes read the raw file."
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
            "raw file), 'agent' (the IANA zone the uploading agent reported "
            "for the instrument PC), 'browser' (the zone of the browser a "
            "web upload came from, a weaker hint, since that machine may "
            "stand anywhere), or 'guess' (the converter host's own clock, "
            "the last-resort fallback)."
        )
    )

    acquisition_timezone: str | None = Field(
        default=None,
        description=(
            "The IANA zone the upload reported, when it was a valid one: the "
            "agent's for the instrument PC, else the browser's for a web "
            "upload. Recorded even when an offset embedded in the file took "
            "precedence; utc_offset_source says what decided the offset."
        ),
    )

    instrument_type: str = Field(
        description=(
            "The instrument class, 'orbi' or 'tof', decided by the reader that "
            "converted the file. Recorded here and on the sample_file row so "
            "an instrument's name need not say which class it is."
        ),
    )
