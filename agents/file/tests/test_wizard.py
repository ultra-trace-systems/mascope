"""Unit tests for the File Agent setup wizard.

Hermetic: prompts and HTTP calls are monkeypatched.
"""

import pytest

from mascope_file_agent import wizard


class FakeResponse:
    def __init__(self, status_code, content_type="application/json"):
        self.status_code = status_code
        self.headers = {"content-type": content_type}


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
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(wizard, "run_pairing", lambda host, verify=True: "paired-token")
    monkeypatch.setattr(
        wizard, "verify_connection", lambda host, token, verify=True: (True, "")
    )

    settings = wizard.run_setup_wizard({"mask": "*.raw", "timeout": 3})

    assert settings["host"] == "mascope.example.com"
    assert settings["access_token"] == "paired-token"
    assert settings["source"] == str(source)
    assert settings["recursive"] is False
    assert settings["verify_tls"] is True
    assert settings["mask"] == "*.raw"
    assert settings["timeout"] == 3
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
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(wizard, "run_pairing", lambda host, verify=True: "paired-token")
    monkeypatch.setattr(
        wizard, "verify_connection", lambda host, token, verify=True: (True, "")
    )

    settings = wizard.run_setup_wizard({"mask": "*.raw", "timeout": 3})
    assert settings["recursive"] is True


def test_run_setup_wizard_tls_verification_can_be_disabled(monkeypatch, tmp_path):
    source = tmp_path / "watched"
    source.mkdir()
    captured = {}

    def fake_pairing(host, verify=True):
        captured["verify"] = verify
        return "paired-token"

    answers = iter(
        [
            "https://self-signed.example.com",  # server address
            "n",  # verify TLS: no (self-signed server)
            str(source),  # watched folder
            "",  # subfolders default
            "",  # mask default
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(wizard, "run_pairing", fake_pairing)
    monkeypatch.setattr(
        wizard, "verify_connection", lambda host, token, verify=True: (True, "")
    )

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
            "a",  # verification failed: pair again
            str(source),  # watched folder
            "",  # subfolders default (no)
            "",  # mask default
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(wizard, "run_pairing", lambda host, verify=True: next(tokens))
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
            "n",  # pairing did not complete - do not try again
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    # Pairing never completes; the user declines to retry and setup aborts.
    monkeypatch.setattr(wizard, "run_pairing", lambda host, verify=True: None)

    with pytest.raises(KeyboardInterrupt):
        wizard.run_setup_wizard({"mask": "*.raw", "timeout": 3})


def _post_sequence(monkeypatch, responses):
    """Queue fake requests.post responses; returns the list of calls made."""
    calls = []
    queue = iter(responses)

    def fake_post(url, json, verify, timeout):
        calls.append({"url": url, "json": json})
        return next(queue)

    monkeypatch.setattr(wizard.requests, "post", fake_post)
    return calls


class FakeJsonResponse(FakeResponse):
    def __init__(self, status_code, body=None):
        super().__init__(status_code)
        self._body = body or {}

    def json(self):
        return self._body


def test_run_pairing_polls_until_approved(monkeypatch, capsys):
    monkeypatch.setattr(wizard.time, "sleep", lambda seconds: None)
    calls = _post_sequence(
        monkeypatch,
        [
            FakeJsonResponse(
                200,
                {
                    "user_code": "BCD-234",
                    "device_code": "d" * 32,
                    "expires_in": 600,
                    "interval": 5,
                },
            ),
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
    assert calls[1]["url"].endswith("/api/auth/pairing/poll")


def test_run_pairing_expired(monkeypatch):
    monkeypatch.setattr(wizard.time, "sleep", lambda seconds: None)
    _post_sequence(
        monkeypatch,
        [
            FakeJsonResponse(
                200,
                {
                    "user_code": "BCD-234",
                    "device_code": "d" * 32,
                    "expires_in": 600,
                    "interval": 5,
                },
            ),
            FakeJsonResponse(200, {"status": "expired"}),
        ],
    )
    assert wizard.run_pairing("mascope.example.com") is None


def test_start_pairing_unsupported_server(monkeypatch, capsys):
    _post_sequence(monkeypatch, [FakeJsonResponse(404)])
    assert wizard.start_pairing("mascope.example.com") is None
    assert "does not support pairing" in capsys.readouterr().out


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
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(wizard, "run_pairing", lambda host, verify=True: "paired-token")
    monkeypatch.setattr(
        wizard, "verify_connection", lambda host, token, verify=True: (True, "")
    )

    settings = wizard.run_setup_wizard({"mask": "*.raw", "timeout": 3})
    assert settings["source"] == str(source)
    assert source.is_dir()
