"""Unit tests for per-user temp file scoping (``api/new/temp/storage.py``).

These pin the properties that keep the ``/api/temp`` download route from
leaking one user's ephemeral files to another, or escaping the temp directory
via a crafted filename.
"""

import os

import pytest

from mascope_backend.api.new.temp import storage


@pytest.fixture
def temp_base(tmp_path, monkeypatch):
    """Point the runtime temp dir at a throwaway location for the test."""
    monkeypatch.setattr(
        storage.runtime.env,
        "path",
        lambda *segments: os.path.join(str(tmp_path), *segments),
    )
    return tmp_path


def test_user_temp_dir_is_per_user(temp_base):
    """Each user gets a distinct, real directory."""
    dir_a = storage.user_temp_dir(1)
    dir_b = storage.user_temp_dir(2)
    assert dir_a != dir_b
    assert os.path.isdir(dir_a)
    assert os.path.isdir(dir_b)


def test_user_temp_path_resolves_inside_user_dir(temp_base):
    """A plain filename resolves to a file directly in the user's dir."""
    path = storage.user_temp_path(7, "export.csv")
    assert path == os.path.join(storage.user_temp_dir(7), "export.csv")


def test_create_false_does_not_mint_directories(temp_base):
    """The read path (create=False) must not create per-user directories.

    Otherwise any authenticated GET probe against /api/temp would leave an
    empty directory behind for the probed user id.
    """
    path = storage.user_temp_path(999, "probe.csv", create=False)
    assert not os.path.isdir(storage.user_temp_dir(999, create=False))
    # The resolved path is still correct for the containment check.
    assert path == os.path.join(storage.user_temp_dir(999, create=False), "probe.csv")


@pytest.mark.parametrize(
    "attack",
    [
        "../8/secret.csv",  # a sibling user's directory
        "../../secrets/jwt_secret_key.txt",  # the secrets dir
        "/etc/passwd",  # absolute path
        "foo/bar.csv",  # nested path
        "..",
        ".",
        "",
    ],
)
def test_user_temp_path_never_escapes_user_dir(temp_base, attack):
    """A crafted filename either raises or collapses to the user's own dir.

    In no case may it resolve to a path outside the requesting user's temp
    directory (another user's dir, the secrets dir, the filesystem root).
    """
    base = os.path.realpath(storage.user_temp_dir(7))
    try:
        resolved = os.path.realpath(storage.user_temp_path(7, attack))
    except ValueError:
        return
    assert os.path.dirname(resolved) == base


# --- download names ---------------------------------------------------------


def test_download_name_replaces_what_a_file_system_or_a_route_cannot_take():
    # A batch named with a slash was a directory to the file system and a path
    # to the download route: the file went under one name, the request under
    # another, and the download failed.
    name = storage.download_name(
        "20260904T101500", "batch_ledger", "site A/run 3", extension="csv"
    )
    assert name == "20260904T101500_batch_ledger_site_A_run_3.csv"
    assert "/" not in name
    assert os.path.basename(name) == name

    hostile = storage.download_name("x", 'a\\b:c*d?e"f<g>h|i', extension="xlsx")
    assert hostile == "x_a_b_c_d_e_f_g_h_i.xlsx"


def test_download_name_collapses_whitespace_and_underscores_and_drops_empty_parts():
    assert (
        storage.download_name("t", "", "  two   words  ", extension=".csv")
        == "t_two_words.csv"
    )
    assert storage.download_name("t", "a__b", extension="csv") == "t_a_b.csv"
    assert storage.download_name("t", "...", extension="csv") == "t.csv"


def test_download_name_caps_the_stem_and_never_comes_back_empty():
    long = storage.download_name("t", "n" * 500, extension="csv")
    assert len(long) <= storage.MAX_DOWNLOAD_STEM + len(".csv")
    assert long.endswith(".csv")
    assert storage.download_name("", "/", extension="csv") == "download.csv"
