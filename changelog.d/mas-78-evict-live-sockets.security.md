- **An open tab stops receiving live updates as soon as its session ends.**
  A socket already subscribed to a workspace's room kept receiving its
  record events after the account was held at a forced password change, had
  its password changed or reset, was disabled or was signed out everywhere,
  until the tab disconnected: the gate only refused new subscriptions. Each of
  those now makes the account's open sockets leave every room but their own
  user room, on whichever backend worker holds them. The connection stays
  open, so the tab still hears that its session is over. A session started in
  the minute before the change, which the cutoff's tolerance keeps, keeps its
  rooms too. The security suite's RT-03 now checks a socket subscribed before
  the account is armed as well as one subscribing after.
