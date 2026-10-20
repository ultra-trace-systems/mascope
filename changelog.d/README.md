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

- `<branch>` is your branch name, with `/` - and anything else but
  letters, digits, `.`, `-` and `_` - written as `-`: `mas-123/fix-the-thing`
  becomes `mas-123-fix-the-thing`, `deps/bump-vite-7.1` becomes
  `deps-bump-vite-7.1`. Any name works so long as no other open pull request
  uses it, and a branch name is the one that is sure not to.
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
uv run python tooling/changelog.py preview
```

The script needs nothing but Python 3.10 or later, so
`python3 tooling/changelog.py ...` works as well wherever `python3` is
installed; CI runs it that way.

## What CI checks

The "Lint and format" job runs `tooling/changelog.py check`, which fails a
pull request that

- writes into the `[Unreleased]` section of `CHANGELOG.md`;
- adds a file here whose name or text does not compile (no `- ` bullet, an
  unknown section, a heading, a paragraph that has lost its indent, the note
  that stands under `[Unreleased]`);
- writes an entry that is already in `CHANGELOG.md` or another fragment.

On a pull request into `master`, which releases what it carries, the check
also fails while any fragment is left uncompiled.

### A branch with an entry in `CHANGELOG.md`

A branch that still writes its entry under `[Unreleased]` (one opened before
the fragments, or one that edited `CHANGELOG.md` by mistake) moves it into a
fragment once, after rebasing onto develop:

1. `git rebase origin/develop`. If it stops on `CHANGELOG.md`, resolve that
   conflict **keeping both sides**: taking develop's side alone drops the
   branch's own entry. Then finish the rebase. It may not stop at all, since
   git can merge an entry under `[Unreleased]` without a conflict, which is
   why the next step asks the check rather than the rebase.
2. Run `uv run python tooling/changelog.py check`. If it says `[Unreleased]`
   has been edited, run

   ```sh
   uv run python tooling/changelog.py extract
   ```

   It moves every entry under `[Unreleased]` into a fragment named after the
   branch and leaves only the note under the heading. It leaves out the
   entries that `CHANGELOG.md` or another fragment holds already, which is
   what the entries a both-sides resolution brings back from develop are.
3. Fold that into the commit that added the entry, so every commit passes on
   its own: pull requests are rebase-merged, so each commit lands on develop
   as written.

   ```sh
   git add CHANGELOG.md changelog.d/
   git commit --fixup=<the commit that added the entry>
   GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash origin/develop
   ```

   `GIT_SEQUENCE_EDITOR=:` takes the todo list as git writes it, so nothing
   opens an editor; git before 2.44 needs `-i` for `--autosquash`. Afterwards
   `git diff origin/develop -- CHANGELOG.md` is empty and the check passes.

Run `extract` after the rebase, not before: before it, `[Unreleased]` also
holds entries the branch inherited from develop, which develop has since
moved into fragments of their own, and `extract` would file them under the
branch's name too. In a rebase that stopped on `CHANGELOG.md`, `extract`
works as well and names the fragment after the branch being rebased; then
`git add` and `git rebase --continue` put the fragment into the commit being
replayed.

## Cutting a release

`uv run python tooling/changelog.py compile X.Y.Z` writes every fragment into
`CHANGELOG.md` as `## [X.Y.Z] - <date>`, deletes the fragments, and sets
`CITATION.cff`'s `version` and `date-released`. Within each section the
entries come in merge order: the order of the commits that added their
fragments, with file-name order between fragments one commit added. Running
it again for the same version, before `vX.Y.Z` is tagged, adds whatever merged
since to that section; once the tag exists it refuses, since the notes are
published. The developer guide's "Cutting a citable release" has the rest of
the procedure.
