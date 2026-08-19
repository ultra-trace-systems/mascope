# Two-factor authentication

Two-factor authentication adds a second step to signing in: after your
password, Mascope asks for a six-digit code from an authenticator app. A
password alone — guessed, phished, or reused — is then no longer enough to
reach your account.

Any account can turn it on. A server can also **require** it for some or all
roles; an account covered by the requirement is walked through this same setup
right after signing in, and reaches the rest of the app once it is done.

You need an authenticator app that produces TOTP codes — Google Authenticator,
Microsoft Authenticator, Aegis, or a password manager with a built-in
authenticator all work.

## Turn it on

1. Open the **Home menu** (the house icon in the top bar) and switch to
   **Settings**.
2. Under *Account*, click **Two-factor authentication** and follow the dialog.
3. Scan the QR code with your authenticator app — or type the setup key into
   it by hand — then enter the code the app shows to confirm.
4. **Save the recovery codes.** Ten single-use codes appear exactly once, with
   a download button. Store them away from the phone that runs the
   authenticator — a password manager or a printed page — and only then
   confirm that you have saved them.

!!! warning "Recovery codes are shown once"
    They are the only way into your account if the authenticator is lost, and
    they cannot be viewed again later. If you did not save them (or are
    running low — the same dialog shows how many remain), turn two-factor
    authentication off and set it up again to get a fresh set.

From the next sign-in on, entering your password is followed by a
**Two-factor verification** step asking for the current code from your app. To
type one of your saved recovery codes instead (each works once), click
**Use a recovery code**.

Occasionally Mascope asks for a code *during* a session: generating an API
access token and approving an instrument agent pairing both hand out
long-lived credentials, so both want proof that it is really you at the
keyboard. Signing in or completing setup counts as that proof for the next
five minutes, so back-to-back prompts are rare.

!!! tip "Two-factor authentication is not configured on this server?"
    If the setup dialog says so, the server is missing its two-factor
    encryption key. Ask whoever operates it to create one — the operator
    documentation covers it.

## If you lose the authenticator

Sign in with one of your recovery codes, then get a working authenticator
back:

- **Two-factor optional on your server** — open **Settings → Two-factor
  authentication**, turn it off (a recovery code is accepted there too), and
  set it up again with the new device. A new setup issues a fresh set of
  recovery codes.
- **Two-factor required for your account** — it cannot be turned off, so ask
  an administrator to reset it (below). You will be asked to set it up again
  at your next sign-in.

Lost the recovery codes as well? An administrator or owner can reset your
two-factor authentication; your password stays as it was. If nobody who could
do that can sign in — say, the only owner lost their authenticator — the
person who operates the server can clear it from the server's command line.

## For administrators

**Manage users** shows a **Two-factor** column and, for enrolled accounts, a
**Reset two-factor** action. Administrators can reset guests and editors;
owners can reset anyone but themselves. A reset only clears the second factor
— it never reveals or changes a password — and you are asked for your own
current code before it goes through. The holder sets up a new authenticator
from their settings (or at their next sign-in, where two-factor authentication
is required).

Whether the server requires two-factor authentication, and for which roles, is
a deployment setting; the note at the bottom of **Manage users** states the
active policy. Requiring it — and the server-side setup behind this feature —
is configured by whoever operates the server, per `docs/maintaining.md` in the
Mascope repository.
