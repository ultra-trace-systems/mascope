"""The mzML export (``mascope_thermo.mzml``).

The A/B the export rests on: every spectrum of the file it writes is read back
with the standard library and held to the reader's own centroids for the same
scan, through each backend available, so the peaks a third party reads are the
peaks Mascope calibrates and assigns from. The window, the streaming pass and the
instrument it writes are tested on the committed files (an Orbitrap Exploris
120, two MS1 scans each, at about 8 s and 16 s) and on hand-made writer output
for the layouts they refuse.
"""

import base64
import io
import os
import xml.etree.ElementTree as ET
import zlib

import numpy as np
import pytest
from thermo_test_support import NEG_ORBI_FILE_PATH, POS_ORBI_FILE_PATH

from mascope_thermo.backend import open_backend
from mascope_thermo.mzml import (
    CvParam,
    InstrumentConfiguration,
    MzMLLayoutError,
    _filter,
    write_mzml,
)
from mascope_thermo.thermo import NoScansFoundError


NS = {"m": "http://psi.hupo.org/ms/mzml"}
FILES = [POS_ORBI_FILE_PATH, NEG_ORBI_FILE_PATH]


def _accessions(element) -> set[str]:
    return {p.get("accession") for p in element.findall("m:cvParam", NS)}


def _array(binary_array) -> np.ndarray:
    accessions = _accessions(binary_array)
    dtype = np.float64 if "MS:1000523" in accessions else np.float32
    raw = base64.b64decode(binary_array.find("m:binary", NS).text or "")
    if "MS:1000574" in accessions:
        raw = zlib.decompress(raw)
    return np.frombuffer(raw, dtype=dtype)


def _spectra(path) -> list[dict]:
    """Each spectrum of an mzML file: its scan, index, terms and arrays."""
    root = ET.parse(path).getroot()
    spectrum_list = root.find("m:run/m:spectrumList", NS)
    spectra = []
    for spectrum in spectrum_list.findall("m:spectrum", NS):
        arrays = {}
        for binary_array in spectrum.findall(
            "m:binaryDataArrayList/m:binaryDataArray", NS
        ):
            accessions = _accessions(binary_array)
            key = "mz" if "MS:1000514" in accessions else "intensity"
            arrays[key] = _array(binary_array)
        spectra.append(
            {
                "scan": int(spectrum.get("id").rsplit("scan=", 1)[1]),
                "index": int(spectrum.get("index")),
                "terms": _accessions(spectrum),
                **arrays,
            }
        )
    assert int(spectrum_list.get("count")) == len(spectra)
    return spectra


@pytest.mark.parametrize("path", FILES)
def test_every_spectrum_is_the_readers_centroids(backend, path, tmp_path):
    out = tmp_path / "out.mzML"
    assert write_mzml(path, str(out)) == 2

    with open_backend(path) as reader:
        centroids = reader.centroids_per_scan(ms_type=None)
    spectra = _spectra(out)

    assert [s["scan"] for s in spectra] == [c["scan"] for c in centroids]
    for spectrum, scan in zip(spectra, centroids):
        np.testing.assert_array_equal(spectrum["mz"], scan["masses"])
        # The writer stores intensities as 32-bit floats.
        np.testing.assert_array_equal(
            spectrum["intensity"], np.float32(scan["intensities"])
        )


@pytest.mark.parametrize("path", FILES)
def test_a_spectrum_is_labelled_centroid_not_profile(path, tmp_path):
    out = tmp_path / "out.mzML"
    write_mzml(path, str(out))

    for spectrum in _spectra(out):
        assert "MS:1000127" in spectrum["terms"]
        assert "MS:1000128" not in spectrum["terms"]


def test_a_whole_file_keeps_its_chromatograms(tmp_path):
    out = tmp_path / "out.mzML"
    write_mzml(POS_ORBI_FILE_PATH, str(out))

    root = ET.parse(out).getroot()
    chromatograms = root.findall("m:run/m:chromatogramList/m:chromatogram", NS)
    assert [c.get("id") for c in chromatograms] == ["TIC", "BPC"]


def test_a_window_keeps_only_its_scans_reindexed(tmp_path):
    out = tmp_path / "out.mzML"
    assert write_mzml(POS_ORBI_FILE_PATH, str(out), t_min=12.0, t_max=20.0) == 1

    spectra = _spectra(out)
    assert [(s["scan"], s["index"]) for s in spectra] == [(2, 0)]
    with open_backend(POS_ORBI_FILE_PATH) as reader:
        (second,) = reader.centroids_per_scan(t_min=12.0, t_max=20.0, ms_type=None)
    np.testing.assert_array_equal(spectra[0]["mz"], second["masses"])


def test_a_window_is_inclusive_at_both_ends(tmp_path):
    with open_backend(POS_ORBI_FILE_PATH) as reader:
        first, second = reader.scan_times(ms_type=None)
    out = tmp_path / "out.mzML"

    assert write_mzml(POS_ORBI_FILE_PATH, str(out), t_min=first, t_max=second) == 2


def test_a_window_leaves_out_the_whole_file_chromatograms(tmp_path):
    out = tmp_path / "out.mzML"
    write_mzml(POS_ORBI_FILE_PATH, str(out), t_max=12.0)

    root = ET.parse(out).getroot()
    assert root.find("m:run/m:chromatogramList", NS) is None
    assert [s["scan"] for s in _spectra(out)] == [1]


def test_a_polarity_alone_is_a_window(tmp_path):
    out = tmp_path / "out.mzML"
    assert write_mzml(POS_ORBI_FILE_PATH, str(out), polarity="+") == 2

    root = ET.parse(out).getroot()
    assert root.find("m:run/m:chromatogramList", NS) is None


def test_a_window_asks_for_fragmentation_scans_too(tmp_path, monkeypatch):
    """A sample's file holds its MS2 scans beside its MS1 ones. The committed
    files have none, so the reader is asked for what the window selects."""
    import mascope_thermo.mzml as m_mzml

    asked = {}

    class Reader:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def scan_indices(self, **kwargs):
            asked.update(kwargs)
            return [1, 2]

    monkeypatch.setattr(m_mzml, "open_backend", lambda _path: Reader())
    write_mzml(POS_ORBI_FILE_PATH, str(tmp_path / "out.mzML"), "+", 1.0, 20.0)

    assert asked == {"polarity": "+", "t_min": 1.0, "t_max": 20.0, "ms_type": None}


def test_an_empty_window_writes_nothing(tmp_path):
    out = tmp_path / "out.mzML"
    with pytest.raises(NoScansFoundError):
        write_mzml(POS_ORBI_FILE_PATH, str(out), polarity="-")

    assert os.listdir(tmp_path) == []


def test_a_failed_write_leaves_no_file(tmp_path, monkeypatch):
    import mascope_thermo.mzml as m_mzml

    def broken(_source, _sink, _keep, **_kwargs):
        raise MzMLLayoutError("broken")

    monkeypatch.setattr(m_mzml, "_filter", broken)
    with pytest.raises(MzMLLayoutError):
        write_mzml(POS_ORBI_FILE_PATH, str(tmp_path / "out.mzML"))

    assert os.listdir(tmp_path) == []


def test_two_writes_of_one_output_do_not_share_a_staging_file(tmp_path, monkeypatch):
    """A second write of the same output, started while the first one is
    writing, leaves both whole: neither truncates nor publishes the other's
    staging file."""
    import mascope_thermo.mzml as m_mzml

    out = tmp_path / "out.mzML"
    real_filter = m_mzml._filter
    nested = []

    def filter_then_write_again(source, sink, keep, **kwargs):
        if not nested:
            nested.append(None)
            nested[0] = write_mzml(POS_ORBI_FILE_PATH, str(out), t_max=12.0)
        return real_filter(source, sink, keep, **kwargs)

    monkeypatch.setattr(m_mzml, "_filter", filter_then_write_again)
    written = write_mzml(POS_ORBI_FILE_PATH, str(out))

    assert nested == [1]
    assert written == 2
    assert [s["scan"] for s in _spectra(out)] == [1, 2]
    assert os.listdir(tmp_path) == ["out.mzML"]


EXPLORIS_120 = InstrumentConfiguration(
    model=CvParam("MS:1003095", "Orbitrap Exploris 120"),
    source=CvParam("MS:1000071", "chemical ionization"),
    analyser=CvParam("MS:1000484", "orbitrap"),
    detector=CvParam("MS:1000624", "inductive detector"),
)


def _terms(element) -> list[tuple[str, str]]:
    return [
        (p.get("accession"), p.get("name")) for p in element.findall("m:cvParam", NS)
    ]


def _configurations(path) -> dict[str, dict]:
    """Each instrument configuration of an mzML file, by its id: its own
    terms and its components' (tag, order, terms)."""
    root = ET.parse(path).getroot()
    configuration_list = root.find("m:instrumentConfigurationList", NS)
    configurations = {}
    for configuration in configuration_list.findall("m:instrumentConfiguration", NS):
        component_list = configuration.find("m:componentList", NS)
        components = None
        if component_list is not None:
            components = [
                (child.tag.split("}")[1], child.get("order"), _terms(child))
                for child in component_list
            ]
            assert int(component_list.get("count")) == len(components)
        configurations[configuration.get("id")] = {
            "terms": _terms(configuration),
            "components": components,
        }
    assert int(configuration_list.get("count")) == len(configurations)
    return configurations


@pytest.mark.parametrize("window", [{}, {"t_min": 12.0}])
def test_the_instrument_is_the_callers_terms(tmp_path, window):
    out = tmp_path / "out.mzML"
    write_mzml(POS_ORBI_FILE_PATH, str(out), instrument=EXPLORIS_120, **window)

    configurations = _configurations(out)
    expected = {
        "terms": [("MS:1003095", "Orbitrap Exploris 120")],
        "components": [
            ("source", "1", [("MS:1000071", "chemical ionization")]),
            ("analyzer", "2", [("MS:1000484", "orbitrap")]),
            ("detector", "3", [("MS:1000624", "inductive detector")]),
        ],
    }
    assert configurations and all(c == expected for c in configurations.values())

    # The run and every scan still point at a configuration the file declares.
    root = ET.parse(out).getroot()
    refs = {root.find("m:run", NS).get("defaultInstrumentConfigurationRef")}
    refs |= {
        scan.get("instrumentConfigurationRef")
        for scan in root.iter("{http://psi.hupo.org/ms/mzml}scan")
    }
    assert refs - {None} <= set(configurations)
    assert None not in refs


def test_without_an_instrument_the_writers_configurations_stay(tmp_path):
    out = tmp_path / "out.mzML"
    write_mzml(POS_ORBI_FILE_PATH, str(out))

    configurations = _configurations(out)
    assert all(c["components"] is None for c in configurations.values())


def test_an_instrument_short_of_a_component_has_no_component_list(tmp_path):
    out = tmp_path / "out.mzML"
    instrument = EXPLORIS_120._replace(detector=None)
    write_mzml(POS_ORBI_FILE_PATH, str(out), instrument=instrument)

    for configuration in _configurations(out).values():
        assert configuration == {
            "terms": [("MS:1003095", "Orbitrap Exploris 120")],
            "components": None,
        }


WRITER_OUTPUT = """\
<run>
  <spectrumList count="2">
    <spectrum id="controllerType=0 controllerNumber=1 scan=1" index="0">
      <cvParam accession="MS:1000128" name="profile spectrum"/>
    </spectrum>
    <spectrum id="controllerType=0 controllerNumber=1 scan=2" index="1">
      <cvParam accession="MS:1000128" name="profile spectrum"/>
    </spectrum>
  </spectrumList>
  <chromatogramList count="1">
    <chromatogram id="TIC" index="0"/>
  </chromatogramList>
</run>
"""


def _filtered(text: str, keep) -> tuple[str, int]:
    sink = io.StringIO()
    written = _filter(io.StringIO(text), sink, keep)
    return sink.getvalue(), written


def test_the_pass_keeps_the_listed_scans_and_recounts():
    text, written = _filtered(WRITER_OUTPUT, {2})

    assert written == 1
    assert '<spectrumList count="1">' in text
    assert 'scan=1"' not in text
    assert 'scan=2" index="0"' in text
    assert "chromatogram" not in text


def test_the_pass_refuses_a_spectrum_without_a_scan_number():
    text = WRITER_OUTPUT.replace("scan=1", "frame=1")
    with pytest.raises(MzMLLayoutError, match="without a scan number"):
        _filtered(text, None)


def test_the_pass_refuses_a_count_it_did_not_write():
    with pytest.raises(MzMLLayoutError, match="1 spectra written where 2"):
        _filtered(WRITER_OUTPUT, {2, 3})
    text = WRITER_OUTPUT.replace('count="2"', 'count="3"')
    with pytest.raises(MzMLLayoutError, match="2 spectra written where 3"):
        _filtered(text, None)


def test_the_pass_refuses_output_without_a_spectrum_list():
    with pytest.raises(MzMLLayoutError, match="no spectrum list"):
        _filtered("<run>\n</run>\n", None)


CONFIGURED_OUTPUT = (
    """\
  <instrumentConfigurationList count="2">
    <instrumentConfiguration id="IC1">
      <cvParam cvRef="MS" accession="MS:1003199" name="Orbitrap Exploris 120"/>
    </instrumentConfiguration>
    <instrumentConfiguration id="IC2">
      <cvParam cvRef="MS" accession="MS:1003199" name="Orbitrap Exploris 120"/>
      <cvParam cvRef="MS" accession="MS:1000484" name="orbitrap"/>
    </instrumentConfiguration>
  </instrumentConfigurationList>
"""
    + WRITER_OUTPUT
)


def test_the_pass_replaces_every_configuration_under_its_id():
    text, _written = _filtered_with(CONFIGURED_OUTPUT, EXPLORIS_120)

    assert "MS:1003199" not in text
    assert '<instrumentConfigurationList count="2">' in text
    assert text.count('<instrumentConfiguration id="IC1">') == 1
    assert text.count('<instrumentConfiguration id="IC2">') == 1
    assert text.count('accession="MS:1003095"') == 2


def test_the_pass_quotes_the_terms_it_writes():
    instrument = InstrumentConfiguration(CvParam("MS:0000000", 'A & "B" <C>'))
    text, _written = _filtered_with(CONFIGURED_OUTPUT, instrument)

    root = ET.fromstring(f'<mzML xmlns="{NS["m"]}">{text}</mzML>')
    names = {p.get("name") for p in root.iter(f"{{{NS['m']}}}cvParam")}
    assert 'A & "B" <C>' in names


def test_the_pass_refuses_output_without_a_configuration_list():
    with pytest.raises(MzMLLayoutError, match="no instrument configuration list"):
        _filtered_with(WRITER_OUTPUT, EXPLORIS_120)


def test_the_pass_refuses_an_empty_configuration_list():
    text = (
        '  <instrumentConfigurationList count="0">\n'
        "  </instrumentConfigurationList>\n" + WRITER_OUTPUT
    )
    with pytest.raises(MzMLLayoutError, match="list is empty"):
        _filtered_with(text, EXPLORIS_120)


def test_the_pass_refuses_a_configuration_list_on_one_line():
    text = (
        '  <instrumentConfigurationList count="0"></instrumentConfigurationList>\n'
        + WRITER_OUTPUT
    )
    with pytest.raises(MzMLLayoutError, match="on one line"):
        _filtered_with(text, EXPLORIS_120)


def _filtered_with(text: str, instrument) -> tuple[str, int]:
    sink = io.StringIO()
    written = _filter(io.StringIO(text), sink, None, instrument=instrument)
    return sink.getvalue(), written
