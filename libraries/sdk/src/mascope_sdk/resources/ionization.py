"""Ionization mechanisms and modes resource for the Mascope SDK."""

import pandas as pd

from ._base import BaseResource, _coerce_datetime_columns


class IonizationResource(BaseResource):
    """Resource for ionization mechanism and mode operations.

    Ionization mechanisms describe how molecules become ions during mass spectrometry.
    They are used for compound matching and chemical formula queries. A mechanism
    is written in the standard adduct notation: ``"[M+H]+"``, ``"[M-H]-"``,
    ``"[M+Br]-"``, and ``"[M]+."`` for electron transfer, the ion's charge last.

    Example::

        from mascope_sdk import MascopeClient

        mascope = MascopeClient()

        # List available ionization mechanisms
        mechanisms = mascope.ionization.list()
        print(mechanisms[["ionization_mechanism_id", "ionization_mechanism"]])

    An ionization mode is the chemistry a sample was acquired under: a name,
    a polarity and the mechanisms it matches with. A sample names its mode by
    ``ionization_mode_id`` alone; :meth:`modes` lists them by name.
    """

    def list(self) -> pd.DataFrame | None:
        """List all available ionization mechanisms.

        :return: A DataFrame containing ionization mechanism information with
                 columns including ``ionization_mechanism_id`` and
                 ``ionization_mechanism``, or None if no mechanisms are found.
        :rtype: pd.DataFrame | None
        :raises AuthenticationError: If authentication fails.
        :raises MascopeAPIError: If the API request fails.

        Example::

            mechanisms = mascope.ionization.list()
            protonation = mechanisms[mechanisms["ionization_mechanism"] == "[M+H]+"]
        """
        cache_key = "ionization_mechanisms"
        if cache_key in self._client._cache:
            # A copy: a caller editing its listing must not edit the cache
            return self._client._cache[cache_key].copy()
        data = self._get("ionization_mechanisms")
        if not data:
            return None
        df = _coerce_datetime_columns(pd.DataFrame(data))
        self._client._cache[cache_key] = df
        return df.copy()

    def modes(self, include_system: bool = False) -> pd.DataFrame | None:
        """List the ionization modes.

        :param include_system: Also list the modes Mascope ships that this
            server has not adopted. They are left out by default, as the web
            app leaves them out until it offers the shipped chemistries
            itself.
        :type include_system: bool
        :return: A DataFrame with one row per mode and columns including
                 ``ionization_mode_id``, ``ionization_mode_name``,
                 ``ionization_mode_polarity``, ``ionization_mode_token``,
                 ``instrument`` and ``ionization_mechanism_ids``, or None if
                 there are none.
        :rtype: pd.DataFrame | None
        :raises AuthenticationError: If authentication fails, or the server
            is older than the token access to the modes.
        :raises MascopeAPIError: If the API request fails.

        Example::

            modes = mascope.ionization.modes()
            samples = mascope.samples.list(batch="Campaign")
            samples = samples.merge(
                modes[["ionization_mode_id", "ionization_mode_name"]],
                on="ionization_mode_id",
                how="left",
            )
        """
        cache_key = f"ionization_modes:{include_system}"
        if cache_key in self._client._cache:
            # A copy: a caller editing its listing must not edit the cache
            return self._client._cache[cache_key].copy()
        params = {"include_system": "true"} if include_system else None
        data = self._get("ionization/modes", params=params)
        if not data:
            return None
        df = _coerce_datetime_columns(pd.DataFrame(data))
        self._client._cache[cache_key] = df
        return df.copy()
