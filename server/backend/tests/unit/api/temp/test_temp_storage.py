"""Unit tests for per-user temp file scoping (``api/new/temp/storage.py``).

These pin the properties that keep the ``/api/temp`` download route from
leaking one user's ephemeral files to another, or escaping the temp directory
via a crafted filename.
"""

import os
import time

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


# --- expiry -------------------------------------------------------------------

DAY = 24 * 60 * 60


@pytest.fixture
def fresh_sweeper(monkeypatch):
    """Forget the last sweep, so the next write sweeps whatever ran before."""
    monkeypatch.setattr(storage, "_last_sweep", None)


def _write(path, age_s=0.0):
    """Write a file at ``path`` whose mtime is ``age_s`` seconds ago."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write("x")
    _age(path, age_s)
    return path


def _age(path, age_s):
    when = time.time() - age_s
    os.utime(path, (when, when), follow_symlinks=False)


def test_sweep_removes_only_downloads_past_retention(temp_base):
    """A download past the retention goes, one inside it stays, any user's."""
    old = _write(str(temp_base / "temp" / "1" / "old.zip"), DAY + 60)
    other = _write(str(temp_base / "temp" / "2" / "package.zip"), DAY + 60)
    new = _write(str(temp_base / "temp" / "1" / "new.zip"), DAY - 60)

    assert storage.sweep_expired_downloads() == 2

    assert not os.path.exists(old)
    assert not os.path.exists(other)
    assert os.path.exists(new)


def test_sweep_removes_an_expired_folder_with_its_files(temp_base):
    """The raw-file bundle leaves its folder of copies beside its zip."""
    folder = temp_base / "temp" / "1" / "sample_files_x"
    _write(str(folder / "a.raw"), DAY + 60)
    _age(str(folder), DAY + 60)

    assert storage.sweep_expired_downloads() == 1
    assert not folder.exists()


def test_sweep_keeps_a_folder_still_being_filled(temp_base):
    """A folder an export is writing into is fresh by its mtime."""
    folder = temp_base / "temp" / "1" / "mzml_x"
    _write(str(folder / "a.mzML"), DAY + 60)  # the file, not the folder, is old

    assert storage.sweep_expired_downloads() == 0
    assert folder.exists()


def test_sweep_leaves_uploads_and_package_builds_alone(temp_base):
    """``tus/`` and ``packages/`` are not downloads, however old."""
    upload = _write(str(temp_base / "temp" / "tus" / "partial"), 10 * DAY)
    build = _write(str(temp_base / "temp" / "packages" / "app-1" / "a"), 10 * DAY)
    _age(os.path.dirname(build), 10 * DAY)
    loose = _write(str(temp_base / "temp" / "loose.csv"), 10 * DAY)

    assert storage.sweep_expired_downloads() == 0
    assert os.path.exists(upload)
    assert os.path.exists(build)
    assert os.path.exists(loose)


def test_sweep_removes_a_symlink_not_its_target(temp_base):
    """An expired link goes; what it points at outside temp/ stays."""
    outside = temp_base / "elsewhere"
    target = _write(str(outside / "keep.raw"))
    link = temp_base / "temp" / "1" / "link"
    os.makedirs(link.parent)
    os.symlink(outside, link)
    _age(str(link), DAY + 60)

    assert storage.sweep_expired_downloads() == 1
    assert not os.path.lexists(link)
    assert os.path.exists(target)


def test_sweep_without_a_temp_dir_removes_nothing(temp_base):
    assert storage.sweep_expired_downloads() == 0


def test_writing_a_download_sweeps_expired_ones(temp_base, fresh_sweeper):
    """Every export resolves its file through user_temp_path: that sweeps."""
    old = _write(str(temp_base / "temp" / "2" / "old.zip"), DAY + 60)

    storage.user_temp_path(1, "new.csv")

    assert not os.path.exists(old)


def test_reading_a_download_does_not_sweep(temp_base, fresh_sweeper):
    """The download route (create=False) only reads."""
    old = _write(str(temp_base / "temp" / "1" / "old.zip"), DAY + 60)

    storage.user_temp_path(1, "old.zip", create=False)

    assert os.path.exists(old)


def test_writes_sweep_at_most_once_a_minute(temp_base, fresh_sweeper):
    """An export writing many files scans the directories once."""
    storage.user_temp_path(1, "first.csv")
    old = _write(str(temp_base / "temp" / "2" / "old.zip"), DAY + 60)

    storage.user_temp_path(1, "second.csv")

    assert os.path.exists(old)


def test_sweep_does_not_follow_a_linked_user_directory(temp_base):
    """A ``temp/<id>`` that is a link is not swept through to its target."""
    outside = temp_base / "elsewhere"
    target = _write(str(outside / "old.raw"), DAY + 60)
    os.makedirs(temp_base / "temp")
    os.symlink(outside, temp_base / "temp" / "3")

    assert storage.sweep_expired_downloads() == 0
    assert os.path.exists(target)
