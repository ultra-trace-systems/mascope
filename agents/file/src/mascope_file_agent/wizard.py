"""Interactive first-run setup for the bundled File Agent.

Runs in the agent's console window when required settings are missing (or
when started with ``--setup``), prompting for the server address, access
token and watched folder. The token is verified against the server right
away so typos surface during setup instead of at the first upload.
"""

import os
import platform
import time

import requests
import urllib3

from mascope_file_agent import __version__
from mascope_file_agent.config import base_url, normalize_host


# Suppress the warning urllib3 emits when a user has turned TLS verification
# off for a self-signed server; it does not fire when verification is on.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

VERIFY_TIMEOUT = 15  # seconds


def verify_connection(
    host: str, access_token: str, verify: bool = True
) -> tuple[bool, str]:
    """Check that the server is reachable and accepts the access token.

    Calls a cheap authenticated endpoint with the file-agent service
    headers, exactly as uploads will.

    :param host: Normalized server host
    :type host: str
    :param access_token: The access token to verify
    :type access_token: str
    :param verify: Whether to verify the server's TLS certificate
    :type verify: bool
    :return: (ok, user-facing error message when not ok)
    :rtype: tuple[bool, str]
    """
    url = f"{base_url(host)}/api/sample/files"
    try:
        resp = requests.get(
            url,
            params={"page": 1, "limit": 1},
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Service-Name": "file-agent",
            },
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
            return False, (
                f"The address {base_url(host)} responded, but it does not "
                "look like the Mascope API (it returned a web page instead "
                "of data), so uploads would fail. In a development setup, "
                "use the backend address (e.g. http://localhost:8090) - the "
                "frontend dev server cannot receive uploads. In production, "
                "use the normal Mascope web app address."
            )
        return True, ""
    if resp.status_code in (401, 403):
        return False, (
            "The server rejected the access token. Pair the agent again to "
            "get a fresh one."
        )
    return False, f"Unexpected response from the server (HTTP {resp.status_code})."


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


def start_pairing(host: str, verify: bool = True) -> dict | None:
    """Request a pairing code from the server.

    :param host: Normalized server host
    :type host: str
    :param verify: Whether to verify the server's TLS certificate
    :type verify: bool
    :return: The pairing response (user_code, device_code, expires_in,
        interval), or None with an explanation printed when pairing is
        unavailable
    :rtype: dict | None
    """
    machine_name = platform.node()[:64] or None
    try:
        resp = requests.post(
            f"{base_url(host)}/api/auth/pairing/start",
            json={"service_name": "file-agent", "machine_name": machine_name},
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


def run_pairing(host: str, verify: bool = True) -> str | None:
    """Interactive pairing: display the code and poll until approved.

    :param host: Normalized server host
    :type host: str
    :param verify: Whether to verify the server's TLS certificate
    :type verify: bool
    :return: The access token, or None when pairing did not complete
    :rtype: str | None
    """
    started = start_pairing(host, verify=verify)
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


def _obtain_token(host: str, verify: bool) -> str | None:
    """Get the access token by pairing with the web app.

    Pairing is the only way an agent obtains a credential: the token stays a
    revocable, short-lived, per-machine device token that the agent renews
    itself. The user can retry if a code expires before it is approved.

    :param host: Normalized server host
    :type host: str
    :param verify: Whether to verify the server's TLS certificate
    :type verify: bool
    :return: The access token, or None if the user gave up
    :rtype: str | None
    """
    while True:
        token = run_pairing(host, verify=verify)
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
    access_token = _obtain_token(host, verify_tls)

    while access_token is not None:
        print("Checking the connection...")
        ok, message = verify_connection(host, access_token, verify=verify_tls)
        if ok:
            print("Connected - the server accepted the access token.\n")
            break
        print(f"\n{message}\n")
        choice = (
            input("Pair [a]gain, re-enter [s]erver address, or [c]ontinue anyway? [a/s/c]: ")
            .strip()
            .lower()
        )
        if choice == "s":
            host = normalize_host(_prompt("Mascope server address", host))
            access_token = _obtain_token(host, verify_tls)
        elif choice == "c":
            print("Continuing without verification.\n")
            break
        else:
            access_token = _obtain_token(host, verify_tls)

    if access_token is None:
        raise KeyboardInterrupt("Setup cancelled: the agent was not paired.")

    source = _prompt_source(settings.get("source", ""))
    recursive = _prompt_yes_no(
        "Also watch subfolders of that folder?",
        bool(settings.get("recursive")),
    )
    mask = _prompt("Pattern of files to upload", settings.get("mask") or "*.raw")

    return {
        **settings,
        "host": host,
        "access_token": access_token,
        "source": source,
        "recursive": recursive,
        "mask": mask,
        "verify_tls": verify_tls,
    }
