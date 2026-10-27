"""The source registry: source name -> ETL adapter.

The CLI (``mascope reference sync <source>``) and any programmatic ingestion
resolve an adapter through here, so adding a source is a one-line registration
and nothing else in the pipeline changes.

Compound sources and spectral libraries are registered apart. What reads the
compound registry - the licence tags Stage A's gate is written in, the windows
``mascope reference sources`` prints - is about compounds, and a spectral
library has neither; its spectra are filtered by licence in their own read
path (:mod:`mascope_reference.spectral_query`). The two share one namespace,
so a name resolves to one adapter.
"""

from mascope_reference.adapters import (
    Adapter,
    ChebiAdapter,
    CoconutAdapter,
    CompToxAdapter,
    CustomAdapter,
    GnpsAdapter,
    HmdbAdapter,
    LipidMapsAdapter,
    MassBankAdapter,
    MonaAdapter,
    NormanAdapter,
    PeakListAdapter,
    PubChemAdapter,
    SpectrumAdapter,
)


# One instance per source, keyed by its stable ``name``.
_ADAPTERS: dict[str, Adapter] = {
    adapter.name: adapter
    for adapter in (
        PubChemAdapter(),
        CompToxAdapter(),
        ChebiAdapter(),
        HmdbAdapter(),
        LipidMapsAdapter(),
        CoconutAdapter(),
        NormanAdapter(),
        CustomAdapter(),
        PeakListAdapter(),
    )
}


# The spectral libraries, keyed the same way.
_SPECTRUM_ADAPTERS: dict[str, SpectrumAdapter] = {
    adapter.name: adapter
    for adapter in (MassBankAdapter(), MonaAdapter(), GnpsAdapter())
}


def available_sources() -> list[str]:
    """Sorted list of registered source names."""
    return sorted(_ADAPTERS)


def get_adapter(source: str) -> Adapter:
    """Resolve a source name to its adapter.

    :param source: Registered source name (see :func:`available_sources`).
    :raises KeyError: If the source is not registered, with the valid names.
    :return: The adapter instance for the source.
    """
    try:
        return _ADAPTERS[source]
    except KeyError:
        raise KeyError(
            f"Unknown reference source '{source}'. "
            f"Available: {', '.join(available_sources())}; spectral libraries: "
            f"{', '.join(available_spectrum_sources())}"
        ) from None


def available_spectrum_sources() -> list[str]:
    """Sorted list of registered spectral library names."""
    return sorted(_SPECTRUM_ADAPTERS)


def is_spectrum_source(source: str) -> bool:
    """Whether a name is a registered spectral library's."""
    return source in _SPECTRUM_ADAPTERS


def get_spectrum_adapter(source: str) -> SpectrumAdapter:
    """Resolve a spectral library's name to its adapter.

    :param source: Registered name (see :func:`available_spectrum_sources`).
    :raises KeyError: If no spectral library has that name, with the valid ones.
    :return: The adapter instance.
    """
    try:
        return _SPECTRUM_ADAPTERS[source]
    except KeyError:
        raise KeyError(
            f"Unknown spectral library '{source}'. "
            f"Available: {', '.join(available_spectrum_sources())}"
        ) from None
