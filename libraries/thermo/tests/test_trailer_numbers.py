"""The numbers a scan's trailer stores (``ReaderBackend.trailer_numbers``).

The Thermo library gives a trailer as text, rounded to the digits it
displays, and a number as stored through ``GetTrailerExtraValue``; OpenTFRaw
gives the stored values in the trailer itself. Scripted readers drive both
here, so this runs without the Thermo DLLs; the committed sample files pin
the two against each other in ``test_backend_parity.py``, and in every .NET
culture in ``test_dotnet_culture.py``. Text a reader already gave, as a
stream's ``acquisition_params`` holds it, is read by ``trailer_float``.
"""

import math
from types import SimpleNamespace

import numpy as np
import pytest

from mascope_thermo.backend import (
    MS2_ISOLATION_WIDTH,
    OpenTFRawBackend,
    ThermoBackend,
    _one_isolation_width,
    trailer_float,
)


B = "Conversion Parameter B:"
C = "Conversion Parameter C:"
HCD = "HCD Energy V:"


class _ScriptedThermoRaw:
    """The slice of the Thermo ``RawFileReaderAdapter`` trailer_numbers reads.

    ``fields`` are ``(label, numeric)`` pairs, the trailer's header; ``stored``
    maps a scan number to the value of each field, in the header's order.
    """

    def __init__(self, fields, stored):
        self._fields = fields
        self._stored = stored
        self.header_reads = 0
        self.value_reads = []

    def GetTrailerExtraHeaderInformation(self):  # noqa: N802
        self.header_reads += 1
        return [
            SimpleNamespace(Label=label, IsNumeric=numeric)
            for label, numeric in self._fields
        ]

    def GetTrailerExtraValue(self, scan_number, index):  # noqa: N802
        self.value_reads.append((scan_number, index))
        return self._stored[scan_number][index]


def _thermo(fields, stored):
    reader = ThermoBackend("unused.raw")
    reader._raw = _ScriptedThermoRaw(fields, stored)
    return reader


# A header as the negative-mode sample file's begins it, cut down: text,
# a switch, integers, a section heading and the conversion parameters.
FIELDS = [
    ("Scan Description:", False),
    ("AGC:", False),
    ("Micro Scan Count:", True),
    ("=== Mass Calibration: ===:", False),
    (B, True),
    (C, True),
]
STORED = {1: ["", True, 10, None, 169409639.6525657, -71050473.73203687]}


def test_thermo_gives_the_stored_numbers_and_only_those():
    reader = _thermo(FIELDS, STORED)

    numbers = reader.trailer_numbers(1, [label for label, _numeric in FIELDS])

    assert numbers == {
        "Micro Scan Count:": 10.0,
        B: 169409639.6525657,
        C: -71050473.73203687,
    }
    assert all(type(number) is float for number in numbers.values())
    # Only a field that stores a number is read.
    assert reader._raw.value_reads == [(1, 2), (1, 4), (1, 5)]


def test_thermo_reads_the_labels_asked_for_and_leaves_out_one_it_lacks():
    reader = _thermo(FIELDS, STORED)

    assert reader.trailer_numbers(1, [C, "Not In The Trailer:"]) == {
        C: -71050473.73203687
    }
    assert reader._raw.value_reads == [(1, 5)]


def test_thermo_reads_the_numbers_whatever_trailer_is_given():
    # A trailer the caller read is the library's text, rounded.
    reader = _thermo(FIELDS, STORED)

    numbers = reader.trailer_numbers(1, [C], {C: "-71050473.7320"})

    assert numbers == {C: -71050473.73203687}


def test_thermo_reads_the_trailers_fields_once_per_open_file():
    reader = _thermo(FIELDS, {**STORED, 2: STORED[1]})

    reader.trailer_numbers(1, [B])
    reader.trailer_numbers(2, [B, C])
    assert reader._raw.header_reads == 1

    # Another file opened by the same reader has its own fields.
    reader.__exit__(None, None, None)
    reader._raw = _ScriptedThermoRaw([(B, True)], {1: [1.5]})
    assert reader.trailer_numbers(1, [B, C]) == {B: 1.5}


def test_thermo_reads_a_repeated_label_at_its_last_field():
    # As scan_trailer's dict keeps the last value under it.
    reader = _thermo([(B, True), (B, True)], {1: [1.0, 2.0]})

    assert reader.trailer_numbers(1, [B]) == {B: 2.0}


@pytest.mark.parametrize("stored", [math.nan, math.inf, -math.inf])
def test_thermo_leaves_out_a_number_that_is_not_finite(stored):
    reader = _thermo([(B, True), (C, True)], {1: [stored, 4.0]})

    assert reader.trailer_numbers(1, [B, C]) == {C: 4.0}


class _ScriptedOpenTFRaw:
    """The slice of ``opentfraw.RawFile`` trailer_numbers reads."""

    def __init__(self, trailers):
        self._trailers = trailers
        self.trailer_reads = []

    def scan_parameters(self, scan_number):
        self.trailer_reads.append(scan_number)
        return self._trailers.get(scan_number)


def _opentfraw(trailers):
    reader = OpenTFRawBackend("unused.raw")
    reader._raw = _ScriptedOpenTFRaw(trailers)
    return reader


# The same fields as OpenTFRaw types them, and a 32-bit float such as a
# FLOAT field gives.
TYPED = {
    "Scan Description:": "",
    "AGC:": True,
    "Micro Scan Count:": 10,
    "=== Mass Calibration: ===:": None,
    B: 169409639.6525657,
    C: -71050473.73203687,
    "Ion Injection Time (ms):": np.float32(0.25),
    "Lock Mass Correction (ppm):": math.nan,
    "Text That Reads As A Number:": "4.25",
}


def test_opentfraw_gives_the_numbers_its_trailer_holds_and_only_those():
    reader = _opentfraw({1: TYPED})

    numbers = reader.trailer_numbers(1, list(TYPED))

    assert numbers == {
        "Micro Scan Count:": 10.0,
        B: 169409639.6525657,
        C: -71050473.73203687,
        "Ion Injection Time (ms):": 0.25,
    }
    assert all(type(number) is float for number in numbers.values())


def test_opentfraw_takes_the_numbers_from_a_trailer_already_read():
    reader = _opentfraw({1: TYPED})
    trailer = reader.scan_trailer(1)

    assert reader.trailer_numbers(1, [B], trailer) == {B: 169409639.6525657}
    assert reader._raw.trailer_reads == [1]


def test_opentfraw_gives_no_numbers_for_a_scan_without_a_trailer():
    assert _opentfraw({}).trailer_numbers(1, [B, C]) == {}


# ---- the MS2 isolation width ----


def test_widths_stored_alike_are_one_width():
    # A 32-bit float's 64-bit copy carries noise below the sixth decimal.
    assert _one_isolation_width([float(np.float32(1.6)), 1.6, 1.6]) == 1.6


@pytest.mark.parametrize(
    ("widths", "message"),
    [
        pytest.param([1.6, 2.0], "Multiple isolation widths", id="two"),
        pytest.param([], "No MS2 scan stores an isolation width", id="none"),
    ],
)
def test_anything_but_one_width_is_refused_saying_which(widths, message):
    with pytest.raises(ValueError, match=message):
        _one_isolation_width(widths)


def _thermo_ms2(trailers):
    """A Thermo backend over MS2 scans 3, 4, ... with these typed trailers,
    the text table formatted from them as the library does."""
    labels = list(trailers[0])
    reader = _thermo(
        [
            (label, isinstance(value, (int, float)) and not isinstance(value, bool))
            for label, value in trailers[0].items()
        ],
        {
            scan: [trailer[label] for label in labels]
            for scan, trailer in enumerate(trailers, start=3)
        },
    )
    reader.scan_acquisition_settings = lambda *_args, **_kwargs: {
        "header_labels": labels,
        "settings": {
            scan: [
                "" if trailer[label] is None else str(trailer[label])
                for label in labels
            ]
            for scan, trailer in enumerate(trailers, start=3)
        },
    }
    return reader


def _opentfraw_ms2(trailers):
    """An OpenTFRaw backend over MS2 scans 3, 4, ... with these trailers."""
    reader = _opentfraw(dict(enumerate(trailers, start=3)))
    reader._selected = lambda *_args, **_kwargs: [
        {"scan_number": scan} for scan in range(3, 3 + len(trailers))
    ]
    return reader


@pytest.fixture(params=[_thermo_ms2, _opentfraw_ms2], ids=["thermo", "opentfraw"])
def ms2_reader(request):
    """Build a reader backend over scripted MS2 trailers, once per backend."""
    return request.param


def test_a_width_held_as_text_is_no_width(ms2_reader):
    # Both read only the fields the file types as numbers, and a reader
    # gives a field typed as text as text.
    reader = ms2_reader([{MS2_ISOLATION_WIDTH: "1.6", HCD: "25"}] * 2)

    with pytest.raises(ValueError, match="No MS2 scan stores an isolation width"):
        reader.ms2_acquisition_info()


def test_thermo_gives_the_isolation_width_it_stores_and_the_energy_it_wrote():
    reader = _thermo(
        [(MS2_ISOLATION_WIDTH, True), (HCD, False)],
        {3: [1.234567, "25"], 4: [1.234567, "25,30"]},
    )
    # The trailer's text, as the library formats it.
    reader.scan_acquisition_settings = lambda *_args, **_kwargs: {
        "header_labels": [MS2_ISOLATION_WIDTH, HCD],
        "settings": {3: ["1.23", "25"], 4: ["1.23", "25,30"]},
    }

    assert reader.ms2_acquisition_info() == (1.234567, {3: "25", 4: "25,30"})


# ---- text a reader already gave ----


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(" 4.25 ", 4.25, id="decimal-point"),
        # Captured on a server in its own culture before load_dotnet pinned
        # the invariant one: a decimal comma (fi-FI, de-DE), and where .NET
        # takes the culture from ICU the minus sign U+2212 (sv-SE, nb-NO), in
        # an exponent too.
        pytest.param(" 4,25 ", 4.25, id="decimal-comma"),
        pytest.param("-4,25", -4.25, id="signed"),
        pytest.param("4,25E+00", 4.25, id="exponent"),
        pytest.param("−4,25", -4.25, id="minus-sign"),
        pytest.param("4,25E−001", 0.425, id="minus-sign-exponent"),
        pytest.param(4.25, 4.25, id="a-number"),
        pytest.param("n/a", None, id="not-a-number"),
        pytest.param("NaN", None, id="not-finite"),
        pytest.param(True, None, id="a-switch"),
        pytest.param(None, None, id="none"),
    ],
)
def test_text_a_reader_gave_is_read_as_a_number(text, expected):
    assert trailer_float(text) == expected
