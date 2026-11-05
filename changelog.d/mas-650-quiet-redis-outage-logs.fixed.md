- **A Redis restart no longer files errors from socket sign-in and sign-out.**
  Socket sign-in, the socket events and signing sockets out log Redis out of
  reach as a warning kept out of GlitchTip, as the socket storage already
  did. The login rate limits and MFA still file an error, now without a stack
  and saying it failed open (or closed) because Redis was out of reach.
