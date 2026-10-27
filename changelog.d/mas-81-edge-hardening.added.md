- **The origin can refuse anything that did not come through our own Cloudflare zone.**
  With `MASCOPE_ORIGIN_PULL=on`, the frontend's port 443 answers only a
  connection that presents a client certificate signed by the CA in the new
  `origin_pull_ca` secret (`.runtime/secrets/origin_pull_ca.pem`), which is the
  zone's Authenticated Origin Pulls certificate. Off by default, so nothing
  changes until a site turns it on. The CLI creates the CA file empty when it
  is missing, so compose still starts. A new listener on port 8443, published
  on the host's `127.0.0.1` only, never asks for the certificate: the reboot
  playbook's probe and the security suite's origin vantage use it.
  `docs/maintaining.md`, "The edge", has the order to turn it on in, and the
  Cloudflare console checklist for managed rules, the sign-in rate limit and
  the sign-in challenge.
