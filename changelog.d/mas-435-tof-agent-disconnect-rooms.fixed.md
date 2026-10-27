- **A TOF agent reconnecting no longer drops its user out of open workspaces.**
  The agent's disconnect removed every room membership of the account it
  signs in as, so a browser tab of that account stopped counting as in its
  workspace until it subscribed again. The agent's socket holds no rooms, and
  now leaves none.
