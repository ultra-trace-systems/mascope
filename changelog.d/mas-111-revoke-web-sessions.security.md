- **A web session can be ended before it expires.** Until now a signed-in
  browser stayed signed in for its full seven days whatever happened to the
  account: a password change, a disable or a forced password change left
  every open session running, and signing out only cleared the one browser's
  cookie. The session token now records when it was issued, and a session
  issued before the account's cutoff is refused with the usual 401. A password
  change, disabling the account and the owner's "require a new password of
  everyone" all move the cutoff, and so does the new sign-out-everywhere
  action: `POST /api/users/me/sign-out-everywhere` for one's own account,
  `POST /api/users/owner/{id}/sign-out-everywhere` for an owner on any
  account. A socket whose session has been ended, or whose account was
  disabled, can no longer join rooms other than its own user room. A session
  issued in the minute before the cutoff survives it, to allow for clock skew
  between workers. API access tokens are unaffected. **Upgrading signs most
  web users out once**: a session issued by an earlier version carries no
  issue time, so it cannot show it postdates the account's last password
  write and is refused for every account that has one - any account created,
  reset or whose password changed since the forced-password-change release
  (`docs/authorization.md`, "Sessions").
