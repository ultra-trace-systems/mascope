- **A socket sign-out that meets a Redis outage is tried again.** Evicting the
  sockets of an account that is disabled, deleted, signed out everywhere or
  held at the password screen, and signing out a logout's tabs, used to be
  dropped when Redis was out of reach, so those tabs went on receiving record
  data once Redis was back. The backend worker now keeps the sign-out and
  carries it out when Redis answers again
  ([#2960](https://github.com/ultra-trace-systems/mascope/issues/2960)).
