- **A short Redis outage in the web socket storage no longer files errors
  in GlitchTip.** A Redis restart, a dropped idle connection or a Redis host
  name that does not resolve yet used to be logged with a stack by the web
  socket session, room, presence and heartbeat storage, and by
  python-socketio's own "retrying" lines, so each blip filed error events
  from every server it touched. That storage now retries a command once
  when Redis dropped its connection. What still fails is logged as a
  warning ending in "(Redis out of reach)", with no stack, and kept out of
  GlitchTip; python-socketio's retry lines are logged at INFO. A wrong
  Redis password, and every other socket storage failure, is still an
  error with its stack. Socket sign-in, the login rate limit and MFA still
  report a Redis outage as an error.
