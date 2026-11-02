- **The monitoring host reports a merged security fix that a deployment does
  not run yet.** `tooling/monitoring/security-fix-distance.py` runs hourly
  beside the assessment runner. It reads the security fixes from the
  `changelog.d/*.security.md` fragments they merge with, and each production
  deployment's release from its `/api/version/public` route. It names every
  deployment whose release lacks a fix, and every one whose release it cannot
  read, and is silent when the fleet runs every fix. Its heartbeat fails
  while anything is behind, and a check that stops running stops pinging,
  which pages too. A security fix now files its changelog entry under
  `security`, never `fixed`, so the check sees it.
