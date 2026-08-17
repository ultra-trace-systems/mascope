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

## Global role

Every user account has a single global role (guest, editor, admin, or owner) set at registration. The global role controls:

- **Who can log in and access the application** — all roles can.
- **User management** — admins can register and manage guests and editors; owners can manage all users including other admins and owners.
- **Shared reference data** — instrument configurations, ionization modes, target compounds, and other system-wide resources. Guests can read; editors and above can create and modify.
- **Calibration** — all users can view calibration state; only admins can run calibrations (they affect data across workspaces).

Global admins and owners also receive automatic membership in all instrument workspaces (see below).

## Workspaces

A workspace is a container that groups related data. All measurement data in Mascope lives inside a workspace:

```
Workspace → Dataset → Sample Batch → Sample Item
```

Each workspace has its own member list. A user's **workspace role** (guest, editor, admin, or owner) in a given workspace determines what they can do with the data inside it.

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
- **Deleting / reprocessing files**: requires at least **admin** in the instrument workspace.

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
| Run calibration                              | Global admin role or higher                         |
| Access data via Jupyter / SDK                | Access token + same permissions as the user account |
