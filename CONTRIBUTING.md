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

## Certifying your contributions

By submitting a contribution, you certify that you have the right to do so, and you agree that your contribution is licensed to the project under [Apache-2.0](LICENSE) - the same license as the project itself.
