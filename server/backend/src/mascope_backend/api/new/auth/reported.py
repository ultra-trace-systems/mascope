"""What a paired agent reports about itself, and the rules for keeping it.

An instrument name and an agent release reach the server by three routes -
the pairing request, the ``X-Agent-Version`` header, and an upload's tus
metadata - and are written to two columns on ``agent_device``. The rules live
here so the routes cannot drift from each other, or from the columns they
feed: a value one route accepts and another silently truncates would show a
machine as something it never reported.
"""

import re


#: Widths of the columns these values are written to (``AgentDevice``).
INSTRUMENT_MAX_LENGTH = 64
AGENT_VERSION_MAX_LENGTH = 32

#: What a reported instrument name may look like: letters, digits and
#: hyphens, the characters the instrument segment of a file name allows
#: (``mascope_file.name``). Deliberately not the whole rule that reads an
#: instrument off a file name, which also requires the name to resolve to an
#: instrument type - that requirement belongs to the file name, not to a name
#: the agent reports, and it is what a later release drops.
INSTRUMENT_NAME_RE = re.compile(rf"^[A-Za-z0-9-]{{1,{INSTRUMENT_MAX_LENGTH}}}$")

#: Stripped from anything an agent reports. Control characters because the
#: values are displayed; the middle dot because Paired machines joins these
#: fields with it, and a value carrying one would render as several fields -
#: a machine could otherwise describe itself as watching an instrument it
#: does not, in the list a sponsor reads to decide what to revoke.
_UNWANTED_CHARACTERS = re.compile(r"[\x00-\x1f\x7f·]")


def clean_reported_text(value: str | None, max_length: int | None = None) -> str | None:
    """Free text an agent reported, fit for storing and showing.

    Unwanted characters are stripped, and the value is cut to the column's
    width when a width is given. Cutting suits a version, which is a label
    the machine puts on itself: refusing one would cost the machine the thing
    it was asking for, a credential or an upload, over something nothing acts
    on. It does not suit an instrument, which is what uploads get filed
    under - a cut name is a *different* name, and files would quietly land
    somewhere nobody asked for - so that caller passes no width and refuses
    an over-long name instead.

    :param value: The raw reported value, if the request carried one.
    :type value: str | None
    :param max_length: Width to cut to, or None to leave the length alone.
    :type max_length: int | None
    :return: The cleaned value, or None when there is nothing left.
    :rtype: str | None
    """
    if not value:
        return None
    cleaned = _UNWANTED_CHARACTERS.sub("", value).strip()
    if max_length is not None:
        cleaned = cleaned[:max_length]
    return cleaned or None
