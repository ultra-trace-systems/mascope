"""
Tests for `mascope init` — runtime home creation.

The command materializes bundled config/compose files, creates the
`.runtime/` skeleton, and generates secrets. It must be safe to re-run
(idempotent), must never regenerate secrets, and must work without any
pre-existing environment.
"""

import os
import stat
import sys

import pytest

from mascope_cli.cmd import init as init_cmd
from mascope_cli.cmd.init import (
    CONFIG_FILES,
    GENERATED_SECRETS,
    IMAGE_READ_SECRETS,
    PLACEHOLDER_SECRETS,
    RUNTIME_DIRS,
)
from mascope_cli.main import app


def _init(cli_runner, home, *args):
    return cli_runner.invoke(app, ["init", "--path", str(home), *args])


def test_init_creates_a_complete_home(cli_runner, tmp_path):
    home = tmp_path / "home"

    result = _init(cli_runner, home)

    assert result.exit_code == 0
    for name in CONFIG_FILES:
        assert (home / name).is_file(), f"missing config file {name}"
    for rel in RUNTIME_DIRS:
        assert (home / rel).is_dir(), f"missing directory {rel}"
    for name in GENERATED_SECRETS:
        secret = (home / ".runtime" / "secrets" / name).read_text().strip()
        assert len(secret) >= 32, f"secret {name} looks too short"


def test_init_creates_the_operator_supplied_ca_empty(cli_runner, tmp_path):
    # Compose refuses to start on a declared secret whose file is missing, and
    # the origin-pull CA is the operator's to bring: an empty file lets a site
    # without origin pulls start, and reads as "not set up" to the frontend.
    home = tmp_path / "home"

    result = _init(cli_runner, home)

    assert result.exit_code == 0
    assert "origin_pull_ca.pem" in PLACEHOLDER_SECRETS
    ca = home / ".runtime" / "secrets" / "origin_pull_ca.pem"
    assert ca.is_file()
    assert ca.read_bytes() == b""


def test_init_creates_the_turnstile_secret_empty(cli_runner, tmp_path):
    # The site's Turnstile secret key is the operator's to bring as well, and
    # an empty file reads as "no secret" to the backend.
    home = tmp_path / "home"

    result = _init(cli_runner, home)

    assert result.exit_code == 0
    secret = home / ".runtime" / "secrets" / "turnstile_secret_key.txt"
    assert secret.is_file()
    assert secret.read_bytes() == b""


@pytest.mark.skipif(os.name == "nt", reason="Windows has no owner-only file mode")
def test_init_creates_the_turnstile_secret_owner_only(cli_runner, tmp_path, open_umask):
    # The operator writes the secret key into the file the CLI made, and the
    # file keeps its mode: made at the umask, the key would be world-readable.
    # The CA holds nothing secret and stays at the umask.
    home = tmp_path / "home"

    _init(cli_runner, home)

    secrets_dir = home / ".runtime" / "secrets"
    mode = stat.S_IMODE((secrets_dir / "turnstile_secret_key.txt").stat().st_mode)
    assert mode == 0o600, f"turnstile_secret_key.txt is mode {oct(mode)}"
    ca_mode = stat.S_IMODE((secrets_dir / "origin_pull_ca.pem").stat().st_mode)
    assert ca_mode == 0o644, f"origin_pull_ca.pem is mode {oct(ca_mode)}"


def test_init_keeps_an_operator_supplied_turnstile_secret(cli_runner, tmp_path):
    home = tmp_path / "home"
    secret = home / ".runtime" / "secrets" / "turnstile_secret_key.txt"
    secret.parent.mkdir(parents=True)
    secret.write_bytes(b"0x4AAA-the-operators-key\n")

    result = _init(cli_runner, home, "--force")

    assert result.exit_code == 0
    assert secret.read_bytes() == b"0x4AAA-the-operators-key\n"


def test_init_keeps_an_operator_supplied_ca(cli_runner, tmp_path):
    home = tmp_path / "home"
    ca = home / ".runtime" / "secrets" / "origin_pull_ca.pem"
    ca.parent.mkdir(parents=True)
    ca.write_bytes(b"-----BEGIN CERTIFICATE-----\n")

    result = _init(cli_runner, home, "--force")

    assert result.exit_code == 0
    assert ca.read_bytes() == b"-----BEGIN CERTIFICATE-----\n"


@pytest.mark.skipif(os.name == "nt", reason="Windows has no owner-only file mode")
def test_init_writes_secrets_only_their_owner_can_read(
    cli_runner, tmp_path, open_umask
):
    # Compose mounts each secret into the containers with the host file's mode,
    # so a secret written at the umask (0644 here) could be read by every user
    # on the server and every uid in the backend container, not only the app.
    home = tmp_path / "home"

    _init(cli_runner, home)

    for name in GENERATED_SECRETS:
        mode = stat.S_IMODE((home / ".runtime" / "secrets" / name).stat().st_mode)
        assert mode == 0o600, f"secret {name} is mode {oct(mode)}"


def test_init_generates_unique_secrets(cli_runner, tmp_path):
    _init(cli_runner, tmp_path / "a")
    _init(cli_runner, tmp_path / "b")

    a = (tmp_path / "a" / ".runtime" / "secrets" / "jwt_secret_key.txt").read_text()
    b = (tmp_path / "b" / ".runtime" / "secrets" / "jwt_secret_key.txt").read_text()
    assert a != b


def test_init_keeps_existing_files(cli_runner, tmp_path):
    home = tmp_path / "home"
    _init(cli_runner, home)

    config = home / "base.mascope.toml"
    secret = home / ".runtime" / "secrets" / "postgres_password.txt"
    config.write_text("# operator override\n", encoding="utf-8")
    original_secret = secret.read_text()

    result = _init(cli_runner, home)

    assert result.exit_code == 0
    assert config.read_text(encoding="utf-8") == "# operator override\n"
    assert secret.read_text() == original_secret


def test_init_force_refreshes_config_but_never_secrets(cli_runner, tmp_path):
    home = tmp_path / "home"
    _init(cli_runner, home)

    config = home / "base.mascope.toml"
    secret = home / ".runtime" / "secrets" / "postgres_password.txt"
    config.write_text("# operator override\n", encoding="utf-8")
    original_secret = secret.read_text()

    result = _init(cli_runner, home, "--force")

    assert result.exit_code == 0
    assert "[meta]" in config.read_text(encoding="utf-8")  # bundled content is back
    assert secret.read_text() == original_secret


def test_init_defaults_to_platform_home(cli_runner, tmp_path, monkeypatch):
    monkeypatch.delenv("MASCOPE_PATH", raising=False)
    default = tmp_path / "default-home"
    monkeypatch.setattr("mascope_cli.cmd.init.default_home", lambda: default)

    result = cli_runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (default / "base.mascope.toml").is_file()


def test_init_hints_when_not_the_default_home(cli_runner, tmp_path):
    result = _init(cli_runner, tmp_path / "custom")

    assert result.exit_code == 0
    assert "MASCOPE_PATH" in result.output


# --- secrets the image cannot read ---
#
# The published backend image runs as uid 1000 and compose mounts each secret
# with its host owner and mode, so a home made by any other user holds 0600
# secrets the containers cannot open. The tests play "another user" by setting
# the image's uid and gid to ones the runner does not have, rather than
# chowning, which needs root.

linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="the check runs on Linux only"
)


@pytest.fixture
def image_is_another_user(monkeypatch):
    """The image runs as a uid and gid other than the runner's."""
    monkeypatch.setattr(init_cmd, "IMAGE_UID", os.getuid() + 1)
    monkeypatch.setattr(init_cmd, "IMAGE_GID", os.getgid() + 1)


@pytest.fixture
def image_is_this_user(monkeypatch):
    """The image runs as the runner's uid and gid."""
    monkeypatch.setattr(init_cmd, "IMAGE_UID", os.getuid())
    monkeypatch.setattr(init_cmd, "IMAGE_GID", os.getgid())


def _secrets(home):
    return home / ".runtime" / "secrets"


# The directories the backend image writes to besides the filestore, whose fix
# is recursive, in the order the check reports them.
FILESTORE = ".runtime/env/default/filestore"
WRITTEN_DIRS = (".runtime/env/default/logs", ".runtime/database/backups/prod")


@pytest.fixture
def fix_operands(split_fix):
    """
    The paths a warning's fix gives to the image's uid, as a shell splits
    the command: ``(chown operands, chown -R operands)``.
    """

    def operands(warning):
        fix = warning.split("Give them to that uid (", 1)[1].rsplit("), or run", 1)[0]
        return split_fix(fix)

    return operands


@linux_only
def test_secrets_another_user_made_are_unreadable_to_the_image(
    cli_runner, tmp_path, image_is_another_user
):
    home = tmp_path / "home"
    _init(cli_runner, home)

    unreadable = init_cmd.secrets_unreadable_by_image(_secrets(home))

    assert unreadable == [(name, os.getuid()) for name in IMAGE_READ_SECRETS]
    # The ones the backend mounts: the generated secrets and the Turnstile key.
    assert set(GENERATED_SECRETS) <= set(IMAGE_READ_SECRETS)
    assert "turnstile_secret_key.txt" in IMAGE_READ_SECRETS
    # Not the TLS files nor the CA, which the frontend reads as root.
    assert "origin_pull_ca.pem" not in IMAGE_READ_SECRETS


@linux_only
def test_secrets_the_images_user_made_are_readable(
    cli_runner, tmp_path, image_is_this_user
):
    home = tmp_path / "home"
    _init(cli_runner, home)

    assert init_cmd.secrets_unreadable_by_image(_secrets(home)) == []


@linux_only
@pytest.mark.parametrize(
    ("mode", "same_group", "readable"),
    [
        (0o640, True, True),  # the image's group may read it
        (0o600, True, False),  # its group may not
        (0o604, False, True),  # anyone may read it
        (0o640, False, False),  # another group's file
    ],
)
def test_a_secret_of_another_owner_is_read_by_group_or_others(
    tmp_path, monkeypatch, mode, same_group, readable
):
    monkeypatch.setattr(init_cmd, "IMAGE_UID", os.getuid() + 1)
    monkeypatch.setattr(
        init_cmd, "IMAGE_GID", os.getgid() if same_group else os.getgid() + 1
    )
    secret = tmp_path / "postgres_password.txt"
    secret.write_text("x\n")
    secret.chmod(mode)

    unreadable = init_cmd.secrets_unreadable_by_image(tmp_path)

    assert unreadable == ([] if readable else [("postgres_password.txt", os.getuid())])


@linux_only
def test_the_owners_bits_decide_for_the_owner(
    tmp_path, monkeypatch, image_is_this_user
):
    # POSIX reads the owner's bits alone for the owner: a group-readable file
    # its owner may not read stays closed to the owner.
    secret = tmp_path / "jwt_secret_key.txt"
    secret.write_text("x\n")
    secret.chmod(0o044)

    unreadable = init_cmd.secrets_unreadable_by_image(tmp_path)

    assert unreadable == [("jwt_secret_key.txt", os.getuid())]


@linux_only
def test_a_missing_secret_is_not_reported(tmp_path, image_is_another_user):
    assert init_cmd.secrets_unreadable_by_image(tmp_path) == []


@linux_only
@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_secrets_are_not_checked_off_linux(
    cli_runner, tmp_path, monkeypatch, image_is_another_user, platform
):
    # Docker Desktop shares host files into its VM under rules of its own.
    home = tmp_path / "home"
    _init(cli_runner, home)
    monkeypatch.setattr(init_cmd.sys, "platform", platform)

    assert init_cmd.secrets_unreadable_by_image(_secrets(home)) == []


@linux_only
def test_init_warns_when_the_image_cannot_read_the_secrets(
    cli_runner, tmp_path, image_is_another_user, open_umask, fix_operands
):
    home = tmp_path / "home"

    result = _init(cli_runner, home)

    # A warning, not a refusal: an image built with its own MASCOPE_UID reads
    # them as that uid.
    assert result.exit_code == 0
    assert "WARNING" in result.stderr
    assert "postgres_password.txt" in result.stderr
    assert f"belong to uid {os.getuid()}" in result.stderr
    # The directories it writes to were made by the same user, at the umask.
    assert "cannot write to them" in result.stderr
    home = home.resolve()
    plain, recursive = fix_operands(result.stderr)
    assert plain == [str(_secrets(home) / name) for name in IMAGE_READ_SECRETS] + [
        str(home / d) for d in WRITTEN_DIRS
    ]
    # The uploads already in the filestore carry the old owner too.
    assert recursive == [str(home / FILESTORE)]


@linux_only
def test_init_warns_when_the_image_cannot_write_to_its_directories(
    cli_runner, tmp_path, image_is_this_user, fix_operands
):
    # The secrets are the image's own; one directory it writes to is closed.
    home = tmp_path / "home"
    _init(cli_runner, home)
    (home / WRITTEN_DIRS[0]).chmod(0o555)

    result = _init(cli_runner, home)

    assert result.exit_code == 0
    assert "WARNING" in result.stderr
    assert "The secrets" not in result.stderr
    assert f"The directories {home.resolve() / WRITTEN_DIRS[0]} belong to" in (
        result.stderr
    )
    assert fix_operands(result.stderr) == ([str(home.resolve() / WRITTEN_DIRS[0])], [])


@linux_only
def test_the_fix_quotes_a_home_with_a_space(
    cli_runner, tmp_path, image_is_another_user, open_umask, fix_operands
):
    home = (tmp_path / "my home; rm -rf x").resolve()

    result = _init(cli_runner, home)

    plain, recursive = fix_operands(result.stderr)
    assert plain == [str(_secrets(home) / name) for name in IMAGE_READ_SECRETS] + [
        str(home / d) for d in WRITTEN_DIRS
    ]
    assert recursive == [str(home / FILESTORE)]
    # Never the Postgres data directory, which the postgres image's user owns,
    # nor the transfer directory, nor anything recursive over the home.
    for path in plain + recursive:
        assert not path.startswith(str(home / ".runtime" / "database" / "prod"))
        assert not path.startswith(str(home / ".runtime" / "database" / "transfer"))
        assert path not in (str(home), str(home / ".runtime"))


@linux_only
@pytest.mark.parametrize(
    ("mode", "same_group", "writable"),
    [
        (0o775, True, True),  # the image's group may write to it
        (0o755, True, False),  # its group may not
        (0o765, True, False),  # write without search takes no new file
        (0o757, False, True),  # anyone may write to it
        (0o776, False, False),  # nor without the others' search bit
        (0o775, False, False),  # another group's directory
    ],
)
def test_a_directory_of_another_owner_is_written_by_group_or_others(
    tmp_path, monkeypatch, mode, same_group, writable
):
    monkeypatch.setattr(init_cmd, "IMAGE_UID", os.getuid() + 1)
    monkeypatch.setattr(
        init_cmd, "IMAGE_GID", os.getgid() if same_group else os.getgid() + 1
    )
    logs = tmp_path / WRITTEN_DIRS[0]
    logs.mkdir(parents=True)
    logs.chmod(mode)

    unwritable = init_cmd.dirs_unwritable_by_image(tmp_path)

    assert unwritable == (
        [] if writable else [init_cmd.UnwritableDir(logs, os.getuid(), False)]
    )


@linux_only
@pytest.mark.parametrize(
    ("mode", "writable"), [(0o300, True), (0o577, False), (0o277, False)]
)
def test_the_owners_bits_decide_for_a_directorys_owner(
    tmp_path, image_is_this_user, mode, writable
):
    backups = tmp_path / WRITTEN_DIRS[1]
    backups.mkdir(parents=True)
    backups.chmod(mode)
    try:
        unwritable = init_cmd.dirs_unwritable_by_image(tmp_path)
    finally:
        backups.chmod(0o700)

    assert unwritable == (
        [] if writable else [init_cmd.UnwritableDir(backups, os.getuid(), False)]
    )


@linux_only
def test_a_symlinked_filestore_is_checked_where_it_is(tmp_path, image_is_another_user):
    # A filestore on a data volume: the link's own bits say nothing.
    volume = tmp_path / "volume" / "store"
    volume.mkdir(parents=True)
    volume.chmod(0o755)
    link = tmp_path / "home" / FILESTORE
    link.parent.mkdir(parents=True)
    link.symlink_to(volume)

    unwritable = init_cmd.dirs_unwritable_by_image(tmp_path / "home")

    assert unwritable == [init_cmd.UnwritableDir(volume, os.getuid(), True)]


@linux_only
def test_a_filestore_of_its_own_is_checked_and_not_the_envs(
    tmp_path, image_is_another_user
):
    own = tmp_path / "store"
    own.mkdir()
    own.chmod(0o755)
    env_store = tmp_path / "home" / ".runtime" / "env" / "site" / "filestore"
    env_store.mkdir(parents=True)
    env_store.chmod(0o755)
    logs = env_store.parent / "logs"
    logs.mkdir()
    logs.chmod(0o755)

    unwritable = init_cmd.dirs_unwritable_by_image(tmp_path / "home", "site", own)

    assert unwritable == [
        init_cmd.UnwritableDir(own, os.getuid(), True),
        init_cmd.UnwritableDir(logs, os.getuid(), False),
    ]


@linux_only
def test_a_missing_directory_is_not_reported(tmp_path, image_is_another_user):
    assert init_cmd.dirs_unwritable_by_image(tmp_path) == []


@linux_only
@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_directories_are_not_checked_off_linux(
    cli_runner, tmp_path, monkeypatch, image_is_another_user, open_umask, platform
):
    home = tmp_path / "home"
    _init(cli_runner, home)
    monkeypatch.setattr(init_cmd.sys, "platform", platform)

    assert init_cmd.dirs_unwritable_by_image(home) == []


@linux_only
def test_init_is_quiet_when_the_image_can_read_the_secrets(
    cli_runner, tmp_path, image_is_this_user
):
    result = _init(cli_runner, tmp_path / "home")

    assert result.exit_code == 0
    assert "WARNING" not in result.output
