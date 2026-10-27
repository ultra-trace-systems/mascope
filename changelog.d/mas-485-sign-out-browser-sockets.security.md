- **Signing out signs every tab of the browser out of live updates.**
  Signing out in one tab ends the session of every tab of that browser, but
  only the signing-out tab's realtime connection was signed out. The others
  kept receiving the updates of what they had open, and could open more.
  Now the server signs out every connection that the ending session
  authenticated. The account's sessions in other browsers carry on.
