- **Bearer tokens are stored hashed.** The `access_token` table now holds a
  SHA-256 digest of each SDK, agent and file-converter token instead of the
  token itself, so a copy of the database hands out no working credential;
  the upgrade hashes the stored tokens in place, and every token already
  issued keeps working. A token still expires 360 days after it is
  generated, and the SDK guide now says how to rotate one. Device-bound
  agent tokens keep their 30-day lifetime and renew themselves as before.

  The server can no longer hand out a token it issued earlier, so the file
  converter's token is minted for each upload or peak request and carried
  with that work, valid for 30 days; a sign-in, a promotion to editor and a
  pairing no longer mint one. Downgrading past this revision deletes every
  access token, since a digest gives no token back.
