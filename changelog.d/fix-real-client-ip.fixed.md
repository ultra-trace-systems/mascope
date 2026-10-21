- **Per-client rate limits are per client again on a host whose Docker runs
  with `iptables: false`.** Such a host relays every connection to the
  frontend through Docker's userland proxy, so nginx saw each request arrive
  from the Docker bridge gateway and never read Cloudflare's
  `CF-Connecting-IP`. Every client of the deployment shared one budget: the
  login limit (10 attempts a minute), the stricter `/api/auth/` edge bucket and
  the API's request and connection caps were all deployment-wide, so one
  misbehaving client could throttle everyone, and the access logs recorded the
  gateway instead of the client. The HTTPS config now trusts
  `CF-Connecting-IP` from the gateway as well, which the container works out
  from its own default route at start. A client that reaches the published
  port directly, not through Cloudflare, can then name its own address in
  that header, so the firewall should admit only Cloudflare to the port. A
  deployment with no Cloudflare in front should set
  `MASCOPE_TRUST_DOCKER_GATEWAY=off` (`docs/hosting.md` has the details).
