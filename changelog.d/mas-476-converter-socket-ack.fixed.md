- **The file converter notices when the backend rejects its events.** When
  a file's access token has expired or been revoked, the backend drops the
  converter's progress and result events for that file. The converter now
  reads the backend's answer, logs a warning once per file naming the event
  and the reason, and stops sending that file's events with the rejected
  token.
