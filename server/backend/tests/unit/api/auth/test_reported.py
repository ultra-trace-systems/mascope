"""What a paired agent reports about itself, and how it is cleaned.

An instrument name and an agent release reach the server by three routes -
the pairing request, the X-Agent-Version header and an upload's tus metadata.
The cleaning is shared so the routes cannot disagree; these tests pin what it
does, because the value is free text from a machine and it is displayed.
"""

from mascope_backend.api.new.auth.access_token.validation import (
    agent_version_from_header,
)
from mascope_backend.api.new.auth.reported import (
    AGENT_VERSION_MAX_LENGTH,
    INSTRUMENT_NAME_RE,
    clean_reported_text,
)


def test_an_absent_or_empty_value_is_nothing():
    assert agent_version_from_header(None) is None
    assert agent_version_from_header("") is None
    assert agent_version_from_header("   ") is None
    # Control characters alone leave nothing behind either.
    assert agent_version_from_header("\x00\x1f\x7f") is None


def test_a_version_is_cut_to_the_column_it_is_written_to():
    # AgentDevice.last_seen_version is String(32). Cut rather than refused:
    # the same value arrives in a pairing request, and refusing that would
    # leave the machine unable to pair at all.
    long_version = "v2026.09.01-9b9e54d-394-g7e674438e"
    assert len(long_version) > AGENT_VERSION_MAX_LENGTH
    cleaned = agent_version_from_header(long_version)
    assert cleaned == long_version[:AGENT_VERSION_MAX_LENGTH]
    assert len(cleaned) == AGENT_VERSION_MAX_LENGTH


def test_characters_that_would_mislead_the_reader_are_stripped():
    # Control characters because the value is displayed, and the middle dot
    # because Paired machines joins these fields with it - a value carrying
    # one would render as several fields.
    assert agent_version_from_header("v2.0\r\n.0") == "v2.0.0"
    assert agent_version_from_header("1.0 · watching Orbi-Lab2") == (
        "1.0  watching Orbi-Lab2"
    )


def test_cutting_happens_after_cleaning():
    # Otherwise a value padded with control characters would lose real
    # characters off the end to make room for ones that get thrown away.
    padded = "\x00" * 40 + "v2.0.0"
    assert clean_reported_text(padded, AGENT_VERSION_MAX_LENGTH) == "v2.0.0"


def test_the_instrument_rule_is_the_file_name_segment_rule():
    # Letters, digits and hyphens, up to 64 - the characters the instrument
    # segment of a file name allows. Deliberately not the whole routing rule,
    # which also requires the name to resolve to an instrument type.
    assert INSTRUMENT_NAME_RE.match("Orbi-Lab2")
    assert INSTRUMENT_NAME_RE.match("Lab2")
    assert INSTRUMENT_NAME_RE.match("A" * 64)
    assert not INSTRUMENT_NAME_RE.match("A" * 65)
    assert not INSTRUMENT_NAME_RE.match("orbi lab")
    assert not INSTRUMENT_NAME_RE.match("Orbi_Lab2")
    assert not INSTRUMENT_NAME_RE.match("Orbi-Lab2.raw")
    assert not INSTRUMENT_NAME_RE.match("")
    # `$` alone would accept a trailing newline; every caller strips first,
    # and this is what makes that safe to rely on.
    assert clean_reported_text("Orbi-Lab2\n", 64) == "Orbi-Lab2"


def test_an_instrument_name_is_never_cut_to_fit():
    # A cut instrument name is a different instrument, and uploads would be
    # filed under it - so the pairing validator asks for no width and refuses
    # an over-long name instead of quietly shortening it.
    too_long = "A" * 65
    assert clean_reported_text(too_long) == too_long
    assert not INSTRUMENT_NAME_RE.match(clean_reported_text(too_long))
