"""Unit tests for the File Agent setup wizard.

Hermetic: prompts and HTTP calls are monkeypatched.

The wizard asks its questions in this order: server address, TLS
verification, watched folder, subfolders, file pattern, instrument name,
then pairing. A prefix question follows the instrument name only when the
watched folder's files do not already start with a name the server reads.
"""

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


def _run_with(monkeypatch, tmp_path, answers, settings=None, captured=None):
    """Run the wizard against a fresh watched folder with the given answers.

    The folder and its files are created by the caller through ``tmp_path``;
    the answers cover everything after the TLS question.
    """
    source = tmp_path / "watched"
    source.mkdir(exist_ok=True)
    sequence = iter(["mascope.example.com", "", str(source), "", "", *answers])
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


def test_a_configured_prefix_is_kept(monkeypatch, tmp_path):
    settings = _run_with(
        monkeypatch,
        tmp_path,
        ["Orbi-Lab2"],
        settings={"filename_prefix": "site1_"},
    )
    assert settings["filename_prefix"] == "site1_"


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
