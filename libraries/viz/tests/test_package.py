"""The package is a workspace member: installed, importable, versioned."""

from importlib.metadata import version

import mascope_viz


def test_package_is_installed():
    assert mascope_viz.__version__ == version("mascope_viz")
