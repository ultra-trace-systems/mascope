# Changelog fragments

Every pull request that changes what a user, an operator or a program using
Mascope sees describes the change in a file of its own here, not in
`CHANGELOG.md`. Cutting a release compiles the files into `CHANGELOG.md` and
deletes them.

Why: when every pull request appended to the one `[Unreleased]` section of
`CHANGELOG.md`, each merge left every other open pull request with an entry
conflicting on GitHub. Files of their own never touch the same lines, so two
pull requests never conflict over the changelog.

## Adding an entry

Name the file `<branch>.<section>.md`:

- `<branch>` is your branch name, with any `/` written as `-`
  (`mas-123-fix-the-thing`). Any name works so long as no other open pull
  request uses it, and a branch name is the one that is sure not to.
- `<section>` is where the entry goes in the release: `added`, `changed`,
  `deprecated`, `removed`, `performance`, `fixed` or `security`.

The file holds the entry exactly as `CHANGELOG.md` is to show it - a bullet
starting `- `, continuation lines indented two spaces, a bold lead sentence as
the other entries have:

```markdown
- **A sample file's scans can be read without their centroids.**
  `GET /api/sample/files/{id}/scans` returns the scan table of a raw Orbitrap
  file: ...

  A second paragraph of the same entry stays indented two spaces.
```

Two entries for one section go in the same file, a blank line between them;
entries for two sections go in two files (`mas-123-x.added.md`,
`mas-123-x.fixed.md`). No heading: the file name says the section.

To see the `[Unreleased]` section the fragments add up to:

```sh
python3 tooling/changelog.py preview
```

## What CI checks

The "Lint and format" job runs `python3 tooling/changelog.py check`, which
fails a pull request that

- writes into the `[Unreleased]` section of `CHANGELOG.md`;
- adds a file here whose name or text does not compile (no `- ` bullet, an
  unknown section, a heading, a paragraph that has lost its indent);
- writes an entry that is already in `CHANGELOG.md` or another fragment.

A branch that still has an entry under `[Unreleased]` - one opened before the
fragments, say - moves it into a fragment named after the branch with

```sh
python3 tooling/changelog.py extract
```

On a pull request into `master`, which releases what it carries, the check
also fails while any fragment is left uncompiled.

## Cutting a release

`python3 tooling/changelog.py compile X.Y.Z` writes every fragment into
`CHANGELOG.md` as `## [X.Y.Z] - <date>`, in merge order within each section,
deletes the fragments, and sets `CITATION.cff`'s `version` and
`date-released`. Running it again for the same version adds whatever merged
since to that section. The developer guide's "Cutting a citable release" has
the rest of the procedure.
