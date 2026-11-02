- **Cloudflare Turnstile on the sign-in form, per site.** A site that sets
  `MASCOPE_TURNSTILE_SITE_KEY` and puts the widget's secret key in the new
  `turnstile_secret_key` compose secret gets Cloudflare's human check on the
  sign-in form. The backend has Cloudflare confirm the check before it looks
  at the password, and refuses the sign-in without it. The two-factor step,
  token-authenticated SDK and agent calls, pairing and uploads are not
  checked. Off by default; the CLI creates the secret file empty, readable by
  its owner only. It replaces the runbook's Managed Challenge on the login
  POST, which the web app could not solve (docs/maintaining.md, "Turning
  Turnstile on").
