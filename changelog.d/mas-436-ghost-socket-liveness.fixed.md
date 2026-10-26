- **A backend worker that dies uncleanly no longer leaves its users' tabs
  missing notifications.** A worker killed without running its disconnect
  handlers (out of memory, `docker kill`, power loss) left its tabs' sessions
  in Redis for up to a day, and those tabs still counted as open. When the
  user's last real tab closed, their room memberships stayed, and a tab they
  opened next on another page missed notifications that were sent only to
  those rooms. Each worker now keeps a short-lived heartbeat in Redis, and a
  tab counts as open only while its worker's heartbeat is there, so the tabs
  of a dead worker stop counting within 30 seconds.
