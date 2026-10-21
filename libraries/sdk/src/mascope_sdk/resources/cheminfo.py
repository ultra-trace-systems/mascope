"""Chemical information resource for the Mascope SDK."""

from ._base import BaseResource


class ChemInfoResource(BaseResource):
    """Resource for chemical information queries.

    Provides methods to query for potential elemental compositions based on
    m/z values and other parameters.

    Example::

        from mascope_sdk import MascopeClient

        mascope = MascopeClient()

        # Query potential formulas for a given m/z
        results = mascope.cheminfo.query_by_mz(
            mz=180.063,
            ionization_mechanism_ids=["mech-1", "mech-2"],
            mz_tolerance=30.0,
        )
        for result in results:
            print(f"{result['formula']}: {result['mass']}")
    """

    def query_by_mz(
        self,
        mz: float,
        ionization_mechanism_ids: list[str],
        formula_ranges: str = "C0-100 H0-100 O0-100 N0-100",
        mz_tolerance: float = 30.0,
        limit: int = 20,
        *,
        isotopologues: bool = True,
        known_only: bool = False,
    ) -> list[dict]:
        """Query potential elemental compositions for a given m/z value.

        Searches for chemical formulas that could produce the specified m/z
        value within the given tolerance, considering the specified ionization
        mechanisms.

        :param mz: The m/z value to query.
        :type mz: float
        :param ionization_mechanism_ids: List of ionization mechanism IDs to consider.
        :type ionization_mechanism_ids: list[str]
        :param formula_ranges: Element ranges to consider, e.g.
                               "C0-100 H0-100 O0-100 N0-100".
                               Defaults to common organic elements.
        :type formula_ranges: str, optional
        :param mz_tolerance: The m/z tolerance in ppm. Defaults to 30.0.
        :type mz_tolerance: float, optional
        :param limit: Sent to the server, which does not honour it: the route
                      returns every composition it finds, up to its own cap, in
                      the composition finder's order rather than by error. Sort
                      and trim the list yourself. Defaults to 20.
        :type limit: int, optional
        :param isotopologues: When True, the m/z may be any line of a
                              candidate's ion above the server's floor, not
                              only its monoisotopic line: a 13C or 81Br line, a
                              dibromide's brighter 79Br81Br line, as a peak's
                              m/z often is. A candidate read at another line
                              has that line's m/z as its
                              ``target_isotope_mz``, and its
                              ``target_isotope_mz_error_ppm`` is against that
                              line. False searches the monoisotopic line only.
                              Always sent; a server that predates it ignores
                              it and searches the monoisotopic line only,
                              whatever it is. Defaults to True.
        :type isotopologues: bool, optional
        :param known_only: When True, keep only candidates whose formula is a
                           known reference compound's; each carries them as
                           ``known_compounds``. Sent only when True; a server
                           that predates it ignores it and returns every
                           candidate. Defaults to False.
        :type known_only: bool, optional
        :return: A list of dictionaries containing potential elemental compositions,
                 each with formula, mass, and other chemical information.
        :rtype: list[dict]
        :raises AuthenticationError: If authentication fails.
        :raises ValidationError: If parameters are invalid.
        :raises MascopeAPIError: If the API request fails.

        Example::

            # Get ionization mechanisms first
            mechanisms = mascope.ionization.list()
            mech_ids = [m['id'] for m in mechanisms[:2]]

            # Query formulas
            results = mascope.cheminfo.query_by_mz(
                mz=180.063,
                ionization_mechanism_ids=mech_ids,
                formula_ranges="C0-20 H0-40 O0-10 N0-5",
                mz_tolerance=20.0,
            )

            # Only formulas whose monoisotopic line is at the m/z
            results = mascope.cheminfo.query_by_mz(
                mz=126.9223,
                ionization_mechanism_ids=mech_ids,
                mz_tolerance=5.0,
                isotopologues=False,
            )
        """
        data = {
            "mz": mz,
            "mz_precision": mz_tolerance,
            "formula_ranges": formula_ranges,
            "ionization_mechanism_ids": ionization_mechanism_ids,
            "limit": limit,
            "isotopologues": isotopologues,
        }
        if known_only:
            data["known_only"] = True
        return self._post("cheminfo/mz/query", data=data) or []
