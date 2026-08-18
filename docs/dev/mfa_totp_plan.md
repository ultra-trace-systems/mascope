# MFA (TOTP) plan

Goal: a second authentication factor for the interactive web session, using
time-based one-time passwords (RFC 6238) from any authenticator app.

The design constraint that shapes everything below: Mascope deployments are
single-tenant and operator-run, with no mail infrastructure and no support desk.
Every recovery path must therefore work offline, and every lockout must be
resolvable by someone with shell access to the host.

## 1. Scope

In scope:

- TOTP enrollment, challenge, and verification for cookie/JWT web sessions.
- Recovery codes, admin-initiated reset, and a CLI escape hatch.
- A per-deployment policy that can require MFA at or above a chosen role.

Out of scope, deliberately:

- **WebAuthn / passkeys.** Better security and better UX, but they bind to an
  origin, and deployments are reached by a different hostname each. Revisit once
  TOTP is in place; the data model below leaves room for a second factor type.
- **Email or SMS codes.** There is no mailer (see §7), and SMS is both weaker
  than TOTP and a per-deployment billing relationship.
- **Bearer access tokens** (`mascope_sdk`, agents, file converter). They are
  non-interactive and cannot render a challenge. What changes is how they are
  *minted* — see §5.

## 2. Why the second factor gets its own step

The tempting implementation is to mirror `must_change_password`: issue the real
session cookie at login, mark the session as "MFA not yet satisfied", and let
the gated dependency wrappers in `api/new/auth/dependencies.py` refuse
everything until it is. That reuses a proven pattern and is far less code.

Do not do it. It means a valid `mascope_auth` cookie exists before the second
factor is presented, so the factor is an application-layer check over an
already-minted session rather than a precondition for having one. Two concrete
consequences:

- `socket/auth/token.py` authenticates Socket.IO connections from that same
  cookie. A cookie that exists pre-verification has to be refused separately on
  the push surface — the exact split-enforcement failure this codebase has hit
  before.
- Any future code path that trusts "has a valid session cookie" inherits the
  hole.

Instead, **the cookie is only ever minted after both factors pass**:

1. `POST /api/auth/login` with credentials. If the account has no MFA, behave
   exactly as today (session cookie, unchanged). If it does, return **no session
   cookie** — return a short-lived, single-use *pending token* and an indicator
   that a second factor is required.
2. `POST /api/auth/mfa/verify` with the pending token plus a 6-digit code (or a
   recovery code) mints the real session cookie.

Because the session cookie does not exist until step 2 succeeds, Socket.IO, the
role dependencies, and every existing route need **no changes at all** — which
is the property worth paying for.

This means not using `fastapi_users.get_auth_router(auth_backend_jwt)` verbatim;
the login path is replaced with a wrapper that calls the same authentication
and, conditionally, the same transport. The logout route and the rest of the
sub-router stay as they are.

### The pending token

- A JWT with its own audience, `mascope-users:mfa`, so it can never be mistaken
  for a session token by the existing strategy (which validates
  `JWT_AUDIENCE = ["mascope-users:auth"]`).
- Signed with a secret derived through the existing `_derive_token_secret()`
  helper in `api/new/auth/config.py`, so it is per-deployment and
  cryptographically independent of the session signing key. No new operator
  secret for this one.
- Lifetime 5 minutes.
- **Single use.** The stateless JWT carries a `jti`; Redis holds the burned ids
  until expiry. Redis is already a hard dependency for rate limiting, so this
  adds no new infrastructure. Burning on both success and lockout stops a
  captured pending token from being re-driven.
- Carries the user id and nothing else. It is not a session: no route accepts it
  except `/api/auth/mfa/verify`.

## 3. Data model

One migration, in the style of
`20260814_b7e4c9a2f1d3_add_forced_password_change_columns.py`.

On `user`:

| Column | Type | Notes |
| --- | --- | --- |
| `mfa_secret` | `String`, nullable | Base32 TOTP seed, encrypted at rest (below). NULL when not enrolled. |
| `mfa_enabled` | `Boolean`, default false | True only after an enrollment code has been verified — an unconfirmed secret must never gate login. |
| `mfa_confirmed_at` | `TIMESTAMP(tz)`, nullable | When enrollment completed. |
| `mfa_last_timestep` | `BigInteger`, nullable | Last accepted TOTP counter; see replay below. |

New table `user_recovery_code`: `id`, `user_id` (FK, cascade delete),
`code_hash`, `used_at` (nullable). Ten codes issued per enrollment.

Three details that are easy to get wrong:

**Encrypt `mfa_secret` with its own key, not one derived from the JWT secret.**
The derivation helper is right for the pending-token secret because those tokens
live five minutes. TOTP seeds live for years, and the JWT secret is rotatable —
it was rotated fleet-wide once already. Deriving the encryption key from it
would silently make every enrolled secret undecryptable at the next rotation,
locking out every user at once. Add
`.runtime/secrets/mfa_encryption_key.txt` alongside the existing three secrets
in `docs/hosting.md`, and treat losing it as equivalent to resetting everyone's
MFA (which the escape hatch in §6 makes survivable).

**Hash recovery codes with SHA-256, not the password hasher.** Recovery codes
are high-entropy values this system generates, not user-chosen secrets, so there
is no brute-force margin to buy with a slow KDF. A plain digest also makes
redemption an indexed lookup instead of iterating a password hash over every
unused code.

**Store the last accepted timestep.** TOTP verification uses a ±1 window to
tolerate clock drift, which means a code stays valid for about 90 seconds — long
enough to be replayed by anyone who observed it. Accept a code only if its
counter is strictly greater than `mfa_last_timestep`, then store it.

## 4. Backend surfaces

New router `api/new/auth/mfa/`, matching the layout of the sibling
`access_token/` and `pairing/` packages (`config.py`, `exceptions.py`,
`routes.py`, `schemas.py`, `service.py`).

| Route | Auth | Purpose |
| --- | --- | --- |
| `POST /api/auth/mfa/verify` | pending token | Second step of login. Accepts a TOTP or recovery code; mints the session cookie. |
| `POST /api/auth/mfa/enroll` | session | Generates a secret, returns the `otpauth://` URI. Does not enable anything. |
| `POST /api/auth/mfa/enroll/confirm` | session | Verifies a code against the pending secret, sets `mfa_enabled`, returns the ten recovery codes **once**. |
| `DELETE /api/auth/mfa` | session + fresh code | Disarms the user's own MFA, unless policy requires it for their role. |
| `GET /api/auth/mfa/status` | session | Enrollment state and count of unused codes, so the UI can nag at a low count. |
| `POST /api/auth/mfa/reauth` | session | Presents a code to open the step-up window (§5) without signing in again. |
| `POST /api/users/admin/{user_id}/mfa/reset` | admin/owner | Clears another account's MFA. Sits with the existing admin actions in `api/new/users/admin/routes.py`. |

Return the `otpauth://` URI and let the frontend render the QR code; that keeps
an image dependency out of the backend.

Rate limits, reusing `api/lib/rate_limit.py`:

- `/mfa/verify` — per-IP via `rate_limit(...)`, plus a per-user counter through
  `enforce_user_rate_limit` keyed on the pending token's subject. Six digits is
  a million-value space; without a limit, a patient attacker with a valid
  password wins.
- After 5 failed codes on one pending token, burn the token and force a restart
  from the password step.
- `/mfa/enroll/confirm` — limited too, so enrollment is not a code oracle.

Login must not become an MFA-enrollment oracle either: an unknown account and a
known account without MFA have to be indistinguishable from the outside. Since a
wrong password already fails before any MFA branch is reached, this holds as
long as the "MFA required" signal is only ever returned *after* the credentials
verify.

## 5. Bypass surfaces to close

A second factor on the login form is worth little if these stay open. The
mechanism is a **step-up window** (`mfa/reauth.py`): presenting a code records a
marker in Redis for five minutes, and the routes below require one. Completing a
sign-in or an enrollment opens the window too, so the ordinary path - sign in,
then mint a token - asks for one code, not two. Accounts with no factor pass
straight through, or these routes would break every deployment not using MFA.

The check fails **closed** when Redis is unreachable, unlike the rate limiters,
which fail open. Those blunt abuse and are safe to skip; this one decides
whether to hand out a year-long credential, and answering "allow" when the
record cannot be read would turn an outage into the bypass it exists to close.

- **Access-token minting** (`POST /api/auth/access_token/regenerate`).
  `ACCESS_TOKEN_EXPIRATION_SECONDS` is 360 days and the raw token is the primary
  key. A stolen session could otherwise mint a year-long credential, not bound
  to that session, and never see a challenge again. This is the bypass that
  matters most.
- **Device pairing** (`POST /api/auth/pairing/approve`). Audited: `start` and
  `poll` are unauthenticated by design and mint nothing; `approve` is the step a
  person performs from a session, and it creates the same year-long token. Same
  requirement.
- **Password change - deliberately NOT gated.** The first draft of this plan
  called for it. On inspection `PATCH /api/users/me/creds` already verifies the
  current password, which a session thief does not have, so a code adds nothing
  a stolen session could defeat. It would add a real risk: that route is the
  only way out of a forced password change, so a new required field there is a
  lockout waiting for a frontend/backend version skew.
- **Gate ordering.** MFA is proven before the session exists; the
  `must_change_password` gate acts on an existing session. So the order for a
  new account with both pending is: password → TOTP challenge (if already
  enrolled) → forced password change → enrollment (if policy requires it). Write
  the test for that sequence; it is the part most likely to be wrong.

## 6. Policy, recovery, and lockout

**Policy.** One setting, `mfa_required_min_role`, defaulting to disabled.
Deployments that want it set it to `admin` or `guest` (meaning everyone). An
account below the threshold may still enroll voluntarily. Users above it who
have not enrolled get an enrollment screen after login, in the same "authenticated
but not yet in the app" state that `must_change_password` already models — the
frontend store's `sessionId()` helper deliberately returns null for such a user,
so their stores stay unloaded behind the screen.

**Recovery, in order of escalation:**

1. Recovery codes — shown once at enrollment, downloadable, single use.
2. Admin reset — any admin or owner clears MFA for a lower-ranked account.
3. **CLI escape hatch** — `mascope prod mfa reset <email>`, operating directly on
   the database from the host. This is not optional. Deployments are
   self-hosted; if the only owner loses their phone and their codes, nothing in
   the web UI can help them, and there is no support desk to call.

Guard the owner case the way `User.count_other_owners` already guards owner
deletion: an owner may not disarm their own MFA if policy requires it and they
are the last one, and the reset route may not be used to strand a deployment
without a reachable owner.

## 7. No email dependency

Every path above works without a mailer, which is deliberate: there is no SMTP
anywhere in the backend, `on_after_forgot_password` is a stub, and the two
`fastapi_users` routers for reset and verification remain commented out in
`api/new/auth/routes.py`. Accounts are admin-provisioned and passwords are
handed over out of band. Adding email would mean per-deployment SMTP
configuration and DNS-level deliverability work (SPF/DKIM/DMARC for whatever
domain each deployment sends as), which buys nothing this feature needs.

## 8. Frontend

- `lib/panes/PaneLogin.vue` gains a second step: on an "MFA required" response
  it swaps the credential fields for a code field, holding the pending token in
  component state only. Include a "use a recovery code instead" affordance.
- `stores/auth.js` gains a `mfaPending` state. The store already models a user
  who is authenticated but not yet in the app, so the branch structure exists;
  the pending state sits *earlier* than that, before `identify()` can return
  anything.
- Enrollment UI in the profile area: QR plus manual-entry secret, a confirm
  field, then the recovery codes with a download and an explicit "I have saved
  these" acknowledgement.
- Admin user table gains an MFA column and a reset action.
- A code field is a one-time-code input: `inputmode="numeric"`,
  `autocomplete="one-time-code"`, and paste-friendly.

## 9. Tests

Unit (`server/backend/tests/unit/`): TOTP acceptance at the window edges,
replay refused via `mfa_last_timestep`, recovery code single use, pending token
rejected by the session strategy on audience mismatch, secret encrypt/decrypt
round-trip.

Integration (`tests/integration/api/auth/`, beside `test_login_flow.py`): the
full two-step flow; login returning no cookie when MFA is on; verify minting
one; pending token refused on a second use; the gate-ordering sequence from §5.

Security (`tests/system/security/`, beside `test_login_rate_limit.py`): verify
endpoint rate limited and locked out after N failures; **no `mascope_auth`
cookie present on any response in the pending state**; Socket.IO connection
refused with only a pending token; access-token minting refused without a fresh
code.

Then mutation-check the new control: disable each piece in turn and confirm that
exactly its own assertions fail while the positive controls still pass. Every
review round in this area so far has turned up at least one check that could not
fail, and always among the siblings of whatever was under review — so check the
neighbours too, not just the new tests.

## 10. Phasing

1. **Core.** Migration, service, enrollment routes, two-step login, login-pane
   step. Enrollment and challenge are meaningless apart — shipping enrollment
   alone would let users arm a factor that never gets checked — so they land
   together.
2. **Bypass closure.** The step-up window, and the requirement on access-token
   minting and pairing approval.

   Landed in the same change as step 1, not after it. A release carrying
   enrollment but not this would offer users a factor that anyone holding their
   session can permanently sidestep by minting a token — worse than offering no
   factor, because it reads as protection without being one.
3. **Policy and administration.** `mfa_required_min_role`, the enrollment gate,
   admin reset, CLI escape hatch, admin UI column.

## 11. Open decisions

- Threshold for `mfa_required_min_role` in the managed deployments, and whether
  customer self-hosted deployments get a different default. Default disabled is
  assumed above.
- Whether to add "remember this device" (a signed, revocable device cookie).
  It is the main UX complaint about TOTP, and it is a whole second trust store —
  recommend deferring past the first release.
- Whether the 360-day access-token lifetime should shrink at the same time.
  Independent of MFA, but the same threat model, and MFA makes the disparity
  starker.
