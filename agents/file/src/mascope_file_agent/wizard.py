"""Interactive first-run setup for the bundled File Agent.

Runs in the agent's console window when required settings are missing (or
when started with ``--setup``), prompting for the server address, the watched
folder and the instrument name, then pairing the machine. The credential is
verified against the server right away so typos surface during setup instead
of at the first upload.
"""

import fnmatch
import os
import platform
import time

import requests
import urllib3

from mascope_file_agent import __version__
from mascope_file_agent.config import base_url, is_valid_instrument, normalize_host
from mascope_sdk import agent_headers


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

#: How far setup looks into the watched folder for a file name to reason
#: from: this many files, this many levels below the watched folder when
#: subfolders are watched, and this long. See _folder_evidence.
_EVIDENCE_MAX_FILES = 500
_EVIDENCE_MAX_DEPTH = 2
_EVIDENCE_TIME_BUDGET_S = 3.0

#: The agent's own quarantine folder inside the watched one. Never watched,
#: and full of names the server refused, so never evidence either.
_FAILED_UPLOADS_DIR = "failed_uploads"

#: Answer that clears a prompt's default instead of accepting it. Not a name
#: anyone would give an instrument, and the only way to remove an optional
#: setting without hand-editing the configuration.
CLEAR_ANSWER = "-"

#: Longest agent version the server keeps. Sent short rather than left for the
#: server to refuse: the pairing request carries the version, and one rejected
#: over its length would leave the machine unable to pair at all.
AGENT_VERSION_MAX_LENGTH = 32


class SetupCancelled(KeyboardInterrupt):
    """Setup was abandoned, carrying the answers given before it was.

    A ``KeyboardInterrupt`` so every existing handler still treats it as the
    cancellation it is; the answers ride along so the caller can save them and
    offer them as defaults next time.
    """

    def __init__(self, message: str, settings: dict):
        super().__init__(message)
        self.settings = settings


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
            headers=agent_headers(access_token),
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


def _prompt(label: str, default: str = "", required: bool = True) -> str:
    """Prompt for a value, offering a default when one exists.

    Empty input takes the default, so Enter keeps what is already configured.
    An optional value is cleared by answering ``-``: without that, a setting
    that has a default could only ever be changed, never removed, leaving a
    hand-edit of the configuration as the only way to undo it.

    :param label: Prompt label
    :type label: str
    :param default: Value returned on empty input
    :type default: str
    :param required: Whether an empty result is refused and asked again
    :type required: bool
    :return: The entered (or default) value, stripped; empty only when the
        value is optional
    :rtype: str
    """
    suffix = f" [{default}]" if default else ""
    while True:
        answer = input(f"{label}{suffix}: ").strip()
        value = "" if answer == CLEAR_ANSWER else (answer or default)
        if value or not required:
            return value
        print("  A value is required.")


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


def _folder_evidence(
    source: str, mask: str, recursive: bool = False, prefix: str = ""
) -> str | None:
    """A file name that says how this instrument's uploads are filed today.

    The newest name a current server could read the instrument from, and only
    if there is none, the newest name of any kind. Taking the newest file
    outright would let one stray ``test.raw`` in a folder of properly named
    acquisitions say the folder needs a prefix - and prefixing them all is
    then a new sample-name lineage for every future file.

    The look is bounded, because an acquisition folder can hold years of
    files on a network share and a full walk of it is exactly the "setup
    hangs" an operator would report: the watched folder first, then, when
    subfolders are watched, its subfolders newest-named first, at most
    ``_EVIDENCE_MAX_DEPTH`` levels down, at most ``_EVIDENCE_MAX_FILES`` files,
    within ``_EVIDENCE_TIME_BUDGET_S`` seconds. A sample of the newest files
    is evidence enough for a suggestion the operator confirms anyway. The
    agent's own ``failed_uploads`` folder is never looked at: it holds names
    the server refused. A file that vanishes between the listing and the stat
    is skipped rather than raised: an acquisition folder is written to while
    setup runs, and a disappearing file must not end it.

    :param source: The watched folder
    :type source: str
    :param mask: The file pattern to upload
    :type mask: str
    :param recursive: Whether subfolders are watched too
    :type recursive: bool
    :param prefix: The configured upload prefix, which is part of the name
        the server sees
    :type prefix: str
    :return: Base name of the file to reason from, or None when the folder
        holds none
    :rtype: str | None
    """
    deadline = time.monotonic() + _EVIDENCE_TIME_BUDGET_S
    examined = 0
    newest = newest_mtime = None
    readable = readable_mtime = None
    pending = [(source, 0)]
    while pending:
        folder, depth = pending.pop(0)
        subfolders = []
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if examined >= _EVIDENCE_MAX_FILES or time.monotonic() > deadline:
                        return readable or newest
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if (
                                recursive
                                and depth < _EVIDENCE_MAX_DEPTH
                                and entry.name != _FAILED_UPLOADS_DIR
                            ):
                                subfolders.append(entry.path)
                            continue
                        if not entry.is_file() or not fnmatch.fnmatch(entry.name, mask):
                            continue
                        mtime = entry.stat().st_mtime
                    except OSError:
                        continue
                    examined += 1
                    name = entry.name
                    if newest_mtime is None or mtime > newest_mtime:
                        newest, newest_mtime = name, mtime
                    if _server_reads_as_instrument(_filed_under(name, prefix)) and (
                        readable_mtime is None or mtime > readable_mtime
                    ):
                        readable, readable_mtime = name, mtime
        except OSError:
            continue
        # Newest-named first: acquisition folders are commonly named by date,
        # so the most recent data is reached before the cap is.
        pending.extend((path, depth + 1) for path in sorted(subfolders, reverse=True))
    return readable or newest


def _filed_under(filename: str, prefix: str) -> str:
    """The instrument a current server would file an upload under.

    The server takes everything before the first underscore of the name it
    receives - of the whole name, extension included, so a name with no
    underscore keeps its suffix and is refused for the dot. The name it
    receives is the on-disk one behind the configured prefix, which is why
    the prefix belongs in this answer.

    :param filename: The file's on-disk base name
    :type filename: str
    :param prefix: The configured upload prefix, possibly empty
    :type prefix: str
    :return: The instrument segment the server would read
    :rtype: str
    """
    return f"{prefix}{filename}".split("_")[0]


def _prompt_instrument(default: str, suggested: str | None) -> str:
    """Prompt for the instrument name, allowing it to be left empty.

    :param default: Previously configured name, if any
    :type default: str
    :param suggested: Name this folder's uploads are filed under today
    :type suggested: str | None
    :return: A valid instrument name, or an empty string
    :rtype: str
    """
    print(
        "\n"
        "The instrument name is reported when pairing and with each upload, so\n"
        "the server can file uploads under it. Letters, digits and hyphens,\n"
        f"e.g. Orbi-Lab2. Leave it empty to skip, or answer '{CLEAR_ANSWER}' to\n"
        "remove a name that is already set."
    )
    if default and not is_valid_instrument(default):
        # Offering it back would make Enter re-submit a name the agent refuses
        # to start with, leaving no way out of this prompt but Ctrl+C.
        print(
            f"The configured name '{default}' is not one the server accepts, so\n"
            "it is not offered as the default."
        )
        default = ""
    if suggested and suggested != default:
        print(f"Uploads from this folder are filed under '{suggested}' today.")
    while True:
        value = _prompt("Instrument name", default or suggested or "", required=False)
        if not value or is_valid_instrument(value):
            return value
        print("  Use letters, digits and hyphens only, at most 64 characters.")


def _offer_filename_prefix(
    example_name: str | None, instrument: str, current_prefix: str
) -> str:
    """Offer to put the instrument name in front of uploaded file names.

    A current server reads the instrument from the start of each uploaded
    name and refuses names it cannot read, so there is one question to
    answer: what would this folder's files be filed under as things stand?
    That accounts for a prefix already configured, which is why one no longer
    skips the check - a prefix left over from an earlier instrument is the
    case most worth catching, since it files uploads under a name the
    reported instrument does not match.

    :param example_name: Base name of a file that says how this folder's
        uploads are filed, or None when there is none to look at
    :type example_name: str | None
    :param instrument: The instrument name entered, possibly empty
    :type instrument: str
    :param current_prefix: The configured prefix, possibly empty
    :type current_prefix: str
    :return: The prefix to configure, possibly empty
    :rtype: str
    """
    if not instrument:
        return current_prefix
    example = example_name
    if example is None:
        print(
            "\nThe watched folder holds no files yet, so setup cannot tell how\n"
            "this instrument names them."
        )
        example = _prompt(
            "Example file name from this instrument (Enter to skip)", required=False
        )
    if not example:
        # Nothing to reason from. Guessing risks prefixing names that already
        # carry one, so leave the configuration alone and say what to do.
        print(
            "  No prefix is configured. If uploads are refused, run setup again\n"
            "  once the folder holds a file, or set 'filename_prefix' by hand.\n"
        )
        return current_prefix
    filed_as = _filed_under(example, current_prefix)
    if filed_as == instrument:
        print(f"\nUploads from this folder are filed under '{instrument}' already.\n")
        return current_prefix
    if _server_reads_as_instrument(filed_as):
        print(
            f"\nUploads from this folder are filed under '{filed_as}', the name the\n"
            "server reads from them today. The instrument name is reported\n"
            "alongside it; nothing changes for existing data.\n"
        )
        return current_prefix
    print(
        f"\nThe server cannot read '{filed_as}' as an instrument name, so it\n"
        "refuses uploads named that way."
    )
    if current_prefix:
        print(f"  The configured prefix '{current_prefix}' is what puts it there.")
    if not _server_reads_as_instrument(instrument):
        print(
            "  No prefix is offered: this server release only files uploads under\n"
            "  an instrument name containing 'orbi', 'tof' or 'api' (e.g.\n"
            f"  Orbi-Lab2), and '{instrument}' has none. Set 'filename_prefix' in\n"
            "  the configuration if the files need one.\n"
        )
        return current_prefix
    if _prompt_yes_no(
        f"Add '{instrument}_' in front of every uploaded file name so the "
        "server can file them?",
        default=True,
    ):
        return f"{instrument}_"
    return current_prefix


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
        # and shown to the approver; an older server drops them. Clipped the
        # way machine_name is: a build stamped by `git describe` can run past
        # what the server stores, and pairing must not fail over a label.
        "agent_version": __version__[:AGENT_VERSION_MAX_LENGTH],
    }
    if instrument:
        payload["instrument"] = instrument
    try:
        resp = requests.post(
            f"{base_url(host)}/api/auth/pairing/start",
            json=payload,
            headers=agent_headers(),
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
                    headers=agent_headers(),
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
    # Scanned once and handed to both questions below: a second scan of a live
    # acquisition folder is slow, and can disagree with the first.
    current_prefix = settings.get("filename_prefix") or ""
    example_name = _folder_evidence(source, mask, recursive, current_prefix)
    filed_as = _filed_under(example_name, current_prefix) if example_name else None
    suggested = filed_as if filed_as and _server_reads_as_instrument(filed_as) else None
    instrument = _prompt_instrument(settings.get("instrument") or "", suggested)
    filename_prefix = _offer_filename_prefix(example_name, instrument, current_prefix)

    # Collected before pairing so a cancelled pairing can hand them back: they
    # are every answer that needed nobody but the person at this machine, and
    # retyping them is the whole cost of walking away to find an approver.
    answers = {
        **settings,
        "host": host,
        "verify_tls": verify_tls,
        "source": source,
        "recursive": recursive,
        "mask": mask,
        "instrument": instrument,
        "filename_prefix": filename_prefix,
    }

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
            answers["host"] = host
            access_token = _obtain_token(host, verify_tls, instrument or None)
        elif choice == "c":
            print("Continuing without verification.\n")
            break
        else:
            access_token = _obtain_token(host, verify_tls, instrument or None)

    if access_token is None:
        raise SetupCancelled("Setup cancelled: the agent was not paired.", answers)

    return {**answers, "access_token": access_token}
