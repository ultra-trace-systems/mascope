- **The hosting guide's egress allow-list names Docker Hub and Turnstile's
  host.** `mascope prod update` pulls every image in the compose file, the
  Postgres and Redis images from Docker Hub as well as the release images
  from GHCR, so a site that allowed only the listed names had every update
  fail at the pull. The list now gives Docker Hub's registry, token and
  layer-download hosts, with a pointer to Docker's own list, since the
  download host has moved before. It also names `challenges.cloudflare.com`
  for a server with Turnstile on: the backend checks every password sign-in
  there and refuses the sign-in when it cannot reach it.
