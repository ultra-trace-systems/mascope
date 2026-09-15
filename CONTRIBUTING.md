# Contributing to Mascope

Thank you for your interest in contributing! This document covers how to set up a development environment, our conventions, and how to get changes merged.

## Development setup

Mascope is a uv-managed Python monorepo (Python 3.12) with a VueJS frontend (Node 22). The setup scripts in `tooling/` install all prerequisites.

**Windows** (requires PowerShell 7):

```sh
git clone https://github.com/ultra-trace-systems/mascope.git && cd mascope && .\tooling\windows.ps1 install
```

**Ubuntu** (22.04 LTS or later):

```sh
git clone https://github.com/ultra-trace-systems/mascope.git && cd mascope && ./tooling/ubuntu.sh install
```

Then launch the dev environment:

```sh
mascope dev run
```

See the [developer guide](docs/dev/developer_guide.md) for the full CLI reference, runtime modes, and architecture overview.

## Running tests

To run the full test suite via the CLI, run:

```sh
mascope test run
```

For more details, run:

```sh
mascope test --help
```

## Code style

Python code is formatted and linted with Ruff (config in `pyproject.toml`); formatting runs automatically on save with the repo's VS Code settings, or run `uv run ruff format && uv run ruff check --fix`.

## Branches and pull requests

- Open pull requests against the `develop` branch. `master` is the release branch.
- CI must pass before a PR is reviewed.
- Keep PRs focused; separate unrelated changes into separate PRs.

## Commit messages

We follow [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): description`. Types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `ci`, `chore`. Common repository scopes (non-exhaustive):

`(ui)` `(backend)` `(db)` `(api)` `(agent)` `(lib)` `(sdk)` `(cli)` `(test)` `(release)`

Breaking changes are marked with `!` after the type/scope.

## Reporting bugs and proposing features

Use the issue templates. For questions and open-ended discussion, join our [Discord community](https://discord.gg/R5kEKJcKe8).

## Contributor License Agreement

Before we can merge your first pull request, we ask you to accept the
[Ultra Trace Systems Individual Contributor License Agreement](https://github.com/ultra-trace-systems/cla/blob/main/ICLA.md)
(ICLA). It is a one-time step, and one acceptance covers Mascope,
[Peaky](https://github.com/ultra-trace-systems/peaky), and our other open-source
projects.

In short: you keep the copyright to your work and may use it however you like;
you license it to Ultra Trace Systems so we can distribute Mascope under
Apache-2.0 and, where we need to, under other terms (for example to customers of
the managed service); and you confirm that you are entitled to contribute it.
Mascope itself stays Apache-2.0. The agreement is based on the
[Harmony](https://www.harmonyagreements.org) individual agreement.

**How to accept.** When you open your first pull request, the CLA assistant posts
a comment asking you to sign. Reply on the pull request with exactly

    I have read the CLA Document and I hereby sign the CLA

and the check passes on its next run (push a commit, or comment `recheck`, if it
has not updated by itself). Your GitHub username, the pull request, and the time
are recorded in the public
[signature register](https://github.com/ultra-trace-systems/cla). Every commit
author on the pull request has to have accepted, and commits must be authored
with an email address linked to a GitHub account so the assistant can tell who
wrote them. Ultra Trace's own developers and the bots are exempt: their work is
the company's already, and they are in the workflow's allowlist.

**If your contribution includes work that is not yours** - code adapted from
another project, a vendored file, data, images - keep it in its own commit or
file, say in the pull request where it comes from and under which licence, add
the attribution to [NOTICE](NOTICE), and make sure the licence is compatible with
Apache-2.0 (the allowlist in `tooling/check-licenses.py` is the reference). The
agreement asks you to confirm you have done this.

**If you contribute as part of your job**, make sure your employer agrees; the
agreement asks you to confirm that too. If your employer needs a corporate
agreement instead, or for any other question about the agreement, write to
support@ultratrace.eu.
