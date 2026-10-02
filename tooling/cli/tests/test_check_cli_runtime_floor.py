"""
Guards for `tooling/check-cli-runtime-floor.py`, which imports the built CLI
wheel against the oldest mascope-runtime the CLI's requirement admits.

The check is worth what its choice of runtime is worth, and both ways of
choosing wrongly are quiet. A runtime newer than the oldest one admitted - the
wheel built from the same checkout above all - passes a CLI that fails for
anyone left on the floor, which is the blind spot the check exists to close. A
runtime no release will ever provide fails a pull request that has nothing
left to fix.

The case worth naming is one version number for two runtimes: PyPI already
has the checkout's runtime version, published before the runtime gained what
the CLI now imports. A release skips a version PyPI has, so PyPI's copy is the
one to import against. The checkout's own wheel is right only for a version
PyPI has never had - and that is how a pull request that needs a newer
runtime goes green: it gives the runtime such a version and raises the floor
to it.

Choosing a runtime needs no network here; the step that installs one runs in
CI's packaging job.
"""

import importlib.util
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tooling" / "check-cli-runtime-floor.py"

# Guarded the same way as test_check_licenses.py: repo-root tooling/ is not
# present in every checkout or packaged layout.
if not SCRIPT.is_file():
    pytest.skip(
        "repo-root tooling/check-cli-runtime-floor.py not available",
        allow_module_level=True,
    )


def _load():
    """Import the script by path - a hyphenated filename is not a module name."""
    spec = importlib.util.spec_from_file_location("check_cli_runtime_floor", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


floor = _load()

PUBLISHED = {Version(v) for v in ("2026.9.15", "2026.9.16", "2026.9.30", "2026.10.1")}


def pick(requirement, checkout, taken=PUBLISHED, served=None):
    """The (version, from the checkout?) the check imports against, or None."""
    target = floor.oldest_admitted(
        Requirement(requirement),
        set(taken),
        set(taken if served is None else served),
        Version(checkout),
    )
    return None if target is None else (str(target.version), target.from_checkout)


# ============= Which runtime =============


def test_the_floor_itself_when_pypi_serves_it():
    assert pick("mascope_runtime>=2026.9.15", "2026.10.1") == ("2026.9.15", False)


def test_the_next_published_runtime_when_pypi_never_had_the_floor():
    # Nobody can be left on a version that was never published.
    assert pick("mascope_runtime>=2026.9.20", "2026.10.1") == ("2026.9.30", False)


def test_pypis_copy_of_the_checkouts_version_not_the_wheel_built_here():
    # The checkout's runtime may have gained names since PyPI got 2026.10.1,
    # and no release will publish 2026.10.1 again.
    assert pick("mascope_runtime>=2026.10.1", "2026.10.1") == ("2026.10.1", False)


def test_the_checkouts_own_runtime_when_pypi_has_never_had_its_version():
    # The release publishes 2026.10.2 from this source, so nothing older is
    # admitted and the wheel built here is what users of the floor get.
    assert pick("mascope_runtime>=2026.10.2", "2026.10.2") == ("2026.10.2", True)


def test_the_checkouts_own_runtime_once_it_moves_past_an_unpublished_floor():
    # Release prep dates the runtime after the floor a pull request set.
    assert pick("mascope_runtime>=2026.10.2", "2026.10.15") == ("2026.10.15", True)


def test_a_published_runtime_the_floor_admits_comes_before_the_checkouts():
    # Moving the runtime's version without the floor changes nothing.
    assert pick("mascope_runtime>=2026.9.15", "2026.10.2") == ("2026.9.15", False)


def test_a_yanked_runtime_is_passed_over():
    served = PUBLISHED - {Version("2026.9.15")}

    assert pick("mascope_runtime>=2026.9.15", "2026.10.1", served=served) == (
        "2026.9.16",
        False,
    )


def test_a_version_pypi_has_taken_is_never_the_checkouts_own():
    # Every file of 2026.10.2 yanked: the publish workflow still skips it.
    taken = PUBLISHED | {Version("2026.10.2")}

    assert (
        pick("mascope_runtime>=2026.10.2", "2026.10.2", taken=taken, served=PUBLISHED)
        is None
    )


def test_nothing_when_the_floor_is_above_every_runtime():
    assert pick("mascope_runtime>=2027.1.1", "2026.10.1") is None


# ============= Reading PyPI =============


def _file(filename, yanked=False):
    return {
        "filename": filename,
        "url": f"https://example.org/{filename}",
        "yanked": yanked,
    }


def test_the_index_page_gives_the_taken_and_the_served_versions():
    page = {
        "versions": ["2026.9.15", "2026.9.16", "2026.9.17", "2026.9.18"],
        "files": [
            _file("mascope_runtime-2026.9.15-py3-none-any.whl"),
            _file("mascope_runtime-2026.9.15.tar.gz"),
            # One file yanked, with a reason: the version is still served.
            _file("mascope_runtime-2026.9.16-py3-none-any.whl", yanked="broken"),
            _file("mascope_runtime-2026.9.16.tar.gz"),
            # Every file yanked.
            _file("mascope_runtime-2026.9.17-py3-none-any.whl", yanked=True),
            _file("mascope_runtime-2026.9.17.tar.gz", yanked=True),
            # 2026.9.18 is listed with no files at all.
        ],
    }

    taken, served = floor.index_versions(page)

    assert taken == {Version(v) for v in page["versions"]}
    assert served == {Version("2026.9.15"), Version("2026.9.16")}


# ============= Installing exactly that runtime =============


def test_pypis_runtime_is_named_by_its_version_with_the_clis_extras(tmp_path):
    # dist/ holds a wheel of the same version, which must not be offered.
    (tmp_path / "mascope_runtime-2026.10.1-py3-none-any.whl").touch()
    target = floor.Target(Version("2026.10.1"), from_checkout=False)

    pinned = floor.install_requirement(
        Requirement("mascope_runtime[logs]>=2026.10.1"), target, tmp_path
    )

    assert pinned == "mascope_runtime[logs]==2026.10.1"


def test_the_checkouts_runtime_is_installed_from_its_own_wheel(tmp_path):
    wheel = tmp_path / "mascope_runtime-2026.10.2-py3-none-any.whl"
    wheel.touch()
    (tmp_path / "mascope_cli-2026.10.2-py3-none-any.whl").touch()
    target = floor.Target(Version("2026.10.2"), from_checkout=True)

    pinned = floor.install_requirement(
        Requirement("mascope_runtime[logs]>=2026.10.2"), target, tmp_path
    )

    assert pinned == f"mascope_runtime[logs] @ {wheel.resolve().as_uri()}"


def test_a_checkout_wheel_of_another_version_is_refused(tmp_path):
    (tmp_path / "mascope_runtime-2026.10.1-py3-none-any.whl").touch()
    target = floor.Target(Version("2026.10.2"), from_checkout=True)

    with pytest.raises(SystemExit, match="mascope-runtime 2026.10.2"):
        floor.install_requirement(
            Requirement("mascope_runtime[logs]>=2026.10.2"), target, tmp_path
        )


# ============= Reading the pyprojects =============


def test_the_runtime_requirement_is_found_among_the_dependencies(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "cli"\nversion = "1"\ndependencies = [\n'
        '    "typer>=0.27.1,<0.28",\n'
        '    "mascope-runtime[logs]>=2026.9.15",\n'
        "]\n",
        encoding="utf-8",
    )

    requirement = floor.runtime_requirement(pyproject)

    assert requirement.extras == {"logs"}
    assert str(requirement.specifier) == ">=2026.9.15"


def test_a_cli_without_a_runtime_requirement_is_refused(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "cli"\nversion = "1"\ndependencies = ["typer"]\n',
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="no mascope-runtime dependency"):
        floor.runtime_requirement(pyproject)


def test_the_files_the_guidance_names_exist():
    # The guidance is read only when the check fails, so nothing else would
    # notice it pointing at a file that has moved.
    for path in (
        floor.CLI_PYPROJECT,
        floor.RUNTIME_PYPROJECT,
        floor.MIRROR_MODULE,
        floor.MIRROR_TEST,
        f"{floor.AGENT_PROJECT}/uv.lock",
    ):
        assert (REPO_ROOT / path).is_file(), path
