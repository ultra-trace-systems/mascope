#!/usr/bin/env bash
# One unattended update pass, as mascope-update.service runs it:
#
#   mascope-update.sh <mascope binary> <runtime home>
#
# Runs `mascope prod update --auto` and passes its exit code on. Exit 40 is a
# wheel install's hand-over: an update is due that only the newest release's
# own CLI can apply, and the run wrote that CLI's requirement
# (`mascope-cli==<calver>`) to <home>/.runtime/update/cli-to-install. This
# installs it with `uv tool install`, then runs `--auto` again as a new
# process, which applies the update. The CLI never reinstalls itself from
# inside its own run (cmd/prod/cli_drift.py), so the two are separate
# processes here. A checkout install never exits 40.
#
# Exit codes are --auto's (0 done, 30 a migration update pending, 2 an error);
# a hand-over that cannot be completed exits 2.
#
# The work is inside a function that is called on the last line, so bash has
# read the whole file before `uv tool install` replaces the environment this
# copy may live in (a wheel install's units run the CLI's bundled copy).

set -uo pipefail

readonly AUTO_ERROR=2
readonly AUTO_INSTALL_CLI=40

function main() {
    local mascope="${1:?usage: mascope-update.sh <mascope binary> <runtime home>}"
    local home="${2:?usage: mascope-update.sh <mascope binary> <runtime home>}"

    "$mascope" prod update --auto
    local rc=$?
    if [[ "$rc" -ne "$AUTO_INSTALL_CLI" ]]; then
        return "$rc"
    fi

    local handover="${home}/.runtime/update/cli-to-install"
    local requirement
    requirement=$(head -n 1 "$handover" 2>/dev/null)
    # The CLI wrote it, but it is what gets installed: take nothing but a
    # pinned mascope-cli.
    if [[ ! "$requirement" =~ ^mascope-cli==[0-9A-Za-z.+!-]+$ ]]; then
        echo "mascope-update: exit 40, but ${handover} names no CLI to install ('${requirement}')" >&2
        return "$AUTO_ERROR"
    fi

    # systemd's PATH lacks the places uv is installed to: the standalone
    # installer's ~/.local/bin and the snap's /snap/bin.
    local uv
    uv=$(PATH="${PATH}:${HOME:-/nonexistent}/.local/bin:/snap/bin" command -v uv)
    if [[ -z "$uv" ]]; then
        echo "mascope-update: cannot install ${requirement}: uv not found" >&2
        return "$AUTO_ERROR"
    fi

    echo "mascope-update: installing ${requirement}, then running the update again"
    if ! "$uv" tool install "$requirement"; then
        echo "mascope-update: '${uv} tool install ${requirement}' failed" >&2
        return "$AUTO_ERROR"
    fi

    "$mascope" prod update --auto
    rc=$?
    if [[ "$rc" -eq "$AUTO_INSTALL_CLI" ]]; then
        # The new CLI is not the one at ${mascope}: the CLI was installed some
        # other way than `uv tool install mascope-cli`.
        echo "mascope-update: ${mascope} still asks for ${requirement} after installing it; is it a 'uv tool install mascope-cli'?" >&2
        return "$AUTO_ERROR"
    fi
    return "$rc"
}

main "$@"; exit $?
