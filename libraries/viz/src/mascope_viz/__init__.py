"""mascope_viz - visual digests of Mascope data.

Turns a scope of Mascope data into charts, an interactive dashboard and a
PDF report. This is the package's skeleton: the data layer, the charts and
the digests land in the phases of ``docs/dev/visual_digest.md``.
"""

from importlib.metadata import version


__version__ = version("mascope_viz")

__all__ = ["__version__"]
