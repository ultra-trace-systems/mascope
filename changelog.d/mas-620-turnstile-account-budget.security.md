- **A refused Turnstile check no longer spends the account's sign-in attempts.**
  With Turnstile on, a sign-in without a solved check counted against the
  account's 15 attempts per 5 minutes before it was refused, so anyone could
  lock an account out without solving a check. The account's attempts are now
  counted after the check; the per-address limit still counts every request.
