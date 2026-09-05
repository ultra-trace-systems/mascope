"""Unit tests for the File Agent setup wizard.

Hermetic: prompts and HTTP calls are monkeypatched.

The wizard asks its questions in this order: server address, TLS
verification, watched folder, subfolders, file pattern, instrument name,
then pairing. A prefix question follows the instrument name only when the
watched folder's files do not already start with a name the server reads.
"""

import os
import time

import pytest

from mascope_file_agent import __version__, wizard


class FakeResponse:
    def __init__(self, status_code, content_type="application/json"):
        self.status_code = status_code
        self.headers = {"content-type": content_type}


def _pairing_stub(token="paired-token", captured=None):
    """A run_pairing replacement returning ``token`` and recording its args."""

    def run_pairing(host, verify=True, instrument=None):
        if captured is not None:
            captured.update(host=host, verify=verify, instrument=instrument)
        return token

    return run_pairing


def _connection_ok(monkeypatch):
    monkeypatch.setattr(
        wizard, "verify_connection", lambda host, token, verify=True: (True, "")
    )


@pytest.fixture(autouse=True)
def _older_server():
    """Each test starts against a server that announced no capabilities."""
    wizard._server_capabilities.clear()
    yield
    wizard._server_capabilities.clear()


def _server_files_by_report(monkeypatch):
    """A pairing stub whose server files uploads under the reported instrument."""

    def run_pairing(host, verify=True, instrument=None):
        wizard._server_capabilities[wizard.FILES_UNDER_REPORTED_INSTRUMENT] = True
        return "paired-token"

    monkeypatch.setattr(wizard, "run_pairing", run_pairing)


def test_verify_connection_accepts_200_json(monkeypatch):
    captured = {}

    def fake_get(url, params, headers, verify, timeout):
        captured.update(url=url, headers=headers)
        return FakeResponse(200)

    monkeypatch.setattr(wizard.requests, "get", fake_get)
    ok, message = wizard.verify_connection("mascope.example.com", "tok")
    assert ok and message == ""
    assert captured["url"] == "https://mascope.example.com/api/sample/files"
    assert captured["headers"]["Authorization"] == "Bearer tok"
    assert captured["headers"]["X-Service-Name"] == "file-agent"
    # The version rides on every request the agent makes, this one included.
    assert captured["headers"]["X-Agent-Version"] == __version__


def test_verify_connection_rejects_html_200(monkeypatch):
    # A single-page-app server (e.g. the Vite frontend dev server) answers
    # any GET with the app page and 200; that must not pass verification.
    monkeypatch.setattr(
        wizard.requests,
        "get",
        lambda *a, **k: FakeResponse(200, content_type="text/html; charset=utf-8"),
    )
    ok, message = wizard.verify_connection("localhost:5173", "tok")
    assert not ok
    assert "does not look like the Mascope API" in message
    assert "http://localhost:8090" in message


@pytest.mark.parametrize("status", [401, 403])
def test_verify_connection_rejected_token(monkeypatch, status):
    monkeypatch.setattr(wizard.requests, "get", lambda *a, **k: FakeResponse(status))
    ok, message = wizard.verify_connection("mascope.example.com", "bad")
    assert not ok
    assert "rejected the access token" in message


def test_verify_connection_unreachable(monkeypatch):
    def fake_get(*args, **kwargs):
        raise wizard.requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(wizard.requests, "get", fake_get)
    ok, message = wizard.verify_connection("mascope.example.com", "tok")
    assert not ok
    assert "Could not connect" in message


def test_run_setup_wizard_happy_path(monkeypatch, tmp_path, capsys):
    source = tmp_path / "watched"
    source.mkdir()
    answers = iter(
        [
            "https://mascope.example.com/",  # server address (normalized)
            "",  # verify TLS: accept default (yes)
            str(source),  # watched folder
            "",  # subfolders: accept default (no)
            "",  # mask: accept default
            "",  # instrument: skip
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(wizard, "run_pairing", _pairing_stub())
    _connection_ok(monkeypatch)

    settings = wizard.run_setup_wizard({"mask": "*.raw", "timeout": 3})

    assert settings["host"] == "mascope.example.com"
    assert settings["access_token"] == "paired-token"
    assert settings["source"] == str(source)
    assert settings["recursive"] is False
    assert settings["verify_tls"] is True
    assert settings["mask"] == "*.raw"
    assert settings["timeout"] == 3
    assert settings["instrument"] == ""
    assert settings["filename_prefix"] == ""
    assert "accepted the access token" in capsys.readouterr().out


def test_run_setup_wizard_enables_subfolder_watching(monkeypatch, tmp_path):
    source = tmp_path / "watched"
    source.mkdir()
    answers = iter(
        [
            "mascope.example.com",  # server address
            "",  # verify TLS: default (yes)
            str(source),  # watched folder
            "y",  # subfolders: yes
            "",  # mask default
            "",  # instrument: skip
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(wizard, "run_pairing", _pairing_stub())
    _connection_ok(monkeypatch)

    settings = wizard.run_setup_wizard({"mask": "*.raw", "timeout": 3})
    assert settings["recursive"] is True


def test_run_setup_wizard_tls_verification_can_be_disabled(monkeypatch, tmp_path):
    source = tmp_path / "watched"
    source.mkdir()
    captured = {}
    answers = iter(
        [
            "https://self-signed.example.com",  # server address
            "n",  # verify TLS: no (self-signed server)
            str(source),  # watched folder
            "",  # subfolders default
            "",  # mask default
            "",  # instrument: skip
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(wizard, "run_pairing", _pairing_stub(captured=captured))
    _connection_ok(monkeypatch)

    settings = wizard.run_setup_wizard({"mask": "*.raw", "timeout": 3})
    assert settings["verify_tls"] is False
    # The choice is threaded into pairing (and into verification).
    assert captured["verify"] is False


def test_run_setup_wizard_re_pairs_on_bad_token(monkeypatch, tmp_path):
    source = tmp_path / "watched"
    source.mkdir()
    tokens = iter(["stale-token", "fresh-token"])
    answers = iter(
        [
            "mascope.example.com",  # server address
            "",  # verify TLS default (yes)
            str(source),  # watched folder
            "",  # subfolders default (no)
            "",  # mask default
            "",  # instrument: skip
            "a",  # verification failed: pair again
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(
        wizard,
        "run_pairing",
        lambda host, verify=True, instrument=None: next(tokens),
    )
    monkeypatch.setattr(
        wizard,
        "verify_connection",
        lambda host, token, verify=True: (
            token == "fresh-token",
            "The server rejected the access token.",
        ),
    )

    settings = wizard.run_setup_wizard({"mask": "*.raw", "timeout": 3})
    assert settings["access_token"] == "fresh-token"


def test_run_setup_wizard_pairing_retry_then_give_up(monkeypatch, tmp_path):
    source = tmp_path / "watched"
    source.mkdir()
    answers = iter(
        [
            "mascope.example.com",  # server address
            "",  # verify TLS default (yes)
            str(source),  # watched folder
            "",  # subfolders default (no)
            "",  # mask default
            "",  # instrument: skip
            "n",  # pairing did not complete - do not try again
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    # Pairing never completes; the user declines to retry and setup aborts.
    monkeypatch.setattr(wizard, "run_pairing", _pairing_stub(token=None))

    with pytest.raises(KeyboardInterrupt):
        wizard.run_setup_wizard({"mask": "*.raw", "timeout": 3})


def test_run_setup_wizard_creates_missing_source(monkeypatch, tmp_path):
    source = tmp_path / "new-folder"
    answers = iter(
        [
            "mascope.example.com",
            "",  # verify TLS default (yes)
            str(source),  # does not exist yet
            "y",  # create it
            "",  # subfolders default (no)
            "",  # mask default
            "",  # instrument: skip
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(wizard, "run_pairing", _pairing_stub())
    _connection_ok(monkeypatch)

    settings = wizard.run_setup_wizard({"mask": "*.raw", "timeout": 3})
    assert settings["source"] == str(source)
    assert source.is_dir()


# --- the instrument name and the prefix offer ---


def _run_with(
    monkeypatch, tmp_path, answers, settings=None, captured=None, recursive=""
):
    """Run the wizard against a fresh watched folder with the given answers.

    The folder and its files are created by the caller through ``tmp_path``;
    the answers cover everything after the file-pattern question.
    """
    source = tmp_path / "watched"
    source.mkdir(exist_ok=True)
    sequence = iter(["mascope.example.com", "", str(source), recursive, "", *answers])
    monkeypatch.setattr("builtins.input", lambda prompt: next(sequence))
    monkeypatch.setattr(wizard, "run_pairing", _pairing_stub(captured=captured))
    _connection_ok(monkeypatch)
    return wizard.run_setup_wizard({"mask": "*.raw", "timeout": 3, **(settings or {})})


def test_the_instrument_name_reaches_pairing(monkeypatch, tmp_path):
    captured = {}
    # An empty folder cannot say what the files look like, so the wizard asks;
    # the default answer is that they already start with the instrument name.
    settings = _run_with(monkeypatch, tmp_path, ["Orbi-Lab2", ""], captured=captured)

    assert settings["instrument"] == "Orbi-Lab2"
    assert settings["filename_prefix"] == ""
    assert captured["instrument"] == "Orbi-Lab2"


def test_skipping_the_instrument_pairs_without_one(monkeypatch, tmp_path):
    captured = {}
    _run_with(monkeypatch, tmp_path, [""], captured=captured)
    assert captured["instrument"] is None


def test_the_instrument_prompt_rejects_names_the_server_would_not(
    monkeypatch, tmp_path, capsys
):
    settings = _run_with(monkeypatch, tmp_path, ["orbi lab 2", "Orbi-Lab2", ""])
    assert settings["instrument"] == "Orbi-Lab2"
    assert "letters, digits and hyphens" in capsys.readouterr().out


def test_an_empty_folder_offers_a_prefix_when_the_names_lack_one(monkeypatch, tmp_path):
    # No files to look at, the operator says the names do not start with the
    # instrument, and accepts the prefix the server needs to file them.
    settings = _run_with(monkeypatch, tmp_path, ["Orbi-Lab2", "n", ""])
    assert settings["filename_prefix"] == "Orbi-Lab2_"


def test_existing_files_suggest_the_name_the_server_already_uses(monkeypatch, tmp_path):
    source = tmp_path / "watched"
    source.mkdir()
    (source / "Orbi-Lab2_2026.09.03-10h12m01s_ambient.raw").write_bytes(b"x")
    # The suggestion is the default, so Enter keeps names consistent with the
    # data already filed; and since the files carry it, no prefix question.
    settings = _run_with(monkeypatch, tmp_path, [""])
    assert settings["instrument"] == "Orbi-Lab2"
    assert settings["filename_prefix"] == ""


def test_a_different_name_leaves_existing_data_alone(monkeypatch, tmp_path, capsys):
    source = tmp_path / "watched"
    source.mkdir()
    (source / "Orbi-A_2026.09.03-10h12m01s_ambient.raw").write_bytes(b"x")
    # The files already start with a name the server reads. A different
    # instrument name is reported alongside; the file names are not touched,
    # so nothing moves to a new acquisition workspace.
    settings = _run_with(monkeypatch, tmp_path, ["Orbi-B"])
    assert settings["instrument"] == "Orbi-B"
    assert settings["filename_prefix"] == ""
    assert "nothing changes for existing data" in capsys.readouterr().out


def test_files_without_an_instrument_get_the_prefix_offer(monkeypatch, tmp_path):
    source = tmp_path / "watched"
    source.mkdir()
    (source / "ambient_2026.09.03-10h12m01s.raw").write_bytes(b"x")
    # 'ambient' is not a name the server reads, so the offer is made and
    # accepted with the default answer.
    settings = _run_with(monkeypatch, tmp_path, ["Orbi-Lab2", ""])
    assert settings["filename_prefix"] == "Orbi-Lab2_"


def test_a_prefix_the_server_would_refuse_is_not_offered(monkeypatch, tmp_path, capsys):
    # A current server files uploads only under a name containing 'orbi' or
    # 'tof'; prefixing 'Lab2_' would make every upload fail, so the wizard
    # declines to offer it and says why.
    settings = _run_with(monkeypatch, tmp_path, ["Lab2", "n"])
    assert settings["instrument"] == "Lab2"
    assert settings["filename_prefix"] == ""
    assert "No prefix is offered" in capsys.readouterr().out


def test_a_configured_prefix_that_still_works_is_kept(monkeypatch, tmp_path):
    source = tmp_path / "watched"
    source.mkdir()
    (source / "ambient_2026.09.03-10h12m01s.raw").write_bytes(b"x")
    # 'Orbi-A_' in front of 'ambient_...' files is filed under Orbi-A, which
    # the server reads, so there is nothing to fix and the prefix stands.
    settings = _run_with(
        monkeypatch,
        tmp_path,
        ["Orbi-A"],
        settings={"filename_prefix": "Orbi-A_"},
    )
    assert settings["filename_prefix"] == "Orbi-A_"


def test_a_prefix_left_from_another_instrument_is_reported(
    monkeypatch, tmp_path, capsys
):
    source = tmp_path / "watched"
    source.mkdir()
    (source / "ambient_2026.09.03-10h12m01s.raw").write_bytes(b"x")
    # The configured prefix files uploads under 'site1', which the server
    # cannot read - so every upload is refused today while the machine would
    # report itself as Orbi-Lab2. Setup says so and offers to replace it.
    settings = _run_with(
        monkeypatch,
        tmp_path,
        ["Orbi-Lab2", ""],
        settings={"filename_prefix": "site1_"},
    )
    assert settings["filename_prefix"] == "Orbi-Lab2_"
    out = capsys.readouterr().out
    assert "cannot read 'site1'" in out
    assert "configured prefix 'site1_'" in out


def test_a_prefix_is_not_offered_when_declined(monkeypatch, tmp_path):
    source = tmp_path / "watched"
    source.mkdir()
    (source / "ambient_2026.09.03-10h12m01s.raw").write_bytes(b"x")
    # Declining leaves the configuration exactly as it was.
    settings = _run_with(monkeypatch, tmp_path, ["Orbi-Lab2", "n"])
    assert settings["filename_prefix"] == ""


def test_a_name_without_an_underscore_gets_the_prefix_offer(monkeypatch, tmp_path):
    source = tmp_path / "watched"
    source.mkdir()
    (source / "Orbion.raw").write_bytes(b"x")
    # The server reads the instrument off the whole name, extension included,
    # so 'Orbion.raw' is refused for the dot even though the stem alone would
    # be fine. Stripping the extension here would call this folder healthy.
    settings = _run_with(monkeypatch, tmp_path, ["Orbi-Lab2", ""])
    assert settings["filename_prefix"] == "Orbi-Lab2_"


def test_subfolders_are_searched_when_they_are_watched(monkeypatch, tmp_path):
    source = tmp_path / "watched"
    (source / "2026.09.03").mkdir(parents=True)
    (source / "2026.09.03" / "Orbi-Lab2_10h12m01s.raw").write_bytes(b"x")
    # The subfolder answer is given before this scan runs, so a site whose
    # files live one level down is not treated as an empty folder.
    settings = _run_with(monkeypatch, tmp_path, [""], recursive="y")
    assert settings["instrument"] == "Orbi-Lab2"
    assert settings["filename_prefix"] == ""


def _touch(path, age_s=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    stamp = time.time() - age_s
    os.utime(path, (stamp, stamp))


def test_the_look_stops_two_levels_below_the_watched_folder(tmp_path):
    # An acquisition tree can be deep and enormous; the bound is what keeps
    # setup from walking all of it. Two levels reach a year/month layout.
    _touch(tmp_path / "2026" / "09" / "Orbi-Lab2_10h12m01s.raw")
    _touch(tmp_path / "2026" / "09" / "03" / "Orbi-Lab2_11h12m01s.raw")
    assert (
        wizard._folder_evidence(str(tmp_path), "*.raw", recursive=True)
        == "Orbi-Lab2_10h12m01s.raw"
    )
    _touch(tmp_path / "2025" / "12" / "31" / "Orbi-Lab2_23h59m59s.raw")
    (tmp_path / "2026" / "09" / "Orbi-Lab2_10h12m01s.raw").unlink()
    assert wizard._folder_evidence(str(tmp_path), "*.raw", recursive=True) is None


def test_the_look_stays_in_the_watched_folder_unless_subfolders_are_watched(tmp_path):
    _touch(tmp_path / "2026.09.03" / "Orbi-Lab2_10h12m01s.raw")
    assert wizard._folder_evidence(str(tmp_path), "*.raw") is None
    assert wizard._folder_evidence(str(tmp_path), "*.raw", recursive=True)


def test_the_agents_own_quarantine_folder_is_never_evidence(tmp_path):
    # failed_uploads holds names the server refused; reasoning from them
    # would offer a prefix to a folder whose live files need none.
    _touch(tmp_path / "failed_uploads" / "ambient_10h12m01s.raw")
    assert wizard._folder_evidence(str(tmp_path), "*.raw", recursive=True) is None


def test_the_look_gives_up_when_its_time_is_spent(tmp_path, monkeypatch):
    # A network share with years of files must not hang setup: past the
    # budget the look returns what it has, here nothing.
    _touch(tmp_path / "Orbi-Lab2_10h12m01s.raw")
    monkeypatch.setattr(wizard, "_EVIDENCE_TIME_BUDGET_S", -1.0)
    assert wizard._folder_evidence(str(tmp_path), "*.raw") is None


def test_the_look_examines_at_most_the_configured_number_of_files(
    tmp_path, monkeypatch
):
    for i in range(5):
        _touch(tmp_path / f"Orbi-Lab2_{i}.raw", age_s=i)
    monkeypatch.setattr(wizard, "_EVIDENCE_MAX_FILES", 2)
    stats = []
    original = os.DirEntry.stat

    def counting_stat(entry, *args, **kwargs):
        stats.append(entry.name)
        return original(entry, *args, **kwargs)

    monkeypatch.setattr(os.DirEntry, "stat", counting_stat)
    assert wizard._folder_evidence(str(tmp_path), "*.raw") in stats
    assert len(stats) == 2


def test_newest_named_subfolders_are_looked_at_first(tmp_path, monkeypatch):
    # Acquisition folders are commonly named by date, so with the file cap
    # in play the most recent data is reached before the cap is.
    _touch(tmp_path / "2025" / "Orbi-Old_10h12m01s.raw", age_s=10)
    _touch(tmp_path / "2026" / "Orbi-New_10h12m01s.raw")
    monkeypatch.setattr(wizard, "_EVIDENCE_MAX_FILES", 1)
    assert (
        wizard._folder_evidence(str(tmp_path), "*.raw", recursive=True)
        == "Orbi-New_10h12m01s.raw"
    )


def test_a_file_that_vanishes_mid_look_is_skipped(tmp_path, monkeypatch):
    _touch(tmp_path / "Orbi-Lab2_10h12m01s.raw")
    _touch(tmp_path / "Orbi-Lab2_11h12m01s.raw")
    original = os.DirEntry.stat

    def vanishing_stat(entry, *args, **kwargs):
        if entry.name.endswith("11h12m01s.raw"):
            raise FileNotFoundError(entry.name)
        return original(entry, *args, **kwargs)

    monkeypatch.setattr(os.DirEntry, "stat", vanishing_stat)
    assert wizard._folder_evidence(str(tmp_path), "*.raw") == "Orbi-Lab2_10h12m01s.raw"


def test_an_empty_folder_asks_for_an_example_name(monkeypatch, tmp_path):
    # Nothing on disk to reason from, so setup asks for one name and runs it
    # through the same rule rather than asking the operator to judge.
    settings = _run_with(
        monkeypatch, tmp_path, ["Orbi-Lab2", "ambient_2026.09.03.raw", ""]
    )
    assert settings["filename_prefix"] == "Orbi-Lab2_"


def test_an_empty_folder_with_no_example_changes_nothing(monkeypatch, tmp_path, capsys):
    settings = _run_with(monkeypatch, tmp_path, ["Orbi-Lab2", ""])
    assert settings["filename_prefix"] == ""
    assert "set 'filename_prefix' by hand" in capsys.readouterr().out


def test_no_prefix_is_offered_when_the_server_files_by_report(
    monkeypatch, tmp_path, capsys
):
    source = tmp_path / "watched"
    source.mkdir()
    (source / "ambient_2026.09.03-10h12m01s.raw").write_bytes(b"x")
    # Against an older server this folder gets the prefix offer; this server
    # files under the reported name, so the file names can stay as the
    # acquisition software wrote them and no question is asked.
    sequence = iter(["mascope.example.com", "", str(source), "", "", "Orbi-Lab2"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(sequence))
    _server_files_by_report(monkeypatch)
    _connection_ok(monkeypatch)

    settings = wizard.run_setup_wizard({"mask": "*.raw", "timeout": 3})

    assert settings["instrument"] == "Orbi-Lab2"
    assert settings["filename_prefix"] == ""
    assert "need not carry it" in capsys.readouterr().out


def test_a_prefix_already_configured_survives_a_server_that_files_by_report(
    monkeypatch, tmp_path
):
    # The server sees a name that already starts with the instrument and adds
    # nothing, so the prefix is harmless; setup leaves it rather than asking.
    source = tmp_path / "watched"
    source.mkdir()
    sequence = iter(["mascope.example.com", "", str(source), "", "", "Orbi-Lab2"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(sequence))
    _server_files_by_report(monkeypatch)
    _connection_ok(monkeypatch)

    settings = wizard.run_setup_wizard(
        {"mask": "*.raw", "timeout": 3, "filename_prefix": "Orbi-Lab2_"}
    )

    assert settings["filename_prefix"] == "Orbi-Lab2_"


def test_a_configured_instrument_can_be_cleared(monkeypatch, tmp_path):
    # Enter keeps the configured name, so without an explicit answer there
    # would be no way to remove one but hand-editing config.toml.
    settings = _run_with(
        monkeypatch, tmp_path, [wizard.CLEAR_ANSWER], settings={"instrument": "Orbi-A"}
    )
    assert settings["instrument"] == ""


def test_an_invalid_configured_instrument_is_not_offered_back(
    monkeypatch, tmp_path, capsys
):
    # Offering it as the default would make Enter re-submit a name the agent
    # refuses to start with, and the prompt would never accept an answer.
    settings = _run_with(
        monkeypatch, tmp_path, [""], settings={"instrument": "orbi lab"}
    )
    assert settings["instrument"] == ""
    assert "is not one the server accepts" in capsys.readouterr().out


def test_a_file_that_vanishes_mid_scan_is_skipped(monkeypatch, tmp_path):
    source = tmp_path / "watched"
    source.mkdir()
    (source / "ambient_2026.09.03-10h12m01s.raw").write_bytes(b"x")

    def vanishing(path):
        raise OSError("gone")

    monkeypatch.setattr(wizard.os.path, "getmtime", vanishing)
    # An acquisition folder is written to while setup runs; a file that goes
    # away between the listing and the stat must not end the wizard.
    settings = _run_with(monkeypatch, tmp_path, ["Orbi-Lab2", ""])
    assert settings["instrument"] == "Orbi-Lab2"


# --- pairing requests ---


def _post_sequence(monkeypatch, responses):
    """Queue fake requests.post responses; returns the list of calls made."""
    calls = []
    queue = iter(responses)

    def fake_post(url, json, verify, timeout, headers=None):
        calls.append({"url": url, "json": json, "headers": headers or {}})
        return next(queue)

    monkeypatch.setattr(wizard.requests, "post", fake_post)
    return calls


class FakeJsonResponse(FakeResponse):
    def __init__(self, status_code, body=None):
        super().__init__(status_code)
        self._body = body or {}

    def json(self):
        return self._body


def _started():
    return FakeJsonResponse(
        200,
        {
            "user_code": "BCD-234",
            "device_code": "d" * 32,
            "expires_in": 600,
            "interval": 5,
        },
    )


def test_run_pairing_polls_until_approved(monkeypatch, capsys):
    monkeypatch.setattr(wizard.time, "sleep", lambda seconds: None)
    calls = _post_sequence(
        monkeypatch,
        [
            _started(),
            FakeJsonResponse(200, {"status": "pending", "interval": 5}),
            FakeJsonResponse(
                200, {"status": "approved", "access_token": "paired-token"}
            ),
        ],
    )
    token = wizard.run_pairing("mascope.example.com")
    assert token == "paired-token"
    out = capsys.readouterr().out
    assert "BCD-234" in out
    assert "Pair an agent" in out
    assert calls[0]["url"].endswith("/api/auth/pairing/start")
    assert calls[0]["json"]["service_name"] == "file-agent"
    # The version is reported both in the start request, where a new server
    # stores it on the paired machine, and as a header on every request.
    assert calls[0]["json"]["agent_version"] == __version__
    assert "instrument" not in calls[0]["json"]
    assert calls[0]["headers"]["X-Agent-Version"] == __version__
    assert calls[1]["url"].endswith("/api/auth/pairing/poll")
    # A start response without capabilities is an older server.
    assert wizard.server_files_under_reported_instrument() is False


def test_run_pairing_keeps_what_the_server_says_it_does(monkeypatch):
    monkeypatch.setattr(wizard.time, "sleep", lambda seconds: None)
    started = _started()
    started._body["capabilities"] = {"files_uploads_under_reported_instrument": True}
    _post_sequence(
        monkeypatch,
        [
            started,
            FakeJsonResponse(
                200, {"status": "approved", "access_token": "paired-token"}
            ),
        ],
    )
    assert wizard.run_pairing("mascope.example.com") == "paired-token"
    assert wizard.server_files_under_reported_instrument() is True


def test_run_pairing_reports_the_instrument(monkeypatch):
    monkeypatch.setattr(wizard.time, "sleep", lambda seconds: None)
    calls = _post_sequence(
        monkeypatch,
        [
            _started(),
            FakeJsonResponse(
                200, {"status": "approved", "access_token": "paired-token"}
            ),
        ],
    )
    assert wizard.run_pairing("mascope.example.com", instrument="Orbi-Lab2")
    assert calls[0]["json"]["instrument"] == "Orbi-Lab2"


def test_run_pairing_expired(monkeypatch):
    monkeypatch.setattr(wizard.time, "sleep", lambda seconds: None)
    _post_sequence(
        monkeypatch,
        [_started(), FakeJsonResponse(200, {"status": "expired"})],
    )
    assert wizard.run_pairing("mascope.example.com") is None


def test_start_pairing_unsupported_server(monkeypatch, capsys):
    _post_sequence(monkeypatch, [FakeJsonResponse(404)])
    assert wizard.start_pairing("mascope.example.com") is None
    assert "does not support pairing" in capsys.readouterr().out


def test_a_long_agent_version_is_clipped_before_pairing(monkeypatch):
    # A build stamped by `git describe` off a date-style release tag runs past
    # what the server stores. Clipped here rather than left for the server to
    # refuse: pairing is the only way a machine gets a credential, so failing
    # it over a version label would leave the machine unusable.
    monkeypatch.setattr(wizard.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(wizard, "__version__", "v2026.09.01-9b9e54d-394-g7e674438e")
    calls = _post_sequence(monkeypatch, [_started()])

    wizard.start_pairing("mascope.example.com")

    sent = calls[0]["json"]["agent_version"]
    assert len(sent) == wizard.AGENT_VERSION_MAX_LENGTH
    assert sent == "v2026.09.01-9b9e54d-394-g7e67443"


def test_cancelling_pairing_keeps_the_answers_given_before_it(monkeypatch, tmp_path):
    # Pairing comes last because it needs a second person at a browser.
    # Walking away to find one must not cost the six answers already given.
    source = tmp_path / "watched"
    source.mkdir()
    answers = iter(
        [
            "mascope.example.com",
            "",  # verify TLS default
            str(source),
            "y",  # subfolders
            "*.h5",  # mask
            "Orbi-Lab2",  # instrument
            # The prefix offer comes after pairing, so nothing else is asked
            # before the pairing attempt.
            "n",  # pairing did not complete - do not try again
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(wizard, "run_pairing", _pairing_stub(token=None))

    with pytest.raises(wizard.SetupCancelled) as excinfo:
        wizard.run_setup_wizard({"mask": "*.raw", "timeout": 3})

    kept = excinfo.value.settings
    assert kept["host"] == "mascope.example.com"
    assert kept["source"] == str(source)
    assert kept["recursive"] is True
    assert kept["mask"] == "*.h5"
    assert kept["instrument"] == "Orbi-Lab2"
    # Still a KeyboardInterrupt, so every existing handler treats it as the
    # cancellation it is.
    assert isinstance(excinfo.value, KeyboardInterrupt)


def test_a_stray_file_does_not_prefix_a_correctly_named_folder(monkeypatch, tmp_path):
    source = tmp_path / "watched"
    source.mkdir()
    (source / "Orbi-Lab2_2026.09.03-10h12m01s.raw").write_bytes(b"x")
    stray = source / "test.raw"
    stray.write_bytes(b"x")
    os.utime(stray, (time.time() + 60, time.time() + 60))
    # The stray file is the newest, but the folder plainly is filed under
    # Orbi-Lab2 already. Prefixing on that evidence would rename every future
    # acquisition to Orbi-Lab2_Orbi-Lab2_... - a new sample-name lineage, for
    # a folder that needed nothing.
    settings = _run_with(monkeypatch, tmp_path, [""])
    assert settings["instrument"] == "Orbi-Lab2"
    assert settings["filename_prefix"] == ""
