- **A browser that has not signed in no longer hears when records change.**
  Updates sent to every open tab, such as a workspace created or deleted, a
  target list changed or a user edited, reached every connected socket: the
  login page's too, and a tab held at a forced password change. They now go
  only to tabs whose session may read. A held tab starts receiving them once
  it changes its password. The security suite's RT-01 now provokes such an
  update itself instead of relying on other traffic.
