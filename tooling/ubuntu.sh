#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# parse args
action="${1:-reinstall}"

# resolve mascope path
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
ROOT_PATH=$( dirname "$SCRIPT_DIR" )

# main procedure
function main() {

    write_section "MASCOPE ${action^^}"

    write_line "Launching setup script in '${action}' mode"

    # Clearing the runtime state belongs with tearing the install down, not
    # with putting one in place: state.json carries the server's active
    # environment, and every deployed server runs a named env rather than
    # `default`. Re-running `install` to pick up a change to the systemd unit
    # templates is a documented operation, and it must not silently point the
    # boot service at a different database.
    if [ "$(action_in 'uninstall' 'reinstall')" ]; then
        uninstall_mascope
        clear_envvars
        clear_state
    fi

    if [ "$(action_in 'install' 'reinstall')" ]; then
        set_envvars
        install_tooling
    fi

    if [ "$(action_in 'install' 'reinstall')" ]; then
        install_mascope
    fi

    write_section "MASCOPE ${action^^} SUCCESSFUL!"

    # The install adds the user to the `docker` group, which an existing
    # session does not pick up; a fresh login does. Only offer that when there
    # is a terminal to log in on - `su` on a key-only deploy account prompts
    # for a password nobody has, and its failure would otherwise be the
    # script's exit status even though the install succeeded.
    if [ "$(action_in 'install' 'reinstall')" ] && [ -t 0 ]; then
        su "${USER}"
    else
        write_line "Log out and back in to pick up the new 'docker' group membership."
    fi
}

function action_in() {
    for i in "${@}" ; do
        if [[ "$action" == "$i" ]]; then
            echo 0;
        fi
    done
}

function set_envvar() {
    # remove the old persisted value
    sudo sed -i "/${1}=/d" /etc/environment
    # set the new persisted value
    sudo bash -c "echo ${1}=${2} >> /etc/environment"
    # export variable to this script
    declare -gx "${1}=${2}"

    echo "Environment variable ${1} set to ${2}"
}

function set_envvars() {
    write_section "SETTING ENV VAR"

    set_envvar 'MASCOPE_PATH' "${ROOT_PATH}"
}

function clear_envvars() {
    write_section "CLEARING ENV VAR"
    
    write_line "Removing all Mascope env vars from /etc/environment"

    sudo sed -i '/MASCOPE/d' /etc/environment
    declare -gx MASCOPE_PATH="${ROOT_PATH}"
}

function clear_state() {
    write_section "CLEARING STATE"

    write_line "Deleting /.runtime/state.json"
    
    rm "${ROOT_PATH}/.runtime/state.json" || true
}

function uv_is_recent_enough() {
    # Probe for the newest uv capability the script relies on instead of
    # comparing version numbers: `uv tool install --with-executables-from`
    # (links the `mascope` executable from the mascope_cli dependency).
    uv tool install --help 2>/dev/null | grep -q -- '--with-executables-from'
}

function ensure_uv() {
    if [[ -z $(command -v uv) ]]; then
        write_line "uv not detected, installing..."

        sudo snap install --classic astral-uv
        return
    fi

    if uv_is_recent_enough; then
        write_line "uv detected ($(uv --version)), skipping install."
        return
    fi

    # A pre-existing uv can predate features the script needs; update it
    # through whichever channel manages it.
    write_line "uv detected ($(uv --version)) but too old, updating..."
    if snap list astral-uv &> /dev/null; then
        sudo snap refresh astral-uv
    else
        # Standalone-installer uv; fails harmlessly on other installs
        # (e.g. pip), which the re-check below turns into a clear error.
        uv self update || true
    fi

    if ! uv_is_recent_enough; then
        write_line "ERROR: uv is too old (needs 'uv tool install --with-executables-from', CI uses uv 0.11.x) and could not be updated automatically. Update uv manually and re-run."
        exit 1
    fi

    write_line "uv updated to $(uv --version)."
}

function install_tooling() {
    write_section "INSTALLING TOOLING"

    sudo apt update
    sudo apt install -y curl build-essential python3-dev pkg-config
    
    ensure_uv

    if [[ $(node -v) != v22* ]]; then
        write_line "Node 22 not detected, installing..."
        
        curl -fsSL https://deb.nodesource.com/setup_22.x -o nodesource_setup.sh
        sudo -E bash nodesource_setup.sh
        rm nodesource_setup.sh
        sudo apt-get install -y nodejs
    else
        write_line "Node 22 detected, skipping install."
    fi

    # restic for encrypted off-site backups (tooling/backup-cron.sh)
    write_line "installing restic"
    sudo apt install --yes restic

    # use jemalloc to ensure no free() pointer errors
    write_line "installing jemalloc"
    sudo apt install --yes --no-install-recommends libjemalloc2
    set_envvar 'LD_PRELOAD' "/usr/lib/x86_64-linux-gnu/libjemalloc.so.2"
    
    # Configure Redis memory overcommit for background saves.
    # Written to /etc/sysctl.d/ rather than /etc/sysctl.conf: systemd-sysctl
    # reads the sysctl.d directories, and only reaches /etc/sysctl.conf on
    # releases that ship an /etc/sysctl.d/99-sysctl.conf symlink pointing at
    # it. Ubuntu 26.04 dropped that symlink, so appending to /etc/sysctl.conf
    # applies until the next boot and then silently stops - which strands
    # postgres if its shared memory request needs the overcommit.
    write_line "configuring Redis memory overcommit"
    sudo tee /etc/sysctl.d/99-mascope.conf > /dev/null <<'EOF'
# Managed by tooling/ubuntu.sh - Redis background saves need overcommit.
vm.overcommit_memory = 1
EOF
    sudo sysctl --system > /dev/null
    write_line "Redis memory overcommit enabled (vm.overcommit_memory = $(sysctl -n vm.overcommit_memory))"

    if [[ -z $(command -v dotnet) ]]; then
        write_line "dotnet runtime not detected, installing..."

        wget https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb -O packages-microsoft-prod.deb \
            && sudo dpkg -i packages-microsoft-prod.deb \
            && rm packages-microsoft-prod.deb \
            && sudo apt update \
            && sudo apt install -y dotnet-runtime-9.0
    else
        write_line "dotnet detected, skipping install."
    fi

    # cli depedencency
    sudo npm install -g concurrently

    if [[ -z $(command -v docker) ]]; then
        write_line "Docker not detected, installing..."
       
        sudo apt install -y apt-transport-https ca-certificates software-properties-common
        sudo install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /tmp/docker.asc
        sudo cp /tmp/docker.asc /etc/apt/keyrings/docker.asc
        sudo chmod a+r /etc/apt/keyrings/docker.asc
        rm /tmp/docker.asc
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" \
            | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
        sudo apt update
        sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        sudo usermod -aG docker "${USER}"
    else
        write_line "Docker detected, skipping install."
    fi
}

function install_mascope() {
    write_section "INSTALLING MASCOPE BINARIES"

    # Force C17 standard to avoid GCC 15+ / C23 incompatibilities with
    # native extensions (e.g. numcodecs blosc typedef bool).
    # Pin Python 3.12 so uv downloads a managed interpreter matching
    # the project's requires-python constraint (<3.13).
    # --reinstall (implies --refresh) forces a rebuild from source: every
    # mascope package is pinned to version 0.0.0 (the real version is derived
    # from git at runtime), so uv's cache cannot tell releases apart and would
    # otherwise reuse a stale wheel - silently shipping old CLI code after an
    # update to a new release.
    # --with-executables-from: `uv tool install` only links executables
    # declared by the requested package itself, and the `mascope` entry point
    # lives in the mascope_cli workspace member (so the standalone PyPI wheel
    # provides it too) - without this flag only mascope-backend gets a shim.
    # uv links the CLI into ~/.local/bin, which the running shell does not have
    # on PATH: the stock ~/.profile adds it only `if [ -d ... ]`, false at login
    # on a freshly created deploy account, and `uv tool update-shell` edits the
    # profiles for *future* shells rather than this one.
    #
    # Put it on PATH before calling uv, not after. `uv tool update-shell` exits
    # non-zero - an error, not a warning - when the directory is absent from
    # PATH while the config it would edit already names it. That is every
    # re-run from a non-login shell: ssh, cron, ansible. Under errexit it kills
    # the install after the binaries are linked but before a single systemd
    # unit is written, which is the same end state as having no PATH fix at
    # all. Exporting first also silences uv's own PATH warning, and makes the
    # binary lookup below resolve on a first install.
    mkdir -p "${HOME}/.local/bin"
    export PATH="${HOME}/.local/bin:${PATH}"

    CFLAGS="-std=c17" uv tool install --force --reinstall --python 3.12 . \
        --with-executables-from mascope-cli
    uv tool update-shell

    write_section "INSTALLING SYSTEMD UNITS"

    # `|| true` so errexit does not kill the script here: without it the
    # explicit error below is unreachable and a missing binary aborts the run
    # with no diagnostic, after the binaries are installed but before any
    # systemd unit is.
    MASCOPE_BIN=$(command -v mascope || true)
    if [[ -z "${MASCOPE_BIN}" ]]; then
        write_line "ERROR: mascope binary not found on PATH after install"
        exit 1
    fi

    SYSTEMD_SRC="${ROOT_PATH}/tooling/systemd"

    # Boot service: brings the stack up on boot / down on stop. Templated with
    # the deploy user, the resolved mascope binary, and the checkout as the
    # working directory (systemd's default is "/", where the CLI finds no
    # repository and so cannot resolve the checked-out release tag).
    sed -e "s|@@USER@@|${USER}|g" \
        -e "s|@@MASCOPE_BIN@@|${MASCOPE_BIN}|g" \
        -e "s|@@MASCOPE_PATH@@|${ROOT_PATH}|g" \
        "${SYSTEMD_SRC}/mascope.service" \
        | sudo tee /etc/systemd/system/mascope.service > /dev/null

    # Unattended-update service + timer. Installed but deliberately NOT enabled:
    # the operator sets a release token in /etc/mascope/update.env and enables
    # the timer when ready (see docs/maintaining.md).
    sed -e "s|@@USER@@|${USER}|g" \
        -e "s|@@MASCOPE_BIN@@|${MASCOPE_BIN}|g" \
        -e "s|@@MASCOPE_PATH@@|${ROOT_PATH}|g" \
        "${SYSTEMD_SRC}/mascope-update.service" \
        | sudo tee /etc/systemd/system/mascope-update.service > /dev/null
    sudo cp "${SYSTEMD_SRC}/mascope-update.timer" \
        /etc/systemd/system/mascope-update.timer

    # Disk-space monitor service + timer. Read-only (it never deletes anything),
    # so unlike the updater it is enabled by default below.
    DISK_CHECK="${ROOT_PATH}/tooling/disk-check.sh"
    sed -e "s|@@USER@@|${USER}|g" \
        -e "s|@@DISK_CHECK@@|${DISK_CHECK}|g" \
        "${SYSTEMD_SRC}/mascope-disk-check.service" \
        | sudo tee /etc/systemd/system/mascope-disk-check.service > /dev/null
    sudo cp "${SYSTEMD_SRC}/mascope-disk-check.timer" \
        /etc/systemd/system/mascope-disk-check.timer

    # Peak-assignment retention service + timer. Deletes only superseded
    # derived data on the documented keep-newest policy, so like the disk
    # monitor it is enabled by default below.
    sed -e "s|@@USER@@|${USER}|g" \
        -e "s|@@MASCOPE_BIN@@|${MASCOPE_BIN}|g" \
        "${SYSTEMD_SRC}/mascope-assignment-prune.service" \
        | sudo tee /etc/systemd/system/mascope-assignment-prune.service > /dev/null
    sudo cp "${SYSTEMD_SRC}/mascope-assignment-prune.timer" \
        /etc/systemd/system/mascope-assignment-prune.timer

    # Config (window / grace / release token; disk thresholds / alert URL).
    # Seed once with restricted permissions; never clobber an existing file.
    sudo install -d -m 755 /etc/mascope
    if [[ ! -f /etc/mascope/update.env ]]; then
        sudo install -m 600 -o "${USER}" -g "${USER}" \
            "${SYSTEMD_SRC}/update.env.example" /etc/mascope/update.env
        write_line "seeded /etc/mascope/update.env"
    else
        write_line "kept existing /etc/mascope/update.env"
    fi
    if [[ ! -f /etc/mascope/disk-check.env ]]; then
        sudo install -m 600 -o "${USER}" -g "${USER}" \
            "${ROOT_PATH}/tooling/disk-check.env.example" /etc/mascope/disk-check.env
        write_line "seeded /etc/mascope/disk-check.env"
    else
        write_line "kept existing /etc/mascope/disk-check.env"
    fi
    if [[ ! -f /etc/mascope/prune.env ]]; then
        sudo install -m 600 -o "${USER}" -g "${USER}" \
            "${SYSTEMD_SRC}/prune.env.example" /etc/mascope/prune.env
        write_line "seeded /etc/mascope/prune.env"
    else
        write_line "kept existing /etc/mascope/prune.env"
    fi

    sudo systemctl daemon-reload
    sudo systemctl enable mascope.service
    write_line "mascope.service enabled for user '${USER}' (bin: ${MASCOPE_BIN})"
    sudo systemctl enable --now mascope-disk-check.timer
    write_line "Disk monitor ENABLED (mascope-disk-check.timer, every 15 min). Set HEALTHCHECK_URL in /etc/mascope/disk-check.env to get alerted. See docs/maintaining.md."
    sudo systemctl enable --now mascope-assignment-prune.timer
    write_line "Assignment-run retention ENABLED (mascope-assignment-prune.timer, nightly; keeps the newest 2 completed runs per sample). Tune in /etc/mascope/prune.env. See docs/maintaining.md."
    write_line "Auto-updates are INSTALLED but DISABLED. To turn them on: 'sudo systemctl enable --now mascope-update.timer' (no token needed). See docs/maintaining.md."
}

function uninstall_mascope() {
    write_section "DISABLING SYSTEMD UNITS"

    # Boot service, auto-update units, and the disk monitor. Files under
    # /etc/mascope/ are left in place so a reinstall keeps the settings/token.
    for unit in mascope-disk-check.timer mascope-disk-check.service \
        mascope-assignment-prune.timer mascope-assignment-prune.service \
        mascope-update.timer mascope-update.service mascope.service; do
        sudo systemctl stop "$unit" || true
        sudo systemctl disable "$unit" || true
        sudo rm -f "/etc/systemd/system/$unit"
    done
    sudo systemctl daemon-reload
    write_line "systemd units disabled and removed (kept /etc/mascope/update.env)"

    write_section "UNINSTALLING MASCOPE BINARIES"

    uv tool uninstall --all
}

function write_section() {
    
    echo "

    [${1}]

    "
}

function write_line() {
    echo "
    ${1}
    "
}

main
