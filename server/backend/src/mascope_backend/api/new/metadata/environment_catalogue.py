"""The environments Mascope ships: the registry's starting set.

Which environments and which regimes is the list curators' call
(``docs/dev/sample_filters_and_metadata.md``, section 4.6). The set starts
with the environments the shipped lists' regimes make a case for: the boreal
forest of the note's own example, read against the monoterpene and biogenic
SOA lists, and the two chamber oxidations the shipped lists come from. An
environment with no regimes would leave every list that declares regimes out
of the samples tagged with it, which is a measurement's to decide (MAS-349),
so none ships yet.

Each entry has a fixed id, so a shipped row reads alike on every server, and
:func:`~mascope_backend.api.new.metadata.environment_service.ensure_shipped_environments`
creates at every start the entries a server does not hold. Adding one here is
safe at any time and needs no migration. A row a server holds is the
server's: an edit made to it stays, and a start never rewrites it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ShippedEnvironment:
    """One environment of the starting set."""

    environment_id: str
    key: str
    label: str
    chemistry_context: str | None
    regimes: tuple[str, ...]


SHIPPED_ENVIRONMENTS: tuple[ShippedEnvironment, ...] = (
    ShippedEnvironment(
        environment_id="sysenvborealfrst",
        key="boreal-forest",
        label="Boreal forest",
        chemistry_context="ambient-air",
        regimes=("monoterpene_ox", "biogenic_soa"),
    ),
    ShippedEnvironment(
        environment_id="sysenvchambermtx",
        key="chamber-monoterpene-oxidation",
        label="Chamber: monoterpene oxidation",
        chemistry_context="chamber",
        regimes=("monoterpene_ox",),
    ),
    ShippedEnvironment(
        environment_id="sysenvchamberisx",
        key="chamber-isoprene-oxidation",
        label="Chamber: isoprene oxidation",
        chemistry_context="chamber",
        regimes=("isoprene_ox",),
    ),
)
