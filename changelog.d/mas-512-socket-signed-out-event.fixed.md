- **Signing out in one tab now shows the sign-in screen in the browser's other
  tabs.** Signing out ends the session of every tab of the browser, but the
  other tabs were not told. Once the browser signed in again elsewhere, such a
  tab kept showing data with no live updates until it was reloaded. The
  server now tells each tab it signs out, and the tab opens a new connection
  and checks its session: it shows the sign-in screen, or picks its live
  updates back up when the browser has signed in again.
