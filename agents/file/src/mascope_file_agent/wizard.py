"""Interactive first-run setup for the bundled File Agent.

Runs in the agent's console window when required settings are missing (or
when started with ``--setup``), prompting for the server address, the watched
folder and the instrument name, then pairing the machine. The credential is
verified against the server right away so typos surface during setup instead
of at the first upload.
"""

import glob
import os
import platform
import time

import requests
import urllib3

from mascope_file_agent import __version__
from mascope_file_agent.config import base_url, is_valid_instrument, normalize_host
from mascope_sdk import AGENT_VERSION_HEADER


# Suppress the warning urllib3 emits when a user has turned TLS verification
# off for a self-signed server; it does not fire when verification is on.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

VERIFY_TIMEOUT = 15  # seconds


#: Outcomes of :func:`check_credential`. REJECTED means the server answered
#: and refused this credential, which pairing fixes; UNREACHABLE covers every
#: way the question went unanswered - no network yet on a machine that just
#: booted, a server being restarted, a mistyped address - none of which pairing
#: would fix, and none of which should send anyone looking for a pairing code.
CREDENTIAL_OK = "ok"
CREDENTIAL_REJECTED = "rejected"
CREDENTIAL_UNREACHABLE = "unreachable"

#: What a current server needs to find in an instrument name to tell the
#: instrument type apart. A file whose name starts with anything else is
#: refused, so a prefix the setup adds has to satisfy it too.
_INSTRUMENT_TYPE_HINTS = ("orbi", "tof", "api")


def _headers(access_token: str | None = None) -> dict:
    """Headers for the wizard's own requests.

    The agent's version always, so the server can record which release a
    machine runs; the credential when there is one.

    :param access_token: The credential to present, if any
    :type access_token: str | None
    :return: Request headers
    :rtype: dict
    """
    headers = {AGENT_VERSION_HEADER: __version__}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
        headers["X-Service-Name"] = "file-agent"
    return headers


def check_credential(
    host: str, access_token: str, verify: bool = True
) -> tuple[str, str]:
    """Ask the server whether it still accepts this machine's credential.

    Calls a cheap authenticated endpoint with the file-agent service headers,
    exactly as uploads will.

    :param host: Normalized server host
    :type host: str
    :param access_token: The access token to check
    :type access_token: str
    :param verify: Whether to verify the server's TLS certificate
    :type verify: bool
    :return: (outcome, user-facing message when not ok)
    :rtype: tuple[str, str]
    """
    url = f"{base_url(host)}/api/sample/files"
    try:
        resp = requests.get(
            url,
            params={"page": 1, "limit": 1},
            headers=_headers(access_token),
            verify=verify,
            timeout=VERIFY_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        return False, f"The server at {base_url(host)} did not respond in time."
    except requests.exceptions.RequestException as e:
        return False, (
            f"Could not connect to {base_url(host)}.\n"
            f"Details: {e.__class__.__name__}: {e}"
        )
    if resp.status_code == 200:
        # A 200 alone is not proof of the API: a single-page-app server
        # (e.g. the Vite frontend dev server, which has no /api proxy)
        # answers any GET with the app's HTML page and 200. The real API
        # responds with JSON.
        content_type = resp.headers.get("content-type", "")
        if "json" not in content_type.lower():
            return CREDENTIAL_UNREACHABLE, (
                f"The address {base_url(host)} responded, but it does not "
                "look like the Mascope API (it returned a web page instead "
                "of data), so uploads would fail. In a development setup, "
                "use the backend address (e.g. http://localhost:8090) - the "
                "frontend dev server cannot receive uploads. In production, "
                "use the normal Mascope web app address."
            )
        return CREDENTIAL_OK, ""
    if resp.status_code in (401, 403):
        return CREDENTIAL_REJECTED, (
            "The server rejected the access token. Pair the agent again to "
            "get a fresh one."
        )
    return CREDENTIAL_UNREACHABLE, (
        f"Unexpected response from the server (HTTP {resp.status_code})."
    )


def verify_connection(
    host: str, access_token: str, verify: bool = True
) -> tuple[bool, str]:
    """Whether the server accepts this credential, for the setup wizard.

    :param host: Normalized server host
    :type host: str
    :param access_token: The access token to verify
    :type access_token: str
    :param verify: Whether to verify the server's TLS certificate
    :type verify: bool
    :return: (ok, user-facing error message when not ok)
    :rtype: tuple[bool, str]
    """
    outcome, message = check_credential(host, access_token, verify=verify)
    return outcome == CREDENTIAL_OK, message


def _prompt(label: str, default: str = "") -> str:
    """Prompt for a value, offering a default when one exists.

    :param label: Prompt label
    :type label: str
    :param default: Value returned on empty input
    :type default: str
    :return: The entered (or default) value, stripped
    :rtype: str
    """
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip() or default
        if value:
            return value
        print("  A value is required.")


def _prompt_optional(label: str, default: str = "") -> str:
    """Prompt for a value that may be left empty.

    :param label: Prompt label
    :type label: str
    :param default: Value returned on empty input
    :type default: str
    :return: The entered (or default) value, stripped; may be empty
    :rtype: str
    """
    suffix = f" [{default}]" if default else ""
    return input(f"{label}{suffix}: ").strip() or default


def _prompt_yes_no(label: str, default: bool = False) -> bool:
    """Prompt for a yes/no answer, offering a default.

    :param label: Prompt label
    :type label: str
    :param default: Value returned on empty input
    :type default: bool
    :return: The answer
    :rtype: bool
    """
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        answer = input(f"{label} {suffix}: ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def _prompt_source(default: str) -> str:
    """Prompt for the watched folder, offering to create it if missing.

    :param default: Previously configured folder, if any
    :type default: str
    :return: Path of an existing directory
    :rtype: str
    """
    while True:
        source = os.path.expandvars(
            os.path.expanduser(_prompt("Folder to watch for new data files", default))
        )
        if os.path.isdir(source):
            return source
        answer = input(f"  {source} does not exist. Create it? [y/N]: ").strip().lower()
        if answer == "y":
            try:
                os.makedirs(source, exist_ok=True)
                return source
            except OSError as e:
                print(f"  Could not create the folder: {e}")


def _server_reads_as_instrument(name: str) -> bool:
    """Whether a current server accepts ``name`` at the start of a file name.

    Mirrors the server's rule: the first underscore-separated segment must be
    letters, digits and hyphens and must contain one of the type hints. A
    later server takes the type from the file's contents and the instrument
    from the agent, which is what the instrument setting is for.

    :param name: The candidate name
    :type name: str
    :return: True when a current server would file uploads under it
    :rtype: bool
    """
    lowered = name.lower()
    return is_valid_instrument(name) and any(
        hint in lowered for hint in _INSTRUMENT_TYPE_HINTS
    )


def _newest_file_segment(source: str, mask: str) -> str | None:
    """The instrument segment of the newest file in the watched folder.

    :param source: The watched folder
    :type source: str
    :param mask: The file pattern to upload
    :type mask: str
    :return: The first underscore-separated segment of the newest matching
        file's name, or None when the folder holds none
    :rtype: str | None
    """
    candidates = [
        path
        for path in glob.glob(os.path.join(glob.escape(source), mask))
        if os.path.isfile(path)
    ]
    if not candidates:
        return None
    newest = max(candidates, key=os.path.getmtime)
    stem = os.path.splitext(os.path.basename(newest))[0]
    return stem.split("_")[0]


def _prompt_instrument(default: str, suggested: str | None) -> str:
    """Prompt for the instrument name, allowing it to be left empty.

    :param default: Previously configured name, if any
    :type default: str
    :param suggested: Name the watched folder's files already start with
    :type suggested: str | None
    :return: A valid instrument name, or an empty string
    :rtype: str
    """
    print(
        "\n"
        "The instrument name is reported when pairing and with each upload, so\n"
        "the server can file uploads under it. Letters, digits and hyphens,\n"
        "e.g. Orbi-Lab2. Leave it empty to skip."
    )
    if suggested and not default:
        print(f"Files in the watched folder start with '{suggested}'.")
    while True:
        value = _prompt_optional("Instrument name", default or suggested or "")
        if not value or is_valid_instrument(value):
            return value
        print("  Use letters, digits and hyphens only, at most 64 characters.")


def _offer_filename_prefix(
    source: str, mask: str, instrument: str, current_prefix: str
) -> str:
    """Offer to put the instrument name in front of uploaded file names.

    A current server reads the instrument from the start of each file name
    and refuses names it cannot read. Files that already start with a name it
    accepts need nothing. Files that do not get the instrument name prefixed
    when the operator agrees, which replaces the hand-edited ``filename_prefix``
    those sites needed before. A prefix that is already configured is kept.

    :param source: The watched folder
    :type source: str
    :param mask: The file pattern to upload
    :type mask: str
    :param instrument: The instrument name entered, possibly empty
    :type instrument: str
    :param current_prefix: The configured prefix, possibly empty
    :type current_prefix: str
    :return: The prefix to configure, possibly empty
    :rtype: str
    """
    if not instrument or current_prefix:
        return current_prefix
    segment = _newest_file_segment(source, mask)
    if segment is not None:
        if segment == instrument:
            return ""
        if _server_reads_as_instrument(segment):
            print(
                f"\nFiles in the folder start with '{segment}', which is the name\n"
                "the server files them under today. The instrument name is\n"
                "reported alongside it; nothing changes for existing data.\n"
            )
            return ""
        print(
            f"\nFiles in the folder start with '{segment}', which the server\n"
            "cannot read as an instrument name."
        )
    elif _prompt_yes_no(
        f"Do the file names from this instrument start with '{instrument}_'?",
        default=True,
    ):
        return ""
    if not _server_reads_as_instrument(instrument):
        print(
            "  No prefix is offered: this server release only files uploads under\n"
            "  an instrument name containing 'orbi' or 'tof' (e.g. Orbi-Lab2), and\n"
            f"  '{instrument}' has neither. Set 'filename_prefix' in the\n"
            "  configuration if the files need one.\n"
        )
        return ""
    if _prompt_yes_no(
        f"Add '{instrument}_' in front of every uploaded file name so the "
        "server can file them?",
        default=True,
    ):
        return f"{instrument}_"
    return ""


def start_pairing(
    host: str, verify: bool = True, instrument: str | None = None
) -> dict | None:
    """Request a pairing code from the server.

    :param host: Normalized server host
    :type host: str
    :param verify: Whether to verify the server's TLS certificate
    :type verify: bool
    :param instrument: Name of the instrument this machine watches, if set
    :type instrument: str | None
    :return: The pairing response (user_code, device_code, expires_in,
        interval), or None with an explanation printed when pairing is
        unavailable
    :rtype: dict | None
    """
    machine_name = platform.node()[:64] or None
    payload = {
        "service_name": "file-agent",
        "machine_name": machine_name,
        # Stored on the paired machine by a server that knows these fields
        # and shown to the approver; an older server drops them.
        "agent_version": __version__,
    }
    if instrument:
        payload["instrument"] = instrument
    try:
        resp = requests.post(
            f"{base_url(host)}/api/auth/pairing/start",
            json=payload,
            headers=_headers(),
            verify=verify,
            timeout=VERIFY_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        print(f"Could not reach the server: {e.__class__.__name__}: {e}")
        return None
    if resp.status_code == 404:
        print("This Mascope server does not support pairing (older version).")
        return None
    if resp.status_code != 200 or "json" not in resp.headers.get("content-type", ""):
        print(f"Unexpected response from the server (HTTP {resp.status_code}).")
        return None
    return resp.json()


def run_pairing(
    host: str, verify: bool = True, instrument: str | None = None
) -> str | None:
    """Interactive pairing: display the code and poll until approved.

    :param host: Normalized server host
    :type host: str
    :param verify: Whether to verify the server's TLS certificate
    :type verify: bool
    :param instrument: Name of the instrument this machine watches, if set
    :type instrument: str | None
    :return: The access token, or None when pairing did not complete
    :rtype: str | None
    """
    started = start_pairing(host, verify=verify, instrument=instrument)
    if not started:
        return None
    minutes = max(1, round(started["expires_in"] / 60))
    print(
        "\n"
        f"  Pairing code: {started['user_code']}\n"
        "\n"
        "  1. Log in to Mascope in your browser (editor role or higher)\n"
        "  2. Click your profile icon to open the sidebar\n"
        "  3. Under 'API Access Tokens', click 'Pair an agent'\n"
        "  4. Enter the code above and approve\n"
        "\n"
        f"Waiting for approval (expires in {minutes} min, Ctrl+C to cancel)...",
        end="",
        flush=True,
    )
    interval = max(1, int(started.get("interval", 5)))
    try:
        while True:
            time.sleep(interval)
            try:
                resp = requests.post(
                    f"{base_url(host)}/api/auth/pairing/poll",
                    json={"device_code": started["device_code"]},
                    headers=_headers(),
                    verify=verify,
                    timeout=VERIFY_TIMEOUT,
                )
            except requests.exceptions.RequestException:
                print("!", end="", flush=True)  # transient; keep polling
                continue
            if resp.status_code != 200:
                print("!", end="", flush=True)
                continue
            body = resp.json()
            if body["status"] == "approved":
                print("\nPaired - the agent received its access token.\n")
                return body["access_token"]
            if body["status"] == "expired":
                print("\nThe pairing code expired before it was approved.")
                return None
            print(".", end="", flush=True)
    except KeyboardInterrupt:
        print("\nPairing cancelled.")
        return None


def _obtain_token(host: str, verify: bool, instrument: str | None = None) -> str | None:
    """Get the access token by pairing with the web app.

    Pairing is the only way an agent obtains a credential: the token stays a
    revocable, short-lived, per-machine device token that the agent renews
    itself. The user can retry if a code expires before it is approved.

    :param host: Normalized server host
    :type host: str
    :param verify: Whether to verify the server's TLS certificate
    :type verify: bool
    :param instrument: Name of the instrument this machine watches, if set
    :type instrument: str | None
    :return: The access token, or None if the user gave up
    :rtype: str | None
    """
    while True:
        token = run_pairing(host, verify=verify, instrument=instrument)
        if token:
            return token
        if not _prompt_yes_no("Pairing did not complete. Try again?", default=True):
            return None


def run_setup_wizard(settings: dict) -> dict:
    """Interactively collect and verify the agent settings.

    :param settings: Current settings; non-empty values become defaults
    :type settings: dict
    :return: The completed settings dict
    :rtype: dict
    """
    print(
        "\n"
        f"=== Mascope File Agent setup ({__version__}) ===\n"
        "\n"
        "The agent watches a folder and uploads new data files to your\n"
        "Mascope server.\n"
    )

    host = normalize_host(_prompt("Mascope server address", settings.get("host", "")))
    verify_tls = _prompt_yes_no(
        "Verify the server's TLS certificate? (answer No only for a "
        "self-signed or development server)",
        bool(settings.get("verify_tls", True)),
    )

    # The local questions come first and pairing last: pairing needs a second
    # person at a browser, and the instrument name it reports is best chosen
    # with the watched folder in view, where the files already say what the
    # server has been filing them under.
    source = _prompt_source(settings.get("source", ""))
    recursive = _prompt_yes_no(
        "Also watch subfolders of that folder?",
        bool(settings.get("recursive")),
    )
    mask = _prompt("Pattern of files to upload", settings.get("mask") or "*.raw")
    segment = _newest_file_segment(source, mask)
    suggested = segment if segment and _server_reads_as_instrument(segment) else None
    instrument = _prompt_instrument(settings.get("instrument") or "", suggested)
    filename_prefix = _offer_filename_prefix(
        source, mask, instrument, settings.get("filename_prefix") or ""
    )

    access_token = _obtain_token(host, verify_tls, instrument or None)

    while access_token is not None:
        print("Checking the connection...")
        ok, message = verify_connection(host, access_token, verify=verify_tls)
        if ok:
            print("Connected - the server accepted the access token.\n")
            break
        print(f"\n{message}\n")
        choice = (
            input(
                "Pair [a]gain, re-enter [s]erver address, or [c]ontinue anyway? [a/s/c]: "
            )
            .strip()
            .lower()
        )
        if choice == "s":
            host = normalize_host(_prompt("Mascope server address", host))
            access_token = _obtain_token(host, verify_tls, instrument or None)
        elif choice == "c":
            print("Continuing without verification.\n")
            break
        else:
            access_token = _obtain_token(host, verify_tls, instrument or None)

    if access_token is None:
        raise KeyboardInterrupt("Setup cancelled: the agent was not paired.")

    return {
        **settings,
        "host": host,
        "access_token": access_token,
        "source": source,
        "recursive": recursive,
        "mask": mask,
        "verify_tls": verify_tls,
        "instrument": instrument,
        "filename_prefix": filename_prefix,
    }
