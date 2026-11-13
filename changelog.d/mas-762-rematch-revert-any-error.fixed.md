- **A batch rematch that fails on an unexpected error no longer leaves the
  batch locked.** The batch went back to *rematch*, or to *recalibrate* when it
  waited for one, only when the error was one the app reports itself. Any
  other error, or a cancelled run, left it at *processing*: every later
  rematch answered that the batch was locked, and calibrating it was refused,
  until the server restarted. It now goes back whatever the error.
