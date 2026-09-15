# Authorization

Mascope controls access through two layers: a **global role** assigned to every user account, and **workspace membership** that determines what data each user can see and modify.

## Roles

Four roles are available, listed from least to most privileged:

| Role       | Description                                                                                    |
| ---------- | ---------------------------------------------------------------------------------------------- |
| **Guest**  | Read-only. Can view data, export spreadsheets, and use Jupyter notebooks via access tokens.    |
| **Editor** | Everything a guest can do, plus create, update, and delete data.                               |
| **Admin**  | Everything an editor can do, plus manage users (up to editor level) and workspace membership.  |
| **Owner**  | Full control. Can manage admins and owners, delete workspaces, and perform all system actions. |

Each higher role inherits all permissions of the roles below it.

The two layers are independent, and a global role does not cap the workspace roles an account may hold. An account with the global **guest** role that is an **editor** or **admin** of some workspace can create and modify data there, and the descriptions above should be read as instance-wide capability rather than as a ceiling. When auditing who can do what, read both layers — demoting someone's global role does not withdraw what their workspace memberships grant.

## Global role

Every user account has a single global role (guest, editor, admin, or owner) set at registration. The global role controls:

- **Who can log in and access the application** — all roles can.
- **User management** — admins can register and manage guests and editors; owners can manage all users including other admins and owners.
- **Shared reference data** — instrument configurations, ionization modes, target compounds, and other system-wide resources. Guests can read; editors and above can create and modify. Note that editing shared reference data is retroactive and instance-wide: changing an ionization mode changes how samples already processed under it are calibrated and matched, and flags every affected batch for recalibration or rematching — in every workspace, not only the editor's own.

  Because a mode is read instance-wide, the calibration and diagnostic collections it names must be readable by the editor setting them, and a collection an ionization mode uses cannot afterwards be narrowed into a single workspace. Otherwise one workspace's private collection would end up governing how every other workspace's samples are matched.
- **Calibration** — all users can view calibration state. Running one is governed by the instrument workspace, not the global role; see below.

Global admins and owners also receive automatic membership in all instrument workspaces (see below).

## Two-factor authentication

Any account can turn on two-factor authentication (TOTP) from its settings: scan
a QR code with an authenticator app, then enter a code to confirm. Ten
single-use recovery codes are shown once at that point - they are the only way
back in if the phone is lost, and are not recoverable afterwards.

A deployment can also require it. Setting `mfa_required_min_role` under
`[backend]` in the config TOML names the lowest role it applies to (`admin`
covers admins and owners, `guest` covers everyone); unset, the default, requires
it of nobody. An account covered by the requirement is held at an enrolment
screen after signing in until it sets a factor up, and cannot turn it off again.

Two actions ask for a current code even in an open session: generating an API
access token, and approving an agent pairing. Both hand out credentials valid
for a year that are not tied to the browser session, so a session on its own is
not enough to obtain one. Signing in or enrolling counts as presenting a code
for the next five minutes, so this rarely means entering one twice.

**If an authenticator is lost together with its recovery codes**, an
administrator clears the factor for guests and editors, and an owner for anyone
but themselves; the account then enrols again. If nobody who could do that can
sign in, the deployment operator runs `mascope prod mfa reset <email>` on the
host. Clearing a factor never reveals or changes a password.

## Workspaces

A workspace is a container that groups related data. All measurement data in Mascope lives inside a workspace:

```
Workspace → Dataset → Sample Batch → Sample Item
```

Each workspace has its own member list. A user's **workspace role** (guest, editor, admin, or owner) in a given workspace determines what they can do with the data inside it.

`GET /api/workspaces` reports this as `my_role` on each workspace, so the app can disable an action rather than offer one that would be refused. It describes membership only: a global admin also bypasses the instrument-workspace checks on raw files without holding a membership, so a check on a file-level action reads `my_role` *or* the global role. Acquisition workspaces additionally report `instrument`, naming the instrument whose raw files they hold, so a client does not have to rebuild the workspace name from a prefix to find them.

### What each workspace role can do

| Action                            | Guest | Editor | Admin | Owner |
| --------------------------------- | :---: | :----: | :---: | :---: |
| View data (spectra, peaks, etc.)  |   ✓   |   ✓    |   ✓   |   ✓   |
| Export data                       |   ✓   |   ✓    |   ✓   |   ✓   |
| View match results                |   ✓   |   ✓    |   ✓   |   ✓   |
| Rate matches                      |   ✓   |   ✓    |   ✓   |   ✓   |
| Create / edit / delete data       |       |   ✓    |   ✓   |   ✓   |
| Upload and process files          |       |   ✓    |   ✓   |   ✓   |
| Run matching                      |       |   ✓    |   ✓   |   ✓   |
| Add or remove workspace members   |       |        |   ✓   |   ✓   |
| Change member roles               |       |        |   ✓   |   ✓   |
| Edit workspace name / description |       |        |   ✓   |   ✓   |
| Delete the workspace              |       |        |       |   ✓   |

> Any member can remove **themselves** from a workspace regardless of role. Removing another member requires admin or higher.

### Role ceiling

When adding or updating workspace members, the assigning user can only grant roles up to their own level:

- An **admin** can assign guest, editor, or admin — but not owner.
- An **owner** can assign any role, including owner.

The same rule applies to global user management: a global admin can register users as guests or editors, but only a global owner can create admin or owner accounts.

## Instrument workspaces

Mascope automatically creates a **system workspace** for each instrument. These workspaces are named after the instrument (e.g. _Acquisitions Vocus_) and contain the acquisition datasets where uploaded files are stored.

### How instrument workspaces are created

When a file is uploaded for an instrument that does not yet have a workspace, Mascope:

1. Creates a new system workspace named `Acquisitions <instrument>`.
2. Adds the uploading user as **owner**.
3. Adds all global **admins** and **owners** as members with matching roles.
4. Global guests and editors are **not** automatically added — they must be invited.

### Access to raw files

Access to sample files (the raw measurement data uploaded from instruments) is controlled through the instrument workspace:

- **Viewing file lists**: a user sees files from instruments whose workspace they belong to, plus any files linked to samples in their other workspaces.
- **Uploading files**: requires at least **editor** in the instrument workspace (or the upload creates the workspace and the user becomes owner).
- **Deleting / reprocessing files**: requires at least **admin** in the instrument workspace, *or* admin in a workspace holding a sample item that references the file.
- **Running an m/z calibration**: requires at least **admin** in the instrument workspace, for the same reason as reprocessing — a calibration is written onto the file, so every sample item referencing it, in any workspace, sees the change.

The instrument role authorises the write and says nothing about the workspace the addressed batch or sample sits in, so a calibration can reach an object the caller could not have listed. The confirmation the route returns therefore names that batch or sample only when a guest-level read would have returned the name; otherwise it is left out. Note that the progress notifications the background job emits to the caller are not filtered this way.

Calibration takes that rule in its **strict** form: unlike deleting or reprocessing, there is no fallback through the workspace an item happens to sit in. Membership of the workspace holding the sample is not sufficient and does not grant a calibration. (The fallback on delete and reprocess is long-standing and is documented here as it behaves, not as an endorsement; the two paths are worth reconciling separately.)

Fitting a calibration is separate and lighter: `POST /api/calibration/mz_fit` computes a fit and returns it without writing anything, so it needs only **editor** in the workspace holding the sample — or **admin** in that file's instrument workspace, since a caller who may write the calibration outright must be able to preview what it is about to write. Writing the fit to the file is the `mz_apply` step, which takes the instrument-workspace admin rule above.

## User-created workspaces

Users with a global role of **editor** or above can create their own workspaces. The creator automatically becomes the workspace **owner** and can then invite other users as members.

User-created workspaces are independent of instrument workspaces. They contain datasets, batches, and samples that reference the raw files stored in instrument workspaces.

## Superusers

Superuser is a special flag on a user account (not a role). Superusers bypass all workspace membership checks — they can access any workspace and any data without being an explicit member. This is intended for system administration and automated services only.

## Passwords

Passwords must be at least **12 characters**. There are no character-class rules — length is the strongest lever, and rules that demand a symbol and a digit mostly produce predictable substitutions. A password is also rejected if it appears in a bundled list of the most commonly used and breached passwords, or if it contains the account's own email address or user name.

A password an administrator issues is **temporary**. Resetting another user's password, and creating a new account, both leave that account required to choose its own password the next time it signs in — so only its holder ever knows the password in use. The account signs in with the issued password as normal and is then held at a password screen until it sets a new one.

An owner can require the same of **every** account at once, from **Manage users**. It puts every account through the current policy, whatever the reason for asking — a tightened rule, a periodic refresh, or a concern about credentials. It is not a lockout: everyone keeps signing in with their existing password until they replace it. See [maintaining.md](maintaining.md#user-accounts) for the operator's view, including how to undo it.

## Access tokens

Each user can generate a personal **access token** for programmatic access (e.g. from Jupyter notebooks or the Mascope SDK). The token carries the same global role and workspace memberships as the user account. Tokens can be regenerated at any time, which invalidates the previous one. Admins and owners can also revoke tokens for other users.

## Quick reference

| I want to …                                  | Required                                            |
| -------------------------------------------- | --------------------------------------------------- |
| View data in a workspace                     | Member of that workspace (any role)                 |
| Upload files for an instrument               | Editor in the instrument's workspace                |
| Create a new workspace                       | Global editor role or higher                        |
| Add someone to a workspace                   | Admin in that workspace                             |
| Delete a workspace                           | Owner of that workspace                             |
| Register new user accounts                   | Global admin (guests/editors) or owner (any role)   |
| Manage instrument configs / ionization modes | Global editor role or higher                        |
| Run calibration                              | Admin in the instrument's workspace                 |
| Access data via Jupyter / SDK                | Access token + same permissions as the user account |
