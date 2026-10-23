- **Closing one tab no longer doubles another tab's notifications.** With two
  tabs open on the same workspace, closing one, or leaving a batch in one,
  made the server think the user had stopped listening there. It then sent
  each notification to the workspace and to the user's personal channel as
  well, so the tab still open showed it twice. The user's membership of a
  room now stays while any of their tabs is still in it.
