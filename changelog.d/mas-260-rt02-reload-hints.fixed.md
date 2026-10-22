- **The security suite no longer reports a broadcast as a room leak.** RT-02
  failed whenever an event sent to every socket, such as a target list's
  reload hint, arrived while it watched a workspace's room. RT-02 and RT-03
  now leave out what a socket subscribed to no room also receives, and RT-03
  counts every room event again rather than workspace events alone.
