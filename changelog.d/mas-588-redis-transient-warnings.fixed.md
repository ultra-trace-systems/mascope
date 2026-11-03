- **A short Redis outage no longer files errors in GlitchTip.** A Redis
  restart, a dropped idle connection or a Redis host name that does not
  resolve yet used to be logged with a stack by the web socket session,
  room, presence and heartbeat storage, and by python-socketio's own
  "Cannot receive from redis... retrying" line, so each blip filed error
  events from every server it touched. The backend now pings an idle Redis
  connection before using it and retries a command that met a dropped
  connection, so most blips heal unseen. What still fails is logged as a
  warning ending in "(Redis out of reach)", with no stack, and kept out of
  GlitchTip; python-socketio's line is logged at INFO. Every other socket
  storage failure is still an error with its stack.
