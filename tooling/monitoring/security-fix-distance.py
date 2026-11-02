#!/usr/bin/env python3
"""
Is a merged security fix not yet running on every production deployment?

A fix on develop is not a fix in production. This check, run on a schedule
from the monitoring host, measures the distance and says it out loud: it names
each deployment that runs a release without a merged security fix, and the
fix it is missing, and says nothing at all when every deployment has them all.

* **What a security fix is.** A commit that adds a changelog fragment under
  the security section, ``changelog.d/<branch>.security.md`` (the agent guide,
  ``CLAUDE.md``, makes it the rule). The fragment lands with the fix, at merge,
  so the check sees a fix from the moment it is on develop, not only once a
  release compiles it into ``CHANGELOG.md``. It reads the fragments from a
  clone of its own, fetched every run, so it needs no token and no API.
* **What a deployment runs.** The release its backend reports on the anonymous
  ``/api/version/public`` route: the running backend answering, never a
  record of what was deployed. A deployment has a fix when that release's tag
  contains the fix's commit (``git tag --contains``).
* **Unknown is a finding.** A deployment that does not answer, answers
  ``unknown`` or ``latest``, or names a build that is no release tag of the
  repository, cannot be shown to have any fix, so it is reported, never
  skipped.
* **A stopped check is an alarm.** Each run pings a healthchecks.io-style
  dead-man switch: success when every deployment has every fix, a failure with
  the counts otherwise. A check that stops running stops pinging, and the
  switch pages.

Only counts go to the heartbeat. Which deployment lacks which fix describes an
open weakness of our own fleet, and the heartbeat service is a third party, so
the names stay on this host: on stdout, for the cron line's logger.

Exit codes: 0 every deployment runs every fix (stdout empty); 1 a deployment
is behind or its release cannot be read (one stdout line per finding); 2 the
configuration is unusable; 3 the repository could not be fetched, so the check
is blind to new fixes.

The configuration is an INI file (``--config``, default below), never in this
repository, since it names the deployments::

    [check]
    heartbeat = https://hc-ping.com/<uuid>
    repository = /opt/pentest-runner/fix-distance.git

    [deployments]
    server-a = https://a.example.com
    server-b = https://b.example.com

Only the standard library, so the host's own ``python3`` (3.10 or later) runs
it: ``python3 -I security-fix-distance.py``.
"""

from __future__ import annotations

import argparse
import configparser
import http.client
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_CONFIG = "/opt/pentest-runner/fix-distance.ini"
DEFAULT_REPOSITORY = "/opt/pentest-runner/fix-distance.git"
DEFAULT_REMOTE = "https://github.com/ultra-trace-systems/mascope.git"
DEFAULT_BRANCHES = "develop master"
DEFAULT_TIMEOUT = 10.0

#: Where the fetched branches are kept in the clone, apart from any branch an
#: operator may have, so a fetch never moves one of theirs.
REF_NAMESPACE = "refs/fix-distance"

FRAGMENTS = "changelog.d/*.security.md"

# Keep this in step with RELEASE_TAG_PATTERN in mascope_runtime and with
# release_tag in run-assessment.sh. Copied rather than imported: this runs on
# the monitoring host's own python3, which does not install the runtime.
RELEASE_TAG = re.compile(
    r"v(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<label>alpha|beta|rc)\.?(?P<number>\d+))?"
)
_PRERELEASE_RANK = {"alpha": 0, "beta": 1, "rc": 2}
_RELEASE_RANK = 3

#: The shape a deployment's answer must have before it is compared or printed:
#: the deployment is a server on the internet, and its answer is data, not
#: text to pass on.
VERSION_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,99}")
#: The shape of a deployment's alias and a fragment's name, for the same
#: reason: both reach the log.
NAME_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
#: The most of a version answer that is read: a real one is under 100 bytes.
MAX_ANSWER = 4096


class ConfigError(Exception):
    """The configuration cannot be used."""


class FetchError(Exception):
    """The repository could not be brought up to date."""


@dataclass
class Fix:
    """A merged security fix: the commit that added its fragment."""

    commit: str
    merged: str
    name: str
    releases: set[str] = field(default_factory=set)

    @property
    def first_release(self) -> str | None:
        return min(self.releases, key=release_key, default=None)

    def describe(self) -> str:
        first = self.first_release
        where = f"released in {first}" if first else "not released"
        return f"{self.commit[:9]} {self.name} (merged {self.merged}, {where})"


@dataclass
class Config:
    repository: Path
    remote: str
    branches: list[str]
    heartbeat: str
    timeout: float
    deployments: dict[str, str]


def release_key(tag: str) -> tuple[int, int, int, int, int]:
    """Precedence of a release tag: a candidate ranks below its own release."""
    m = RELEASE_TAG.fullmatch(tag)
    assert m is not None, tag
    label = m["label"]
    return (
        int(m["major"]),
        int(m["minor"]),
        int(m["patch"]),
        _PRERELEASE_RANK[label] if label else _RELEASE_RANK,
        int(m["number"]) if label else 0,
    )


def load_config(path: str) -> Config:
    parser = configparser.ConfigParser(interpolation=None)
    # Aliases keep their case: they are the operator's names for the servers.
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    try:
        with open(path, encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    section = parser["check"] if parser.has_section("check") else {}
    branches = section.get("branches", DEFAULT_BRANCHES).split()
    for branch in branches:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch):
            raise ConfigError(f"not a branch name: {branch!r}")
    if not branches:
        raise ConfigError("no branches to read the fixes from")
    try:
        timeout = float(section.get("timeout", DEFAULT_TIMEOUT))
    except ValueError as exc:
        raise ConfigError(f"timeout is not a number: {exc}") from exc

    # Without the heartbeat a stopped check pages no one, and a finding reaches
    # only a log nobody reads.
    heartbeat = section.get("heartbeat", "")
    if not re.fullmatch(r"https?://\S+", heartbeat):
        raise ConfigError("no heartbeat URL: a check nobody hears is no alarm")

    deployments = (
        dict(parser["deployments"]) if parser.has_section("deployments") else {}
    )
    # No deployment would be a check that passes by measuring nothing.
    if not deployments:
        raise ConfigError("no deployments to check")
    for alias, url in deployments.items():
        if not NAME_SHAPE.fullmatch(alias):
            raise ConfigError(f"not a usable deployment alias: {alias!r}")
        if not re.fullmatch(r"https?://[^\s/]+(/\S*)?", url):
            raise ConfigError(f"{alias}: not an http(s) URL")

    return Config(
        repository=Path(section.get("repository", DEFAULT_REPOSITORY)),
        remote=section.get("remote", DEFAULT_REMOTE),
        branches=branches,
        heartbeat=heartbeat,
        timeout=timeout,
        deployments=deployments,
    )


def git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, ["git", *args], result.stdout, result.stderr
        )
    return result.stdout


def fetch(config: Config) -> None:
    """Bring the clone's branches and tags up to the remote's, or fail."""
    try:
        if not config.repository.exists():
            config.repository.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "init", "--bare", "-q", str(config.repository)],
                check=True,
                capture_output=True,
            )
        refspecs = [f"+refs/heads/{b}:{REF_NAMESPACE}/{b}" for b in config.branches]
        # Tags are forced and pruned too: a release tag that moved or went
        # must not keep answering from the clone.
        refspecs.append("+refs/tags/*:refs/tags/*")
        git(
            config.repository,
            "fetch",
            "--quiet",
            "--prune",
            "--no-tags",
            config.remote,
            *refspecs,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise FetchError(detail.strip().splitlines()[-1] if detail else "") from exc


def marked_fixes(config: Config) -> list[Fix]:
    """Every commit on the branches that added a security fragment.

    A fragment moved into the security section after its fix merged reads as
    an addition too: the pathspec leaves its old name out, so git has nothing
    to pair it with as a rename. A rename from one security fragment to
    another is no addition, and the fix keeps the commit that first added it.
    """
    refs = [f"{REF_NAMESPACE}/{b}" for b in config.branches]
    out = git(
        config.repository,
        "log",
        "--diff-filter=A",
        "--format=commit %H %cs",
        "--name-only",
        *refs,
        "--",
        FRAGMENTS,
    )
    fixes: list[Fix] = []
    commit = merged = ""
    for line in out.splitlines():
        if line.startswith("commit "):
            _, commit, merged = line.split()
            continue
        if not line:
            continue
        name = Path(line).name.removesuffix(".security.md")
        # A name that cannot be printed still marks a fix: skipping it would
        # be a check that passes by not looking.
        if not NAME_SHAPE.fullmatch(name):
            name = "(fragment name withheld)"
        fixes.append(Fix(commit=commit, merged=merged, name=name))

    for fix in fixes:
        tags = git(config.repository, "tag", "--contains", fix.commit).split()
        fix.releases = {t for t in tags if RELEASE_TAG.fullmatch(t)}
    return fixes


def deployed_version(url: str, timeout: float) -> tuple[str | None, str]:
    """The release a deployment reports, or None and why it names none."""
    request = urllib.request.Request(
        url.rstrip("/") + "/api/version/public",
        # A name of its own, so an edge rule can let the check through.
        headers={"Accept": "application/json", "User-Agent": "mascope-fix-distance"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_ANSWER)
    except urllib.error.HTTPError as exc:
        return None, f"answered HTTP {exc.code}"
    except (OSError, http.client.HTTPException) as exc:
        reason = getattr(exc, "reason", None) or type(exc).__name__
        return None, f"no answer ({reason})"
    try:
        version = json.loads(body)["data"]["version"]
    except (ValueError, KeyError, TypeError):
        return None, "answered no version"
    if not isinstance(version, str) or not VERSION_SHAPE.fullmatch(version):
        return None, "answered no version"
    if version in ("unknown", "latest"):
        # "latest" names the rolling tag, not a build: it could be any release.
        return None, f"runs {version!r}, which names no release"
    if not RELEASE_TAG.fullmatch(version):
        return None, f"runs {version}, which is not a release"
    return version, ""


def check(config: Config) -> tuple[list[str], dict[str, int]]:
    """The findings, one line each, and the counts for the heartbeat."""
    fix_list = marked_fixes(config)
    findings: list[str] = []
    behind = unreadable = 0
    for alias, url in config.deployments.items():
        version, why = deployed_version(url, config.timeout)
        if version is not None and not git_has_tag(config.repository, version):
            version, why = None, f"runs {version}, a tag the repository does not have"
        if version is None:
            unreadable += 1
            findings.append(f"unreadable: {alias} {why}")
            continue
        missing = [f for f in fix_list if version not in f.releases]
        if missing:
            behind += 1
        for fix in missing:
            findings.append(f"behind: {alias} runs {version}, missing {fix.describe()}")
    counts = {
        "fixes": len(fix_list),
        "unreleased": sum(1 for f in fix_list if not f.releases),
        "deployments": len(config.deployments),
        "behind": behind,
        "unreadable": unreadable,
    }
    return findings, counts


def git_has_tag(repository: Path, tag: str) -> bool:
    try:
        git(repository, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}")
    except subprocess.CalledProcessError:
        return False
    return True


def ping(url: str, suffix: str = "", body: str = "") -> None:
    """A heartbeat. Never fatal: an unreachable heartbeat is the switch's to see."""
    for attempt in range(3):
        try:
            urllib.request.urlopen(
                urllib.request.Request(url + suffix, data=body.encode()), timeout=10
            ).close()
            return
        except OSError:
            if attempt < 2:
                time.sleep(2**attempt)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"security-fix-distance: {exc}", file=sys.stderr)
        return 2

    ping(config.heartbeat, "/start")
    try:
        fetch(config)
    except FetchError as exc:
        print(f"security-fix-distance: fetch failed: {exc}", file=sys.stderr)
        ping(config.heartbeat, "/fail", "fetch failed: the check is blind")
        return 3

    try:
        findings, counts = check(config)
    except subprocess.SubprocessError as exc:
        print(f"security-fix-distance: git failed: {exc}", file=sys.stderr)
        ping(config.heartbeat, "/fail", "git failed: the check is blind")
        return 3

    summary = " ".join(f"{key}={value}" for key, value in counts.items())
    for line in findings:
        print(line)
    if findings:
        ping(config.heartbeat, "/fail", summary)
        return 1
    ping(config.heartbeat, "", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
