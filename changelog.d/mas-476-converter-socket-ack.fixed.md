- **The file converter notices when the backend rejects its events.** When
  a file's access token has expired or been revoked, the backend drops the
  converter's progress and result events for that file. The converter now
  reads the backend's answer, logs a warning once per file naming the event
  and the reason, and stops sending that file's events with the rejected
  token. A token the backend could not check for a moment, for example
  while its database is busy, is no longer reported as invalid: that one
  event is lost, and the file's later events are still sent.
