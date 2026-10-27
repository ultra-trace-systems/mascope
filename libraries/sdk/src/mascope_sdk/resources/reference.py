"""Reference lists resource for the Mascope SDK."""

from typing import Any
from urllib.parse import quote

import pandas as pd
from loguru import logger

from ._base import BaseResource, _log_api_message


#: Rows per request of :meth:`ReferenceResource.compounds`: the most the
#: server gives on one page.
COMPOUNDS_PAGE_LIMIT = 1000

#: The columns of :meth:`ReferenceResource.sources`, in the server's order.
_SOURCE_COLUMNS = [
    "name",
    "version",
    "label",
    "references",
    "license",
    "polarity",
    "allow_radicals",
    "known_window",
    "record_count",
    "ingested_at",
    "admitted",
]

#: The columns of :meth:`ReferenceResource.compounds`, in the server's order.
_COMPOUND_COLUMNS = [
    "name",
    "formula",
    "monoisotopic_mass",
    "inchikey",
    "source_native_id",
    "reference",
    "tags",
]


class ReferenceResource(BaseResource):
    """Resource for the reference lists the server has loaded.

    A reference list is a published list of known compounds - the shipped
    atmospheric and contaminant lists, or one an operator loaded - that the
    deployment matches peaks against in Stage A of peak assignment. Lists
    belong to the deployment, not to a workspace, so any role reads them.

    Example::

        from mascope_sdk import MascopeClient

        mascope = MascopeClient()

        # Which lists is this deployment matching against?
        sources = mascope.reference.sources()
        print(sources[["label", "version", "record_count", "admitted"]])

        # And what is in one of them?
        compounds = mascope.reference.compounds("nitroaromatics")
    """

    def sources(self) -> pd.DataFrame:
        """List the active reference sources, one row each.

        :return: A DataFrame sorted by ``label``, with columns ``name``,
                 ``version``, ``label`` (a shipped list's own label, any
                 other source's name), ``references`` (the publications the
                 list comes from: a list of dicts with ``citation``, ``doi``,
                 ``isbn`` and ``note``, None where the list gives none; empty
                 for a list loaded from outside the shipped catalogue, whose
                 citation the server does not keep yet), ``license``, ``polarity`` (None for a
                 list of either), ``allow_radicals``, ``known_window`` (the
                 window its compounds are matched in, a dict), ``record_count``,
                 ``ingested_at`` (UTC) and ``admitted``: whether the
                 deployment's licence gate lets Stage A match the source. Empty
                 when the server has loaded none.
        :rtype: pd.DataFrame
        :raises AuthenticationError: If authentication fails.
        :raises NotFoundError: If the server predates the reference sources
            route.
        :raises MascopeAPIError: If the API request fails.

        Example::

            sources = mascope.reference.sources()
            unmatched = sources[~sources["admitted"]]
            # The publication each list comes from, to cite it.
            for label, refs in zip(sources["label"], sources["references"]):
                for ref in refs:
                    print(label, ref["citation"], ref["doi"] or ref["isbn"])
        """
        rows = self._get("reference/sources") or []
        frame = pd.DataFrame(rows) if rows else pd.DataFrame(columns=_SOURCE_COLUMNS)
        # ISO8601, not a format inferred from the first row: the server leaves
        # the fraction out of a load date whose microseconds are 0.
        frame["ingested_at"] = pd.to_datetime(
            frame["ingested_at"], utc=True, format="ISO8601"
        )
        logger.info("Listed {} reference source(s)", len(frame))
        return frame

    def compounds(self, source_name: str, version: str | None = None) -> pd.DataFrame:
        """List the compounds one version of a reference source holds.

        Reads every page of the route, so the frame holds the whole version.
        Without ``version``, the later pages ask for the version the first
        one came from, so a list reloaded during the read is not mixed in.

        :param source_name: The source's ``name``, as :meth:`sources` lists it
            (not its label).
        :type source_name: str
        :param version: The version to read, active or not; the active one
            when omitted.
        :type version: str | None
        :return: A DataFrame ordered by mass, with columns ``name``,
                 ``formula``, ``monoisotopic_mass``, ``inchikey``,
                 ``source_native_id`` (the list's own id for it),
                 ``reference`` (the DOI the list cites for it) and ``tags`` (a
                 list). ``attrs["source"]`` and ``attrs["version"]`` name the
                 version read, the active one's when none was given.
        :rtype: pd.DataFrame
        :raises NotFoundError: If the server has no such source, or the source
            no such version (no active one, when none is named), or the server
            predates the route.
        :raises AuthenticationError: If authentication fails.
        :raises MascopeAPIError: If the API request fails.

        Example::

            compounds = mascope.reference.compounds("nitroaromatics")
            compounds[compounds["formula"] == "C6H5NO3"]
        """
        params: dict[str, Any] = {"limit": COMPOUNDS_PAGE_LIMIT}
        if version is not None:
            params["version"] = version
        path = f"reference/sources/{quote(source_name, safe='')}/compounds"

        rows: list[dict[str, Any]] = []
        page = 0
        while True:
            envelope = self._get_envelope(path, {**params, "page": page})
            _log_api_message(envelope)
            batch = envelope.get("data") or []
            rows.extend(batch)
            # Read the later pages from the version page 0 answered with, so
            # a load that becomes active mid-read cannot mix two versions.
            params.setdefault("version", envelope.get("version"))
            if not batch or len(rows) >= int(envelope.get("total") or 0):
                break
            page += 1

        frame = pd.DataFrame(rows) if rows else pd.DataFrame(columns=_COMPOUND_COLUMNS)
        frame.attrs["source"] = source_name
        frame.attrs["version"] = envelope.get("version")
        logger.info(
            "Listed {} compound(s) of {} {}",
            len(frame),
            source_name,
            frame.attrs["version"],
        )
        return frame
