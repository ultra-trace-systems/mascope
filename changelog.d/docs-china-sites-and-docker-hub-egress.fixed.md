- **The hosting guide's egress allow-list names Docker Hub.** `mascope prod
  update` pulls every image in the compose file, the Postgres and Redis
  images from Docker Hub as well as the release images from GHCR, so a site
  that allowed only the listed names had every update fail at the pull. The
  list now gives Docker Hub's registry, token and layer-download hosts, with
  a pointer to Docker's own list, since the download host has moved before.
